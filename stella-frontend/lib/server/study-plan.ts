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
import { currentRoadmapPath } from "@/lib/server/goldPipeline";

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

// v16: added vocabulary_coach. The roadmap engine
// (gold_lie_profile_builder_standalone_v1_4_5.py) now emits a real
// vocabulary_coach phase between practice and essay_revision -- this map
// previously had no entry for it at all, which (combined with the engine
// never emitting that phase either) is why Vocabulary Coach never appeared
// anywhere in the roadmap UI, reported directly by the user.
const SERVICE_LABELS: Record<string, string> = {
  writing_coach: "Writing Coach",
  practice: "Daily practice",
  vocabulary_coach: "Vocabulary Coach",
  essay_revision: "Essay revision",
};

const SERVICE_ICONS: Record<string, string> = {
  writing_coach: "edit_note",
  practice: "schedule",
  vocabulary_coach: "translate",
  essay_revision: "history_edu",
};

// v22 (2026-07-23): now takes studentId too. Defect 1 fix (see goldPipeline.ts's
// refreshLearnerProfile() module comment) -- refreshLearnerProfile() no longer
// writes 08c_gold_learning_roadmap.json back into an essay's own session dir;
// it writes a per-student "current" file instead (currentRoadmapPath()). This
// reads that current file first (meaning at least one Practice/Coach/Vocab/
// Revision refresh or a new-essay reseed has happened for this student) and
// only falls back to the essay's own original 08c when no current file exists
// yet -- e.g. a brand-new student/essay where runGoldEvaluation's reseed
// hasn't run either (shouldn't happen post-v22 for any "done" essay, but kept
// as a safe degrade rather than an empty state regression for pre-v22 data).
export function getLearningRoadmap(
  studentId: string | undefined | null,
  sessionDir: string | undefined | null
): LearningRoadmap | undefined {
  const currentFile = studentId ? currentRoadmapPath(studentId) : null;
  const file =
    currentFile && fs.existsSync(currentFile)
      ? currentFile
      : sessionDir
      ? path.join(sessionDir, "08c_gold_learning_roadmap.json")
      : null;
  if (!file || !fs.existsSync(file)) return undefined;
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
    console.error(`[ST.ELLA] Could not read learning roadmap from ${file}:`, e);
    return undefined;
  }
}

export function serviceLabel(service: string): string {
  return SERVICE_LABELS[service] ?? service.replace(/_/g, " ");
}

export function serviceIcon(service: string): string {
  return SERVICE_ICONS[service] ?? "arrow_forward";
}
