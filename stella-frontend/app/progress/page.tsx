import { redirect } from "next/navigation";
import { currentUser } from "@/lib/server/auth";
import { practiceResultsFor, submissionsFor } from "@/lib/server/store";
import { CRITERION_LABELS } from "@/lib/types";
import { getSkillProgress, perSkillTrend } from "@/lib/server/progress";

export default async function ProgressPage() {
  const user = await currentUser();
  if (!user) return null;
  if (user.role === "trainer") redirect("/trainer");

  const evals = submissionsFor(user.id)
    .filter((s) => s.status === "done" && s.report)
    .reverse();
  const practices = practiceResultsFor(user.id);
  const bands = evals.map((s) => s.report!.score_summary.holistic_band);
  const goal = user.intake?.goalBand ?? 7;

  // v18 (session-audit Finding 5): the real per-skill trend data, accumulated
  // across every session for this student — previously computed correctly by
  // the pipeline but never read here. Pulled from the latest submission's
  // session dir since that's where the cross-session accumulation lives.
  const latestSessionDir = evals[evals.length - 1]?.sessionDir;
  const skillProgress = getSkillProgress(latestSessionDir);
  const skillTrend = skillProgress ? perSkillTrend(skillProgress) : [];

  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-2xl font-semibold">Your progress</h1>

      <div className="card mt-4">
        <h2 className="font-medium">Band trend</h2>
        {bands.length === 0 ? (
          <p className="mt-2 text-sm text-ink-400">
            Submit your first essay to start tracking.
          </p>
        ) : (
          <BandChart bands={bands} goal={goal} />
        )}
      </div>

      {/* v18 (session-audit Finding 5): per-skill trend across every session,
          read from the pipeline's own progress-tracker artifact rather than
          re-derived from each submission's lightweight report — this is the
          data the "progress tracker is empty" complaint was actually asking
          for, previously computed correctly but never rendered anywhere. */}
      {skillTrend.some((s) => s.points.length > 0) && (
        <div className="card mt-4">
          <h2 className="font-medium">Skill trend</h2>
          <p className="mt-1 text-xs text-ink-400">
            Per-criterion bands across your evaluated essays. Faded points aren't stable enough
            yet to count as a confirmed trend.
          </p>
          <div className="mt-3 space-y-3">
            {skillTrend
              .filter((s) => s.points.length > 0)
              .map((s) => (
                <div key={s.criterion}>
                  <p className="text-xs font-medium uppercase tracking-wide text-ink-400">
                    {s.label}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {s.points.map((p, i) => (
                      <span
                        key={`${p.essayId}-${i}`}
                        className={
                          "rounded-full px-2.5 py-0.5 text-xs font-medium " +
                          (p.stable
                            ? "bg-brand-50 text-brand-800"
                            : "bg-ink-50 text-ink-400")
                        }
                        title={new Date(p.recordedAt).toLocaleDateString()}
                      >
                        {p.band}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </div>
      )}

      {evals.length > 0 && (
        <div className="card mt-4">
          <h2 className="font-medium">Evaluations</h2>
          <div className="mt-3 space-y-2">
            {evals
              .slice()
              .reverse()
              .map((s) => (
                <a
                  key={s.id}
                  href={`/writing/report/${s.id}`}
                  className="flex items-center justify-between rounded-card border border-brand-100 p-3 text-sm hover:border-brand-400"
                >
                  <span className="text-ink-600">
                    {new Date(s.createdAt).toLocaleDateString()}
                  </span>
                  <span className="flex gap-3">
                    {Object.entries(s.report!.score_summary.criteria_bands).map(
                      ([k, v]) => (
                        <span key={k} className="text-xs text-ink-400">
                          {(CRITERION_LABELS[k] ?? k).split(" ")[0]} {v}
                        </span>
                      )
                    )}
                    <span className="font-semibold text-brand-800">
                      {s.report!.score_summary.holistic_band.toFixed(1)}
                    </span>
                  </span>
                </a>
              ))}
          </div>
        </div>
      )}

      <div className="card mt-4">
        <h2 className="font-medium">Practice accuracy</h2>
        {practices.length === 0 ? (
          <p className="mt-2 text-sm text-ink-400">No practice sessions yet.</p>
        ) : (
          <div className="mt-3 flex items-end gap-1.5">
            {practices.slice(-20).map((p: any, i: number) => {
              const pct = p.total ? Math.round((p.correct / p.total) * 100) : 0;
              return (
                <div key={i} className="flex flex-col items-center gap-1" title={`${pct}%`}>
                  <div
                    className={`w-6 rounded-t ${pct >= 80 ? "bg-mint-400" : pct >= 50 ? "bg-brand-400" : "bg-rose-400"}`}
                    style={{ height: `${Math.max(8, pct * 0.8)}px` }}
                  />
                  <span className="text-[10px] text-ink-400">{pct}%</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function BandChart({ bands, goal }: { bands: number[]; goal: number }) {
  const W = 560;
  const H = 160;
  const min = 3;
  const max = 9;
  const x = (i: number) =>
    bands.length === 1 ? W / 2 : 20 + (i * (W - 40)) / (bands.length - 1);
  const y = (b: number) => H - 20 - ((b - min) / (max - min)) * (H - 40);
  const points = bands.map((b, i) => `${x(i)},${y(b)}`).join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="mt-3 w-full">
      <line
        x1="20"
        x2={W - 20}
        y1={y(goal)}
        y2={y(goal)}
        stroke="#1D9E75"
        strokeDasharray="6 4"
        strokeWidth="1.5"
      />
      <text x={W - 18} y={y(goal) + 4} fontSize="11" fill="#0F6E56">
        {goal.toFixed(1)}
      </text>
      {bands.length > 1 && (
        <polyline points={points} fill="none" stroke="#534AB7" strokeWidth="2.5" />
      )}
      {bands.map((b, i) => (
        <g key={i}>
          <circle cx={x(i)} cy={y(b)} r="5" fill="#534AB7" />
          <text x={x(i)} y={y(b) - 10} fontSize="12" textAnchor="middle" fill="#3C3489">
            {b.toFixed(1)}
          </text>
        </g>
      ))}
    </svg>
  );
}
