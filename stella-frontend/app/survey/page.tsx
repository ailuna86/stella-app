"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

// v9: extended onboarding survey. Exam type comes first because General
// branches out early (not supported yet) — no point asking a General
// student nine more questions about a product they can't use.
// v14: rebuilt to match the Stitch onboarding_survey screen — icon choice
// cards with a checkmark indicator, a fixed top progress bar, a step badge,
// and an explicit Back/Continue footer (select, then confirm) instead of
// auto-advancing on click.
const steps = [
  "Exam type",
  "Goal",
  "Exam date",
  "Residence",
  "Referral",
  "Purpose",
  "Level",
  "Experience",
  "Challenge",
  "Practice time",
] as const;

const COUNTRIES = [
  "Kyrgyzstan", "Kazakhstan", "Uzbekistan", "Tajikistan", "Turkmenistan",
  "Russia", "Turkey", "China", "India", "Pakistan", "Bangladesh", "Nepal",
  "Vietnam", "Philippines", "Indonesia", "Nigeria", "Egypt", "Saudi Arabia",
  "United Arab Emirates", "United Kingdom", "United States", "Canada",
  "Australia", "Germany", "Other",
];

const REFERRAL_OPTIONS = [
  { label: "Instagram", icon: "camera_alt" },
  { label: "A trainer or tutor", icon: "school" },
  { label: "A friend", icon: "group" },
  { label: "Google search", icon: "search" },
  { label: "Other", icon: "more_horiz" },
];

const PURPOSE_OPTIONS = [
  { label: "University admission", icon: "school" },
  { label: "Immigration or visa", icon: "flight_takeoff" },
  { label: "Work requirement", icon: "work" },
  { label: "Personal goal", icon: "star" },
];

const LEVEL_OPTIONS = [
  { label: "Beginner", icon: "looks_one" },
  { label: "Intermediate", icon: "looks_two" },
  { label: "Upper-intermediate", icon: "looks_3" },
  { label: "Advanced", icon: "looks_4" },
];

const ATTEMPT_OPTIONS = [
  { label: "First time taking IELTS", icon: "flag" },
  { label: "1-2 previous attempts", icon: "history" },
  { label: "3+ previous attempts", icon: "replay" },
];

const CHALLENGE_OPTIONS = [
  { label: "Grammar accuracy", icon: "spellcheck" },
  { label: "Vocabulary range", icon: "menu_book" },
  { label: "Staying on topic (task response)", icon: "assignment_turned_in" },
  { label: "Coherence & structure", icon: "account_tree" },
  { label: "Running out of time", icon: "schedule" },
];

const SUBTITLES: Record<number, string> = {
  0: "This helps us tailor your study plan and practice materials to your specific goals.",
  1: "We'll build your study plan around reaching this score.",
  2: "We'll pace your study plan around this date, if you have one.",
  3: "This helps us understand your exam context — no wrong answers here.",
  4: "Just curious — helps us know where to focus.",
  5: "This helps us tailor examples and feedback to what matters for your goal.",
  6: "Be honest — this just sets your starting point, not a judgment.",
  7: "This helps us calibrate how much support to give you early on.",
  8: "We'll prioritize practice and coaching around this first.",
  9: "We'll size your daily practice sessions to fit your schedule.",
};

