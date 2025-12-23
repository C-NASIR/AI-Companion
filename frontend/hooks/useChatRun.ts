import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchRunState,
  startRunRequest,
  subscribeToRunEvents,
  type ChatMode,
  type RunEvent,
  type RunEventSubscription,
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
  DecisionEntry,
  RetrievedChunkEntry,
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

interface UseChatRunArgs {
  message: string;
  context: string;
  mode: ChatMode;
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

export const useChatRun = ({ message, context, mode }: UseChatRunArgs) => {
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

  const [formError, setFormError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [lastSubmission, setLastSubmission] = useState<SubmissionMeta | null>(
    null
  );

  const subscriptionRef = useRef<RunEventSubscription | null>(null);
  const lastSeqRef = useRef(0);
  const outputRef = useRef("");

  const statusDisplay: StatusDisplay = useMemo(() => {
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
  }, [statusValue]);

  const orderedSteps = useMemo(
    () =>
      STEP_LABELS.map((label) => {
        const state = steps[label] ?? "pending";
        const displayLabel =
          label === TOOL_STEP_LABELS.completed && state === "failed"
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
    lastSeqRef.current = 0;
  }, []);

  const handleRunEvent = useCallback((event: RunEvent) => {
    if (event.seq <= lastSeqRef.current) {
      return;
    }
    lastSeqRef.current = event.seq;

    switch (event.type) {
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

      case "tool.requested": {
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.requested]: "completed",
          [TOOL_STEP_LABELS.executing]: "started",
          [TOOL_STEP_LABELS.completed]: "pending",
        }));
        break;
      }

      case "tool.completed": {
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.executing]: "completed",
          [TOOL_STEP_LABELS.completed]: "completed",
        }));
        break;
      }

      case "tool.failed": {
        setSteps((prev) => ({
          ...prev,
          [TOOL_STEP_LABELS.executing]: "completed",
          [TOOL_STEP_LABELS.completed]: "failed",
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
        if (!cancelled) {
          setRetrievedChunks(chunks);
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
      setRunError(msg);
      setIsStreaming(false);
    } finally {
      // keep streaming state controlled by events
    }
  }, [connectToRunEvents, context, message, mode, resetRunView]);

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
    retrievedChunks,
    retrievalAttempted,
  };
};
