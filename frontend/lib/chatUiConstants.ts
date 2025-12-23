import type { ChatMode } from "./backend";
import type { StepVisualState } from "./chatTypes";

export const MODES: Array<{ value: ChatMode; label: string }> = [
  { value: "answer", label: "Answer" },
  { value: "research", label: "Research" },
  { value: "summarize", label: "Summarize" },
] as const;

export const STEP_LABELS = [
  "Receive",
  "Plan",
  "Respond",
  "Tool requested",
  "Tool executing",
  "Tool completed",
  "Verify",
  "Finalize",
] as const;

export const TOOL_STEP_LABELS = {
  requested: "Tool requested",
  executing: "Tool executing",
  completed: "Tool completed",
} as const;

export const NODE_TO_STEP_LABEL: Record<string, (typeof STEP_LABELS)[number]> = {
  receive: "Receive",
  plan: "Plan",
  respond: "Respond",
  verify: "Verify",
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

export const DECISION_LABELS: Record<string, string> = {
  plan_type: "Plan",
  response_strategy: "Response Strategy",
  verification: "Verification",
  outcome: "Outcome",
  tool_intent: "Tool Intent",
  tool_result: "Tool Result",
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
