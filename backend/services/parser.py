import io
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd
import pdfplumber

from .metrics import log_event

try:
    from llama_index.core.node_parser import MarkdownNodeParser
    from llama_index.core.schema import Document
    from llama_parse import LlamaParse

    LLAMA_AVAILABLE = True
except ImportError:
    MarkdownNodeParser = None
    Document = None
    LlamaParse = None
    LLAMA_AVAILABLE = False


IDENTIFIER_KEYWORDS = {"usn", "reg", "register", "registration", "roll", "studentid", "student_id"}
NAME_KEYWORDS = {"name", "studentname", "student_name", "candidate", "student"}
SGPA_KEYWORDS = {"sgpa", "s.g.p.a", "semestergpa", "gpa"}
HEADER_IGNORE_TOKENS = {"slno", "sl", "usn", "name", "gr", "gp"}
CONTROL_PATTERNS = [
    re.compile(r"^\d+:\d+:\d+:\d+$"),
    re.compile(r"^noofpages", re.IGNORECASE),
    re.compile(r"^semester", re.IGNORECASE),
    re.compile(r"^course", re.IGNORECASE),
    re.compile(r"^branchdept", re.IGNORECASE),
    re.compile(r"^academicyear", re.IGNORECASE),
    re.compile(r"^msramaiah", re.IGNORECASE),
    re.compile(r"^provisionalgradereport", re.IGNORECASE),
]
USN_PATTERN = re.compile(r"^(?=.*[A-Z])(?=.*\d)[A-Z0-9][A-Z0-9-]{2,24}$")
LLAMA_PARSE_API_KEY = os.getenv("LLAMA_CLOUD_API_KEY") or os.getenv("LLAMA_PARSE_API_KEY")


@dataclass
class ParsedStudent:
    usn: str
    name: str
    semester: int
    sgpa: float
    cgpa: float
    pass_fail: str
    results: List[Dict[str, Optional[float]]]


def _extract_semester_from_filename(filename: str) -> int:
    """Extract semester number from the upload filename, defaulting to 1."""
    match = re.search(r"(?:sem|semester|s)(?:ester)?\s*(\d+)", filename.lower())
    if match:
        return int(match.group(1))
    return 1


def parse_uploaded_file(file_bytes: bytes, filename: str) -> Tuple[List[ParsedStudent], pd.DataFrame]:
    start = time.perf_counter()
    metrics: Dict[str, object] = {
        "filename": filename,
        "extension": Path(filename).suffix.lower() or "unknown",
        "used_llama": False,
        "frames": 0,
        "raw_rows": 0,
    }
    semester = _extract_semester_from_filename(filename)
    try:
        llama_students = _parse_with_llama(file_bytes, filename, semester)
        if llama_students:
            metrics["used_llama"] = True
            deduplicated = _deduplicate_students(llama_students)
            processed_df = _students_to_dataframe(deduplicated)
            metrics.update(
                {
                    "parsed_students": len(llama_students),
                    "deduped_students": len(deduplicated),
                    "processed_rows": int(len(processed_df)),
                    "success": True,
                    "duration_ms": int((time.perf_counter() - start) * 1000),
                }
            )
            log_event("parse", metrics)
            return deduplicated, processed_df

        extension = Path(filename).suffix.lower()
        frames: List[pd.DataFrame] = []

        if extension == ".xlsx":
            excel_file = pd.ExcelFile(io.BytesIO(file_bytes))
            for sheet_name in excel_file.sheet_names:
                sheet_df = pd.read_excel(excel_file, sheet_name=sheet_name, header=None, dtype=object)
                if not sheet_df.empty:
                    frames.append(sheet_df)
        elif extension == ".pdf":
            frames.extend(_extract_pdf_frames(file_bytes))
        else:
            raise ValueError("Unsupported file type. Only .xlsx and .pdf files are allowed.")

        metrics["frames"] = len(frames)
        metrics["raw_rows"] = int(sum(int(frame.shape[0]) for frame in frames))

        if not frames:
            raise ValueError("No readable tabular data found in the uploaded file.")

        students: List[ParsedStudent] = []
        for raw_frame in frames:
            students.extend(_normalize_frame(raw_frame, semester))

        if not students:
            raise ValueError("Could not detect student result rows from the uploaded file.")

        deduplicated = _deduplicate_students(students)
        processed_df = _students_to_dataframe(deduplicated)
        metrics.update(
            {
                "parsed_students": len(students),
                "deduped_students": len(deduplicated),
                "processed_rows": int(len(processed_df)),
                "success": True,
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        log_event("parse", metrics)
        return deduplicated, processed_df
    except Exception as exc:
        metrics.update(
            {
                "success": False,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - start) * 1000),
            }
        )
        log_event("parse", metrics)
        raise


