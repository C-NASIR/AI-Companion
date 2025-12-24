# AI Companion

Session 6 keeps the event-driven backbone and knowledge foundation from earlier sessions but now routes every tool interaction through a Model Context Protocol (MCP) boundary with dynamic discovery, centralized permissions, and explicit provenance. Every backend startup still ingests the markdown corpus under `backend/data/docs`, yet intelligence now consults the MCP registry during planning, emits `tool.discovered` / `tool.requested` / `tool.denied` events, and the UI surfaces standardized tool metadata (name, scope, and source) together with denial messaging so users understand why a capability was or was not used.

## Repository layout

- `backend/` – FastAPI app (entrypoint `app/main.py`, routes in `app/api.py`, coordinator + event primitives under `app/`).
- `frontend/` – Next.js 14 App Router UI with Tailwind styling and streaming hooks/components.
- `backend/data/events` – JSONL event logs (one file per `run_id`, created automatically).
- `backend/data/state` – `RunState` snapshots persisted after every node.
- `backend/data/traces` – Session 8 trace files (`{run_id}.json`) with the trace envelope plus every span.
- `infra/compose.yaml` – Docker Compose stack. The backend bind-mounts `backend/data` into `/app/data`, and its entrypoint wipes that directory on container start and shutdown so you see live files locally without persisting them between runs.
- `docs/` – Project overview, per-session prompts, and the Session 5 implementation plan.
- `backend/data/docs` – Authoritative markdown corpus that ingestion reads on startup. Stable filenames become `document_id` values inside chunk metadata.

## Prerequisites

- Python 3.11+
- Node.js 18+ and npm
- Docker + Docker Compose (for the one-command run flow)
- (Optional) OpenAI API key for live streaming. Without it the backend emits a deterministic fake response.
- (Optional) `GITHUB_TOKEN` for the external GitHub MCP server. Leave unset to exercise permission denial paths; when provided it enables `github.list_files` and `github.read_file`.

## Run everything with Docker Compose

```bash
docker compose -f infra/compose.yaml up --build
```

Ports:

- Frontend at http://localhost:3000 (Next.js dev server with hot reload)
- Backend at http://localhost:8000 (Uvicorn `--reload`)

Stop with `CTRL+C`. The backend entrypoint still clears `backend/data/events` and `backend/data/state` on startup/shutdown, but the repository itself is bind-mounted into both containers. That means any code change you make locally is reflected immediately in the running containers without rebuilding; the backend auto-restarts, and the frontend dev server hot reloads the UI.

> Tip: run `npm install` inside `frontend/` once on the host so `node_modules/` exists for the bind mount. The containers reuse that directory for faster restarts.

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

### Tool connectivity, MCP, and permissions

1. `backend/app/mcp/` hosts the MCP schema models (`schema.py`), registry (`registry.py`), client (`client.py`), abstract server contract (`server.py`), and concrete servers under `servers/`. Two servers are registered at startup: `CalculatorMCPServer` (local arithmetic) and `GitHubMCPServer` (read-only repo access via the GitHub REST API and the `GITHUB_TOKEN` env var).
2. `backend/app/permissions.py` defines a `PermissionGate` and `PermissionContext` that enforce scopes mechanically outside of intelligence. Calculators are always allowed, `github.read` is allowed only in `development`, and any future scope defaults to deny.
3. `backend/app/intelligence.py` planning consults the allowed set via the registry + permission gate, emits `tool.discovered` decision data, records `tool_selected`, and requests a tool directly from the plan node (transitioning into the waiting phase). Respond only streams model output when no tool was selected.
4. `backend/app/executor.py` subscribes to `tool.requested`, validates descriptors, runs the permission gate, emits `tool.denied` (without contacting the server) when scopes are disallowed, and routes execution through the MCP client when permitted. Server-level failures emit `tool.server.error` before the usual `tool.failed`.
5. `RunState` now stores `available_tools`, `requested_tool`, `tool_source`, `tool_permission_scope`, and `tool_denied_reason`. All MCP events are persisted so replay clearly shows which tools existed, which server provided them, and why a request did or did not run.
6. The UI steps panel tracks “Tool discovered → Tool requested → Tool executed → Tool denied” progress from event data. The Response panel includes a tool provenance card that lists every discovered tool plus the currently requested tool with source/scope metadata, and displays the mandated denial message (“Tool X was not permitted in this context”) whenever the backend emits `tool.denied`.

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
- `backend/app/coordinator.py` now bridges HTTP requests, tool events, and the workflow engine instead of running the graph directly. Every step transition is persisted in the workflow store and mirrored through `workflow.*` events so the execution order remains fully observable after restarts.
- The frontend displays the current status, per-node progress, output chunks, and decision log entirely from the streamed events. No hidden client-side state machines exist.

