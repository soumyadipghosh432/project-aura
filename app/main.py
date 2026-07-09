import os
import csv
import json
import shutil
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from threading import Thread
import numpy as np

from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.config import OFFLINE_MODEL_HOME, BATCH_LIMIT
from app.database import init_db, get_db, Incident, KnowledgeDocument, Category, IncidentSearch, TelemetryLog, JobStatus, DeletedAction
from app.telemetry import telemetry_span, start_trace, clear_trace, trace_id_var
from app.parsers import parse_csv_file, parse_pdf_document, parse_docx_document, chunk_text, calculate_sha256
from app.vector_store import (
    add_incident_chunks, add_knowledge_chunks, delete_knowledge_by_file,
    delete_incidents_by_number, query_incidents, query_knowledge, reset_vector_store
)
from app.models_orchestrator import model_manager

# Ensure knowledge document folders exist
PENDING_DIR = "./knowledge_docs/pending"
PROCESSED_DIR = "./knowledge_docs/processed"
FAILED_DIR = "./knowledge_docs/failed"
DELETED_DIR = "./knowledge_docs/deleted"

for d in [PENDING_DIR, PROCESSED_DIR, FAILED_DIR, DELETED_DIR]:
    os.makedirs(d, exist_ok=True)

# Lifespan context manager for startup and shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Database Tables
    init_db()
    print("Database tables initialized successfully.")
    
    # Initialize LLM Model
    model_manager.load_model()
    yield

app = FastAPI(title="Project Aura", lifespan=lifespan)

# Mount static and templates folders
# Templates is in app/templates
# Static is in app/static
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(PROJECT_ROOT, "app", "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(PROJECT_ROOT, "app", "templates"))

# --- BACKGROUND EXECUTION WRAPPERS ---

def background_vectorize_incidents(parent_trace_id=None):
    """Background thread function to process PENDING incidents and vectorize them in ChromaDB."""
    db = next(get_db())
    if parent_trace_id:
        trace_id_var.set(parent_trace_id)
    else:
        start_trace() # Initialize thread-local trace ID
    try:
        with telemetry_span("incident_vectorization") as span:
            pending = db.query(Incident).filter(Incident.process_status == "PENDING").all()
            if not pending:
                span.message = "No pending incidents found for vectorization."
                return
            
            added_count = 0
            for inc in pending:
                try:
                    with telemetry_span("single_incident_vectorization") as sub_span:
                        text_content = f"{inc.short_desc or ''} {inc.description or ''} {inc.work_notes or ''} {inc.closed_note or ''}".strip()
                        if not text_content:
                            inc.process_status = "PROCESSED" # nothing to embed
                            continue
                            
                        chunks = chunk_text(text_content, max_tokens=500, overlap=75)
                        ids = [f"{inc.number}_chunk_{i}" for i in range(len(chunks))]
                        metadatas = [{"number": inc.number, "cmdb_ci": inc.cmdb_ci or "", "chunk_id": chunk_id} for chunk_id in ids]
                        
                        add_incident_chunks(chunks, ids, metadatas)
                        inc.process_status = "PROCESSED"
                        added_count += 1
                        sub_span.message = f"Vectorized ticket {inc.number} | CMDB CI: '{inc.cmdb_ci or ''}' | Description: '{inc.short_desc or ''}'"
                except Exception as inc_err:
                    print(f"Error vectorizing incident {inc.number}: {inc_err}")
                    inc.process_status = "FAILED"
                    sub_span.message = f"Failed to vectorize ticket {inc.number}: {str(inc_err)}"
                    
            db.commit()
            span.message = f"Successfully vectorized {added_count} incidents into ChromaDB."
    except Exception as e:
        print(f"Error in background incident vectorization: {e}")
    finally:
        clear_trace()
        db.close()

def background_macro_categorization(parent_trace_id=None):
    """Background thread function to run categorization over unclassified incidents."""
    db = next(get_db())
    if parent_trace_id:
        trace_id_var.set(parent_trace_id)
    else:
        start_trace()
    try:
        # Set job status to running in DB
        job = db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").first()
        if not job:
            job = JobStatus(job_name="macro_categorization", is_running=True, total_items=0, processed_items=0)
            db.add(job)
        else:
            job.is_running = True
            job.total_items = 0
            job.processed_items = 0
        db.commit()
        
        with telemetry_span("macro_categorization") as span:
            # Query unclassified incidents
            unclassified = db.query(Incident).filter(
                Incident.process_status == "PROCESSED"
            ).filter(
                ~Incident.number.in_(db.query(Category.number))
            ).limit(BATCH_LIMIT).all()
            
            if not unclassified:
                span.message = "No unclassified incidents to process."
                return
                
            total = len(unclassified)
            db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").update({
                "total_items": total,
                "processed_items": 0
            })
            db.commit()
            
            processed_count = 0
            for inc in unclassified:
                with telemetry_span("single_incident_categorization") as sub_span:
                    prompt_user = (
                        f"Categorize the following IT support incident into exactly one of these predefined categories: "
                        f"Access, Failure in Service, User Knowledge Gap, Application Error, Data/Missing Files Issues. "
                        f"If the incident does not fit the baseline categories, you are encouraged to dynamically define a specific, concise new category (e.g. Network Connectivity, Hardware Deployment, License Renewal, Security Violation) that describes the incident.\n"
                        f"Additionally, extract any error codes and key error messages from the text if they exist.\n\n"
                        f"Incident Details:\n"
                        f"Short description: {inc.short_desc or 'None'}\n"
                        f"Description: {inc.description or 'None'}\n"
                        f"Closed note: {inc.closed_note or 'None'}\n\n"
                        f"You must return your response strictly in the following JSON format without any markdown wrappers or text padding:\n"
                        f"{{\n"
                        f"  \"category\": \"assigned category\",\n"
                        f"  \"error_codes\": \"comma-separated error codes or None\",\n"
                        f"  \"error_messages\": \"comma-separated error messages or None\"\n"
                        f"}}"
                    )
                    
                    system_prompt = "You are an expert IT system incident categorization classifier. You output clean raw JSON data."
                    
                    try:
                        raw_response = model_manager.generate_completion(
                            user_prompt=prompt_user,
                            system_prompt=system_prompt,
                            max_tokens=256,
                            temperature=0.0
                        )
                        
                        clean_text = raw_response.strip()
                        if clean_text.startswith("```"):
                            lines = clean_text.splitlines()
                            if lines[0].startswith("```json") or lines[0].startswith("```"):
                                lines = lines[1:-1]
                            clean_text = "\n".join(lines).strip()
                        
                        # Parse response
                        data = json.loads(clean_text)
                        cat_name = data.get("category", "Unclassified").strip()
                        err_codes = data.get("error_codes", "None").strip()
                        err_msgs = data.get("error_messages", "None").strip()
                        
                        cat_record = Category(
                            number=inc.number,
                            assigned_cat=cat_name,
                            error_codes=err_codes,
                            error_msgs=err_msgs
                        )
                        db.add(cat_record)
                        processed_count += 1
                        sub_span.message = f"Classified ticket {inc.number} to category '{cat_name}' | Error messages: '{err_msgs}'"
                    except Exception as err:
                        print(f"Error categorizing {inc.number}: {err}")
                        # Write fallback
                        cat_record = Category(
                            number=inc.number,
                            assigned_cat="Unclassified",
                            error_codes="None",
                            error_msgs=f"Error classifying: {str(err)}"
                        )
                        db.add(cat_record)
                        sub_span.message = f"Failed to classify ticket {inc.number}: {str(err)}"
                    
                    # Gradual progress updates committed to database
                    db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").update({
                        "processed_items": processed_count
                    })
                    db.commit()
                    
            span.message = f"Macro Categorization processed {processed_count} incidents."
    except Exception as e:
        print(f"Error in macro categorization background thread: {e}")
    finally:
        # Reset job status
        job = db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").first()
        if job:
            job.is_running = False
            db.commit()
        clear_trace()
        db.close()


