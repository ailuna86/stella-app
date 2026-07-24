// v8: rewritten from the ground up on top of lib/server/db.ts (SQLite)
// instead of reading/writing whole JSON files. Every function keeps the
// same name and signature as v5–v7 where possible so the rest of the app
// barely had to change. New: everything is scoped to an organizationId —
// see lib/types.ts comment for why.
import { randomUUID } from "crypto";
import { db } from "./db";
import { PROMPT_SEED } from "@/lib/prompt-bank";
import type {
  AlgorithmFeedback,
  Assignment,
  Organization,
  SubmissionRecord,
  User,
} from "@/lib/types";

const PILOT_ORG_ID = "org_pilot_1";

function ensureSeed() {
  const d = db();
  const org = d.prepare("SELECT id FROM organizations WHERE id = ?").get(PILOT_ORG_ID);
  if (!org) {
    d.prepare("INSERT INTO organizations (id, name, created_at) VALUES (?, ?, ?)").run(
      PILOT_ORG_ID,
      "ST.ELLA Pilot",
      new Date().toISOString()
    );
  }
  const anyPrompt = d.prepare("SELECT id FROM prompts LIMIT 1").get();
  if (!anyPrompt) {
    for (const p of PROMPT_SEED) seedPrompt(p);
  }
  const trainer = d.prepare("SELECT id FROM users WHERE id = ?").get("trainer_1");
  if (!trainer) {
    d.prepare(
      `INSERT INTO users (id, organization_id, name, email, role, plan, verified_at, consent_at, pilot_ends_at, entitlements, intake)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).run(
      "trainer_1",
      PILOT_ORG_ID,
      "Ailuna",
      "ailuna.shamurzaeva@gmail.com",
      "trainer",
      "gold",
      null,
      null,
      null,
      JSON.stringify({ can_self_submit: true, can_practice: true, evaluations_left: 999 }),
      null
    );
  }
}

function rowToUser(row: any): User {
  return {
    id: row.id,
    organizationId: row.organization_id,
    name: row.name,
    email: row.email,
    role: row.role,
    plan: row.plan,
    verifiedAt: row.verified_at ?? undefined,
    consentAt: row.consent_at ?? undefined,
    pilotEndsAt: row.pilot_ends_at ?? undefined,
    entitlements: JSON.parse(row.entitlements),
    intake: row.intake ? JSON.parse(row.intake) : undefined,
    seenEngineIntros: row.seen_engine_intros ? JSON.parse(row.seen_engine_intros) : [],
  };
}

function rowToAssignment(row: any): Assignment {
  return {
    id: row.id,
    organizationId: row.organization_id,
    trainerId: row.trainer_id,
    prompt: row.prompt,
    dueDate: row.due_date,
    studentIds: JSON.parse(row.student_ids),
    createdAt: row.created_at,
  };
}

function rowToSubmission(row: any): SubmissionRecord {
  return {
    id: row.id,
    organizationId: row.organization_id,
    studentId: row.student_id,
    assignmentId: row.assignment_id,
    prompt: row.prompt,
    essay: row.essay,
    status: row.status,
    createdAt: row.created_at,
    report: row.report ? JSON.parse(row.report) : undefined,
    sessionDir: row.session_dir ?? undefined,
    error: row.error ?? undefined,
    mode: row.mode ?? undefined,
    timeSpentSeconds: row.time_spent_seconds ?? undefined,
    topic: row.topic ?? undefined,
  };
}

// -- organizations -----------------------------------------------------------

export function getOrganization(id: string): Organization | undefined {
  ensureSeed();
  const row = db().prepare("SELECT * FROM organizations WHERE id = ?").get(id) as any;
  return row ? { id: row.id, name: row.name, createdAt: row.created_at } : undefined;
}

export function defaultOrganizationId(): string {
  ensureSeed();
  return PILOT_ORG_ID;
}

// -- users ---------------------------------------------------------------

export function getUsers(organizationId?: string): User[] {
  ensureSeed();
  const rows = organizationId
    ? db().prepare("SELECT * FROM users WHERE organization_id = ?").all(organizationId)
    : db().prepare("SELECT * FROM users").all();
  return (rows as any[]).map(rowToUser);
}

export function saveUser(user: User) {
  db()
    .prepare(
      `UPDATE users SET name=?, email=?, role=?, plan=?, verified_at=?, consent_at=?, pilot_ends_at=?, entitlements=?, intake=?, seen_engine_intros=?
       WHERE id=?`
    )
    .run(
      user.name,
      user.email.toLowerCase(),
      user.role,
      user.plan,
      user.verifiedAt ?? null,
      user.consentAt ?? null,
      user.pilotEndsAt ?? null,
      JSON.stringify(user.entitlements),
      user.intake ? JSON.stringify(user.intake) : null,
      JSON.stringify(user.seenEngineIntros ?? []),
      user.id
    );
}

// v17: mark one engine's intro pop-up as acknowledged for this student —
// server-side (see User.seenEngineIntros), idempotent (adding an already-
// present key is a no-op). Deliberately its own small UPDATE rather than a
// full saveUser() round trip, since callers hitting this (the "Got it"
// button) shouldn't need to first fetch/reconstruct the whole User object.
export function markEngineIntroSeen(userId: string, engineKey: string): void {
  const row = db().prepare("SELECT seen_engine_intros FROM users WHERE id = ?").get(userId) as any;
  if (!row) return;
  const seen: string[] = row.seen_engine_intros ? JSON.parse(row.seen_engine_intros) : [];
  if (seen.includes(engineKey)) return;
  seen.push(engineKey);
  db().prepare("UPDATE users SET seen_engine_intros = ? WHERE id = ?").run(JSON.stringify(seen), userId);
}

export function addUser(user: User) {
  ensureSeed();
  db()
    .prepare(
      `INSERT INTO users (id, organization_id, name, email, role, plan, verified_at, consent_at, pilot_ends_at, entitlements, intake)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      user.id,
      user.organizationId,
      user.name,
      user.email.toLowerCase(),
      user.role,
      user.plan,
      user.verifiedAt ?? null,
      user.consentAt ?? null,
      user.pilotEndsAt ?? null,
      JSON.stringify(user.entitlements),
      user.intake ? JSON.stringify(user.intake) : null
    );
}

export function findUserByEmail(email: string): User | undefined {
  ensureSeed();
  const row = db()
    .prepare("SELECT * FROM users WHERE email = ?")
    .get((email ?? "").trim().toLowerCase()) as any;
  return row ? rowToUser(row) : undefined;
}

export function getUserById(id: string): User | undefined {
  ensureSeed();
  const row = db().prepare("SELECT * FROM users WHERE id = ?").get(id) as any;
  return row ? rowToUser(row) : undefined;
}

// -- assignments -----------------------------------------------------------

export function getAssignments(organizationId?: string): Assignment[] {
  const rows = organizationId
    ? db().prepare("SELECT * FROM assignments WHERE organization_id = ?").all(organizationId)
    : db().prepare("SELECT * FROM assignments").all();
  return (rows as any[]).map(rowToAssignment);
}

export function addAssignment(a: Assignment) {
  db()
    .prepare(
      `INSERT INTO assignments (id, organization_id, trainer_id, prompt, due_date, student_ids, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`
    )
    .run(a.id, a.organizationId, a.trainerId, a.prompt, a.dueDate, JSON.stringify(a.studentIds), a.createdAt);
}

export function activeAssignmentFor(studentId: string): Assignment | undefined {
  const rows = db().prepare("SELECT * FROM assignments ORDER BY created_at DESC").all() as any[];
  const match = rows
    .map(rowToAssignment)
    .find((a) => a.studentIds.includes(studentId));
  return match;
}

// -- submissions -----------------------------------------------------------

export function saveSubmission(s: SubmissionRecord) {
  const exists = db().prepare("SELECT id FROM submissions WHERE id = ?").get(s.id);
  const reportJson = s.report ? JSON.stringify(s.report) : null;
  if (exists) {
    db()
      .prepare(
        `UPDATE submissions SET status=?, report=?, session_dir=?, error=? WHERE id=?`
      )
      .run(s.status, reportJson, s.sessionDir ?? null, s.error ?? null, s.id);
  } else {
    db()
      .prepare(
        `INSERT INTO submissions (id, organization_id, student_id, assignment_id, prompt, essay, status, created_at, report, session_dir, error, mode, time_spent_seconds)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .run(
        s.id,
        s.organizationId,
        s.studentId,
        s.assignmentId,
        s.prompt,
        s.essay,
        s.status,
        s.createdAt,
        reportJson,
        s.sessionDir ?? null,
        s.error ?? null,
        s.mode ?? null,
        s.timeSpentSeconds ?? null
      );
  }
}

// v26 (2026-07-23): sets a submission's classified essay topic (one of the
// 18 real vocab_coach_topic_bank_v1_5_0.json topic keys, or null) after
// classifyEssayTopic() runs in goldPipeline.ts's post-evaluation success
// path. A dedicated single-column UPDATE, same pattern as markEngineIntroSeen
// above -- the caller (runGoldEvaluation) already has just the submission id
// and topic string in hand, no need to round-trip a full SubmissionRecord.
export function setSubmissionTopic(submissionId: string, topic: string | null): void {
  db().prepare("UPDATE submissions SET topic = ? WHERE id = ?").run(topic, submissionId);
}

export function getSubmission(id: string): SubmissionRecord | undefined {
  const row = db().prepare("SELECT * FROM submissions WHERE id = ?").get(id) as any;
  return row ? rowToSubmission(row) : undefined;
}

export function submissionsFor(studentId?: string): SubmissionRecord[] {
  const rows = studentId
    ? db()
        .prepare("SELECT * FROM submissions WHERE student_id = ? ORDER BY created_at DESC")
        .all(studentId)
    : db().prepare("SELECT * FROM submissions ORDER BY created_at DESC").all();
  return (rows as any[]).map(rowToSubmission);
}

// -- practice ---------------------------------------------------------------

export function getSeen(studentId: string): string[] {
  const rows = db()
    .prepare("SELECT exercise_id FROM seen_exercises WHERE student_id = ?")
    .all(studentId) as any[];
  return rows.map((r) => r.exercise_id);
}

export function addSeen(studentId: string, ids: string[]) {
  const stmt = db().prepare(
    "INSERT OR IGNORE INTO seen_exercises (student_id, exercise_id) VALUES (?, ?)"
  );
  const tx = db().transaction((all: string[]) => {
    for (const id of all) stmt.run(studentId, id);
  });
  tx(ids);
}

export function savePracticeResult(studentId: string, result: { at: string; correct: number; total: number; exerciseIds: string[] }) {
  db()
    .prepare(
      "INSERT INTO practice_results (student_id, at, correct, total, exercise_ids) VALUES (?, ?, ?, ?, ?)"
    )
    .run(studentId, result.at, result.correct, result.total, JSON.stringify(result.exerciseIds));
}

// -- writing coach missions --------------------------------------------------
// v17: see db.ts's mission_results comment -- mission grading used to have no
// persistence at all (Pipeline_Frontend_Spec_v2 §4).

export function saveMissionResult(studentId: string, result: { at: string; outcome: string; missionTitle?: string | null }) {
  db()
    .prepare("INSERT INTO mission_results (student_id, at, outcome, mission_title) VALUES (?, ?, ?, ?)")
    .run(studentId, result.at, result.outcome, result.missionTitle ?? null);
}

export function missionResultsFor(studentId: string): Array<{ at: string; outcome: string; missionTitle: string | null }> {
  const rows = db()
    .prepare("SELECT * FROM mission_results WHERE student_id = ? ORDER BY at ASC")
    .all(studentId) as any[];
  return rows.map((r) => ({ at: r.at, outcome: r.outcome, missionTitle: r.mission_title }));
}

export function practiceResultsFor(studentId: string): any[] {
  const rows = db()
    .prepare("SELECT * FROM practice_results WHERE student_id = ? ORDER BY at ASC")
    .all(studentId) as any[];
  return rows.map((r) => ({
    at: r.at,
    correct: r.correct,
    total: r.total,
    exerciseIds: JSON.parse(r.exercise_ids),
  }));
}

// -- learner profile refresh attempts ----------------------------------------
// v22 (2026-07-23): Defect 2 fix for goldPipeline.ts's refreshLearnerProfile()
// -- see its module comment. One row per real refresh attempt; used by
// app/trainer/page.tsx to show a warning badge next to a student whose most
// recent attempt failed.

export function recordLearnerProfileRefreshAttempt(
  studentId: string,
  status: "success" | "failure",
  errorMessage: string | null
) {
  db()
    .prepare(
      "INSERT INTO learner_profile_refresh_attempts (student_id, at, status, error_message) VALUES (?, ?, ?, ?)"
    )
    .run(studentId, new Date().toISOString(), status, errorMessage);
}

export function latestLearnerProfileRefreshAttempt(
  studentId: string
): { at: string; status: string; errorMessage: string | null } | undefined {
  const row = db()
    .prepare(
      "SELECT * FROM learner_profile_refresh_attempts WHERE student_id = ? ORDER BY id DESC LIMIT 1"
    )
    .get(studentId) as any;
  if (!row) return undefined;
  return { at: row.at, status: row.status, errorMessage: row.error_message };
}

// -- platform feedback -------------------------------------------------------

export interface PlatformFeedback {
  studentId: string;
  context: "report" | "practice";
  clarity: number;
  usefulness: number;
  comment: string;
  at: string;
}

export function savePlatformFeedback(entry: PlatformFeedback) {
  db()
    .prepare(
      "INSERT INTO platform_feedback (student_id, context, clarity, usefulness, comment, at) VALUES (?, ?, ?, ?, ?, ?)"
    )
    .run(entry.studentId, entry.context, entry.clarity, entry.usefulness, entry.comment, entry.at);
}

export function allPlatformFeedback(): PlatformFeedback[] {
  const rows = db()
    .prepare("SELECT * FROM platform_feedback ORDER BY at DESC")
    .all() as any[];
  return rows.map((r) => ({
    studentId: r.student_id,
    context: r.context,
    clarity: r.clarity,
    usefulness: r.usefulness,
    comment: r.comment ?? "",
    at: r.at,
  }));
}

// -- v8: algorithm feedback (trainer QA on the AI's evaluation) -------------

export function saveAlgorithmFeedback(entry: Omit<AlgorithmFeedback, "id" | "createdAt">) {
  const row: AlgorithmFeedback = { ...entry, id: randomUUID(), createdAt: new Date().toISOString() };
  db()
    .prepare(
      `INSERT INTO algorithm_feedback
       (id, organization_id, submission_id, trainer_id, overall_accuracy, criteria_feedback, wrong_error_ids, missed_errors, feedback_quality_notes, general_notes, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
    )
    .run(
      row.id,
      row.organizationId,
      row.submissionId,
      row.trainerId,
      row.overallAccuracy,
      JSON.stringify(row.criteriaFeedback),
      JSON.stringify(row.wrongErrorIds),
      row.missedErrors,
      row.feedbackQualityNotes,
      row.generalNotes,
      row.createdAt
    );
  return row;
}

export function algorithmFeedbackFor(submissionId: string): AlgorithmFeedback[] {
  const rows = db()
    .prepare("SELECT * FROM algorithm_feedback WHERE submission_id = ? ORDER BY created_at DESC")
    .all(submissionId) as any[];
  return rows.map((r) => ({
    id: r.id,
    organizationId: r.organization_id,
    submissionId: r.submission_id,
    trainerId: r.trainer_id,
    overallAccuracy: r.overall_accuracy,
    criteriaFeedback: JSON.parse(r.criteria_feedback),
    wrongErrorIds: JSON.parse(r.wrong_error_ids),
    missedErrors: r.missed_errors ?? "",
    feedbackQualityNotes: r.feedback_quality_notes ?? "",
    generalNotes: r.general_notes ?? "",
    createdAt: r.created_at,
  }));
}

// -- v8: prompt bank (DB-backed so trainer approval persists) ---------------

export interface Prompt {
  id: string;
  topic: string;
  type: string;
  text: string;
  approved: boolean;
  createdAt: string;
}

function rowToPrompt(row: any): Prompt {
  return {
    id: row.id,
    topic: row.topic,
    type: row.type,
    text: row.text,
    approved: !!row.approved,
    createdAt: row.created_at,
  };
}

export function seedPrompt(p: { id: string; topic: string; type: string; text: string; approved: boolean }) {
  const exists = db().prepare("SELECT id FROM prompts WHERE id = ?").get(p.id);
  if (exists) return;
  db()
    .prepare(
      "INSERT INTO prompts (id, topic, type, text, approved, created_at) VALUES (?, ?, ?, ?, ?, ?)"
    )
    .run(p.id, p.topic, p.type, p.text, p.approved ? 1 : 0, new Date().toISOString());
}

export function getPrompts(opts?: { approvedOnly?: boolean }): Prompt[] {
  const rows = opts?.approvedOnly
    ? db().prepare("SELECT * FROM prompts WHERE approved = 1 ORDER BY topic").all()
    : db().prepare("SELECT * FROM prompts ORDER BY approved ASC, topic ASC").all();
  return (rows as any[]).map(rowToPrompt);
}

export function setPromptApproval(id: string, approved: boolean, text?: string) {
  if (text !== undefined) {
    db().prepare("UPDATE prompts SET approved = ?, text = ? WHERE id = ?").run(approved ? 1 : 0, text, id);
  } else {
    db().prepare("UPDATE prompts SET approved = ? WHERE id = ?").run(approved ? 1 : 0, id);
  }
}

// -- v9: upgrade requests (manual-fulfillment bridge until real payment) ----

export interface UpgradeRequest {
  id: string;
  organizationId: string;
  userId: string;
  requestedPlan: "premium" | "gold";
  status: "pending" | "done";
  createdAt: string;
}

export function addUpgradeRequest(req: { organizationId: string; userId: string; requestedPlan: "premium" | "gold" }) {
  const row: UpgradeRequest = {
    id: randomUUID(),
    organizationId: req.organizationId,
    userId: req.userId,
    requestedPlan: req.requestedPlan,
    status: "pending",
    createdAt: new Date().toISOString(),
  };
  db()
    .prepare(
      `INSERT INTO upgrade_requests (id, organization_id, user_id, requested_plan, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`
    )
    .run(row.id, row.organizationId, row.userId, row.requestedPlan, row.status, row.createdAt);
  return row;
}

function rowToUpgradeRequest(row: any): UpgradeRequest {
  return {
    id: row.id,
    organizationId: row.organization_id,
    userId: row.user_id,
    requestedPlan: row.requested_plan,
    status: row.status,
    createdAt: row.created_at,
  };
}

export function pendingUpgradeRequests(): UpgradeRequest[] {
  const rows = db()
    .prepare("SELECT * FROM upgrade_requests WHERE status = 'pending' ORDER BY created_at DESC")
    .all() as any[];
  return rows.map(rowToUpgradeRequest);
}

export function markUpgradeRequestDone(id: string) {
  db().prepare("UPDATE upgrade_requests SET status = 'done' WHERE id = ?").run(id);
}