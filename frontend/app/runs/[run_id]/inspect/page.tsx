"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchRunTrace,
  type SpanRecord,
  type TracePayload,
} from "../../../../lib/backend";

type InspectorPageProps = {
  params: { run_id: string };
};

interface TimelineEntry {
  span: SpanRecord;
  depth: number;
}

const KIND_STYLES: Record<string, string> = {
  workflow: "bg-emerald-500/20 text-emerald-200 border-emerald-400/40",
  intelligence: "bg-cyan-500/20 text-cyan-200 border-cyan-400/40",
  model: "bg-indigo-500/20 text-indigo-200 border-indigo-400/40",
  tool: "bg-orange-500/20 text-orange-200 border-orange-400/40",
  system: "bg-slate-500/20 text-slate-200 border-slate-400/40",
};

const STATUS_COLORS: Record<string, string> = {
  success: "text-emerald-300",
  failed: "text-rose-300",
  retried: "text-amber-300",
  waiting: "text-sky-300",
};

function formatDurationMs(span: SpanRecord): string {
  const ms =
    typeof span.duration_ms === "number"
      ? span.duration_ms
      : span.start_time && span.end_time
      ? Date.parse(span.end_time) - Date.parse(span.start_time)
      : null;
  if (!ms || Number.isNaN(ms) || ms < 0) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  if (ms < 60_000) {
    return `${(ms / 1000).toFixed(1)} s`;
  }
  return `${(ms / 60_000).toFixed(1)} min`;
}

function formatTimestamp(ts?: string | null): string {
  if (!ts) return "—";
  try {
    const date = new Date(ts);
    return `${date.toLocaleTimeString()} · ${date.toLocaleDateString()}`;
  } catch {
    return ts;
  }
}

