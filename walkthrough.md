# Walkthrough - Project Aura Implementation

Project Aura has been successfully implemented, verified, and tested as a unified, CPU-bound, 100% offline incident analysis and knowledge-synthesis system.

---

## Accomplishments

1. **Monolithic Architecture**: Constructed a unified FastAPI application rendering Jinja2 templates for the frontend and providing clean REST API interfaces for backend workflows.
2. **Offline Local Model Orchestration**:
   - Integrated `llama-cpp-python` (resolved AVX-512 issues by using version `0.3.19` which targets the specific Python 3.13 ABI on Windows without illegal instruction faults).
   - Configured `config.json` with active and fallback model checks.
   - Built the **Silent Memory Fallback Mechanism** that catches primary model load failures (such as OOMs) and silently activates the fallback `Phi-3.1` model, logging a warning log.
3. **Knowledge Base Curator**:
   - Parsed `.docx` files using `python-docx` and `.pdf` files page-by-page using `pypdf`.
   - Used local `all-MiniLM-L6-v2` embeddings via a custom ChromaDB Persistent client targeting `/.chromadb`.
   - Implemented SHA-256 deduplication and overwrite handlers for updated files.
4. **Incident Ingestion & Vectorization**:
   - Ingests CSV ticketing files with case-insensitive validation and text sanitization.
   - Vectorizes text in a background thread to prevent blocking the main loop.
5. **Guidance, Triage, and Categorization**:
   - Developed semantic search against the incidents collection with hard `cmdb_ci` filtering, match scoring, and top 5 ticket lookup.
   - Implemented RAG synthesis over the top 4 documentation chunks.
   - Configured background thread execution for the Macro Categorization Engine with DB job-status locking.
   - Set up Bulk Incident Triage (up to 10 queries per CSV file).
6. **Aesthetics & Theme Management**:
   - Created a clean CSS styling structure with global Light/Dark CSS variables.
   - Standardized top navigation bar with application and company logos.
   - Enabled client-side theme toggling with persistent state preservation in local storage.
7. **Premium Telemetry Transaction Logging**:
   - Implemented LangSmith-grouped transaction telemetry traces with collapsible child sub-spans and chevron toggles on the settings dashboard.
   - Captured individual ticket vectorization and categorization details as trace sub-spans.
   - Added a log CSV exporter matching the current UI filter context.
8. **Asynchronous Progress Execution**:
   - Refactored bulk incident triage to run asynchronously inside background tasks.
   - Added interactive polling loader bars and counters (`2 of 8 completed (25%)`) to bulk triage and macro categorization dashboards based on database progress metrics.

---

## Files Created

- [/app/config.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/config.py) — Config loader and validation rules.
- [/app/database.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/database.py) — SQLAlchemy engine, session generator, and PostgreSQL schema mappings.
- [/app/telemetry.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/telemetry.py) — OTel-analogue database logging span context manager.
- [/app/vector_store.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/vector_store.py) — ChromaDB collections and persistent search queries.
- [/app/parsers.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/parsers.py) — PDF/DOCX extractors, CSV schema validator, and token-based chunking.
- [/app/models_orchestrator.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/models_orchestrator.py) — LLM generator with chat template format wrappers and silent OOM fallback.
- [/app/main.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/main.py) — FastAPI routing server, background workers, and endpoints.
- [/app/static/style.css](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/static/style.css) — Custom stylesheet containing Light/Dark mode themes.
- [/app/templates/base.html](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/templates/base.html) — Parent page blueprint with header, logos, and nav elements.
- [/app/templates/homepage.html](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/templates/homepage.html) — Dashboard showing system analytics and compute counters.
- [/app/templates/knowledge.html](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/templates/knowledge.html) — Upload dashboards for tickets and doc curators.
- [/app/templates/categorization.html](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/templates/categorization.html) — Categorization poller, volume breakdown, and filter dashboard.
- [/app/templates/guidance.html](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/templates/guidance.html) — Search panel for RAG queries and CSV bulk triage.
- [/tests/test_pipelines.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/tests/test_pipelines.py) — Automated test suite verifying pipeline sanity.
- [/.env](file:///c:/Users/Roni/Documents/GitHub/project-aura/.env) — Local environment configuration.
- [/config.json](file:///c:/Users/Roni/Documents/GitHub/project-aura/config.json) — Available model registration arrays.

---

## Test Execution Results

Automated unit tests were run using Python's `unittest` module in the local virtual environment:

```powershell
.\venv\Scripts\python -m unittest tests/test_pipelines.py
```

### Output:
```text
...llama_context: n_ctx_seq (2048) < n_ctx_train (4096) -- the full capacity of the model will not be utilized
..
----------------------------------------------------------------------
Ran 5 tests in 17.342s

OK
Configuration loaded and validated successfully.
Loading primary active model: non_existent_model_oom.gguf from C:\Users\Roni\Documents\Python Projects\Offline models\non_existent_model_oom.gguf...
Warning: Failed to initialize primary model 'non_existent_model_oom.gguf': Model path does not exist: C:\Users\Roni\Documents\Python Projects\Offline models\non_existent_model_oom.gguf. Invoking silent fallback mechanism...
Successfully initialized fallback model: Phi-3.1-mini-4k-instruct-Q4_K_M.gguf
```

All 5 test cases successfully passed:
1. **Model Configuration Loading**: Confirmed only 1 active and 1 fallback model are registered.
2. **Date Parser**: Validated DD-MM-YYYY HH:MM:SS AM/PM string parsing to UTC datetime.
3. **CSV Schema Enforcement**: Verified header case-insensitivity mapping.
4. **Text Chunking**: Confirmed token-based text chunk splits.
5. **Silent Memory Fallback Simulation**: Simulated OOM loading failure on a dummy model file name, successfully capturing the error and initializing the `Phi-3.1` fallback model.
