# AI Companion

Session 2 extends the Session 1 vertical slice with an explicit intelligence control graph (`receive → plan → respond → verify → finalize`), typed node/decision events, and visible verification outcomes while preserving the same streaming and run_id guarantees.

## Repository layout

- `backend/` – FastAPI app (entrypoint `app/main.py`, routes in `app/api.py`, model adapters in `app/model.py`, Dockerfile, requirements).
- `frontend/` – Next.js 14 App Router UI with Tailwind styling plus Dockerfile.
- `infra/compose.yaml` – Single command to build/run both services.
- `docs/` – Project context, per-session prompts, plans, and findings (`session_1_phase1.md` summarizes current-state verification).

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- Docker + Docker Compose (for the one-command run flow)
- (Optional) OpenAI API key for live streaming. Without it the backend emits a deterministic fake response.

## Run everything with Docker Compose

```bash
docker compose -f infra/compose.yaml up --build
```

Ports:

- Frontend at http://localhost:3000
- Backend at http://localhost:8000

You can stop the stack with `CTRL+C`; containers are disposable and can be rebuilt via the same command.

## Local development workflow

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:8000 npm run dev
```

Visit http://localhost:3000 and use the session UI. When you click **Send**, the browser console logs the generated `run_id` and the backend begins streaming NDJSON events that drive the status banner, steps panel, and output area.

## Environment variables

- `OPENAI_API_KEY` (optional) – enables live streaming via the OpenAI SDK.
- `OPENAI_BASE_URL` (optional) – override the API base URL.
- `OPENAI_MODEL` (optional) – default `gpt-4o-mini`.
- `NEXT_PUBLIC_BACKEND_URL` – frontend uses this to locate the backend (automatically set inside Docker, configure manually for local dev).

## Streaming event schema and intelligence graph observability

- Content type: `application/x-ndjson`
- Envelope format:

```json
{
  "type": "status | step | output | error | done | node | decision",
  "run_id": "<uuid>",
  "ts": "<ISO-8601 timestamp>",
  "data": {}
}
```

- `status`: `{ "value": "received" | "thinking" | "responding" | "complete" }`
- `step`: `{ "label": "Receive" | "Plan" | "Respond" | "Verify" | "Finalize", "state": "started" | "completed" }`
- `output`: `{ "text": "<chunk>" }`
- `error`: `{ "message": "<human readable>" }`
- `node`: `{ "name": "<graph node>", "state": "started" | "completed" }`
- `decision`: `{ "name": "plan_type" | "verification" | "outcome" | ..., "value": "<value>", "notes": "<optional reason>" }`
- `done`: `{ "final_text": "<full output>", "outcome": "success|failed", "reason": "<optional>" }`

Implementation references: backend event helpers in `backend/app/schemas.py`, NDJSON generator in `backend/app/api.py`, frontend stream parser in `frontend/lib/ndjson.ts`.

Feedback submissions are persisted to `backend/data/feedback.jsonl`. The backend creates the directory/file at runtime, but you can inspect it locally with `tail -n 2 backend/data/feedback.jsonl`.

Use a root-level `.env` to share OpenAI settings with docker-compose, or export them before launching the backend locally.

### Inspecting the intelligence layer

- The backend orchestrates runs via `backend/app/intelligence.py`, a fixed graph composed of `receive`, `plan`, `respond`, `verify`, and `finalize` nodes. Each node emits `node` and `step` events so the UI mirrors internal progress.
- Plan decisions classify the run as `direct_answer`, `needs_clarification`, or `cannot_answer`. Verification emits `pass`/`fail` decisions, and finalize emits an `outcome` decision. All are logged with the same run_id.
- The frontend’s response panel includes a “Decisions” block showing the emitted decision events plus the final outcome/reason so you can trace why a response took a particular path.

## Regression checklist (Phase 8)

1. **Structured intent** – Use the UI to enter message + optional context + mode; empty message should trigger client validation.
2. **Visible flow** – Observe status banner cycling `Received → Thinking → Responding → Complete` before any output chunk. The status card also shows the final outcome/reason once `done` arrives.
3. **Steps panel & nodes** – Ensure the steps panel (Receive/Plan/Respond/Verify/Finalize) only updates when the backend emits matching step events, and that `node` events appear in NDJSON logs for deeper inspection.
4. **Structured streaming** – Run  
   ```bash
   curl -N -H "Content-Type: application/json" \
     -d '{"message":"ping","mode":"answer"}' \
     http://localhost:8000/chat
   ```  
   confirm NDJSON lines include `node`/`decision` events alongside status/output entries, still using `application/x-ndjson`.
5. **Feedback as data** – After completion, click 👍 or 👎. For 👎 select a reason (or choose **Other** and submit a description) and verify `backend/data/feedback.jsonl` gains a new entry with the run_id.
6. **Trace discipline** – Check browser console + backend logs for identical run_id values by running `docker compose logs backend | grep <run_id>`. Node/decision logs should include the same run id.

See `docs/session_1_phase1.md` for baseline findings and `docs/session_1_phase8.md` for the detailed validation runbook used to verify these steps.
