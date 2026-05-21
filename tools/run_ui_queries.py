import json
import time
import requests


SERVER = "http://127.0.0.1:8000"
ENDPOINT = f"{SERVER}/analytics/query"

QUERIES = [
    "topper",
    "top 5 students",
    "average SGPA",
    "average GP",
    "who failed",
    "students with A+",
    "students with F",
    "list all subjects",
    "pass percentage",
    "total students",
    "who scored highest in mathematics",
    "who failed in engineering chemistry lab",
    "students with GP = 0",
    "students with GP zero and an A grade",
    "student details for USN 1BM19CS001",
    "result of John",
    "show students with A grade",
    "topper in 2nd semester",
    "average SGPA for semester 2",
    "who didn't pass design thinking",
    "students with A+ but failed in another subject",
    "get the grade of 1BM19CS010 in mathematics",
    "list students who have inconsistent performance",
    "compare performance of class between semester 1 and 2",
    "show insights",
    "give class summary",
    "who are the top 10 students",
    "how many students failed",
    "students with grade B",
    "students with grade C",
    "students with grade P",
    "show average GP per subject",
    "most frequent grade",
    "students with SGPA above 8.0",
    "students with SGPA below 4.0",
    "who are the weakest subjects",
    "subject-wise analysis",
    "list students from dataset 1",
    "show all students",
    "get result by name Alice",
    "get result by usn 1BM19CS005",
    "who are the strong students in physics",
    "students who improved",
    "students who performed poorly in electronics",
    "How many passed overall?",
    "Give me the topper and average SGPA",
    "Explain why some students failed",
    "Which subject has highest average GP",
    "Show grade distribution for mathematics",
]


def run():
    results = []
    for idx, q in enumerate(QUERIES[:50], start=1):
        payload = {"query": q, "history": []}
        try:
            resp = requests.post(ENDPOINT, json=payload, timeout=10)
            try:
                data = resp.json()
            except Exception:
                data = {"status_code": resp.status_code, "text": resp.text}
            print(f"[{idx}/{len(QUERIES)}] Query: {q!r} -> HTTP {resp.status_code}")
            results.append({"query": q, "status_code": resp.status_code, "response": data})
        except Exception as exc:
            print(f"[{idx}/{len(QUERIES)}] Query: {q!r} -> ERROR: {exc}")
            results.append({"query": q, "status": "error", "error": str(exc)})
        time.sleep(0.12)

    out_path = "tests/query_run_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Saved results to {out_path}")


if __name__ == "__main__":
    run()
