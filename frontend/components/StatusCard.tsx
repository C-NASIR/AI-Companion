"use client";

import type { StatusDisplay } from "../hooks/useChatRun";

interface StatusCardProps {
  statusDisplay: StatusDisplay;
  currentRunId: string | null;
  runOutcome: string | null;
  runOutcomeReason: string | null;
}

export default function StatusCard({
  statusDisplay,
  currentRunId,
  runOutcome,
  runOutcomeReason,
}: StatusCardProps) {
  return (
    <div className="rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-lg">
      <div className="flex flex-col gap-1">
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
          Current status
        </p>
        <p className="text-2xl font-bold text-slate-100">
          {statusDisplay.label}
        </p>
        <p className="text-sm text-slate-400">{statusDisplay.hint}</p>
        {currentRunId ? (
          <p className="text-xs font-mono text-slate-500">
            run_id: <span className="text-slate-200">{currentRunId}</span>
          </p>
        ) : (
          <p className="text-xs text-slate-500">
            Run id will appear once you start streaming.
          </p>
        )}
        {runOutcome ? (
          <p className="text-xs text-slate-400">
            Outcome:{" "}
            <span className="font-semibold text-slate-200">{runOutcome}</span>
            {runOutcomeReason ? ` — ${runOutcomeReason}` : ""}
          </p>
        ) : null}
      </div>
    </div>
  );
}
