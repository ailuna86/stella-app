// v15: complete rewrite. The old version of this file read
// `{studentId}_study_plan.json` from a `learner_profiles` directory — that
// file has never existed for any student, on either pipeline. Traced the
// real artifact by grepping the whole pipeline folder for "week_number" /
// "study_plan" (zero matches) and then for "roadmap" (real matches):
// gold_lie_profile_builder_standalone_v1_4_3.py writes a real, much simpler
// per-session file, `08c_gold_learning_roadmap.json`, inside each
// submission's session directory (same pattern as loadWritingCoach /
// loadRevisionWorkspace / loadLretSession in goldPipeline.ts) — confirmed
// against real files in gold_sessions/ and gold_web_sessions/.
//
// The real shape is NOT a multi-week calendar — it's a 3-phase "what to do
// next" sequence regenerated after every essay: phase 1's focus/service are
// dynamic (pulled from that essay's highest-priority weakness), phases 2-3
// are a fixed practice → essay-revision follow-through. This is why "Your
// study plan" always showed the empty state: it was looking for a file that
// was never going to exist, regardless of how many essays were evaluated.
import fs from "fs";
import path from "path";

export interface LearningRoadmapPhase {
  phase: number;
  focus: string;
  service: string;
  goal: string;
}

export interface LearningRoadmap {
  createdAt: string;
  phases: LearningRoadmapPhase[];
}

const SERVICE_LABELS: Record<string, string> = {
  writing_coach: "Writing Coach",
  practice: "Daily practice",
  essay_revision: "Essay revision",
};

const SERVICE_ICONS: Record<string, string> = {
  writing_coach: "edit_note",
  practice: "schedule",
  essay_revision: "history_edu",
};

export function getLearningRoadmap(sessionDir: string | undefined | null): LearningRoadmap | undefined {
  if (!sessionDir) return undefined;
  const file = path.join(sessionDir, "08c_gold_learning_roadmap.json");
  if (!fs.existsSync(file)) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(file, "utf8"));
    return {
      createdAt: raw.created_at ?? "",
      phases: Array.isArray(raw.roadmap)
        ? raw.roadmap.map((p: any) => ({
            phase: p.phase ?? 0,
            focus: String(p.focus ?? "").replace(/_/g, " "),
            service: p.service ?? "",
            goal: p.goal ?? "",
          }))
        : [],
    };
  } catch (e) {
    console.error(`[ST.ELLA] Could not read learning roadmap from ${sessionDir}:`, e);
    return undefined;
  }
}

export function serviceLabel(service: string): string {
  return SERVICE_LABELS[service] ?? service.replace(/_/g, " ");
}

export function serviceIcon(service: string): string {
  return SERVICE_ICONS[service] ?? "arrow_forward";
}
