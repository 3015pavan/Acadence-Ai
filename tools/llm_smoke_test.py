#!/usr/bin/env python3
"""Tiny direct smoke test for the Gemini-backed LLM helper.

Run:
    python tools/llm_smoke_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.intelligence import _llm_chat_json


def main() -> int:
    system_prompt = "Return only valid JSON with keys status and message."
    user_prompt = (
        "Perform a minimal LLM smoke test. "
        "Set status to ok and message to a short sentence that confirms the model is reachable."
    )

    response = _llm_chat_json(system_prompt, user_prompt, timeout_seconds=20)
    if response is None:
        print("LLM smoke test failed: no response returned.")
        return 1

    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())