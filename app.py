import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openai import OpenAI
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from starlette.requests import Request

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str = "") -> str:
    value = os.getenv(name, default)
    return value.strip()


OPENAI_BASE_URL = env("OPENAI_BASE_URL")
OPENAI_API_KEY = env("OPENAI_API_KEY")
CHAT_MODEL = env("CHAT_MODEL", "gpt-4o")
EMBEDDING_MODEL = env("EMBEDDING_MODEL", "text-embedding-ada-002")


client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL) if OPENAI_API_KEY and OPENAI_BASE_URL else None


@dataclass
class Chunk:
    chunk_id: int
    text: str
    expansion_terms: list[str] = field(default_factory=list)

    @property
    def expanded_text(self) -> str:
        if not self.expansion_terms:
            return self.text
        return f"{self.text}\n\nExpansion terms: {' '.join(self.expansion_terms)}"


@dataclass
class SIRAIndex:
    document_name: str = ""
    raw_text: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    tokenized_expanded_chunks: list[list[str]] = field(default_factory=list)
    bm25: BM25Okapi | None = None
    ready: bool = False


class ChatRequest(BaseModel):
    message: str
    top_k: int = 4


class SIRAEngine:
    def __init__(self) -> None:
        self.index = SIRAIndex()

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-zA-Z0-9_\-]+", text.lower())

    def _chunk_text(self, text: str, chunk_words: int = 220, overlap_words: int = 40) -> list[str]:
        words = text.split()
        if not words:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = min(start + chunk_words, len(words))
            chunk = " ".join(words[start:end]).strip()
            if chunk:
                chunks.append(chunk)
            if end == len(words):
                break
            start = max(0, end - overlap_words)
        return chunks

    def _extract_text(self, file_path: Path) -> str:
        suffix = file_path.suffix.lower()

        if suffix in {".txt", ".md"}:
            return file_path.read_text(encoding="utf-8", errors="ignore")

        if suffix == ".pdf":
            if PdfReader is None:
                raise HTTPException(status_code=500, detail="pypdf is not installed. Install dependencies first.")
            reader = PdfReader(str(file_path))
            pages = []
            for page in reader.pages:
                pages.append(page.extract_text() or "")
            return "\n".join(pages)

        raise HTTPException(status_code=400, detail="Unsupported file format. Upload PDF, TXT, or MD.")

    def _llm_json_list(self, system_prompt: str, user_prompt: str, max_terms: int = 8) -> list[str]:
        if client is None:
            return []

        response = client.chat.completions.create(
            model=CHAT_MODEL,
            temperature=0.0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content or "{}"
        parsed = json.loads(content)
        terms = parsed.get("terms", [])
        if not isinstance(terms, list):
            return []

        clean = []
        for term in terms:
            if isinstance(term, str):
                value = term.strip().lower()
                if value and value not in clean:
                    clean.append(value)
            if len(clean) >= max_terms:
                break
        return clean

    def _expand_chunk_terms(self, chunk_text: str) -> list[str]:
        system_prompt = (
            "You generate lexical expansion terms for retrieval. "
            "Return strict JSON with key 'terms': array of short terms/phrases. "
            "No explanations."
        )
        user_prompt = (
            "Generate up to 8 expansion terms useful for BM25 retrieval for this chunk. "
            "Include aliases, related domain terms, and alternative phrasing.\n\n"
            f"Chunk:\n{chunk_text[:2500]}"
        )
        try:
            return self._llm_json_list(system_prompt, user_prompt, max_terms=8)
        except Exception:
            return []

    def _expected_response_sketch(self, query: str) -> list[str]:
        system_prompt = (
            "You create expected response sketches for retrieval. "
            "Return strict JSON with key 'terms' as an array of concise lexical terms likely in ideal answer passages."
        )
        user_prompt = (
            "Given the user question, output up to 10 retrieval-oriented terms likely to appear in good evidence passages.\n\n"
            f"Question: {query}"
        )
        try:
            return self._llm_json_list(system_prompt, user_prompt, max_terms=10)
        except Exception:
            return []

    def build_index(self, file_name: str, file_path: Path) -> dict[str, Any]:
        started = time.perf_counter()
        raw_text = self._extract_text(file_path)
        if not raw_text.strip():
            raise HTTPException(status_code=400, detail="The uploaded file appears empty.")

        chunk_texts = self._chunk_text(raw_text)
        if not chunk_texts:
            raise HTTPException(status_code=400, detail="Could not create chunks from the uploaded file.")

        chunks: list[Chunk] = []
        tokenized: list[list[str]] = []

        for idx, chunk_text in enumerate(chunk_texts, start=1):
            expansion_terms = self._expand_chunk_terms(chunk_text)
            chunk = Chunk(chunk_id=idx, text=chunk_text, expansion_terms=expansion_terms)
            chunks.append(chunk)
            tokenized.append(self._tokenize(chunk.expanded_text))

        bm25 = BM25Okapi(tokenized)

        self.index = SIRAIndex(
            document_name=file_name,
            raw_text=raw_text,
            chunks=chunks,
            tokenized_expanded_chunks=tokenized,
            bm25=bm25,
            ready=True,
        )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "document_name": file_name,
            "chunk_count": len(chunks),
            "elapsed_ms": elapsed_ms,
            "embedding_model_configured": bool(EMBEDDING_MODEL),
        }

    def chat(self, query: str, top_k: int = 4) -> dict[str, Any]:
        if not self.index.ready or self.index.bm25 is None:
            raise HTTPException(status_code=400, detail="No indexed document found. Upload a document first.")

        stages: list[dict[str, Any]] = []

        t0 = time.perf_counter()
        sketch_terms = self._expected_response_sketch(query)
        stages.append(
            {
                "name": "Expected Response Sketch",
                "details": "Predicted lexical terms likely to appear in high-quality answer passages.",
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "meta": {"sketch_terms": sketch_terms},
            }
        )

        t1 = time.perf_counter()
        fused_query = query.strip()
        if sketch_terms:
            fused_query = f"{fused_query} {' '.join(sketch_terms)}"
        fused_tokens = self._tokenize(fused_query)
        stages.append(
            {
                "name": "Query Fusion",
                "details": "Merged user query with expected-response sketch terms.",
                "duration_ms": int((time.perf_counter() - t1) * 1000),
                "meta": {"fused_query": fused_query},
            }
        )

        t2 = time.perf_counter()
        scores = self.index.bm25.get_scores(fused_tokens)
        scored_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        top_indices = scored_indices[: max(1, min(top_k, 8))]

        retrieved = []
        context_blocks = []
        for rank, i in enumerate(top_indices, start=1):
            chunk = self.index.chunks[i]
            snippet = chunk.text[:450].strip()
            context_blocks.append(f"[Chunk {chunk.chunk_id}]\n{chunk.text}")
            retrieved.append(
                {
                    "rank": rank,
                    "chunk_id": chunk.chunk_id,
                    "score": float(scores[i]),
                    "snippet": snippet,
                    "expansion_terms": chunk.expansion_terms,
                }
            )

        stages.append(
            {
                "name": "One-shot BM25 Retrieval",
                "details": "Single lexical retrieval pass over expanded chunks.",
                "duration_ms": int((time.perf_counter() - t2) * 1000),
                "meta": {"top_k": len(retrieved)},
            }
        )

        t3 = time.perf_counter()
        answer = self._synthesize_answer(query=query, contexts=context_blocks)
        stages.append(
            {
                "name": "Answer Synthesis",
                "details": "Generated final response grounded in retrieved chunks.",
                "duration_ms": int((time.perf_counter() - t3) * 1000),
                "meta": {},
            }
        )

        return {
            "answer": answer,
            "stages": stages,
            "retrieved": retrieved,
            "document_name": self.index.document_name,
        }

    def _synthesize_answer(self, query: str, contexts: list[str]) -> str:
        if client is None:
            fallback = "\n\n".join(contexts[:2])
            return (
                "API configuration missing, so this is a retrieval-only preview. "
                "Set OPENAI_BASE_URL and OPENAI_API_KEY to enable LLM answers.\n\n"
                f"Best matching evidence:\n{fallback[:2200]}"
            )

        prompt = (
            "You are a document QA assistant. Use only the provided evidence chunks. "
            "If the answer is not present, say so clearly. Cite chunk IDs like [Chunk 3].\n\n"
            f"User question: {query}\n\n"
            f"Evidence:\n{'\n\n'.join(contexts)}"
        )

        try:
            response = client.chat.completions.create(
                model=CHAT_MODEL,
                temperature=0.2,
                messages=[
                    {"role": "system", "content": "Answer with concise, evidence-grounded explanations."},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content or "No answer returned by the model."
        except Exception as exc:
            return f"The model call failed: {exc}"


engine = SIRAEngine()

app = FastAPI(title="SIRA Chatbot Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "chat_model": CHAT_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "base_url": OPENAI_BASE_URL,
        },
    )


@app.post("/api/upload")
async def upload_document(file: UploadFile = File(...)) -> Any:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Invalid file name.")

    safe_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    file_path = UPLOAD_DIR / safe_name

    content = await file.read()
    file_path.write_bytes(content)

    result = engine.build_index(file_name=safe_name, file_path=file_path)
    return {
        "ok": True,
        "message": "Document uploaded and indexed with SIRA-style expansion.",
        "index": result,
    }


@app.post("/api/chat")
def chat(payload: ChatRequest) -> Any:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    result = engine.chat(query=payload.message, top_k=payload.top_k)
    return {"ok": True, **result}


@app.get("/api/health")
def health() -> Any:
    return {
        "ok": True,
        "index_ready": engine.index.ready,
        "document_name": engine.index.document_name,
        "chat_model": CHAT_MODEL,
        "embedding_model": EMBEDDING_MODEL,
        "base_url": OPENAI_BASE_URL,
    }
