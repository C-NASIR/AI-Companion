const DEFAULT_BACKEND_URL = "http://localhost:8000";
const CONTAINER_HOSTNAMES = new Set(["backend", "frontend"]);

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
  | "error.raised"
  | "tool.discovered"
  | "tool.requested"
  | "tool.completed"
  | "tool.failed"
  | "tool.denied"
  | "tool.server.error"
  | "retrieval.started"
  | "retrieval.completed"
  | "workflow.started"
  | "workflow.step.started"
  | "workflow.step.completed"
  | "workflow.retrying"
  | "workflow.waiting_for_event"
  | "workflow.waiting_for_approval"
  | "workflow.approval.recorded"
  | "workflow.completed"
  | "workflow.failed";

export interface RunEvent {
  id: string;
  run_id: string;
  seq: number;
  ts: string;
  type: RunEventType;
  data: Record<string, unknown>;
}

export type StatusValue = "received" | "thinking" | "responding" | "complete";
export type WorkflowStatusValue =
  | "running"
  | "waiting_for_approval"
  | "retrying"
  | "completed"
  | "failed";

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
    const normalized = normalizeUrl(envUrl.trim());
    return resolveContainerHost(normalized);
  }
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return DEFAULT_BACKEND_URL;
}

function resolveContainerHost(url: string): string {
  if (typeof window === "undefined") {
    return url;
  }
  try {
    const parsed = new URL(url);
    if (CONTAINER_HOSTNAMES.has(parsed.hostname)) {
      parsed.hostname = window.location.hostname;
      if (!parsed.port) {
        parsed.port = "8000";
      }
      return normalizeUrl(parsed.toString());
    }
    return url;
  } catch {
    return url;
  }
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

export interface RetrievedChunkState {
  chunk_id: string;
  document_id: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface RunStatePayload {
  run_id: string;
  output_text: string;
  retrieved_chunks?: RetrievedChunkState[];
  available_tools?: AvailableToolEntry[];
  requested_tool?: string | null;
  tool_source?: string | null;
  tool_permission_scope?: string | null;
  tool_denied_reason?: string | null;
  last_tool_status?: string | null;
}

export interface WorkflowStatePayload {
  run_id: string;
  current_step: string | null;
  status: WorkflowStatusValue;
  attempts?: Record<string, number>;
  waiting_for_human: boolean;
  human_decision?: string | null;
  last_error?: Record<string, unknown> | null;
  pending_events?: string[];
}

export type ApprovalDecision = "approved" | "rejected";

export interface AvailableToolEntry {
  name: string;
  source: string;
  permission_scope: string;
  server_id?: string | null;
}

export async function fetchRunState(
  runId: string
): Promise<RunStatePayload | null> {
  const response = await fetch(`${getBackendUrl()}/runs/${runId}/state`, {
    method: "GET",
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Failed to load run state: ${response.status}`);
  }
  const payload = (await response.json()) as RunStatePayload;
  return payload;
}

export async function fetchWorkflowState(
  runId: string
): Promise<WorkflowStatePayload | null> {
  const response = await fetch(`${getBackendUrl()}/runs/${runId}/workflow`, {
    method: "GET",
  });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Failed to load workflow state: ${response.status}`);
  }
  const payload = (await response.json()) as WorkflowStatePayload;
  return payload;
}

export async function submitApprovalDecisionRequest(
  runId: string,
  decision: ApprovalDecision
): Promise<void> {
  const response = await fetch(`${getBackendUrl()}/runs/${runId}/approval`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ decision }),
  });
  if (!response.ok) {
    const message = await response.text().catch(() => null);
    throw new Error(
      message || `Failed to submit approval decision: ${response.status}`
    );
  }
}
