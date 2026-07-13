# Project Aura — Offline-First Support Triage Workstation

Project Aura is a 100% offline, air-gapped support ticket triage, categorization, and Retrival-Augmented Generation (RAG) synthesis workstation. Designed to run locally on standard CPU hardware, Project Aura embeds and index support manuals and ticket databases to provide intelligent IT resolution guides without external network connections.

---

## 🚀 Key Features

1. **Dashboard System Analytics:** Monitors incident counts, knowledge directories, active offline LLM statuses, semantic search success rates, and compute/latency profiling metrics (Search/RAG AVG & P90, per-incident tasks) in seconds.
2. **Knowledge Base Curator:** Upload and parse ticketing CSV files and technical manuals (`.pdf`/`.docx`) page-by-page. Automatically indexes texts using local `all-MiniLM-L6-v2` embeddings in ChromaDB. Uses SHA-256 validation to skip duplicates.
3. **Macro Categorization Engine:** Batch classifies raw, unclassified tickets into operational categories (Access, Failure in Service, etc.) in an asynchronous background thread. Provides date and application filters, error codes/messages extraction, and volume breakdowns.
4. **Interactive Search & Triage (RAG):**
   - **Single Query Triage:** Input issues and target applications (CMDB CI) to synthesize step-by-step resolution guides based on local manuals, complete with source citations and deduplicated historical tickets (showing Assigned To and Group details). Renders rich markdown offline using local `marked.min.js`.
   - **Bulk Incident Triage:** Batch triage lists of queries using a CSV upload (caps at 10 items) and download synthesized triage reports.
5. **System Installer & Reset (Settings):** Runs local diagnostics on databases and models, and provides a button to clear and seed system PostgreSQL tables and vector storage indexes cleanly.

---

## 🛠️ Technical Architecture

- **Backend:** FastAPI (Python monolithic layer serving API endpoints and rendering Jinja2 template views).
- **Relational Storage:** PostgreSQL (relational mapping via SQLAlchemy ORM).
- **Vector Storage:** ChromaDB Persistent Client (L2 metrics, persisting inside `/.chromadb`).
- **Offline Models:**
  - *Embeddings:* `all-MiniLM-L6-v2` SentenceTransformer model (CPU).
  - *Large Language Models:* `gemma-2-2b-it-Q4_K_M.gguf` (Primary) and `Phi-3.1-mini-4k-instruct-Q4_K_M.gguf` (Fallback).
- **Memory Fallback Lifecycle:** If the primary LLM fails to boot (OOM on CPU), the backend catches the error, registers a warning in the telemetry logs, and silently switches to the fallback model to maintain system availability.

---

## ⚙️ Project Configuration

Create a `.env` file in the root directory:
```env
DATABASE_URL=postgresql://postgres:admin@localhost:5432/project_aura
OFFLINE_MODEL_HOME=C:\Users\Roni\Documents\Python Projects\Offline models
PROJECT_ROOT=C:\Users\Roni\Documents\GitHub\project-aura
```

### Registered Models Configuration (`config.json`):
```json
{
  "models": [
    {
      "name": "gemma-2-2b-it-Q4_K_M.gguf",
      "type": "gemma",
      "Active": true
    },
    {
      "name": "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf",
      "type": "phi",
      "Active": false
    }
  ]
}
```

---

## ⚡ Running the Application

Double-click the **`run_app.bat`** file in the root folder, or execute:
```powershell
# Activate environment
.\venv\Scripts\activate

# Run Uvicorn
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open **`http://localhost:8000/`** in your web browser.

*First Time Setup Note:* Go to the **Settings** tab in the navigation bar and click **Reset & Install Application** to initialize your database structure, vector collections, and file repositories.
