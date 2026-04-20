#!/usr/bin/env python3
import argparse
from datetime import datetime
import json
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path
from zipfile import ZipFile


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
TARGET_STEP_KEYWORDS = ["설명서 교부", "설명 의무"]
TARGET_STEP_KEYWORDS = ["일반 투자자정보 부적합 여부","부적합상품판매가이드라인","적합한 상품 투자권유", "설명서 교부",    
                        "설명서 및 약관 교부", "설명 의무","핵심 설명서 필수 사항 설명","핵심(요약) 설명서 필수 사항 설명",
                        "금소법상 설명서 필수 사항 설명","(핵심설명서 및) 금소법상 설명서 필수 사항 설명","2943"]

def normalize_text(value: str) -> str:
    if value is None:
        return ""
    value = value.replace("\r", "\n")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def col_to_index(col: str) -> int:
    n = 0
    for ch in col:
        if "A" <= ch <= "Z":
            n = n * 26 + (ord(ch) - ord("A") + 1)
    return n


def split_cell_ref(ref: str):
    col = "".join(ch for ch in ref if ch.isalpha()).upper()
    row = "".join(ch for ch in ref if ch.isdigit())
    return col_to_index(col), int(row)


def si_text(si_node) -> str:
    return "".join(
        t.text or "" for t in si_node.findall(f".//{{{NS_MAIN}}}t")
    )


def parse_shared_strings(zf: ZipFile):
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []

    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    return [si_text(si) for si in root.findall(f"{{{NS_MAIN}}}si")]


def parse_workbook_sheets(zf: ZipFile):
    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))

    rel_map = {}
    for rel in rel_root.findall(f"{{{NS_REL_PACKAGE}}}Relationship"):
        rel_map[rel.attrib["Id"]] = rel.attrib["Target"]

    sheets = []
    for sheet in wb_root.findall(f".//{{{NS_MAIN}}}sheet"):
        rid = sheet.attrib.get(f"{{{NS_REL_OFFICE}}}id")
        target = rel_map.get(rid, "")
        if not target:
            continue
        sheets.append(
            {
                "name": sheet.attrib.get("name", ""),
                "path": f"xl/{target}" if not target.startswith("xl/") else target,
            }
        )
    return sheets


def get_cell_text(cell_node, shared_strings):
    t = cell_node.attrib.get("t")
    v = cell_node.find(f"{{{NS_MAIN}}}v")
    is_node = cell_node.find(f"{{{NS_MAIN}}}is")

    if t == "s" and v is not None and v.text:
        idx = int(v.text)
        return shared_strings[idx] if idx < len(shared_strings) else ""
    if t == "inlineStr" and is_node is not None:
        return "".join(
            x.text or "" for x in is_node.findall(f".//{{{NS_MAIN}}}t")
        )
    if v is not None and v.text is not None:
        return v.text
    return ""


def parse_sheet_rows(zf: ZipFile, sheet_path: str, shared_strings):
    root = ET.fromstring(zf.read(sheet_path))
    rows = []
    for row_node in root.findall(f".//{{{NS_MAIN}}}sheetData/{{{NS_MAIN}}}row"):
        row_idx = int(row_node.attrib["r"])
        cells = {}
        for cell_node in row_node.findall(f"{{{NS_MAIN}}}c"):
            ref = cell_node.attrib.get("r", "")
            if not ref:
                continue
            col_idx, _ = split_cell_ref(ref)
            cells[col_idx] = normalize_text(get_cell_text(cell_node, shared_strings))
        rows.append((row_idx, cells))
    return rows


def find_header(rows):
    for row_idx, cells in rows:
        step_col = None
        example_col = None
        for col_idx, text in cells.items():
            if text == "단계":
                step_col = col_idx
            if text == "예시":
                example_col = col_idx
        if step_col and example_col and example_col > step_col:
            return row_idx, step_col, example_col
    return None


def build_stage_json(rows, header_row, step_col, example_col):
    result = OrderedDict()
    current_main_step = ""
    current_key = ""

    for row_idx, cells in rows:
        if row_idx <= header_row:
            continue

        step_main = normalize_text(cells.get(step_col, ""))
        step_sub = normalize_text(cells.get(step_col + 1, ""))
        example = normalize_text(cells.get(example_col, ""))

        if not example:
            continue

        if step_main:
            current_main_step = step_main

        key = ""
        if step_main and step_sub:
            key = f"{step_main}-{step_sub}"
        elif step_main:
            key = step_main
        elif step_sub and current_main_step:
            key = f"{current_main_step}-{step_sub}"
        elif step_sub:
            key = step_sub
        else:
            # 병합셀(단계)로 인해 단계 텍스트가 비어있는 연속행은
            # 바로 이전 단계 key에 이어 붙인다.
            key = current_key

        if not key:
            continue

        current_key = key
        # 설명서 단계에 키워드 있으면 결과에 포함, 없으면 스킵
        is_target_step = any(keyword in key for keyword in TARGET_STEP_KEYWORDS)
        if not is_target_step:
            continue

        if key in result:
            if example and example not in result[key]:
                result[key] = f"{result[key]}\n{example}"
        else:
            result[key] = example

    return result