# --- HTML PAGE ROUTINGS ---

@app.get("/", response_class=HTMLResponse)
async def homepage_dashboard(request: Request, db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("view_homepage"):
        # 1. Incident Ingestion Counters
        total_processed = db.query(Incident).filter(Incident.process_status == "PROCESSED").count()
        total_pending = db.query(Incident).filter(Incident.process_status == "PENDING").count()
        total_failed = db.query(Incident).filter(Incident.process_status == "FAILED").count()
        
        # 2. Knowledge Base Metrics
        kb_processed = db.query(KnowledgeDocument).count()
        kb_failed = len(os.listdir(FAILED_DIR)) if os.path.exists(FAILED_DIR) else 0
        
        # 3. Search Performance Telemetry
        total_searches = db.query(TelemetryLog).filter(TelemetryLog.event_name == "guidance_query").count()
        successful_searches = db.query(TelemetryLog).filter(
            TelemetryLog.event_name == "guidance_query"
        ).filter(
            TelemetryLog.status == "SUCCESS"
        ).count()
        success_rate = (successful_searches / total_searches * 100.0) if total_searches > 0 else 100.0
        
        # 4. Latency & Compute Profiling (in seconds)
        # Search & RAG Latencies
        search_logs = db.query(TelemetryLog).filter(TelemetryLog.event_name == "guidance_query").all()
        search_latencies = [l.duration_ms for l in search_logs if l.duration_ms is not None]
        avg_latency_sec = (sum(search_latencies) / len(search_latencies) / 1000.0) if search_latencies else 0.0
        p90_latency_sec = (np.percentile(search_latencies, 90) / 1000.0) if search_latencies else 0.0
        
        # Ingestion (per-incident vectorization) Latencies
        ingest_logs = db.query(TelemetryLog).filter(TelemetryLog.event_name == "single_incident_vectorization").all()
        ingest_latencies = [l.duration_ms for l in ingest_logs if l.duration_ms is not None]
        avg_ingest_sec = (sum(ingest_latencies) / len(ingest_latencies) / 1000.0) if ingest_latencies else 0.0
        
        # Categorization (per-incident categorization) Latencies
        categ_logs = db.query(TelemetryLog).filter(TelemetryLog.event_name == "single_incident_categorization").all()
        categ_latencies = [l.duration_ms for l in categ_logs if l.duration_ms is not None]
        avg_categ_sec = (sum(categ_latencies) / len(categ_latencies) / 1000.0) if categ_latencies else 0.0
        
        total_tokens = db.query(func.sum(TelemetryLog.token_count)).scalar() or 0
        
        system_status = {
            "inc_processed": total_processed,
            "inc_pending": total_pending,
            "inc_failed": total_failed,
            "kb_processed": kb_processed,
            "kb_failed": kb_failed,
            "total_searches": total_searches,
            "success_rate": round(success_rate, 2),
            "avg_latency_sec": round(avg_latency_sec, 3),
            "p90_latency_sec": round(p90_latency_sec, 3),
            "avg_ingest_sec": round(avg_ingest_sec, 3),
            "avg_categ_sec": round(avg_categ_sec, 3),
            "total_tokens": total_tokens,
            "active_model": model_manager.active_model_name,
            "is_fallback": model_manager.is_fallback_active
        }
        
        return templates.TemplateResponse(request=request, name="homepage.html", context={"status": system_status})

@app.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request, doc_page: int = 1, db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("view_knowledge"):
        total_docs_count = db.query(KnowledgeDocument).count()
        failed_docs_count = len(os.listdir(FAILED_DIR)) if os.path.exists(FAILED_DIR) else 0
        
        # Paginated docs list (10 per page)
        limit = 10
        offset = (doc_page - 1) * limit
        all_docs = db.query(KnowledgeDocument).order_by(KnowledgeDocument.processed_date.desc()).offset(offset).limit(limit).all()
        
        total_pages = max(1, (total_docs_count + limit - 1) // limit)
        
        pending_incs = db.query(Incident).filter(Incident.process_status == "PENDING").count()
        processed_incs = db.query(Incident).filter(Incident.process_status == "PROCESSED").count()
        
        # Distinct CMDB CIs for document upload form dropdown
        cmdb_cis = [item[0] for item in db.query(Incident.cmdb_ci).distinct().all() if item[0]]
        
        return templates.TemplateResponse(
            request=request, 
            name="knowledge.html", 
            context={
                "all_docs": all_docs, 
                "total_docs_count": total_docs_count, 
                "failed_docs_count": failed_docs_count,
                "pending_incs": pending_incs, 
                "processed_incs": processed_incs,
                "cmdb_cis": cmdb_cis,
                "current_page": doc_page,
                "total_pages": total_pages
            }
        )

@app.get("/categorization", response_class=HTMLResponse)
async def categorization_page(
    request: Request, 
    start_date: str = None, 
    end_date: str = None, 
    cmdb_ci: str = None, 
    db: Session = Depends(get_db)
):
    start_trace()
    with telemetry_span("view_categorization"):
        # Check running status
        job = db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").first()
        is_running = job.is_running if job else False
        
        # Unclassified count with filters
        unclass_query = db.query(Incident).filter(
            Incident.process_status == "PROCESSED"
        ).filter(
            ~Incident.number.in_(db.query(Category.number))
        )
        if cmdb_ci:
            unclass_query = unclass_query.filter(Incident.cmdb_ci == cmdb_ci)
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                unclass_query = unclass_query.filter(Incident.sys_created_on >= sd)
            except ValueError:
                pass
        if end_date:
            try:
                ed = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                unclass_query = unclass_query.filter(Incident.sys_created_on <= ed)
            except ValueError:
                pass
        unclassified_count = unclass_query.count()
        
        # Categorized breakdown with filters
        query = db.query(
            Category.assigned_cat, 
            func.count(Category.id)
        ).join(Incident, Category.number == Incident.number)
        
        if cmdb_ci:
            query = query.filter(Incident.cmdb_ci == cmdb_ci)
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                query = query.filter(Incident.sys_created_on >= sd)
            except ValueError:
                pass
        if end_date:
            try:
                ed = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                query = query.filter(Incident.sys_created_on <= ed)
            except ValueError:
                pass
                
        breakdown = query.group_by(Category.assigned_cat).all()
        
        # Explanations dictionary
        baseline_explanations = {
            "Access": "Login failures, lockouts, password resets, active directory, privilege issues.",
            "Failure in Service": "Server outage, slow processing, hardware crash, unexpected system shutdowns.",
            "User Knowledge Gap": "How-to requests, instruction guides, general user inquiries, settings changes.",
            "Application Error": "Crashes, runtime errors, bugs, interface issues, UI display failures.",
            "Data/Missing Files Issues": "Integrity checks, corrupt files, database mismatch, file loading exceptions."
        }
        
        chart_data = []
        for cat_name, count in breakdown:
            desc = baseline_explanations.get(cat_name)
            if not desc:
                # Dynamically retrieve sample error messages for dynamic categories
                samples = db.query(Category.error_msg).filter(
                    Category.assigned_cat == cat_name
                ).filter(Category.error_msg != "None").limit(3).all()
                sample_msgs = [s[0] for s in samples if s[0]]
                if sample_msgs:
                    desc = f"Dynamically generated operational category. Linked to error messages: {', '.join(sample_msgs)}."
                else:
                    desc = "Dynamically generated category from incident analysis."
            
            chart_data.append({
                "category": cat_name,
                "count": count,
                "description": desc
            })
            
        # CMDB CI list for filters (always distinct across all incidents)
        cmdb_cis = [item[0] for item in db.query(Incident.cmdb_ci).distinct().all() if item[0]]
        
        return templates.TemplateResponse(
            request=request, 
            name="categorization.html", 
            context={
                "is_running": is_running, 
                "unclassified_count": unclassified_count,
                "chart_data": chart_data,
                "cmdb_cis": cmdb_cis,
                "batch_limit": BATCH_LIMIT,
                "start_date": start_date,
                "end_date": end_date,
                "cmdb_ci": cmdb_ci
            }
        )

@app.get("/guidance", response_class=HTMLResponse)
async def guidance_page(request: Request, db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("view_guidance"):
        cmdb_cis = [item[0] for item in db.query(Incident.cmdb_ci).distinct().all() if item[0]]
        return templates.TemplateResponse(request=request, name="guidance.html", context={"cmdb_cis": cmdb_cis})

@app.get("/help", response_class=HTMLResponse)
async def help_page(request: Request):
    start_trace()
    with telemetry_span("view_help"):
        return templates.TemplateResponse(request=request, name="help.html", context={})


# --- API ENDPOINTS ---

@app.post("/api/ingest/incidents")
async def ingest_incidents(file: UploadFile = File(...), db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("incident_ingestion") as span:
        try:
            content_bytes = await file.read()
            content_str = content_bytes.decode("utf-8")
            
            parsed = parse_csv_file(content_str)
            
            added_or_updated = 0
            for record in parsed:
                num = record["number"]
                existing = db.query(Incident).filter(Incident.number == num).first()
                
                if existing:
                    if existing.process_status == "PROCESSED":
                        continue # skip already processed records
                    else:
                        # Overwrite columns
                        existing.cmdb_ci = record["cmdb_ci"]
                        existing.short_desc = record["short_description"]
                        existing.description = record["description"]
                        existing.work_notes = record["comments_and_work_notes"]
                        existing.closed_note = record["closed_note"]
                        existing.assigned_to = record["assigned_to"]
                        existing.assignment_group = record["assignment_group"]
                        existing.sys_created_on = record["sys_created_on_dt"]
                        existing.process_status = "PENDING"
                        added_or_updated += 1
                else:
                    # Insert new
                    new_inc = Incident(
                        number=num,
                        cmdb_ci=record["cmdb_ci"],
                        short_desc=record["short_description"],
                        description=record["description"],
                        work_notes=record["comments_and_work_notes"],
                        closed_note=record["closed_note"],
                        assigned_to=record["assigned_to"],
                        assignment_group=record["assignment_group"],
                        sys_created_on=record["sys_created_on_dt"],
                        process_status="PENDING"
                    )
                    db.add(new_inc)
                    added_or_updated += 1
                    
            db.commit()
            
            # Spawn background thread for vector processing
            parent_trace_id = trace_id_var.get()
            Thread(target=background_vectorize_incidents, args=(parent_trace_id,)).start()
            
            span.message = f"Ingested {len(parsed)} CSV rows. Scheduled {added_or_updated} for vectorization."
            return {"status": "SUCCESS", "message": span.message}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/ingest/knowledge")
async def ingest_knowledge(file: UploadFile = File(...), db: Session = Depends(get_db)):
    start_trace()
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in [".docx", ".pdf"]:
        raise HTTPException(status_code=400, detail="Only .docx and .pdf files are accepted.")
        
    with telemetry_span("knowledge_ingestion") as span:
        # Save temp file
        pending_path = os.path.join(PENDING_DIR, filename)
        processed_path = os.path.join(PROCESSED_DIR, filename)
        failed_path = os.path.join(FAILED_DIR, filename)
        
        try:
            content_bytes = await file.read()
            with open(pending_path, "wb") as f:
                f.write(content_bytes)
                
            hash_val = calculate_sha256(content_bytes)
            
            # Hash exists check
            dup_hash = db.query(KnowledgeDocument).filter(KnowledgeDocument.hash_value == hash_val).first()
            if dup_hash:
                os.remove(pending_path)
                span.message = f"File duplicate skipped by SHA-256 hash match: {filename}."
                return {"status": "SKIPPED", "message": span.message}
                
            # Filename exists check
            dup_name = db.query(KnowledgeDocument).filter(KnowledgeDocument.filename == filename).first()
            if dup_name:
                # Delete old vector chunks
                delete_knowledge_by_file(filename)
                # Delete DB row
                db.delete(dup_name)
                # Delete old physical file from processed
                if os.path.exists(processed_path):
                    os.remove(processed_path)
                db.commit()
                print(f"Overwriting old file: {filename}.")
                
            # Parsing and chunking
            chunks = []
            if ext == ".pdf":
                pages = parse_pdf_document(pending_path)
                for page_num, text in pages:
                    page_chunks = chunk_text(text, max_tokens=500, overlap=75)
                    for c_idx, c_text in enumerate(page_chunks):
                        chunks.append({
                            "text": c_text,
                            "metadata": {"filename": filename, "page_number": page_num},
                            "id": f"{filename}_page_{page_num}_chunk_{c_idx}"
                        })
            else: # .docx
                full_text = parse_docx_document(pending_path)
                docx_chunks = chunk_text(full_text, max_tokens=500, overlap=75)
                for c_idx, c_text in enumerate(docx_chunks):
                    chunks.append({
                        "text": c_text,
                        "metadata": {"filename": filename},
                        "id": f"{filename}_chunk_{c_idx}"
                    })
                    
            if chunks:
                doc_texts = [c["text"] for c in chunks]
                doc_ids = [c["id"] for c in chunks]
                doc_metas = [c["metadata"] for c in chunks]
                
                # Load embeddings and add to collection
                add_knowledge_chunks(doc_texts, doc_ids, doc_metas)
                
            # Move to processed
            shutil.move(pending_path, processed_path)
            
            # Save document
            new_doc = KnowledgeDocument(filename=filename, hash_value=hash_val)
            db.add(new_doc)
            db.commit()
            
            span.message = f"Successfully parsed and vectorized {filename}."
            return {"status": "SUCCESS", "message": span.message}
        except Exception as e:
            db.rollback()
            if os.path.exists(pending_path):
                shutil.move(pending_path, failed_path)
            elif os.path.exists(processed_path):
                shutil.move(processed_path, failed_path)
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/analysis/categorize")
async def trigger_categorization(db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("trigger_categorization"):
        job = db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").first()
        is_running = job.is_running if job else False
        
        if is_running:
            return {"status": "RUNNING", "message": "Categorization is already in progress."}
            
        # Spawn macro categorization in a separate thread
        parent_trace_id = trace_id_var.get()
        Thread(target=background_macro_categorization, args=(parent_trace_id,)).start()
        
        return {"status": "STARTED", "message": "Categorization engine started successfully."}

@app.get("/api/analysis/job-status")
async def get_job_status(db: Session = Depends(get_db)):
    job = db.query(JobStatus).filter(JobStatus.job_name == "macro_categorization").first()
    total_items = job.total_items if job else 0
    processed_items = job.processed_items if job else 0
    
    return {
        "is_running": is_running,
        "unclassified_count": unclassified_count,
        "total_items": total_items,
        "processed_items": processed_items
    }

@app.get("/api/analysis/export-csv")
async def export_categorization_csv(
    start_date: str = None, 
    end_date: str = None, 
    cmdb_ci: str = None, 
    db: Session = Depends(get_db)
):
    start_trace()
    with telemetry_span("export_csv"):
        query = db.query(Category.number, Category.assigned_cat).join(
            Incident, Incident.number == Category.number
        )
        
        if cmdb_ci:
            query = query.filter(Incident.cmdb_ci == cmdb_ci)
            
        if start_date:
            try:
                sd = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                query = query.filter(Incident.sys_created_on >= sd)
            except ValueError:
                pass
                
        if end_date:
            try:
                ed = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                query = query.filter(Incident.sys_created_on <= ed)
            except ValueError:
                pass
                
        results = query.all()
        
        # Write to temporary file
        export_file = os.path.join(PROJECT_ROOT, "app", "static", "categorization_export.csv")
        with open(export_file, "w", encoding="utf-8") as f:
            f.write("number,assigned_category\n")
            for num, cat in results:
                f.write(f'"{num}","{cat}"\n')
                
        return FileResponse(
            export_file, 
            media_type="text/csv", 
            filename="incident_categories.csv"
        )

@app.get("/api/analysis/guidance")
async def search_guidance(
    query_text: str, 
    cmdb_ci: str, 
    min_threshold: float = 10.0, 
    parent_trace_id: str = None,
    db: Session = Depends(get_db)
):
    if parent_trace_id:
        trace_id_var.set(parent_trace_id)
    else:
        start_trace()
    with telemetry_span("guidance_query") as span:
        try:
            # Query incidents collection
            inc_results = query_incidents(query_text, cmdb_ci, n_results=10)
            
            # Map distances to match scores
            matched_records = []
            ids = inc_results.get("ids", [[]])[0]
            distances = inc_results.get("distances", [[]])[0]
            metadatas = inc_results.get("metadatas", [[]])[0]
            
            seen_incidents = set()
            
            for doc_id, dist, meta in zip(ids, distances, metadatas):
                score = max(0.0, (1.0 - dist / 2.0) * 100.0)
                if score < min_threshold:
                    continue
                    
                inc_num = meta.get("number")
                if inc_num in seen_incidents:
                    continue
                seen_incidents.add(inc_num)
                
                # Fetch raw data from DB
                inc_db = db.query(Incident).filter(Incident.number == inc_num).first()
                if inc_db:
                    matched_records.append({
                        "number": inc_db.number,
                        "cmdb_ci": inc_db.cmdb_ci,
                        "short_desc": inc_db.short_desc,
                        "description": inc_db.description,
                        "closed_note": inc_db.closed_note,
                        "assigned_to": inc_db.assigned_to,
                        "assignment_group": inc_db.assignment_group,
                        "match_score": round(score, 2)
                    })
                    
                if len(matched_records) >= 5:
                    break # cap at top 5 deduplicated matching incidents
                    
            # Query knowledge base collection
            kb_results = query_knowledge(query_text, n_results=4)
            kb_ids = kb_results.get("ids", [[]])[0]
            kb_docs = kb_results.get("documents", [[]])[0]
            kb_metas = kb_results.get("metadatas", [[]])[0]
            kb_distances = kb_results.get("distances", [[]])[0]
            
            combined_docs = []
            citations = []
            seen_citations = set()
            
            for doc_text, meta, dist in zip(kb_docs, kb_metas, kb_distances):
                score = max(0.0, (1.0 - dist / 2.0) * 100.0)
                if score < min_threshold:
                    continue # Discard low-confidence documentation chunks below the threshold
                    
                combined_docs.append(doc_text)
                fn = meta.get("filename")
                if fn and fn not in seen_citations:
                    seen_citations.add(fn)
                    citations.append(fn)
                    
            # Synthesis Phase (RAG)
            rag_output = ""
            tokens_consumed = 0
            if combined_docs:
                doc_context = "\n\n".join(combined_docs)
                prompt_user = (
                    f"Synthesize a clear resolution guide for the following user issue using the provided offline documentation chunks.\n"
                    f"Be concise, step-by-step, and reference source file names (citations) directly where applicable.\n\n"
                    f"User Issue: {query_text}\n"
                    f"CMDB CI: {cmdb_ci}\n\n"
                    f"Documentation Chunks:\n"
                    f"{doc_context}"
                )
                system_prompt = (
                    "You are a helpful IT support synthesis agent. "
                    "You provide unified instruction guides based strictly on documentation context. "
                    "If documentation does not provide instructions, synthesize a generic troubleshooting flow."
                )
                
                # Run LLM
                rag_output = model_manager.generate_completion(
                    user_prompt=prompt_user,
                    system_prompt=system_prompt,
                    max_tokens=512,
                    temperature=0.1
                )
            else:
                rag_output = "No matching documentation found. Proceed with standard diagnostics."
                
            # Log Search result to database
            new_search = IncidentSearch(
                query_text=query_text,
                matched_incs=matched_records,
                rag_response=rag_output,
                citations=citations
            )
            db.add(new_search)
            db.commit()
            
            # If no matches are found, we classify it as NO_MATCH for success rate analytics
            has_matches = (len(matched_records) > 0 or len(citations) > 0)
            span.status = "SUCCESS" if has_matches else "NO_MATCH"
            
            span.message = f"Search query: '{query_text}' | CMDB CI: '{cmdb_ci}' | Matches: {len(matched_records)} incidents, {len(citations)} manuals"
            
            return {
                "matched_incidents": matched_records,
                "rag_response": rag_output,
                "citations": citations
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

BULK_TRIAGE_RUNS = {}

async def async_bulk_triage_worker(job_id: str, parsed_rows: list, parent_trace_id: str, db: Session):
    if parent_trace_id:
        trace_id_var.set(parent_trace_id)
    try:
        results = []
        total = len(parsed_rows)
        for idx, item in enumerate(parsed_rows):
            q_text = item["description"]
            cmdb_ci = item["cmdb_ci"]
            
            guidance = await search_guidance(
                query_text=q_text,
                cmdb_ci=cmdb_ci,
                min_threshold=10.0,
                parent_trace_id=parent_trace_id,
                db=db
            )
            
            results.append({
                "query": q_text,
                "cmdb_ci": cmdb_ci,
                "matched_incidents": guidance["matched_incidents"],
                "rag_response": guidance["rag_response"],
                "citations": guidance["citations"]
            })
            
            # Update gradual progress
            BULK_TRIAGE_RUNS[job_id]["processed"] = idx + 1
            db.query(JobStatus).filter(JobStatus.job_name == "bulk_triage").update({
                "processed_items": idx + 1
            })
            db.commit()
            
        BULK_TRIAGE_RUNS[job_id].update({
            "status": "COMPLETED",
            "results": results
        })
        
        # Reset DB job status
        db.query(JobStatus).filter(JobStatus.job_name == "bulk_triage").update({
            "is_running": False,
            "processed_items": total
        })
        db.commit()
    except Exception as e:
        print(f"Error in async bulk triage background task: {e}")
        BULK_TRIAGE_RUNS[job_id].update({
            "status": "FAILED",
            "error": str(e)
        })
        db.query(JobStatus).filter(JobStatus.job_name == "bulk_triage").update({
            "is_running": False
        })
        db.commit()

@app.post("/api/analysis/bulk-triage")
async def bulk_triage(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    import secrets
    start_trace()
    parent_trace_id = trace_id_var.get()
    
    with telemetry_span("bulk_triage") as span:
        try:
            content_bytes = await file.read()
            content_str = content_bytes.decode("utf-8")
            
            lines = content_str.splitlines()
            if not lines:
                raise ValueError("Empty CSV file")
                
            reader = csv.reader(lines)
            rows = list(reader)
            headers = [h.strip().lower() for h in rows[0]]
            
            if "description" not in headers or "cmdb_ci" not in headers:
                raise ValueError("CSV must contain 'description' and 'cmdb_ci' column headers.")
                
            desc_idx = headers.index("description")
            ci_idx = headers.index("cmdb_ci")
            
            parsed_rows = []
            for row in rows[1:]:
                if not row or all(not cell.strip() for cell in row):
                    continue
                parsed_rows.append({
                    "description": row[desc_idx].strip(),
                    "cmdb_ci": row[ci_idx].strip()
                })
                if len(parsed_rows) >= 20: # Cap at max 20 per batch for stability
                    break
                    
            if not parsed_rows:
                raise ValueError("No valid rows found in CSV.")
                
            job_id = secrets.token_hex(16)
            BULK_TRIAGE_RUNS[job_id] = {
                "status": "RUNNING",
                "total": len(parsed_rows),
                "processed": 0,
                "results": [],
                "error": None
            }
            
            # Update DB status
            db.query(JobStatus).filter(JobStatus.job_name == "bulk_triage").update({
                "is_running": True,
                "total_items": len(parsed_rows),
                "processed_items": 0
            })
            db.commit()
            
            background_tasks.add_task(
                async_bulk_triage_worker,
                job_id,
                parsed_rows,
                parent_trace_id,
                db
            )
            
            span.message = f"Enqueued bulk triage job with {len(parsed_rows)} rows."
            return {"job_id": job_id, "status": "RUNNING", "total": len(parsed_rows)}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/analysis/bulk-triage/status/{job_id}")
async def get_bulk_triage_status(job_id: str):
    if job_id not in BULK_TRIAGE_RUNS:
        raise HTTPException(status_code=404, detail="Bulk triage job not found")
    return BULK_TRIAGE_RUNS[job_id]

@app.post("/api/knowledge/delete-doc")
async def delete_knowledge_doc(payload: dict, db: Session = Depends(get_db)):
    start_trace()
    filename = payload.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="Filename parameter is required.")
        
    with telemetry_span("delete_knowledge_doc") as span:
        try:
            # 1. Fetch document from PostgreSQL DB
            doc = db.query(KnowledgeDocument).filter(KnowledgeDocument.filename == filename).first()
            if not doc:
                raise HTTPException(status_code=404, detail=f"Knowledge document '{filename}' not found in database.")
                
            # 2. Clear vector chunks from ChromaDB collection
            delete_knowledge_by_file(filename)
            
            # 3. Delete from DB
            db.delete(doc)
            
            # 4. Log in deleted_actions
            action_details = f"Cleaned all vector chunks from ChromaDB 'knowledge_base' collection and removed DB row. File moved to deleted directory."
            action_log = DeletedAction(
                item_type="KNOWLEDGE_DOCUMENT",
                item_identifier=filename,
                details=action_details
            )
            db.add(action_log)
            
            # 5. Move physical file to deleted folder
            processed_path = os.path.join(PROCESSED_DIR, filename)
            if os.path.exists(processed_path):
                deleted_path = os.path.join(DELETED_DIR, filename)
                if os.path.exists(deleted_path):
                    name, ext = os.path.splitext(filename)
                    suffix = datetime.now().strftime("_%Y%m%d_%H%M%S")
                    new_filename = f"{name}{suffix}{ext}"
                    target_path = os.path.join(DELETED_DIR, new_filename)
                else:
                    target_path = deleted_path
                shutil.move(processed_path, target_path)
                
            db.commit()
            span.message = f"Successfully deleted knowledge document '{filename}' and archived physical file."
            return {"status": "SUCCESS", "message": span.message}
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/incidents/delete-records")
async def delete_incident_records(payload: dict, db: Session = Depends(get_db)):
    start_trace()
    inc_input = payload.get("incidents", "")
    if not inc_input:
        raise HTTPException(status_code=400, detail="Incident numbers are required.")
        
    # Split comma-separated incident identifiers
    inc_numbers = [num.strip() for num in inc_input.split(",") if num.strip()]
    if not inc_numbers:
        raise HTTPException(status_code=400, detail="No valid incident numbers parsed.")
        
    with telemetry_span("delete_incident_records") as span:
        try:
            deleted_list = []
            not_found_list = []
            
            for number in inc_numbers:
                inc = db.query(Incident).filter(Incident.number == number).first()
                if inc:
                    # Delete vector index chunks from ChromaDB
                    delete_incidents_by_number(number)
                    
                    # Delete associated classifications/categories if they exist
                    category = db.query(Category).filter(Category.number == number).first()
                    if category:
                        db.delete(category)
                        
                    # Delete structured incident row
                    db.delete(inc)
                    
                    # Log deleted actions
                    action_details = f"Cleaned all vector chunks from ChromaDB 'incidents' collection, removed categories classification, and deleted SQL database rows."
                    action_log = DeletedAction(
                        item_type="INCIDENT",
                        item_identifier=number,
                        details=action_details
                    )
                    db.add(action_log)
                    deleted_list.append(number)
                else:
                    not_found_list.append(number)
                    
            db.commit()
            span.message = f"Deleted incidents: {', '.join(deleted_list)}. Not found: {', '.join(not_found_list)}."
            return {
                "status": "SUCCESS", 
                "message": f"Successfully processed deletions. Deleted: {len(deleted_list)}, Not Found: {len(not_found_list)}.",
                "deleted": deleted_list,
                "not_found": not_found_list
            }
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/settings/logs")
async def get_logs_insights(filter_type: str = "all", db: Session = Depends(get_db)):
    # 1. Find trace IDs of parent events matching our filter first:
    parent_names = []
    if filter_type == "doc_upload":
        parent_names = ["knowledge_ingestion"]
    elif filter_type == "inc_upload":
        parent_names = ["incident_ingestion", "incident_vectorization"]
    elif filter_type == "categorization":
        parent_names = ["macro_categorization", "single_incident_categorization", "trigger_categorization"]
    elif filter_type == "search":
        parent_names = ["guidance_query", "bulk_triage"]
    elif filter_type == "deletion":
        parent_names = []
        
    trace_ids = []
    if filter_type != "deletion":
        p_query = db.query(TelemetryLog)
        if parent_names:
            p_query = p_query.filter(TelemetryLog.event_name.in_(parent_names))
        else:
            parent_events = ["knowledge_ingestion", "incident_ingestion", "macro_categorization", "guidance_query", "reset_install_app", "bulk_triage", "incident_vectorization"]
            p_query = p_query.filter(TelemetryLog.event_name.in_(parent_events))
            
        parent_logs = p_query.order_by(TelemetryLog.timestamp.desc()).limit(30).all()
        seen_tids = set()
        for log in parent_logs:
            if log.trace_id and log.trace_id not in seen_tids:
                seen_tids.add(log.trace_id)
                trace_ids.append(log.trace_id)
        
    telemetry_results = []
    if trace_ids:
        telemetry_results = db.query(TelemetryLog).filter(TelemetryLog.trace_id.in_(trace_ids)).order_by(TelemetryLog.timestamp.asc()).all()
    elif filter_type == "all":
        telemetry_results = db.query(TelemetryLog).order_by(TelemetryLog.timestamp.desc()).limit(150).all()
        
    groups = {}
    for log in telemetry_results:
        tid = log.trace_id or "orphan"
        if tid not in groups:
            groups[tid] = {
                "trace_id": tid,
                "timestamp": None,
                "event_type": "SYSTEM ACTION",
                "identifier": tid[:8],
                "status": "SUCCESS",
                "details": "",
                "duration_ms": 0,
                "children": []
            }
            
        ev_type = log.event_name.upper().replace("_", " ")
        if log.event_name == "knowledge_ingestion":
            ev_type = "DOCUMENT UPLOAD"
        elif log.event_name == "incident_ingestion":
            ev_type = "INCIDENT DATA UPLOAD"
        elif log.event_name == "incident_vectorization":
            ev_type = "VECTOR INGESTION RUN"
        elif log.event_name == "single_incident_vectorization":
            ev_type = "TICKET VECTORIZED"
        elif log.event_name == "macro_categorization":
            ev_type = "CATEGORIZATION BATCH"
        elif log.event_name == "single_incident_categorization":
            ev_type = "TICKET CLASSIFIED"
        elif log.event_name == "guidance_query":
            ev_type = "SEARCH QUERY"
        elif log.event_name == "reset_install_app":
            ev_type = "SYSTEM RESET/INSTALL"
            
        span_data = {
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "event_name": log.event_name,
            "event_type": ev_type,
            "duration_ms": log.duration_ms or 0,
            "status": log.status,
            "details": log.message or "Processed step."
        }
        
        is_parent = log.event_name in ["knowledge_ingestion", "incident_ingestion", "macro_categorization", "guidance_query", "reset_install_app", "bulk_triage"]
        if is_parent or groups[tid]["timestamp"] is None:
            groups[tid]["timestamp"] = log.timestamp.isoformat() if log.timestamp else None
            groups[tid]["event_type"] = ev_type
            groups[tid]["status"] = log.status
            groups[tid]["details"] = log.message or "Executed transaction."
            groups[tid]["duration_ms"] = log.duration_ms or 0
        else:
            groups[tid]["children"].append(span_data)
            
    output_list = list(groups.values())
    
    if filter_type in ["all", "deletion"]:
        del_results = db.query(DeletedAction).order_by(DeletedAction.timestamp.desc()).limit(50).all()
        for log in del_results:
            ev_type = f"DELETION ({'DOC' if log.item_type == 'KNOWLEDGE_DOCUMENT' else 'INCIDENT'})"
            output_list.append({
                "trace_id": f"del-{log.id}",
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "event_type": ev_type,
                "identifier": log.item_identifier,
                "status": "DELETED",
                "details": log.details,
                "duration_ms": 0,
                "children": []
            })
            
    output_list.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    return output_list

@app.get("/api/settings/export-logs")
async def export_logs(filter_type: str = "all", db: Session = Depends(get_db)):
    logs = await get_logs_insights(filter_type=filter_type, db=db)
    
    import io
    import csv
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(["Timestamp", "Event Type", "Reference / Trace ID", "Status", "Details", "Duration (ms)"])
    
    for item in logs:
        writer.writerow([
            item["timestamp"],
            item["event_type"],
            item["identifier"],
            item["status"],
            item["details"],
            item["duration_ms"]
        ])
        
        for child in item.get("children", []):
            writer.writerow([
                child["timestamp"],
                f"  └─ {child['event_type']}",
                "",
                child["status"],
                child["details"],
                child["duration_ms"]
            ])
            
    from fastapi.responses import StreamingResponse
    output.seek(0)
    return StreamingResponse(
        io.StringIO(output.getvalue()),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=project_aura_logs_{filter_type}.csv"}
    )

@app.get("/files/{filename}")
async def serve_file(filename: str):
    """File server route to securely download processed documentation files."""
    filepath = os.path.join(PROCESSED_DIR, filename)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Requested file not found in processed repository.")
    return FileResponse(filepath, filename=filename)

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    start_trace()
    with telemetry_span("view_settings"):
        return templates.TemplateResponse(request=request, name="settings.html", context={})

@app.get("/api/settings/diagnostics")
async def get_diagnostics(db: Session = Depends(get_db)):
    # 1. DB check
    db_status = "CONNECTED"
    db_err = None
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "FAILED"
        db_err = str(e)
        
    # 2. Vector check
    vs_status = "ACTIVE"
    vs_err = None
    try:
        from app.vector_store import client as chroma_client
        chroma_client.list_collections()
    except Exception as e:
        vs_status = "FAILED"
        vs_err = str(e)
        
    # 3. Model files check
    m_status = "CONFIGURED"
    m_err = None
    active_model_found = None
    try:
        if not os.path.exists(OFFLINE_MODEL_HOME):
            raise FileNotFoundError(f"Model home directory not found: {OFFLINE_MODEL_HOME}")
        
        from app.config import active_model, fallback_model
        pm_path = os.path.join(OFFLINE_MODEL_HOME, active_model["name"])
        fm_path = os.path.join(OFFLINE_MODEL_HOME, fallback_model["name"])
        
        if not os.path.exists(pm_path):
            raise FileNotFoundError(f"Primary model file missing: {active_model['name']}")
        if not os.path.exists(fm_path):
            raise FileNotFoundError(f"Fallback model file missing: {fallback_model['name']}")
            
        active_model_found = active_model["name"]
    except Exception as e:
        m_status = "FAILED"
        m_err = str(e)
        
    return {
        "database": db_status,
        "db_error": db_err,
        "vector_store": vs_status,
        "vector_error": vs_err,
        "models": m_status,
        "model_error": m_err,
        "active_model_found": active_model_found
    }

@app.post("/api/settings/reset-install")
async def reset_install_app(db: Session = Depends(get_db)):
    start_trace()
    with telemetry_span("reset_install_app") as span:
        try:
            # 1. Close current DB session transactions
            db.rollback()
            
            # 2. Run validations
            try:
                db.execute(text("SELECT 1"))
            except Exception as e:
                raise ValueError(f"PostgreSQL connection validation failed: {str(e)}")
                
            if not os.path.exists(OFFLINE_MODEL_HOME):
                raise ValueError(f"Offline model home directory path does not exist: {OFFLINE_MODEL_HOME}")
            
            from app.config import active_model, fallback_model
            pm_path = os.path.join(OFFLINE_MODEL_HOME, active_model["name"])
            fm_path = os.path.join(OFFLINE_MODEL_HOME, fallback_model["name"])
            if not os.path.exists(pm_path) or not os.path.exists(fm_path):
                raise ValueError("Model binaries are missing in the configured directory.")
            
            # 3. Drop and recreate PostgreSQL database schemas
            from app.database import Base, engine
            Base.metadata.drop_all(bind=engine)
            Base.metadata.create_all(bind=engine)
            
            # Seed default jobs
            db.add(JobStatus(job_name="macro_categorization", is_running=False, total_items=0, processed_items=0))
            db.add(JobStatus(job_name="bulk_triage", is_running=False, total_items=0, processed_items=0))
            db.commit()
            
            # 4. Drop and recreate ChromaDB Persistent indexes
            reset_vector_store()
            
            # 5. Clear physical folders under /knowledge_docs
            for folder in [PENDING_DIR, PROCESSED_DIR, FAILED_DIR]:
                if os.path.exists(folder):
                    for item in os.listdir(folder):
                        item_path = os.path.join(folder, item)
                        try:
                            if os.path.isfile(item_path):
                                os.remove(item_path)
                        except Exception:
                            pass
                            
            span.message = "Application database structure, vector collections, and documents catalog successfully initialized and seeded."
            return {"status": "SUCCESS", "message": span.message}
        except Exception as e:
            span.message = f"Installation script failed: {str(e)}"
            raise HTTPException(status_code=400, detail=span.message)