def _parse_with_llama(file_bytes: bytes, filename: str, semester: int) -> List[ParsedStudent]:
    if not LLAMA_AVAILABLE or not LLAMA_PARSE_API_KEY:
        return []

    suffix = Path(filename).suffix.lower() or ".bin"
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(file_bytes)
            temp_path = Path(handle.name)

        parser = LlamaParse(api_key=LLAMA_PARSE_API_KEY, result_type="markdown", verbose=False)
        llama_documents = parser.load_data(str(temp_path))
        markdown_documents = _llama_documents_to_markdown(llama_documents)
        if not markdown_documents:
            return []

        node_parser = MarkdownNodeParser()
        nodes = node_parser.get_nodes_from_documents(
            [Document(text=markdown_text) for markdown_text in markdown_documents]
        )

        frames = _markdown_nodes_to_frames([node.text for node in nodes if getattr(node, "text", "").strip()])
        if not frames:
            return []

        students: List[ParsedStudent] = []
        for frame in frames:
            students.extend(_normalize_frame(frame, semester))
        return students
    except Exception:
        return []
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _llama_documents_to_markdown(llama_documents: Sequence[object]) -> List[str]:
    markdown_documents: List[str] = []
    for item in llama_documents:
        text = getattr(item, "text", "") or getattr(item, "markdown", "")
        if text:
            markdown_documents.append(str(text))
    return markdown_documents


def _markdown_nodes_to_frames(markdown_texts: Sequence[str]) -> List[pd.DataFrame]:
    tables: List[pd.DataFrame] = []
    for markdown_text in markdown_texts:
        tables.extend(_extract_markdown_tables(markdown_text))
    return tables


def _extract_markdown_tables(markdown_text: str) -> List[pd.DataFrame]:
    lines = [line.rstrip() for line in markdown_text.splitlines()]
    tables: List[pd.DataFrame] = []
    current_block: List[str] = []

    for line in lines:
        stripped = line.strip()
        if "|" in stripped:
            current_block.append(stripped)
            continue
        if current_block:
            frame = _markdown_block_to_frame(current_block)
            if frame is not None:
                tables.append(frame)
            current_block = []

    if current_block:
        frame = _markdown_block_to_frame(current_block)
        if frame is not None:
            tables.append(frame)

    return tables


def _markdown_block_to_frame(lines: Sequence[str]) -> Optional[pd.DataFrame]:
    normalized_rows: List[List[str]] = []
    for line in lines:
        compact = line.strip().strip("|")
        if not compact:
            continue
        if re.fullmatch(r"[:\-\s|]+", line):
            continue
        cells = [cell.strip() for cell in compact.split("|")]
        normalized_rows.append(cells)

    if len(normalized_rows) < 2:
        return None

    width = max(len(row) for row in normalized_rows)
    padded_rows = [row + [""] * (width - len(row)) for row in normalized_rows]
    return pd.DataFrame(padded_rows)


def _extract_pdf_frames(file_bytes: bytes) -> List[pd.DataFrame]:
    frames: List[pd.DataFrame] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            for table in tables or []:
                if table:
                    frame = pd.DataFrame(table)
                    if not frame.empty:
                        frames.append(frame)
    return frames


