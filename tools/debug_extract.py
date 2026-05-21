#!/usr/bin/env python3
"""Debug subject phrase extraction.

Run:
    python tools/debug_extract.py
"""

import sys
import os

# Add the project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.services.query_engine import _extract_subject_phrase, _normalize_text

test_queries = [
    "In which subject did AJAY JAYAPRAKASH get O grade?",
    "What subject did AJAY JAYAPRAKASH get O in?",
    "In which subject did AJAY JAYAPRAKASH get O?",
]

for query in test_queries:
    subject = _extract_subject_phrase(query)
    normalized = _normalize_text(query)
    print(f"\nQuery: {query}")
    print(f"Normalized: {normalized}")
    print(f"Extracted subject: '{subject}'")
    if subject:
        student_query = normalized.replace(_normalize_text(subject), " ")
        print(f"After removing subject: '{student_query}'")
