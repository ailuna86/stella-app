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
      intake TEXT
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
      error TEXT
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
}