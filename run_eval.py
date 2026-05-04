#!/usr/bin/env python3
"""
CI-ready evaluation script — Industrial Packaging RAG Agent
============================================================
Runs an LLM-as-judge evaluation against a live agent instance and writes a
machine-readable results file (eval_results.json).

Exit codes:
  0  All metrics met their thresholds  → CI passes, deployment proceeds
  1  One or more metrics below threshold → CI fails, deployment blocked

Environment variables (all read from env, never hardcoded):
  OPENAI_API_KEY   OpenAI key used for the LLM judge
  AGENT_URL        Base URL of the running agent  (default: http://localhost:8000)

Usage:
  python run_eval.py
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests
from openai import OpenAI

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8000")
RESULTS_FILE = "eval_results.json"
THRESHOLDS_FILE = "eval_thresholds.json"

# ── Test dataset ──────────────────────────────────────────────────────────────
# Each entry is a question the agent must answer from its knowledge base.
# Questions are selected to cover all three knowledge-base documents.
TEST_CASES = [
    {
        "id": "tc_001",
        "question": "What are the primary materials used in industrial packaging?",
    },
    {
        "id": "tc_002",
        "question": "How does vacuum packaging extend product shelf life?",
    },
    {
        "id": "tc_003",
        "question": "What ISO standards apply to industrial packaging quality management?",
    },
    {
        "id": "tc_004",
        "question": "What is the difference between primary, secondary, and tertiary packaging?",
    },
    {
        "id": "tc_005",
        "question": "How should chemical hazardous materials be packaged for transport?",
    },
    {
        "id": "tc_006",
        "question": "What is the Edge Crush Test and why is it important for corrugated packaging?",
    },
    {
        "id": "tc_007",
        "question": "What is a just-in-time packaging deployment strategy?",
    },
]


# ── Helper functions ──────────────────────────────────────────────────────────

def load_thresholds() -> dict[str, Any]:
    with open(THRESHOLDS_FILE, encoding="utf-8") as fh:
        return json.load(fh)


def wait_for_agent(url: str, timeout_seconds: int = 180) -> bool:
    """Poll /health until agent_ready is true or timeout is reached."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            resp = requests.get(f"{url}/health", timeout=5)
            if resp.status_code == 200 and resp.json().get("agent_ready"):
                log.info("Agent at %s is ready", url)
                return True
        except Exception:
            pass
        log.info("Agent not ready yet — retrying in 5 s …")
        time.sleep(5)
    return False


