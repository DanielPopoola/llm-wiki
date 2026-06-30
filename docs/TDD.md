# LLM Wiki — Technical Design Document

**Author:** AI Engineering  
**Status:** Draft v2 — Evaluation pillar added, open questions resolved  
**Last Updated:** 2026-06-15

---

## 1. Overview

LLM Wiki is a personal knowledge base where an LLM agent reads curated source
documents, extracts key information, and builds a living, interlinked wiki of
markdown files. Knowledge is compiled once and kept current — not re-derived on
every query.

This is the fundamental difference from RAG. RAG re-discovers knowledge from
raw documents at query time. LLM Wiki compiles knowledge into structured pages
that grow smarter with every source added.

---

## 2. Goals

- Ingest source documents and compile them into a structured, interlinked wiki
- Detect and surface contradictions between sources rather than silently
  overwriting claims
- Answer questions by searching compiled wiki pages with citations
- Keep the wiki consistent as it grows via a lint workflow
- Support multiple isolated wiki projects
- Persist all knowledge across sessions — files on disk, embeddings in Oracle DB

### Non-Goals

- Replacing a general-purpose search engine
- Providing financial or any domain-specific advice — the wiki organises what
  sources say, it does not make recommendations
- Real-time ingestion of live data feeds

---

## 3. System Architecture

### 3.1 The Three Layers

```
┌─────────────────────────────────────────┐
│           Source Documents              │  ← Read-only truth. Never modified.
│   raw articles, blog posts, papers      │    Dropped in by the user.
└────────────────────┬────────────────────┘
                     │ agent reads
                     ▼
┌─────────────────────────────────────────┐
│             Wiki Layer                  │  ← Agent-owned markdown files.
│  summaries / entities / topics / raw    │    Human-readable. Git-friendly.
│  index.md / log.md / SCHEMA.md          │    The compiled knowledge base.
│  log.ndjson  (internal crash recovery)  │
└────────────────────┬────────────────────┘
                     │ agent indexes
                     ▼
┌─────────────────────────────────────────┐
│          Oracle AI Database             │  ← Search infrastructure.
│  vector embeddings + full-text index    │    Page metadata + project registry.
│  + project metadata                     │    Persists across sessions.
└─────────────────────────────────────────┘
```

**Key invariant:** Source documents flow downward only. The agent reads sources,
writes wiki pages, and indexes those pages. Sources are never modified.

---

### 3.2 The Four System Components

```
┌────────────────────────────────────────────────────────────────────┐
│                        LangGraph Agent                             │
│                                                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ ┌──────────┐  │
│  │  Ingestion  │  │    Query    │  │    Lint     │ │   Eval   │  │
│  │  Workflow   │  │  Workflow   │  │  Workflow   │ │ Workflow │  │
│  └─────────────┘  └─────────────┘  └─────────────┘ └──────────┘  │
└──────────────┬───────────────┬─────────────────────────────────────┘
               │               │
               ▼               ▼
┌─────────────────┐   ┌──────────────────────────────┐
│  Wiki on Disk   │   │      Oracle AI Database       │
│                 │   │                               │
│  summaries/     │   │  - Page embeddings (768-dim)  │
│  entities/      │   │  - Full-text index            │
│  topics/        │   │  - Page metadata + hashes     │
│  raw/           │   │  - Project registry           │
│  index.md       │   │                               │
│  log.md         │   │                               │
│  log.ndjson     │   │                               │
│  SCHEMA.md      │   │                               │
└─────────────────┘   └──────────────────────────────┘
```

---

### 3.3 Technology Choices

| Technology | Role | Why |
|---|---|---|
| Oracle AI Database | Vector + full-text search | Single DB for both search modes; no need for a separate vector store |
| LangGraph | Agent workflow orchestration | State machine model handles branching, checkpointing, and resume natively |
| LangChain | LLM provider abstraction | Swap providers without rewriting workflow logic |
| qwen/qwen-32b | Generation LLM | Writes wiki pages, extracts entities, synthesises query answers |
| Claude Sonnet | Eval judge + golden set generation | Separate model family from generator eliminates self-evaluation bias; stronger at structured annotation |
| nomic-embed-text-v2-moe | Embedding model | Open source, Apache 2.0, runs locally on CPU, 768-dim vectors |
| Markdown files | Wiki storage format | Human-readable, Obsidian-compatible, git-friendly, agent can write them easily |
| uv | Python package management | Fast, reproducible, lock-file based |

---

## 4. Agent Workflows

Each workflow is a LangGraph state machine. Each node does exactly one thing
and passes state forward. This makes workflows inspectable, resumable, and
easy to reason about.

### 4.1 Ingestion Workflow

Triggered when a new source document is added.

```
Read Source
    │
    ▼
Hash Source ──► duplicate? ──► STOP (already ingested)
    │
    ▼
Extract Entities + Concepts
    │
    ▼
Write Summary Page  (summaries/)
    │
    ▼
Update Entity Pages  (entities/)
    │
    ▼
Update Topic Overview Pages  (topics/)
    │
    ▼
Flag Contradictions  (annotate conflicting pages)
    │
    ▼
Create Stub Pages  (for referenced but missing entities)
    │
    ▼
Update index.md
    │
    ▼
Append to log.md  (STARTED → COMPLETED)
    │
    ▼
Re-embed Changed Pages  (only pages touched by this ingestion)
```

