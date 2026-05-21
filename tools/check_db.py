#!/usr/bin/env python3
"""Check if students exist in database.

Run:
    python tools/check_db.py
"""

import sys
import os

# Add the project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from backend.services.analyzer import fetch_students
from backend.database import SessionLocal

def check():
    db = SessionLocal()
    try:
        students = fetch_students(db)
        print(f"Total students in DB: {len(students)}")
        if students:
            print(f"First 5 students:")
            for s in students[:5]:
                print(f"  - {s.name} ({s.usn})")
            
            # Look for AJAY
            for s in students:
                if "AJAY" in s.name.upper():
                    print(f"\nFound AJAY: {s.name} ({s.usn})")
                    break
    finally:
        db.close()

if __name__ == "__main__":
    check()