export default function Survey() {
  const [step, setStep] = useState(0);
  const [examType, setExamType] = useState<"academic" | "general" | "">("");
  const [goalBand, setGoalBand] = useState<number | null>(null);
  const [examDate, setExamDate] = useState("");
  const [residence, setResidence] = useState("");
  const [referralSource, setReferralSource] = useState("");
  const [purpose, setPurpose] = useState("");
  const [selfLevel, setSelfLevel] = useState("");
  const [priorAttempts, setPriorAttempts] = useState("");
  const [biggestChallenge, setBiggestChallenge] = useState("");
  const [minutesPerDay, setMinutes] = useState<5 | 10 | 15 | null>(null);
  const router = useRouter();

  const back = () => step > 0 && setStep(step - 1);
  const next = () => (step < steps.length - 1 ? setStep(step + 1) : finish());

  async function submitIntake(partial: Record<string, unknown>) {
    await fetch("/api/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(partial),
    });
  }

  async function chooseGeneral() {
    setExamType("general");
    await submitIntake({ examType: "general" });
    router.push("/general-waitlist");
    router.refresh();
  }

  async function finish() {
    await submitIntake({
      examType: examType || "academic",
      goalBand: goalBand ?? 7.0,
      examDate,
      residence,
      referralSource,
      purpose,
      selfLevel,
      priorAttempts,
      biggestChallenge,
      minutesPerDay: minutesPerDay ?? 10,
    });
    router.push("/dashboard");
    router.refresh();
  }

  // Whether the current step has a value chosen (gates the Continue button).
  const canContinue =
    [
      !!examType,
      goalBand !== null,
      !!examDate,
      !!residence,
      !!referralSource,
      !!purpose,
      !!selfLevel,
      !!priorAttempts,
      !!biggestChallenge,
      minutesPerDay !== null,
    ][step] ?? false;

  return (
    <div className="flex min-h-[calc(100vh-4rem)] flex-col">
      <div className="fixed left-0 top-0 z-50 h-1.5 w-full bg-brand-50">
        <div
          className="h-full bg-brand-600 transition-all duration-500"
          style={{ width: `${((step + 1) / steps.length) * 100}%` }}
        />
      </div>

      <div className="mx-auto flex w-full max-w-2xl flex-1 flex-col px-4 py-10">
        <span className="mb-4 inline-block w-fit rounded-full bg-brand-50 px-3 py-1 text-xs font-medium text-brand-800">
          Step {step + 1} of {steps.length}
        </span>

        {step === 0 && (
          <Shell title="Which IELTS test are you taking?" subtitle={SUBTITLES[0]}>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <ChoiceCard
                icon="menu_book"
                title="Academic"
                description="For university admission or professional registration."
                selected={examType === "academic"}
                onClick={() => setExamType("academic")}
              />
              <ChoiceCard
                icon="work"
                title="General Training"
                description="For migration, work experience, or secondary education."
                selected={examType === "general"}
                onClick={chooseGeneral}
              />
            </div>
            <div className="mt-4 flex items-start gap-3 rounded-xl border border-brand-100 bg-brand-50/40 p-4">
              <span className="material-symbols-outlined text-brand-600">info</span>
              <p className="text-sm leading-relaxed text-ink-600">
                ST.ELLA currently coaches Academic Writing (Task 1 report/graph + Task 2 essay).
                General Training support is coming soon — choosing it puts you on our
                early-access waitlist instead of the survey.
              </p>
            </div>
          </Shell>
        )}

        {step === 1 && (
          <Shell title="What band score do you need?" subtitle={SUBTITLES[1]}>
            <div className="grid grid-cols-4 gap-2">
              {[5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0].map((b) => (
                <button
                  key={b}
                  onClick={() => setGoalBand(b)}
                  className={`rounded-xl border p-4 text-lg font-medium transition ${
                    goalBand === b
                      ? "border-brand-600 bg-brand-50 text-brand-800"
                      : "border-brand-100 hover:border-brand-400"
                  }`}
                >
                  {b.toFixed(1)}
                </button>
              ))}
            </div>
          </Shell>
        )}

        {step === 2 && (
          <Shell title="When is your exam?" subtitle={SUBTITLES[2]}>
            <div className="flex items-center gap-2 rounded-xl border border-brand-100 bg-white p-2 pl-4 focus-within:border-brand-400">
              <span className="material-symbols-outlined text-brand-600">event</span>
              <input
                type="date"
                className="w-full p-2 text-sm outline-none"
                value={examDate}
                onChange={(e) => setExamDate(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="mt-2 text-sm font-medium text-brand-600 hover:text-brand-800"
              onClick={() => {
                setExamDate("not-booked");
                next();
              }}
            >
              I haven&apos;t booked yet
            </button>
          </Shell>
        )}

        {step === 3 && (
          <Shell title="Where are you based?" subtitle={SUBTITLES[3]}>
            <div className="flex items-center gap-2 rounded-xl border border-brand-100 bg-white p-2 pl-4 focus-within:border-brand-400">
              <span className="material-symbols-outlined text-brand-600">public</span>
              <select
                className="w-full bg-transparent p-2 text-sm outline-none"
                value={residence}
                onChange={(e) => setResidence(e.target.value)}
              >
                <option value="">Select a country</option>
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
          </Shell>
        )}

        {step === 4 && (
          <Shell title="How did you hear about us?" subtitle={SUBTITLES[4]}>
            <div className="space-y-2">
              {REFERRAL_OPTIONS.map((opt) => (
                <ChoiceCard
                  key={opt.label}
                  icon={opt.icon}
                  title={opt.label}
                  selected={referralSource === opt.label}
                  onClick={() => setReferralSource(opt.label)}
                  compact
                />
              ))}
            </div>
          </Shell>
        )}

        {step === 5 && (
          <Shell title="What's this score for?" subtitle={SUBTITLES[5]}>
            <div className="space-y-2">
              {PURPOSE_OPTIONS.map((opt) => (
                <ChoiceCard
                  key={opt.label}
                  icon={opt.icon}
                  title={opt.label}
                  selected={purpose === opt.label}
                  onClick={() => setPurpose(opt.label)}
                  compact
                />
              ))}
            </div>
          </Shell>
        )}

        {step === 6 && (
          <Shell title="How would you rate your current English writing?" subtitle={SUBTITLES[6]}>
            <div className="space-y-2">
              {LEVEL_OPTIONS.map((opt) => (
                <ChoiceCard
                  key={opt.label}
                  icon={opt.icon}
                  title={opt.label}
                  selected={selfLevel === opt.label}
                  onClick={() => setSelfLevel(opt.label)}
                  compact
                />
              ))}
            </div>
          </Shell>
        )}

        {step === 7 && (
          <Shell title="Have you taken IELTS before?" subtitle={SUBTITLES[7]}>
            <div className="space-y-2">
              {ATTEMPT_OPTIONS.map((opt) => (
                <ChoiceCard
                  key={opt.label}
                  icon={opt.icon}
                  title={opt.label}
                  selected={priorAttempts === opt.label}
                  onClick={() => setPriorAttempts(opt.label)}
                  compact
                />
              ))}
            </div>
          </Shell>
        )}

        {step === 8 && (
          <Shell title="What's your biggest writing challenge?" subtitle={SUBTITLES[8]}>
            <div className="space-y-2">
              {CHALLENGE_OPTIONS.map((opt) => (
                <ChoiceCard
                  key={opt.label}
                  icon={opt.icon}
                  title={opt.label}
                  selected={biggestChallenge === opt.label}
                  onClick={() => setBiggestChallenge(opt.label)}
                  compact
                />
              ))}
            </div>
          </Shell>
        )}

        {step === 9 && (
          <Shell title="How much time can you practice daily?" subtitle={SUBTITLES[9]}>
            <div className="grid grid-cols-3 gap-3">
              {([5, 10, 15] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMinutes(m)}
                  className={`flex flex-col items-center gap-1 rounded-xl border p-5 font-medium transition ${
                    minutesPerDay === m
                      ? "border-brand-600 bg-brand-50 text-brand-800"
                      : "border-brand-100 hover:border-brand-400"
                  }`}
                >
                  <span className="material-symbols-outlined">schedule</span>
                  {m} min
                </button>
              ))}
            </div>
          </Shell>
        )}

        <div className="mt-auto flex items-center justify-between pt-10">
          <button
            type="button"
            onClick={back}
            disabled={step === 0}
            className="rounded-full px-6 py-2.5 text-sm font-medium text-ink-400 transition hover:text-ink-800 disabled:opacity-0"
          >
            Back
          </button>
          <button
            type="button"
            onClick={next}
            disabled={!canContinue}
            className="btn-primary !px-8 disabled:opacity-40"
          >
            {step === steps.length - 1 ? "Finish setup" : "Continue"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Shell({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-ink-900">{title}</h1>
      {subtitle && <p className="mt-1.5 text-sm text-ink-600">{subtitle}</p>}
      <div className="mt-6">{children}</div>
    </div>
  );
}

function ChoiceCard({
  icon,
  title,
  description,
  selected,
  onClick,
  compact,
}: {
  icon: string;
  title: string;
  description?: string;
  selected: boolean;
  onClick: () => void;
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group relative flex w-full items-start gap-3 rounded-xl border text-left transition ${
        compact ? "p-4" : "flex-col p-6"
      } ${selected ? "border-brand-600 bg-brand-50" : "border-brand-100 bg-white hover:border-brand-400"}`}
    >
      <div
        className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition ${
          selected ? "bg-brand-600 text-white" : "bg-brand-50 text-brand-600"
        } ${compact ? "" : "mb-3"}`}
      >
        <span className="material-symbols-outlined text-[20px]">{icon}</span>
      </div>
      <div>
        <p className="font-medium text-ink-900">{title}</p>
        {description && <p className="mt-1 text-xs text-ink-400">{description}</p>}
      </div>
      {selected && (
        <span className="material-symbols-outlined absolute right-4 top-4 text-brand-600">
          check_circle
        </span>
      )}
    </button>
  );
}
