"use client";

import { useState } from "react";

import ChatForm from "../components/ChatForm";
import FeedbackPanel from "../components/FeedbackPanel";
import ResponsePanel from "../components/ResponsePanel";
import StatusCard from "../components/StatusCard";
import StepsPanel from "../components/StepsPanel";
import { useChatRun } from "../hooks/useChatRun";
import { useFeedback } from "../hooks/useFeedback";
import type { ChatMode } from "../lib/backend";

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [context, setContext] = useState("");
  const [mode, setMode] = useState<ChatMode>("answer");

  const chatRun = useChatRun({ message, context, mode });
  const feedback = useFeedback({
    runComplete: chatRun.runComplete,
    currentRunId: chatRun.currentRunId,
    lastSubmission: chatRun.lastSubmission,
    finalText: chatRun.finalText,
  });

  return (
    <main className="flex min-h-screen flex-col gap-6 bg-slate-950 p-6 text-slate-100 md:flex-row md:p-10">
      <ChatForm
        message={message}
        context={context}
        mode={mode}
        isStreaming={chatRun.isStreaming}
        canSend={chatRun.canSend}
        formError={chatRun.formError}
        onMessageChange={setMessage}
        onContextChange={setContext}
        onModeChange={setMode}
        onSend={chatRun.handleSend}
      />
      <section className="flex w-full flex-1 flex-col gap-4">
        <StatusCard
          statusDisplay={chatRun.statusDisplay}
          currentRunId={chatRun.currentRunId}
          runOutcome={chatRun.runOutcome}
          runOutcomeReason={chatRun.runOutcomeReason}
        />
        <div className="flex flex-col gap-4 md:flex-row">
          <ResponsePanel
            output={chatRun.output}
            finalText={chatRun.finalText}
            decisions={chatRun.decisions}
            retrievedChunks={chatRun.retrievedChunks}
            retrievalAttempted={chatRun.retrievalAttempted}
            runComplete={chatRun.runComplete}
          />
          <StepsPanel steps={chatRun.orderedSteps} />
        </div>
        {chatRun.runError ? (
          <div className="rounded-xl border border-rose-900/40 bg-rose-950/40 p-4 text-sm text-rose-100">
            {chatRun.runError}
          </div>
        ) : null}
        {chatRun.runComplete ? (
          <FeedbackPanel
            currentRunId={chatRun.currentRunId}
            feedbackSubmitted={feedback.feedbackSubmitted}
            feedbackStatus={feedback.feedbackStatus}
            isSubmittingFeedback={feedback.isSubmittingFeedback}
            awaitingReason={feedback.awaitingReason}
            selectedFeedback={feedback.selectedFeedback}
            isOtherReasonSelected={feedback.isOtherReasonSelected}
            otherReasonText={feedback.otherReasonText}
            onThumbsUp={feedback.handleThumbsUp}
            onThumbsDown={feedback.handleThumbsDown}
            onReasonSelect={feedback.handleReasonSelect}
            onOtherReasonChange={feedback.setOtherReasonText}
            onOtherReasonSubmit={feedback.handleOtherReasonSubmit}
          />
        ) : null}
      </section>
    </main>
  );
}
