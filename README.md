# AI Companion

Session 3 evolves the Session 2 vertical slice into an event-driven system. Runs now emit durable timeline events (`run.started → … → run.completed/failed`) that outlive the HTTP request, the backend coordinator executes the fixed intelligence graph asynchronously, and the frontend consumes Server-Sent Events (SSE) so a run can be replayed even after refreshing the page.

## Repository layout

- `backend/` – FastAPI app (entrypoint `app/main.py`, routes in `app/api.py`, coordinator + event primitives under `app/`).
- `frontend/` – Next.js 14 App Router UI with Tailwind styling and streaming hooks/components.
- `backend/data/events` – JSONL event logs (one file per `run_id`, created automatically).
- `backend/data/state` – `RunState` snapshots persisted after every node.
- `infra/compose.yaml` – Docker Compose stack. The backend bind-mounts `backend/data` into `/app/data`, and its entrypoint wipes that directory on container start and shutdown so you see live files locally without persisting them between runs.
- `docs/` – Project overview, per-session prompts, and the Session 3 plan describing the current implementation phases.

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

Stop with `CTRL+C`. The backend entrypoint empties `/app/data` on startup and again on shutdown, so each `docker compose up` session begins with fresh directories while still exposing live files at `backend/data/events` and `backend/data/state` on your host. If you want to persist runs, remove the cleanup logic or change the compose mount per the inline comments.

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

Visit http://localhost:3000 and use the UI. When you click **Send**, the browser logs the generated `run_id`, POSTs to `/runs`, immediately opens an SSE connection to `/runs/{run_id}/events`, and keeps all UI state in sync with replayed timeline events. Refreshing mid-run reconnects using the stored `run_id` and replays history before live updates resume.

### Manual curl flow

1. Generate an id and start a run:

   ```bash
   RUN_ID=$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)
   curl -s -X POST \
     -H "Content-Type: application/json" \
     -H "X_Run_Id: $RUN_ID" \
     -d '{"message":"walk me through the control graph","mode":"answer"}' \
     http://localhost:8000/runs
   echo "Run started: $RUN_ID"
   ```

2. Subscribe to its timeline (replay + live):

   ```bash
   curl -N http://localhost:8000/runs/$RUN_ID/events
   ```

3. Inspect the latest `RunState` snapshot:

   ```bash
   curl http://localhost:8000/runs/$RUN_ID/state | jq
   ```

Feedback stays the same: POST to `/feedback` with the `run_id`, score, reason (for 👎), and final text after the run completes.

## Environment variables

- `OPENAI_API_KEY` (optional) – enables live streaming via the OpenAI SDK.
- `OPENAI_BASE_URL` (optional) – override the API base URL.
- `OPENAI_MODEL` (optional) – default `gpt-4o-mini`.
- `NEXT_PUBLIC_BACKEND_URL` – frontend uses this to locate the backend (automatically set inside Docker, configure manually for local dev).

## Event schema, timeline, and intelligence graph observability

- Content type: `text/event-stream`
- Every SSE message contains compact JSON (one per line) with this envelope:

```json
{
  "id": "94cf3eed-3521-4e44-b3ec-68d3a62d4064",
  "run_id": "6a8f0582-2c80-4ff4-b7ee-821f12d6de0b",
  "seq": 12,
  "ts": "2024-06-07T18:41:20.109486+00:00",
  "type": "node.started",
  "data": { "name": "respond" }
}
```

- Event types emitted in Session 3:
  - `run.started`, `run.completed`, `run.failed`
  - `node.started`, `node.completed`
  - `decision.made`
  - `status.changed`
  - `output.chunk`
  - `error.raised`
- Status payloads keep the Session 2 values (`received | thinking | responding | complete`).
- Node events include `{ "name": "receive|plan|respond|verify|finalize" }`. The frontend maps those to the steps panel; no synthetic UI state exists.
- Decision events cover `plan_type`, `response_strategy`, `verification`, and `outcome`, each with optional `notes`.
- `output.chunk` streams deterministic text chunks (either OpenAI output or the fallback response). `run.completed` or `run.failed` carries `{ "final_text": "...", "reason": "<optional>" }`.

Implementation references: event primitives live in `backend/app/events.py`, the coordinator is `backend/app/coordinator.py`, node logic is in `backend/app/intelligence.py`, and the frontend consumes the SSE feed via the hook in `frontend/hooks/useChatRun.ts` which uses `subscribeToRunEvents` from `frontend/lib/backend.ts`.

All events are appended to `backend/data/events/<run_id>.jsonl`, so you can replay any run later with `cat backend/data/events/<run_id>.jsonl`. RunState snapshots at `backend/data/state/<run_id>.json` store the same information in structured form.

## Inspecting the intelligence layer

- The intelligence graph (`receive → plan → respond → verify → finalize`) is defined explicitly in `backend/app/intelligence.py`. Each node emits its own lifecycle/status/decision/output events and persists the RunState snapshot upon completion.
- `backend/app/coordinator.py` listens to the event bus and advances the graph only after it observes the prior node’s completion event, ensuring the graph’s execution order is visible in the timeline.
- The frontend displays the current status, per-node progress, output chunks, and decision log entirely from the streamed events. No hidden client-side state machines exist.

## Validation checklist (Session 3)

1. **Runs outlive requests** – Start a run, close the tab, and tail `backend/data/events/<run_id>.jsonl`. The coordinator should keep appending events until `run.completed` or `run.failed` shows up.
2. **UI reconstructs from events** – Trigger a run in the UI and refresh mid-flight. The page should reconnect (using the run id stored in `sessionStorage`), replay the timeline, and continue streaming live events without losing output or decisions.
3. **Every step emits events** – Subscribe via `curl -N http://localhost:8000/runs/<run_id>/events` and confirm each node yields `node.started`/`node.completed`, `status.changed`, and, where relevant, `decision.made` + `output.chunk`.
4. **Feedback ties to the timeline** – After completion, submit 👍 or 👎 (with a reason) and verify `backend/data/feedback.jsonl` records the entry with the correct `run_id`.
5. **Trace discipline** – Browser console logs, backend logs, event files, and RunState snapshots should all contain the same `run_id`. Use `docker compose logs backend | grep <run_id>` to cross-check.

See `docs/session_3_plan.md` for the implementation plan backing these changes and future work notes.
