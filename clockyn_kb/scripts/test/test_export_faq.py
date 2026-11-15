"""Unit test of the helpers of scripts/export-faq-from-db.py (no database, no psycopg2 needed). Run: python scripts/test/test_export_faq.py"""
import datetime
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("export_faq", os.path.join(HERE, "..", "export-faq-from-db.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class BuildPayload(unittest.TestCase):
    def test_rows_and_generated_date(self):
        rows = [
            (2, "Help & Fixes", "Troubleshooting", "Slow?", "Check your network.", 20, 1, datetime.datetime(2026, 11, 10, 8, 0)),
            (1, "Help & Fixes", "Troubleshooting", "Crash?", "Update the app.", None, None, datetime.datetime(2026, 11, 12, 9, 30)),
        ]
        payload = mod.build_payload(rows, "2000-01-01")
        self.assertEqual(payload["generated"], "2026-11-12")
        self.assertEqual(payload["source"], "db")
        self.assertEqual(payload["rows"][1], {"id": 1, "bucket": "Help & Fixes", "category": "Troubleshooting", "question": "Crash?",
                                              "answer": "Update the app.", "sort_order": 0, "status": 1})

    def test_empty_export_uses_the_fallback_date(self):
        self.assertEqual(mod.build_payload([], "2026-11-15"), {"generated": "2026-11-15", "source": "db", "rows": []})

    def test_schema_name_must_be_an_identifier(self):
        with self.assertRaises(SystemExit):
            mod.fetch_rows(object(), "clockyn_dev; DROP TABLE x")


if __name__ == "__main__":
    unittest.main()