## Session 7 – Durable workflows

Session 7 replaces the transient coordinator loop with a workflow engine that persists every transition under `backend/data/workflow/<run_id>.json`. The engine maps each intelligence node to an idempotent activity, enforces retry policies (`respond` and `retrieve` back off before exhausting attempts), pauses cleanly for human approval, and resumes from durable state after crashes or restarts. New workflow events (`workflow.step.started`, `workflow.retrying`, `workflow.waiting_for_event`, `workflow.waiting_for_approval`, etc.) show up in the event log and power the frontend’s workflow status card.

### Crash-test durability

1. Start the backend (`uvicorn app.main:app --reload` or `docker compose up`) and kick off a run that streams for a few seconds (e.g., a question that triggers retrieval/respond).
2. Tail the workflow events: `tail -f backend/data/events/<run_id>.jsonl | rg workflow`.
3. After you see `workflow.step.started` for `respond` or `retrieve`, kill the backend process (CTRL+C or `docker compose stop backend`).
4. Restart the backend. On boot it reloads `RunState` + `WorkflowState`, observes pending steps, and emits `workflow.step.started` again without duplicating prior output/tool effects.
5. Refresh the UI – it fetches `/runs/<run_id>/workflow` to rebuild the workflow summary, reconnects to SSE, and continues streaming from the resumed step.
6. Repeat the test during tool execution or approval pauses to confirm retries, tool dedupe, and approvals all survive restarts.

### Workflow + approval APIs

- `GET /runs/{run_id}/workflow` returns the persisted workflow state (current step, attempts, pending events, approval flags) so you can inspect or debug a run outside the UI.
- `POST /runs/{run_id}/approval` with `{"decision":"approved"}` or `{"decision":"rejected"}` records the human decision and causes the workflow engine to resume the paused step.
- The frontend renders a dedicated Approval Gate with Approve / Reject buttons whenever the workflow emits `workflow.waiting_for_approval`.

## Session 8 – Traces and observability

- Every run now creates a durable trace under `backend/data/traces/{run_id}.json`. Each file stores the trace envelope plus every span (workflow steps, intelligence nodes, tools, model calls, waits, and retries) so you can replay timelines after restarts.
- Read-only endpoints expose this data: `GET /runs/{run_id}/trace` returns the combined trace/spans payload, while `GET /runs/{run_id}/spans` streams spans only. The frontend’s inspector as well as CLI tooling use these routes.
- The main UI status card surfaces a thin slice of tracing signals. You will now see explicit messaging when the system is retrying a step, waiting for approval, blocked on an MCP tool, or waiting on retrieval to finish. These hints are derived directly from the workflow spans and stay in sync with the backend.
- While a run is in flight the frontend polls `/runs/{run_id}/spans` every few seconds, extracts spans with `status=retried` or `status=waiting`, and renders banner chips (“Retry scheduled”, “Waiting for approval”, “Tool running”, “Retrieval pending”). No stack traces or raw errors leak to users—only high-level span metadata.
- A developer-only Run Inspector lives at `http://localhost:3000/runs/<run_id>/inspect` (there’s also a link on the status card once a run starts). It fetches the stored trace, renders a timeline with parent/child indentation, shows detailed span metadata, and summarizes workflow steps so you can explain any run in minutes—no log tailing required.
- Typical flow: kick off a run, copy the printed `run_id` (also shown in the UI), open the inspector route in a new tab, and refresh as needed. The trace persists even if you restart the backend, making yesterday’s failures just as inspectable as today’s.

