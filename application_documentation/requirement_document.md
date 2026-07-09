# Software Requirements Specification (SRS)

## Project Name: Project Aura

**Version:** 1.0

**Environment:** 100% Offline / Air-Gapped Laptop Deployment (CPU-Only) 

---

## 1. Executive Summary & System Context

Project Aura is a fully offline, air-gapped incident analysis and knowledge-synthesis system designed to operate locally on standard laptop hardware without GPU acceleration. The platform optimizes IT support workflows by utilizing historical incident logs and technical documentation to provide local semantic search, guided dynamic categorization, and Retrieval-Augmented Generation (RAG). Because the application is deployed in a strictly private, offline environment, it explicitly forbids any external API connections, external telemetry sinks, or remote dependencies.

---

## 2. Technical Architecture & Foundational Design

### 2.1 Unified Single-Layer Architecture

* 
**Monolithic Framework:** The application is built as a unified **FastAPI (Python)** application. To maximize performance and eliminate IPC (Inter-Process Communication) and HTTP serialization overhead on a CPU-bound machine, FastAPI handles both the backend REST API endpoints and renders the front-end user interface via **Jinja2 Templates**.


* 
**Vector Database Mode:** **ChromaDB** must run strictly as a `PersistentClient` targeting a designated local directory (`/.chromadb`) within the root folder of the project. A client-server ChromaDB deployment is prohibited to preserve system memory.


* 
**Relational Database Engine:** A local instance of **PostgreSQL** handles structured relational data storage. All database configuration options and access credentials must be loaded at runtime from a local `.env` file.



### 2.2 Strict Offline Model Ingestion & Memory Fallback Lifecycle

* 
**Model Store:** All foundational models are stored locally within the directory pathway designated by the `OFFLINE_MODEL_HOME` variable inside the `.env` file.


* **Pre-downloaded Model Assets:**
* 
*Embedding Engine:* `all-MiniLM-L6-v2`.


* 
*Large Language Models (LLMs):* `gemma-2-2b-it-Q4_K_M.gguf`, `gemma-4-E2B_q4_0-it.gguf`, and `Phi-3.1-mini-4k-instruct-Q4_K_M.gguf`.




* 
**Active Model Orchestration:** The system reads a local `config.json` configuration file containing a declaration array of available models.


* Each model profile includes an `Active` boolean flag.


* Exactly one model can be flagged as `Active=TRUE` at any given time. If zero or multiple models are flagged as active, the application must throw a hard configuration initialization error during boot.


* The lowest-power model (`Phi-3.1-mini-4k-instruct-Q4_K_M.gguf`) must be explicitly flagged within the system configurations as the global **Fallback Model**.




* 
**Silent Memory Fallback Mechanism:** If the user-selected primary active LLM fails to initialize due to a system memory allocation error (OOM on CPU), the FastAPI backend must catch the initialization exception, flag the failing model as temporarily disabled, and **silently initialize the designated fallback model**. The application must remain functional, and a warning log must be appended to the telemetry database table.



---

## 3. UI/UX & Visual Design Standards

### 3.1 Design Language Rules

* **Aesthetics:** The interface must look clean and professional, using rounded corners, drop shadows, and subtle micro-interactions. Heavy animations and decorative, non-functional elements are explicitly banned.
* **Theme Management:** The UI requires native support for two themes, controlled completely through centralized variables in a single global stylesheet (`style.css`).
  * *Theme Switcher:* Realized as a premium round-slider checkbox toggle switch styled with CSS transitions (custom checkbox slider representing 🌙 Dark vs ☀️ Light).
  * *Light Theme:* Primary = `#FFFFFF` (White), Secondary = `#000000` (Black), Accent = `#808080` (Grey), with light slate borders and subtle shadows.
  * *Dark Theme:* Primary = `#000000` (Black), Secondary = `#808080` (Grey), Accent = `#FFFFFF` (White).

### 3.2 Global Structural Layout & Navigation

* **Persistent Grid-Based Top Bar:** Structured using CSS Grid (`grid-template-columns: 1fr auto 1fr`) to keep elements centered perfectly:
  * *Top-Left Alignment:* Company Logo placeholder (renders `logo.png` / `logo_dark.png` based on theme).
  * *Center Alignment:* Proportional Application Logo (`logo.png` / `logo_dark.png`) rendered inline next to the **PROJECT AURA** title.
  * *Top-Right Alignment:* Theme Toggle Slider followed by a circular **`?` Help Button** that redirects the user to a detailed User Manual route (`/help`).
