# AI Companion

Session 5 keeps the event-driven backbone from earlier sessions and adds a Knowledge Foundation layer. Every backend startup ingests a small corpus under `backend/data/docs`, chunks and embeds it, and stores the embeddings in an in-memory retrieval store. The intelligence graph now routes through a dedicated `retrieve` node before responding, answers must cite chunk ids like `[document.md::000]`, verification enforces grounding, and the UI surfaces a Sources panel so you can inspect provenance alongside the streamed response.

## Repository layout

- `backend/` – FastAPI app (entrypoint `app/main.py`, routes in `app/api.py`, coordinator + event primitives under `app/`).
- `frontend/` – Next.js 14 App Router UI with Tailwind styling and streaming hooks/components.
- `backend/data/events` – JSONL event logs (one file per `run_id`, created automatically).
- `backend/data/state` – `RunState` snapshots persisted after every node.
- `infra/compose.yaml` – Docker Compose stack. The backend bind-mounts `backend/data` into `/app/data`, and its entrypoint wipes that directory on container start and shutdown so you see live files locally without persisting them between runs.
- `docs/` – Project overview, per-session prompts, and the Session 5 implementation plan.
- `backend/data/docs` – Authoritative markdown corpus that ingestion reads on startup. Stable filenames become `document_id` values inside chunk metadata.

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

### Knowledge ingestion & retrieval

1. Place 3–5 markdown files inside `backend/data/docs`. The filename is treated as the immutable `document_id` and the first Markdown header becomes the document title in metadata. Keep filenames stable so previously cited chunk ids remain valid.
2. On backend startup, `backend/app/ingestion.py` loads every document, chunks text into ~500 character windows (100 character overlap), generates embeddings (OpenAI embeddings when `OPENAI_API_KEY` is present, otherwise a deterministic fake vector), and stores the chunk payloads through `backend/app/retrieval.py`.
3. Ingestion emits `knowledge.ingestion.started` / `knowledge.ingestion.completed` events with counts so you can trace corpus refreshes in the log. Failures log under the synthetic run id `knowledge-ingestion`.
4. The intelligence graph runs `receive → plan → retrieve → respond → verify → finalize`. The `retrieve` node emits `retrieval.started` and `retrieval.completed` events, stores structured chunk data in `RunState.retrieved_chunks`, and drives the new steps panel entries.
5. The respond node always sees an explicit evidence list. When chunks exist, they are formatted as a numbered list with chunk ids and passed verbatim to the model along with instructions to cite ids inline. When no chunks are retrieved it instructs the model to reply “I lack sufficient evidence to answer.”
6. Verification parses the streamed output, ensures at least one chunk id is cited whenever retrieval succeeded, and rejects answers that mention unknown chunks via reasons `missing_citations` or `invalid_citation`.
7. The frontend fetches the persisted RunState after completion, parses cited chunk ids, and renders a Sources section with document titles, chunk ids, and expandable chunk previews. If retrieval never ran or returned zero chunks the panel shows “No sources were used for this answer.”

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
- `OPENAI_EMBEDDING_MODEL` (optional) – default `text-embedding-3-small`, used by the ingestion pipeline when generating embeddings.
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
- Session 4 introduces structured tool lifecycle events:
  - `tool.requested` with `{ "tool_name": "...", "arguments": { ... } }`
  - `tool.completed` with `{ "tool_name": "...", "output": { ... }, "duration_ms": <int> }`
  - `tool.failed` with `{ "tool_name": "...", "error": { ... }, "duration_ms": <int> }`
- Session 5 extends the log with knowledge events:
  - `knowledge.ingestion.started` / `knowledge.ingestion.completed` for startup pipelines.
  - `retrieval.started` records the query text/length for the run.
  - `retrieval.completed` includes `{ "number_of_chunks": <int>, "chunk_ids": ["doc.md::000", ...] }`.
- Status payloads keep the Session 2 values (`received | thinking | responding | complete`).
- Node events include `{ "name": "receive|plan|respond|verify|finalize" }`. The frontend maps those to the steps panel; no synthetic UI state exists.
- Decision events now cover `plan_type`, `response_strategy`, `retrieval_chunks`, `grounding`, `verification`, `outcome`, `tool_intent`, and `tool_result`, each with optional `notes`.
- `output.chunk` streams deterministic text chunks (either OpenAI output or the fallback response). `run.completed` or `run.failed` carries `{ "final_text": "...", "reason": "<optional>" }`.

Implementation references: event primitives live in `backend/app/events.py`, the coordinator is `backend/app/coordinator.py`, node logic is in `backend/app/intelligence.py`, and the frontend consumes the SSE feed via the hook in `frontend/hooks/useChatRun.ts` which uses `subscribeToRunEvents` from `frontend/lib/backend.ts`.

RunState snapshots (`backend/data/state/<run_id>.json`) now include `tool_requests`, `tool_results`, and `last_tool_status`, preserving the history of each tool invocation during Session 4 runs.

