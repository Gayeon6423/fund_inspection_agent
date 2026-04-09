import base64
import csv
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from zipfile import ZipFile

import streamlit as st

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
except ImportError:
    try:
        from streamlit.scriptrunner import add_script_run_ctx
    except ImportError:
        def add_script_run_ctx(t):
            return t
from dotenv import find_dotenv, load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from excel_json.excel_to_json import convert_excel_to_json_by_sheets


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"

UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads_external"
OUTPUT_EXCEL_JSON_DIR = PROJECT_ROOT / "data" / "output_excel_json"
OUTPUT_AGENT_DIR = PROJECT_ROOT / "data" / "output_agent"
PROMPT_DIR = Path(__file__).resolve().parent / "prompt"
META_KEYS = {"category", "summary", "summary_script", "summary_manual", "match_rate", "mismatches"}


def safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", value).strip()


def parse_sheet_names_from_xlsx_bytes(raw: bytes):
    with ZipFile(BytesIO(raw)) as zf:
        wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

        rel_map = {}
        for rel in rel_root.findall(f"{{{NS_REL_PACKAGE}}}Relationship"):
            rel_map[rel.attrib["Id"]] = rel.attrib["Target"]

        names = []
        for sheet in wb_root.findall(f".//{{{NS_MAIN}}}sheet"):
            rid = sheet.attrib.get(f"{{{NS_REL_OFFICE}}}id")
            if rid in rel_map:
                names.append(sheet.attrib.get("name", ""))
        return names


def parse_json_from_text(answer_text: str):
    start = answer_text.find("{")
    end = answer_text.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("Claude 응답에서 JSON 본문을 찾지 못했습니다.")
    return json.loads(answer_text[start:end])


def calc_match_rate(result_json: dict):
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


