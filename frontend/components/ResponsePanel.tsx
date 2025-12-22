"use client";

import { DECISION_LABELS } from "../lib/chatUiConstants";
import type { DecisionEntry } from "../lib/chatTypes";

interface ResponsePanelProps {
  output: string;
  decisions: DecisionEntry[];
}

export default function ResponsePanel({
  output,
  decisions,
}: ResponsePanelProps) {
  return (
    <div className="flex flex-1 flex-col rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-inner">
      <h2 className="text-xl font-semibold text-slate-100">Response</h2>
      <p className="text-xs text-slate-500">
        Output updates whenever an output event arrives.
      </p>
      <div className="mt-3 flex-1 rounded-xl border border-slate-800/60 bg-slate-950/50 p-3">
        {output ? (
          <pre className="h-full overflow-y-auto whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-slate-100">
            {output}
          </pre>
        ) : (
          <p className="text-slate-500">
            No output yet. Status updates will arrive before text does.
          </p>
        )}
      </div>
      <div className="mt-3 rounded-xl border border-slate-800/60 bg-slate-950/40 p-3">
        <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-500">
          Decisions
        </p>
        {decisions.length === 0 ? (
          <p className="mt-2 text-xs text-slate-500">
            Decision events will appear here as the run progresses.
          </p>
        ) : (
          <ul className="mt-2 space-y-2 text-sm text-slate-200">
            {decisions.map((entry) => {
              const label = DECISION_LABELS[entry.name] ?? entry.name;
              return (
                <li
                  key={`${entry.ts}-${entry.name}`}
                  className="rounded-lg border border-slate-800/50 bg-slate-900/60 p-2"
                >
                  <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                    {label}
                  </p>
                  <p className="font-mono text-sm text-slate-100">
                    {entry.value}
                  </p>
                  {entry.notes ? (
                    <p className="text-xs text-slate-500">{entry.notes}</p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
