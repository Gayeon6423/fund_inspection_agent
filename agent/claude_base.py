import json
import os
import sys
import urllib.error
import urllib.request
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

# 1) 내 API 키를 환경변수에서 읽어온다.
api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    print("에러: ANTHROPIC_API_KEY 환경변수를 먼저 설정해주세요.")
    sys.exit(1)

# 2) Claude에 보낼 내용을 만든다.
model = os.getenv("LLM_MODEL")

user_input = "안녕"
system_prompt = "당신은 친절한 한국어 AI 어시스턴트입니다. 항상 간결하고 명확하게 답변해주세요."
payload = {
    "model": model,
    "max_tokens": 200,
    "system": system_prompt,
    "messages": [
        {"role": "user", "content": user_input},
    ],
}

# 3) Claude API로 요청을 보낸다.
request = urllib.request.Request(
    url="https://api.anthropic.com/v1/messages",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    },
    method="POST",
)

# 4) Claude 답변을 받아서 화면에 출력한다.
with urllib.request.urlopen(request) as response:
    result = json.loads(response.read().decode("utf-8"))
print("Claude 응답:")
print(result["content"][0]["text"])