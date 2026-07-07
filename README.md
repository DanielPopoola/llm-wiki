# LLM Wiki: Your Personal Knowledge Base Agent

This project helps you build and manage your own smart knowledge base. You can feed it various documents, ask questions about your ingested information, and rely on it to keep your wiki organized and consistent. It's designed to be a straightforward yet powerful tool for anyone looking to create a self-curating knowledge repository without the constant manual effort.

## Installation

Let's get you set up to run this locally.

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/DanielPopoola/llm-wiki.git
    cd llm-wiki
    ```

2.  **Set Up Virtual Environment:**

    It's always a good idea to work in a virtual environment.

    ```bash
    python -m venv .venv
    source .venv/bin/activate  # On Windows, use `.venv\Scripts\activate`
    ```

3.  **Install Dependencies:**

    All the project's dependencies are listed in `pyproject.toml`.

    ```bash
    pip install --upgrade pip
    pip install -e ".[ui]" # Installs core dependencies and the Reflex UI components
    ```

4.  **Configure Environment Variables:**

    Copy the example environment file and fill in your details.

    ```bash
    cp .env.example .env
    ```

    Open the `.env` file and update the following:
    -   **Oracle Database:** Provide connection details for your Oracle instance.
    -   **LLM Provider:** Choose your LLM (`openai`, `anthropic`, or `ollama`) and provide the `LLM_API_KEY` and `LLM_MODEL`. If using Ollama, set `LLM_BASE_URL`.
    -   **Judge Model (Optional):** If you want a separate model for evaluation, configure `JUDGE_MODEL` and `JUDGE_API_KEY`.
    -   **LangSmith (Optional):** For tracing and debugging LLM calls, set your `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT`.

    A typical setup for development might look like this (adjust values for your actual database and API keys):

    ```example
    # Oracle Database (example for local Oracle XE or Docker)
    ORACLE_HOST=localhost
    ORACLE_PORT=1521
    ORACLE_SERVICE=FREEPDB1 # Or your specific service name
    ORACLE_USER=llmwiki
    ORACLE_PASSWORD=your_oracle_password

    # LLM Provider (example for OpenAI)
    LLM_PROVIDER=openai
    LLM_MODEL=gpt-4o
    LLM_API_KEY=sk-your_openai_api_key

    # Eval judge (separate from generation model)
    JUDGE_MODEL=claude-3-opus-20240229
    JUDGE_API_KEY=sk-ant-your_anthropic_api_key

    # Embedding model (runs locally, no API key needed)
    EMBED_MODEL=BAAI/bge-small-en-v1.5

    # Wiki settings
    WIKIS_DIR=./wikis # Directory where your wiki projects will be stored

    # LangSmith tracing
    LANGSMITH_API_KEY=your-langsmith-api-key-here
    LANGSMITH_PROJECT=llm-wiki
    LANGSMITH_TRACING=true
    ```

5.  **Initialize Database (Manual Step):**
    Ensure your Oracle database schema is set up. This project expects tables like `wiki_pages`, `wiki_sources`, and `wiki_projects` to exist. You'll need to run DDL scripts for these tables if they don't already exist.

    An example `CREATE TABLE` statement for `wiki_pages` (you'll need similar for `wiki_sources` and `wiki_projects`):

    ```bash
    uv run scripts/setup_db.py
    ```

## Usage

Once installed, you can interact with your LLM Wiki using the `llm-wiki` command-line interface.

1.  **Create a New Wiki:**

    Start by creating a new wiki project. This sets up the directory structure and configures it as your active wiki.

    ```bash
    llm-wiki new my-first-wiki
    ```

2.  **Select an Existing Wiki:**

    If you have multiple wikis, you can switch between them.

    ```bash
    llm-wiki select my-stocks-wiki
    ```

3.  **List All Wikis:**

    See all your wiki projects and their basic stats.

    ```bash
    llm-wiki list
    ```

4.  **Inspect Active Wiki:**

    Get an overview of your currently selected wiki's structure and page counts.

    ```bash
    llm-wiki inspect
    ```

5.  **Ingest a Source Document:**

    Add new knowledge to your wiki. The agent will process the document, extract information, and create/update relevant wiki pages.

    ```bash
    llm-wiki ingest path/to/my-document.pdf
    # Or for a specific project
    llm-wiki ingest path/to/another-doc.md --project my-stocks-wiki
    ```

6.  **Query Your Wiki:**

    Ask questions about the information contained in your wiki. The agent will retrieve relevant pages and synthesize an answer.

    ```bash
    llm-wiki query "What are the key findings from GTBank's Q3 2024 earnings?"
    # You can also save the answer as a new wiki page
    llm-wiki query "Summarize the history of Nigerian banking." --save
    ```

7.  **Lint Your Wiki:**

    Run a health check to identify contradictions, broken links, orphaned pages, or knowledge gaps, and get suggestions for improvement.

    ```bash
    llm-wiki lint
    # Apply fixes automatically
    llm-wiki lint --auto
    ```

8.  **Evaluate Wiki Performance:**

    Run an evaluation workflow to test the accuracy and completeness of your wiki's answers against a set of predefined questions and expected answers.

    ```bash
    llm-wiki eval path/to/evals.jsonl
    ```

9.  **Launch the Web UI:**

    Start the interactive web interface to chat with your wiki.

    ```bash
    uv run reflex run
    ```

## Features

Here's what you can do with your LLM Wiki:

-   **Intelligent Document Ingestion:** Automatically processes source documents to extract entities, concepts, and key claims, then uses an LLM to generate summary, entity, and topic pages. It intelligently updates existing pages or creates new ones, logging all changes and supporting rollback.

    ```mermaid
    sequenceDiagram
      actor User
      participant CLI
      participant IngestionWorkflow
      participant SourceReader
      participant Hasher
      participant OracleDB as "Oracle DB"
      participant LLMProvider as "LLM (GPT-4o/Claude)"
      participant FileWriter
      participant EmbeddingsService
      participant WikiFilesystem as "Wiki Files (MD)"

      User->>CLI: llm-wiki ingest document.md
      CLI->>IngestionWorkflow: run_ingestion(document.md)
      IngestionWorkflow->>SourceReader: Read document content
      SourceReader-->>IngestionWorkflow: Document Text
      IngestionWorkflow->>Hasher: Calculate content hash
      Hasher->>OracleDB: Check if source ingested
      OracleDB-->>Hasher: Status (skip/continue)
      alt If Not Skipped
        IngestionWorkflow->>LLMProvider: Extract entities, concepts, claims
        LLMProvider-->>IngestionWorkflow: ExtractionResult
        IngestionWorkflow->>FileWriter: Write Summary Page (summaries/)
        IngestionWorkflow->>FileWriter: Write/Update Entity Pages (entities/)
        IngestionWorkflow->>FileWriter: Write/Update Topic Pages (topics/)
        FileWriter->>WikiFilesystem: Save/Update .md files
        IngestionWorkflow->>LLMProvider: Flag contradictions
        LLMProvider-->>IngestionWorkflow: ContradictionResult
        IngestionWorkflow->>FileWriter: Add contradictions to pages
        IngestionWorkflow->>FileWriter: Create Stub Pages for missing links
        IngestionWorkflow->>FileWriter: Update index.md
        IngestionWorkflow->>EmbeddingsService: Generate embeddings for changed pages
        EmbeddingsService->>OracleDB: Upsert page embeddings & metadata
        IngestionWorkflow->>OracleDB: Record source as completed
      end
      IngestionWorkflow-->>CLI: IngestionState (pages written)
      CLI-->>User: Ingestion complete message
    ```

-   **Semantic Search and Q&A:** Ask natural language questions, and the system performs a hybrid search (vector similarity + full-text) across your wiki pages. It then synthesizes a concise answer, complete with citations to the original wiki pages, and flags knowledge gaps.

    ```mermaid
    sequenceDiagram
      actor User
      participant CLI
      participant QueryWorkflow
      participant EmbeddingsService
      participant OracleDB as "Oracle DB"
      participant PageReader
      participant LLMProvider as "LLM (GPT-4o/Claude)"
      participant WikiFilesystem as "Wiki Files (MD)"

      User->>CLI: llm-wiki query "my question"
      CLI->>QueryWorkflow: run_query("my question")
      QueryWorkflow->>EmbeddingsService: Generate embedding for question
      EmbeddingsService-->>QueryWorkflow: QueryVector
      QueryWorkflow->>OracleDB: Hybrid Search (Vector + Full-Text)
      OracleDB-->>QueryWorkflow: Top N candidate page paths
      QueryWorkflow->>PageReader: Read full content of candidate pages
      PageReader->>WikiFilesystem: Load .md content
      WikiFilesystem-->>PageReader: Page Contents
      PageReader-->>QueryWorkflow: Page Content Dictionaries
      QueryWorkflow->>LLMProvider: Synthesize answer from pages + history
      LLMProvider-->>QueryWorkflow: AnswerResult (answer, citations, has_gap)
      QueryWorkflow-->>CLI: QueryState (answer, citations)
      CLI->>User: Display answer and citations
      opt Optional Save
        User->>CLI: Confirm save (y/N)
        CLI->>QueryWorkflow: User confirmed save
        QueryWorkflow->>LLMProvider: Reformat answer as wiki page
        LLMProvider-->>QueryWorkflow: New Page Body
        QueryWorkflow->>PageReader: Generate new frontmatter
        QueryWorkflow->>WikiFilesystem: Write new page (topics/)
        QueryWorkflow->>WikiFilesystem: Update index.md
        QueryWorkflow->>EmbeddingsService: Embed new page
        EmbeddingsService->>OracleDB: Upsert new page embedding
      end
    ```

-   **Wiki Health-Checking (Linting):** Proactively identifies and suggests fixes for common wiki issues, including:
    -   **Contradictions:** Flags conflicting claims between related pages.
    -   **Stale Claims:** Detects outdated information by comparing entity pages against newer source summaries.
    -   **Orphaned Pages:** Finds pages with no inbound links, indicating they might be isolated or incomplete.
    -   **Broken Links:** Locates wikilinks that point to non-existent pages.
    -   **Knowledge Gaps:** Identifies concepts frequently referenced but lacking their own dedicated pages.
    -   **Research Suggestions:** Based on identified gaps and orphans, the agent suggests new research questions and types of sources to look for.

    ```mermaid
    sequenceDiagram
      actor User
      participant CLI
      participant LintWorkflow
      participant PageWalker
      participant WikiFilesystem as "Wiki Files (MD)"
      participant LLMProvider as "LLM (GPT-4o/Claude)"
      participant FixApplier

      User->>CLI: llm-wiki lint
      CLI->>LintWorkflow: run_lint()
      LintWorkflow->>PageWalker: Scan all wiki pages
      PageWalker->>WikiFilesystem: Read all .md files
      WikiFilesystem-->>PageWalker: All Page Contents
      PageWalker-->>LintWorkflow: List of Page Data
      LintWorkflow->>LLMProvider: check_contradictions (pairwise comparison for shared entities)
      LLMProvider-->>LintWorkflow: Contradiction Findings
      LintWorkflow->>LLMProvider: check_stale_claims (entity vs. newer summary)
      LLMProvider-->>LintWorkflow: Stale Claim Findings
      LintWorkflow->>LintWorkflow: find_orphan_pages (no inbound wikilinks)
      LintWorkflow->>LintWorkflow: find_broken_links (wikilinks to non-existent pages)
      LintWorkflow->>LintWorkflow: identify_gaps (frequent wikilink targets without dedicated pages)
      LintWorkflow->>LLMProvider: suggest_research (from gaps and orphans)
      LLMProvider-->>LintWorkflow: Research Questions & Source Suggestions
      LintWorkflow-->>CLI: Present Findings (critical, warnings, suggestions, research)
      opt Apply Fixes (interactive or --auto)
        CLI->>User: Prompt for fixes (e.g., "Create stub for [[Concept]]?")
        User->>CLI: y/N
        CLI->>FixApplier: Apply confirmed fixes
        FixApplier->>WikiFilesystem: Write stub pages, remove broken links, etc.
      end
      LintWorkflow-->>CLI: LintState (findings, applied fixes)
      CLI-->>User: Lint complete message
    ```

-   **Wiki Evaluation and Benchmarking:** This feature allows you to systematically test the performance of your wiki in answering questions or performing tasks. Provide a dataset of questions and expected answers (a `jsonl` file), and the evaluation workflow will query the wiki, generate answers, and then use a "Judge LLM" to score the wiki's responses against the ground truth. This helps track improvements and regressions over time.

    ```mermaid
    sequenceDiagram
      actor User
      participant CLI
      participant EvalWorkflow
      participant EvalDataset as "Evaluation Dataset (jsonl)"
      participant QueryWorkflow
      participant LLMProvider as "LLM (Generator)"
      participant JudgeLLM as "LLM (Judge)"

      User->>CLI: llm-wiki eval dataset.jsonl
      CLI->>EvalWorkflow: run_evaluation(dataset.jsonl)
      loop For each question in dataset
        EvalWorkflow->>EvalDataset: Read question & expected answer
        EvalWorkflow->>QueryWorkflow: Query wiki with question
        QueryWorkflow->>LLMProvider: Generate answer (via semantic search)
        LLMProvider-->>QueryWorkflow: Wiki Answer
        QueryWorkflow-->>EvalWorkflow: Wiki Answer & Citations
        EvalWorkflow->>JudgeLLM: Score Wiki Answer vs. Expected Answer
        JudgeLLM-->>EvalWorkflow: Evaluation Score
        EvalWorkflow->>EvalWorkflow: Aggregate results
      end
      EvalWorkflow-->>CLI: Evaluation Summary
      CLI-->>User: Display evaluation report
    ```

-   **Interactive Web UI:** Beyond the command-line interface, LLM Wiki now includes a web-based user interface, built with Reflex. This UI provides a chat-like experience where you can interact with your wiki, ask questions, and receive answers in a more dynamic and user-friendly environment. It leverages the same powerful backend workflows for querying and interaction.

## System Architecture / Design

The LLM Wiki is structured around a CLI interface and an optional web UI that orchestrate various `workflows` using a LangGraph state machine. It interacts with your local `wiki` files (Markdown documents), a local embedding model (powered by PyTorch), and an Oracle Database for persistent storage of page metadata, embeddings, and project information. All heavy lifting for content understanding and generation is delegated to an external Large Language Model provider.

```mermaid
flowchart LR
    User[("User (CLI)")] -->|Commands| CLI(cli/main.py)
    User -->|Web Interface| ReflexUI["Reflex Web UI (Frontend)"]
    ReflexUI --> ReflexBackend["Reflex Backend (Python)"]

    subgraph Core Components
        CLI --> Workflows["Workflows (Ingestion, Query, Lint, Eval)"]
        ReflexBackend --> Workflows
        Workflows --> WikiCore["Wiki Core (Pages, Index, Schema, Embeddings)"]
        WikiCore --> Filesystem[("Wiki Filesystem (.md pages, index.md, log.ndjson)")]
    end

    subgraph Data & AI
        Workflows --> LLMProvider["External LLM (OpenAI, Anthropic, Ollama)"]
        Workflows --> EmbeddingsModel["Local Embedding Model (BAAI/bge-small-en-v1.5, PyTorch)"]
        Workflows --> OracleDB[("Oracle Database")]
    end

    CLI --> OracleDB
    WikiCore --> EmbeddingsModel
    WikiCore --> OracleDB
    Workflows --> Filesystem
    EmbeddingsModel --> OracleDB

    style User fill:#cef,stroke:#333,stroke-width:2px
    style Filesystem fill:#e0f7fa,stroke:#0097a7,stroke-width:2px
    style OracleDB fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    style LLMProvider fill:#ffe0b2,stroke:#ff8f00,stroke-width:2px
    style EmbeddingsModel fill:#f3e5f5,stroke:#8e24aa,stroke-width:2px
    style ReflexUI fill:#e6e6fa,stroke:#6a5acd,stroke-width:2px
    style ReflexBackend fill:#e6e6fa,stroke:#6a5acd,stroke-width:2px
