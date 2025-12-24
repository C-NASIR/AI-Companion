"use client";

import type { StatusDisplay, WorkflowSummary } from "../hooks/useChatRun";
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
}

export default function StatusCard({
  statusDisplay,
  currentRunId,
  runOutcome,
  runOutcomeReason,
  workflowSummary,
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
        </div>
      ) : null}
    </div>
  );
}
