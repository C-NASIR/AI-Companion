import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchRunSpans,
  fetchRunState,
  fetchWorkflowState,
  startRunRequest,
  submitApprovalDecisionRequest,
  subscribeToRunEvents,
  type ApprovalDecision,
  type ChatMode,
  type RunEvent,
  type RunEventSubscription,
  type WorkflowStatusValue,
  type SpanAlert,
  type SpanRecord,
} from "../lib/backend";
import {
  NODE_TO_STEP_LABEL,
  createInitialSteps,
  generateRunId,
  isStatusValue,
  STATUS_HINTS,
  STATUS_LABELS,
  STEP_LABELS,
  TOOL_STEP_LABELS,
  type StatusValue,
  type StepStateMap,
} from "../lib/chatUiConstants";
import type {
  AvailableToolEntry,
  DecisionEntry,
  GuardrailEventEntry,
  GuardrailSummaryState,
  InjectionSignalEntry,
  RetrievedChunkEntry,
  SanitizedContextEntry,
  ToolContextState,
} from "../lib/chatTypes";

export interface SubmissionMeta {
  message: string;
  context: string;
  mode: ChatMode;
}

export interface StatusDisplay {
  label: string;
  hint: string;
}

export interface WorkflowSummary {
  status: WorkflowStatusValue | null;
  currentStep: string | null;
  currentAttempt: number | null;
  retry:
    | {
        step: string;
        attempt: number;
        backoffSeconds: number;
      }
    | null;
  waitingForEvents:
    | {
        events: string[];
        reason: string | null;
      }
    | null;
}

export interface ApprovalState {
  waiting: boolean;
  reason: string | null;
  decision: string | null;
  isSubmitting: boolean;
  error: string | null;
}

export interface OperationalAlert {
  type: "rate_limit" | "budget" | "degraded" | "system";
  title: string;
  message: string;
  ts: string;
}

interface UseChatRunArgs {
  message: string;
  context: string;
  mode: ChatMode;
  tenantId: string;
  userId: string;
}

const ACTIVE_RUN_STORAGE_KEY = "ai_companion_active_run";

interface StoredRun {
  runId: string;
  submission: SubmissionMeta | null;
}

const persistActiveRun = (value: StoredRun | null) => {
  if (typeof window === "undefined") return;
  if (!value) {
    window.sessionStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
    return;
  }
  window.sessionStorage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify(value));
};

const readStoredRun = (): StoredRun | null => {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(ACTIVE_RUN_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as StoredRun;
    if (parsed && typeof parsed.runId === "string") {
      return parsed;
    }
  } catch (error) {
    console.warn("Failed to parse stored run", error);
  }
  window.sessionStorage.removeItem(ACTIVE_RUN_STORAGE_KEY);
  return null;
};

const INITIAL_TOOL_CONTEXT: ToolContextState = {
  requestedTool: null,
  toolSource: null,
  toolPermissionScope: null,
  toolDeniedReason: null,
  lastToolStatus: null,
};

const isWorkflowStatusValue = (
  value: unknown
): value is WorkflowStatusValue =>
  value === "running" ||
  value === "waiting_for_approval" ||
  value === "retrying" ||
  value === "completed" ||
  value === "failed";

const toNumber = (value: unknown): number | null => {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
};

const parseTimestampMs = (value?: string | null): number => {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
};

const findLatestSpan = (
  spans: SpanRecord[],
  predicate: (span: SpanRecord) => boolean
): SpanRecord | null => {
  let latest: SpanRecord | null = null;
  let latestTs = -Infinity;
  for (const span of spans) {
    if (!predicate(span)) {
      continue;
    }
    const ts = parseTimestampMs(span.start_time);
    if (ts > latestTs) {
      latest = span;
      latestTs = ts;
    }
  }
  return latest;
};

