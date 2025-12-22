export type StepVisualState = "pending" | "started" | "completed";

export interface DecisionEntry {
  name: string;
  value: string;
  notes?: string;
  ts: string;
}