```

## Technologies Used

| Category         | Technology                 | Description                                    |
| :--------------- | :------------------------- | :--------------------------------------------- |
| **Language**     | Python                     | The core programming language for the project. |
| **CLI Framework**| Typer                      | Intuitive command-line interface creation.     |
| **Web UI**       | Reflex                     | Framework for building the interactive web user interface. |
| **LLM Orchestration** | LangChain / LangGraph  | Frameworks for building LLM-powered applications and stateful agent workflows. |
| **Database**     | Oracle Database (`oracledb`)| Persistent storage for wiki metadata, embeddings, and project information. Includes vector search capabilities. |
| **Embeddings**   | Sentence Transformers      | Python library for state-of-the-art sentence, paragraph, and image embeddings. |
| **ML Framework** | PyTorch                    | Powering the local embedding model inference.  |
| **LLM Providers**| OpenAI, Anthropic, Ollama  | Configurable LLM backends for text generation and structured extraction. |
| **Document Processing**| Unstructured, BeautifulSoup | Libraries for parsing and extracting text from various document types (PDFs, HTML, etc.). |
| **Text Utilities**| Tiktoken, FuzzyWuzzy, Python-Levenshtein | For tokenization, fuzzy string matching, and calculating Levenshtein distance. |
| **Local Vector DB**| ChromaDB                   | Used for local testing and development of vector search functionalities. |
| **Logging**      | Loguru                     | Provides flexible and powerful logging capabilities. |
| **Testing**      | Pytest                     | Robust framework for writing and running tests. |
| **CLI Styling**  | Rich                       | For beautiful terminal output.                 |
| **Configuration**| Python-dotenv, PyYAML      | Managing environment variables and YAML frontmatter. |

## Contributing

We welcome contributions! If you're looking to help out, here's how you can get started:

1.  **Fork the Repository:** Start by forking the `llm-wiki` repository to your GitHub account.
2.  **Create a New Branch:**
    ```bash
    git checkout -b feature/your-feature-name
    ```
3.  **Make Your Changes:** Implement your feature or bug fix. Please follow the existing coding style and structure.
4.  **Write Tests:** Add unit tests for your new features or bug fixes. Ensure all existing tests pass.
5.  **Run Linting & Formatting:** Use tools like `pyright` and `black` to ensure code quality and consistency.
6.  **Commit Your Changes:**
    ```bash
    git commit -m "feat: Add new feature"
    ```
7.  **Push to Your Fork:**
    ```bash
    git push origin feature/your-feature-name
    ```
8.  **Create a Pull Request:** Open a pull request against the `main` branch of this repository. Describe your changes clearly and link to any relevant issues.

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

## Author Info

Connect with me!

-   **LinkedIn:** [Daniel Popoola](https://www.linkedin.com/in/daniel-popoola-942aa8216/)
-   **X (Twitter):** [@iamuchihadan](https://x.com/iamuchihadan)

---

[![Readme was generated by Dokugen](https://img.shields.io/badge/Readme%20was%20generated%20by-Dokugen-brightgreen)](https://www.npmjs.com/package/dokugen)
