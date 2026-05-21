"""
Evaluation script for Acadence AI.
- Computes ingestion metrics from processed Excel outputs
- Reports PostgreSQL counts
- Runs query validation using TEST_QUERIES_VALIDATION.py and `execute_query` to compute intent accuracy and RAG-like score

Run:
  (& .\.venv\Scripts\Activate.ps1); python tools\evaluate.py

"""

import glob
import os
import sys
import statistics
from pathlib import Path
from typing import List, Dict

import pandas as pd

# Ensure project root is on sys.path so backend package imports work
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Import DB session and models
from backend.database import SessionLocal
from backend.models import Dataset, Student, StudentSemester, Result

# Import query test harness and engine
from TEST_QUERIES_VALIDATION import TEST_QUERIES, EXPECTED_INTENTS
from backend.services.query_engine import execute_query


def find_latest_processed_excel() -> Path:
    pattern = "backend/storage/agent_outputs/**/processed_results.xlsx"
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None
    latest = max(matches, key=os.path.getmtime)
    return Path(latest)


def ingestion_metrics(excel_path: Path) -> Dict[str, object]:
    df = pd.read_excel(excel_path)
    rows = len(df)
    cols = list(df.columns)
    usn_count = df['USN'].nunique() if 'USN' in df.columns else df.shape[0]
    missing_usn = int(df['USN'].isna().sum()) if 'USN' in df.columns else 0
    critical_cols = ['USN', 'Name', 'SGPA']
    missing_critical = {c: int(df[c].isna().sum()) if c in df.columns else rows for c in critical_cols}
    completeness = round(100.0 * (rows - sum(missing_critical.values())) / (rows * len(critical_cols)) , 2) if rows>0 else 0.0
    return {
        'file': str(excel_path),
        'rows': rows,
        'columns': cols,
        'unique_usn': int(usn_count),
        'missing_usn': int(missing_usn),
        'missing_critical': missing_critical,
        'completeness_percent': completeness,
    }


def db_counts() -> Dict[str, int]:
    db = SessionLocal()
    try:
        return {
            'datasets': int(db.query(Dataset).count()),
            'students': int(db.query(Student).count()),
            'student_semesters': int(db.query(StudentSemester).count()),
            'results': int(db.query(Result).count()),
        }
    finally:
        db.close()


def run_query_validation(sample_limit: int = None) -> Dict[str, object]:
    db = SessionLocal()
    try:
        queries_flat: List[str] = []
        for category, queries in TEST_QUERIES.items():
            queries_flat.extend(queries)
        if sample_limit and sample_limit > 0:
            queries_flat = queries_flat[:sample_limit]

        total = 0
        matched = 0
        confidences: List[float] = []
        details = []

        for q in queries_flat:
            expected = EXPECTED_INTENTS.get(q, [])
            total += 1
            resp = execute_query(db, q)
            got_intent = resp.get('intent')
            meta = resp.get('meta', {}) or {}
            conf = float(meta.get('confidence', 0.0) or 0.0)
            confidences.append(conf)

            ok = False
            if not expected:
                # if no expected intents, consider contextual answers acceptable
                ok = True if got_intent else False
            else:
                # match if returned intent is in expected list
                if got_intent in expected:
                    ok = True
                else:
                    # allow contextual answers as partial matches
                    if got_intent == 'CONTEXTUAL_ANSWER' and 'CONTEXTUAL_ANSWER' in expected:
                        ok = True

            if ok:
                matched += 1
            details.append({'query': q, 'expected': expected, 'got': got_intent, 'confidence': conf, 'ok': ok})

        intent_accuracy = round(matched / total, 4) if total else 0.0
        avg_conf = round(statistics.mean(confidences), 4) if confidences else 0.0
        rag_score = round(0.7 * intent_accuracy + 0.3 * avg_conf, 4)

        return {
            'total_queries': total,
            'matched': matched,
            'intent_accuracy': intent_accuracy,
            'avg_confidence': avg_conf,
            'rag_score': rag_score,
            'details': details,
        }
    finally:
        db.close()


def print_report():
    print("\n" + "="*80)
    print("ACADENCE AI EVALUATION REPORT")
    print("="*80 + "\n")

    excel = find_latest_processed_excel()
    if excel is None:
        print("No processed_results.xlsx found under backend/storage/agent_outputs/. Skipping ingestion metrics.")
    else:
        meta = ingestion_metrics(excel)
        print("INGESTION METRICS")
        print("-"*40)
        print(f"Processed file: {meta['file']}")
        print(f"Rows: {meta['rows']}")
        print(f"Unique USNs: {meta['unique_usn']}")
        print(f"Missing critical (USN,Name,SGPA): {meta['missing_critical']}")
        print(f"Completeness (%): {meta['completeness_percent']}")
        print()

    print("DATABASE COUNTS")
    print("-"*40)
    counts = db_counts()
    for k, v in counts.items():
        print(f"{k}: {v}")
    print()

    print("QUERY VALIDATION (RAG-style)")
    print("-"*40)
    qres = run_query_validation()
    print(f"Total Queries Tested: {qres['total_queries']}")
    print(f"Matched Intents: {qres['matched']}")
    print(f"Intent Accuracy: {qres['intent_accuracy']*100:.2f}%")
    print(f"Average Confidence: {qres['avg_confidence']:.3f}")
    print(f"RAG Score: {qres['rag_score']:.3f}   (0-1 scaled composite) ")
    print()

    # Print top 10 mismatches
    mismatches = [d for d in qres['details'] if not d['ok']]
    if mismatches:
        print("TOP MISMATCHES")
        print("-"*40)
        for item in mismatches[:10]:
            print(f"Query: {item['query']}")
            print(f"  Expected: {item['expected']}")
            print(f"  Got: {item['got']} (conf={item['confidence']})\n")
    else:
        print("All queries matched expected intents (within test mapping).")


if __name__ == '__main__':
    print_report()
