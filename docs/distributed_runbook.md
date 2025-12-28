# Distributed Mode Runbook

Distributed mode splits responsibilities across processes while keeping the same event vocabulary and UI behavior.

## What runs where

- **Backend API** (`infra/compose.distributed.yaml` → `backend`)
  - Serves HTTP + SSE.
  - Publishes run requests/events.
  - Does **not** drive workflow execution.
  - Does **not** execute tools.

- **Workflow worker** (`workflow-worker`)
  - Runs knowledge ingestion on boot (so in-memory retrieval is populated).
  - Drives the durable workflow execution via the `RunCoordinator`.

- **Tool worker** (`tool-worker`)
  - Consumes durable tool requests from Redis Streams.
  - Executes tools via `ToolExecutor` and emits completion/failure events.

- **Redis**
  - Event fanout (pubsub transport).
  - Durable stores (events/state/workflow/traces) for distributed mode.
  - Durable tool queue (Redis Streams).

## Prereqs

- Docker + Docker Compose
- Root `.env` file exists (start with `cp .env.example .env`)

Required in `.env` for distributed mode:

- `BACKEND_MODE=distributed`
- `REDIS_URL=redis://redis:6379/0` (Compose also sets this)

Optional:

- `OPENAI_API_KEY` (enables real model output; otherwise deterministic fallback)
- `GITHUB_TOKEN` (enables GitHub MCP tools)

## Start the stack

From repo root:

```bash
docker compose -f infra/compose.distributed.yaml up -d --build
```

Check containers:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expected (names may vary slightly):

- `infra-redis-1`
- `infra-backend-1` (port `8000`)
- `infra-workflow-worker-1`
- `infra-tool-worker-1`
- `infra-frontend-1` (port `3000`)

## Health check

```bash
curl -sf http://localhost:8000/health
```

Expected:

- `{"status":"ok"}`

## Debugging

Tail logs:

```bash
docker logs --tail 200 infra-backend-1
docker logs --tail 200 infra-workflow-worker-1
docker logs --tail 200 infra-tool-worker-1
```

Confirm SSE subscribers exist (useful when UI looks stuck):

```bash
docker exec infra-redis-1 redis-cli PUBSUB NUMSUB events:all
```

Inspect tool queue health:

```bash
docker exec infra-redis-1 redis-cli XLEN queue:tools
docker exec infra-redis-1 redis-cli XINFO GROUPS queue:tools
docker exec infra-redis-1 redis-cli XINFO CONSUMERS queue:tools tool-workers
```

Inspect run event list (Redis-backed event store):

```bash
RUN_ID=<run_id>
docker exec infra-redis-1 redis-cli --raw LRANGE ai:run:$RUN_ID:events 0 -1 | tail -n 80
```

## Shut down

```bash
docker compose -f infra/compose.distributed.yaml down
```
