import os
import chromadb
from chromadb.utils import embedding_functions
from app.config import OFFLINE_MODEL_HOME

# Define persistent storage directories
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROMA_DIR = os.path.join(PROJECT_ROOT, ".chromadb")
MODEL_PATH = os.path.join(OFFLINE_MODEL_HOME, "all-MiniLM-L6-v2")

if not os.path.exists(MODEL_PATH):
    raise FileNotFoundError(f"Embedding model folder not found at {MODEL_PATH}")

# Instantiate PersistentClient targeting the designated project folder subdirectory
client = chromadb.PersistentClient(path=CHROMA_DIR)

# Initialize the embedding function using the local offline MiniLM model path running on CPU
embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name=MODEL_PATH,
    device="cpu"
)

# Initialize collections with explicit L2 metric
incidents_collection = client.get_or_create_collection(
    name="incidents",
    embedding_function=embedding_function,
    metadata={"hnsw:space": "l2"}
)

knowledge_collection = client.get_or_create_collection(
    name="knowledge_base",
    embedding_function=embedding_function,
    metadata={"hnsw:space": "l2"}
)

def add_incident_chunks(chunks, ids, metadatas):
    """Adds chunked text arrays with metadata filters to the incident collection."""
    incidents_collection.add(
        documents=chunks,
        ids=ids,
        metadatas=metadatas
    )

def add_knowledge_chunks(chunks, ids, metadatas):
    """Adds chunked text arrays with metadata filters to the knowledge collection."""
    knowledge_collection.add(
        documents=chunks,
        ids=ids,
        metadatas=metadatas
    )

def delete_knowledge_by_file(filename):
    """Deletes all chunks matching the specific filename from the knowledge base collection."""
    knowledge_collection.delete(where={"filename": filename})

def delete_incidents_by_number(number):
    """Deletes all chunks matching the specific incident number from the incidents collection."""
    incidents_collection.delete(where={"number": number})

def query_incidents(query_text, cmdb_ci, n_results=10):
    """Semantic search against the incidents collection with hard cmdb_ci filtering."""
    results = incidents_collection.query(
        query_texts=[query_text],
        n_results=n_results,
        where={"cmdb_ci": cmdb_ci}
    )
    return results

def query_knowledge(query_text, n_results=4):
    """Semantic search against the knowledge collection."""
    results = knowledge_collection.query(
        query_texts=[query_text],
        n_results=n_results
    )
    return results

def reset_vector_store():
    """Deletes and recreates the persistent collections to start with a clean index."""
    global incidents_collection, knowledge_collection
    try:
        client.delete_collection("incidents")
    except Exception:
        pass
    try:
        client.delete_collection("knowledge_base")
    except Exception:
        pass
        
    incidents_collection = client.get_or_create_collection(
        name="incidents",
        embedding_function=embedding_function,
        metadata={"hnsw:space": "l2"}
    )
    knowledge_collection = client.get_or_create_collection(
        name="knowledge_base",
        embedding_function=embedding_function,
        metadata={"hnsw:space": "l2"}
    )
