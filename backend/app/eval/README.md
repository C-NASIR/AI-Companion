# Evaluation module scaffold

This directory holds all Session 9 evaluation code:

- `dataset.yaml` – curated replay cases with expectations.
- `runner.py` – deterministic workflow replay runner.
- `trajectory.py` – adapters turning traces/events into scorer-ready features.
- `scorers.py` – independent scorers (outcome, retrieval, tool usage, grounding, verification).
- `report.py` – human + machine-readable summaries.
- `gate.py` – regression gate enforcing pass criteria.
- `cli.py` – `python -m app.eval.run` entrypoint that orchestrates dataset → runner → scorers → report → gate.

Phase 0 reserves this layout so later phases can drop each module in place without re-explaining structure.
