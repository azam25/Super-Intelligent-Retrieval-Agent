# SIRA-Based Chatbot Demo

A demo app inspired by Meta's SIRA retrieval design:

1. Offline-like document expansion on upload (aliases, related terms).
2. Expected response sketch at query time.
3. Query fusion.
4. One-shot BM25 retrieval.
5. Grounded answer synthesis.

## Features

- Upload PDF, TXT, or MD files.
- Build lexical BM25 index with expansion terms.
- Chat with document.
- Frontend timeline shows each processing stage and timing.
- Professional light-theme UI.

## Stack

- Backend: FastAPI
- Retrieval: BM25 (`rank-bm25`)
- LLM API: OpenAI-compatible (`openai` client)
- Frontend: HTML + CSS + JS

## Setup

1. Create a virtual environment and install dependencies:

```bash
cd sira-chatbot-demo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Configure environment variables:

```bash
cp .env.example .env
```

Update `.env` with your API key.

3. Start the app:

```bash
export $(grep -v '^#' .env | xargs)
uvicorn app:app --reload
```

4. Open:

- http://127.0.0.1:8000

## API Endpoints

- `POST /api/upload` - upload document and build index
- `POST /api/chat` - ask question against indexed document
- `GET /api/health` - health/config status

## Notes

- The `EMBEDDING_MODEL` config is displayed in UI for compatibility with your LLM server setup.
- Retrieval is BM25-based to align with SIRA one-shot lexical retrieval behavior.
- Uploaded files are stored in `uploads/`.