def _normalize_frame(raw_df: pd.DataFrame, semester: int) -> List[ParsedStudent]:
    cleaned_df = raw_df.copy().fillna("")
    cleaned_df = cleaned_df.apply(lambda column: column.map(_as_clean_text))
    cleaned_df = cleaned_df.loc[~(cleaned_df.eq("").all(axis=1))]
    cleaned_df = cleaned_df.loc[:, ~(cleaned_df.eq("").all(axis=0))]

    if cleaned_df.empty:
        return []

    header_indices = _find_header_rows(cleaned_df)
    if not header_indices:
        return []

    partial_students: List[ParsedStudent] = []
    for idx, header_idx in enumerate(header_indices):
        end_idx = header_indices[idx + 1] if idx + 1 < len(header_indices) else len(cleaned_df)
        section_students = _parse_section(cleaned_df, header_idx, end_idx, semester)
        partial_students.extend(section_students)

    return partial_students


def _find_header_rows(df: pd.DataFrame) -> List[int]:
    rows: List[int] = []
    for idx in range(len(df)):
        row_tokens = [_sanitize_token(value) for value in df.iloc[idx].tolist()]
        if any(token == "usn" for token in row_tokens) and any(token == "name" for token in row_tokens):
            rows.append(idx)
    return rows


def _parse_section(df: pd.DataFrame, header_idx: int, end_idx: int, semester: int) -> List[ParsedStudent]:
    header_row = df.iloc[header_idx].tolist()
    usn_col = _find_column_in_row(header_row, IDENTIFIER_KEYWORDS)
    name_col = _find_column_in_row(header_row, NAME_KEYWORDS)

    if usn_col is None or name_col is None:
        return []

    sgpa_col = _find_column_in_row(header_row, SGPA_KEYWORDS)
    grade_row_idx = _find_grade_marker_row(df, header_idx, end_idx)

    if grade_row_idx is not None:
        return _parse_grade_section(df, header_idx, grade_row_idx, end_idx, usn_col, name_col, sgpa_col, semester)

    if sgpa_col is not None:
        return _parse_summary_section(df, header_idx, end_idx, usn_col, name_col, sgpa_col, semester)

    return []


def _find_grade_marker_row(df: pd.DataFrame, header_idx: int, end_idx: int) -> Optional[int]:
    scan_end = min(end_idx, header_idx + 8)
    for idx in range(header_idx + 1, scan_end):
        tokens = [_sanitize_token(value) for value in df.iloc[idx].tolist()]
        gr_count = sum(1 for token in tokens if token == "gr")
        gp_count = sum(1 for token in tokens if token == "gp")
        if gr_count >= 2 and gp_count >= 1:
            return idx
    return None


