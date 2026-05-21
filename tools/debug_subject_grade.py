#!/usr/bin/env python3
"""Debug the subject-for-grade query handler conditions.

Run:
    python tools/debug_subject_grade.py
"""

import sys
import os

# Add the project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.services.intelligence import _extract_grade_value, _extract_name_value, _extract_usn_value

# Test queries
test_queries = [
    "In which subject did AJAY JAYAPRAKASH get O grade?",
    "What subject did AJAY JAYAPRAKASH get O in?",
    "In which subject did AJAY JAYAPRAKASH get O?",
]

def debug():
    for query in test_queries:
        print(f"\n{'='*80}")
        print(f"Query: {query}")
        print(f"{'='*80}")
        
        normalized = query.lower()
        print(f"Lowercase: {normalized}")
        
        # Check conditions
        cond1 = any(word in normalized for word in ["which subject", "what subject", "in which subject"])
        cond2 = any(word in normalized for word in ["grade", "got", "get"])
        
        print(f"Has 'which/what subject': {cond1}")
        print(f"Has 'grade/got/get': {cond2}")
        
        grade = _extract_grade_value(query)
        name = _extract_name_value(query)
        usn = _extract_usn_value(query)
        
        print(f"Extracted grade: {grade}")
        print(f"Extracted name: {name}")
        print(f"Extracted USN: {usn}")
        print(f"Has student identifier: {bool(name or usn)}")

if __name__ == "__main__":
    debug()
