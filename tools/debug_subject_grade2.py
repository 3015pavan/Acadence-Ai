#!/usr/bin/env python3
"""Debug the subject-for-grade query handler - enhanced.

Run:
    python tools/debug_subject_grade2.py
"""

import sys
import os

# Add the project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.services.intelligence import _extract_grade_value
from backend.services.query_engine import _find_students_from_query_or_history, _student_results, _normalize_text
from backend.database import SessionLocal

# Test queries
test_queries = [
    "In which subject did AJAY JAYAPRAKASH get O grade?",
    "What subject did AJAY JAYAPRAKASH get O in?",
    "In which subject did AJAY JAYAPRAKASH get O?",
]

def debug():
    db = SessionLocal()
    try:
        for query in test_queries:
            print(f"\n{'='*80}")
            print(f"Query: {query}")
            print(f"{'='*80}")
            
            normalized = _normalize_text(query)
            print(f"Normalized: {normalized}")
            
            # Check conditions
            cond1 = any(word in normalized for word in ["which subject", "what subject", "in which subject"])
            cond2 = any(word in normalized for word in ["grade", "got", "get"])
            
            print(f"Has 'which/what subject': {cond1}")
            print(f"Has 'grade/got/get': {cond2}")
            
            grade = _extract_grade_value(query)
            print(f"Extracted grade: {grade}")
            
            students = _find_students_from_query_or_history(db, query)
            print(f"Found students: {[(s.name, s.usn) for s in students]}")
            
            if students:
                student = students[0]
                print(f"Using student: {student.name} ({student.usn})")
                
                grade_upper = str(grade).upper()
                matching_results = []
                
                for result in _student_results(student):
                    if str(result.grade).upper() == grade_upper:
                        matching_results.append(result)
                
                print(f"Subjects with grade '{grade_upper}': {[r.subject for r in matching_results]}")
    finally:
        db.close()

if __name__ == "__main__":
    debug()
