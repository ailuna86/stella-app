// Nav intentionally hidden during first-time intake — the student cannot
// browse until the survey is complete.
export default function SurveyLayout({ children }: { children: React.ReactNode }) {
  return <div className="fixed inset-0 z-50 overflow-auto bg-white">{children}</div>;
}