def normalize_sheet_name(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def resolve_sheet(requested_name: str, sheets):
    for sheet in sheets:
        if sheet["name"] == requested_name:
            return sheet

    req_norm = normalize_sheet_name(requested_name)

    for sheet in sheets:
        if normalize_sheet_name(sheet["name"]) == req_norm:
            return sheet

    candidates = []
    for sheet in sheets:
        sheet_norm = normalize_sheet_name(sheet["name"])
        if req_norm in sheet_norm or sheet_norm in req_norm:
            candidates.append(sheet)

    if len(candidates) == 1:
        return candidates[0]

    available = ", ".join(sheet["name"] for sheet in sheets)
    raise ValueError(
        f"시트를 찾지 못했습니다: '{requested_name}'. 사용 가능 시트: {available}"
    )


def safe_filename_part(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]', "_", text)
    text = text.strip()
    return text or "sheet"


def resolve_input_path(input_excel: str) -> Path:
    # 1) 사용자가 준 경로 그대로 먼저 확인
    raw = Path(input_excel).expanduser()
    if raw.exists():
        return raw

    # 2) 파일명만 입력한 경우, 프로젝트의 data 폴더에서 자동 탐색
    project_root = Path(__file__).resolve().parent.parent
    data_root = project_root / "data"

    if not data_root.exists():
        raise FileNotFoundError(f"data 폴더를 찾지 못했습니다: {data_root}")

    target_name = raw.name
    names_to_try = [target_name]
    if Path(target_name).suffix == "":
        names_to_try.append(f"{target_name}.xlsx")

    matches = []
    for name in names_to_try:
        matches.extend(data_root.rglob(name))

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        paths = ", ".join(str(p) for p in matches)
        raise FileNotFoundError(
            f"동일 파일명이 여러 개입니다. 경로를 포함해서 입력해주세요: {paths}"
        )

    raise FileNotFoundError(
        f"입력 파일을 찾지 못했습니다: {input_excel} (data 폴더에서도 미발견)"
    )


def convert_excel_to_json_by_sheets(input_path: Path, sheet_names, output_dir: Path):
    results = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    with ZipFile(input_path) as zf:
        shared_strings = parse_shared_strings(zf)
        sheets = parse_workbook_sheets(zf)

        for requested_sheet_name in sheet_names:
            sheet = resolve_sheet(requested_sheet_name, sheets)
            rows = parse_sheet_rows(zf, sheet["path"], shared_strings)
            header = find_header(rows)
            if not header:
                raise ValueError(
                    f"'{sheet['name']}' 시트에서 '단계'/'예시' 컬럼을 찾지 못했습니다."
                )

            header_row, step_col, example_col = header
            payload = build_stage_json(rows, header_row, step_col, example_col)

            output_name = (
                f"{timestamp}_{input_path.stem}_{safe_filename_part(requested_sheet_name)}.json"
            )
            output_path = output_dir / output_name
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            results.append(
                {
                    "requested_sheet": requested_sheet_name,
                    "matched_sheet": sheet["name"],
                    "output_path": output_path,
                    "count": len(payload),
                }
            )

    return results


def main():
    parser = argparse.ArgumentParser(description="판매대본 Excel(.xlsx) -> JSON 변환")
    parser.add_argument(
        "input_excel",
        nargs="?",
        default="~$사모_판매대본_라이프META일반사모투자신탁 제2호.xlsx",
        help="입력 xlsx 파일 경로",
    )
    parser.add_argument(
        "--sheets",
        nargs="+",
        default=["사모펀드(내점)", "사모펀드(방문)"],
        help="변환할 시트명 리스트",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data/output_excel_json"),
        help="출력 폴더 경로",
    )
    args = parser.parse_args()

    input_path = resolve_input_path(args.input_excel)
    output_dir = Path(args.output_dir)

    outputs = convert_excel_to_json_by_sheets(input_path, args.sheets, output_dir)
    for item in outputs:
        print(f"완료: {item['output_path']}")
        print(
            f"시트(요청/매칭): {item['requested_sheet']} / {item['matched_sheet']}"
        )
        print(f"변환 항목 수: {item['count']}")


if __name__ == "__main__":
    main()