A single source ingestion may touch 10-15 wiki pages. Each page write is an
LLM call, so prompts must be tight and purposeful.

**Crash recovery:** The log records `STARTED` at the beginning and `COMPLETED`
at the end. On startup, any ingestion with `STARTED` but no `COMPLETED` is
detected as incomplete. The agent can resume from the LangGraph checkpoint or
rollback to the pre-ingestion state. See Section 6.1.

---

### 4.2 Query Workflow

Triggered when the user asks a question.

```
Read index.md  (Steps 1-3 only: identify candidate pages before search exists)
    │
    ▼
Hybrid Search  (vector similarity + full-text in Oracle DB)
    │
    ▼
Read Top Candidate Pages  (full content, top 3-5 pages)
    │
    ▼
Synthesise Answer  (with citations to specific pages)
    │
    ▼
Offer to Save Answer  (optionally write as new wiki page)
```

**Note on index-first navigation:** reading `index.md` at the start of every
query is the Step 3 approach — it works at small scale and requires no
database. From Step 4 onwards, Oracle hybrid search replaces the index read
for retrieval. The index remains useful for humans browsing the wiki but is
no longer in the query hot path. At large scale, removing the index read from
the query flow entirely is the correct decision.

The query workflow carries conversation history in LangGraph state so follow-up
questions maintain context without restating the topic.

---

### 4.3 Lint Workflow

Triggered on demand. Interactive by default — no changes made without
confirmation.

```
Walk All Wiki Pages
    │
    ▼
Check Contradictions  (two pages make incompatible claims)
    │
    ▼
Check Stale Claims  (claim contradicted by newer source)
    │
    ▼
Find Orphan Pages  (no inbound links)
    │
    ▼
Find Broken Links  (wikilink points to non-existent page)
    │
    ▼
Identify Gaps  (concept discussed across pages but no dedicated page)
    │
    ▼
Suggest Research  (new questions to investigate, new sources to look for)
    │
    ▼
Present Prioritised Findings  (critical → warnings → suggestions → research)
    │
    ▼
Apply Confirmed Fixes  (user accepts/rejects each one)
    │
    ▼
Append to log.md
```

The "Suggest Research" node is what turns lint from a bug-finding exercise
into a research planning tool. The agent looks at gaps and thin areas and
asks: what sources would fill these gaps? What questions remain unanswered?
This output is presented as suggestions, never applied automatically.

---

### 4.4 Eval Workflow

Triggered on demand via `llm-wiki eval <project>`. Measures system quality
across three targets and writes a report to `eval/report.md`.

```
Load Golden Set  (eval/golden_ingestion.json + eval/golden_queries.json)
    │
    ▼
Run Ingestion on Golden Docs  (isolated test wiki, not the live wiki)
    │
    ▼
Score Ingestion Quality  (LLM-as-judge per golden doc)
    │
    ▼
Run Query on Test Questions  (against the test wiki)
    │
    ▼
Score Retrieval  (Recall@5, Precision@5 per question)
    │
    ▼
Score Answer Quality  (faithfulness + relevance via LLM-as-judge)
    │
    ▼
Write eval/report.md  (scores + regressions vs previous run)
    │
    ▼
Append to log.md
```

The eval workflow always runs against an **isolated test wiki**, never the live
wiki. This means eval is safe to run at any time without corrupting real data.



### 5.1 Wiki Directory Structure

```
wikis/
└── <project-name>/
    ├── SCHEMA.md          ← conventions: naming, frontmatter fields, link style
    ├── index.md           ← catalogue of all pages with one-line descriptions
    ├── log.md             ← human-readable chronological record of all operations
    │                         format: ## [YYYY-MM-DD] type | Description
    │                         grep-parseable: grep "^## \[" log.md | tail -5
    ├── log.ndjson         ← internal write-ahead log for crash recovery only
    │                         not for human consumption; not specified by task
    ├── raw/               ← original source files, never modified
    ├── summaries/         ← one page per ingested source
    ├── entities/          ← pages for people, companies, organisations
    └── topics/            ← overview pages connecting concepts

eval/                      ← lives at project root, not inside any wiki
    ├── golden_ingestion.json   ← Claude-generated + human-reviewed ground truth (10 docs)
    ├── golden_queries.json     ← Claude-generated + human-reviewed test questions (20-30)
    └── report.md               ← eval scores per run, tracked over time
```

**Two log files, two distinct purposes:**

`log.md` is task-specified. It is the human-readable operations record — what
the agent did, when, and to what. Every operation (ingest, query, lint, eval)
appends a `## [YYYY-MM-DD] type | Description` entry. It is the file the task
instructions explicitly describe and test against.

`log.ndjson` is our addition for crash recovery. It is the write-ahead log —
every file write during ingestion appended immediately as a structured JSON
event. It is not for human consumption; it exists so rollback recovery has a
durable record even if the process crashes mid-operation. See Section 7.1.

### 5.2 Page Frontmatter

Every wiki page (except index and log) carries YAML frontmatter:

```yaml
---
title: "GTBank Q3 2024 Earnings"
type: summary                        # summary | entity | topic
created: 2024-11-15
updated: 2024-11-15
tags: [banking, earnings, nigeria]
sources:
  - raw/gtbank-q3-2024.md
---
```

### 5.3 Cross-Reference Convention

Pages reference each other using wikilinks: `[[Entity Name]]`. The schema file
records this convention so the agent is consistent. Lint checks that every
wikilink resolves to an existing page.

