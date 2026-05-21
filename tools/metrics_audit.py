"""
Audit project metrics for the latest dataset and query set.
Run:
  (& .\.venv\Scripts\Activate.ps1); python tools\metrics_audit.py
"""

import json
import math
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import SessionLocal
from backend.agent_models import AgentProcessedDataset, AgentProcessedEmail
from backend.models import Dataset
from backend.services.query_engine import execute_query


METRICS_LOG = ROOT / "backend" / "logs" / "metrics.jsonl"
EVAL_CASES = ROOT / "tools" / "qa_eval_cases.json"

USN_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9][A-Z0-9-]{2,24}$")
ALLOWED_GRADES = {"O", "A+", "A", "B+", "B", "C", "D", "E", "P", "F", "NA"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9+]+", str(text).lower())


def _norm(text: str) -> str:
    return " ".join(_tokenize(text)).strip()


def _lcs_len(a: List[str], b: List[str]) -> int:
    if not a or not b:
        return 0
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1]


def _bleu_1(pairs: List[Tuple[str, str]]) -> float:
    total_match = 0
    total_pred = 0
    total_ref = 0
    for pred, ref in pairs:
        pred_tokens = _tokenize(pred)
        ref_tokens = _tokenize(ref)
        total_pred += len(pred_tokens)
        total_ref += len(ref_tokens)
        ref_counts: Dict[str, int] = {}
        for token in ref_tokens:
            ref_counts[token] = ref_counts.get(token, 0) + 1
        for token in pred_tokens:
            if ref_counts.get(token, 0) > 0:
                total_match += 1
                ref_counts[token] -= 1
    if total_pred == 0:
        return 0.0
    precision = total_match / total_pred
    if total_pred >= total_ref:
        bp = 1.0
    else:
        bp = math.exp(1 - (total_ref / max(total_pred, 1)))
    return round(precision * bp, 4)


def _rouge_l(pairs: List[Tuple[str, str]]) -> float:
    scores = []
    for pred, ref in pairs:
        ref_tokens = _tokenize(ref)
        if not ref_tokens:
            continue
        lcs = _lcs_len(_tokenize(pred), ref_tokens)
        scores.append(lcs / max(len(ref_tokens), 1))
    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 4)


