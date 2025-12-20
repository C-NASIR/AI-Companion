"use client";

import { useCallback, useMemo, useState } from "react";
import type { CSSProperties } from "react";

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
    <main style={styles.page}>
      <section style={styles.panel}>
        <h1>AI Companion</h1>
        <p>Session 0 streaming vertical slice.</p>
        <label style={styles.label}>
          Message
          <textarea
            style={styles.textarea}
            rows={6}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Describe your intent..."
          />
        </label>
        <button
          style={canSend ? styles.button : styles.buttonDisabled}
          disabled={!canSend}
          onClick={handleSend}
        >
          {isStreaming ? "Streaming..." : "Send"}
        </button>
        {currentRunId ? (
          <div style={styles.meta}>Current run_id: {currentRunId}</div>
        ) : null}
        {error ? (
          <div style={styles.error}>Error: {error}</div>
        ) : null}
      </section>
      <section style={styles.outputPanel}>
        <h2>Response</h2>
        <div style={styles.outputBox}>
          {output ? <pre style={styles.pre}>{output}</pre> : "Awaiting input"}
        </div>
      </section>
    </main>
  );
}

const styles: Record<string, CSSProperties> = {
  page: {
    display: "flex",
    flexDirection: "row",
    gap: "2rem",
    padding: "2rem",
    minHeight: "100vh",
    background: "#05060a",
    color: "#f6f6f8",
  },
  panel: {
    flex: "0 0 40%",
    display: "flex",
    flexDirection: "column",
    gap: "1rem",
    padding: "1.5rem",
    borderRadius: "1rem",
    background: "#111422",
    boxShadow: "0 15px 40px rgba(0,0,0,0.35)",
  },
  label: {
    display: "flex",
    flexDirection: "column",
    gap: "0.5rem",
    fontWeight: 600,
  },
  textarea: {
    width: "100%",
    resize: "vertical",
    padding: "0.75rem",
    fontSize: "1rem",
    borderRadius: "0.5rem",
    border: "1px solid #2a2f4a",
    background: "#05060a",
    color: "inherit",
  },
  button: {
    padding: "0.75rem 1.5rem",
    fontSize: "1rem",
    borderRadius: "9999px",
    border: "none",
    cursor: "pointer",
    background: "linear-gradient(120deg, #5c6cff, #a855f7)",
    color: "white",
    fontWeight: 600,
  },
  buttonDisabled: {
    padding: "0.75rem 1.5rem",
    fontSize: "1rem",
    borderRadius: "9999px",
    border: "none",
    background: "#333748",
    color: "#888c9f",
    cursor: "not-allowed",
    fontWeight: 600,
  },
  meta: {
    fontSize: "0.9rem",
    color: "#c7c8f5",
    wordBreak: "break-all",
  },
  error: {
    padding: "0.75rem",
    background: "#2a0d15",
    color: "#ff7b93",
    borderRadius: "0.5rem",
  },
  outputPanel: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    gap: "1rem",
  },
  outputBox: {
    flex: 1,
    borderRadius: "1rem",
    background: "#0d0f1a",
    padding: "1rem",
    overflowY: "auto",
  },
  pre: {
    margin: 0,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  },
};