def _parse_grade_section(
    df: pd.DataFrame,
    header_idx: int,
    grade_row_idx: int,
    end_idx: int,
    usn_col: int,
    name_col: int,
    sgpa_col: Optional[int],
    semester: int,
) -> List[ParsedStudent]:
    grade_row = [_sanitize_token(value) for value in df.iloc[grade_row_idx].tolist()]
    gr_columns = [idx for idx, token in enumerate(grade_row) if token == "gr" and idx > name_col]
    gp_columns = [idx for idx, token in enumerate(grade_row) if token == "gp" and idx > name_col]

    if not gr_columns:
        return []

    context_start = max(0, header_idx - 2)
    subject_mappings = _build_subject_mappings(df, context_start, header_idx, grade_row_idx, gr_columns, gp_columns)
    if not subject_mappings:
        return []

    students: List[ParsedStudent] = []
    for row_idx in range(grade_row_idx + 1, end_idx):
        row = df.iloc[row_idx]
        usn = _normalize_usn(row.iloc[usn_col] if usn_col < len(row) else "")
        name = _normalize_name(row.iloc[name_col] if name_col < len(row) else "")
        if not usn or not name:
            continue

        results: List[Dict[str, Optional[float]]] = []
        weighted_points: List[float] = []
        credits_for_points: List[float] = []
        has_fail = False

        for mapping in subject_mappings:
            grade_value = _as_clean_text(row.iloc[mapping["gr_col"]]) if mapping["gr_col"] < len(row) else ""
            gp_value = _as_float(row.iloc[mapping["gp_col"]]) if mapping["gp_col"] is not None and mapping["gp_col"] < len(row) else None
            if not grade_value and gp_value is None:
                continue

            normalized_grade = grade_value.upper()
            if normalized_grade == "F":
                has_fail = True

            if gp_value is not None and mapping["credits"] is not None and mapping["credits"] > 0:
                weighted_points.append(gp_value)
                credits_for_points.append(mapping["credits"])

            results.append(
                {
                    "subject": mapping["subject"],
                    "grade": normalized_grade or "NA",
                    "gp": gp_value,
                }
            )

        sgpa = _as_float(row.iloc[sgpa_col]) if sgpa_col is not None and sgpa_col < len(row) else None
        if sgpa is None:
            sgpa = _compute_sgpa_from_weighted_points(weighted_points, credits_for_points)

        students.append(
            ParsedStudent(
                usn=usn,
                name=name,
                semester=semester,
                sgpa=sgpa,
                cgpa=sgpa,
                pass_fail="FAIL" if has_fail else "PASS",
                results=results,
            )
        )

    return students


def _build_subject_mappings(
    df: pd.DataFrame,
    context_start: int,
    header_idx: int,
    grade_row_idx: int,
    gr_columns: Sequence[int],
    gp_columns: Sequence[int],
) -> List[Dict[str, Optional[float]]]:
    mappings: List[Dict[str, Optional[float]]] = []
    for idx, gr_col in enumerate(gr_columns):
        next_gr = gr_columns[idx + 1] if idx + 1 < len(gr_columns) else df.shape[1]
        block_end = next_gr - 1
        gp_col = next((col for col in gp_columns if gr_col < col <= block_end), None)
        subject = _build_subject_name(df, context_start, grade_row_idx, gr_col, block_end)
        if not subject:
            subject = f"Subject {idx + 1}"
        credits = _extract_subject_credits(df, context_start, grade_row_idx, gr_col, block_end)
        mappings.append(
            {
                "subject": subject,
                "gr_col": gr_col,
                "gp_col": gp_col,
                "credits": credits,
            }
        )
    return mappings


def _build_subject_name(df: pd.DataFrame, start_row: int, grade_row_idx: int, start_col: int, end_col: int) -> str:
    pieces: List[str] = []
    for row_idx in range(start_row, grade_row_idx):
        for col_idx in range(start_col, min(end_col + 1, df.shape[1])):
            token = _as_clean_text(df.iat[row_idx, col_idx])
            if not token:
                continue
            normalized = _sanitize_token(token)
            if not normalized or normalized in HEADER_IGNORE_TOKENS:
                continue
            if any(pattern.match(normalized) for pattern in CONTROL_PATTERNS):
                continue
            if "gr" in normalized and "gp" in normalized:
                continue
            if re.search(r"(?:\b(?:o|a\+|a|b\+|b|c|d|e|p|f|x|ne)\b\s*\d{0,2}\s*){3,}", token, re.IGNORECASE):
                continue
            if token not in pieces:
                pieces.append(token.replace("\n", " "))
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()


def _extract_subject_credits(df: pd.DataFrame, start_row: int, grade_row_idx: int, start_col: int, end_col: int) -> Optional[float]:
    for row_idx in range(start_row, grade_row_idx):
        for col_idx in range(start_col, min(end_col + 1, df.shape[1])):
            token = _as_clean_text(df.iat[row_idx, col_idx])
            if re.fullmatch(r"\d+:\d+:\d+:\d+", token):
                return float(sum(int(part) for part in token.split(":")))
    return None


