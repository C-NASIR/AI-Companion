import { useCallback, useEffect, useState } from "react";

import { submitFeedback, type FeedbackScore } from "../lib/backend";
import type { SubmissionMeta } from "./useChatRun";

interface UseFeedbackArgs {
  runComplete: boolean;
  currentRunId: string | null;
  lastSubmission: SubmissionMeta | null;
  finalText: string;
}

export const useFeedback = ({
  runComplete,
  currentRunId,
  lastSubmission,
  finalText,
}: UseFeedbackArgs) => {
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackStatus, setFeedbackStatus] = useState<string | null>(null);
  const [isSubmittingFeedback, setIsSubmittingFeedback] = useState(false);
  const [awaitingReason, setAwaitingReason] = useState(false);
  const [selectedFeedback, setSelectedFeedback] =
    useState<FeedbackScore | null>(null);
  const [isOtherReasonSelected, setIsOtherReasonSelected] = useState(false);
  const [otherReasonText, setOtherReasonText] = useState("");

  const resetFeedback = useCallback(() => {
    setFeedbackSubmitted(false);
    setFeedbackStatus(null);
    setIsSubmittingFeedback(false);
    setAwaitingReason(false);
    setSelectedFeedback(null);
    setIsOtherReasonSelected(false);
    setOtherReasonText("");
  }, []);

  useEffect(() => {
    if (currentRunId) {
      resetFeedback();
    }
  }, [currentRunId, resetFeedback]);

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
    [feedbackSubmitted, handleFeedbackSubmission, isSubmittingFeedback]
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

  return {
    awaitingReason,
    feedbackStatus,
    feedbackSubmitted,
    handleOtherReasonSubmit,
    handleReasonSelect,
    handleThumbsDown,
    handleThumbsUp,
    isOtherReasonSelected,
    isSubmittingFeedback,
    otherReasonText,
    resetFeedback,
    selectedFeedback,
    setOtherReasonText,
  };
};
