"""Simple smoke test to POST a query to the local server.

Run after starting the app: `python tools/smoke_test_query.py`
"""
import requests
import sys

BASE = "http://127.0.0.1:8000"

def run_query(q):
    try:
        r = requests.post(f"{BASE}/analytics/query", json={"query": q, "history": []}, timeout=30)
        print('Status:', r.status_code)
        print('Response:', r.json())
    except Exception as exc:
        print('Request failed:', exc)

if __name__ == '__main__':
    query = sys.argv[1] if len(sys.argv) > 1 else 'Summarize this class'
    run_query(query)
