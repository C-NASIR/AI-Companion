"use client";

import { useMemo, type ReactNode } from "react";

import type {
  GuardrailEventEntry,
  GuardrailSummaryState,
  InjectionSignalEntry,
  RetrievedChunkEntry,
  SanitizedContextEntry,
} from "../lib/chatTypes";

interface SafetyPanelProps {
  guardrailSummary: GuardrailSummaryState | null;
  guardrailEvents: GuardrailEventEntry[];
  sanitizedContext: SanitizedContextEntry[];
  injectionSignals: InjectionSignalEntry[];
  retrievedChunks: RetrievedChunkEntry[];
  toolDeniedReason: string | null;
}

interface SectionProps {
  title: string;
  emptyMessage: string;
  children: ReactNode;
  hasContent: boolean;
}

function SafetySection({
  title,
  emptyMessage,
  children,
  hasContent,
}: SectionProps) {
  return (
    <div className="rounded-xl border border-slate-800/60 bg-slate-950/40 p-3">
      <p className="text-xs font-semibold uppercase tracking-[0.3em] text-slate-400">
        {title}
      </p>
      {hasContent ? (
        <div className="mt-2 space-y-2 text-sm text-slate-200">{children}</div>
      ) : (
        <p className="mt-2 text-xs text-slate-500">{emptyMessage}</p>
      )}
    </div>
  );
}

export default function SafetyPanel({
  guardrailSummary,
  guardrailEvents,
  sanitizedContext,
  injectionSignals,
  retrievedChunks,
  toolDeniedReason,
}: SafetyPanelProps) {
  const chunkLookup = useMemo(() => {
    const map = new Map<string, RetrievedChunkEntry>();
    retrievedChunks.forEach((chunk) => map.set(chunk.chunk_id, chunk));
    return map;
  }, [retrievedChunks]);

  const sanitizedEntries = useMemo(() => {
    const merged = new Map<string, SanitizedContextEntry>();
    sanitizedContext.forEach((entry) => {
      if (!entry.applied) {
        return;
      }
      merged.set(entry.chunkId, entry);
    });
    return Array.from(merged.values());
  }, [sanitizedContext]);

  const guardrailBanner =
    guardrailSummary?.status === "refused" ||
    guardrailSummary?.status === "guardrail_triggered";

  return (
    <div className="rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-inner">
      <div className="mb-3">
        <h2 className="text-xl font-semibold text-slate-100">
          Safety activity
        </h2>
        <p className="text-xs text-slate-500">
          Shows real-time guardrail, sanitization, and signal events.
        </p>
      </div>
      <div
        className={`rounded-xl border p-3 text-sm ${
          guardrailBanner
            ? "border-rose-800/60 bg-rose-900/30 text-rose-100"
            : "border-emerald-800/40 bg-emerald-900/20 text-emerald-100"
        }`}
      >
        {guardrailBanner ? (
          <>
            <p className="text-xs font-semibold uppercase tracking-[0.3em]">
              Guardrail active
            </p>
            <p className="mt-1 text-base font-semibold">
              {guardrailSummary?.reason || "Safety layer stopped execution."}
            </p>
            <p className="text-xs text-rose-200">
              Layer: {guardrailSummary?.layer ?? "unknown"} • Threat:{" "}
              {guardrailSummary?.threatType ?? "unknown"}
            </p>
          </>
        ) : (
          <>
            <p className="text-xs font-semibold uppercase tracking-[0.3em]">
              Guardrails clear
            </p>
            <p className="mt-1 text-base font-semibold">
              No blocking guardrail events.
            </p>
            <p className="text-xs text-emerald-200">
              Injection detections or sanitization steps will appear below.
            </p>
          </>
        )}
      </div>
      {toolDeniedReason ? (
        <div className="mt-3 rounded-xl border border-amber-800/40 bg-amber-900/20 p-3 text-xs text-amber-100">
          Tool denied: {toolDeniedReason}
        </div>
      ) : null}
      <div className="mt-3 flex flex-col gap-3">
        <SafetySection
          title="Sanitized context"
          emptyMessage="No retrieved chunks required sanitization."
          hasContent={sanitizedEntries.length > 0}
        >
          {sanitizedEntries.map((entry) => {
            const chunk = chunkLookup.get(entry.chunkId);
            const titleValue =
              chunk &&
              typeof chunk.metadata?.["title"] === "string" &&
              chunk.metadata["title"]
                ? (chunk.metadata["title"] as string)
                : chunk?.document_id;
            return (
              <div
                key={`${entry.chunkId}-${entry.ts}`}
                className="rounded-lg border border-slate-800/50 bg-slate-900/50 p-2"
              >
                <p className="text-sm font-semibold text-slate-100">
                  {titleValue || entry.chunkId}
                </p>
                <p className="text-xs font-mono text-slate-500">
                  {entry.chunkId}
                </p>
                <p className="mt-1 text-xs text-slate-300">
                  {entry.notes || "Imperative or executable text removed."}
                </p>
              </div>
            );
          })}
        </SafetySection>
        <SafetySection
          title="Guardrail events"
          emptyMessage="No guardrail interventions recorded."
          hasContent={guardrailEvents.length > 0}
        >
          {guardrailEvents.map((entry, index) => (
            <div
              key={`${entry.ts}-${entry.layer}-${index}`}
              className="rounded-lg border border-rose-800/30 bg-rose-950/30 p-2"
            >
              <p className="text-sm font-semibold text-rose-100">
                {entry.layer} • {entry.threatType}
              </p>
              <p className="text-xs text-rose-200">
                {entry.notes || "Guardrail triggered."}
              </p>
              {entry.confidence ? (
                <p className="text-[0.65rem] uppercase tracking-[0.3em] text-rose-300">
                  Confidence: {entry.confidence}
                </p>
              ) : null}
            </div>
          ))}
        </SafetySection>
        <SafetySection
          title="Injection signals"
          emptyMessage="No prompt injection signals detected."
          hasContent={injectionSignals.length > 0}
        >
          {injectionSignals.map((entry, index) => (
            <div
              key={`${entry.ts}-${entry.location}-${index}`}
              className="rounded-lg border border-sky-800/30 bg-sky-950/30 p-2"
            >
              <p className="text-sm font-semibold text-sky-100">
                {entry.location} • {entry.pattern ?? "pattern detected"}
              </p>
              {entry.confidence ? (
                <p className="text-xs uppercase tracking-[0.3em] text-sky-300">
                  Confidence: {entry.confidence}
                </p>
              ) : null}
            </div>
          ))}
        </SafetySection>
      </div>
    </div>
  );
}