### Trace persistence crash test

1. Start the backend (local or via Docker Compose) and trigger a run that will take a few seconds (tool invocation, retrieval, etc.). Copy the `run_id` from the UI status card.
2. Tail the trace file while the run is still executing: `jq . backend/data/traces/<run_id>.json` — new spans are appended as soon as they start/end.
3. Kill the backend (`CTRL+C` or `docker compose stop backend`) before the run finishes. The workflow engine persists `WorkflowState` plus the trace file is already on disk.
4. Restart the backend. As soon as the workflow resumes, open `http://localhost:3000/runs/<run_id>/inspect` or call `curl http://localhost:8000/runs/<run_id>/trace`. You should see all pre-crash spans intact.
5. Let the run finish. Re-open the same inspector view—the final spans (retry, wait, finalize, etc.) append to the existing JSON file without overwriting prior data. Repeat the test the next day to confirm “yesterday’s run” is still inspectable without re-running anything.

### Retention / cleanup

Trace files are plain JSON documents under `backend/data/traces` and are not automatically pruned. When running long-lived environments, use the helper script to remove older files:

```bash
python backend/scripts/purge_traces.py --days 14          # delete traces older than 14 days
python backend/scripts/purge_traces.py --days 30 --dry-run  # preview deletions without removing files
```

The script defaults to the repository’s `backend/data/traces` directory but accepts `--traces-dir` if you mount data elsewhere (e.g., inside Docker volumes).

### Observability quick reference

| Artifact | Location / API | Notes |
| --- | --- | --- |
| Trace files | `backend/data/traces/{run_id}.json` | Contains `{trace, spans}`; safe to inspect offline. |
| Full trace API | `GET /runs/{run_id}/trace` | Returns the JSON payload used by the inspector. |
| Span-only API | `GET /runs/{run_id}/spans` | Useful for lightweight polling (the frontend uses this for Status card alerts). |
| Run inspector | `http://localhost:3000/runs/{run_id}/inspect` | Dev-only route; also linked from the Status card once a run starts. |

Error types follow the Session 8 classification (e.g., `network_failure`, `bad_plan`, `permission_denied`). You can filter spans by `attributes.error_type` to separate intelligence failures from system issues. All spans share the same `trace_id`, and parent-child relationships mirror real execution order—if a span lacks its parent, treat it as an observability bug.

## Validation checklist (Session 6)

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
11. **MCP discovery** – Start a run and watch the SSE stream for `tool.discovered` events before planning makes a decision. The steps panel should mark “Tool discovered” as soon as at least one tool is available, and `/runs/<id>/state` should list the same descriptors (with `source`, `permission_scope`, and `server_id`).
12. **Permission enforcement** – Leave `GITHUB_TOKEN` unset and ask for GitHub data (e.g., “list files in repo octocat/Hello-World”). Planning should still discover the GitHub tools but the executor must emit `tool.denied` with reason `scope_not_allowed_environment`, the UI must display “Tool github.list_files was not permitted in this context,” and the run should finalize with a failure outcome without contacting GitHub.
13. **External execution** – Provide a valid `GITHUB_TOKEN`, rerun the same request, and confirm `tool.executed` completes successfully, `tool.server.error` never fires, and the tool panel shows `source=external` with scope `github.read`. Disable the GitHub server in `backend/app/main.py` (comment out `GitHubMCPServer`) and verify no intelligence changes are required—the planner simply lists fewer discovered tools.

See `docs/session_3_plan.md` and `docs/session_4_plan.md` for the implementation details and follow-up notes.
