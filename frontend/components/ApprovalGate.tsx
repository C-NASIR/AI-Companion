"use client";

interface ApprovalGateProps {
  waiting: boolean;
  reason: string | null;
  decision: string | null;
  isSubmitting: boolean;
  error: string | null;
  onApprove: () => void;
  onReject: () => void;
}

export default function ApprovalGate({
  waiting,
  reason,
  decision,
  isSubmitting,
  error,
  onApprove,
  onReject,
}: ApprovalGateProps) {
  if (!waiting && !decision) {
    return null;
  }

  const headline = waiting
    ? "Human approval required"
    : "Approval decision recorded";
  const description = waiting
    ? "The workflow paused while waiting for a human decision. Approve to continue or reject to stop."
    : `Decision: ${decision}`;

  return (
    <div className="rounded-2xl border border-amber-500/30 bg-amber-950/30 p-4 text-sm text-amber-100 shadow-inner">
      <p className="text-xs font-semibold uppercase tracking-[0.3em] text-amber-300">
        Approval Gate
      </p>
      <h3 className="mt-1 text-xl font-bold text-amber-100">{headline}</h3>
      <p className="mt-1 text-amber-200">{description}</p>
      {reason ? (
        <p className="mt-1 text-xs text-amber-200/80">
          Reason: <span className="font-semibold">{reason}</span>
        </p>
      ) : null}
      {waiting ? (
        <div className="mt-4 flex flex-wrap gap-3">
          <button
            type="button"
            disabled={isSubmitting}
            className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-emerald-950 shadow disabled:cursor-not-allowed disabled:bg-emerald-800 disabled:text-emerald-200"
            onClick={onApprove}
          >
            {isSubmitting ? "Submitting…" : "Approve"}
          </button>
          <button
            type="button"
            disabled={isSubmitting}
            className="rounded-xl bg-rose-600 px-4 py-2 text-sm font-semibold text-rose-50 shadow disabled:cursor-not-allowed disabled:bg-rose-900/70 disabled:text-rose-200/80"
            onClick={onReject}
          >
            {isSubmitting ? "Submitting…" : "Reject"}
          </button>
        </div>
      ) : null}
      {error ? (
        <p className="mt-3 text-xs text-rose-200">
          Approval failed: {error}. Try again.
        </p>
      ) : null}
    </div>
  );
}
