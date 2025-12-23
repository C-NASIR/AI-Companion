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
