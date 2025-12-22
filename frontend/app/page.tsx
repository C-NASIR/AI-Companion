"use client";

import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import StepsPanel, { type StepVisualState } from "../components/StepsPanel";
import {
  streamChatRequest,
  submitFeedback,
  type ChatEvent,
  type ChatMode,
  type FeedbackScore,
} from "../lib/backend";

const MODES: Array<{ value: ChatMode; label: string }> = [
  { value: "answer", label: "Answer" },
  { value: "research", label: "Research" },
  { value: "summarize", label: "Summarize" },
] as const;

const STEP_LABELS = ["Receive", "Plan", "Respond", "Verify", "Finalize"] as const;

const FEEDBACK_REASONS = [
  "Incorrect",
  "Incomplete",
  "Latency",
  "Off-topic",
  "Other",
] as const;

type StepLabel = (typeof STEP_LABELS)[number];
type StepUpdateState = "started" | "completed";
type StatusValue = "received" | "thinking" | "responding" | "complete";

type StepStateMap = Record<StepLabel, StepVisualState>;

interface DecisionEntry {
  name: string;
  value: string;
  notes?: string;
  ts: string;
}

interface SubmissionMeta {
  message: string;
  context: string;
  mode: ChatMode;
}

const STATUS_LABELS: Record<StatusValue, string> = {
  received: "Received",
  thinking: "Thinking",
  responding: "Responding",
  complete: "Complete",
};

const STATUS_HINTS: Record<StatusValue, string> = {
  received: "Intent captured. Backend is logging the request.",
  thinking: "Model call is being prepared.",
  responding: "Chunks are streaming back.",
  complete: "Run finished. Review output or send feedback.",
};

