# Issue Resolution Tracker - Project Aura

This tracker documents all bugs, code failures, and feature enhancements encountered during internal testing and User Acceptance Testing (UAT), along with their root causes and resolutions.

---

| Issue ID | Source | Component | Description / Root Cause | Resolution | Status |
| --- | --- | --- | --- | --- | --- |
| **BUG-001** | Internal | Model Orchestrator | `llama-cpp-python` failed compiling from source due to missing local MSVC toolchains on Windows. | Switched installation instructions to target precompiled CPU wheels matching the local Python 3.13 ABI. | **Resolved** |
| **BUG-002** | Internal | Vector Store | PDF parser ran successfully but Word (`.docx`) file chunks threw a ChromaDB None value metadata exception. | Modified chunk metadata generator to completely omit `page_number` keys for DOCX uploads. | **Resolved** |
| **BUG-003** | Internal | Telemetry Logs | Telemetry logs endpoint returned HTTP 500 due to PostgreSQL SELECT DISTINCT sorting restriction. | Rewrote query to fetch parent telemetry records first, extracting unique trace IDs in Python. | **Resolved** |
| **BUG-004** | UAT | Reset & Install | Clicking Application Install failed: `'unclassified_count' is an invalid keyword argument for JobStatus`. | Removed the invalid column parameter and seeded both jobs with correct default fields. | **Resolved** |
| **BUG-005** | UAT | RAG Synthesis | Irrelevant search queries (e.g. `"This is a test incident"`) still returned manual citations and synthesized fake guides. | Added L2 distance score threshold filtering to the knowledge base manual query chunks. | **Resolved** |
| **BUG-006** | UAT | Telemetry Logs | Logs displayed as flat tables; vectorization runs showed up separately and lacked incident ticket identifiers. | Implemented LangSmith-style collapsible parent-child groupings and logged ticket IDs in child spans. | **Resolved** |
| **BUG-007** | UAT | Progress Bars | Categorization progress bar jumped from 0% to 100% instantly; bulk CSV triage locked the UI thread. | Migrated bulk triage to async BackgroundTasks. Added database progress tracking columns. | **Resolved** |
| **BUG-008** | UAT | Dependencies | `llama-cpp-python` wheel installation failed on the new environment with "not supported wheel on this platform" error. | Added Python version and compatible tags diagnostic instructions to download the exact matching wheel. | **Resolved** |
| **BUG-009** | UAT | Configuration | Database name was hardcoded as `postgres` in documentation and manual seed scripts, causing mismatches. | Updated all installation guides and README connection strings to use `project_aura`. | **Resolved** |
| **BUG-010** | UAT | Vector Store | Recreating collections in the manual seed script without passing the embedding function crashed offline app startup. | Updated seed script snippet to import and pass the custom offline `embedding_function` when creating collections. | **Resolved** |

---

## Detailed Root Cause Analysis & Fix Verification

### BUG-004: JobStatus Seed Keyword Exception
*   **Root Cause:** The PostgreSQL initialization script triggered from `/api/settings/reset-install` attempted to instantiate the `JobStatus` ORM class using an outdated parameter `unclassified_count=0` that was not defined as a column in database.py.
*   **Resolution:** Modified [app/main.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/main.py) to seed database records for `macro_categorization` and `bulk_triage` using valid columns:
    ```python
    db.add(JobStatus(job_name="macro_categorization", is_running=False, total_items=0, processed_items=0))
    db.add(JobStatus(job_name="bulk_triage", is_running=False, total_items=0, processed_items=0))
    ```

### BUG-005: RAG False Citations on Irrelevant Queries
*   **Root Cause:** The search query returned the top 4 documentation chunks regardless of distance score. For highly irrelevant error texts, the LLM attempted to synthesize a guide utilizing these low-confidence matches.
*   **Resolution:** Applied the match score distance threshold check to the document curator collections inside [app/main.py](file:///c:/Users/Roni/Documents/GitHub/project-aura/app/main.py):
    ```python
    score = max(0.0, (1.0 - dist / 2.0) * 100.0)
    if score < min_threshold:
        continue
    ```
    This successfully discards low-confidence chunks. Unrelated queries now return a clean, citation-free notification.

### BUG-007: Inconsistent Progress Bars & Synchronous Bulk Triage
*   **Root Cause:** 
    1.  The bulk triage endpoint ran synchronously in the main thread loop, blocking page responsiveness.
    2.  The categorization loop committed Category rows at the end of the batch run rather than progressively, preventing poller queries from calculating gradual progress.
*   **Resolution:** 
    1.  Refactored bulk triage to execute asynchronously utilizing FastAPI's `BackgroundTasks` handler.
    2.  Added `total_items` and `processed_items` columns to `JobStatus` table.
    3.  Updated macro-categorization and bulk triage loops to commit progress changes progressively.
    4.  Created `/api/analysis/bulk-triage/status/{job_id}` polling status endpoints for the frontend.

### BUG-008: llama-cpp-python Platform Compatibility Wheel Error
*   **Root Cause:** The target virtual environment ran on a different Python ABI (e.g. Python 3.12 vs 3.13) or architecture than the downloaded `llama-cpp-python` precompiled CPU wheel file, causing `pip` to reject the installation.
*   **Resolution:** Added explicit debugging steps inside [MANUAL_INSTALL.md](file:///c:/Users/Roni/Documents/GitHub/project-aura/application_documentation/MANUAL_INSTALL.md) guiding developers to verify their environment using `python --version` and `pip debug --verbose` to download the exact compatible wheel tag (e.g. `cp312-cp312-win_amd64.whl`).

### BUG-009: postgres Database Connection Mismatch
*   **Root Cause:** The database configurations and sample `.env` properties declared inside the manuals referenced `postgres` as the default database, which mismatched the target workstation's database named `project_aura`.
*   **Resolution:** Updated all database configurations, environmental variables, and connection strings inside [MANUAL_INSTALL.md](file:///c:/Users/Roni/Documents/GitHub/project-aura/application_documentation/MANUAL_INSTALL.md) and [README.md](file:///c:/Users/Roni/Documents/GitHub/project-aura/README.md) to use `project_aura`.

### BUG-010: Offline ChromaDB Collection Initialization Crash
*   **Root Cause:** The manual seed script block created collections without specifying an embedding function. Consequently, ChromaDB defaulted to its standard embedding function, which requires an active internet connection to download weights from Hugging Face, crashing the app on air-gapped systems.
*   **Resolution:** Updated the manual seed script in [MANUAL_INSTALL.md](file:///c:/Users/Roni/Documents/GitHub/project-aura/application_documentation/MANUAL_INSTALL.md) to import `embedding_function` from `app.vector_store` and pass it as an argument during collection creation calls.
