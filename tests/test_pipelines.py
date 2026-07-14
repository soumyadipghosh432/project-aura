import os
import sys
import unittest
from datetime import datetime, timezone

# Add parent directory to path so app modules are importable
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import OFFLINE_MODEL_HOME, models_list, active_model, fallback_model
from app.database import Base, Incident, KnowledgeDocument, Category, engine, SessionLocal, init_db
from app.parsers import parse_date, parse_csv_file, chunk_text
from app.models_orchestrator import ModelManager

class TestProjectAuraPipelines(unittest.TestCase):

    def setUp(self):
        # Create schema for tests in the SQLite or postgres db
        init_db()
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()

    def test_configuration_loading(self):
        """Verify model configuration constraints (exactly one active, one fallback)."""
        active_count = sum(1 for m in models_list if m.get("active"))
        fallback_count = sum(1 for m in models_list if m.get("fallback"))
        
        self.assertEqual(active_count, 1, "Exactly one model must be active")
        self.assertEqual(fallback_count, 1, "Exactly one model must be designated fallback")
        
        self.assertEqual(active_model["name"], "gemma-2-2b-it-Q4_K_M.gguf")
        self.assertEqual(fallback_model["name"], "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf")

    def test_date_parser(self):
        """Verify CSV date string parses to timezone-aware UTC datetime."""
        date_str = "09-07-2026 11:12:26 AM"
        dt = parse_date(date_str)
        
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 7)
        self.assertEqual(dt.day, 9)
        self.assertEqual(dt.hour, 11)
        self.assertEqual(dt.minute, 12)
        self.assertEqual(dt.second, 26)
        self.assertEqual(dt.tzinfo, timezone.utc)

        # Test ISO YYYY-MM-DD format
        dt2 = parse_date("2025-12-18 14:09:44")
        self.assertEqual(dt2.year, 2025)
        self.assertEqual(dt2.month, 12)
        self.assertEqual(dt2.day, 18)
        self.assertEqual(dt2.hour, 14)
        self.assertEqual(dt2.minute, 9)
        self.assertEqual(dt2.second, 44)
        self.assertEqual(dt2.tzinfo, timezone.utc)

    def test_csv_parser_validation(self):
        """Verify CSV column header validation works case-insensitively."""
        csv_data = (
            "number,CMDB_CI,short_description,caller_id,u_ge_affected_user,opened_by,priority,state,"
            "assignment_group,assigned_to,description,comments_and_work_notes,closed_note,sys_created_on\n"
            "INC001,AppA,ShortDesc,C1,U1,O1,3,Closed,GroupA,UserA,DescText,WorkNotes,ClosedNotes,09-07-2026 11:12:26 AM\n"
        )
        records = parse_csv_file(csv_data)
        
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["number"], "INC001")
        self.assertEqual(records[0]["cmdb_ci"], "AppA")
        self.assertEqual(records[0]["closed_note"], "ClosedNotes")

        # Test alternative column name 'close_notes'
        csv_data_alt = (
            "number,CMDB_CI,short_description,caller_id,u_ge_affected_user,opened_by,priority,state,"
            "assignment_group,assigned_to,description,comments_and_work_notes,close_notes,sys_created_on\n"
            "INC002,AppB,ShortDescB,C2,U2,O2,2,Open,GroupB,UserB,DescTextB,WorkNotesB,ClosedNotesB,2025-12-18 14:09:44\n"
        )
        records_alt = parse_csv_file(csv_data_alt)
        self.assertEqual(len(records_alt), 1)
        self.assertEqual(records_alt[0]["number"], "INC002")
        self.assertEqual(records_alt[0]["closed_note"], "ClosedNotesB")

    def test_text_chunker(self):
        """Verify token-based text chunker works correctly and returns list of chunks."""
        text = "This is a simple sentence to check the chunking capability of Project Aura."
        chunks = chunk_text(text, max_tokens=10, overlap=2)
        
        self.assertTrue(len(chunks) >= 1)
        self.assertTrue(any("simple" in c for c in chunks))

    def test_silent_fallback_simulation(self):
        """Test that memory OOM simulation triggers fallback loading successfully."""
        # Instantiate a mock manager
        mock_manager = ModelManager()
        # Set primary active model to a non-existent name to force an error
        mock_manager.active_model_name = "non_existent_model_oom.gguf"
        
        # This should capture the failure, log it, and load the fallback model
        # since fallback_model_name is Phi-3.1-mini-4k-instruct-Q4_K_M.gguf which exists
        mock_manager.load_model()
        
        self.assertTrue(mock_manager.is_fallback_active)
        self.assertEqual(mock_manager.active_model_name, "Phi-3.1-mini-4k-instruct-Q4_K_M.gguf")
        self.assertIsNotNone(mock_manager.llm)

if __name__ == "__main__":
    unittest.main()
