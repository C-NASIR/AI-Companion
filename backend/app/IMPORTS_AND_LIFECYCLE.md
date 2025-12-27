# Imports and Lifecycle

## Rules of Thumb
- Imports should be safe: importing a module must not write to disk, start background tasks, or perform network calls.
- Initialization must be explicit: construct dependencies with `build_container(...)` and start side effects via `startup(container)`.
- Prefer dependency injection: pass dependencies (or access `app.state.container`) instead of importing module globals.

## Where Things Live
- **Container construction (no side effects):** `backend/app/container.py:build_container`
- **Startup side effects:** `backend/app/container.py:startup` and FastAPI startup in `backend/app/main.py:create_app`
- **Shutdown:** `backend/app/container.py:shutdown`
- **API router wiring:** `backend/app/api.py:get_router`

## Entry Point
- Use `uvicorn app.main:create_app --factory`.
- Avoid importing `app.main:app` directly in new tooling; it exists only for legacy compatibility.

## Anti-patterns (Avoid)
- Module-level singletons like `FOO = Foo(...)` when they touch IO or runtime wiring.
- Calling `Path(...).mkdir(...)`, `load_dotenv()`, starting threads/tasks, or subscribing to buses at import time.
- Importing `STATE_STORE`, `EVENT_BUS`, etc. from `app.api` (deprecated).

## Local Checks
- Import-only side effects: `python3 backend/scripts/verify_import_side_effects.py`
- Multi-worker startup: `cd backend && python3 scripts/verify_multiworker_startup.py --workers 2`

