"use client";

import type { ChatMode } from "../lib/backend";
import { MODES } from "../lib/chatUiConstants";

interface ChatFormProps {
  message: string;
  context: string;
  mode: ChatMode;
  isStreaming: boolean;
  canSend: boolean;
  formError: string | null;
  onMessageChange: (value: string) => void;
  onContextChange: (value: string) => void;
  onModeChange: (mode: ChatMode) => void;
  onSend: () => void;
}

export default function ChatForm({
  message,
  context,
  mode,
  isStreaming,
  canSend,
  formError,
  onMessageChange,
  onContextChange,
  onModeChange,
  onSend,
}: ChatFormProps) {
  return (
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
          onChange={(event) => onMessageChange(event.target.value)}
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
          onChange={(event) => onContextChange(event.target.value)}
          placeholder="Add supporting facts or constraints..."
          disabled={isStreaming}
        />
      </label>
      <label className="flex flex-col gap-2 text-sm font-semibold text-slate-200">
        Mode
        <select
          className="w-full rounded-xl border border-slate-700 bg-slate-950/70 p-3 text-base text-slate-50 outline-none transition focus:border-slate-500 focus:ring-2 focus:ring-slate-600"
          value={mode}
          onChange={(event) => onModeChange(event.target.value as ChatMode)}
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
        onClick={onSend}
      >
        {isStreaming ? "Running..." : "Send"}
      </button>
      {formError ? (
        <div className="rounded-lg border border-rose-900/40 bg-rose-950/40 p-3 text-sm text-rose-200">
          {formError}
        </div>
      ) : null}
    </section>
  );
}