* **Interactive User Manual (`/help`):** A dedicated, searchable manual detailing application layout descriptions, navigation guides, step-by-step feature usage, and scenario walkthroughs (Active Directory unlock and Bulk CSV ingestion scenarios).
* **Homepage Architecture:** Features a grid of 6 analytics KPI cards, followed by a layout split into two functional zones for Knowledge Management and Analysis & Synthesis.



---

## 4. Functional Requirements: Knowledge Management (Left Section)

### 4.1 Feature 1: Historical Incident Ingestion

* **Input Constraints:** Accepts local file uploads strictly in `.csv` format.
* **Schema Enforcement:** The CSV parser performs case-insensitive validation against the following fields:
  1. `number` (Primary Key string)
  2. `cmdb_ci` (Configuration Item / Application Identifier)
  3. `short_description`
  4. `caller_id`
  5. `u_ge_affected_user`
  6. `opened_by`
  7. `priority`
  8. `state`
  9. `assignment_group` (parsed and mapped to DB)
  10. `assigned_to` (parsed and mapped to DB)
  11. `description`
  12. `comments_and_work_notes`
  13. `closed_note`
  14. `sys_created_on` (parsed case-insensitively, supporting format `DD-MM-YYYY HH:MM:SS AM/PM` or standard timestamp models)
* **Text Sanitization Rules:** The parser must handle unescaped quotation marks, raw commas, HTML blocks, and literal newline characters (`\n`) frequently found in unstructured fields like `comments_and_work_notes`. Data must be ingested safely using standard UTF-8 encoding.
* **Deduplication & Resiliency Rules:** If an incident `number` matches a record that already exists in the relational database, evaluate its `process_status` column.
  * If `process_status == 'PROCESSED'`, the record is skipped.
  * If the incident `number` matches but `process_status != 'PROCESSED'`, the system must overwrite the relational columns with the incoming data and mark the record for vector processing.
* **Vectorization Pipeline:**
  * *Target Text Content:* A concatenated string constructed as: `[Short Description] + [Description] + [Comments and Work Notes] + [Closed Note]`.
  * *Chunking Strategy:* Split into chunks of 500 tokens with a fixed overlap of 75 tokens.
  * *Metadata Enrichment:* Chunks committed to the incident collection in ChromaDB must include the incident `number`, the `cmdb_ci`, and a unique `chunk_id` string. Empty `cmdb_ci` fields are mapped to `""` (empty string) to prevent vectorization errors.

### 4.2 Feature 2: Knowledge Base Curator

* **Input Constraints:** Accepts file uploads limited strictly to `.docx` and `.pdf` extensions.
* **File Path Management Lifecycle:** Documents are managed locally within the project directory using three physical sub-directories:
  * `/knowledge_docs/pending/` – Stores documents while they are being processed.
  * `/knowledge_docs/processed/` – Stores successfully chunked and vectorized documents.
  * `/knowledge_docs/failed/` – Stores documents that caused parsing or vectorization errors.
* **Processing Pipeline & Metadata Rules:**
  1. When a document is uploaded, it is placed in `/knowledge_docs/pending/`.
  2. A SHA-256 hash is calculated. Duplicate hashes are skipped.
  3. If filename exists but hash differs, old vector chunks matching the filename are deleted from ChromaDB, and database/physical files are overwritten.
  4. The file is chunked, vectorized using `all-MiniLM-L6-v2`, and stored in a separate ChromaDB knowledge collection.
  5. *ChromaDB Metadata Safety Rule:* ChromaDB does not accept null or `None` values in metadata fields. For PDFs, the page number is vectorized as `page_number` (int). For `.docx` files (which do not have page numbers), the `page_number` key must be **omitted entirely** from the metadata dictionary to prevent database exceptions.
  6. Upon success, the entry is written to database, and physical file is moved to `/knowledge_docs/processed/`. On failure, the file is moved to `/knowledge_docs/failed/`.
* **Active Documents Catalog:** To prevent clutter in production environments with 100+ documents, the UI displays summarized metric cards (Total Active Docs and Total Failed ingestions) and a compact list of the **top 5 most recently uploaded documents** instead of a full document list.





---

## 5. Functional Requirements: Analysis (Right Section)

### 5.1 Feature 1: Macro Categorization Engine

