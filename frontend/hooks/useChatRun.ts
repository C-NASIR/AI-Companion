import { useCallback, useMemo, useRef, useState } from "react";

import {
  streamChatRequest,
  type ChatEvent,
  type ChatMode,
} from "../lib/backend";
import {
  createInitialSteps,
  generateRunId,
  isStatusValue,
  isStepLabel,
  isStepState,
  STATUS_HINTS,
  STATUS_LABELS,
  STEP_LABELS,
  type StatusValue,
  type StepStateMap,
} from "../lib/chatUiConstants";
import type { DecisionEntry } from "../lib/chatTypes";

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

  const [formError, setFormError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [lastSubmission, setLastSubmission] = useState<SubmissionMeta | null>(
    null
  );

  const ignoreOutputRef = useRef(false);

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
    () => STEP_LABELS.map((label) => ({ label, state: steps[label] })),
    [steps]
  );

  const canSend = useMemo(
    () => message.trim().length > 0 && !isStreaming,
    [message, isStreaming]
  );

  const handleChatEvent = useCallback((event: ChatEvent) => {
    switch (event.type) {
      case "status": {
        const value = event.data?.value;
        if (isStatusValue(value)) {
          setStatusValue(value);
        }
        break;
      }
      case "step": {
        const label = event.data?.label;
        const state = event.data?.state;
        if (isStepLabel(label) && isStepState(state)) {
          setSteps((prev) => ({
            ...prev,
            [label]: state,
          }));
        }
        break;
      }
      case "output": {
        if (ignoreOutputRef.current) break;
        const text = event.data?.text;
        if (typeof text === "string" && text.length > 0) {
          setOutput((prev) => prev + text);
        }
        break;
      }
      case "error": {
        const message =
          typeof event.data?.message === "string"
            ? event.data.message
            : "Unexpected error while streaming.";
        setRunError(message);
        ignoreOutputRef.current = true;
        break;
      }
      case "decision": {
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
      case "node": {
        break;
      }
      case "done": {
        const final =
          typeof event.data?.final_text === "string"
            ? event.data.final_text
            : "";
        setFinalText(final);
        setRunComplete(true);
        const outcome =
          typeof event.data?.outcome === "string"
            ? event.data.outcome
            : null;
        const reason =
          typeof event.data?.reason === "string"
            ? event.data.reason
            : null;
        setRunOutcome(outcome);
        setRunOutcomeReason(reason);
        break;
      }
      default:
        break;
    }
  }, []);

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
    setStatusValue(null);
    setSteps(createInitialSteps());
    setOutput("");
    setFinalText("");
    setDecisions([]);
    setRunOutcome(null);
    setRunOutcomeReason(null);
    setCurrentRunId(runId);
    setIsStreaming(true);
    setRunComplete(false);
    ignoreOutputRef.current = false;

    setLastSubmission({
      message: trimmedMessage,
      context: trimmedContext,
      mode,
    });

    try {
      const resolvedRunId = await streamChatRequest(
        {
          message: trimmedMessage,
          mode,
          ...(trimmedContext ? { context: trimmedContext } : {}),
        },
        runId,
        handleChatEvent
      );
      setCurrentRunId(resolvedRunId);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setRunError(msg);
    } finally {
      setIsStreaming(false);
    }
  }, [context, handleChatEvent, message, mode]);

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
  };
};
