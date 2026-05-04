import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent import RAGAgent
from app.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

agent: RAGAgent | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    logger.info("Initialising RAG agent …")
    agent = RAGAgent(settings)
    agent.ingest_default_documents()
    logger.info("Agent ready")
    yield
    logger.info("Shutdown complete")


app = FastAPI(
    title="Industrial Packaging RAG Agent",
    description=(
        "AI-powered question-answering system grounded in industrial packaging, "
        "deployment strategy, and quality management knowledge."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Allow requests from the demo HTML opened as a local file (file://) or any
# other origin — required so demo.html can call the API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str

    model_config = {"json_schema_extra": {"example": {"question": "What materials are used in industrial packaging?"}}}


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]
    contexts: list[str]


class IngestRequest(BaseModel):
    text: str
    source: str = "manual"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", summary="Health check")
def health():
    return {"status": "ok", "agent_ready": agent is not None}


@app.post("/query", response_model=QueryResponse, summary="Query the RAG agent")
def query(request: QueryRequest):
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent is still initialising")
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question must not be empty")
    try:
        result = agent.query(request.question)
        return QueryResponse(**result)
    except Exception as exc:
        logger.exception("Query failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest", summary="Ingest a text document into the knowledge base")
def ingest(request: IngestRequest):
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent is still initialising")
    try:
        from langchain.schema import Document
        doc = Document(page_content=request.text, metadata={"source": request.source})
        agent.vectorstore.add_documents([doc])
        return {"status": "ingested", "source": request.source}
    except Exception as exc:
        logger.exception("Ingest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
