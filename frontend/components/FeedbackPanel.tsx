"use client";

import type { FeedbackScore } from "../lib/backend";
import { FEEDBACK_REASONS } from "../lib/chatUiConstants";

interface FeedbackPanelProps {
  currentRunId: string | null;
  feedbackSubmitted: boolean;
  feedbackStatus: string | null;
  isSubmittingFeedback: boolean;
  awaitingReason: boolean;
  selectedFeedback: FeedbackScore | null;
  isOtherReasonSelected: boolean;
  otherReasonText: string;
  onThumbsUp: () => void;
  onThumbsDown: () => void;
  onReasonSelect: (reason: string) => void;
  onOtherReasonChange: (value: string) => void;
  onOtherReasonSubmit: () => void;
}

export default function FeedbackPanel({
  currentRunId,
  feedbackSubmitted,
  feedbackStatus,
  isSubmittingFeedback,
  awaitingReason,
  selectedFeedback,
  isOtherReasonSelected,
  otherReasonText,
  onThumbsUp,
  onThumbsDown,
  onReasonSelect,
  onOtherReasonChange,
  onOtherReasonSubmit,
}: FeedbackPanelProps) {
  return (
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
            onClick={onThumbsUp}
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
            onClick={onThumbsDown}
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
                  onClick={() => onReasonSelect(reason)}
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
                  onChange={(event) => onOtherReasonChange(event.target.value)}
                  disabled={isSubmittingFeedback}
                />
                <div className="flex justify-end">
                  <button
                    className="rounded-full bg-rose-500/80 px-4 py-2 text-sm font-semibold text-white transition hover:bg-rose-500 disabled:opacity-50"
                    onClick={onOtherReasonSubmit}
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
  );
}
