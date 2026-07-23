// v18 (session-audit Finding 5): app/progress/page.tsx previously derived
// "Your progress" purely from submissionsFor()'s lightweight report.score_summary
// per submission — it never read the pipeline's actual per-skill progress
// artifact (02f_gold_progress_tracker_persisted.json / 02e_gold_progress_tracker.json,
// written by progress_tracker_v2_scorer_feed.py), which already accumulates
// score_events with per-criterion bands and a stable_for_trend flag across every
// session for this student. Confirmed via a real session dump that the backend
// data is present and well-formed — the "progress tracker is empty" complaint
// was a frontend read gap, same shape as study-plan.ts's pre-v15 bug (reading a
// file that was never the real artifact). This file adds the real read,
// following the same getX(sessionDir) pattern as getLearningRoadmap().
import fs from "fs";
import path from "path";

export interface SkillScoreEvent {
  essayId: string;
  recordedAt: string;
  overallBand: number;
  criteriaBands: Record<string, number>;
  stableForTrend: boolean;
}

export interface SkillProgress {
  events: SkillScoreEvent[];
  stableEvents: SkillScoreEvent[];
  lastUpdatedAt: string;
}

// Real criteria_bands keys as written by the Gold scorer/progress-tracker
// chain (confirmed against a real 02f_gold_progress_tracker_persisted.json) —
// deliberately separate from lib/types.ts's CRITERION_LABELS, whose keys
// (task_achievement / grammatical_range_accuracy) don't match this artifact's
// actual field names (task_response / grammar) and would silently produce
// blank labels if reused here.
export const PROGRESS_CRITERION_LABELS: Record<string, string> = {
  task_response: "Task response",
  coherence_cohesion: "Coherence & cohesion",
  lexical_resource: "Lexical resource",
  grammar: "Grammar",
};

function mapEvent(e: any): SkillScoreEvent | null {
  const rs = e?.released_score;
  if (!rs) return null;
  return {
    essayId: String(e.essay_id ?? ""),
    recordedAt: String(e.recorded_at ?? e.created_at ?? ""),
    overallBand: Number(rs.overall_band ?? 0),
    criteriaBands: rs.criteria_bands ?? {},
    stableForTrend: Boolean(e.stable_for_trend),
  };
}

export function getSkillProgress(sessionDir: string | undefined | null): SkillProgress | undefined {
  if (!sessionDir) return undefined;
  // Prefer the persisted variant (02f) — it's the canonical cross-session
  // accumulation; 02e is the same shape written earlier in the same run and
  // is a safe fallback if 02f wasn't produced for some reason.
  const persistedFile = path.join(sessionDir, "02f_gold_progress_tracker_persisted.json");
  const liveFile = path.join(sessionDir, "02e_gold_progress_tracker.json");
  const file = fs.existsSync(persistedFile) ? persistedFile : liveFile;
  if (!fs.existsSync(file)) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    const events = (Array.isArray(raw.score_events) ? raw.score_events : [])
      .map(mapEvent)
      .filter((e: SkillScoreEvent | null): e is SkillScoreEvent => e !== null);
    const stableEvents = (Array.isArray(raw.stable_score_events) ? raw.stable_score_events : [])
      .map(mapEvent)
      .filter((e: SkillScoreEvent | null): e is SkillScoreEvent => e !== null);
    return {
      events,
      stableEvents,
      lastUpdatedAt: String(raw.last_updated_at ?? ""),
    };
  } catch (e) {
    console.error(`[ST.ELLA] Could not read progress tracker from ${sessionDir}:`, e);
    return undefined;
  }
}

// Per-skill trend: [{ criterion, label, points: [{essayId, recordedAt, band, stable}] }]
export function perSkillTrend(progress: SkillProgress) {
  const criteria = Object.keys(PROGRESS_CRITERION_LABELS);
  return criteria.map((criterion) => ({
    criterion,
    label: PROGRESS_CRITERION_LABELS[criterion],
    points: progress.events
      .filter((e) => typeof e.criteriaBands[criterion] === "number")
      .map((e) => ({
        essayId: e.essayId,
        recordedAt: e.recordedAt,
        band: e.criteriaBands[criterion],
        stable: e.stableForTrend,
      })),
  }));
}