const buildSpanAlerts = (spans: SpanRecord[]): SpanAlert[] => {
  const alerts: SpanAlert[] = [];
  const approvalSpan = findLatestSpan(
    spans,
    (span) =>
      span.status === "waiting" &&
      (span.attributes?.["error_type"] === "approval_wait" ||
        span.name.includes("approve"))
  );
  if (approvalSpan) {
    const reason =
      (approvalSpan.attributes?.["reason"] as string) ??
      (approvalSpan.error?.reason as string) ??
      "Waiting for approval";
    alerts.push({
      type: "approval",
      title: "Waiting for approval",
      message: reason,
    });
  }

  const retrySpan = findLatestSpan(
    spans,
    (span) => span.status === "retried"
  );
  if (retrySpan) {
    alerts.push({
      type: "retry",
      title: "Retry scheduled",
      message: `Retrying ${retrySpan.name}`,
    });
  }

  const toolSpan = findLatestSpan(
    spans,
    (span) =>
      span.kind === "tool" &&
      (!span.end_time || span.status === "running" || span.status === "waiting")
  );
  if (toolSpan) {
    const toolName =
      (toolSpan.attributes?.["tool_name"] as string) ??
      toolSpan.name.replace("tool.", "");
    alerts.push({
      type: "tool",
      title: "Tool running",
      message: `${toolName} is still executing.`,
    });
  }

  const retrievalSpan = findLatestSpan(
    spans,
    (span) =>
      span.kind === "intelligence" &&
      span.name.includes("retrieve") &&
      span.status === "waiting"
  );
  if (retrievalSpan) {
    alerts.push({
      type: "retrieval",
      title: "Retrieval pending",
      message: "Waiting for retrieval results before responding.",
    });
  }

  return alerts;
};

