import csv
import html
import json
import os
import re
import sys
import threading
import time
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
from fund_core import (
    call_llm_compare,
    parse_json_from_text,
    calc_match_rate,
    build_comparison_rows,
    summary_manual_to_text,
)

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"

UPLOAD_DIR            = PROJECT_ROOT / "data" / "uploads_external"
OUTPUT_EXCEL_JSON_DIR = PROJECT_ROOT / "data" / "output_excel_json"
OUTPUT_AGENT_DIR      = PROJECT_ROOT / "data" / "output_agent"
PROMPT_DIR            = Path(__file__).resolve().parent / "prompt"


# ── 유틸 ─────────────────────────────────────────────────

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


def get_setting(name: str, default: str | None = None):
    if name in st.secrets:
        return st.secrets[name]
    return os.getenv(name, default)


def rows_to_csv(rows: list) -> bytes:
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=["항목", "판정", "판매대본", "설명서", "근거"])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


# ── UI 컴포넌트 ───────────────────────────────────────────

def to_html_text(value):
    if value is None:
        return "-"
    return html.escape(str(value)).replace("\n", "<br>")


def render_info_block(title: str, body):
    st.markdown(
        f"""
        <div style="border:1px solid rgba(128,128,128,0.2); border-radius:14px; padding:14px;
                    background:var(--secondary-background-color); height:100%;">
          <div style="font-size:14px; color:var(--text-color); opacity:0.65; margin-bottom:8px;">{to_html_text(title)}</div>
          <div style="font-size:15px; line-height:1.6; color:var(--text-color);">{to_html_text(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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


def render_comparison_table(rows, verdict_filter: str):
    if verdict_filter != "전체":
        rows = [r for r in rows if r["판정"] == verdict_filter]
    if not rows:
        st.info("조건에 맞는 항목이 없습니다.")
        return

    st.markdown(
        """
        <style>
        .cmp-table { width:100%; border-collapse:collapse; font-size:14px; color:var(--text-color); }
        .cmp-table th {
            text-align:left; background:var(--secondary-background-color);
            color:var(--text-color); padding:10px; border:1px solid rgba(128,128,128,0.25);
        }
        .cmp-table td { vertical-align:top; padding:10px; border:1px solid rgba(128,128,128,0.25); color:var(--text-color); }
        .badge-ok { color:#065f46; font-weight:700; background:#d1fae5; padding:3px 8px; border-radius:999px; }
        .badge-no { color:#991b1b; font-weight:700; background:#fee2e2; padding:3px 8px; border-radius:999px; }
        .memo { display:block; white-space:pre-wrap; line-height:1.5; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    table_html = [
        "<table class='cmp-table'>",
        "<thead><tr><th style='width:22%'>항목</th><th style='width:8%'>판정</th><th style='width:25%'>판매대본</th><th style='width:25%'>설명서</th><th>근거</th></tr></thead>",
        "<tbody>",
    ]
    for row in rows:
        verdict = row["판정"]
        badge = "<span class='badge-ok'>일치</span>" if verdict == "일치" else "<span class='badge-no'>불일치</span>"
        script_cell = html.escape(str(row.get("판매대본", "-") or "-")).replace("\n", "<br>")
        manual_cell = html.escape(str(row.get("설명서", "-") or "-")).replace("\n", "<br>")
        reason_cell = html.escape(str(row.get("근거", "-") or "-")).replace("\n", "<br>")
        table_html.append(
            "<tr>"
            f"<td>{html.escape(str(row['항목']))}</td>"
            f"<td>{badge}</td>"
            f"<td><span class='memo'>{script_cell}</span></td>"
            f"<td><span class='memo'>{manual_cell}</span></td>"
            f"<td><span class='memo'>{reason_cell}</span></td>"
            "</tr>"
        )
    table_html.append("</tbody></table>")
    st.markdown("".join(table_html), unsafe_allow_html=True)


# ── 메인 ─────────────────────────────────────────────────

def main():
    st.set_page_config(page_title="펀드판매대본 점검", layout="wide")
    load_dotenv(find_dotenv(), override=True)

    api_key            = get_setting("API_KEY")
    model              = get_setting("LLM_MODEL")
    SYSTEM_PROMPT_VERSION = get_setting("SYSTEM_PROMPT_VERSION", "system_prompt_v10")

    prompt_path = PROMPT_DIR / f"{SYSTEM_PROMPT_VERSION}.txt"
    if not prompt_path.exists():
        st.error(f"프롬프트 파일이 없습니다: {prompt_path}\nSYSTEM_PROMPT_VERSION 값을 확인해주세요.")
        st.stop()
    SYSTEM_PROMPT = prompt_path.read_text(encoding="utf-8")

    st.markdown(
        """
        <style>
        .app-header { background:var(--secondary-background-color); border:1px solid rgba(128,128,128,0.25);
                      border-radius:14px; padding:16px 18px; margin-bottom:14px; }
        .app-header .title    { font-size:30px; font-weight:800; color:var(--text-color); line-height:1.2; }
        .app-header .subtitle { font-size:16px; font-weight:600; color:var(--text-color); opacity:0.8; margin-top:6px; }
        </style>
        <div class="app-header">
          <div class="title">📄 펀드판매대본 점검 시스템</div>
          <div class="subtitle">펀드 판매대본과 상품 설명서의 일치도를 분석합니다.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        "### 파일 업로드 (<span style='color:#ff4b4b;'>복호화</span> 파일만 가능)",
        unsafe_allow_html=True,
    )
    # st.sidebar.warning("복호화된 파일만 업로드해주세요")
    script_excel = st.sidebar.file_uploader("• 판매대본 파일 업로드 (Excel)", type=["xlsx"])
    manual_pdf   = st.sidebar.file_uploader("• 설명서 파일 업로드 (PDF)", type=["pdf"])

    selected_sheets = []
    if script_excel is not None:
        try:
            sheet_names = parse_sheet_names_from_xlsx_bytes(script_excel.getvalue())
            selected_sheets = st.sidebar.multiselect(
                "• 판매대본 시트 선택",
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
            st.error("API_KEY가 설정되지 않았습니다.")
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
        run_upload_dir = UPLOAD_DIR / ts
        run_upload_dir.mkdir(parents=True, exist_ok=True)
        excel_upload_path = run_upload_dir / safe_name(script_excel.name)
        pdf_upload_path = run_upload_dir / safe_name(manual_pdf.name)
        excel_upload_path.write_bytes(script_excel.getvalue())
        manual_pdf_bytes = manual_pdf.getvalue()
        pdf_upload_path.write_bytes(manual_pdf_bytes)

        analysis_results = []
        convert_status_map = {s: "대기" for s in selected_sheets}
        analyze_status_map = {s: "대기" for s in selected_sheets}
        st.session_state["convert_status"] = convert_status_map
        st.session_state["analyze_status"] = analyze_status_map
        render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

        for sheet in selected_sheets:
            sheet_start  = time.time()
            timer_ph     = st.empty()
            current_step = ["변환 중"]
            done_event   = threading.Event()

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
                conversion = convert_excel_to_json_by_sheets(
                    input_path=excel_upload_path,
                    sheet_names=[sheet],
                    output_dir=OUTPUT_EXCEL_JSON_DIR,
                )[0]
                script_json = json.loads(Path(conversion["output_path"]).read_text(encoding="utf-8"))

                convert_status_map[sheet] = "변환 완료"
                analyze_status_map[sheet] = "분석 중"
                current_step[0] = "분석 중"
                st.session_state["convert_status"] = convert_status_map
                st.session_state["analyze_status"] = analyze_status_map
                render_status_panel(status_placeholder, selected_sheets, convert_status_map, analyze_status_map)

                answer_text = call_llm_compare(
                    script_json=script_json,
                    manual_pdf_bytes=manual_pdf_bytes,
                    model=model,
                    api_key=api_key,
                    system_prompt=SYSTEM_PROMPT,
                )
                result_json = parse_json_from_text(answer_text)
                match_rate  = calc_match_rate(result_json)

                output_path = OUTPUT_AGENT_DIR / f"web_{ts}_{safe_name(sheet)}_{SYSTEM_PROMPT_VERSION}.json"
                output_path.write_text(json.dumps(result_json, ensure_ascii=False, indent=2), encoding="utf-8")

                analysis_results.append({
                    "sheet": sheet,
                    "script_json": script_json,
                    "match_rate": match_rate,
                    "result_json": result_json,
                    "output_path": str(output_path),
                })
                done_event.set()
                timer_thread.join(timeout=2)
                elapsed_total = int(time.time() - sheet_start)
                analyze_status_map[sheet] = f"분석 완료 ({elapsed_total}초)"
                timer_ph.success(f"✅ [{sheet}] 완료! (총 {elapsed_total}초 소요)")

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

    # ── 결과 표시 ─────────────────────────────────────────
    results = st.session_state.get("analysis_results", [])
    if not results:
        return

    st.subheader("대본별 일치도 분석 결과")
    if "selected_sheet" not in st.session_state:
        st.session_state["selected_sheet"] = results[0]["sheet"]

    per_row = 4
    for start in range(0, len(results), per_row):
        chunk = results[start:start + per_row]
        cols = st.columns(per_row)
        for idx, item in enumerate(chunk):
            with cols[idx]:
                is_selected  = st.session_state["selected_sheet"] == item["sheet"]
                border_color = "#2563eb" if is_selected else "rgba(128,128,128,0.2)"
                bg_color     = "rgba(37,99,235,0.08)" if is_selected else "var(--secondary-background-color)"
                st.markdown(
                    f"""
                    <div style="border:2px solid {border_color}; border-radius:12px; padding:10px 14px;
                                background:{bg_color}; margin-bottom:4px;">
                      <div style="font-size:14px; font-weight:700; color:var(--text-color);
                                  white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">
                        {html.escape(item['sheet'])}
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button("선택", key=f"sel_{item['sheet']}", use_container_width=True):
                    st.session_state["selected_sheet"] = item["sheet"]
                    st.rerun()

    selected_sheet = st.session_state.get("selected_sheet", results[0]["sheet"])
    selected       = next((x for x in results if x["sheet"] == selected_sheet), results[0])
    result_json    = selected["result_json"]

    st.markdown("---")
    rows = build_comparison_rows(
        result_json=result_json,
        script_json=selected.get("script_json"),
        summary_manual=result_json.get("summary_manual"),
    )
    filter_col, btn_col = st.columns([3, 1])
    with filter_col:
        verdict_filter = st.radio(" ", options=["전체", "일치", "불일치"], horizontal=True, key="verdict_filter")
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

    st.markdown("---")
    st.subheader("요약 정보")
    summary_script = result_json.get("summary_script") or result_json.get("summary", "-")
    summary_manual = summary_manual_to_text(result_json.get("summary_manual"))
    left, right = st.columns(2)
    with left:
        render_info_block("판매대본 요약", summary_script)
    with right:
        render_info_block("상품설명서 요약", summary_manual)


if __name__ == "__main__":
    main()
