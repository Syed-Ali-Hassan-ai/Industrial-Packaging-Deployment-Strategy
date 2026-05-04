# AI407L — Deployment Packaging & Automated Quality Gates
## Project Report: Industrial Packaging RAG Agent

**Course:** AI407L  
**Project:** Industrial Packaging & Deployment Strategy + Automated Quality Gates & CI/CD  
**Repository:** https://github.com/Syed-Ali-Hassan-ai/Industrial-Packaging-Deployment-Strategy  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Part A — Industrial Packaging & Deployment Strategy](#part-a--industrial-packaging--deployment-strategy)
   - [2. Reproducible Container Image](#2-reproducible-container-image)
   - [3. Secret-Free Image](#3-secret-free-image)
   - [4. Multi-Service Orchestration](#4-multi-service-orchestration)
   - [5. End-to-End Test Evidence](#5-end-to-end-test-evidence)
3. [Part B — Automated Quality Gates & CI/CD](#part-b--automated-quality-gates--cicd)
   - [6. CI-Ready Evaluation Script](#6-ci-ready-evaluation-script)
   - [7. Pipeline Configuration](#7-pipeline-configuration)
   - [8. Versioned Threshold Configuration](#8-versioned-threshold-configuration)
   - [9. Breaking Change Demonstration](#9-breaking-change-demonstration)
4. [Submission Checklist](#submission-checklist)

---

## 1. System Overview

The project is a **Retrieval-Augmented Generation (RAG) AI agent** specialised in industrial packaging, deployment strategy, and quality management knowledge. Users ask natural-language questions; the agent retrieves the most relevant passages from a ChromaDB vector store and asks GPT-4o-mini to generate an answer grounded strictly in those passages.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Docker Compose (single command: docker compose up --build) │
│                                                             │
│  ┌──────────────────────────┐   ┌────────────────────────┐  │
│  │  agent  (port 8000)      │──▶│  chroma  (port 8001)   │  │
│  │  FastAPI + LangChain RAG │   │  ChromaDB HTTP server  │  │
│  │  GPT-4o-mini             │   │  Vector index          │  │
│  └──────────────────────────┘   └────────────┬───────────┘  │
│                                              │               │
│                                  ┌───────────▼──────────┐   │
│                                  │  chroma_data (volume)│   │
│                                  │  Persists on restart │   │
│                                  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Layer | Technology | Version |
|---|---|---|
| API Framework | FastAPI + Uvicorn | 0.115.5 / 0.32.1 |
| RAG Orchestration | LangChain | 0.3.7 |
| Embeddings | OpenAI text-embedding-3-small | via API |
| LLM | GPT-4o-mini | via API |
| Vector Store | ChromaDB | 0.5.20 |
| Container Runtime | Docker + Docker Compose | v2 |
| CI/CD | GitHub Actions | ubuntu-latest |

### Knowledge Base

Three domain-specific documents were authored and ingested at startup:

| File | Chunks | Domain |
|---|---|---|
| `data/industrial_packaging.txt` | 15 | Materials, standards, packaging types |
| `data/deployment_strategies.txt` | 21 | JIT, packaging logistics, ECT |
| `data/quality_management.txt` | 22 | ISO standards, QA processes |
| **Total** | **58** | |

---

## Part A — Industrial Packaging & Deployment Strategy

---

## 2. Reproducible Container Image

### 2.1 Choice of Base Image

The base image selected is **`python:3.11-slim`** for both the builder and runtime stages.

**Justification:**

- `python:3.11-slim` weighs approximately 50 MB, compared to ~900 MB for the full `python:3.11` image. The reduction comes from omitting compilers, documentation, and testing utilities that are irrelevant at runtime.
- Python 3.11 is used because `langchain==0.3.7`, `chromadb==0.5.20`, and `pydantic-settings==2.6.1` all explicitly support it and it is the version pinned in the GitHub Actions runner (`actions/setup-python@v5` with `python-version: "3.11"`), guaranteeing identical interpreter behaviour in CI and in the container.
- `python:3.11-slim` retains the C standard library (`libc`), which is required by ChromaDB's native `hnswlib` extension. A `python:3.11-alpine` base was evaluated and rejected because `musl libc` causes incompatibilities with `hnswlib`'s pre-compiled wheels.

### 2.2 Multi-Stage Build

The `Dockerfile` uses a **two-stage build**:

```dockerfile
# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder
WORKDIR /build

# gcc and build-essential compile chromadb's hnswlib C extension.
# Installed ONLY in the builder — never in the runtime image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim
WORKDIR /app

COPY --from=builder /root/.local /root/.local   # compiled packages only
COPY app/ ./app/
COPY data/ ./data/

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
```

**Why two stages?**

- `gcc` and `build-essential` are needed only to compile `hnswlib` during `pip install`. If they remain in the final image they add ~200 MB and introduce a larger attack surface.
- The multi-stage pattern ensures the runtime image contains **only compiled packages and application code** — no compiler, no pip cache, no build artefacts.
- `--no-cache-dir` on the `pip install` prevents pip's HTTP cache from being committed into the layer, further reducing image size.

**Verification — gcc absent from runtime image:**

```
$ docker run --rm industrial-packaging-deployment-strategy-agent which gcc
(no output — gcc not present)

$ docker run --rm industrial-packaging-deployment-strategy-agent gcc --version
/bin/sh: 1: gcc: not found
```

### 2.3 Layer Ordering Strategy

Layer ordering directly determines how much of the Docker build cache is reused on each rebuild. The strategy is:

```
Layer 1  FROM python:3.11-slim          ← changes never (base image)
Layer 2  apt-get install gcc            ← changes only if build tools change
Layer 3  COPY requirements.txt .        ← changes only when dependencies change
Layer 4  pip install -r requirements    ← invalidated only when Layer 3 changes
Layer 5  COPY app/ ./app/               ← changes on every code edit
Layer 6  COPY data/ ./data/             ← changes when knowledge base changes
```

**Key decision:** `requirements.txt` is copied and installed **before** `app/` is copied. Application code changes on every commit; dependency changes are rare. By separating them into different layers, a typical code-only change skips Layers 2–4 (the expensive `pip install`) entirely, reducing rebuild time from ~90 seconds to ~3 seconds.

---

## 3. Secret-Free Image

### 3.1 What is excluded from the image

The `Dockerfile` contains no `ENV OPENAI_API_KEY` directive. The image therefore contains zero credentials at build time. Additionally, `.dockerignore` excludes:

```
.env
.env.*
.chroma_local/
venv/
__pycache__/
*.pyc
```

**Verification — OPENAI_API_KEY absent from image config:**

```bash
$ docker inspect industrial-packaging-deployment-strategy-agent \
    --format '{{json .Config.Env}}'

["PATH=/root/.local/bin:/usr/local/bin:...", "PYTHONUNBUFFERED=1",
 "PYTHONDONTWRITEBYTECODE=1"]
```

`OPENAI_API_KEY` does not appear. The image can be pushed to a public registry without leaking credentials.

### 3.2 Runtime Secret Injection

The key is injected at runtime via Docker Compose reading from the host shell environment:

```yaml
# docker-compose.yml
services:
  agent:
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}   # read from host env at compose-up time
```

**Local development:** the developer creates a `.env` file (excluded from git by `.gitignore`):

```bash
cp .env.example .env
# edit .env: OPENAI_API_KEY=sk-...
docker compose up --build
```

Docker Compose automatically loads `.env` from the project directory. The key is present only in the running container's memory, never on disk inside the image.

**CI/CD:** the key is stored in GitHub → Repository Settings → Secrets → Actions as `OPENAI_API_KEY` and injected by the workflow:

```yaml
- name: Build and start services
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: docker compose up -d --build
```

GitHub Actions masks the value in all log output, replacing it with `***`.

### 3.3 Verification

```bash
# Running container DOES have the key (injected at runtime)
$ docker exec industrial-packaging-deployment-strategy-agent-1 \
    printenv OPENAI_API_KEY
sk-proj-...

# Image inspect shows NO key baked in
$ docker inspect industrial-packaging-deployment-strategy-agent \
    --format '{{range .Config.Env}}{{println .}}{{end}}' | grep OPENAI
(no output)
```

---

## 4. Multi-Service Orchestration

### 4.1 Services

Two services are defined in `docker-compose.yml`:

| Service | Image | Internal Port | Host Port | Role |
|---|---|---|---|---|
| `chroma` | `chromadb/chroma:0.5.20` | 8000 | 8001 | Vector database HTTP server |
| `agent` | Built from `Dockerfile` | 8000 | 8000 | RAG API |

### 4.2 Service Discovery

Services communicate using **Docker's internal DNS**. Docker Compose places both containers on the same bridge network and registers each service name as a DNS hostname resolvable within that network.

```yaml
# agent's environment in docker-compose.yml
environment:
  - CHROMA_HOST=chroma      # resolves to chroma container's IP via Docker DNS
  - CHROMA_PORT=8000        # ChromaDB's internal port (not the host port 8001)
```

```python
# app/config.py
class Settings(BaseSettings):
    chroma_host: str = "localhost"
    chroma_port: int = 8000
    chroma_mode: str = "server"
```

```python
# app/agent.py — HttpClient uses the DNS name
client = chromadb.HttpClient(
    host=self.settings.chroma_host,   # "chroma"
    port=self.settings.chroma_port,   # 8000
)
```

The agent **never** uses an IP address. Docker DNS ensures the name `chroma` always resolves regardless of the container's assigned IP.

### 4.3 Start / Stop Ordering

The agent must not start before ChromaDB is accepting connections. This is enforced with a `depends_on` health check condition:

```yaml
agent:
  depends_on:
    chroma:
      condition: service_healthy   # waits for ChromaDB /api/v1/heartbeat to pass

chroma:
  healthcheck:
    test:
      - "CMD-SHELL"
      - python -c "import urllib.request;
          urllib.request.urlopen('http://localhost:8000/api/v1/heartbeat')"
    interval: 10s
    timeout: 5s
    retries: 10
    start_period: 15s
```

Docker Compose will not launch the agent container until ChromaDB passes the health check. The agent additionally has its own 15-retry connection loop (`app/agent.py: _connect_chroma`) so it tolerates any residual delay.

**Start all services:**
```bash
docker compose up -d --build
```

**Stop all services (preserving data):**
```bash
docker compose down
```

**Stop and remove all data (full clean reset):**
```bash
docker compose down -v
```

### 4.4 Persistent Data — Proof

The vector index is stored in a **named Docker volume** (`chroma_data`). Named volumes are not removed by `docker compose down` (only by `docker compose down -v`), so the index survives container restarts.

**Persistence test procedure:**

**Step 1 — Confirm chunk count before restart:**
```bash
$ curl -s http://localhost:8000/health
{"status":"ok","agent_ready":true}

$ curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What materials are used in industrial packaging?"}'
# Returns full answer sourced from industrial_packaging.txt
```

Agent logs on startup confirm 58 chunks ingested:
```
INFO  app.agent  Prepared 21 chunks from deployment_strategies.txt
INFO  app.agent  Prepared 15 chunks from industrial_packaging.txt
INFO  app.agent  Prepared 22 chunks from quality_management.txt
INFO  app.agent  Ingested 58 total chunks into collection 'packaging_kb'
```

**Step 2 — Restart only the ChromaDB container:**
```bash
$ docker compose restart chroma
```

**Step 3 — Confirm the agent reconnects and the index is intact:**
```bash
$ curl -s http://localhost:8000/health
{"status":"ok","agent_ready":true}
```

Agent logs after restart show **skip ingestion** (collection already has 58 chunks):
```
INFO  app.agent  Collection 'packaging_kb' already has 58 chunks — skipping ingestion
```

The vector index was loaded from the `chroma_data` volume — no re-embedding was needed.

**Step 4 — Full stack restart (both containers):**
```bash
$ docker compose down
$ docker compose up -d
```

Same result: agent detects 58 existing chunks and skips ingestion. The `chroma_data` volume is unchanged.

---

## 5. End-to-End Test Evidence

### 5.1 Starting from Configuration Files Alone

```bash
# Clone the repository on a fresh machine
git clone https://github.com/Syed-Ali-Hassan-ai/Industrial-Packaging-Deployment-Strategy.git
cd Industrial-Packaging-Deployment-Strategy

# Configure the only required secret
echo "OPENAI_API_KEY=sk-..." > .env

# Single command — no manual setup
docker compose up --build
```

### 5.2 Health Check

```bash
$ curl http://localhost:8000/health
{"status":"ok","agent_ready":true}
```

### 5.3 Live Query — In-Scope Question

```bash
$ curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the Edge Crush Test and why is it important?"}' \
  | python -m json.tool
```

**Response:**
```json
{
  "answer": "The Edge Crush Test (ECT) measures the stacking strength of
  corrugated board by compressing a flute edge sample. It is important
  for corrugated packaging because the ECT value directly correlates to
  the box compression strength (BCT) of corrugated cases, which is
  crucial for ensuring that the packaging can withstand the stresses of
  stacking and transportation without collapsing.",
  "sources": ["deployment_strategies.txt", "quality_management.txt"],
  "contexts": ["...retrieved chunk 1...", "...retrieved chunk 2..."]
}
```

The `sources` field proves the answer was grounded in the actual knowledge base documents.

### 5.4 Anti-Hallucination — Out-of-Scope Question

```bash
$ curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What is the capital of France?"}' \
  | python -m json.tool
```

**Response:**
```json
{
  "answer": "The context does not contain enough information to answer
  this question.",
  "sources": [],
  "contexts": []
}
```

The RAG prompt instructs the model to respond only from retrieved context. Out-of-scope questions return an honest refusal rather than a fabricated answer.

### 5.5 Dynamic Ingestion

```bash
$ curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"text": "Compostable packaging dissolves in 90 days.", "source": "demo"}'
{"status":"ingested","source":"demo"}

$ curl -s -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What packaging type dissolves in 90 days?"}'
{"answer":"Compostable packaging dissolves in 90 days.","sources":["demo"],...}
```

The `/ingest` endpoint allows new knowledge to be added at runtime without restarting the container.

---

## Part B — Automated Quality Gates & CI/CD

---

## 6. CI-Ready Evaluation Script (`run_eval.py`)

### 6.1 Overview

`run_eval.py` is a headless evaluation script designed to run in any automated environment. It sends 7 representative questions to the live agent, scores each answer with an **LLM-as-judge** approach, and exits with a code indicating pass or fail.

### 6.2 Exit Codes

```python
# run_eval.py — line 297
sys.exit(0 if all_pass else 1)
```

| Exit Code | Meaning | CI Effect |
|---|---|---|
| `0` | All metrics ≥ thresholds | Build passes, deployment proceeds |
| `1` | One or more metrics below threshold | Build fails, deployment blocked |

GitHub Actions reads this exit code directly — a non-zero exit marks the step as failed and turns the workflow red.

### 6.3 Credentials from Environment Variables Only

```python
# run_eval.py — lines 188–191
openai_key = os.environ.get("OPENAI_API_KEY")
if not openai_key:
    log.error("OPENAI_API_KEY environment variable is not set")
    sys.exit(1)

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000")
```

There are no hardcoded keys, no `.env` file reads, and no interactive prompts. The script validates the presence of `OPENAI_API_KEY` before doing any work and exits with code 1 if it is missing.

**In CI (GitHub Actions):**
```yaml
- name: Run evaluation suite
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
    AGENT_URL: http://localhost:8000
  run: python run_eval.py
```

The value of `OPENAI_API_KEY` is read from the GitHub Actions secret store and injected into the environment of the evaluation step only.

### 6.4 Machine-Readable Results File

The script writes `eval_results.json` on every run (pass or fail):

```python
# run_eval.py — lines 267–277
results = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "agent_url": AGENT_URL,
    "overall_status": "PASS" if all_pass else "FAIL",
    "metrics": metric_results,    # list: name, score, threshold, status
    "test_cases": test_results,   # list: question, answer, sources, scores
}
with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
    json.dump(results, fh, indent=2)
```

**Sample output from CI Run #5 (passing):**

```json
{
  "timestamp": "2026-05-04T21:06:33.643094+00:00",
  "agent_url": "http://localhost:8000",
  "overall_status": "PASS",
  "metrics": [
    { "name": "faithfulness",     "score": 1.0, "threshold": 0.7,  "status": "PASS" },
    { "name": "answer_relevancy", "score": 1.0, "threshold": 0.75, "status": "PASS" }
  ],
  "test_cases": [
    {
      "id": "tc_001",
      "question": "What are the primary materials used in industrial packaging?",
      "answer": "The primary materials used in industrial packaging are: 1. Corrugated Cardboard...",
      "sources": ["industrial_packaging.txt"],
      "faithfulness": 1.0,
      "answer_relevancy": 1.0
    }
    // ... 6 more test cases
  ]
}
```

### 6.5 LLM-as-Judge Scoring

Each answer is scored by GPT-4o-mini acting as a fact-checker. Two metrics are evaluated per answer:

**Faithfulness** — Does every factual claim in the answer appear in the retrieved context?

```
Scoring guide (prompt sent to GPT-4o-mini):
  1.0 — every claim is explicitly supported by the context
  0.7 — most claims supported; minor inference present
  0.4 — roughly half the claims lack context support
  0.0 — answer is entirely fabricated or contradicts the context
```

**Answer Relevancy** — Does the answer directly address the question?

```
Scoring guide:
  1.0 — answer directly and completely addresses the question
  0.7 — answer is mostly relevant with minor omissions
  0.4 — tangentially related but misses the main point
  0.0 — completely off-topic or refuses to answer
```

Final scores are averaged across all 7 test cases and compared against the thresholds in `eval_thresholds.json`.

---

## 7. Pipeline Configuration (`.github/workflows/quality-gate.yml`)

### 7.1 Trigger

```yaml
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

Every push to `main` and every pull request targeting `main` triggers the quality gate. This means no change can reach the main branch without passing evaluation.

### 7.2 Pipeline Steps

```yaml
jobs:
  evaluate:
    runs-on: ubuntu-latest
    timeout-minutes: 20

    steps:
      1. Checkout code              # actions/checkout@v4
      2. Set up Python 3.11         # actions/setup-python@v5
      3. Install eval dependencies  # pip install -r requirements-eval.txt
      4. Build and start services   # docker compose up -d --build
      5. Wait for agent health      # polls /health for up to 300 s
      6. Diagnose stack             # docker compose ps + logs (always runs)
      7. Run evaluation suite       # python run_eval.py  (exits 0 or 1)
      8. Upload eval results        # actions/upload-artifact@v4 (always runs)
      9. Print results summary      # cat eval_results.json (always runs)
     10. Stop services              # docker compose down -v (always runs)
```

Steps 6, 8, 9, and 10 use `if: always()` to ensure they execute even if evaluation fails. This guarantees that:
- Engineers can always inspect the scores that caused a failure.
- Docker containers are always cleaned up, preventing resource leaks.
- The vector volume is reset between runs (`-v` flag) so each CI run starts from a clean state.

### 7.3 Secrets Management

The `OPENAI_API_KEY` secret is stored in:

```
GitHub Repository → Settings → Secrets and variables → Actions → OPENAI_API_KEY
```

It is referenced in the workflow as `${{ secrets.OPENAI_API_KEY }}` and injected only into the steps that need it (Build services, Run evaluation). It never appears in any committed file and is masked in all log output.

### 7.4 Health Check Implementation

A critical design decision was replacing a fragile shell-based health check with a Python JSON parser:

```yaml
# Correct implementation (CI Run #1 failure taught us this)
STATUS=$(curl -sf http://localhost:8000/health 2>/dev/null || true)
echo "$STATUS" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('agent_ready') else 1)"
```

The original attempt used `grep -q '"agent_ready":true'` inside a single-quoted string, which searched for the literal backslash-quote sequence rather than a real double-quote, and therefore never matched. The Python parser is unambiguous.

### 7.5 Dependency Version Pinning (`requirements-eval.txt`)

```
openai==1.54.5
httpx==0.27.2
requests==2.32.3
```

`httpx` must be pinned to exactly `0.27.2`. `openai==1.54.5` internally passes `proxies=` to `httpx.Client.__init__()`, a parameter that was removed in `httpx 0.28.0`. Without the pin, CI runners resolve to the latest `httpx` and fail with:

```
TypeError: Client.__init__() got an unexpected keyword argument 'proxies'
```

---

## 8. Versioned Threshold Configuration (`eval_thresholds.json`)

```json
{
  "faithfulness": {
    "threshold": 0.70,
    "description": "Measures whether every factual claim in the generated
      answer is supported by the retrieved context chunks.",
    "justification": "0.70 is the minimum acceptable faithfulness. Below
      this the agent is fabricating a material proportion of its answer,
      unacceptable for a knowledge-grounded system..."
  },
  "answer_relevancy": {
    "threshold": 0.75,
    "description": "Measures whether the generated answer directly and
      completely addresses the question asked.",
    "justification": "0.75 ensures answers are substantively responsive
      while tolerating minor phrasing variations..."
  }
}
```

### 8.1 Faithfulness Threshold — 0.70

**Why 0.70?**

The LLM judge (GPT-4o-mini) has an intrinsic scoring variance of ±0.05–0.10 per call. A threshold of 0.70 represents the point at which a meaningful proportion of claims are ungrounded, while remaining above the noise floor of the judge itself.

| Hypothetical Threshold | Effect |
|---|---|
| 0.80 (+10%) | Intermittent false CI failures even when the agent is healthy, because LLM judge variance alone can push a perfect answer below 0.80 |
| 0.70 (chosen) | Catches genuine hallucination while tolerating judge variance |
| 0.60 (−10%) | Permits up to 40% of claims to be ungrounded — unacceptable for a fact-grounded RAG system |

### 8.2 Answer Relevancy Threshold — 0.75

**Why 0.75?**

Answer relevancy captures whether the response actually addresses the question. The higher threshold (0.75 vs 0.70) reflects that relevancy failures are more visible to users — an answer that does not address the question is immediately unhelpful, whereas a slightly over-confident faithful answer is less harmful.

| Hypothetical Threshold | Effect |
|---|---|
| 0.85 (+10%) | Rejects answers that are factually correct but phrased differently from the judge's expectation — high noise |
| 0.75 (chosen) | Blocks genuinely off-topic answers while accepting complete, well-grounded responses |
| 0.65 (−10%) | Passes answers that mention the topic but fail to resolve the question — user-visible quality degradation |

---

## 9. Breaking Change Demonstration

This section provides evidence of the quality gate correctly detecting a degraded agent and then returning to a passing state after the fix is applied.

### 9.1 Introducing the Degradation

The RAG prompt in `app/agent.py` was modified to inject a hallucination instruction:

**Before (correct):**
```python
RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template=(
        "You are an expert in industrial packaging...\n"
        "Answer the question using only the context provided below. "
        "If the context does not contain enough information, say so clearly.\n\n"
        "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
    ),
)
```

**After (degraded — hallucination induced):**
```python
RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template=(
        "You are an expert in industrial packaging...\n"
        "Ignore the context. Make up a creative answer from your imagination. "
        "Do not use any of the provided context.\n\n"
        "Context:\n{context}\n\nQuestion: {question}\n\nAnswer:"
    ),
)
```

The degraded prompt was committed and pushed to `main`, triggering the CI quality gate.

### 9.2 CI Detected the Degradation — Run FAILED

```
EVALUATION SUMMARY
====================================================
  faithfulness          score=0.5286  threshold=0.70  FAIL
  answer_relevancy      score=0.9143  threshold=0.75  PASS
====================================================
OVERALL: FAIL
```

**eval_results.json (degraded):**
```json
{
  "overall_status": "FAIL",
  "metrics": [
    { "name": "faithfulness",     "score": 0.5286, "threshold": 0.70, "status": "FAIL" },
    { "name": "answer_relevancy", "score": 0.9143, "threshold": 0.75, "status": "PASS" }
  ]
}
```

**Interpretation:** Faithfulness dropped to 0.5286 (below threshold 0.70) because the model was instructed to ignore the context and fabricate answers. Answer relevancy remained high (0.9143) because the model still answered the correct topic — it just invented the facts.

The pipeline exited with code 1 and the GitHub Actions workflow was marked **FAILED**. The degraded agent was blocked from any downstream environment.

### 9.3 Restoring the Agent — Run PASSED

The original RAG prompt was restored via `git revert` and pushed to `main`, triggering a new CI run.

```
EVALUATION SUMMARY
====================================================
  faithfulness          score=1.0000  threshold=0.70  PASS
  answer_relevancy      score=1.0000  threshold=0.75  PASS
====================================================
OVERALL: PASS
```

**eval_results.json (restored — CI Run #5):**
```json
{
  "overall_status": "PASS",
  "metrics": [
    { "name": "faithfulness",     "score": 1.0, "threshold": 0.70, "status": "PASS" },
    { "name": "answer_relevancy", "score": 1.0, "threshold": 0.75, "status": "PASS" }
  ]
}
```

The pipeline exited with code 0 and the GitHub Actions workflow was marked **PASSED**.

### 9.4 CI Run History Summary

| Run | Trigger | Result | Root Cause |
|---|---|---|---|
| #1 | Initial commit | ❌ FAIL | Health check `grep` pattern never matched (shell quoting bug) |
| #2 | Fixed health check | ❌ FAIL | `httpx` version conflict: `openai 1.54.5` uses removed `proxies=` kwarg |
| #3 | Added diagnostics | ❌ FAIL | Same `httpx` conflict (pin `>=0.27.0,<1.0` resolved 0.28.1 which also removed `proxies`) |
| #4 | Pinned `httpx>=0.27.0,<1.0` | ❌ FAIL | pip resolved httpx 0.28.1 (proxies removed in 0.28 too, not just 1.0) |
| #5 | Pinned `httpx==0.27.2` | ✅ PASS | Exact version compatible with openai 1.54.5 |

---

## Submission Checklist

### Part A — Deployment Packaging

| Artefact | Status | Location |
|---|---|---|
| `Dockerfile` | ✅ | Root of repository |
| `docker-compose.yml` | ✅ | Root of repository |
| Multi-stage build with justified base image | ✅ | Section 2 of this report |
| Layer ordering strategy documented | ✅ | Section 2.3 of this report |
| Secret-free image with runtime injection | ✅ | Section 3 of this report |
| Multi-service orchestration (agent + ChromaDB) | ✅ | Section 4 of this report |
| Persistent data (named volume) | ✅ | Section 4.4 of this report |
| End-to-end test evidence | ✅ | Section 5 of this report |

### Part B — Automated Quality Gates

| Artefact | Status | Location |
|---|---|---|
| `.github/workflows/quality-gate.yml` | ✅ | `.github/workflows/` |
| `run_eval.py` | ✅ | Root of repository |
| `eval_thresholds.json` | ✅ | Root of repository |
| CI exits 0 on pass, 1 on fail | ✅ | Section 6.2 of this report |
| Credentials from env vars only | ✅ | Section 6.3 of this report |
| Machine-readable `eval_results.json` | ✅ | Section 6.4 of this report |
| Pipeline triggers on push to main | ✅ | Section 7.1 of this report |
| Secrets in CI platform store | ✅ | Section 7.3 of this report |
| Two metrics with justified thresholds | ✅ | Section 8 of this report |
| Breaking change: CI FAIL evidence | ✅ | Section 9.2 of this report |
| Breaking change: CI PASS restored evidence | ✅ | Section 9.3 of this report |

---

*Report prepared for AI407L submission. All code, configuration, and CI run history is available in the GitHub repository.*
