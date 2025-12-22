import { parseNdjsonStream } from "./ndjson";

const DEFAULT_BACKEND_URL = "http://localhost:8000";

export type ChatMode = "answer" | "research" | "summarize";

export type ChatEventType = "status" | "step" | "output" | "error" | "done";

export interface ChatEvent {
  type: ChatEventType;
  run_id: string;
  ts: string;
  data: Record<string, unknown>;
}

export type StatusValue = "received" | "thinking" | "responding" | "complete";

export type FeedbackScore = "up" | "down";

export interface ChatPayload {
  message: string;
  context?: string;
  mode: ChatMode;
}

export interface FeedbackPayload {
  run_id: string;
  score: FeedbackScore;
  reason?: string;
  final_text: string;
  message: string;
  context?: string;
  mode: ChatMode;
}

function normalizeUrl(url: string): string {
  return url.replace(/\/+$/, "");
}

export function getBackendUrl(): string {
  const envUrl = process.env.NEXT_PUBLIC_BACKEND_URL;
  if (envUrl && envUrl.trim().length > 0) {
    return normalizeUrl(envUrl.trim());
  }
  return DEFAULT_BACKEND_URL;
}

export async function streamChatRequest(
  payload: ChatPayload,
  runId: string,
  onEvent: (event: ChatEvent) => void
): Promise<string> {
  const body: Record<string, unknown> = {
    message: payload.message,
    mode: payload.mode,
  };
  if (payload.context && payload.context.trim().length > 0) {
    body.context = payload.context;
  }

  const response = await fetch(`${getBackendUrl()}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X_Run_Id": runId,
    },
    body: JSON.stringify(body),
  });

  if (!response.body) {
    throw new Error("Streaming response body missing");
  }

  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }

  await parseNdjsonStream(response.body, (value) => {
    if (!value || typeof value !== "object") return;
    const event = value as ChatEvent;
    onEvent(event);
  });

  return response.headers.get("X_Run_Id") ?? runId;
}

export async function submitFeedback(payload: FeedbackPayload): Promise<void> {
  const response = await fetch(`${getBackendUrl()}/feedback`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const message = await response
      .text()
      .catch(() => "Unable to read error message");
    throw new Error(
      message || `Feedback submission failed with ${response.status}`
    );
  }
}
