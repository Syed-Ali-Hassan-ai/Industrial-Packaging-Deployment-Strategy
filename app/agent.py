import time
import logging
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate

logger = logging.getLogger(__name__)

RAG_PROMPT = PromptTemplate(
    input_variables=["context", "question"],
    template=(
        "You are an expert in industrial packaging, deployment strategies, and quality management.\n"
        "Answer the question using only the context provided below. "
        "If the context does not contain enough information, say so clearly.\n\n"
        "Context:\n{context}\n\n"
        "Question: {question}\n\n"
        "Answer:"
    ),
)


class RAGAgent:
    def __init__(self, settings):
        self.settings = settings
        self.chroma_client = self._connect_chroma()
        self.embeddings = OpenAIEmbeddings(
            openai_api_key=settings.openai_api_key,
            model="text-embedding-3-small",
        )
        self.llm = ChatOpenAI(
            openai_api_key=settings.openai_api_key,
            model="gpt-4o-mini",
            temperature=0,
        )
        self.vectorstore = Chroma(
            client=self.chroma_client,
            collection_name=settings.collection_name,
            embedding_function=self.embeddings,
        )
        self.chain = RetrievalQA.from_chain_type(
            llm=self.llm,
            chain_type="stuff",
            retriever=self.vectorstore.as_retriever(search_kwargs={"k": 4}),
            return_source_documents=True,
            chain_type_kwargs={"prompt": RAG_PROMPT},
        )

    def _connect_chroma(self, max_retries: int = 15) -> chromadb.ClientAPI:
        if self.settings.chroma_mode == "embedded":
            logger.info("Using embedded ChromaDB (local dev mode)")
            return chromadb.PersistentClient(path=".chroma_local")

        for attempt in range(1, max_retries + 1):
            try:
                client = chromadb.HttpClient(
                    host=self.settings.chroma_host,
                    port=self.settings.chroma_port,
                )
                client.heartbeat()
                logger.info(
                    "Connected to ChromaDB at %s:%d",
                    self.settings.chroma_host,
                    self.settings.chroma_port,
                )
                return client
            except Exception as exc:
                logger.warning(
                    "ChromaDB not ready (attempt %d/%d): %s", attempt, max_retries, exc
                )
                time.sleep(3)
        raise RuntimeError(
            f"Could not connect to ChromaDB at {self.settings.chroma_host}:{self.settings.chroma_port}"
        )

    def ingest_default_documents(self) -> None:
        collection = self.chroma_client.get_or_create_collection(
            self.settings.collection_name
        )
        if collection.count() > 0:
            logger.info(
                "Collection '%s' already has %d chunks — skipping ingestion",
                self.settings.collection_name,
                collection.count(),
            )
            return

        data_dir = Path(self.settings.data_dir)
        if not data_dir.exists():
            logger.warning("Data directory '%s' not found — no documents ingested", data_dir)
            return

        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=60)
        docs = []
        for txt_file in sorted(data_dir.glob("*.txt")):
            text = txt_file.read_text(encoding="utf-8")
            chunks = splitter.create_documents(
                [text], metadatas=[{"source": txt_file.name}]
            )
            docs.extend(chunks)
            logger.info("Prepared %d chunks from %s", len(chunks), txt_file.name)

        if docs:
            self.vectorstore.add_documents(docs)
            logger.info("Ingested %d total chunks into collection '%s'", len(docs), self.settings.collection_name)

    def query(self, question: str) -> dict:
        result = self.chain.invoke({"query": question})
        source_docs = result.get("source_documents", [])
        return {
            "answer": result["result"],
            "sources": sorted({doc.metadata.get("source", "") for doc in source_docs}),
            "contexts": [doc.page_content for doc in source_docs],
        }
