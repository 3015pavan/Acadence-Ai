#!/usr/bin/env python3
"""Direct one-message smoke test for the GCP Gemini API.

Run:
    python tools/gcp_gemini_simple_test.py
"""

from __future__ import annotations

import json
import os
import time
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main() -> int:
    api_key = os.getenv("GCP_GEMINI_KEY", "").strip()
    primary_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    base_url = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")

    if not api_key:
        print("Missing GCP_GEMINI_KEY in environment or .env")
        return 1

    prompt = "Say hello in one short sentence."
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 64,
            "temperature": 0.2,
        },
    }

    candidate_models = [primary_model]

    # Discover available models for this key so we can fall back to a model
    # that the project actually has access to.
    try:
        discovery = requests.get(f"{base_url}/v1beta/models?key={api_key}", timeout=30)
        if discovery.ok:
            discovery_payload = discovery.json()
            for item in discovery_payload.get("models", []):
                name = str(item.get("name", "")).split("/")[-1]
                methods = item.get("supportedGenerationMethods", [])
                if name and "generateContent" in methods and name not in candidate_models:
                    candidate_models.append(name)
    except Exception:
        pass

    # Keep the test small, but allow a few discovered candidates.
    candidate_models = candidate_models[:5]
    data = None
    last_error = None

    for model in candidate_models:
        url = f"{base_url}/v1beta/models/{model}:generateContent?key={api_key}"
        for attempt in range(3):
            try:
                response = requests.post(url, json=payload, timeout=30)
                print(f"Model {model} attempt {attempt + 1}: HTTP {response.status_code}")
                if response.status_code == 429:
                    time.sleep(2 ** attempt)
                    last_error = f"429 Too Many Requests for model {model}"
                    continue
                if not response.ok:
                    try:
                        error_body = response.json()
                    except Exception:
                        error_body = response.text
                    print(json.dumps({"model": model, "error": error_body}, indent=2, ensure_ascii=False))
                response.raise_for_status()
                data = response.json()
                break
            except Exception as exc:
                last_error = f"{model}: {exc}"
                break
        if data is not None:
            break

    if data is None:
        print(f"Request failed: {last_error or 'unknown error'}")
        return 1

    text = ""
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {}) if isinstance(candidate, dict) else {}
        for part in content.get("parts", []) if isinstance(content, dict) else []:
            if part.get("text"):
                text += part["text"]

    print(json.dumps({"prompt": prompt, "reply": text.strip()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())