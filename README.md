# AI Companion

Session 1 builds on the Session 0 vertical slice by making the intelligence flow observable: structured intent capture (message/context/mode), NDJSON event streaming (`status`, `step`, `output`, `error`, `done`), a visible steps panel, and run-tied feedback logging. Run IDs still span browser logs, backend `/chat`, and `/feedback` endpoints.

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

## Streaming event schema

- Content type: `application/x-ndjson`
- Envelope format:

```json
{
  "type": "status | step | output | error | done",
  "run_id": "<uuid>",
  "ts": "<ISO-8601 timestamp>",
  "data": {}
}
```

- `status`: `{ "value": "received" | "thinking" | "responding" | "complete" }`
- `step`: `{ "label": "Request received" | "Model call started" | "Model streaming response" | "Response complete", "state": "started" | "completed" }`
- `output`: `{ "text": "<chunk>" }`
- `error`: `{ "message": "<human readable>" }`
- `done`: `{ "final_text": "<full output>" }`

Implementation references: backend event helpers in `backend/app/schemas.py`, NDJSON generator in `backend/app/api.py`, frontend stream parser in `frontend/lib/ndjson.ts`.

Feedback submissions are persisted to `backend/data/feedback.jsonl`. The backend creates the directory/file at runtime, but you can inspect it locally with `tail -n 2 backend/data/feedback.jsonl`.

Use a root-level `.env` to share OpenAI settings with docker-compose, or export them before launching the backend locally.

## Regression checklist (Phase 8)

1. **Structured intent** – Use the UI to enter message + optional context + mode; empty message should trigger client validation.
2. **Visible flow** – Observe status banner cycling `Received → Thinking → Responding → Complete` before any output chunk.
3. **Steps panel** – Ensure steps flip from Pending → In progress → Done strictly in response to backend events.
4. **Structured streaming** – Run  
   ```bash
   curl -N -H "Content-Type: application/json" \
     -d '{"message":"ping","mode":"answer"}' \
     http://localhost:8000/chat
   ```  
   and confirm NDJSON lines follow `{type,run_id,ts,data}` schema with `application/x-ndjson` content type.
5. **Feedback as data** – After completion, click 👍 or 👎. For 👎 select a reason (or choose **Other** and submit a description) and verify `backend/data/feedback.jsonl` gains a new entry with the run_id.
6. **Trace discipline** – Check browser console + backend logs for identical run_id values by running `docker compose logs backend | grep <run_id>`.

See `docs/session_1_phase1.md` for baseline findings and `docs/session_1_phase8.md` for the detailed validation runbook used to verify these steps.