const generateRunId = () => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `fallback-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

const createInitialSteps = (): StepStateMap => {
  const map: Partial<Record<StepLabel, StepVisualState>> = {};
  STEP_LABELS.forEach((label) => {
    map[label] = "pending";
  });
  return map as StepStateMap;
};

const isStepLabel = (value: unknown): value is StepLabel =>
  typeof value === "string" &&
  STEP_LABELS.includes(value as StepLabel);

const isStepState = (value: unknown): value is StepUpdateState =>
  value === "started" || value === "completed";

const isStatusValue = (value: unknown): value is StatusValue =>
  value === "received" ||
  value === "thinking" ||
  value === "responding" ||
  value === "complete";

const DECISION_LABELS: Record<string, string> = {
  plan_type: "Plan",
  verification: "Verification",
  outcome: "Outcome",
};

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [context, setContext] = useState("");
  const [mode, setMode] = useState<ChatMode>("answer");

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
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackStatus, setFeedbackStatus] = useState<string | null>(null);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [awaitingReason, setAwaitingReason] = useState(false);
  const [selectedFeedback, setSelectedFeedback] =
    useState<FeedbackScore | null>(null);
  const [isOtherReasonSelected, setIsOtherReasonSelected] = useState(false);
  const [otherReasonText, setOtherReasonText] = useState("");

  const ignoreOutputRef = useRef(false);

  const statusDisplay = useMemo(() => {
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
        // Node events are logged implicitly through the steps panel and don't
        // require direct rendering beyond acknowledging the type.
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
    setFeedbackSubmitted(false);
    setFeedbackStatus(null);
    setAwaitingReason(false);
    setSelectedFeedback(null);
    setIsOtherReasonSelected(false);
    setOtherReasonText("");
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

  const handleFeedbackSubmission = useCallback(
    async (score: FeedbackScore, reason?: string) => {
      if (!runComplete || !currentRunId || !lastSubmission) {
        setFeedbackStatus("Feedback is available once a run finishes.");
        return;
      }
      if (score === "down" && (!reason || reason.trim().length === 0)) {
        setFeedbackStatus("Select a reason to send a thumbs down.");
        return;
      }
      setFeedbackStatus(null);
      setIsSubmittingFeedback(true);
      try {
        await submitFeedback({
          run_id: currentRunId,
          score,
          reason,
          final_text: finalText,
          message: lastSubmission.message,
          context: lastSubmission.context || undefined,
          mode: lastSubmission.mode,
        });
        setFeedbackSubmitted(true);
        setAwaitingReason(false);
        setSelectedFeedback(score);
        setIsOtherReasonSelected(false);
        setOtherReasonText("");
        setFeedbackStatus("Thanks for the feedback!");
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setFeedbackStatus(msg || "Failed to submit feedback.");
      } finally {
        setIsSubmittingFeedback(false);
      }
    },
    [currentRunId, finalText, lastSubmission, runComplete]
  );

  const handleThumbsDown = useCallback(() => {
    if (feedbackSubmitted || isSubmittingFeedback) {
      return;
    }
    setAwaitingReason(true);
    setIsOtherReasonSelected(false);
    setOtherReasonText("");
    setFeedbackStatus("Select a reason to describe what went wrong.");
  }, [feedbackSubmitted, isSubmittingFeedback]);

  const handleReasonSelect = useCallback(
    (reason: string) => {
      if (isSubmittingFeedback || feedbackSubmitted) return;
      if (reason === "Other") {
        setIsOtherReasonSelected(true);
        setFeedbackStatus("Describe what went wrong.");
        return;
      }
      void handleFeedbackSubmission("down", reason);
    },
    [
      feedbackSubmitted,
      handleFeedbackSubmission,
      isSubmittingFeedback,
    ]
  );

  const handleOtherReasonSubmit = useCallback(() => {
    if (!isOtherReasonSelected || isSubmittingFeedback || feedbackSubmitted) {
      return;
    }
    const trimmed = otherReasonText.trim();
    if (trimmed.length === 0) {
      setFeedbackStatus("Please describe the issue before submitting.");
      return;
    }
    void handleFeedbackSubmission("down", trimmed);
  }, [
    feedbackSubmitted,
    handleFeedbackSubmission,
    isOtherReasonSelected,
    isSubmittingFeedback,
    otherReasonText,
  ]);

  const handleThumbsUp = useCallback(() => {
    if (isSubmittingFeedback || feedbackSubmitted) return;
    void handleFeedbackSubmission("up");
  }, [feedbackSubmitted, handleFeedbackSubmission, isSubmittingFeedback]);

  const handleModeChange = (event: ChangeEvent<HTMLSelectElement>) => {
    setMode(event.target.value as ChatMode);
  };

  return (
    <main className="flex min-h-screen flex-col gap-6 bg-slate-950 p-6 text-slate-100 md:flex-row md:p-10">
      <section className="flex w-full flex-col gap-4 rounded-2xl bg-slate-900/80 p-6 shadow-2xl backdrop-blur md:max-w-md">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-400">
            Session 1
          </p>
          <h1 className="text-3xl font-bold">AI Companion</h1>
          <p className="text-sm text-slate-400">
            Capture intent with optional context, pick a mode, and observe the
            flow in real time.
          </p>
        </div>
        <label className="flex flex-col gap-2 text-sm font-semibold text-slate-200">
          Message
          <textarea
            className="w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-base text-slate-50 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-600"
            rows={5}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Describe your intent..."
            disabled={isStreaming}
          />
        </label>
        <label className="flex flex-col gap-2 text-sm font-semibold text-slate-200">
          Context (optional)
          <textarea
            className="w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-base text-slate-50 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-600"
            rows={3}
            value={context}
            onChange={(event) => setContext(event.target.value)}
            placeholder="Add supporting facts or constraints..."
            disabled={isStreaming}
          />
        </label>
        <label className="flex flex-col gap-2 text-sm font-semibold text-slate-200">
          Mode
          <select
            className="w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-base text-slate-50 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-600"
            value={mode}
            onChange={handleModeChange}
            disabled={isStreaming}
          >
            {MODES.map((entry) => (
              <option key={entry.value} value={entry.value}>
                {entry.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className={`rounded-full px-6 py-3 text-base font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-200 disabled:cursor-not-allowed disabled:bg-slate-700 ${
            canSend
              ? "bg-gradient-to-r from-indigo-500 to-fuchsia-500 text-white shadow-lg hover:from-indigo-400 hover:to-fuchsia-400"
              : "bg-slate-800 text-slate-400"
          }`}
          disabled={!canSend}
          onClick={handleSend}
        >
          {isStreaming ? "Running..." : "Send"}
        </button>
        {formError ? (
          <div className="rounded-lg border border-rose-900/40 bg-rose-950/40 p-3 text-sm text-rose-200">
            {formError}
          </div>
        ) : null}
      </section>
      <section className="flex w-full flex-1 flex-col gap-4">
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
                run_id:{" "}
                <span className="text-slate-200">{currentRunId}</span>
              </p>
            ) : (
              <p className="text-xs text-slate-500">
                Run id will appear once you start streaming.
              </p>
            )}
            {runOutcome ? (
              <p className="text-xs text-slate-400">
                Outcome:{" "}
                <span className="font-semibold text-slate-200">
                  {runOutcome}
                </span>
                {runOutcomeReason ? ` — ${runOutcomeReason}` : ""}
              </p>
            ) : null}
          </div>
        </div>
        <div className="flex flex-col gap-4 md:flex-row">
          <div className="flex flex-1 flex-col rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-inner">
            <h2 className="text-xl font-semibold text-slate-100">Response</h2>
            <p className="text-xs text-slate-500">
              Output updates whenever an output event arrives.
            </p>
            <div className="mt-3 flex-1 rounded-xl border border-slate-800/60 bg-slate-950/50 p-3">
              {output ? (
                <pre className="h-full overflow-y-auto whitespace-pre-wrap break-words font-mono text-sm leading-relaxed text-slate-100">
                  {output}
                </pre>
              ) : (
                <p className="text-slate-500">
                  No output yet. Status updates will arrive before text does.
                </p>
              )}
            </div>
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
                          <p className="text-xs text-slate-500">
                            {entry.notes}
                          </p>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
          <StepsPanel steps={orderedSteps} />
        </div>
        {runError ? (
          <div className="rounded-xl border border-rose-900/40 bg-rose-950/40 p-4 text-sm text-rose-100">
            {runError}
          </div>
        ) : null}
        {runComplete ? (
          <div className="rounded-2xl border border-slate-800/70 bg-slate-900/60 p-4 shadow-lg">
            <div className="flex flex-col gap-3">
              <div>
                <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-400">
                  Feedback
                </p>
                <h3 className="text-xl font-bold text-slate-100">
                  Was this helpful?
                </h3>
                <p className="text-sm text-slate-400">
                  Feedback is stored with the same run_id so we can trace issues.
                </p>
              </div>
              <div className="flex flex-wrap gap-3">
                <button
                  className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition ${
                    selectedFeedback === "up"
                      ? "border-emerald-400 bg-emerald-500/10 text-emerald-200"
                      : "border-slate-700 text-slate-200 hover:border-emerald-400"
                  }`}
                  disabled={feedbackSubmitted || isSubmittingFeedback}
                  onClick={handleThumbsUp}
                >
                  👍 Good
                </button>
                <button
                  className={`flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition ${
                    selectedFeedback === "down"
                      ? "border-rose-400 bg-rose-500/10 text-rose-200"
                      : "border-slate-700 text-slate-200 hover:border-rose-400"
                  }`}
                  disabled={feedbackSubmitted || isSubmittingFeedback}
                  onClick={handleThumbsDown}
                >
                  👎 Needs work
                </button>
              </div>
              {awaitingReason && !feedbackSubmitted ? (
                <div>
                  <p className="text-sm text-slate-400">Select a reason:</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {FEEDBACK_REASONS.map((reason) => (
                      <button
                        key={reason}
                        className="rounded-full border border-slate-700 px-3 py-1 text-xs font-semibold text-slate-200 transition hover:border-slate-500"
                        disabled={isSubmittingFeedback}
                        onClick={() => handleReasonSelect(reason)}
                      >
                        {reason}
                      </button>
                    ))}
                  </div>
                  {isOtherReasonSelected ? (
                    <div className="mt-3 flex flex-col gap-2 rounded-xl border border-slate-800/60 bg-slate-950/50 p-3">
                      <textarea
                        className="rounded-lg border border-slate-700 bg-slate-950/70 p-2 text-sm text-slate-100 outline-none transition focus:border-slate-500"
                        rows={3}
                        placeholder="Describe the issue..."
                        value={otherReasonText}
                        onChange={(event) =>
                          setOtherReasonText(event.target.value)
                        }
                        disabled={isSubmittingFeedback}
                      />
                      <div className="flex justify-end">
                        <button
                          className="rounded-full bg-rose-500/80 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50"
                          onClick={handleOtherReasonSubmit}
                          disabled={isSubmittingFeedback}
                        >
                          Submit reason
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
              {feedbackStatus ? (
                <p className="text-sm text-slate-400">{feedbackStatus}</p>
              ) : null}
              {feedbackSubmitted ? (
                <p className="text-sm text-emerald-300">
                  Feedback recorded for run {currentRunId}.
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
      </section>
    </main>
  );
}
