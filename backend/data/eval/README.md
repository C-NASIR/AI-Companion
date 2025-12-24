# Evaluation artifacts

Evaluation runs persist machine-readable outputs here:

- `report.json` – aggregate summary consumed by CI and local tooling.
- `cases/<case_id>/` – optional per-case exports (state snapshots, copied event/trace handles, scorer breakdowns).
- Additional scratch files (e.g., `latest_run_id`) may be written by the CLI but should be treated as ephemeral artifacts.

Phase 0 creates this directory so later phases can write artifacts without touching other `backend/data` consumers.
