# Industrial Packaging RAG Agent

AI-powered question-answering system for industrial packaging, deployment strategy, and quality management knowledge.

**AI407L — Deployment Packaging & Automated Quality Gates**

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Docker Compose                                         │
│                                                         │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │   agent (port 8000)  │───▶│  chroma (port 8001)  │  │
│  │   FastAPI + LangChain│    │   ChromaDB server    │  │
│  │   RAG pipeline       │    │   Vector index       │  │
│  └──────────────────────┘    └──────────────────────┘  │
│              │                          │               │
│              │               ┌──────────┴──────┐       │
│              │               │  chroma_data    │       │
│              │               │  (named volume) │       │
│              │               └─────────────────┘       │
└─────────────────────────────────────────────────────────┘
```

The agent retrieves relevant chunks from ChromaDB, then asks GPT-4o-mini to answer
using only those chunks (RAG = Retrieval-Augmented Generation).

---

## Quick Start

### Prerequisites
- Docker Desktop (includes docker compose v2)
- An OpenAI API key

### 1. Configure secrets

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY=sk-...
```

### 2. Start the stack

```bash
docker compose up --build
```

Both services start. The agent waits for ChromaDB to pass its health check before
ingesting documents and accepting requests.

### 3. Query the agent

```bash
curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What materials are used in industrial packaging?"}' | python -m json.tool
```

### 4. Health check

```bash
curl http://localhost:8000/health
# {"status":"ok","agent_ready":true}
```

### 5. Stop the stack

```bash
docker compose down        # keeps the vector index (chroma_data volume)
docker compose down -v     # removes the volume too (full clean reset)
```

---

## Local Development (without Docker)

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Set OPENAI_API_KEY and CHROMA_MODE=embedded in .env

uvicorn app.main:app --reload
```

In `embedded` mode, ChromaDB runs in-process (no separate container needed).

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Returns `{"status":"ok","agent_ready":true}` when ready |
| POST | `/query` | Query the knowledge base — body: `{"question": "..."}` |
| POST | `/ingest` | Add a document — body: `{"text": "...", "source": "my_doc"}` |

Interactive docs: http://localhost:8000/docs

---

## Automated Quality Gate

The CI pipeline (`.github/workflows/quality-gate.yml`) runs on every push to `main`:

1. Builds and starts the full Docker Compose stack
2. Waits for the agent to become healthy
3. Runs `run_eval.py` — an LLM-as-judge evaluation against 7 test questions
4. Checks scores against thresholds in `eval_thresholds.json`
5. Exits 0 (pass) or 1 (fail); uploads `eval_results.json` as a build artefact

### Quality thresholds

| Metric | Threshold | Meaning |
|--------|-----------|---------|
| `faithfulness` | ≥ 0.70 | Answers must be grounded in retrieved context |
| `answer_relevancy` | ≥ 0.75 | Answers must address the question asked |

### Running the evaluation locally

```bash
# Start the stack first
docker compose up -d --build

# Install lightweight eval dependencies
pip install -r requirements-eval.txt

# Run evaluation
OPENAI_API_KEY=sk-... AGENT_URL=http://localhost:8000 python run_eval.py

# Results
cat eval_results.json
```

### Required GitHub Secret

Add `OPENAI_API_KEY` to **Repository → Settings → Secrets → Actions** before the
first pipeline run. No secret ever appears in any committed file.

---

## Deliverables

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage container build |
| `docker-compose.yml` | Multi-service orchestration (agent + ChromaDB) |
| `.github/workflows/quality-gate.yml` | CI/CD pipeline with quality gate |
| `run_eval.py` | CI-ready evaluation script (exits 0/1) |
| `eval_thresholds.json` | Versioned quality thresholds |