* **Core Objective:** Scans all unclassified historical incidents in the relational database and uses the active local LLM to assign them to a high-level operational taxonomy.
* **Prompt Constraints & Clustering:** The system prompt must instruct the LLM to use a finite, predictable set of categories (*Access, Failure in Service, User Knowledge Gap, Application Error, Data/Missing Files Issues*). The LLM also extracts associated error codes and key error messages from the text.
* **Async Processing Bounds:** Capped at 1,500 incidents per run (default batch process size is 15). The engine executes in an asynchronous background thread so it does not freeze the FastAPI main loop.
* **State Management & UI Lifecycles:** Renders active poller progress bars during runs that update gradually and consistently using real-time database-backed progress metrics (total and processed item counters, e.g. `2 of 8 completed (25%)`). If all incidents are categorized, the "Run Categorization" button is hidden.
* **Analytical Dashboard & Reporting Requirements:**
  * *Volume Breakdown Card:* Moved out of the dual-column split to occupy the **full horizontal width** of the page. Shows categorical bar graphs.
  * *Export CSV Relocation:* The "Export CSV" action button is placed in the **top-right corner of the Volume Breakdown card** (downloading a CSV containing the `number` and `assigned_category`, honoring any active filters).
  * *Interactive Filters:* A "Apply Filter" action button reloads the page with parameters to perform query-based filtering on both the unclassified metrics card and the volume breakdown chart based on start date, end date, and CMDB CI.
  * *Dynamic Category Taxonomies & Explanations:* A dedicated full-width section listing all categories currently in the database. Baseline categories show defined context descriptions. Dynamically generated custom categories query the database and list up to 3 real-world error messages related to it (e.g. *"Linked to error messages: ..."*).

### 5.2 Feature 2: Single Incident Guidance Locator

* **Search Phase:** The user enters an issue description and specifies a target `cmdb_ci`. Pre-filtering excludes chunks that do not explicitly match the user-specified `cmdb_ci`.
* **Scoring & Threshold Resolution:** ChromaDB L2 distance calculations are mapped into a percentage scale:
  $$\text{Match Score (\%)} = \max\left(0, \left(1 - \frac{\text{L2 Distance}}{2}\right) \times 100\right)$$
  Results below the configured threshold (default $10\%$) are discarded.
* **Deduplication & Incidents Metadata:** Extracts top 5 deduplicated matching incidents. Bypasses LLM summarization on tickets to save CPU memory. Fetches raw data from DB and renders **Assigned To** and **Assignment Group** metadata under the incident title.
* **Knowledge Base Synthesis (RAG) & Markdown Rendering:** Top 4 matching knowledge chunks are combined and passed to the active LLM to compile guidelines.
  * *Markdown Rendering Rule:* RAG responses must be rendered as formatted HTML rather than raw text. The application uses a local version of `marked.min.js` to process markdown offline.
  * *Scrollable Resolution Guide:* The Assistant Resolution Guide card has a fixed height limit (`max-height: 300px` with `overflow-y: auto`) so the layout does not expand with large text output.
  * *Equal-Height Columns:* The Input Parameters card and the Assistant Resolution Guide card use CSS Grid stretch behaviors to match heights exactly.

### 5.3 Feature 3: Bulk Incident Triage

* **Core Execution:** Performs batch search, thresholding, database retrieval, and knowledge RAG pipelines over uploaded CSV files containing `description` and `cmdb_ci` columns.
* **Batch Constraints:** Capped at a maximum of **20 incidents per batch**. Outputs render using `marked.parse` for rich formatted markdown.
* **Asynchronous Progress Execution:** Bulk triage runs asynchronously in background tasks. The frontend displays an active polling loader, updating a visual progress bar and text metrics (e.g. `2 of 8 records completed (25%)`) until batch processing finishes.



---

## 6. Functional Requirements: Homepage System Analytics

The homepage features a dedicated analytics dashboard providing an operational view of the local system's health and data metrics:

* **Incident Ingestion Counters:** Total number of incidents marked as `PROCESSED`, `PENDING`, or `FAILED`.
* **Knowledge Base Metrics:** Total successfully processed documents versus failed document tasks.
* **Search Performance Telemetry:**
  * *Total Searches:* Sum of all executed interactive single searches and background bulk triage rows.
  * *Semantic Success Rate (%):* The percentage of search executions that returned at least one matching document or incident.