### 5.4 Oracle Database Schema

```sql
-- One row per wiki page
CREATE TABLE wiki_pages (
    id           NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project      VARCHAR2(255)   NOT NULL,
    page_path    VARCHAR2(1000)  NOT NULL,
    title        VARCHAR2(500),
    page_type    VARCHAR2(50),          -- summary | entity | topic
    tags         VARCHAR2(1000),
    content_hash VARCHAR2(64),          -- SHA-256; used for change detection
    snippet      VARCHAR2(2000),        -- title + first 400 tokens; what gets embedded
    embedding    VECTOR(768),           -- nomic-embed-text output
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project, page_path)
);

-- Vector index for semantic similarity search
CREATE VECTOR INDEX idx_wiki_embedding
    ON wiki_pages (embedding)
    ORGANIZATION NEIGHBOR PARTITIONS
    WITH DISTANCE COSINE;

-- Full-text index for exact term and name search
CREATE INDEX idx_wiki_fulltext
    ON wiki_pages (title, snippet)
    INDEXTYPE IS CTXSYS.CONTEXT;

-- Project registry
CREATE TABLE wiki_projects (
    id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            VARCHAR2(255) UNIQUE NOT NULL,
    wiki_path       VARCHAR2(1000) NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_ingested   TIMESTAMP,
    page_count      NUMBER DEFAULT 0,
    source_count    NUMBER DEFAULT 0
);

-- Source registry — one row per ingested source document
-- Provides duplicate detection, ingestion history, and audit trail
-- in a queryable form rather than buried in logs
CREATE TABLE wiki_sources (
    id              NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project         VARCHAR2(255)   NOT NULL,
    source_path     VARCHAR2(1000)  NOT NULL,
    content_hash    VARCHAR2(64)    NOT NULL,   -- SHA-256; duplicate detection
    title           VARCHAR2(500),
    ingested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status          VARCHAR2(50)    NOT NULL,   -- completed | failed | rolled_back
    UNIQUE (project, content_hash)              -- same content never ingested twice
);
```

---

## 6. Evaluation

Evaluation is the mechanism that tells you whether the system is actually doing
a good job — and whether changes make it better or worse. Without it, prompt
changes, model swaps, and new source documents silently degrade quality with no
way to detect it.

**Model roles in evaluation:**

| Role | Model | Why |
|---|---|---|
| Generation | qwen/qwen-32b | Writes wiki pages, extracts entities, synthesises answers |
| Golden set generation | Claude Sonnet | Stronger at careful structured annotation; produces higher-quality ground truth |
| Eval judge | Claude Sonnet | Different model family from generator eliminates self-evaluation bias |

The judge and generator must be from different model families. If Qwen generates
wiki pages and Qwen also judges them, the judge shares the same blind spots and
tendencies as the generator — scores look good but don't reflect real quality.
Claude Sonnet as an independent judge from a different family gives an honest signal.

**Important:** Claude-generated golden data must be human-reviewed before being
locked in. Claude can produce plausible-looking but subtly wrong annotations,
especially for financial content where domain knowledge matters. The golden set
is only as trustworthy as the review process behind it.

There are three distinct targets to evaluate. Each needs a different approach.

---

### 6.1 Ingestion Quality — Did the Agent Extract the Right Things?

Measures the quality of what Qwen produces when it reads a source document.

**Golden set generation:**

Claude Sonnet generates the ground truth for 10 source documents from the test
corpus. For each document, Claude produces a structured annotation:

```json
{
  "source": "gtbank-q3-2024.md",
  "key_claims": [
    "GTBank reported loan growth of 12% in Q3 2024",
    "Non-performing loans declined to 3.2%",
    "Net interest margin compressed by 40 basis points"
  ],
  "entities": ["GTBank", "Segun Agbaje", "Nigerian Banking Sector"],
  "contradictions_with_existing_wiki": [
    {
      "existing_page": "entities/gtbank.md",
      "existing_claim": "GTBank loan growth was 8% as of Q2 2024",
      "new_claim": "GTBank loan growth is 12% as of Q3 2024",
      "nature": "updated figure, not a true contradiction"
    }
  ],
  "expected_page_structure": ["## Overview", "## Key Metrics", "## Sources"]
}
```

This file is committed to `eval/golden_ingestion.json` and human-reviewed
before any automated eval runs against it.

**What we measure and how:**

*Claim Coverage* — graded 1–5, discrete:

```
Task: Determine whether the generated summary page captures the key claims
from the source document.

Criteria: A key claim is any factual assertion in the source that a reader
would need to understand the document's main argument. Ignore stylistic
details, repeated points, and illustrative examples. Focus only on
substantive claims. Compare the generated summary against the provided
list of key claims from the ground truth annotation.

Scoring:
  5 — All key claims present, accurately represented
  4 — All key claims present, at least one slightly imprecise
  3 — Most key claims present (≥70%), no critical omissions
  2 — Some key claims present (<70%), at least one critical claim missing
  1 — Few or no key claims present, summary is misleading or empty
```

*Hallucination* — classification, not numerical. The question is binary:
either a sentence is supported by the source or it is not.

