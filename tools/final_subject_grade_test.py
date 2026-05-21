#!/usr/bin/env python3
"""Comprehensive test of subject-grade query handler.

This script tests the newly implemented subject-grade reverse lookup feature
that handles queries like "In which subject did [student] get [grade]?"

Run:
    python tools/final_subject_grade_test.py
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.services.query_engine import _execute_subject_for_grade_query
from backend.database import SessionLocal

# Test queries covering different patterns
TEST_CASES = [
    # Basic patterns
    ("In which subject did AJAY JAYAPRAKASH get O grade?", "Student should get O grades"),
    ("What subject did AJAY JAYAPRAKASH get O in?", "Alternative phrasing for O grades"),
    ("In which subject did AJAY JAYAPRAKASH get O?", "Shorter version of O grades query"),
    
    # Different grades
    ("Which subject did AJAY JAYAPRAKASH get A+ in?", "A+ grades lookup"),
    ("In which subject did AJAY JAYAPRAKASH get A?", "A grades lookup"),
    ("What subject did AJAY JAYAPRAKASH get B in?", "B grades lookup"),
    
    # USN-based queries
    ("In which subject did 1MS21CS009 get O grade?", "Using student USN instead of name"),
    ("What subject did 1MS21CS009 get A+ in?", "USN-based A+ grades query"),
    
    # Different student
    ("In which subject did AKASH SURESH BADADANI get O?", "Different student O grades"),
]

def main():
    db = SessionLocal()
    try:
        print("="*80)
        print("SUBJECT-GRADE REVERSE LOOKUP TEST SUITE")
        print("="*80)
        
        passed = 0
        failed = 0
        
        for query, description in TEST_CASES:
            print(f"\n📝 {description}")
            print(f"   Query: {query}")
            
            result = _execute_subject_for_grade_query(db, query)
            
            if result:
                passed += 1
                intent = result.get('intent')
                answer = result.get('answer', '')[:150] + ("..." if len(result.get('answer', '')) > 150 else "")
                print(f"   ✓ PASS")
                print(f"   Intent: {intent}")
                print(f"   Answer: {answer}")
                meta = result.get('meta', {})
                if meta.get('matched_subjects'):
                    print(f"   Subjects: {meta.get('matched_subjects')[:5]}")
            else:
                failed += 1
                print(f"   ✗ FAIL - Function returned None")
            
            print(f"   {'-'*76}")
        
        print(f"\n{'='*80}")
        print(f"RESULTS: {passed} passed, {failed} failed out of {len(TEST_CASES)} tests")
        print(f"{'='*80}")
        
        if failed == 0:
            print("\n✅ ALL TESTS PASSED - Subject-grade query handler is working correctly!")
        else:
            print(f"\n⚠️  {failed} test(s) failed")
            
    finally:
        db.close()

if __name__ == "__main__":
    main()
