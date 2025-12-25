import type { AvailableToolEntry as BackendAvailableToolEntry } from "./backend";

export type StepVisualState = "pending" | "started" | "completed" | "failed";

export interface DecisionEntry {
  name: string;
  value: string;
  notes?: string;
  ts: string;
}

export interface RetrievedChunkEntry {
  chunk_id: string;
  document_id: string;
  text: string;
  score: number;
  metadata: Record<string, unknown>;
}

export type AvailableToolEntry = BackendAvailableToolEntry;

export interface ToolContextState {
  requestedTool: string | null;
  toolSource: string | null;
  toolPermissionScope: string | null;
  toolDeniedReason: string | null;
  lastToolStatus: string | null;
}

export interface GuardrailSummaryState {
  status: string;
  reason: string | null;
  layer: string | null;
  threatType: string | null;
}

export interface GuardrailEventEntry {
  ts: string;
  layer: string;
  threatType: string;
  notes: string | null;
  confidence: string | null;
}

export interface SanitizedContextEntry {
  ts: string;
  chunkId: string;
  applied: boolean;
  notes: string | null;
}

export interface InjectionSignalEntry {
  ts: string;
  location: string;
  pattern: string | null;
  confidence: string | null;
}
