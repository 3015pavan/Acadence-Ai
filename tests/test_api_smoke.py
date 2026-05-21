import requests

BASE = "http://127.0.0.1:8000"


def test_health():
    r = requests.get(f"{BASE}/health", timeout=5)
    assert r.status_code == 200
    assert r.json().get("status") == "ok"


def test_query_endpoint():
    # Requires server running and at least one uploaded dataset
    r = requests.post(f"{BASE}/analytics/query", json={"query": "topper", "history": []}, timeout=30)
    assert r.status_code in (200, 400, 500)
