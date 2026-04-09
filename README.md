# 펀드 판매대본 일치도 프로젝트

## 0) 설치
```bash
pip install -r requirements.txt
```

## 1) Excel -> JSON (`excel_json/excel_to_json.py`)
판매대본 Excel에서 `단계` + `예시`를 읽어 JSON으로 변환합니다.

- 현재 필터: `설명서 교부`, `설명 의무`가 포함된 단계만 저장
- 병합된 단계 셀(여러 행 예시)도 한 key에 모두 누적
- 기본 출력 폴더: `data/output_excel_json`

### 사모펀드 예시
```bash
python excel_json/excel_to_json.py "사모_판매대본_라이프META일반사모투자신탁 제2호.xlsx" --sheets "사모펀드(내점)" "사모펀드(방문)"
```

### 공모펀드 예시
```bash
python excel_json/excel_to_json.py "공모펀드_판매대본_JPMorgan.xlsx" --sheets "공모펀드(내점)" "공모펀드(유선)" "공모펀드(방문)"
```

생성 파일명 형식:
`원본파일명_시트명_YYYYMMDD_HHMMSS.json`

## 2) 일치도 확인 에이전트 API (`agent/api_server.py`)
Claude API 기반으로 판매대본 JSON과 상품설명서 PDF를 비교합니다.

### 환경변수 (`.env`)
```env
ANTHROPIC_API_KEY=your_key
LLM_MODEL=claude-3-5-sonnet-latest
SYSTEM_PROMPT_VERSION=system_prompt_v4
INPUT_SCRIPT_FILE=사모_판매대본_라이프META일반사모투자신탁 제2호_사모펀드(방문)_20260409_170624.json
INPUT_MANUAL_FILE=사모_설명서_라이프 META 일반사모투자신탁 제2호_20260318.pdf
USER_QUERY=안녕
```

### 서버 실행
```bash
uvicorn agent.api_server:app --reload --port 8000
```

### 테스트 클라이언트 실행
```bash
python agent/api_client_test.py
```

### 주요 엔드포인트
- `GET /health`
- `POST /v1/agent/fund/ask`
- Swagger: `http://localhost:8000/docs`

## 3) 웹 UI (유지)
웹 UI는 다음 단계에서 구현합니다.