* **Latency & Compute Profiling (in Seconds):**
  * *Search & RAG Synthesis Queries Latency:* AVG and P90 execution latencies displayed **side-by-side** on a single line.
  * *Per-Incident Ingestion Latency:* Average time in seconds to vectorize a ticket (tracked via background `single_incident_vectorization` telemetry spans).
  * *Per-Incident Categorization Latency:* Average LLM classification duration in seconds per incident in the background thread (tracked via `single_incident_categorization` telemetry spans).
* **Cumulative Compute Metrics:** Total LLM tokens consumed across all sessions.

## 7. Database & Telemetry Schemas

### 7.1 Relational Database Storage Layout (`PostgreSQL`)

The local database instance contains six primary tables to organize the application state:

```
  +-------------------------------+             +-------------------------+
  |           incidents           |             |   knowledge_documents   |
  +-------------------------------+             +-------------------------+
  | PK  number (VARCHAR)          |             | PK  id (SERIAL)         |
  |     cmdb_ci (VARCHAR)         |             |     filename (VARCHAR)  |
  |     short_desc (TEXT)         |             |     hash_value (VARCHAR)|
  |     description (TEXT)        |             |     processed_date (TZ) |
  |     work_notes (TEXT)         |             +-------------------------+
  |     closed_note (TEXT)        |
  |     assigned_to (VARCHAR)     |             +-------------------------+
  |     assignment_group (VARCHAR)|             |      categories         |
  |     sys_created_on (TZ)       |             +-------------------------+
  |     process_status (VARCHAR)  |             | PK  id (SERIAL)         |
  +-------------------------------+             | FK  number (VARCHAR)    |
                 |                              |     assigned_cat (VAR)  |
                 | 1                            |     error_codes (TEXT)  |
                 +-- Draws From ----------------|     error_msgs (TEXT)   |
                 | 1                            +-------------------------+
                 v
  +-------------------------+                   +-------------------------+
  |    incident_searches    |                   |      job_statuses       |
  +-------------------------+                   +-------------------------+
  | PK  search_id (UUID)    |                   | PK  job_name (VARCHAR)  |
  |     query_text (TEXT)   |                   |     is_running (BOOLEAN)|
  |     matched_incs (JSON) |                   |     total_items (INT)   |
  |     rag_response (TEXT) |                   |     processed_items(INT)|
  |     citations (JSON)    |                   +-------------------------+
  +-------------------------+
                                                +-------------------------+
                                                |     deleted_actions     |
                                                +-------------------------+
                                                | PK  id (SERIAL)         |
                                                |     timestamp (TZ)      |
                                                |     item_type (VARCHAR) |
                                                |     item_id (VARCHAR)   |
                                                |     details (TEXT)      |
                                                +-------------------------+
```

### 7.2 Structured Telemetry Logs Layout (`telemetry_logs`)

All operational events must be recorded synchronously into a dedicated table designed around OpenTelemetry semantic standards:

| Column Name | Storage Type | OTel Semantic Attribute Analogue | Functional Description |
| --- | --- | --- | --- |
| `id` | SERIAL (PK) | N/A | Internal row identifier. |
| `timestamp` | TIMESTAMP WITH TZ | `time` | Exact local system time when the log event occurred. |
| `trace_id` | VARCHAR(32) | `trace_id` | Unique string grouping all logs within a single transaction lifecycle. |
| `span_id` | VARCHAR(16) | `span_id` | Identifies the specific operation wrapper block.

 |
| `event_name` | VARCHAR(255) | `name` | Functional type descriptor (e.g., `incident_ingestion`, `rag_query`). |
| `duration_ms` | INTEGER | `duration_ms` | Total elapsed compute processing time in milliseconds.

 |
| `token_count` | INTEGER | `llm.token_count` | Number of LLM tokens consumed during the inference call.

 |
| `status` | VARCHAR(50) | `status.code` | Output state indicator, recorded as `SUCCESS` or `ERROR`.

 |
| `exception_type` | TEXT | `exception.type` | Tracks explicit system faults, such as `RuntimeMemoryOut`.

 |
| `message` | TEXT | `exception.message` | Detailed structural log context message or raw error payload.

 |

---

## 8. Verification & Non-Functional Constraints

1. 
**Strict Offline Isolation:** The software must run entirely without internet access. Any network socket calls pointing outside of `localhost` / `127.0.0.1` must be blocked, and the system must never attempt to contact external endpoints for updates, dependencies, or telemetry validation.


2. 
**Resource Constraints:** The application must be heavily optimized for CPU execution environments with limited RAM. Heavy arrays must be cleared quickly, and model binaries must be managed carefully in memory to prevent the host laptop from freezing or crashing.