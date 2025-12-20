"use client";

import { useCallback, useMemo, useState } from "react";

import { streamChatRequest } from "../lib/backend";

const generateRunId = () => {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `fallback-${Date.now()}-${Math.random().toString(16).slice(2)}`;
};

export default function HomePage() {
  const [message, setMessage] = useState("");
  const [output, setOutput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const canSend = useMemo(() => message.trim().length > 0 && !isStreaming, [
    message,
    isStreaming,
  ]);

  const handleSend = useCallback(async () => {
    if (!message.trim()) {
      setError("Please enter a message");
      return;
    }
    const runId = generateRunId();
    console.log("run_id", runId);
    setCurrentRunId(runId);
    setOutput("");
    setError(null);
    setIsStreaming(true);

    try {
      await streamChatRequest(message, runId, (chunk) => {
        setOutput((prev) => prev + chunk);
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
    } finally {
      setIsStreaming(false);
    }
  }, [message]);

  return (
    <main className="flex min-h-screen flex-col gap-6 bg-slate-950 p-6 text-slate-100 md:flex-row md:p-10">
      <section className="flex w-full flex-col gap-4 rounded-2xl bg-slate-900/80 p-6 shadow-2xl backdrop-blur md:max-w-md">
        <div>
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-400">
            Session 0
          </p>
          <h1 className="text-3xl font-bold">AI Companion</h1>
          <p className="text-sm text-slate-400">
            Type a prompt and watch the stream arrive in real time.
          </p>
        </div>
        <label className="flex flex-col gap-2 text-sm font-semibold text-slate-200">
          Message
          <textarea
            className="w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-base text-slate-50 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-600"
            rows={6}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Describe your intent..."
            disabled={isStreaming}
          />
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
          {isStreaming ? "Streaming..." : "Send"}
        </button>
        {currentRunId ? (
          <div className="text-xs font-mono text-slate-400">
            Current run_id:{" "}
            <span className="text-slate-200">{currentRunId}</span>
          </div>
        ) : null}
        {error ? (
          <div className="rounded-lg border border-rose-900/40 bg-rose-950/40 p-3 text-sm text-rose-200">
            Error: {error}
          </div>
        ) : null}
      </section>
      <section className="flex w-full flex-1 flex-col gap-4">
        <div>
          <h2 className="text-2xl font-semibold">Response</h2>
          <p className="text-sm text-slate-400">
            Streaming chunks appear below. Console logs include run_id.
          </p>
        </div>
        <div className="flex flex-1 flex-col rounded-2xl border border-slate-800/60 bg-slate-900/60 p-4 shadow-inner">
          {output ? (
            <pre className="font-mono text-sm leading-relaxed text-slate-100 whitespace-pre-wrap break-words">
              {output}
            </pre>
          ) : (
            <p className="text-slate-500">Awaiting input</p>
          )}
        </div>
      </section>
    </main>
  );
}
