"""
fund_core.py — 공유 비즈니스 로직
app.py(Streamlit)와 api_server.py(FastAPI) 양쪽에서 import해서 사용합니다.
"""
import base64
import json
import urllib.error
import urllib.request

META_KEYS = {"category", "summary", "summary_script", "summary_manual", "match_rate", "mismatches"}


# ── LLM API ────────────────────────────────────────────

def call_llm(user_content: list, model: str, api_key: str, system_prompt: str) -> str:
    """범용 LLM API 호출. user_content는 메시지 content 블록 리스트."""
    payload = {
        "model": model,
        "max_tokens": 16000,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    if any(block.get("type") == "document" for block in user_content):
        headers["anthropic-beta"] = "pdfs-2024-09-25"

    req = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["content"][0]["text"]


def call_llm_compare(script_json: dict, manual_pdf_bytes: bytes,
                        model: str, api_key: str, system_prompt: str) -> str:
    """판매대본 JSON + 설명서 PDF를 LLM에 전달해 비교 분석 결과(텍스트)를 반환."""
    user_content = [
        {
            "type": "text",
            "text": f"[판매대본 JSON]\n{json.dumps(script_json, ensure_ascii=False, indent=2)}",
        },
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(manual_pdf_bytes).decode("utf-8"),
            },
        },
    ]
    return call_llm(user_content, model, api_key, system_prompt)


# ── 응답 파싱 ─────────────────────────────────────────────

def parse_json_from_text(answer_text: str) -> dict:
    """LLM 응답 텍스트에서 JSON 블록을 추출해 파싱."""
    start = answer_text.find("{")
    end = answer_text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("LLM 응답에서 JSON 본문을 찾지 못했습니다.")
    return json.loads(answer_text[start:end])


# ── 결과 처리 ─────────────────────────────────────────────

def calc_match_rate(result_json: dict):
    """일치율(%) 계산. JSON에 match_rate 필드가 있으면 그대로 사용."""
    if isinstance(result_json.get("match_rate"), int):
        return result_json["match_rate"]

    match_count = 0
    total_count = 0
    for value in result_json.values():
        if isinstance(value, list) and value:
            label = value[0]
            if label in ("일치", "불일치"):
                total_count += 1
                if label == "일치":
                    match_count += 1

    if total_count == 0:
        return None
    return round((match_count / total_count) * 100)


def build_comparison_rows(result_json: dict) -> list:
    """결과 JSON → 비교 표 행 리스트 [{"항목", "판정", "근거"}, ...]"""
    rows = []
    for key, value in result_json.items():
        if key in META_KEYS:
            continue
        if isinstance(value, list) and value:
            label = value[0]
            if label in ("일치", "불일치"):
                rows.append({
                    "항목": key,
                    "판정": label,
                    "근거": "\n".join(str(x) for x in value[1:]) if len(value) > 1 else "",
                })

    # v3 형태 대응: mismatches만 오는 경우
    if not rows and isinstance(result_json.get("mismatches"), list):
        for item in result_json["mismatches"]:
            rows.append({"항목": "mismatches", "판정": "불일치", "근거": str(item)})
    return rows


def summary_manual_to_text(summary_manual) -> str:
    """summary_manual 필드를 사람이 읽을 수 있는 문자열로 변환."""
    if isinstance(summary_manual, dict):
        lines = [f"- {k}: {v}" for k, v in summary_manual.items()]
        return "\n".join(lines) if lines else "-"
    return summary_manual if summary_manual else "-"
