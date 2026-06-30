# LLM Wiki

A personal knowledge base where an LLM agent reads your curated source
documents, extracts key information, and builds a living, interlinked wiki
of markdown files. Knowledge is compiled once and kept current — not
re-derived on every query.

This is the fundamental difference from RAG. RAG re-discovers knowledge from
raw documents at query time. LLM Wiki compiles knowledge into structured pages
that grow smarter with every source you add.

---

## How It Works

```
[ Your Source Documents ]  →  [ Agent ]  →  [ Wiki Pages ]  →  [ Your Questions ]
  raw articles, PDFs            reads          compiled              answered with
  blog posts, papers            writes         knowledge             citations
```

When you add a source, the agent reads it, extracts entities and key claims,
updates relevant wiki pages, flags contradictions with existing content, and
indexes everything for search. When you ask a question, it searches the compiled
wiki — not the raw documents — and synthesises an answer with citations.

---

## Requirements

- Docker (for Oracle AI Database)
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- An LLM API key (Anthropic recommended)

---

## Setup

**1. Clone the repository**

```bash
git clone github.com/DanielPopoola/llm-wiki
cd llm-wiki
```

**2. Start Oracle AI Database**

```bash
docker compose up -d
```

Wait until the container is ready (takes ~2 minutes):

```bash
docker logs -f llm-wiki-db | grep "DATABASE IS READY"
```

**3. Install dependencies**

```bash
uv sync
```

**4. Configure environment**

```bash
cp .env.example .env
```

Open `.env` and fill in your credentials. See `.env.example` for all
required values. Never commit `.env` — it is gitignored.

**5. Verify the setup**

```bash
uv run python scripts/verify.py
```

All four checks must pass before proceeding:
- ✅ Oracle DB: connected
- ✅ Embedding model: 768-dimensional vector returned
- ✅ Environment: no credentials in source code

---

## Usage

**Create a wiki project**

```bash
uv run llm-wiki new my-stocks-wiki
```

**Ingest a source document**

```bash
uv run llm-wiki ingest my-stocks-wiki ./sources/gtbank-q3-report.md
```

**Ask a question**

```bash
uv run llm-wiki query my-stocks-wiki "What is GTBank's loan growth trend?"
```

**Check wiki health**

```bash
uv run llm-wiki health my-stocks-wiki
```

**Run the lint workflow**

```bash
uv run llm-wiki lint my-stocks-wiki
```

**Run evaluation**

```bash
uv run llm-wiki eval my-stocks-wiki
```

---

## Project Structure

```
llm-wiki/
├── wiki/               # core library — pages, storage, embeddings, logs
├── workflows/          # LangGraph agent workflows — ingestion, query, lint, eval
├── cli/                # CLI entrypoint (Typer)
├── ui/                 # web interface (Step 8)
├── eval/               # golden sets and eval reports (committed to git)
├── wikis/              # your wiki projects on disk (gitignored)
├── scripts/            # utility scripts (verify.py, setup_db.py)
└── tests/              # mirrors source structure
```

The dependency direction is strictly: `cli/ui → workflows → wiki → external systems`.
Nothing in `wiki/` knows about the CLI or UI.

---

## Documentation

- [Technical Design Document](docs/TDD.md) — architecture, decisions, and tradeoffs
- [CHANGELOG](CHANGELOG.md) — notable changes per version

---

## Development

**Run tests**

```bash
uv run pytest
```

**Run a single test file**

```bash
uv run pytest tests/test_storage.py -v
```

Tests never use real API keys, real databases, or live LLM calls. All
external systems are mocked.