function buildTimeline(trace: TracePayload | null): TimelineEntry[] {
  if (!trace?.spans?.length) {
    return [];
  }
  const nodes = new Map<string, { span: SpanRecord; children: SpanRecord[] }>();
  trace.spans.forEach((span) => {
    nodes.set(span.span_id, { span, children: [] });
  });
  trace.spans.forEach((span) => {
    const parentId = span.parent_span_id;
    if (!parentId) {
      return;
    }
    const parent = nodes.get(parentId);
    if (parent) {
      parent.children.push(span);
    }
  });
  nodes.forEach((node) => {
    node.children.sort(
      (a, b) => Date.parse(a.start_time ?? "") - Date.parse(b.start_time ?? "")
    );
  });
  const roots: SpanRecord[] = [];
  const rootCandidate =
    (trace.trace.root_span_id && nodes.get(trace.trace.root_span_id)?.span) ||
    null;
  if (rootCandidate) {
    roots.push(rootCandidate);
  } else {
    trace.spans.forEach((span) => {
      if (!span.parent_span_id || !nodes.has(span.parent_span_id)) {
        roots.push(span);
      }
    });
    roots.sort(
      (a, b) => Date.parse(a.start_time ?? "") - Date.parse(b.start_time ?? "")
    );
  }

  const ordered: TimelineEntry[] = [];
  const visited = new Set<string>();
  const walk = (span: SpanRecord, depth: number) => {
    if (visited.has(span.span_id)) {
      return;
    }
    visited.add(span.span_id);
    ordered.push({ span, depth });
    const childContainer = nodes.get(span.span_id);
    if (childContainer) {
      childContainer.children.forEach((child) => walk(child, depth + 1));
    }
  };
  roots.forEach((span) => walk(span, 0));
  return ordered;
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export default function RunInspectorPage({ params }: InspectorPageProps) {
  const runId = params.run_id;
  const [traceData, setTraceData] = useState<TracePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedSpanId, setSelectedSpanId] = useState<string | null>(null);

  const loadTrace = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchRunTrace(runId);
      if (!payload) {
        setTraceData(null);
        setSelectedSpanId(null);
        setError("Trace not found. Run may not have started tracing.");
        return;
      }
      setTraceData(payload);
      setSelectedSpanId((prev) => {
        if (prev && payload.spans.some((span) => span.span_id === prev)) {
          return prev;
        }
        return payload.trace.root_span_id || payload.spans[0]?.span_id || null;
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    loadTrace();
  }, [loadTrace]);

  const timelineEntries = useMemo(() => buildTimeline(traceData), [traceData]);
  const selectedSpan = useMemo(
    () =>
      timelineEntries.find((entry) => entry.span.span_id === selectedSpanId)
        ?.span ?? null,
    [timelineEntries, selectedSpanId]
  );
  const workflowSpans = useMemo(
    () =>
      timelineEntries
        .map((entry) => entry.span)
        .filter(
          (span) => span.kind === "workflow" && span.name !== "workflow.run"
        ),
    [timelineEntries]
  );

  return (
    <main className="min-h-screen bg-slate-950 p-6 text-slate-100">
      <header className="mb-6 flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-slate-500">
            Run inspector (dev only)
          </p>
          <h1 className="text-3xl font-bold text-white">
            Trace for run {runId}
          </h1>
          <p className="text-sm text-slate-400">
            Every span recorded for this run is displayed in start-time order.
          </p>
        </div>
        <div className="flex gap-3">
          <Link
            href="/"
            className="rounded-lg border border-slate-700/80 px-4 py-2 text-sm text-slate-200 hover:border-slate-500"
          >
            Back to app
          </Link>
          <button
            type="button"
            onClick={() => loadTrace()}
            className="rounded-lg border border-indigo-500/60 bg-indigo-500/10 px-4 py-2 text-sm font-semibold text-indigo-200 hover:bg-indigo-500/20"
          >
            Refresh trace
          </button>
        </div>
      </header>

      {error ? (
        <div className="mb-4 rounded-xl border border-rose-900/40 bg-rose-950/40 p-4 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      <section className="grid gap-5 lg:grid-cols-[2fr,1fr]">
        <div className="rounded-2xl border border-slate-800/60 bg-slate-900/50 p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">Timeline</h2>
            {loading ? (
              <span className="text-xs text-slate-400">Loading…</span>
            ) : null}
          </div>
          {timelineEntries.length === 0 ? (
            <p className="text-sm text-slate-400">
              No spans recorded yet. Start a run and refresh this view.
            </p>
          ) : (
            <ol className="flex flex-col">
              {timelineEntries.map(({ span, depth }) => {
                const kindStyle =
                  KIND_STYLES[span.kind] ??
                  "bg-slate-500/20 text-slate-200 border-slate-400/40";
                const statusColor =
                  STATUS_COLORS[span.status] ?? "text-slate-300";
                const isSelected = selectedSpanId === span.span_id;
                return (
                  <li key={span.span_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedSpanId(span.span_id)}
                      className={`mb-2 w-full rounded-xl border px-3 py-2 text-left transition ${
                        isSelected
                          ? "border-indigo-500 bg-indigo-500/10"
                          : "border-slate-800/70 hover:border-slate-600"
                      }`}
                      style={{ paddingLeft: `${depth * 16 + 12}px` }}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <span
                            className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${kindStyle}`}
                          >
                            {span.kind}
                          </span>
                          <span className="text-sm font-semibold text-slate-100">
                            {span.name}
                          </span>
                        </div>
                        <span className={`text-xs font-mono ${statusColor}`}>
                          {span.status}
                        </span>
                      </div>
                      <div className="mt-1 text-xs text-slate-400">
                        {formatTimestamp(span.start_time)}
                        <span className="mx-2 text-slate-600">•</span>
                        Duration {formatDurationMs(span)}
                      </div>
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>

        <div className="space-y-4 max-w-4xl">
          <div className="rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4">
            <h2 className="text-lg font-semibold text-white">Span details</h2>
            {selectedSpan ? (
              <div className="mt-3 space-y-2 text-sm text-slate-200">
                <p>
                  <span className="text-slate-400">Name:</span>{" "}
                  <span className="font-semibold">{selectedSpan.name}</span>
                </p>
                <p>
                  <span className="text-slate-400">Kind:</span>{" "}
                  {selectedSpan.kind}
                </p>
                <p>
                  <span className="text-slate-400">Status:</span>{" "}
                  <span className="font-semibold">{selectedSpan.status}</span>
                </p>
                <p>
                  <span className="text-slate-400">Duration:</span>{" "}
                  {formatDurationMs(selectedSpan)}
                </p>
                <p>
                  <span className="text-slate-400">Parent span:</span>{" "}
                  {selectedSpan.parent_span_id || "root"}
                </p>
                <p className="text-slate-400">Attributes:</p>
                <pre className="rounded-lg border border-slate-800/70 bg-slate-950/50 p-2 text-xs text-slate-200">
                  {Object.keys(selectedSpan.attributes || {}).length
                    ? safeJson(selectedSpan.attributes)
                    : "// none"}
                </pre>
                {selectedSpan.error ? (
                  <>
                    <p className="text-slate-400">Error:</p>
                    <pre className="rounded-lg border border-rose-900/40 bg-rose-950/30 p-2 text-xs text-rose-100 overflow-scroll">
                      {safeJson(selectedSpan.error)}
                    </pre>
                  </>
                ) : null}
              </div>
            ) : (
              <p className="mt-2 text-sm text-slate-400">
                Select a span from the timeline to inspect it.
              </p>
            )}
          </div>

          <div className="rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4">
            <h2 className="text-lg font-semibold text-white">Step summary</h2>
            {workflowSpans.length === 0 ? (
              <p className="text-sm text-slate-400">
                Workflow spans have not been recorded yet.
              </p>
            ) : (
              <ul className="mt-3 space-y-2 text-sm text-slate-200">
                {workflowSpans.map((span) => (
                  <li
                    key={span.span_id}
                    className="rounded-xl border border-slate-800/70 bg-slate-950/50 p-3"
                  >
                    <p className="font-semibold text-white">{span.name}</p>
                    <p className="text-xs text-slate-400">
                      Status: {span.status} · Duration {formatDurationMs(span)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      <section className="mt-6 rounded-2xl border border-slate-800/60 bg-slate-900/40 p-4 text-sm text-slate-300">
        <p>
          Trace status:{" "}
          <span className="font-semibold text-white">
            {traceData?.trace.status || "unknown"}
          </span>
        </p>
        <p>
          Started at {formatTimestamp(traceData?.trace.start_time)} — Finished
          at {formatTimestamp(traceData?.trace.end_time)}
        </p>
      </section>
    </main>
  );
}
