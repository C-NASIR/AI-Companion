"use client";

import { useMemo } from "react";

import { DECISION_LABELS } from "../lib/chatUiConstants";
import type {
  DecisionEntry,
  RetrievedChunkEntry,
} from "../lib/chatTypes";

interface ResponsePanelProps {
  output: string;
  finalText: string;
  decisions: DecisionEntry[];
  retrievedChunks: RetrievedChunkEntry[];
  retrievalAttempted: boolean;
  runComplete: boolean;
}

const CITATION_PATTERN = /\[([\w\-\.:]+)\]/g;

export default function ResponsePanel({
  output,
  finalText,
  decisions,
  retrievedChunks,
  retrievalAttempted,
  runComplete,
}: ResponsePanelProps) {
  const displayOutput = runComplete && finalText ? finalText : output;

  const citedSources = useMemo(() => {
    if (!runComplete || retrievedChunks.length === 0) {
      return [];
    }
    const citedIds = new Set<string>();
    const text = finalText || output;
    for (const match of text.matchAll(CITATION_PATTERN)) {
      const id = match[1];
      if (id) citedIds.add(id);
    }
    if (citedIds.size === 0) {
      return [];
    }
    const ordered: RetrievedChunkEntry[] = [];
    citedIds.forEach((id) => {
      const chunk = retrievedChunks.find(
        (entry) => entry.chunk_id === id
      );
      if (chunk) {
        ordered.push(chunk);
      }
    });
    return ordered;
  }, [finalText, output, retrievedChunks, runComplete]);

  const noSourcesUsed =
    runComplete &&
    (!retrievalAttempted ||
      retrievedChunks.length === 0 ||
      citedSources.length === 0);

  return (
    <div className="flex flex-1 flex-col rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-inner">
      <h2 className="text-xl font-semibold text-slate-100">Response</h2>
      <p className="text-xs text-slate-500">
        Output updates whenever an output event arrives.
      </p>
      <div className="mt-3 flex-1 rounded-xl border border-slate-800/60 bg-slate-950/50 p-3">
        {displayOutput ? (
          <pre className="h-full overflow-y-auto whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-slate-100">
            {displayOutput}
          </pre>
        ) : (
          <p className="text-slate-500">
            No output yet. Status updates will arrive before text does.
          </p>
        )}
      </div>
      {runComplete ? (
        <div className="mt-3 rounded-xl border border-slate-800/60 bg-slate-950/40 p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-500">
            Sources
          </p>
          {noSourcesUsed ? (
            <p className="mt-2 text-sm text-slate-500">
              No sources were used for this answer.
            </p>
          ) : (
            <ul className="mt-2 space-y-2 text-sm text-slate-200">
              {citedSources.map((chunk) => {
                const titleValue =
                  chunk.metadata &&
                  typeof chunk.metadata["title"] === "string"
                    ? (chunk.metadata["title"] as string)
                    : chunk.document_id;
                const title = titleValue?.trim()
                  ? titleValue
                  : chunk.document_id;
                return (
                  <li
                    key={chunk.chunk_id}
                    className="rounded-lg border border-slate-800/50 bg-slate-900/60 p-2"
                  >
                    <details>
                      <summary className="flex cursor-pointer flex-col text-sm font-semibold text-slate-100">
                        <span>{title}</span>
                        <span className="text-xs font-mono text-slate-500">
                          {chunk.chunk_id}
                        </span>
                      </summary>
                      <p className="mt-2 whitespace-pre-wrap text-xs text-slate-300">
                        {chunk.text}
                      </p>
                    </details>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ) : null}
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
