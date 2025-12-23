const DEFAULT_BACKEND_URL = "http://localhost:8000";

export type ChatMode = "answer" | "research" | "summarize";

export type RunEventType =
  | "run.started"
  | "run.completed"
  | "run.failed"
  | "node.started"
  | "node.completed"
  | "decision.made"
  | "output.chunk"
  | "status.changed"
  | "error.raised";

export interface RunEvent {
  id: string;
  run_id: string;
  seq: number;
  ts: string;
  type: RunEventType;
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

export async function startRunRequest(
  payload: ChatPayload,
  runId: string
): Promise<string> {
  const body: Record<string, unknown> = {
    message: payload.message,
    mode: payload.mode,
  };
  if (payload.context && payload.context.trim().length > 0) {
    body.context = payload.context;
  }

  const response = await fetch(`${getBackendUrl()}/runs`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X_Run_Id": runId,
    },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`);
  }

  const payloadJson = (await response.json().catch(() => null)) as
    | { run_id?: string }
    | null;
  return payloadJson?.run_id || runId;
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

export interface RunEventSubscription {
  close: () => void;
}

export function subscribeToRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void
): RunEventSubscription {
  const url = `${getBackendUrl()}/runs/${runId}/events`;
  const source = new EventSource(url, { withCredentials: false });

  source.onmessage = (message: MessageEvent<string>) => {
    try {
      const parsed = JSON.parse(message.data) as RunEvent;
      onEvent(parsed);
    } catch (error) {
      console.error("Failed to parse event payload", error, message.data);
    }
  };

  source.onerror = (error) => {
    console.error("EventSource error", error);
  };

  return {
    close: () => {
      source.close();
    },
  };
}