```
Task: Identify any claims in the generated summary that are not supported
by the source document.

Criteria: A hallucinated claim is any factual assertion in the summary
that cannot be directly verified from the source document text provided.
Do not penalise reasonable inferences or paraphrasing. Only flag statements
that introduce new facts, figures, names, or dates not present in the source.

Scoring:
  CLEAN    — every claim in the summary is traceable to the source
  MINOR    — one claim is unverifiable but does not materially mislead
  CRITICAL — one or more claims introduce facts that contradict or
              significantly extend beyond the source
```

MINOR and CRITICAL have different consequences — MINOR may be acceptable,
CRITICAL means the page must be rejected and rewritten.

*Entity Accuracy* — graded 1–5, discrete:

```
Task: Compare the extracted entities against the ground truth entity list
and score the extraction.

Criteria: An entity is correctly extracted if its name matches or is a
recognisable variation of a ground truth entity ("GTBank" and "Guaranty
Trust Bank" count as the same entity). An entity is spurious if it appears
in the extraction but not in the ground truth. A missed entity is one in
the ground truth absent from the extraction.

Scoring:
  5 — All ground truth entities extracted, zero spurious entities
  4 — All ground truth entities extracted, one or two spurious entities
  3 — One ground truth entity missed, few spurious entities
  2 — Multiple ground truth entities missed OR many spurious entities
  1 — Extraction bears little resemblance to ground truth
```

*Contradiction Detection* — classification, four classes because missed
and false positive are different failure modes with different consequences:

```
Task: Determine whether the agent correctly identified contradictions
between the new source and existing wiki content.

Criteria: A contradiction exists when two sources make mutually exclusive
factual claims about the same entity or event — two different figures for
the same metric, conflicting dates, or opposing descriptions of an outcome.
Differences in opinion, emphasis, or interpretation do not count as
contradictions. An updated figure (Q2 vs Q3) is not a contradiction.

Scoring:
  CORRECT    — agent identified all contradictions in the ground truth
                and flagged no false positives
  PARTIAL    — agent identified some but not all ground truth contradictions,
                OR flagged one false positive alongside correct detections
  MISSED     — agent identified no contradictions despite ground truth
                containing at least one
  FALSE_POS  — agent flagged contradictions where ground truth contains none
```

---

### 6.2 Retrieval Quality — Did Search Find the Right Pages?

Retrieval is the one target where we do **not** use LLM-as-judge. Recall@5
and Precision@5 are computed deterministically against the golden query set —
no LLM involved. Retrieval quality is a maths problem, not a judgement problem.

**Golden query set — generated by Claude Sonnet:**

After ingesting the test corpus, Claude Sonnet generates 20–30 questions with
expected pages and reference answers. Human-reviewed before use.

```json
{
  "question": "What did GTBank report about loan growth in Q3 2024?",
  "expected_pages": [
    "summaries/gtbank-q3-2024.md",
    "entities/gtbank.md"
  ],
  "reference_answer": "GTBank reported loan growth of 12% in Q3 2024..."
}
```

**Metrics:**

- **Recall@5** — of all pages that *should* be returned, what fraction
  appeared in the top 5 results? 1.0 = all expected pages found.
- **Precision@5** — of the top 5 results, what fraction were actually
  relevant? Low precision means irrelevant pages consuming context budget.

**Concrete example:**

```
Question: "What did GTBank report about loan growth in Q3?"
Expected: [summaries/gtbank-q3-2024.md, entities/gtbank.md]

Retrieved top-5:
  1. summaries/gtbank-q3-2024.md  ✅
  2. topics/nigerian-banking.md   ❌
  3. entities/access-bank.md      ❌
  4. entities/gtbank.md           ✅
  5. summaries/gtbank-q2-2024.md  ❌

Recall@5:    2/2 = 1.0  ✅ both expected pages found
Precision@5: 2/5 = 0.4  ⚠️  3 irrelevant pages in top 5
```

Precision@5 of 0.4 signals the hybrid search weighting needs tuning.

---

### 6.3 Answer Quality — Did the Query Workflow Produce a Good Answer?

Claude Sonnet judges the final synthesised answer Qwen produces.

*Faithfulness* — classification. The most critical metric.

```
Task: Determine whether every factual claim in the generated answer is
supported by the cited wiki pages provided.

Criteria: For each factual claim in the answer, check whether it can be
directly verified from the text of the cited pages. A claim is supported
if the cited page contains the same information, even if worded differently.
A claim is unsupported if it introduces information not present in any cited
page, regardless of whether that information is true in the real world.
Truth in the real world is irrelevant — only support from the cited pages
matters.

Scoring:
  FAITHFUL     — every claim in the answer is supported by a cited page
  MINOR_DRIFT  — one claim extends slightly beyond the cited pages but
                  does not introduce materially new information
  HALLUCINATED — one or more claims introduce facts not present in any
                  cited page
```

The instruction "truth in the real world is irrelevant" is essential. Without
it, Claude will use its own world knowledge to validate claims rather than
checking only against the cited pages — which defeats the purpose entirely.

*Relevance* — classification:

```
Task: Determine whether the generated answer addresses the question asked.

Criteria: The answer is relevant if it directly addresses the core intent
of the question. Do not penalise brevity — a short accurate answer is
preferable to a long meandering one. Partial relevance occurs when the
answer addresses the topic but misses the specific intent.

Scoring:
  RELEVANT          — answer directly and completely addresses the question
  PARTIALLY_RELEVANT — answer addresses the topic but misses specific intent
  IRRELEVANT        — answer does not address the question
```

*Citation Accuracy* — classification:

