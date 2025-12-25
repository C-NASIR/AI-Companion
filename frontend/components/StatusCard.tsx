"use client";

import Link from "next/link";

import type {
  OperationalAlert,
  StatusDisplay,
  WorkflowSummary,
} from "../hooks/useChatRun";
import type { SpanAlert } from "../lib/backend";
import {
  NODE_TO_STEP_LABEL,
  WORKFLOW_STATUS_HINTS,
  WORKFLOW_STATUS_LABELS,
} from "../lib/chatUiConstants";

interface StatusCardProps {
  statusDisplay: StatusDisplay;
  currentRunId: string | null;
  runOutcome: string | null;
  runOutcomeReason: string | null;
  workflowSummary: WorkflowSummary;
  spanAlerts: SpanAlert[];
  operationalAlerts: OperationalAlert[];
}

export default function StatusCard({
  statusDisplay,
  currentRunId,
  runOutcome,
  runOutcomeReason,
  workflowSummary,
  spanAlerts,
  operationalAlerts,
}: StatusCardProps) {
  const workflowStatusLabel = workflowSummary.status
    ? WORKFLOW_STATUS_LABELS[workflowSummary.status]
    : null;
  const workflowStatusHint = workflowSummary.status
    ? WORKFLOW_STATUS_HINTS[workflowSummary.status]
    : null;
  const currentStepLabel =
    workflowSummary.currentStep &&
    (NODE_TO_STEP_LABEL[workflowSummary.currentStep] ||
      workflowSummary.currentStep);
  const attemptText =
    workflowSummary.currentAttempt && workflowSummary.currentAttempt > 1
      ? `Attempt ${workflowSummary.currentAttempt}`
      : workflowSummary.currentAttempt
      ? "Attempt 1"
      : null;
  const waitingEvents = workflowSummary.waitingForEvents?.events ?? [];
  const waitingForTool = waitingEvents.some((event) =>
    event.startsWith("tool.")
  );
  const waitingForRetrieval = waitingEvents.some((event) =>
    event.startsWith("retrieval.")
  );

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
          <div className="text-xs text-slate-500">
            <p className="font-mono">
              run_id: <span className="text-slate-200">{currentRunId}</span>
            </p>
            <Link
              href={`/runs/${currentRunId}/inspect`}
              className="inline-block text-indigo-300 hover:text-indigo-200"
              target="_blank"
              rel="noreferrer"
            >
              Open run inspector →
            </Link>
          </div>
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
      {operationalAlerts.length ? (
        <div className="mt-3 flex flex-col gap-2">
          {operationalAlerts.map((alert, index) => {
            const color =
              alert.type === "budget"
                ? "border-rose-500/40 bg-rose-500/10 text-rose-100"
                : alert.type === "degraded"
                ? "border-amber-400/40 bg-amber-400/10 text-amber-100"
                : "border-indigo-500/40 bg-indigo-500/10 text-indigo-100";
            return (
              <div
                key={`${alert.type}-${index}-${alert.ts}`}
                className={`rounded-xl border p-3 text-sm ${color}`}
              >
                <p className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                  {alert.title}
                </p>
                <p>{alert.message}</p>
              </div>
            );
          })}
        </div>
      ) : null}
      {spanAlerts.length ? (
        <div className="mt-3 flex flex-col gap-2">
          {spanAlerts.map((alert, index) => (
            <div
              key={`${alert.type}-${index}`}
              className="rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-3 text-sm text-indigo-100"
            >
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-indigo-300">
                {alert.title}
              </p>
              <p>{alert.message}</p>
            </div>
          ))}
        </div>
      ) : null}
      {workflowStatusLabel ? (
        <div className="mt-4 rounded-xl border border-slate-800/60 bg-slate-950/40 p-3 text-sm text-slate-200">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">
            Workflow status
          </p>
          <p className="text-lg font-semibold text-slate-100">
            {workflowStatusLabel}
          </p>
          {workflowStatusHint ? (
            <p className="text-xs text-slate-400">{workflowStatusHint}</p>
          ) : null}
          {currentStepLabel ? (
            <p className="mt-2 text-xs text-slate-300">
              Current step:{" "}
              <span className="font-semibold text-slate-100">
                {currentStepLabel}
              </span>
              {attemptText ? (
                <span className="ml-2 text-slate-400">{attemptText}</span>
              ) : null}
            </p>
          ) : null}
          {workflowSummary.retry ? (
            <p className="mt-1 text-xs text-amber-300">
              Retrying {workflowSummary.retry.step} (attempt{" "}
              {workflowSummary.retry.attempt}) in{" "}
              {workflowSummary.retry.backoffSeconds}s.
            </p>
          ) : null}
          {workflowSummary.waitingForEvents ? (
            <p className="mt-1 text-xs text-slate-400">
              Waiting for events:{" "}
              <span className="font-mono">
                {workflowSummary.waitingForEvents.events.join(", ")}
              </span>
              {workflowSummary.waitingForEvents.reason
                ? ` — ${workflowSummary.waitingForEvents.reason}`
                : ""}
            </p>
          ) : null}
          {waitingForTool ? (
            <p className="mt-1 text-xs text-amber-300">
              Blocked on tool execution. The run will resume once the tool
              finishes.
            </p>
          ) : null}
          {waitingForRetrieval ? (
            <p className="mt-1 text-xs text-sky-300">
              Retrieval still in progress. Knowledge chunks will appear when
              ready.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
