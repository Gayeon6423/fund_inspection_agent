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
SYSTEM_PROMPT_VERSION = os.getenv("SYSTEM_PROMPT_VERSION", "system_prompt_v10")
_prompt_path = Path(__file__).resolve().parent / "prompt" / f"{SYSTEM_PROMPT_VERSION}.txt"
with open(_prompt_path, encoding="utf-8") as _f:
    SYSTEM_PROMPT = _f.read().strip()