```
Task: For each citation in the generated answer, determine whether the
cited page actually supports the specific claim it is cited for.

Criteria: A citation is accurate if the cited page contains information
that directly supports the claim attributed to it. A citation is misleading
if the cited page is topically related but does not contain the specific
claim. A citation is wrong if the cited page is unrelated to the claim.

Scoring:
  ALL_ACCURATE  — every citation supports its specific claim
  SOME_ACCURATE — at least one citation is misleading or wrong
  NONE_ACCURATE — no citations support their specific claims
```

---

### 6.4 The Meta-Pattern Across All Judge Prompts

Every judge prompt follows the same three-part structure from Chip Huyen's
AI Engineering framework:

1. **Task** — what the judge is being asked to do, stated precisely
2. **Criteria** — explicit definitions of every ambiguous term. This is the
   most important part. "Key claim," "hallucinated," "contradiction,"
   "supported" — each is defined so the judge cannot interpret them
   differently across runs
3. **Scoring** — classification for binary/detect tasks; discrete 1–5 for
   genuinely graded tasks. Classification labels are semantic (CRITICAL,
   PARTIAL, FALSE_POS) not just numbers, so consuming code knows what
   action to take from the label alone

---

### 6.5 Eval Report

Every eval run appends results to `eval/report.md`:

```markdown
## [2024-11-15] Eval Run — qwen/qwen-32b, judge: claude-sonnet

### Ingestion Quality (10 golden docs)
| Metric                  | Result              |
|-------------------------|---------------------|
| Claim coverage (avg)    | 4.2 / 5             |
| Hallucination           | 8 CLEAN, 2 MINOR    |
| Entity accuracy (avg)   | 4.5 / 5             |
| Contradiction detection | 7 CORRECT, 2 PARTIAL, 1 MISSED |

### Retrieval Quality (25 questions)
| Metric      | Score |
|-------------|-------|
| Recall@5    | 0.84  |
| Precision@5 | 0.61  |

### Answer Quality (25 questions)
| Metric            | Result                          |
|-------------------|---------------------------------|
| Faithfulness      | 22 FAITHFUL, 2 MINOR_DRIFT, 1 HALLUCINATED |
| Relevance         | 23 RELEVANT, 2 PARTIALLY_RELEVANT |
| Citation accuracy | 20 ALL_ACCURATE, 5 SOME_ACCURATE |

### Regressions vs previous run
- Recall@5: 0.84 vs 0.79 ✅ improved
- Faithfulness: 1 HALLUCINATED vs 0 ⚠️ degraded — review prompt changes
```

If any metric degrades vs the previous run, the eval command exits with a
non-zero status so CI catches regressions automatically.

---

### 6.6 How Evaluation Connects to the Other Pillars

| Pillar | How eval enforces it |
|---|---|
| Reliability | Hallucination classification catches when Qwen stops being trustworthy |
| Scalability | Recall@5 degrades as wiki grows — eval surfaces this before users notice |
| Maintainability | Eval is the regression suite for the AI layer — a prompt change that produces a HALLUCINATED where there was none is caught before it ships, exactly as a unit test catches a logic regression |



---

## 7. Reliability

### 7.1 Crash Recovery

The ingestion workflow is the most vulnerable to partial failure. A crash
midway leaves the wiki in an inconsistent state — some pages written, some not,
index and embeddings out of sync.

**Detection:** The log is newline-delimited JSON (NDJSON) — one JSON object
per line, append-only. NDJSON is chosen over a single JSON array because you
can append a new entry with a single write without reading and rewriting the
entire file. It is also structured enough for code to query without a parser
and readable enough for a human to inspect directly.

Every file write during ingestion appends to `log.ndjson` immediately —
before anything else. This makes it a write-ahead log (WAL): the record of
what happened survives a crash even if the process did not.

There are two distinct cases a write can be: **creating** a new page, or
**modifying** an existing one. The WAL handles them differently:

```json
{"timestamp": "2024-11-15T09:15:01Z", "event": "ingest", "status": "started", "source": "gtbank-q3-2024.md", "thread_id": "abc123"}
{"timestamp": "2024-11-15T09:15:03Z", "event": "ingest", "status": "backup", "path": "entities/gtbank.md", "old_content": "# GTBank\n...", "thread_id": "abc123"}
{"timestamp": "2024-11-15T09:15:04Z", "event": "ingest", "status": "wrote", "path": "summaries/gtbank-q3-2024.md", "is_new": true, "thread_id": "abc123"}
{"timestamp": "2024-11-15T09:15:07Z", "event": "ingest", "status": "wrote", "path": "entities/gtbank.md", "is_new": false, "thread_id": "abc123"}
{"timestamp": "2024-11-15T09:15:11Z", "event": "ingest", "status": "completed", "source": "gtbank-q3-2024.md", "thread_id": "abc123"}
```

The `backup` event is written **before** the node modifies an existing page.
It stores the previous content so rollback can restore it exactly. No backup
event means the page was newly created and can simply be deleted on rollback.

On startup, the agent scans the log for incomplete ingestions:

```python
events    = [json.loads(l) for l in open("log.ndjson")]
started   = {e["thread_id"] for e in events if e["status"] == "started"}
completed = {e["thread_id"] for e in events if e["status"] == "completed"}
incomplete = started - completed

for thread_id in incomplete:
    # restore modified pages from their backup
    for e in events:
        if e["thread_id"] != thread_id:
            continue
        if e["status"] == "backup":
            Path(e["path"]).write_text(e["old_content"])   # restore
        if e["status"] == "wrote" and e["is_new"]:
            Path(e["path"]).unlink(missing_ok=True)        # delete new pages
```

