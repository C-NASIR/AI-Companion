import type { ChatMode, WorkflowStatusValue } from "./backend";
import type { StepVisualState } from "./chatTypes";

export const MODES: Array<{ value: ChatMode; label: string }> = [
  { value: "answer", label: "Answer" },
  { value: "research", label: "Research" },
  { value: "summarize", label: "Summarize" },
] as const;

export const STEP_LABELS = [
  "Receive",
  "Plan",
  "Retrieve",
  "Retrieval started",
  "Retrieval completed",
  "Respond",
  "Tool discovered",
  "Tool requested",
  "Tool executed",
  "Tool denied",
  "Verify",
  "Approval",
  "Finalize",
] as const;

export const TOOL_STEP_LABELS = {
  discovered: "Tool discovered",
  requested: "Tool requested",
  executed: "Tool executed",
  denied: "Tool denied",
} as const;

export const NODE_TO_STEP_LABEL: Record<string, (typeof STEP_LABELS)[number]> = {
  receive: "Receive",
  plan: "Plan",
  retrieve: "Retrieve",
  respond: "Respond",
  verify: "Verify",
  maybe_approve: "Approval",
  finalize: "Finalize",
};

export const FEEDBACK_REASONS = [
  "Incorrect",
  "Incomplete",
  "Latency",
  "Off-topic",
  "Other",
] as const;

export type StepLabel = (typeof STEP_LABELS)[number];
export type StepUpdateState = "started" | "completed" | "failed";
export type StatusValue = "received" | "thinking" | "responding" | "complete";

export type StepStateMap = Record<StepLabel, StepVisualState>;

export const STATUS_LABELS: Record<StatusValue, string> = {
  received: "Received",
  thinking: "Thinking",
  responding: "Responding",
  complete: "Complete",
};

export const STATUS_HINTS: Record<StatusValue, string> = {
  received: "Intent captured and stored in the run timeline.",
  thinking: "Planning or verification is in progress.",
  responding: "Output chunks are flowing through the event log.",
  complete: "Run finished. Review output or send feedback.",
};

export const WORKFLOW_STATUS_LABELS: Record<WorkflowStatusValue, string> = {
  running: "Running",
  waiting_for_approval: "Waiting for approval",
  retrying: "Retrying",
  completed: "Workflow completed",
  failed: "Workflow failed",
};

export const WORKFLOW_STATUS_HINTS: Record<WorkflowStatusValue, string> = {
  running: "Steps are executing via the durable workflow engine.",
  waiting_for_approval:
    "Execution paused until you approve or reject the pending step.",
  retrying: "A step failed and will re-run after the configured backoff.",
  completed: "Workflow reached the finalize step successfully.",
  failed: "Retries exhausted or an unrecoverable error occurred.",
};

export const DECISION_LABELS: Record<string, string> = {
  plan_type: "Plan",
  response_strategy: "Response Strategy",
  verification: "Verification",
  outcome: "Outcome",
  tool_selected: "Tool Selection",
  available_tools: "Available Tools",
  tool_result: "Tool Result",
  retrieval_chunks: "Retrieval",
  grounding: "Grounding",
};

export const generateRunId = () => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `fallback-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export const createInitialSteps = (): StepStateMap => {
  const map: Partial<Record<StepLabel, StepVisualState>> = {};
  STEP_LABELS.forEach((label) => {
    map[label] = "pending";
  });
  return map as StepStateMap;
};

export const isStepLabel = (value: unknown): value is StepLabel =>
  typeof value === "string" && STEP_LABELS.includes(value as StepLabel);

export const isStepState = (value: unknown): value is StepUpdateState =>
  value === "started" || value === "completed" || value === "failed";

export const isStatusValue = (value: unknown): value is StatusValue =>
  value === "received" ||
  value === "thinking" ||
  value === "responding" ||
  value === "complete";