Session 4 also adds a dedicated tool registry at `backend/app/tools.py`. Tools declare Pydantic schemas for inputs/outputs/errors, live in a central registry, and expose deterministic execute functions. The initial tool is a `calculator` with operations `add|subtract|multiply|divide`.

## Tool execution (Session 4)

- The respond node scans for simple arithmetic intents (e.g., “what is 2 + 3” or “add 5 and 7”), records a `tool_intent` decision, and, when applicable, emits a `tool.requested` event (`calculator` with parsed arguments) instead of streaming a model response.
- `backend/app/executor.py` hosts the `ToolExecutor`, which subscribes to the shared `EventBus`, validates requests via the registry, executes the tool, and emits `tool.completed` or `tool.failed` events with structured payloads and execution duration.
- `RunCoordinator` watches those events, updates `RunState.tool_results`, emits `tool_result` decision events, and either resumes at `verify` (on success) or skips straight to `finalize` with a failure outcome.
- Verify/finalize nodes summarize tool output for the user (“The result is X.”) and surface failures without exposing raw tool payloads.
- The frontend steps panel shows “Tool requested → Tool executing → Tool completed/failed” progress driven exclusively by the new event types, and the decision log displays both `tool_intent` and `tool_result`.

All events are appended to `backend/data/events/<run_id>.jsonl`, so you can replay any run later with `cat backend/data/events/<run_id>.jsonl`. RunState snapshots at `backend/data/state/<run_id>.json` store the same information in structured form.

## Inspecting the intelligence layer

- The intelligence graph (`receive → plan → retrieve → respond → verify → finalize`) is defined explicitly in `backend/app/intelligence.py`. Each node emits its own lifecycle/status/decision/output events and persists the RunState snapshot upon completion. The retrieve node stores structured chunks on the RunState, respond streams model output with evidence instructions, and verify enforces grounding before the final outcome.
- `backend/app/coordinator.py` listens to the event bus and advances the graph only after it observes the prior node’s completion event, ensuring the graph’s execution order is visible in the timeline.
- The frontend displays the current status, per-node progress, output chunks, and decision log entirely from the streamed events. No hidden client-side state machines exist.

## Validation checklist (Session 5)

1. **Runs outlive requests** – Start a run, close the tab, and tail `backend/data/events/<run_id>.jsonl`. The coordinator should keep appending events until `run.completed` or `run.failed` shows up.
2. **UI reconstructs from events** – Trigger a run in the UI and refresh mid-flight. The page should reconnect (using the run id stored in `sessionStorage`), replay the timeline, and continue streaming live events without losing output or decisions.
3. **Every step emits events** – Subscribe via `curl -N http://localhost:8000/runs/<run_id>/events` and confirm each node yields `node.started`/`node.completed`, `status.changed`, and, where relevant, `decision.made` + `output.chunk`.
4. **Feedback ties to the timeline** – After completion, submit 👍 or 👎 (with a reason) and verify `backend/data/feedback.jsonl` records the entry with the correct `run_id`.
5. **Trace discipline** – Browser console logs, backend logs, event files, and RunState snapshots should all contain the same `run_id`. Use `docker compose logs backend | grep <run_id>` to cross-check.
6. **Tool lifecycle is observable** – Send “what is 2 + 3” (or similar). The respond node should emit `tool.requested`, the executor should append `tool.completed/tool.failed`, the steps panel should highlight the tool stages, and the decision log should show `tool_intent` followed by `tool_result`. The final message should summarize the tool outcome rather than dumping the raw tool payload.
7. **Ingestion runs once per startup** – Start the backend and confirm logs show `knowledge.ingestion.started` / `knowledge.ingestion.completed`. Inspect `backend/data/events/knowledge-ingestion.jsonl` for the same events and verify chunk counts match the files under `backend/data/docs`.
8. **Retrieval is visible** – Kick off a run and watch for `retrieval.started` / `retrieval.completed` in the SSE stream. The frontend steps panel should mark “Retrieval started” and “Retrieval completed” in order, and `RunState.retrieved_chunks` (via `/runs/<id>/state`) should list the chunk metadata stored for the run.
9. **Grounded answers** – When retrieval returns chunks, the streamed output must cite chunk ids like `[internal_docs.md::000]`. Delete citations or alter chunk ids and rerun to confirm verification fails with `missing_citations` or `invalid_citation` and the run ends in a failure outcome. When retrieval returns zero chunks, the model should say it lacks sufficient evidence.
10. **UI sources panel** – After a successful run, the Response panel should show a Sources section listing each cited chunk with document title, chunk id, and expandable preview. When no evidence was available (e.g., retrieval returned zero chunks) the panel should display “No sources were used for this answer.”

See `docs/session_3_plan.md` and `docs/session_4_plan.md` for the implementation details and follow-up notes.