export const useChatRun = ({
  message,
  context,
  mode,
  tenantId,
  userId,
}: UseChatRunArgs) => {
  const [statusValue, setStatusValue] = useState<StatusValue | null>(null);
  const [steps, setSteps] = useState<StepStateMap>(() => createInitialSteps());
  const [output, setOutput] = useState("");
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);
  const [runComplete, setRunComplete] = useState(false);
  const [finalText, setFinalText] = useState("");
  const [decisions, setDecisions] = useState<DecisionEntry[]>([]);
  const [runOutcome, setRunOutcome] = useState<string | null>(null);
  const [runOutcomeReason, setRunOutcomeReason] = useState<string | null>(null);
  const [retrievedChunks, setRetrievedChunks] = useState<RetrievedChunkEntry[]>(
    []
  );
  const [retrievalAttempted, setRetrievalAttempted] = useState(false);
  const [retrievalPending, setRetrievalPending] = useState(false);
  const [availableTools, setAvailableTools] = useState<AvailableToolEntry[]>(
    []
  );
  const [toolContext, setToolContext] = useState<ToolContextState>(
    INITIAL_TOOL_CONTEXT
  );
  const [workflowStatus, setWorkflowStatus] =
    useState<WorkflowStatusValue | null>(null);
  const [workflowCurrentStep, setWorkflowCurrentStep] = useState<string | null>(
    null
  );
  const [workflowAttempts, setWorkflowAttempts] = useState<Record<string, number>>(
    {}
  );
  const [workflowRetryInfo, setWorkflowRetryInfo] = useState<WorkflowSummary["retry"]>(
    null
  );
  const [workflowWaitingInfo, setWorkflowWaitingInfo] =
    useState<WorkflowSummary["waitingForEvents"]>(null);
  const [approvalPending, setApprovalPending] = useState<{
    reason: string | null;
  } | null>(null);
  const [approvalDecision, setApprovalDecision] = useState<string | null>(null);
  const [isSubmittingApproval, setIsSubmittingApproval] = useState(false);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [spanAlerts, setSpanAlerts] = useState<SpanAlert[]>([]);
  const [guardrailSummary, setGuardrailSummary] =
    useState<GuardrailSummaryState | null>(null);
  const [guardrailEvents, setGuardrailEvents] = useState<GuardrailEventEntry[]>(
    []
  );
  const [sanitizedContext, setSanitizedContext] = useState<
    SanitizedContextEntry[]
  >([]);
  const [injectionSignals, setInjectionSignals] = useState<
    InjectionSignalEntry[]
  >([]);
  const [operationalAlerts, setOperationalAlerts] = useState<OperationalAlert[]>(
    []
  );
  const [degradedReason, setDegradedReason] = useState<string | null>(null);
  const [budgetStatus, setBudgetStatus] = useState<"ok" | "exhausted">("ok");

  const [formError, setFormError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [lastSubmission, setLastSubmission] = useState<SubmissionMeta | null>(
    null
  );

  const subscriptionRef = useRef<RunEventSubscription | null>(null);
  const lastSeqRef = useRef(0);
  const outputRef = useRef("");

  const statusDisplay: StatusDisplay = useMemo(() => {
    if (budgetStatus === "exhausted") {
      return {
        label: "Budget exhausted",
        hint: "This run stopped after hitting its model spend limit.",
      };
    }
    if (degradedReason) {
      return {
        label: "Degraded mode",
        hint: degradedReason,
      };
    }
    if (guardrailSummary?.status === "refused") {
      return {
        label: "Request refused",
        hint:
          guardrailSummary.reason ??
          "Guardrails refused this request before it started.",
      };
    }
    if (guardrailSummary?.status === "guardrail_triggered") {
      return {
        label: "Guardrail triggered",
        hint:
          guardrailSummary.reason ??
          `Execution stopped at the ${guardrailSummary.layer ?? "safety"} layer.`,
      };
    }
    if (operationalAlerts.length > 0) {
      const latest = operationalAlerts[operationalAlerts.length - 1];
      return {
        label: latest.title,
        hint: latest.message,
      };
    }
    if (spanAlerts.length > 0) {
      return {
        label: spanAlerts[0]?.title,
        hint: spanAlerts[0]?.message,
      };
    }
    if (approvalPending) {
      return {
        label: "Waiting for approval",
        hint:
          approvalPending.reason ??
          "Execution is paused until you approve or reject.",
      };
    }
    if (workflowRetryInfo) {
      return {
        label: `Retrying ${workflowRetryInfo.step}`,
        hint: `Attempt ${workflowRetryInfo.attempt} resumes in ${workflowRetryInfo.backoffSeconds}s.`,
      };
    }
    const waitingEvents = workflowWaitingInfo?.events ?? [];
    const waitingReason = workflowWaitingInfo?.reason;
    const waitingForTool = waitingEvents.some((event) =>
      event.startsWith("tool.")
    );
    const waitingForRetrieval = waitingEvents.some((event) =>
      event.startsWith("retrieval.")
    );
    if (waitingForTool) {
      const toolName =
        toolContext.requestedTool ||
        toolContext.toolSource ||
        "the requested tool";
      return {
        label: "Waiting on tool",
        hint:
          waitingReason ??
          `Workflow is blocked until ${toolName} produces a result.`,
      };
    }
    if (waitingForRetrieval || retrievalPending) {
      return {
        label: "Retrieving knowledge",
        hint:
          waitingReason ??
          "Retrieval is still running; results will stream in automatically.",
      };
    }
    if (!statusValue) {
      return {
        label: "Idle",
        hint: "Awaiting your intent. Submit a message to begin a run.",
      };
    }
  return {
    label: STATUS_LABELS[statusValue],
    hint: STATUS_HINTS[statusValue],
  };
  }, [
    guardrailSummary,
    approvalPending,
    retrievalPending,
    spanAlerts,
    statusValue,
    toolContext.requestedTool,
    toolContext.toolSource,
    workflowRetryInfo,
    workflowWaitingInfo,
    operationalAlerts,
    degradedReason,
    budgetStatus,
  ]);

  const orderedSteps = useMemo(
    () =>
      STEP_LABELS.map((label) => {
        const state = steps[label] ?? "pending";
        const displayLabel =
          label === TOOL_STEP_LABELS.executed && state === "failed"
            ? "Tool failed"
            : label;
        return { label: displayLabel, state };
      }),
    [steps]
  );

  const canSend = useMemo(
    () => message.trim().length > 0 && !isStreaming,
    [message, isStreaming]
  );

  const cleanupSubscription = useCallback(() => {
    subscriptionRef.current?.close();
    subscriptionRef.current = null;
  }, []);

  useEffect(() => {
    return () => {
      cleanupSubscription();
    };
  }, [cleanupSubscription]);

  useEffect(() => {
    outputRef.current = output;
  }, [output]);

  const resetRunView = useCallback(() => {
    setStatusValue(null);
    setSteps(createInitialSteps());
    setOutput("");
    setFinalText("");
    setDecisions([]);
    setRunOutcome(null);
    setRunOutcomeReason(null);
    setRunComplete(false);
    setRunError(null);
    setRetrievedChunks([]);
    setRetrievalAttempted(false);
    setRetrievalPending(false);
    setAvailableTools([]);
    setToolContext(INITIAL_TOOL_CONTEXT);
    setWorkflowStatus(null);
    setWorkflowCurrentStep(null);
    setWorkflowAttempts({});
    setWorkflowRetryInfo(null);
    setWorkflowWaitingInfo(null);
    setApprovalPending(null);
    setApprovalDecision(null);
    setApprovalError(null);
    setSpanAlerts([]);
    setGuardrailSummary(null);
    setGuardrailEvents([]);
    setSanitizedContext([]);
    setInjectionSignals([]);
    setOperationalAlerts([]);
    setDegradedReason(null);
    setBudgetStatus("ok");
    lastSeqRef.current = 0;
  }, []);

  useEffect(() => {
    if (!currentRunId) {
      setSpanAlerts([]);
      return;
    }
    let cancelled = false;
    const loadSpans = async () => {
      try {
        const spans = (await fetchRunSpans(currentRunId)) ?? [];
        if (cancelled) return;
        setSpanAlerts(buildSpanAlerts(spans));
      } catch {
        if (!cancelled) {
          setSpanAlerts([]);
        }
      }
    };
    loadSpans();
    if (runComplete) {
      return () => {
        cancelled = true;
      };
    }
    const interval = setInterval(loadSpans, 4000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [currentRunId, runComplete]);

  const handleRunEvent = useCallback((event: RunEvent) => {
    if (event.seq <= lastSeqRef.current) {
      return;
    }
    lastSeqRef.current = event.seq;

    switch (event.type) {
      case "guardrail.triggered": {
        const layer =
          typeof event.data?.layer === "string" ? event.data.layer : "unknown";
        const threatType =
          typeof event.data?.threat_type === "string"
            ? event.data.threat_type
            : "unknown";
        const notes =
          typeof event.data?.notes === "string" ? event.data.notes : null;
        const confidence =
          typeof event.data?.confidence === "string"
            ? event.data.confidence
            : null;
        setGuardrailEvents((prev) => [
          ...prev,
          {
            ts: event.ts,
            layer,
            threatType,
            notes,
            confidence,
          },
        ]);
        setGuardrailSummary({
          status: "guardrail_triggered",
          reason: notes,
          layer,
          threatType,
        });
        break;
      }

      case "context.sanitized": {
        const chunkId =
          typeof event.data?.original_chunk_id === "string"
            ? event.data.original_chunk_id
            : "unknown";
        const applied = Boolean(event.data?.sanitization_applied);
        const notes =
          typeof event.data?.notes === "string" ? event.data.notes : null;
        setSanitizedContext((prev) => [
          ...prev,
          {
            ts: event.ts,
            chunkId,
            applied,
            notes,
          },
        ]);
        break;
      }

      case "injection.detected": {
        const location =
          typeof event.data?.location === "string"
            ? event.data.location
            : "unknown";
        const pattern =
          typeof event.data?.pattern === "string" ? event.data.pattern : null;
        const confidence =
          typeof event.data?.confidence === "string"
            ? event.data.confidence
            : null;
        setInjectionSignals((prev) => [
          ...prev,
          { ts: event.ts, location, pattern, confidence },
        ]);
        break;
      }

      case "status.changed": {
        const value = event.data?.value;
        if (isStatusValue(value)) {
          setStatusValue(value);
        }
        break;
      }

      case "run.started": {
        setSteps(createInitialSteps());
        setRetrievalAttempted(false);
        setRetrievalPending(false);
        break;
      }

      case "workflow.started": {
        const step =
          typeof event.data?.current_step === "string"
            ? event.data.current_step
            : null;
        setWorkflowCurrentStep(step);
        setWorkflowAttempts({});
        setWorkflowRetryInfo(null);
        setWorkflowWaitingInfo(null);
        setApprovalPending(null);
        setApprovalDecision(null);
        setRetrievalPending(false);
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        }
        break;
      }

      case "workflow.step.started": {
        const step =
          typeof event.data?.step === "string" ? event.data.step : null;
        if (step) {
          setWorkflowCurrentStep(step);
          const attempt = toNumber(event.data?.attempt) ?? 1;
          setWorkflowAttempts((prev) => ({
            ...prev,
            [step]: attempt,
          }));
          if (step !== "retrieve") {
            setRetrievalPending(false);
          }
        }
        setWorkflowRetryInfo(null);
        setWorkflowWaitingInfo(null);
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        }
        break;
      }

      case "workflow.step.completed": {
        const step =
          typeof event.data?.step === "string" ? event.data.step : null;
        if (step === "retrieve") {
          setRetrievalPending(false);
        }
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        }
        setWorkflowRetryInfo(null);
        setWorkflowWaitingInfo(null);
        break;
      }

      case "workflow.retrying": {
        const step =
          typeof event.data?.step === "string" ? event.data.step : null;
        const attempt = toNumber(event.data?.attempt) ?? null;
        const backoff = toNumber(event.data?.backoff_seconds) ?? 0;
        if (step && attempt) {
          setWorkflowRetryInfo({
            step,
            attempt,
            backoffSeconds: backoff,
          });
          setWorkflowAttempts((prev) => ({
            ...prev,
            [step]: attempt,
          }));
        }
        setWorkflowWaitingInfo(null);
        if (step === "retrieve") {
          setRetrievalPending(false);
        }
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        }
        break;
      }

      case "workflow.waiting_for_event": {
        const events = Array.isArray(event.data?.event_types)
          ? (event.data.event_types as string[])
          : [];
        const reason =
          typeof event.data?.reason === "string" ? event.data.reason : null;
        setWorkflowWaitingInfo({
          events,
          reason,
        });
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        }
        break;
      }

      case "workflow.waiting_for_approval": {
        const reason =
          typeof event.data?.reason === "string" ? event.data.reason : null;
        setApprovalPending({ reason });
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        } else {
          setWorkflowStatus("waiting_for_approval");
        }
        break;
      }

      case "workflow.approval.recorded": {
        const decision =
          typeof event.data?.decision === "string"
            ? event.data.decision
            : null;
        setApprovalPending(null);
        if (decision) {
          setApprovalDecision(decision);
        }
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        } else {
          setWorkflowStatus("running");
        }
        break;
      }

      case "rate.limit.exceeded": {
        const scope =
          typeof event.data?.scope === "string"
            ? event.data.scope
            : "unknown";
        const alert: OperationalAlert = {
          type: scope === "model_budget" ? "budget" : "rate_limit",
          title:
            scope === "model_budget"
              ? "Budget exhausted"
              : "Rate limit reached",
          message:
            scope === "model_budget"
              ? "Model spend limit was reached. The run will halt."
              : "Run throughput exceeded the configured limit. Please retry shortly.",
          ts: event.ts,
        };
        setOperationalAlerts((prev) => [...prev, alert]);
        if (scope === "model_budget") {
          setBudgetStatus("exhausted");
        }
        break;
      }

      case "degraded.mode.entered": {
        const reason =
          typeof event.data?.reason === "string"
            ? event.data.reason
            : "Operating in degraded mode.";
        setDegradedReason(reason);
        setOperationalAlerts((prev) => [
          ...prev,
          {
            type: "degraded",
            title: "Degraded mode",
            message: reason,
            ts: event.ts,
          },
        ]);
        break;
      }

      case "workflow.completed":
      case "workflow.failed": {
        setRetrievalPending(false);
        if (isWorkflowStatusValue(event.data?.status)) {
          setWorkflowStatus(event.data.status);
        } else if (event.type === "workflow.completed") {
          setWorkflowStatus("completed");
        } else {
          setWorkflowStatus("failed");
        }
        setWorkflowRetryInfo(null);
        setWorkflowWaitingInfo(null);
        setApprovalPending(null);
        break;
      }

      case "node.started":
      case "node.completed": {
        const name =
          typeof event.data?.name === "string" ? event.data.name : null;
        if (!name) break;
        const label = NODE_TO_STEP_LABEL[name];
        if (!label) break;
        setSteps((prev) => ({
          ...prev,
          [label]: event.type === "node.started" ? "started" : "completed",
        }));
        break;
      }

      case "output.chunk": {
        const text = event.data?.text;
        if (typeof text === "string" && text.length > 0) {
          setOutput((prev) => {
            const updated = prev + text;
            outputRef.current = updated;
            return updated;
          });
        }
        break;
      }

      case "error.raised": {
        const message =
          typeof event.data?.message === "string"
            ? event.data.message
            : "Unexpected error while streaming.";
        setRunError(message);
        break;
      }

      case "decision.made": {
        const name =
          typeof event.data?.name === "string" ? event.data.name : null;
        const value =
          typeof event.data?.value === "string" ? event.data.value : null;
        if (!name || !value) break;
        const notes =
          typeof event.data?.notes === "string" ? event.data.notes : undefined;
        setDecisions((prev) => [
          ...prev,
          {
            name,
            value,
            notes,
            ts: event.ts,
          },
        ]);
        break;
      }

      case "retrieval.started": {
        setRetrievalAttempted(true);
        setRetrievalPending(true);
        setSteps((prev) => ({
          ...prev,
          "Retrieval started": "completed",
          "Retrieval completed": "started",
        }));
        break;
      }

      case "retrieval.completed": {
        setSteps((prev) => ({
          ...prev,
          "Retrieval completed": "completed",
        }));
        setRetrievalPending(false);
        break;
      }

      case "run.completed":
      case "run.failed": {
        const final =
          (typeof event.data?.final_text === "string" &&
            event.data.final_text.length > 0
            ? event.data.final_text
            : outputRef.current) ?? "";
        setFinalText(final);
        setRunComplete(true);
        setRetrievalPending(false);
        const reason =
          typeof event.data?.reason === "string" ? event.data.reason : null;
        if (event.type === "run.completed") {
          setRunOutcome("success");
          setRunOutcomeReason(null);
        } else {
          setRunOutcome("failed");
          setRunOutcomeReason(reason);
        }
        setIsStreaming(false);
        persistActiveRun(null);
        cleanupSubscription();
        break;
      }

      case "tool.discovered": {
        const toolName =
          typeof event.data?.tool_name === "string" ? event.data.tool_name : null;
        const source =
          typeof event.data?.source === "string" ? event.data.source : null;
        const scope =
          typeof event.data?.permission_scope === "string"
            ? event.data.permission_scope
            : null;
        if (toolName && source && scope) {
          setAvailableTools((prev) => {
            const exists = prev.some((entry) => entry.name === toolName);
            if (exists) {
              return prev;
            }
            return [
              ...prev,
              {
                name: toolName,
                source,
                permission_scope: scope,
              },
            ];
          });
        }
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.discovered]: "completed",
        }));
        break;
      }

      case "tool.requested": {
        const toolName =
          typeof event.data?.tool_name === "string" ? event.data.tool_name : null;
        const source =
          typeof event.data?.source === "string" ? event.data.source : null;
        const scope =
          typeof event.data?.permission_scope === "string"
            ? event.data.permission_scope
            : null;
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.discovered]:
            prev[TOOL_STEP_LABELS.discovered] ?? "completed",
          [TOOL_STEP_LABELS.requested]: "completed",
          [TOOL_STEP_LABELS.executed]: "started",
          [TOOL_STEP_LABELS.denied]: "pending",
        }));
        if (toolName) {
          setToolContext({
            requestedTool: toolName,
            toolSource: source ?? null,
            toolPermissionScope: scope ?? null,
            toolDeniedReason: null,
            lastToolStatus: "requested",
          });
        }
        break;
      }

      case "tool.completed": {
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.executed]: "completed",
          [TOOL_STEP_LABELS.denied]: "pending",
        }));
        setToolContext((prev) => ({
          ...prev,
          toolDeniedReason: null,
          lastToolStatus: "completed",
        }));
        break;
      }

      case "tool.failed": {
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.executed]: "failed",
          [TOOL_STEP_LABELS.denied]: "pending",
        }));
        setToolContext((prev) => ({
          ...prev,
          toolDeniedReason: null,
          lastToolStatus: "failed",
        }));
        break;
      }

      case "tool.denied": {
        const reason =
          typeof event.data?.reason === "string" ? event.data.reason : "denied";
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.executed]: "pending",
          [TOOL_STEP_LABELS.denied]: "failed",
        }));
        setToolContext((prev) => ({
          ...prev,
          toolDeniedReason: reason,
          lastToolStatus: "denied",
        }));
        break;
      }

      default:
        break;
    }
  }, [cleanupSubscription]);

  const connectToRunEvents = useCallback(
    (runId: string) => {
      cleanupSubscription();
      lastSeqRef.current = 0;
      subscriptionRef.current = subscribeToRunEvents(runId, handleRunEvent);
    },
    [cleanupSubscription, handleRunEvent]
  );

  useEffect(() => {
    const stored = readStoredRun();
    if (!stored) return;
    setCurrentRunId(stored.runId);
    if (stored.submission) {
      setLastSubmission(stored.submission);
    }
    resetRunView();
    setIsStreaming(true);
    connectToRunEvents(stored.runId);
  }, [connectToRunEvents, resetRunView]);

  useEffect(() => {
    if (!runComplete || !currentRunId) {
      return;
    }
    let cancelled = false;
    fetchRunState(currentRunId)
      .then((payload) => {
        if (cancelled || !payload) {
          return;
        }
        const chunks = Array.isArray(payload.retrieved_chunks)
          ? (payload.retrieved_chunks as RetrievedChunkEntry[])
          : [];
        const tools = Array.isArray(payload.available_tools)
          ? (payload.available_tools as AvailableToolEntry[])
          : [];
        const resolvedToolContext: ToolContextState = {
          requestedTool: payload.requested_tool ?? null,
          toolSource: payload.tool_source ?? null,
          toolPermissionScope: payload.tool_permission_scope ?? null,
          toolDeniedReason: payload.tool_denied_reason ?? null,
          lastToolStatus: payload.last_tool_status ?? null,
        };
        if (!cancelled) {
          setRetrievedChunks(chunks);
          if (tools.length > 0) {
            setAvailableTools(tools);
          }
          setToolContext((prev) => ({
            ...prev,
            ...resolvedToolContext,
          }));
          if (Array.isArray(payload.sanitized_chunk_ids)) {
            const fallbackTs =
              (typeof payload.updated_at === "string" && payload.updated_at) ||
              new Date().toISOString();
            setSanitizedContext((prev) => {
              const existingIds = new Set(prev.map((entry) => entry.chunkId));
              const additions = (payload.sanitized_chunk_ids ?? [])
                .filter((id): id is string => typeof id === "string")
                .filter((id) => !existingIds.has(id))
                .map((id) => ({
                  ts: fallbackTs,
                  chunkId: id,
                  applied: true,
                  notes: null,
                }));
              return additions.length ? [...prev, ...additions] : prev;
            });
          }
          if (typeof payload.guardrail_status === "string") {
            setGuardrailSummary({
              status: payload.guardrail_status,
              reason: payload.guardrail_reason ?? null,
              layer: payload.guardrail_layer ?? null,
              threatType: payload.guardrail_threat_type ?? null,
            });
          }
          if (payload.degraded) {
            setDegradedReason(
              typeof payload.degraded_reason === "string"
                ? payload.degraded_reason
                : "Operating in degraded mode."
            );
          }
          if (
            typeof payload.cost_limit_usd === "number" &&
            typeof payload.cost_spent_usd === "number" &&
            payload.cost_limit_usd > 0 &&
            payload.cost_spent_usd >= payload.cost_limit_usd
          ) {
            setBudgetStatus("exhausted");
          }
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRetrievedChunks([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runComplete, currentRunId]);

  useEffect(() => {
    if (!currentRunId) {
      return;
    }
    let cancelled = false;
    fetchWorkflowState(currentRunId)
      .then((state) => {
        if (cancelled || !state) {
          return;
        }
        if (isWorkflowStatusValue(state.status)) {
          setWorkflowStatus(state.status);
        }
        if (typeof state.current_step === "string") {
          setWorkflowCurrentStep(state.current_step);
        }
        if (state.attempts && typeof state.attempts === "object") {
          const normalized: Record<string, number> = {};
          Object.entries(state.attempts).forEach(([key, value]) => {
            const attempt = toNumber(value);
            if (attempt) {
              normalized[key] = attempt;
            }
          });
          setWorkflowAttempts(normalized);
        }
        if (state.waiting_for_human) {
          const reason =
            typeof state.last_error?.reason === "string"
              ? (state.last_error.reason as string)
              : null;
          setApprovalPending({ reason });
        } else {
          setApprovalPending(null);
        }
        setApprovalDecision(state.human_decision ?? null);
        if (Array.isArray(state.pending_events) && state.pending_events.length) {
          setWorkflowWaitingInfo({
            events: state.pending_events as string[],
            reason:
              typeof state.last_error?.reason === "string"
                ? (state.last_error.reason as string)
                : null,
          });
        } else {
          setWorkflowWaitingInfo(null);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWorkflowStatus((prev) => prev);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentRunId]);

  const handleSend = useCallback(async () => {
    const trimmedMessage = message.trim();
    const trimmedContext = context.trim();
    if (!trimmedMessage) {
      setFormError("Please enter a message before sending.");
      return;
    }
    const runId = generateRunId();
    console.log("run_id", runId);

    setFormError(null);
    setRunError(null);
    resetRunView();
    setCurrentRunId(runId);
    setIsStreaming(true);

    setLastSubmission({
      message: trimmedMessage,
      context: trimmedContext,
      mode,
    });

    try {
      const resolvedRunId = await startRunRequest(
        {
          message: trimmedMessage,
          mode,
          ...(trimmedContext ? { context: trimmedContext } : {}),
          identity: {
            tenant_id: tenantId.trim() || "default",
            user_id: userId.trim() || "anonymous",
          },
        },
        runId
      );
      setCurrentRunId(resolvedRunId);
      setIsStreaming(true);
      persistActiveRun({
        runId: resolvedRunId,
        submission: {
          message: trimmedMessage,
          context: trimmedContext,
          mode,
        },
      });
      connectToRunEvents(resolvedRunId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg === "rate_limited") {
        setOperationalAlerts((prev) => [
          ...prev,
          {
            type: "rate_limit",
            title: "Rate limit reached",
            message:
              "Concurrency is capped for this tenant. Retry in a few seconds or reduce outstanding runs.",
            ts: new Date().toISOString(),
          },
        ]);
        setRunError(
          "This tenant hit the concurrency limit. Please retry in a few seconds or reduce parallel runs."
        );
      } else {
        setRunError(msg);
      }
      setIsStreaming(false);
    } finally {
      // keep streaming state controlled by events
    }
  }, [connectToRunEvents, context, message, mode, resetRunView, tenantId, userId]);

  const handleApprovalDecision = useCallback(
    async (decision: ApprovalDecision) => {
      if (!currentRunId) {
        setApprovalError("No active run to approve.");
        return;
      }
      setIsSubmittingApproval(true);
      setApprovalError(null);
      try {
        await submitApprovalDecisionRequest(currentRunId, decision);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setApprovalError(msg);
      } finally {
        setIsSubmittingApproval(false);
      }
    },
    [currentRunId]
  );

  const workflowSummary: WorkflowSummary = useMemo(() => {
    const attempt =
      workflowCurrentStep && workflowAttempts[workflowCurrentStep]
        ? workflowAttempts[workflowCurrentStep]
        : null;
    return {
      status: workflowStatus,
      currentStep: workflowCurrentStep,
      currentAttempt: attempt,
      retry: workflowRetryInfo,
      waitingForEvents: workflowWaitingInfo,
    };
  }, [
    workflowStatus,
    workflowCurrentStep,
    workflowAttempts,
    workflowRetryInfo,
    workflowWaitingInfo,
  ]);

  const approvalState: ApprovalState = useMemo(
    () => ({
      waiting: Boolean(approvalPending),
      reason: approvalPending?.reason ?? null,
      decision: approvalDecision,
      isSubmitting: isSubmittingApproval,
      error: approvalError,
    }),
    [approvalPending, approvalDecision, isSubmittingApproval, approvalError]
  );

  return {
    canSend,
    currentRunId,
    decisions,
    finalText,
    formError,
    handleSend,
    isStreaming,
    lastSubmission,
    orderedSteps,
    output,
    runComplete,
    runError,
    runOutcome,
    runOutcomeReason,
    statusDisplay,
    spanAlerts,
    operationalAlerts,
    guardrailSummary,
    guardrailEvents,
    sanitizedContext,
    injectionSignals,
    retrievedChunks,
    retrievalAttempted,
    availableTools,
    toolContext,
    workflowSummary,
    approvalState,
    handleApprovalDecision,
  };
};
