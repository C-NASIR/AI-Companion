export type StepVisualState = "pending" | "started" | "completed" | "failed";

export interface DecisionEntry {
  name: string;
  value: string;
  notes?: string;
  ts: string;
}