This correctly handles both cases:
- **New page** (`is_new: true`) — deleted on rollback. Never existed before.
- **Modified page** (`is_new: false`) — restored from `old_content` in the
  backup event. Prior data is never lost.

The `thread_id` is the LangGraph thread ID — it ties all log entries for one
ingestion together across a crash boundary.

**Recovery options:**

- **Rollback (default):** restore modified pages from backup events, delete
  newly created pages, clean stale index entries, re-ingest from scratch.
- **Resume (future optimisation):** LangGraph's checkpointer can save state
  at each node for large ingestions where re-running from scratch is expensive.
  Requires idempotency guards on every node — deferred to avoid upfront complexity.

**Concurrency:** V1 assumes one ingestion at a time. A file lock on the wiki
directory is acquired at ingestion start and released on completion or rollback.
Concurrent ingestions are rejected with a clear error message. Parallel
ingestion is a future optimisation.

**Pre-flight checks** run before every ingestion:
- Is Oracle DB reachable?
- Is there sufficient disk space?
- Is the source file readable and non-empty?
- Has this source already been ingested? (hash check against `wiki_sources`)

### 7.2 Fault Table

| Fault | Impact | Mitigation |
|---|---|---|
| Oracle DB container crashes mid-embedding | Embeddings not stored | LangGraph checkpoint; re-embed on resume |
| WSL2 closes during ingestion | Pages half-written | WAL backup events; rollback on restart |
| LLM API timeout | Node fails to complete | Retry with exponential backoff via LangChain |
| LLM hallucinates entities | Spurious pages created | Validate extracted structure before writing; human review mode |
| Duplicate source ingested | Duplicate summary pages | Hash check against `wiki_sources` before ingesting; skip if exists |
| Disk full | Page write fails | Check disk space in pre-flight; abort with clear error |
| Broken wikilink written | Dead link in wiki | Lint workflow detects and reports after every ingestion |
| Wrong project selected | Source ingested into wrong wiki | Confirm project name before every ingestion |
| Two ingestions run simultaneously | Race condition on shared entity pages | File lock on wiki directory; concurrent ingestions rejected with clear error |

---

## 8. Scalability

Load in this system shows up in three distinct places:

### 8.1 Document Volume

| Scale | Behaviour | Mitigation |
|---|---|---|
| ~50 pages | Index-first navigation works fine | No action needed |
| ~500 pages | Index-first still fine, slightly slower | No action needed |
| ~5000 pages | index.md becomes large; reading it costs tokens on every query | Hybrid search in Oracle DB replaces full index reads at query time |

**Change detection** prevents re-embedding the entire wiki on every ingestion.
Each page has a `content_hash` in Oracle DB. After ingestion, only pages whose
hash changed are re-embedded. Unchanged pages keep their existing embeddings.

### 8.2 Ingestion Complexity

One source may trigger 10-15 LLM calls (one per page touched). At scale:

- Use batch ingestion mode — process multiple sources in sequence with a
  single review summary rather than per-source interaction
- Local embedding model (nomic, runs on CPU) removes the embedding API
  rate-limit concern entirely
- LLM calls are the bottleneck — tight, purposeful prompts reduce token usage
  and latency

### 8.3 Query Complexity

A query spanning many pages risks hitting context window limits.

**Mitigation:** retrieve page *summaries* first (stored as `snippet` in Oracle
DB), then fetch full content only for the top 2-3 most relevant pages. This
keeps context usage predictable regardless of wiki size.

### 8.4 Entity Page Growth (Deferred)

After many ingestions, a high-traffic entity page like `entities/gtbank.md`
could grow to thousands of lines as each source adds claims, contradictions,
and cross-references. At that size the LLM will struggle to update it coherently
and context costs increase.

This is not a concern at 50-100 sources but becomes one beyond that. The
mitigation when it arises is hierarchical pages:

```
entities/
  gtbank.md           ← overview and current state (stays small)
  gtbank_history.md   ← chronological record of past claims
  gtbank_financials.md ← earnings, metrics, ratios over time
```

Deferred to post-V1. The lint workflow can flag pages exceeding a line
threshold as a signal that splitting is needed.

---

## 9. Maintainability

### 9.1 Operability

One command tells you the health of any wiki project:

```bash
llm-wiki health my-stocks-wiki

# ✅ Oracle DB: connected
# ✅ Pages on disk: 247
# ✅ Pages in index: 247        ← matches disk
# ✅ Pages embedded: 247        ← matches index
# ⚠️  Last ingestion: 14 days ago
# ❌ 3 broken wikilinks detected — run: llm-wiki lint my-stocks-wiki
```

`log.md` is the human-readable operations dashboard — scan it directly or
grep it for a chronological summary:

```bash
grep "^## \[" log.md | tail -10
```

`log.ndjson` is the internal crash recovery log — query it programmatically
to find incomplete ingestions:

```python
events = [json.loads(l) for l in open('log.ndjson')]
started   = {e['thread_id'] for e in events if e['status'] == 'started'}
completed = {e['thread_id'] for e in events if e['status'] == 'completed'}
incomplete = started - completed  # these need rollback
```

