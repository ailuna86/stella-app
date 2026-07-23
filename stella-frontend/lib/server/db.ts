// v8: replaces the v4–v7 approach of reading/writing whole JSON files on
// every request (which had no locking — two requests at the same instant
// could corrupt each other's writes) with a single local SQLite database
// file. SQLite is still just "a file on disk" (same deployment model as
// before, no separate database server to run), but every write is now an
// atomic, safe operation, and the shape is ready for a second organization
// (school) to be added later without a schema rewrite.
import Database from "better-sqlite3";
import fs from "fs";
import path from "path";

const DATA_DIR = path.join(process.cwd(), "data");
const DB_PATH = path.join(DATA_DIR, "stella.db");

let _db: Database.Database | null = null;

export function db(): Database.Database {
  if (_db) return _db;
  fs.mkdirSync(DATA_DIR, { recursive: true });
  _db = new Database(DB_PATH);
  _db.pragma("journal_mode = WAL");
  migrate(_db);
  return _db;
}

function migrate(d: Database.Database) {
  d.exec(`
    CREATE TABLE IF NOT EXISTS organizations (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL REFERENCES organizations(id),
      name TEXT NOT NULL,
      email TEXT NOT NULL UNIQUE,
      role TEXT NOT NULL,
      plan TEXT NOT NULL,
      verified_at TEXT,
      consent_at TEXT,
      pilot_ends_at TEXT,
      entitlements TEXT NOT NULL,
      intake TEXT,
      seen_engine_intros TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id);

    CREATE TABLE IF NOT EXISTS assignments (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL,
      trainer_id TEXT NOT NULL,
      prompt TEXT NOT NULL,
      due_date TEXT NOT NULL,
      student_ids TEXT NOT NULL,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_assignments_org ON assignments(organization_id);

    CREATE TABLE IF NOT EXISTS submissions (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL,
      student_id TEXT NOT NULL,
      assignment_id TEXT,
      prompt TEXT NOT NULL,
      essay TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      report TEXT,
      session_dir TEXT,
      error TEXT,
      mode TEXT,
      time_spent_seconds INTEGER
    );
    CREATE INDEX IF NOT EXISTS idx_submissions_student ON submissions(student_id);
    CREATE INDEX IF NOT EXISTS idx_submissions_org ON submissions(organization_id);

    CREATE TABLE IF NOT EXISTS seen_exercises (
      student_id TEXT NOT NULL,
      exercise_id TEXT NOT NULL,
      PRIMARY KEY (student_id, exercise_id)
    );

    CREATE TABLE IF NOT EXISTS practice_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT NOT NULL,
      at TEXT NOT NULL,
      correct INTEGER NOT NULL,
      total INTEGER NOT NULL,
      exercise_ids TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_practice_student ON practice_results(student_id);

    -- v17: mission_results — Pipeline_Frontend_Spec_v2 §4 daily digest needs
    -- to say "you completed N writing missions today". runMissionGrading
    -- already writes each attempt to sessionDir/mission_attempts/*.json, but
    -- that's scattered one file per attempt inside whichever essay's session
    -- folder happened to be "latest" at submit time -- not something a daily
    -- digest can cheaply query across a student's whole history. This table
    -- is a lightweight queryable index alongside those files, not a
    -- replacement for them.
    CREATE TABLE IF NOT EXISTS mission_results (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT NOT NULL,
      at TEXT NOT NULL,
      outcome TEXT NOT NULL,
      mission_title TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_mission_student ON mission_results(student_id);

    -- v22 (2026-07-23): learner_profile_refresh_attempts -- Defect 2 fix for
    -- goldPipeline.ts's refreshLearnerProfile(). That function is called
    -- fire-and-forget from 4 routes and NEVER rejects by design, which used
    -- to mean a failure was fully invisible (no log, no record). One row per
    -- real refresh attempt (the function's own "no essay yet" / "artifacts
    -- incomplete" no-op early-returns do NOT write a row -- see its module
    -- comment) so a trainer or the PO can actually check whether a given
    -- student's continuous-loop refresh is working.
    CREATE TABLE IF NOT EXISTS learner_profile_refresh_attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT NOT NULL,
      at TEXT NOT NULL,
      status TEXT NOT NULL,
      error_message TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_refresh_attempts_student ON learner_profile_refresh_attempts(student_id);

    CREATE TABLE IF NOT EXISTS platform_feedback (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      student_id TEXT NOT NULL,
      context TEXT NOT NULL,
      clarity INTEGER NOT NULL,
      usefulness INTEGER NOT NULL,
      comment TEXT,
      at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS algorithm_feedback (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL,
      submission_id TEXT NOT NULL,
      trainer_id TEXT NOT NULL,
      overall_accuracy TEXT NOT NULL,
      criteria_feedback TEXT NOT NULL,
      wrong_error_ids TEXT NOT NULL,
      missed_errors TEXT,
      feedback_quality_notes TEXT,
      general_notes TEXT,
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_algo_feedback_submission ON algorithm_feedback(submission_id);

    CREATE TABLE IF NOT EXISTS prompts (
      id TEXT PRIMARY KEY,
      topic TEXT NOT NULL,
      type TEXT NOT NULL,
      text TEXT NOT NULL,
      approved INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS consents (
      user_id TEXT PRIMARY KEY,
      accepted_at TEXT NOT NULL,
      version TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS otp_codes (
      email TEXT PRIMARY KEY,
      code TEXT NOT NULL,
      expires INTEGER NOT NULL,
      attempts INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS rate_limits (
      key TEXT PRIMARY KEY,
      count INTEGER NOT NULL,
      window_start INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS upgrade_requests (
      id TEXT PRIMARY KEY,
      organization_id TEXT NOT NULL,
      user_id TEXT NOT NULL,
      requested_plan TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_upgrade_requests_status ON upgrade_requests(status);
  `);

  // Defensive migration for databases created before pilot_ends_at existed —
  // CREATE TABLE IF NOT EXISTS above won't add a column to an existing table,
  // so a pre-pilot database needs this ALTER TABLE to pick it up.
  try {
    d.exec("ALTER TABLE users ADD COLUMN pilot_ends_at TEXT");
  } catch {
    // column already exists — fine
  }
  // Same defensive migration for seen_engine_intros (ST_ELLA_Student_Journey_v1.docx
  // §4.4 — engine intro pop-ups, "Got it" state stored server-side per student
  // per engine so it follows them across devices, unlike localStorage).
  try {
    d.exec("ALTER TABLE users ADD COLUMN seen_engine_intros TEXT");
  } catch {
    // column already exists — fine
  }
  // v20: essay-submission timer (exam mode / practice mode, PO request "Timer
  // for essay submission") — defensive migration for databases created
  // before mode/time_spent_seconds existed on submissions, same pattern as
  // the two ALTERs above.
  try {
    d.exec("ALTER TABLE submissions ADD COLUMN mode TEXT");
  } catch {
    // column already exists — fine
  }
  try {
    d.exec("ALTER TABLE submissions ADD COLUMN time_spent_seconds INTEGER");
  } catch {
    // column already exists — fine
  }
  // v26 (2026-07-23): essay topic tag, for Vocab Coach topic-matching (PO
  // design discussion -- vocabulary practice should stay on the same topic
  // as the essay being revised, so it can plausibly show up in the revision;
  // random topic otherwise). Populated by classifyEssayTopic() in
  // goldPipeline.ts right after a Gold-tier submission is evaluated. One of
  // the 18 real vocab_coach_topic_bank_v1_5_0.json topic keys, or NULL if
  // classification wasn't confident enough -- NULL is a valid, expected
  // state (falls back to the existing random-topic rotation), not an error.
  try {
    d.exec("ALTER TABLE submissions ADD COLUMN topic TEXT");
  } catch {
    // column already exists — fine
  }
}