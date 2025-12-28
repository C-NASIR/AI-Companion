# Backend Overview (AI Companion)

This document explains the backend as if you’ve just cloned the repo and don’t know the codebase yet. It’s a high-level map of **what the app is**, **what it does**, and **where to look** when you want to change behavior.

Here’s the backend in plain terms: it’s a run engine for an AI assistant where every significant thing that happens is recorded as an event, and those events are streamed live to the UI.

## Module map

- **Execution**: `backend/app/workflow/engine.py` + `backend/app/workflow/activities.py`
- **State**: `backend/app/state.py` + stores under `backend/app/state_store.py` / `backend/app/workflow/store.py`
- **Planning**: `backend/app/planning.py`
- **Tool intents**: `backend/app/tool_intents.py`
- **Tool feedback**: `backend/app/tool_feedback.py`
- **Tools**: `backend/app/executor.py`, `backend/app/mcp/*`
- **Guardrails**: `backend/app/guardrails/*`
- **Observability**: `backend/app/events.py`, `backend/app/observability/*`

## Table of contents

- [What the backend is](#what-the-backend-is)
- [What happens during a run](#what-happens-during-a-run)
- [Major components](#major-components)
  - [App bootstrap + dependency wiring](#1-app-bootstrap--dependency-wiring)
  - [Events (the backbone)](#2-events-the-backbone)
  - [RunState (the snapshot)](#3-runstate-the-snapshot)
  - [Workflow engine (durable execution)](#4-workflow-engine-durable-execution)
  - [Intelligence logic (plan/retrieve/respond/verify/finalize)](#5-intelligence-logic-planretrieverespondverifyfinalize)
  - [Retrieval + knowledge ingestion](#6-retrieval--knowledge-ingestion)
  - [Model streaming (real or deterministic fallback)](#7-model-streaming-real-or-deterministic-fallback)
  - [Tools via MCP](#8-tools-via-mcp)
  - [Guardrails](#9-guardrails)
  - [Observability (traces/spans)](#10-observability-tracesspans)
- [Single-process vs distributed mode](#single-process-vs-distributed-mode)
- [Where to start reading](#where-to-start-reading)

---

## What the backend is

The backend is a **run engine** for an AI assistant where every significant thing that happens is recorded as an event, and those events are streamed live to the UI.

- It’s a **FastAPI** service you can call to start a “run” (a single chat execution).
- It drives a workflow (plan → retrieve → respond → verify → finalize).
- It optionally executes tools.
- It enforces guardrails.
- It persists **events**, **state**, and **trace spans** so you can replay and debug runs later.

If you remember one sentence: **the backend is an event-driven workflow runner.**

### Key API endpoints (mental model)

- `POST /runs` → start a new run; returns a `run_id`
- `GET /runs/{run_id}/events` → SSE stream of all events (replay + live)
- `GET /runs/{run_id}/state` → latest `RunState` snapshot
- `GET /runs/{run_id}/workflow` → durable workflow state
- `POST /runs/{run_id}/approval` → record approve/reject and resume
- `GET /runs/{run_id}/trace` / `GET /runs/{run_id}/spans` → trace timeline/spans
- `POST /feedback` → save thumbs up/down feedback (JSONL)

Routes are defined in `backend/app/api.py`.

---

## What happens during a run

When you call `POST /runs`, the backend:

1. Creates a `RunState` (a canonical snapshot of the run’s current state).
2. Emits a `run.started` event.
3. Starts the **WorkflowEngine** for that run.
4. The workflow executes steps and emits events like:
   - `workflow.step.started`, `node.started`, `retrieval.started`, `output.chunk`, `decision.made`, `tool.requested`, `tool.completed`, `run.completed`, etc.
5. Everything is persisted so the UI can reconstruct the run any time.

The frontend is primarily a **subscriber**: it listens to `/runs/<run_id>/events` and renders exactly what it sees.

---

## Major components

### 1) App bootstrap + dependency wiring

- `backend/app/main.py` boots the FastAPI app:

  - loads env
  - runs startup checks
  - builds the dependency container
  - in `single_process` mode: initializes MCP, starts the in-process tool executor, and runs knowledge ingestion
  - in `distributed` mode: stays API-focused (workers handle ingestion + tool execution)

- `backend/app/container.py` builds a `BackendContainer`, which holds constructed dependencies:
  - event store/bus
  - state/workflow/trace stores
  - workflow engine + activity context
  - retrieval store + embedding generator
  - MCP registry/client
  - permission gate, caching, rate limiting, budgets, guardrails

This structure is intentional: it keeps imports side-effect free and makes “single vs distributed” wiring explicit.

---

### 2) Events (the backbone)

Everything the system does is captured as a durable event and streamed live.

- `backend/app/events.py` defines:
  - the `Event` schema (`run_id`, `seq`, `ts`, `type`, `data`)
  - helper constructors for typed events (tool lifecycle, retrieval events, guardrail events, etc.)
  - `EventStore` (durable JSONL per run)
  - `EventBus` (persist-first, then broadcast)
  - `sse_event_stream()` (replay + live SSE streaming)

Conceptually:

- The **EventStore** is the append-only history on disk (or Redis-backed in distributed mode).
- The **EventBus** is “write to store first, then fan out.”
- The UI relies on events as the single source of truth.

Live fanout transport is implemented in `backend/app/event_transport.py` (in-memory or Redis pub/sub).

---

### 3) RunState (the snapshot)

Events are history; `RunState` is the latest snapshot.

- `backend/app/state.py` defines `RunState`, which includes:

  - message/context/mode + identity (tenant/user)
  - current phase (`RunPhase`)
  - decisions (human-readable “why we did X” records)
  - tool requests/results + denial reasons
  - retrieved chunks and sanitized chunk IDs
  - guardrail status/reason/layer
  - accumulated output text
  - budget/degraded flags

- `backend/app/state_store.py` persists the latest snapshot as JSON: `backend/data/state/<run_id>.json`.

Mental model:

- **Events**: append-only, replayable, ordered timeline of everything that happened.
- **RunState**: a materialized view you can query without replaying events.

---

### 4) Workflow engine (durable execution)

The workflow engine is the “brainstem”: it runs steps, persists workflow progress, and handles retries/waits.

- `backend/app/workflow/engine.py`:

  - runs step-by-step execution
  - supports retries with backoff
  - can enter “waiting” states (waiting for tool events, approvals, etc.)
  - persists `WorkflowState` so a run can resume after a crash/restart

- `backend/app/workflow/models.py` and `backend/app/workflow/store.py`:

  - define `WorkflowState` and store/load it

- `backend/app/workflow/activities.py`:
  - implements step logic (receive/plan/retrieve/respond/verify/finalize)
  - updates `RunState`
  - emits events
  - enforces safety and budgets
  - coordinates with tools and external events

If you want to understand “how a run actually works,” start in `backend/app/workflow/activities.py`.

---

### 5) Intelligence logic (plan/retrieve/respond/verify/finalize)

The durable workflow path is the source of truth for execution:

- `backend/app/workflow/activities.py` implements the step logic (receive/plan/retrieve/respond/verify/finalize).
- `backend/app/workflow/engine.py` drives steps, persists workflow state, and handles retries/waits.

Some helper logic is factored into small shared modules:

- `backend/app/planning.py` (plan heuristics)
- `backend/app/tool_intents.py` (tool intent parsing)
- `backend/app/tool_feedback.py` (tool summary/failure formatting)
- `backend/app/run_logging.py` (run-scoped logging helper)

If you’re new, start in `backend/app/workflow/activities.py`.

---

### 6) Retrieval + knowledge ingestion

Retrieval answers the question: “what internal evidence should we use?”

- `backend/app/ingestion.py`:

  - reads markdown documents from `backend/data/docs`
  - chunks text
  - generates embeddings
  - inserts chunk embeddings into the retrieval store
  - emits knowledge ingestion events

- `backend/app/retrieval.py`:
  - in-memory cosine similarity store
  - returns top-k `RetrievedChunk` records (id, text, score, metadata)

This is intentionally simple (not a vector DB) to keep the system deterministic and easy to understand.

---

### 7) Model streaming (real or deterministic fallback)

The backend can stream real model output or a deterministic local fallback.

- `backend/app/model.py`:
  - `real_stream()` streams OpenAI responses when `OPENAI_API_KEY` is set
  - `fake_stream()` emits deterministic text chunks when no key is set
  - `stream_chat()` chooses between them

Model selection is routed by capability:

- `backend/app/models/router.py` maps capabilities (planning/generation/verification/classification) to model IDs via env vars.

---

### 8) Tools via MCP

Tools are discovered and executed through an MCP-style registry + executor with permission scopes and schemas.

Core pieces:

- `backend/app/mcp/bootstrap.py` registers MCP servers and discovers tool descriptors on startup.
- `backend/app/mcp/registry.py` stores discovered tool descriptors.
- `backend/app/mcp/client.py` executes tools against registered servers.
- `backend/app/mcp/servers/*` provide example servers:
  - calculator (local)
  - GitHub read-only (external via HTTP)

Execution is centralized in:

- `backend/app/executor.py` (`ToolExecutor`):
  - listens for `tool.requested` events
  - validates argument schemas
  - enforces a tool firewall (allowlist + limits)
  - checks permission scopes (`backend/app/permissions.py`)
  - emits `tool.completed`, `tool.failed`, `tool.denied`, and `tool.server.error`
  - caches read-only tool results when enabled

In distributed mode, tool requests can be consumed by `backend/app/worker/tool_worker.py` via a durable queue.

---

### 9) Guardrails

Guardrails are layered safety checks that can stop or modify execution with clear reasons.

- `backend/app/guardrails/*` includes:
  - input gate
  - context sanitizer
  - injection detector
  - output validator
  - refusal helpers

Guardrail interventions emit inspectable events:

- `guardrail.triggered`
- `context.sanitized`
- `injection.detected`

This is designed so “safety behavior” is visible and debuggable, not mysterious.

---

### 10) Observability (traces/spans)

Traces provide a timeline view of execution (steps, waits, retries, model calls, tool calls).

- `backend/app/observability/tracer.py` + `backend/app/observability/store.py` persist per-run traces and spans.
- `backend/app/observability/api.py` exposes:
  - `GET /runs/{run_id}/trace`
  - `GET /runs/{run_id}/spans`

The frontend’s run inspector reads this data and renders a timeline.

---

## Single-process vs distributed mode

**Single-process (`BACKEND_MODE=single_process`)**

- FastAPI process runs workflow engine + tool executor in-process.
- Artifacts persist under `backend/data/` (events/state/workflow/traces).

**Distributed (`BACKEND_MODE=distributed`)**

- API service accepts requests and persists events/state.
- A workflow worker drives workflows (`backend/app/worker/workflow_worker.py`).
- A tool worker executes tools (`backend/app/worker/tool_worker.py`).
- Redis provides fanout + a durable tool queue.

---

## Where to start reading

If you’re new, this is the most effective reading order:

1. `backend/app/main.py` and `backend/app/api.py` (entry + endpoints)
2. `backend/app/events.py` (event model + SSE)
3. `backend/app/workflow/engine.py` and `backend/app/workflow/activities.py` (core execution)
4. `backend/app/executor.py` and `backend/app/mcp/*` (tools)
5. `backend/app/guardrails/*` (safety)
6. `backend/app/observability/*` (traces)

If you tell me whether you want to (a) add a new tool, (b) change workflow behavior, or (c) map UI ↔ events, I can point you to the exact files/functions to modify.