def _load_cases() -> List[Dict[str, object]]:
    data = json.loads(EVAL_CASES.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def _load_metrics_log() -> List[Dict[str, object]]:
    if not METRICS_LOG.exists():
        return []
    events: List[Dict[str, object]] = []
    for line in METRICS_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return events


def _latest_dataset(db) -> Tuple[AgentProcessedDataset, Dataset]:
    dataset_row = db.query(AgentProcessedDataset).order_by(AgentProcessedDataset.created_at.desc()).first()
    if dataset_row is None:
        raise RuntimeError("No processed dataset found in agent_processed_datasets.")
    dataset = db.query(Dataset).filter(Dataset.name == dataset_row.dataset_name).first()
    if dataset is None:
        raise RuntimeError("Dataset row not found for name: {}".format(dataset_row.dataset_name))
    return dataset_row, dataset


def _dataset_quality_metrics(processed_path: Path) -> Dict[str, float]:
    df = pd.read_excel(processed_path)
    total_rows = int(len(df))
    if total_rows == 0:
        return {
            "missing_value_ratio": 1.0,
            "duplicate_detection_rate": 0.0,
            "schema_mapping_accuracy": 0.0,
            "validation_accuracy": 0.0,
            "data_consistency_score": 0.0,
            "data_integrity_score": 0.0,
        }

    required = ["USN", "Name", "SGPA"]
    missing_total = 0
    for col in required:
        if col not in df.columns:
            missing_total += total_rows
            continue
        series = df[col]
        missing_total += int(series.isna().sum())
        missing_total += int(series.astype(str).str.strip().eq("").sum())

    missing_ratio = missing_total / float(total_rows * len(required))

    duplicate_usn = 0
    if "USN" in df.columns:
        duplicate_usn = int(df["USN"].duplicated().sum())

    valid_rows = 0
    for _, row in df.iterrows():
        usn = str(row.get("USN", "")).strip().upper()
        name = str(row.get("Name", "")).strip()
        sgpa = row.get("SGPA")
        if not usn or not name:
            continue
        if not USN_PATTERN.fullmatch(usn):
            continue
        try:
            sgpa_val = float(sgpa)
        except Exception:
            continue
        if sgpa_val < 0 or sgpa_val > 10:
            continue

        valid = True
        for col in df.columns:
            if col.endswith(" Grade"):
                grade = str(row.get(col, "")).strip().upper() or "NA"
                if grade and grade not in ALLOWED_GRADES:
                    valid = False
                    break
            if col.endswith(" GP"):
                gp = row.get(col)
                if pd.isna(gp) or gp == "":
                    continue
                try:
                    gp_val = float(gp)
                except Exception:
                    valid = False
                    break
                if gp_val < 0 or gp_val > 10:
                    valid = False
                    break
        if valid:
            valid_rows += 1

    schema_mapping_accuracy = 1.0 - missing_ratio
    validation_accuracy = valid_rows / float(total_rows)
    data_consistency_score = validation_accuracy
    data_integrity_score = schema_mapping_accuracy

    return {
        "missing_value_ratio": round(missing_ratio, 4),
        "duplicate_detection_rate": round(duplicate_usn / float(total_rows), 4),
        "schema_mapping_accuracy": round(schema_mapping_accuracy, 4),
        "validation_accuracy": round(validation_accuracy, 4),
        "data_consistency_score": round(data_consistency_score, 4),
        "data_integrity_score": round(data_integrity_score, 4),
    }


def _find_latest_processed_excel() -> Path | None:
    candidates = list(ROOT.glob("backend/storage/agent_outputs/**/processed_results.xlsx"))
    if not candidates:
        fallback = ROOT / "backend" / "storage" / "processed_results.xlsx"
        return fallback if fallback.exists() else None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _run_query_evaluation(db, dataset_id: int) -> Dict[str, object]:
    cases = _load_cases()
    exact = 0
    contains = 0
    passed = 0
    total = len(cases)
    timings: List[int] = []
    context_times: List[int] = []
    generation_times: List[int] = []
    pairs: List[Tuple[str, str]] = []
    by_category: Dict[str, Dict[str, int]] = {}

    for case in cases:
        query = case["query"]
        expected = case["expected"]
        category = str(case.get("category") or "other")
        start = time.perf_counter()
        response = execute_query(db, query, history=[], dataset_ids=[dataset_id], merge="union")
        duration_ms = int((time.perf_counter() - start) * 1000)
        timings.append(duration_ms)

        answer = response.get("answer", "") if isinstance(response, dict) else str(response)
        pairs.append((answer, expected))

        ok_exact = _norm(answer) == _norm(expected)
        ok_contains = _norm(expected) in _norm(answer)
        if " and " in expected.lower():
            parts = [p.strip() for p in expected.split(" and ")]
            ok_contains = all(_norm(p) in _norm(answer) for p in parts)
        if expected.lower().startswith("yes") and _norm(answer).startswith("yes"):
            ok_contains = True
        if expected.lower().startswith("no") and _norm(answer).startswith("no"):
            ok_contains = True

        ok = ok_exact or ok_contains
        if ok_exact:
            exact += 1
        if ok_contains:
            contains += 1
        if ok:
            passed += 1

        meta = response.get("meta", {}) if isinstance(response, dict) else {}
        context_ms = meta.get("context_ms")
        generation_ms = meta.get("generation_ms")
        if isinstance(context_ms, (int, float)):
            context_times.append(int(context_ms))
        if isinstance(generation_ms, (int, float)):
            generation_times.append(int(generation_ms))

        stats = by_category.setdefault(category, {"total": 0, "passed": 0})
        stats["total"] += 1
        if ok:
            stats["passed"] += 1

    exact_score = exact / total if total else 0.0
    contains_score = contains / total if total else 0.0
    pass_score = passed / total if total else 0.0

    def cat_score(key: str) -> float:
        stats = by_category.get(key, {"total": 0, "passed": 0})
        return (stats["passed"] / stats["total"]) if stats["total"] else 0.0

    return {
        "exact": round(exact_score, 4),
        "contains": round(contains_score, 4),
        "pass": round(pass_score, 4),
        "bleu": _bleu_1(pairs),
        "rouge_l": _rouge_l(pairs),
        "latency_ms_avg": round(statistics.mean(timings), 2) if timings else 0.0,
        "latency_ms_p95": round(statistics.quantiles(timings, n=20)[-1], 2) if len(timings) >= 20 else (max(timings) if timings else 0.0),
        "context_ms_avg": round(statistics.mean(context_times), 2) if context_times else 0.0,
        "generation_ms_avg": round(statistics.mean(generation_times), 2) if generation_times else 0.0,
        "filter_accuracy": round(cat_score("filter"), 4),
        "aggregation_accuracy": round(cat_score("aggregation"), 4),
        "ranking_accuracy": round(cat_score("ranking"), 4),
        "lookup_accuracy": round(cat_score("lookup"), 4),
        "verification_accuracy": round(cat_score("verification"), 4),
        "mapping_accuracy": round(cat_score("mapping"), 4),
        "report_meta_accuracy": round(cat_score("report_meta"), 4),
    }


def _email_metrics(db) -> Dict[str, float]:
    total = db.query(AgentProcessedEmail).count()
    processed = db.query(AgentProcessedEmail).filter(AgentProcessedEmail.status == "processed").count()
    failed = db.query(AgentProcessedEmail).filter(AgentProcessedEmail.status == "failed").count()
    if total == 0:
        return {"email_success_rate": 0.0, "email_failure_rate": 0.0}
    return {
        "email_success_rate": round(processed / float(total), 4),
        "email_failure_rate": round(failed / float(total), 4),
    }


def main() -> None:
    db = SessionLocal()
    try:
        dataset_row, dataset = _latest_dataset(db)
        processed_path = Path(dataset_row.processed_excel_path)
        if not processed_path.exists():
            fallback = _find_latest_processed_excel()
            if fallback is not None:
                processed_path = fallback
        if processed_path.exists():
            dataset_metrics = _dataset_quality_metrics(processed_path)
        else:
            dataset_metrics = {
                "missing_value_ratio": None,
                "duplicate_detection_rate": None,
                "schema_mapping_accuracy": None,
                "validation_accuracy": None,
                "data_consistency_score": None,
                "data_integrity_score": None,
            }

        events = _load_metrics_log()
        parse_events = [e for e in events if e.get("event") == "parse" and e.get("filename") == dataset_row.source_filename]
        ingestion_events = [
            e for e in events
            if e.get("event") == "ingestion"
            and (e.get("dataset_name") == dataset_row.dataset_name or e.get("dataset_hash") == dataset_row.dataset_hash)
        ]

        parse_success_rate = None
        if parse_events:
            parse_success_rate = round(
                sum(1 for e in parse_events if e.get("success")) / float(len(parse_events)), 4
            )

        ingestion_success_rate = None
        ingestion_throughput = None
        etl_ms = None
        if ingestion_events:
            ingestion_success_rate = round(
                sum(1 for e in ingestion_events if e.get("success")) / float(len(ingestion_events)), 4
            )
            durations = [e.get("duration_ms") for e in ingestion_events if isinstance(e.get("duration_ms"), (int, float))]
            rows = [e.get("rows") for e in ingestion_events if isinstance(e.get("rows"), (int, float))]
            if durations:
                etl_ms = round(statistics.mean(durations), 2)
            if durations and rows:
                total_rows = sum(rows)
                total_seconds = sum(durations) / 1000.0
                if total_seconds > 0:
                    ingestion_throughput = round(total_rows / total_seconds, 2)

        query_metrics = _run_query_evaluation(db, dataset.id)
        email_metrics = _email_metrics(db)

        freshness_hours = None
        if dataset_row.created_at:
            freshness_hours = round((_now_utc() - dataset_row.created_at).total_seconds() / 3600.0, 2)

        metrics: Dict[str, object] = {}
        metrics["Extraction Accuracy"] = {"value": None, "note": "No OCR ground truth available."}
        metrics["Parsing Success Rate"] = {"value": parse_success_rate, "note": "From parse logs for latest file."}
        metrics["OCR Accuracy"] = {"value": None, "note": "No OCR ground truth available."}
        metrics["Table Detection Accuracy"] = {"value": None, "note": "No table detection ground truth available."}
        metrics["Schema Mapping Accuracy"] = {"value": dataset_metrics["schema_mapping_accuracy"], "note": "Row-level USN/Name/SGPA presence."}
        metrics["Validation Accuracy"] = {"value": dataset_metrics["validation_accuracy"], "note": "Row-level validation checks."}
        metrics["Duplicate Detection Rate"] = {"value": dataset_metrics["duplicate_detection_rate"], "note": "Duplicate USN ratio."}
        metrics["Missing Value Ratio"] = {"value": dataset_metrics["missing_value_ratio"], "note": "Missing USN/Name/SGPA ratio."}
        metrics["Data Consistency Score"] = {"value": dataset_metrics["data_consistency_score"], "note": "Valid rows ratio."}
        metrics["Data Integrity Score"] = {"value": dataset_metrics["data_integrity_score"], "note": "Non-missing critical fields ratio."}
        metrics["ETL/ELT Processing Time"] = {"value": etl_ms, "note": "Average ingestion duration (ms)."}
        metrics["Pipeline Success Rate"] = {"value": ingestion_success_rate, "note": "From ingestion logs."}
        metrics["Data Freshness"] = {"value": freshness_hours, "note": "Hours since latest dataset created."}
        metrics["Warehouse Sync Accuracy"] = {"value": None, "note": "No warehouse sync telemetry available."}
        metrics["Ingestion Throughput"] = {"value": ingestion_throughput, "note": "Rows per second from ingestion logs."}
        metrics["Query Accuracy"] = {"value": query_metrics["pass"], "note": "Pass rate on QA cases."}
        metrics["SQL Generation Accuracy"] = {"value": None, "note": "No SQL generation pipeline present."}
        metrics["SQL Execution Success Rate"] = {"value": None, "note": "No SQL execution logging available."}
        metrics["Complex Query Accuracy"] = {"value": query_metrics["filter_accuracy"], "note": "Proxy using filter accuracy."}
        metrics["Aggregation Accuracy"] = {"value": query_metrics["aggregation_accuracy"], "note": "Aggregation category pass rate."}
        metrics["Ranking Accuracy"] = {"value": query_metrics["ranking_accuracy"], "note": "Ranking category pass rate."}
        metrics["Filtering Accuracy"] = {"value": query_metrics["filter_accuracy"], "note": "Filtering category pass rate."}
        metrics["Multi-turn Context Accuracy"] = {"value": None, "note": "No multi-turn ground truth available."}
        metrics["Query Resolution Accuracy"] = {"value": query_metrics["pass"], "note": "Same as query pass rate."}
        metrics["Context Retention Accuracy"] = {"value": None, "note": "No multi-turn ground truth available."}
        metrics["Retrieval Precision"] = {"value": None, "note": "No relevance labels available."}
        metrics["Retrieval Recall"] = {"value": None, "note": "No relevance labels available."}
        metrics["Recall@K"] = {"value": None, "note": "No relevance labels available."}
        metrics["Precision@K"] = {"value": None, "note": "No relevance labels available."}
        metrics["Hit Rate"] = {"value": None, "note": "No relevance labels available."}
        metrics["Mean Reciprocal Rank (MRR)"] = {"value": None, "note": "No relevance labels available."}
        metrics["NDCG (Normalized Discounted Cumulative Gain)"] = {"value": None, "note": "No relevance labels available."}
        metrics["Context Precision"] = {"value": None, "note": "No relevance labels available."}
        metrics["Context Recall"] = {"value": None, "note": "No relevance labels available."}
        metrics["Context Relevance Score"] = {"value": None, "note": "No relevance labels available."}
        metrics["Chunk Relevance Score"] = {"value": None, "note": "No relevance labels available."}
        metrics["Semantic Similarity Score"] = {"value": None, "note": "No gold references beyond QA set."}
        metrics["Reranker Accuracy"] = {"value": None, "note": "No reranker labels available."}
        metrics["Groundedness Score"] = {"value": None, "note": "No grounding labels available."}
        metrics["Faithfulness Score"] = {"value": None, "note": "No grounding labels available."}
        metrics["Answer Relevancy"] = {"value": query_metrics["contains"], "note": "Contains-match pass rate."}
        metrics["Answer Correctness"] = {"value": query_metrics["pass"], "note": "Pass rate on QA cases."}
        metrics["Response Relevance"] = {"value": query_metrics["contains"], "note": "Contains-match pass rate."}
        metrics["Citation Accuracy"] = {"value": None, "note": "No citation labels available."}
        metrics["Hallucination Rate"] = {"value": round(1.0 - query_metrics["pass"], 4), "note": "1 - QA pass rate."}
        metrics["Exact Match Score"] = {"value": query_metrics["exact"], "note": "Exact-match rate on QA cases."}
        metrics["BLEU Score"] = {"value": query_metrics["bleu"], "note": "BLEU-1 on QA cases."}
        metrics["ROUGE Score"] = {"value": query_metrics["rouge_l"], "note": "ROUGE-L on QA cases."}
        metrics["Context Utilization Score"] = {"value": None, "note": "Context usage not labeled."}
        metrics["Multi-hop Reasoning Accuracy"] = {"value": None, "note": "No multi-hop labels available."}
        metrics["Agent Task Completion Rate"] = {"value": ingestion_success_rate, "note": "Using ingestion success rate as proxy."}
        metrics["Tool Invocation Accuracy"] = {"value": None, "note": "No tool-invocation labels available."}
        metrics["Workflow Success Rate"] = {"value": ingestion_success_rate, "note": "Using ingestion success rate as proxy."}
        metrics["Email Automation Success Rate"] = {"value": email_metrics["email_success_rate"], "note": "From agent_processed_emails."}
        metrics["File Classification Accuracy"] = {"value": None, "note": "No file classification labels available."}
        metrics["Agent Routing Accuracy"] = {"value": None, "note": "No routing labels available."}
        metrics["Conversation Success Rate"] = {"value": query_metrics["pass"], "note": "QA pass rate proxy."}
        metrics["User Satisfaction Score"] = {"value": None, "note": "No user feedback collected."}
        metrics["Query Latency"] = {"value": query_metrics["latency_ms_avg"], "note": "Average end-to-end latency (ms)."}
        metrics["Retrieval Latency"] = {"value": query_metrics["context_ms_avg"], "note": "Average context build time (ms)."}
        metrics["SQL Execution Latency"] = {"value": None, "note": "No SQL execution telemetry."}
        metrics["Generation Latency"] = {"value": query_metrics["generation_ms_avg"], "note": "Average generation time (ms)."}
        metrics["Upload Processing Time"] = {"value": etl_ms, "note": "Average ingestion duration (ms)."}
        metrics["End-to-End Response Time"] = {"value": query_metrics["latency_ms_avg"], "note": "Average end-to-end latency (ms)."}
        metrics["API Success Rate"] = {"value": query_metrics["pass"], "note": "QA pass rate proxy."}
        metrics["API Error Rate"] = {"value": round(1.0 - query_metrics["pass"], 4), "note": "1 - QA pass rate proxy."}
        metrics["System Uptime"] = {"value": None, "note": "No uptime telemetry available."}
        metrics["Failure Recovery Rate"] = {"value": None, "note": "No recovery telemetry available."}
        metrics["Database Query Performance"] = {"value": None, "note": "No DB timing telemetry available."}

        report = {
            "dataset_name": dataset_row.dataset_name,
            "dataset_id": dataset.id,
            "processed_excel_path": str(processed_path),
            "metrics": metrics,
        }

        output_path = ROOT / "tests" / "metrics_report.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        print(json.dumps(report, indent=2))
        print("\nSaved metrics report to {}".format(output_path))
    finally:
        db.close()


if __name__ == "__main__":
    main()
