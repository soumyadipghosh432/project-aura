import uuid
from sqlalchemy import create_engine, Column, String, Integer, Text, DateTime, ForeignKey, func, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Incident(Base):
    __tablename__ = "incidents"
    
    number = Column(String, primary_key=True, index=True)
    cmdb_ci = Column(String, index=True)
    short_desc = Column(Text)
    description = Column(Text)
    work_notes = Column(Text)
    closed_note = Column(Text)
    assigned_to = Column(String, index=True)
    assignment_group = Column(String, index=True)
    sys_created_on = Column(DateTime(timezone=True), index=True)
    process_status = Column(String, default="PENDING", index=True) # PENDING, PROCESSED, FAILED

class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, unique=True, index=True)
    hash_value = Column(String, unique=True)
    processed_date = Column(DateTime(timezone=True), server_default=func.now())

class Category(Base):
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    number = Column(String, ForeignKey("incidents.number", ondelete="CASCADE"), unique=True)
    assigned_cat = Column(String, index=True)
    error_codes = Column(Text)
    error_msgs = Column(Text)

class IncidentSearch(Base):
    __tablename__ = "incident_searches"
    
    search_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    query_text = Column(Text)
    matched_incs = Column(JSONB) # List of incident details
    rag_response = Column(Text)
    citations = Column(JSONB) # List of file links / details
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)

class TelemetryLog(Base):
    __tablename__ = "telemetry_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    trace_id = Column(String(32), index=True)
    span_id = Column(String(16), index=True)
    event_name = Column(String(255), index=True)
    duration_ms = Column(Integer)
    token_count = Column(Integer)
    status = Column(String(50), index=True) # SUCCESS, ERROR
    exception_type = Column(Text)
    message = Column(Text)

class JobStatus(Base):
    __tablename__ = "job_statuses"
    
    job_name = Column(String, primary_key=True)
    is_running = Column(Boolean, default=False)
    total_items = Column(Integer, default=0)
    processed_items = Column(Integer, default=0)

class DeletedAction(Base):
    __tablename__ = "deleted_actions"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    item_type = Column(String(100), nullable=False, index=True) # "KNOWLEDGE_DOCUMENT" or "INCIDENT"
    item_identifier = Column(String(255), nullable=False, index=True) # e.g. filename or INC001234
    details = Column(Text, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