def _parse_summary_section(
    df: pd.DataFrame,
    header_idx: int,
    end_idx: int,
    usn_col: int,
    name_col: int,
    sgpa_col: int,
    semester: int,
) -> List[ParsedStudent]:
    students: List[ParsedStudent] = []
    for row_idx in range(header_idx + 1, end_idx):
        row = df.iloc[row_idx]
        usn = _normalize_usn(row.iloc[usn_col] if usn_col < len(row) else "")
        name = _normalize_name(row.iloc[name_col] if name_col < len(row) else "")
        sgpa = _as_float(row.iloc[sgpa_col] if sgpa_col < len(row) else None)
        if not usn or not name or sgpa is None:
            continue

        students.append(
            ParsedStudent(
                usn=usn,
                name=name,
                semester=semester,
                sgpa=sgpa,
                cgpa=sgpa,
                pass_fail="PASS",
                results=[],
            )
        )
    return students


def _find_column_in_row(row: Sequence[object], keywords: set[str]) -> Optional[int]:
    best_idx = None
    best_score = -1
    for idx, value in enumerate(row):
        token = _sanitize_token(value)
        score = 0
        for keyword in keywords:
            if keyword in token:
                score = max(score, len(keyword))
        if score > best_score:
            best_idx = idx
            best_score = score
    return best_idx if best_score > 0 else None


def _compute_sgpa_from_weighted_points(weighted_points: Sequence[float], credits: Sequence[float]) -> float:
    if not weighted_points or not credits:
        return 0.0
    total_points = float(pd.Series(weighted_points, dtype="float64").sum())
    total_credits = float(pd.Series(credits, dtype="float64").sum())
    if total_credits == 0:
        return 0.0
    return round(total_points / total_credits, 2)


def _normalize_usn(value: object) -> str:
    text = _as_clean_text(value).upper()
    if not text or "\n" in text or " " in text:
        return ""
    if not USN_PATTERN.fullmatch(text):
        return ""
    return text


def _normalize_name(value: object) -> str:
    text = _as_clean_text(value)
    if not text or text.upper() == "NAME" or "\n" in text:
        return ""
    return re.sub(r"\s+", " ", text)


def _deduplicate_students(students: Sequence[ParsedStudent]) -> List[ParsedStudent]:
    merged: Dict[str, ParsedStudent] = {}
    for student in students:
        if student.usn not in merged:
            merged[student.usn] = student
            continue

        existing = merged[student.usn]
        results_by_subject = {result["subject"]: result for result in existing.results}
        for result in student.results:
            results_by_subject[result["subject"]] = result

        merged[student.usn] = ParsedStudent(
            usn=student.usn,
            name=student.name or existing.name,
            semester=student.semester if student.semester > 0 else existing.semester,
            sgpa=student.sgpa if student.sgpa > 0 else existing.sgpa,
            cgpa=student.cgpa if student.cgpa > 0 else existing.cgpa,
            pass_fail="FAIL" if "FAIL" in {existing.pass_fail, student.pass_fail} else "PASS",
            results=list(results_by_subject.values()),
        )
    return sorted(merged.values(), key=lambda item: item.usn)


def _students_to_dataframe(students: Sequence[ParsedStudent]) -> pd.DataFrame:
    rows: List[Dict[str, object]] = []
    for student in students:
        row: Dict[str, object] = {
            "USN": student.usn,
            "Name": student.name,
            "SGPA": student.sgpa,
            "Pass/Fail": student.pass_fail,
        }
        for result in student.results:
            subject = result["subject"]
            row[f"{subject} Grade"] = result["grade"]
            row[f"{subject} GP"] = result["gp"]
        rows.append(row)
    return pd.DataFrame(rows)


def _sanitize_token(value: object) -> str:
    text = _as_clean_text(value).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def _as_clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _as_float(value: object) -> Optional[float]:
    text = _as_clean_text(value)
    if not text or "\n" in text:
        return None
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        return float(match.group(0))
