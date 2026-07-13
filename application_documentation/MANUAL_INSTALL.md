# Project Aura Manual Installation Guide

This document details the step-by-step instructions to configure, initialize, and deploy Project Aura on your local Windows workstation. Use these steps if you want to configure components manually or if the automated seeding script fails.

---

## 1. Prerequisites
Ensure the following software is installed on your workstation before starting:
- **Python 3.10 to 3.13** (64-bit version).
- **PostgreSQL 14+** (running locally on standard port `5432`).

---

## 2. Step-by-Step Manual Setup

### Step 2.1: Clone/Copy the Code Repository
Extract the source bundle files into your chosen project workspace, for example:
```text
C:\Users\Roni\Documents\GitHub\project-aura
```

### Step 2.2: Setup Python Virtual Environment
Open PowerShell or CMD in the project root directory and run:
```powershell
# Create a local virtual environment named 'venv'
python -m venv venv

# Activate the virtual environment
# On Windows PowerShell:
.\venv\Scripts\Activate.ps1
# On Windows CMD:
.\venv\Scripts\activate.bat
```

### Step 2.3: Install Offline Dependencies
Install the required packages listed in `requirements.txt`.
```powershell
pip install -r requirements.txt
```
*Note: If `llama-cpp-python` fails to compile due to missing compiler tools:*
1. Download the pre-compiled CPU wheel file for Windows matching your Python version from the official wheels index at `https://abetlen.github.io/llama-cpp-python/whl/cpu`.
2. Ensure you download the correct tag (e.g., `cp312` for Python 3.12, `cp313` for Python 3.13, and `win_amd64` for 64-bit Windows).
3. If you get a *"not supported wheel on this platform"* error, check your environment's active Python version via `python --version` and run `pip debug --verbose` to view compatible platform tags.
4. Install the matching wheel file directly, for example: `pip install llama_cpp_python-0.3.19-cp313-cp313-win_amd64.whl`.

### Step 2.4: Setup Configuration Environments
Create a `.env` file in the root folder containing the following variables:
```env
DATABASE_URL=postgresql://postgres:admin@localhost:5432/project_aura
OFFLINE_MODEL_HOME=C:\Users\Roni\Documents\Python Projects\Offline models
PROJECT_ROOT=C:\Users\Roni\Documents\GitHub\project-aura
```
- Ensure database credentials (`postgres/admin`) and PostgreSQL port match your local database settings.
- Ensure the offline model path correctly points to the directory containing model binaries.

### Step 2.5: Setup Offline Model Binaries
Ensure the following models are downloaded and placed in the folder designated by `OFFLINE_MODEL_HOME`:
1. **SentenceTransformer Embeddings:** `all-MiniLM-L6-v2` directory.
2. **Gemma LLM GGUF:** `gemma-2-2b-it-Q4_K_M.gguf`.
3. **Phi Fallback LLM GGUF:** `Phi-3.1-mini-4k-instruct-Q4_K_M.gguf`.

---

## 3. Database Initialization (Manual Seed)

If the automated settings installer fails, you can initialize the PostgreSQL schemas and collections manually.
Create a temporary Python script `manual_seed.py` in the root folder:

```python
import os
import shutil
from sqlalchemy import create_engine
from app.database import Base, JobStatus
from app.vector_store import client as chroma_client, embedding_function

# PostgreSQL Connection
DB_URL = "postgresql://postgres:admin@localhost:5432/project_aura"
engine = create_engine(DB_URL)

print("Dropping old database tables...")
Base.metadata.drop_all(bind=engine)

print("Creating database tables...")
Base.metadata.create_all(bind=engine)

# Seed Initial Job Status
from sqlalchemy.orm import sessionmaker
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
db = SessionLocal()
try:
    db.add(JobStatus(job_name="macro_categorization", is_running=False, total_items=0, processed_items=0))
    db.add(JobStatus(job_name="bulk_triage", is_running=False, total_items=0, processed_items=0))
    db.commit()
    print("Database tables initialized and seeded successfully.")
finally:
    db.close()

# Recreate ChromaDB Collections
print("Recreating ChromaDB collections...")
try:
    chroma_client.delete_collection("incidents")
except:
    pass
try:
    chroma_client.delete_collection("knowledge_base")
except:
    pass

chroma_client.get_or_create_collection("incidents", embedding_function=embedding_function, metadata={"hnsw:space": "l2"})
chroma_client.get_or_create_collection("knowledge_base", embedding_function=embedding_function, metadata={"hnsw:space": "l2"})
print("Vector store collections successfully initialized.")

# Recreate Physical Folders
for folder in ["./knowledge_docs/pending", "./knowledge_docs/processed", "./knowledge_docs/failed"]:
    if os.path.exists(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
print("Physical manuals curation folders successfully cleared.")
```

Run the seed script:
```powershell
python manual_seed.py
```

---

## 4. Launching the Application
Execute the batch script `run_app.bat` or run:
```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Open `http://localhost:8000/` in your browser.

---

## 5. Adding a New LLM Model to the Application Scope

To incorporate a new GGUF large language model (e.g., `TinyLlama-1.1B-Chat-v1.0.Q4_K_M.gguf`) as your primary or fallback language model, follow these steps:

### Step 5.1: Download and Place the Model Binary
1. Obtain the `.gguf` file of the target model (e.g. from Hugging Face).
2. Place the file inside your local offline model home directory (defined by the `OFFLINE_MODEL_HOME` variable in your `.env` file):
   ```text
   C:\Users\Roni\Documents\Python Projects\Offline models\TinyLlama-1.1B-Chat-v1.0.Q4_K_M.gguf
   ```

### Step 5.2: Register the Model in `config.json`
Open `config.json` in the project root folder. Add the model definition to the `models` list:
```json
{
  "models": [
    {
      "name": "TinyLlama-1.1B-Chat-v1.0.Q4_K_M.gguf",
      "type": "llama",
      "Active": true
    },
    {
      "name": "gemma-2-2b-it-Q4_K_M.gguf",
      "type": "gemma",
      "Active": false
    },
    {
      "name": "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf",
      "type": "phi",
      "Active": false
    }
  ]
}
```
*Note: Keep only **one** model with `"Active": true` in the list. The application parses the active model at startup.*

### Step 5.3: Ensure the Prompt Format is Mapped
If the model uses a different prompt format (e.g. Llama/TinyLlama `<|im_start|>` syntax vs Gemma `<start_of_turn>` syntax), ensure the prompt orchestrator mappings in `app/models_orchestrator.py` support the model `"type"` (e.g. `"llama"`, `"gemma"`, or `"phi"`). The orchestrator automatically applies standard chat format templates based on this type attribute.

