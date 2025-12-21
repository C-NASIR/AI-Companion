# AI Companion

Session 0 delivers a minimal intent-to-response flow: a user types a message in the Next.js UI, the request goes through our FastAPI backend, and the response streams back chunk-by-chunk while sharing a `run_id` across browser logs and backend logs.

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

Visit http://localhost:3000 and use the textarea/send button. The browser console logs the generated `run_id`; backend logs show identical IDs for the lifecycle messages (`request received`, `model call started`, `model stream ended`, `request completed`).

## Environment variables

- `OPENAI_API_KEY` (optional) – enables live streaming via the OpenAI SDK.
- `OPENAI_BASE_URL` (optional) – override the API base URL.
- `OPENAI_MODEL` (optional) – default `gpt-4o-mini`.
- `NEXT_PUBLIC_BACKEND_URL` – frontend uses this to locate the backend (automatically set inside Docker, configure manually for local dev).

Use a root-level `.env` to share OpenAI settings with docker-compose, or export them before launching the backend locally.

## Regression checklist

1. Start backend + frontend (locally or via Docker).
2. Submit a prompt from the UI; observe streaming output updates without waiting for completion.
3. Confirm the same `run_id` appears in browser console and backend logs.
4. Optional: `curl -N -H "Content-Type: application/json" -d '{"message":"ping"}' http://localhost:8000/chat` to watch raw streaming chunks.

See `docs/session_1_phase1.md` for Phase 1 findings plus the upcoming structured intent/event streaming plans for Session 1.
