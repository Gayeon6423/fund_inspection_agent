# 펀드 판매대본 점검 시스템

판매대본 Excel과 상품설명서 PDF를 비교해 **시트별 일치율**과 **항목별 일치/불일치 근거**를 확인하는 프로젝트입니다.

## 핵심 기능
- Excel 판매대본을 JSON으로 변환 (`excel_json/excel_to_json.py`)
- Claude API 기반 비교 분석 (`agent/api_server.py`)
- Streamlit 웹 UI에서 업로드/시트선택/상태확인/결과조회 (`agent/app.py`)
- API 서버 단계별 진행 로그(어디서 멈췄는지 추적 가능)

## 폴더 구조
```text
.
├─ agent/
│  ├─ api_server.py         # FastAPI 서버 (비교 분석 API)
│  ├─ api_client_test.py    # API 테스트 스크립트
│  ├─ app.py      # Streamlit 웹 UI
│  └─ prompt/
├─ excel_json/
│  └─ excel_to_json.py      # Excel -> JSON 변환
├─ data/
│  ├─ output_excel_json/    # 변환된 판매대본 JSON
│  ├─ output_agent/         # 분석 결과 JSON
│  └─ uploads/              # 웹 UI 업로드 임시 파일
├─ requirements.txt
└─ .env
```

## 1) 설치
```bash
pip install -r requirements.txt
```

## 2) 환경변수 설정 (`.env`)
프로젝트 루트에 `.env` 파일 생성:

```env
ANTHROPIC_API_KEY=your_key
LLM_MODEL=claude-3-5-sonnet-latest
SYSTEM_PROMPT_VERSION=system_prompt_v4
INPUT_SCRIPT_FILE=사모_판매대본_라이프META일반사모투자신탁 제2호_사모펀드(방문)_20260409_170624.json
INPUT_MANUAL_FILE=사모_설명서_라이프 META 일반사모투자신탁 제2호_20260318.pdf
USER_QUERY=안녕
```

## 3) Excel -> JSON 변환
스크립트: `excel_json/excel_to_json.py`

동작:
- `단계` / `예시` 컬럼 기반 변환
- 병합 셀로 이어진 다중 행 예시 누적
- 현재 필터: `설명서 교부`, `설명 의무` 포함 단계만 저장
- 기본 출력 폴더: `data/output_excel_json`
- 출력 파일명: `원본파일명_시트명_YYYYMMDD_HHMMSS.json`

예시:
```bash
# 사모펀드
python excel_json/excel_to_json.py "사모_판매대본_라이프META일반사모투자신탁 제2호.xlsx" --sheets "사모펀드(내점)" "사모펀드(방문)"

# 공모펀드
python excel_json/excel_to_json.py "공모펀드_판매대본_JPMorgan.xlsx" --sheets "공모펀드(내점)" "공모펀드(유선)" "공모펀드(방문)"
```

## 4) API 서버 실행 (FastAPI)
```bash
uvicorn agent.api_server:app --reload --port 8000
```

주요 엔드포인트:
- `GET /health`
- `POST /v1/agent/fund/ask`
- Swagger: `http://localhost:8000/docs`

### 진행 로그 확인 (중요)
`api_server.py`는 요청마다 `request_id`를 생성해 단계별 로그를 출력합니다.

로그 단계 예:
- 요청 수신
- 입력 컨텐츠 구성 시작/완료
- 판매대본/설명서 파일 로드
- Claude API 요청 시작/완료
- 응답 JSON 파싱 시작/완료
- 결과 저장 시작/완료
- 요청 처리 완료/실패(소요시간)

즉, 중간에 멈추면 로그에서 **어느 단계에서 실패했는지** 바로 확인 가능합니다.

## 5) 웹 UI 실행 (Streamlit)
```bash
streamlit run agent/app.py
```

UI 기능:
- 사이드바에서
  - 판매대본 Excel 업로드
  - 설명서 PDF 업로드
  - 시트 선택
  - 선택 시트 수 확인
  - 변환 상태 / 분석 상태 확인
- 본문에서
  - 시트별 일치율 카드
  - 상세 분석(category, summary)
  - 항목별 일치 비교 표
  - 판정 필터(전체/일치/불일치)

주의:
- 업로드 파일은 반드시 **복호화된 파일**이어야 합니다.

## 6) API 테스트 스크립트
```bash
python agent/api_server_test.py
```

## 7) 문제 해결
- `ModuleNotFoundError: excel_json`
  - 프로젝트 루트에서 실행
  - `streamlit run agent/app.py` 형태로 실행
- Claude 호출 실패 시
  - `.env`의 `ANTHROPIC_API_KEY` 확인
  - API 서버 로그의 `request_id` 기준으로 실패 단계 확인
