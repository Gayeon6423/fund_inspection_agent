import base64
import json
import re
import urllib.request
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(), override=True)
API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("LLM_MODEL")
SYSTEM_PROMPT_VERSION = os.getenv("GENERATE_SYSTEM_PROMPT_VERSION") or os.getenv("SYSTEM_PROMPT_VERSION", "generate_system_prompt_v1")
_prompt_dir = Path(__file__).resolve().parent / "prompt"
_prompt_candidates = [
    _prompt_dir / f"{SYSTEM_PROMPT_VERSION}.md",
    _prompt_dir / f"{SYSTEM_PROMPT_VERSION}.txt",
]
for _candidate in _prompt_candidates:
    if _candidate.exists():
        _prompt_path = _candidate
        SYSTEM_PROMPT_VERSION = _candidate.stem
        break
else:
    raise FileNotFoundError(
        f"프롬프트 파일을 찾을 수 없습니다. 확인 대상: {', '.join(str(p) for p in _prompt_candidates)}"
    )

with open(_prompt_path, encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()