def call_claude_compare(script_json: dict, manual_pdf_bytes: bytes, model: str, api_key: str, system_prompt: str):
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [
            {
                "role": "user",
                "content": [
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
                ],
            }
        ],
    }

    request = urllib.request.Request(
        url="https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "pdfs-2024-09-25",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=180) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["content"][0]["text"]


def to_html_text(value):
    if value is None:
        return "-"
    text = str(value)
    text = html.escape(text).replace("\n", "<br>")
    return text


def render_info_block(title: str, body):
    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.2); border-radius:14px; padding:14px; background:var(--secondary-background-color); height:100%;">
          <div style="font-size:14px; color:var(--text-color); opacity:0.65; margin-bottom:8px;">{to_html_text(title)}</div>
          <div style="font-size:15px; line-height:1.6; color:var(--text-color);">{to_html_text(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_rate_card(sheet: str, rate):
    rate_text = "-" if rate is None else f"{rate}%"
    rate_color = "var(--text-color)"
    if isinstance(rate, int):
        if rate >= 80:
            rate_color = "#22c55e"
        elif rate >= 60:
            rate_color = "#f59e0b"
        else:
            rate_color = "#ef4444"

    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.2); border-radius:16px; padding:16px; background:var(--secondary-background-color);">
          <div style="font-size:14px; color:var(--text-color); opacity:0.65; margin-bottom:6px;">시트</div>
          <div style="font-size:16px; font-weight:700; margin-bottom:12px; color:var(--text-color);">{to_html_text(sheet)}</div>
          <div style="font-size:13px; color:var(--text-color); opacity:0.65;">일치율</div>
          <div style="font-size:32px; font-weight:800; color:{rate_color}; line-height:1.2;">{rate_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def summary_manual_to_text(summary_manual):
    if isinstance(summary_manual, dict):
        lines = []
        for k, v in summary_manual.items():
            lines.append(f"- {k}: {v}")
        return "\n".join(lines) if lines else "-"
    return summary_manual if summary_manual else "-"


def build_comparison_rows(result_json: dict):
    rows = []
    for key, value in result_json.items():
        if key in META_KEYS:       
            continue
        if isinstance(value, list) and value:
            label = value[0]
            if label in ("일치", "불일치"):
                rows.append(
                    {
                        "항목": key,
                        "판정": label,
                        "근거": "\n".join(str(x) for x in value[1:]) if len(value) > 1 else "",
                    }
                )

    # v3 형태 대응: mismatches만 오는 경우
    if not rows and isinstance(result_json.get("mismatches"), list):
        for item in result_json["mismatches"]:
            rows.append({"항목": "mismatches", "판정": "불일치", "근거": str(item)})
    return rows


def status_icon(status: str) -> str:
    if "오류" in status:
        return "❌"
    if "중" in status:
        return "🔄"
    if "완료" in status:
        return "✅"
    return "⏳"


def render_status_panel(container, sheets, convert_status_map, analyze_status_map):
    with container.container():
        if not sheets:
            st.caption("시트를 선택하면 상태가 표시됩니다.")
            return

        st.markdown("### 변환 상태")
        for sheet in sheets:
            status = convert_status_map.get(sheet, "대기")
            st.markdown(f"- {status_icon(status)} **{sheet}**: {status}")

        st.markdown("### 분석 상태")
        for sheet in sheets:
            status = analyze_status_map.get(sheet, "대기")
            st.markdown(f"- {status_icon(status)} **{sheet}**: {status}")


def rows_to_csv(rows: list) -> bytes:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["항목", "판정", "근거"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def render_comparison_table(rows, verdict_filter: str):
    if verdict_filter != "전체":
        rows = [r for r in rows if r["판정"] == verdict_filter]

    if not rows:
        st.info("조건에 맞는 항목이 없습니다.")
        return

    st.markdown(
        """
        <style>
        .cmp-table { width:100%; border-collapse: collapse; font-size:14px; color:var(--text-color); }
        .cmp-table th {
            text-align:left;
            background:var(--secondary-background-color);
            color:var(--text-color);
            padding:10px;
            border:1px solid rgba(128,128,128,0.25);
        }
        .cmp-table td {
            vertical-align:top;
            padding:10px;
            border:1px solid rgba(128,128,128,0.25);
            color:var(--text-color);
        }
        .badge-ok { color:#065f46; font-weight:700; background:#d1fae5; padding:3px 8px; border-radius:999px; }
        .badge-no { color:#991b1b; font-weight:700; background:#fee2e2; padding:3px 8px; border-radius:999px; }
        .memo { display:block; white-space:pre-wrap; line-height:1.5; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    table_html = [
        "<table class='cmp-table'>",
        "<thead><tr><th style='width:28%'>항목</th><th style='width:12%'>판정</th><th>근거</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        verdict = row["판정"]
        badge = "<span class='badge-ok'>일치</span>" if verdict == "일치" else "<span class='badge-no'>불일치</span>"
        memo_full = html.escape(str(row.get("근거", "") or "-")).replace("\n", "<br>")
        table_html.append(
            "<tr>"
            f"<td>{html.escape(str(row['항목']))}</td>"
            f"<td>{badge}</td>"
            f"<td><span class='memo'>{memo_full}</span></td>"
            "</tr>"
        )
    table_html.append("</tbody></table>")
    st.markdown("".join(table_html), unsafe_allow_html=True)


# def main():
#     load_dotenv(find_dotenv(), override=True)
#     api_key = os.getenv("ANTHROPIC_API_KEY")
#     model = os.getenv("LLM_MODEL")
#     system_prompt_version = os.getenv("SYSTEM_PROMPT_VERSION")

#     prompt_path = PROMPT_DIR / f"{system_prompt_version}.txt"
#     if not prompt_path.exists():
#         st.error(f"프롬프트 파일이 없습니다: {prompt_path}")
#         st.stop()
#     system_prompt = prompt_path.read_text(encoding="utf-8")

def get_setting(name: str, default: str | None = None):
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)


def main():
    st.set_page_config(page_title="펀드판매대본 점검", layout="wide")

    load_dotenv(find_dotenv(), override=True)

    api_key = get_setting("ANTHROPIC_API_KEY")
    model = get_setting("LLM_MODEL", "claude-sonnet-4-6")
    system_prompt_version = get_setting("SYSTEM_PROMPT_VERSION", "system_prompt_v5")

    prompt_path = PROMPT_DIR / f"{system_prompt_version}.txt"
    if not prompt_path.exists():
        st.error(
            f"프롬프트 파일이 없습니다: {prompt_path}\n"
            f"SYSTEM_PROMPT_VERSION 값을 확인해주세요."
        )
        st.stop()

    system_prompt = prompt_path.read_text(encoding="utf-8")

    st.set_page_config(page_title="펀드판매대본 점검", layout="wide")
    st.markdown(
        """
        <style>
        .app-header {
            background: var(--secondary-background-color);
            border: 1px solid rgba(128,128,128,0.25);
            border-radius: 14px;
            padding: 16px 18px;
            margin-bottom: 14px;
        }
        .app-header .title { font-size:30px; font-weight:800; color:var(--text-color); line-height:1.2; }
        .app-header .subtitle { font-size:16px; font-weight:600; color:var(--text-color); opacity:0.8; margin-top:6px; }
        </style>
        <div class="app-header">
          <div class="title">📄 펀드판매대본 점검 시스템</div>
          <div class="subtitle">펀드 판매대본과 상품 설명서의 일치도를 분석합니다.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.warning("주의: 복호화된 파일만 업로드해주세요.")

    script_excel = st.sidebar.file_uploader("• 판매대본 파일 업로드 (Excel)", type=["xlsx"])

    manual_pdf = st.sidebar.file_uploader("• 설명서 파일 업로드 (PDF)", type=["pdf"])

    selected_sheets = []
    if script_excel is not None:
        excel_bytes = script_excel.getvalue()
        try:
            sheet_names = parse_sheet_names_from_xlsx_bytes(excel_bytes)
            selected_sheets = st.sidebar.multiselect(
                "판매대본 시트 선택",
                options=sheet_names,
                default=sheet_names[:1],
            )
        except Exception as e:
            st.error(f"시트 목록을 읽지 못했습니다: {e}")
            st.stop()

    convert_status_map = st.session_state.get("convert_status", {})
    analyze_status_map = st.session_state.get("analyze_status", {})
    status_placeholder = st.sidebar.empty()
    render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

    run = st.sidebar.button("일치도 분석 실행", type="primary", use_container_width=True)
    if run:
        if not api_key:
            st.error("ANTHROPIC_API_KEY가 설정되지 않았습니다. .env를 확인해주세요.")
            st.stop()
        if script_excel is None or manual_pdf is None:
            st.error("판매대본 파일과 설명서 파일을 모두 업로드해주세요.")
            st.stop()
        if not selected_sheets:
            st.error("분석할 시트를 1개 이상 선택해주세요.")
            st.stop()

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_EXCEL_JSON_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_AGENT_DIR.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        excel_upload_path = UPLOAD_DIR / f"{ts}_{safe_name(script_excel.name)}"
        pdf_upload_path = UPLOAD_DIR / f"{ts}_{safe_name(manual_pdf.name)}"
        excel_upload_path.write_bytes(script_excel.getvalue())
        manual_pdf_bytes = manual_pdf.getvalue()
        pdf_upload_path.write_bytes(manual_pdf_bytes)

        analysis_results = []
        convert_status_map = {sheet: "대기" for sheet in selected_sheets}
        analyze_status_map = {sheet: "대기" for sheet in selected_sheets}
        st.session_state["convert_status"] = convert_status_map
        st.session_state["analyze_status"] = analyze_status_map
        render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

        for sheet in selected_sheets:
            sheet_start = time.time()
            timer_ph = st.empty()
            current_step = ["변환 중"]
            done_event = threading.Event()

            def _timer_tick(ph, start, step_ref, stop):
                while not stop.wait(timeout=1):
                    elapsed = int(time.time() - start)
                    ph.info(f"[{sheet}] {step_ref[0]}... ({elapsed}초 경과)")

            timer_thread = threading.Thread(
                target=_timer_tick,
                args=(timer_ph, sheet_start, current_step, done_event),
                daemon=True,
            )
            add_script_run_ctx(timer_thread)
            timer_thread.start()

            convert_status_map[sheet] = "변환 중"
            st.session_state["convert_status"] = convert_status_map
            render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)
            try:
                with st.spinner(f"[{sheet}] 변환 및 분석 중..."):
                    conversion = convert_excel_to_json_by_sheets(
                        input_path=excel_upload_path,
                        sheet_names=[sheet],
                        output_dir=OUTPUT_EXCEL_JSON_DIR,
                    )[0]
                    script_json_path = Path(conversion["output_path"])
                    script_json = json.loads(script_json_path.read_text(encoding="utf-8"))
                    convert_status_map[sheet] = "변환 완료"
                    analyze_status_map[sheet] = "분석 중"
                    current_step[0] = "분석 중"
                    st.session_state["convert_status"] = convert_status_map
                    st.session_state["analyze_status"] = analyze_status_map
                    render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

                    answer_text = call_claude_compare(
                        script_json=script_json,
                        manual_pdf_bytes=manual_pdf_bytes,
                        model=model,
                        api_key=api_key,
                        system_prompt=system_prompt,
                    )
                    result_json = parse_json_from_text(answer_text)
                    match_rate = calc_match_rate(result_json)

                output_name = f"{system_prompt_version}_{safe_name(sheet)}_{ts}.json"
                output_path = OUTPUT_AGENT_DIR / output_name
                output_path.write_text(
                    json.dumps(result_json, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                analysis_results.append(
                    {
                        "sheet": sheet,
                        "match_rate": match_rate,
                        "result_json": result_json,
                        "output_path": str(output_path),
                    }
                )
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed_total = int(time.time() - sheet_start)
                analyze_status_map[sheet] = f"분석 완료 ({elapsed_total}초)"
                timer_ph.success(f"✅ [{sheet}] 완료! (총 {elapsed_total}초 소요)")

            except urllib.error.HTTPError as e:
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed = int(time.time() - sheet_start)
                body = e.read().decode("utf-8", errors="ignore")
                st.error(f"[{sheet}] Claude API HTTP 에러: {e.code}\n{body}")
                timer_ph.error(f"❌ [{sheet}] 오류 ({elapsed}초 후)")
                if convert_status_map.get(sheet) == "변환 중":
                    convert_status_map[sheet] = "변환 오류"
                else:
                    analyze_status_map[sheet] = "분석 오류"
            except urllib.error.URLError as e:
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed = int(time.time() - sheet_start)
                st.error(f"[{sheet}] 네트워크 에러: {e.reason}")
                timer_ph.error(f"❌ [{sheet}] 오류 ({elapsed}초 후)")
                if convert_status_map.get(sheet) == "변환 중":
                    convert_status_map[sheet] = "변환 오류"
                else:
                    analyze_status_map[sheet] = "분석 오류"
            except Exception as e:
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed = int(time.time() - sheet_start)
                st.error(f"[{sheet}] 처리 중 오류: {e}")
                timer_ph.error(f"❌ [{sheet}] 오류 ({elapsed}초 후)")
                if convert_status_map.get(sheet) == "변환 중":
                    convert_status_map[sheet] = "변환 오류"
                else:
                    analyze_status_map[sheet] = "분석 오류"
            finally:
                st.session_state["convert_status"] = convert_status_map
                st.session_state["analyze_status"] = analyze_status_map
                render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

        st.session_state["analysis_results"] = analysis_results
        if analysis_results:
            st.session_state["selected_sheet"] = analysis_results[0]["sheet"]

    results = st.session_state.get("analysis_results", [])
    if not results:
        return

    st.markdown("---")
    st.subheader("판매대본별 일치율")

    # 좌우 배치 카드
    per_row = 3
    for start in range(0, len(results), per_row):
        chunk = results[start:start + per_row]
        cols = st.columns(per_row)
        for idx, item in enumerate(chunk):
            with cols[idx]:
                render_rate_card(item["sheet"], item["match_rate"])
                st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
                c1, c2, c3 = st.columns([1, 1.6, 1])
                with c2:
                    if st.button("상세 보기", key=f"detail_{item['sheet']}", use_container_width=True):
                        st.session_state["selected_sheet"] = item["sheet"]

    selected_sheet = st.session_state.get("selected_sheet", results[0]["sheet"])
    selected = next((x for x in results if x["sheet"] == selected_sheet), results[0])
    result_json = selected["result_json"]

    st.markdown("---")
    st.subheader(f"상세 분석: {selected['sheet']}")
    st.caption(f"저장 위치: {selected['output_path']}")

    category = result_json.get("category", "-")
    summary_script = result_json.get("summary_script") or result_json.get("summary", "-")
    summary_manual = summary_manual_to_text(result_json.get("summary_manual"))

    left, right = st.columns([1, 2])
    with left:
        render_info_block("카테고리", category)
    with right:
        render_info_block("판매대본 요약", summary_script)
        st.markdown("")
        render_info_block("상품설명서 요약", summary_manual)

    st.markdown("")
    st.subheader("항목별 일치 비교")
    rows = build_comparison_rows(result_json)
    filter_col, btn_col = st.columns([3, 1])
    with filter_col:
        verdict_filter = st.radio(
            " ",
            options=["전체", "일치", "불일치"],
            horizontal=True,
            key="verdict_filter",
        )
    with btn_col:
        if rows:
            st.markdown("<div style='height:22px;'></div>", unsafe_allow_html=True)
            st.download_button(
                label="📥 CSV 다운로드",
                data=rows_to_csv(rows),
                file_name=f"비교결과_{safe_name(selected['sheet'])}.csv",
                mime="text/csv",
                use_container_width=True,
            )
    render_comparison_table(rows, verdict_filter)


if __name__ == "__main__":
    main()
