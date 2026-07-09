import os
import csv
import hashlib
from datetime import datetime, timezone
from pypdf import PdfReader
from docx import Document
from tokenizers import Tokenizer
from app.config import OFFLINE_MODEL_HOME

# Load tokenizer from local MiniLM-L6-v2 directory
TOKENIZER_PATH = os.path.join(OFFLINE_MODEL_HOME, "all-MiniLM-L6-v2", "tokenizer.json")
if not os.path.exists(TOKENIZER_PATH):
    raise FileNotFoundError(f"Tokenizer file not found at {TOKENIZER_PATH}")

tokenizer = Tokenizer.from_file(TOKENIZER_PATH)

def calculate_sha256(content: bytes) -> str:
    """Calculates the SHA-256 hash of raw bytes."""
    sha256 = hashlib.sha256()
    sha256.update(content)
    return sha256.hexdigest()

def parse_date(date_str: str) -> datetime:
    """Parses sys_created_on timestamp in format 'DD-MM-YYYY HH:MM:SS AM/PM'
    and returns a timezone-aware UTC datetime.
    """
    date_str = date_str.strip()
    try:
        # Match standard DD-MM-YYYY HH:MM:SS AM/PM format
        dt = datetime.strptime(date_str, "%d-%m-%Y %I:%M:%S %p")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            # Fallback format without AM/PM
            dt = datetime.strptime(date_str, "%d-%m-%Y %H:%M:%S")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError(
                f"Date format '{date_str}' is invalid. "
                "Expected format: 'DD-MM-YYYY HH:MM:SS AM/PM'"
            )

def parse_csv_file(file_content_str: str):
    """Parses and validates the CSV content according to case-insensitive column schemas."""
    # Split text into lines and feed into csv reader
    lines = file_content_str.splitlines()
    reader = csv.reader(lines)
    rows = list(reader)
    if not rows:
        raise ValueError("The uploaded CSV file is empty.")

    # Convert headers to clean lowercase fields
    headers = [h.strip().lower() for h in rows[0]]

    # Expected column list (ticketing tool schema)
    required_columns = [
        "number",
        "cmdb_ci",
        "short_description",
        "caller_id",
        "u_ge_affected_user",
        "opened_by",
        "priority",
        "state",
        "assignment_group",
        "assigned_to",
        "description",
        "comments_and_work_notes",
        "closed_note",
        "sys_created_on"
    ]

    for col in required_columns:
        if col not in headers:
            raise ValueError(f"Missing required CSV column header: '{col}'")

    header_indices = {col: headers.index(col) for col in required_columns}

    parsed_records = []
    for line_num, row in enumerate(rows[1:], start=2):
        if not row or all(not cell.strip() for cell in row):
            continue  # Skip blank rows

        # Pad rows that are shorter than headers
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))

        record = {}
        try:
            for col in required_columns:
                record[col] = row[header_indices[col]].strip()
            
            # Validate date conversion
            record["sys_created_on_dt"] = parse_date(record["sys_created_on"])
            parsed_records.append(record)
        except Exception as e:
            raise ValueError(f"Row {line_num} parsing failure: {str(e)}")

    return parsed_records

def parse_pdf_document(file_path: str):
    """Extracts text page-by-page from a PDF file."""
    reader = PdfReader(file_path)
    pages_text = []
    for page_idx, page in enumerate(reader.pages):
        text = page.extract_text()
        if text:
            pages_text.append((page_idx + 1, text.strip()))
    return pages_text

def parse_docx_document(file_path: str) -> str:
    """Extracts all text from a DOCX file."""
    doc = Document(file_path)
    paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)

def chunk_text(text: str, max_tokens: int = 500, overlap: int = 75) -> list[str]:
    """Chunks the text based on the local MiniLM tokenizer tokens."""
    encoded = tokenizer.encode(text)
    ids = encoded.ids

    if len(ids) <= max_tokens:
        return [text]

    chunks = []
    i = 0
    while i < len(ids):
        chunk_ids = ids[i : i + max_tokens]
        # Decode back to a readable text representation
        chunk_str = tokenizer.decode(chunk_ids)
        if chunk_str.strip():
            chunks.append(chunk_str.strip())
        i += (max_tokens - overlap)
        
        # Guard against zero increment
        if max_tokens <= overlap:
            break
            
    return chunks