def query_agent(url: str, question: str) -> dict[str, Any]:
    resp = requests.post(
        f"{url}/query",
        json={"question": question},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def score_faithfulness(
    question: str,
    answer: str,
    contexts: list[str],
    client: OpenAI,
) -> float:
    """
    LLM-as-judge: does the answer use only information present in the context?
    Returns a float in [0, 1].
    """
    ctx_block = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    prompt = (
        "You are a strict fact-checker evaluating an AI-generated answer.\n\n"
        "Faithfulness = every factual claim in the answer is directly supported by "
        "the retrieved context. Unsupported or invented claims lower the score.\n\n"
        f"Context documents:\n{ctx_block}\n\n"
        f"Question: {question}\n"
        f"Answer: {answer}\n\n"
        "Scoring guide:\n"
        "  1.0 — every claim is explicitly supported by the context\n"
        "  0.7 — most claims are supported; minor inference present\n"
        "  0.4 — roughly half the claims lack context support\n"
        "  0.0 — answer is entirely fabricated or contradicts the context\n\n"
        "Reply with a single decimal number between 0.0 and 1.0, nothing else."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=10,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        log.warning("Could not parse faithfulness score '%s' — defaulting to 0.0", raw)
        return 0.0


def score_answer_relevancy(
    question: str,
    answer: str,
    client: OpenAI,
) -> float:
    """
    LLM-as-judge: does the answer directly address the question?
    Returns a float in [0, 1].
    """
    prompt = (
        "You are evaluating whether an AI answer is relevant and responsive to the question.\n\n"
        f"Question: {question}\n"
        f"Answer: {answer}\n\n"
        "Scoring guide:\n"
        "  1.0 — answer directly and completely addresses the question\n"
        "  0.7 — answer is mostly relevant with minor omissions\n"
        "  0.4 — answer is tangentially related but misses the main point\n"
        "  0.0 — answer is completely off-topic or refuses to answer\n\n"
        "Reply with a single decimal number between 0.0 and 1.0, nothing else."
    )
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=10,
    )
    raw = resp.choices[0].message.content.strip()
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        log.warning("Could not parse relevancy score '%s' — defaulting to 0.0", raw)
        return 0.0


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # 1. Validate environment
    openai_key = os.environ.get("OPENAI_API_KEY")
    if not openai_key:
        log.error("OPENAI_API_KEY environment variable is not set")
        sys.exit(1)

    thresholds = load_thresholds()
    openai_client = OpenAI(api_key=openai_key)

    # 2. Wait for agent
    log.info("Connecting to agent at %s …", AGENT_URL)
    if not wait_for_agent(AGENT_URL):
        log.error("Agent did not become ready within the timeout window")
        _write_failure_results(thresholds, "Agent health check timed out")
        sys.exit(1)

    # 3. Evaluate each test case
    faithfulness_scores: list[float] = []
    relevancy_scores: list[float] = []
    test_results: list[dict] = []

    for tc in TEST_CASES:
        log.info("[%s] %s", tc["id"], tc["question"][:70])
        try:
            agent_resp = query_agent(AGENT_URL, tc["question"])
            answer = agent_resp["answer"]
            contexts = agent_resp.get("contexts", [])

            f_score = score_faithfulness(tc["question"], answer, contexts, openai_client)
            r_score = score_answer_relevancy(tc["question"], answer, openai_client)

            faithfulness_scores.append(f_score)
            relevancy_scores.append(r_score)

            log.info(
                "  → faithfulness=%.3f  answer_relevancy=%.3f", f_score, r_score
            )
            test_results.append(
                {
                    "id": tc["id"],
                    "question": tc["question"],
                    "answer": answer,
                    "sources": agent_resp.get("sources", []),
                    "faithfulness": round(f_score, 4),
                    "answer_relevancy": round(r_score, 4),
                }
            )
        except Exception as exc:
            log.error("  ERROR evaluating %s: %s", tc["id"], exc)
            faithfulness_scores.append(0.0)
            relevancy_scores.append(0.0)
            test_results.append({"id": tc["id"], "error": str(exc)})

    # 4. Aggregate scores
    n = len(faithfulness_scores)
    avg_faithfulness = sum(faithfulness_scores) / n if n else 0.0
    avg_relevancy = sum(relevancy_scores) / n if n else 0.0

    faith_thresh = thresholds["faithfulness"]["threshold"]
    rel_thresh = thresholds["answer_relevancy"]["threshold"]

    faith_pass = avg_faithfulness >= faith_thresh
    rel_pass = avg_relevancy >= rel_thresh
    all_pass = faith_pass and rel_pass

    metric_results = [
        {
            "name": "faithfulness",
            "score": round(avg_faithfulness, 4),
            "threshold": faith_thresh,
            "status": "PASS" if faith_pass else "FAIL",
        },
        {
            "name": "answer_relevancy",
            "score": round(avg_relevancy, 4),
            "threshold": rel_thresh,
            "status": "PASS" if rel_pass else "FAIL",
        },
    ]

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_url": AGENT_URL,
        "overall_status": "PASS" if all_pass else "FAIL",
        "metrics": metric_results,
        "test_cases": test_results,
    }

    # 5. Write machine-readable results file
    with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    log.info("Results written to %s", RESULTS_FILE)

    # 6. Print summary and exit
    separator = "=" * 52
    log.info(separator)
    log.info("EVALUATION SUMMARY")
    log.info(separator)
    for m in metric_results:
        mark = "PASS" if m["status"] == "PASS" else "FAIL"
        log.info(
            "  %-20s  score=%.4f  threshold=%.2f  %s",
            m["name"],
            m["score"],
            m["threshold"],
            mark,
        )
    log.info(separator)
    log.info("OVERALL: %s", results["overall_status"])

    sys.exit(0 if all_pass else 1)


def _write_failure_results(thresholds: dict, reason: str) -> None:
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_url": AGENT_URL,
        "overall_status": "FAIL",
        "error": reason,
        "metrics": [
            {
                "name": "faithfulness",
                "score": 0.0,
                "threshold": thresholds["faithfulness"]["threshold"],
                "status": "FAIL",
            },
            {
                "name": "answer_relevancy",
                "score": 0.0,
                "threshold": thresholds["answer_relevancy"]["threshold"],
                "status": "FAIL",
            },
        ],
        "test_cases": [],
    }
    with open(RESULTS_FILE, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)


if __name__ == "__main__":
    main()