Silent failures are surfaced explicitly. If an ingestion completes but the
embedded page count does not increase, the health check reports it.

### 9.2 Simplicity

- **One workflow, one file** — `ingestion.py`, `query.py`, `lint.py`. No
  hunting for where logic lives.
- **The LangGraph graph definition is the documentation** — named nodes make
  the workflow self-describing. A new engineer reads the graph and understands
  the system.
- **No clever abstractions** — explicit code for each page type (summary, entity,
  topic) rather than a generic page processor. More lines, much clearer intent.
- **Single config source** — all credentials and settings in `.env`. Nothing
  hardcoded, nothing scattered.

### 9.3 Evolvability

Clean boundaries between concerns make future changes local, not global:

| Future change | Boundary that contains it |
|---|---|
| Swap Oracle DB for Postgres | All DB calls behind `storage.py`; nothing else imports the driver |
| Add PDF or URL source support | Source reading is one node; add a format detector before it |
| Add a new embedding model | Embedding generation is one function in `embeddings.py` |
| Swap LLM provider | LangChain abstraction; change the provider in config, not in workflow code |
| Add a web UI | Workflows are UI-agnostic; the UI calls the same workflow functions the CLI does |

---

## 10. Module Boundaries (Planned Project Structure)

```
llm-wiki/
├── .env                        ← credentials and config (never committed)
├── .env.example                ← template committed to git
├── pyproject.toml              ← dependencies via uv
│
├── wiki/                       ← core library (no CLI, no UI here)
│   ├── storage.py              ← all Oracle DB interactions (wiki_pages, wiki_sources, wiki_projects)
│   ├── embeddings.py           ← embedding model, generate + store
│   ├── pages.py                ← read, write, parse frontmatter
│   ├── index.py                ← index.md read/write
│   ├── log.py                  ← log.md append + log.ndjson WAL read/write
│   └── schema.py               ← SCHEMA.md parse and validate
│
├── workflows/                  ← LangGraph state machines
│   ├── ingestion.py            ← ingestion workflow graph
│   ├── query.py                ← query workflow graph
│   ├── lint.py                 ← lint workflow graph
│   └── eval.py                 ← eval workflow graph
│
├── eval/                       ← evaluation ground truth and reports
│   ├── golden_ingestion.json   ← Claude-generated + human-reviewed ground truth (10 docs)
│   ├── golden_queries.json     ← Claude-generated + human-reviewed test questions (20-30)
│   └── report.md               ← eval scores per run, tracked over time
│
├── cli/
│   └── main.py                 ← CLI entrypoint (Typer)
│
├── ui/
│   └── app.py                  ← web interface (Step 8)
│
└── tests/
    ├── test_storage.py
    ├── test_pages.py
    ├── test_ingestion.py
    ├── test_query.py
    └── test_eval.py
```

The `wiki/` package is the foundation everything else builds on. Workflows
import from `wiki/`. The CLI and UI import from workflows. Nothing in `wiki/`
knows about the CLI or UI — this is the key boundary that makes the UI
swappable in Step 8 without touching core logic.

The `eval/` directory lives at project root because it belongs to the
engineering process, not to any specific wiki project. Golden sets and reports
are committed to git so score history is preserved across machines.

---

## 11. Implementation Phases

| Phase | Task Step | Deliverable |
|---|---|---|
| 0 | Step 0 | Environment verified — Oracle DB running, embeddings working, LLM API responding |
| 1 | Steps 1-3 | Wiki scaffolding, ingestion pipeline, index and log — functional agent on disk |
| 2 | Step 4 | Oracle DB integration — hybrid search working |
| 3 | Steps 5-6 | Query system and project management — full agent loop |
| 4 | Step 7 | Lint workflow — wiki health and consistency |
| 5 | Step 8 | Web UI and CLI ingestion tool — user-facing interface |

**Eval is our addition, not a task requirement.** The golden set and eval
workflow are engineering best practices that sit alongside the task phases —
they do not gate any phase. Recommended timing: create the golden set after
Phase 1 produces a working ingestion, run the first eval after Phase 2 adds
search. This gives you a baseline before optimisation begins.

---

## 12. Resolved Design Decisions

These were open questions. Decisions are recorded here so the rationale is not
lost.

---

**Embedding strategy: what do we embed?**

Decision: embed **title + first 400 tokens of page content** as a single
string.

Rationale: full page text dilutes the semantic signal for long pages —
a 3000-token entity page about GTBank covers so many sub-topics that its
embedding ends up representing "Nigerian banking in general" rather than
"GTBank specifically." The title anchors the embedding to the specific subject.
400 tokens of body captures the key claims without noise from peripheral
detail.

Validation: Retrieval Recall@5 is measured in the eval workflow against the
golden query set. If recall is poor, the embedding strategy is the first
variable to change — try full page, or title-only, and re-run eval to compare.

---

**Contradiction detection: how do we decide two claims conflict?**

Decision: **retrieval-first, then prompt-based pairwise comparison.**

Rationale: embedding similarity alone catches topically related content, not
logically contradictory content. "GTBank's Q3 loan growth was 12%" and
"GTBank's Q3 loan growth was 8%" are semantically very similar (high cosine
similarity) but directly contradictory. Catching this requires reading both
claims and reasoning about them — which is exactly what an LLM does well.

