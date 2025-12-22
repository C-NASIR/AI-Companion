"use client";

export type StepVisualState = "pending" | "started" | "completed";

interface Step {
  label: string;
  state: StepVisualState;
}

interface StepsPanelProps {
  steps: Step[];
}

const STATE_STYLES: Record<StepVisualState, string> = {
  pending: "border-slate-700 text-slate-500",
  started: "border-amber-400 text-amber-300",
  completed: "border-emerald-500 text-emerald-400",
};

const STATE_LABELS: Record<StepVisualState, string> = {
  pending: "Pending",
  started: "In progress",
  completed: "Done",
};

export default function StepsPanel({ steps }: StepsPanelProps) {
  return (
    <div className="w-full rounded-2xl border border-slate-800/70 bg-slate-900/50 p-4 shadow-inner md:w-80">
      <h3 className="text-lg font-semibold text-slate-100">Steps</h3>
      <ol className="mt-4 space-y-3">
        {steps.map(({ label, state }) => (
          <li
            key={label}
            className="flex items-center justify-between gap-3 rounded-xl border border-slate-800/50 bg-slate-950/50 px-3 py-2"
          >
            <div>
              <p className="text-sm font-semibold text-slate-100">{label}</p>
              <p className="text-xs text-slate-500">{STATE_LABELS[state]}</p>
            </div>
            <span
              className={`flex h-8 w-8 items-center justify-center rounded-full border text-xs font-semibold uppercase tracking-wide ${STATE_STYLES[state]}`}
            >
              {state === "completed"
                ? "✓"
                : state === "started"
                ? "…"
                : "•"}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