However, comparing every new claim against every existing claim in the wiki
does not scale. At 5,000 pages with multiple claims each, this becomes an
O(n) LLM call per ingestion — prohibitively expensive.

The solution is **retrieval-first**:

```
New claim extracted from source
    │
    ▼
Embed the new claim
    │
    ▼
Vector search: retrieve top-k most semantically similar existing claims
(from entity pages on the same entities mentioned in the source)
    │
    ▼
LLM pairwise comparison: "Do any of these pairs contradict each other?"
    │
    ▼
Annotate conflicts under ## Contradictions on the relevant pages
```

This keeps contradiction detection O(k) per claim where k is small (5–10),
not O(n) across the whole wiki. The vector search step acts as a cheap
pre-filter; the LLM reasoning step runs only on the candidates it surfaces.

Contradictions are annotated on the relevant pages, not resolved. The user
decides which claim to trust.

Threshold: there is no numeric threshold. The LLM decides what constitutes
a contradiction given the retrieved candidates. Eval measures how often it
agrees with the golden set's hand-annotated contradictions.

---

**Interactive vs auto mode for ingestion?**

Decision: **auto by default, interactive flag available.**

Rationale: the task instructions describe interactive mode as an option, not
the default. For a knowledge base that grows with frequent ingestion (your dad
dropping in a new article), requiring confirmation on every extracted entity
creates too much friction. Auto mode is safer than it sounds because:
- The eval workflow catches systematic extraction errors before they corrupt the wiki
- The lint workflow catches inconsistencies after the fact
- The log records every change so nothing is silently lost

Interactive mode (`--interactive` flag) is available for cases where the
source is ambiguous or high-stakes.

---

**Rollback vs resume on crash?**

Decision: **rollback by default.**

Rationale: resume requires that every node in the ingestion workflow is
idempotent — safe to run twice with the same input. Guaranteeing idempotency
across file writes, LLM calls, and Oracle DB inserts adds significant
complexity upfront. Rollback is simpler: delete any pages written during the
failed ingestion (tracked in the LangGraph state as a `pages_written` list),
clean any stale index entries, and start clean. The source document is
untouched, so nothing is lost — just re-ingest.

**Why idempotency is hard — concrete example:**

Say the ingestion crashes after writing `entities/gtbank.md` but before
finishing `topics/nigerian-banking.md`. LangGraph's checkpointer knows the
last completed node was `update_entity_pages`. On resume, it re-runs
`update_topic_pages` — fine. But it also re-runs `update_entity_pages`,
because making resume safe means every node must be able to run again
without doubling up its side effects:

```
# update_entity_pages runs a second time on resume.
# Without idempotency guards, this happens:

entities/gtbank.md before crash:
  ## Sources
  - [[summaries/gtbank-q3-2024]]     ← written on first run

entities/gtbank.md after resume (no guard):
  ## Sources
  - [[summaries/gtbank-q3-2024]]     ← duplicate! written again on second run
  - [[summaries/gtbank-q3-2024]]
```

To prevent this, every node that writes a file must first check whether its
output already exists and skip or merge rather than overwrite. Every Oracle DB
insert must use `INSERT OR IGNORE` / `MERGE`. Every LLM call result must be
cached so it isn't billed twice. This is three separate idempotency concerns
per node, across ten nodes — significant complexity for an edge case.

**What rollback does instead:**

LangGraph state carries a `pages_written` list that each node appends to as
it creates or modifies files. Critically, every append to `pages_written` is
also immediately written to `log.ndjson` as a `wrote` event — this is what
makes recovery durable across a crash. The LangGraph state is the live working
copy; the NDJSON log is the durable record. On crash detection at startup, the
log is the source of truth:

```
log.ndjson entries for thread_id abc123:
  {"status": "started",  "source": "gtbank-q3-2024.md", ...}
  {"status": "wrote",    "path": "summaries/gtbank-q3-2024.md", ...}
  {"status": "wrote",    "path": "entities/gtbank.md", ...}
  ← no "completed" entry → incomplete ingestion detected

Recovery:
  → delete summaries/gtbank-q3-2024.md
  → restore entities/gtbank.md to pre-ingestion state (or delete if new)
  → remove stale index entries for these paths
  → append: {"status": "rolled_back", "source": "gtbank-q3-2024.md", ...}
  → wiki is back to exactly the state it was in before ingestion started
```

The source file in `raw/` is never written to `pages_written` and is never
touched. Re-ingesting it from scratch produces the same result as if the
crash never happened.

Resume is documented as a future optimisation for large ingestions where
re-running from scratch is expensive.

---

## 13. Deferred Concerns

Valid engineering concerns noted but explicitly deferred. Not in scope for
the current implementation phases. Recorded here so they are not forgotten.

| Concern | Why deferred | When to revisit |
|---|---|---|
| Entity page growth — pages become large after many ingestions | Won't bite at 50-100 sources | When a single entity page exceeds ~3000 tokens |
| Structured claims table — store claims in DB for cheaper contradiction detection | Adds schema complexity upfront | When retrieval-first contradiction detection still feels slow |
| Parallel ingestion — file locking prevents concurrent ingest | V1 is single-user | When multiple users or batch automation is needed |
| Resume on crash — idempotency guards per node | High complexity for an edge case | When ingestions are large enough that rollback + re-ingest is too slow |
| Ingestion metrics — tokens used, LLM time, embedding time per run | Useful for debugging at scale | When diagnosing performance regressions |
