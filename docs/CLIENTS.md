# 고객용 연결 가이드

FindTheGap MCP에 AI 클라이언트(Claude Code, Claude Desktop, OpenAI API 등)를 연결해 **자기 회사 데이터**를 조회·분석·시각화하고 보고서를 만드는 방법입니다.

> 이 문서의 도구 호출 예시(`"step"`이 있는 JSON 블록)는 `mcp-gateway/tests/test_client_guide.py`가 문서에서 그대로 꺼내 실제 스택에 실행합니다. 예시가 API와 어긋나면 테스트가 실패합니다.

## 1. 준비물
- **API 키** `mcpk_…` — 회사 관리자가 콘솔에서 발급합니다. 발급 화면에서 **한 번만** 보이니 안전한 곳에 보관하세요.
- 키는 **사용자 권한을 대리**합니다. 쓸 수 있는 서버·도구는 관리자가 부여한 "서버 접근"으로 정해지고, 키는 그 범위를 좁힐 수만 있습니다.
- 키를 채팅창·코드·저장소에 붙여 넣지 마세요. 유출되면 관리자에게 즉시 정지·회전을 요청하세요.

## 2. 엔드포인트
| 방식 | URL | 언제 |
|---|---|---|
| **게이트웨이 (권장)** | `https://<게이트웨이 호스트>/mcp` (개발: `http://localhost:19000/mcp`) | 키로 쓸 수 있는 모든 서버의 도구가 한 곳에 `<서버별칭>__<도구>`로 보입니다 |
| 서버 직접 | `https://<게이트웨이 호스트>/mcp-<postgres|clickhouse|mongo|hacker>?server=<server_id>` (개발: `http://localhost:19000/mcp-postgres?server=…`) | 특정 서버만 쓸 때. 키 범위가 서버 1개면 `?server=` 생략 가능 |

인증 헤더: `Authorization: Bearer mcpk_…`

## 3. 클라이언트 설정
**Claude Code**
```bash
claude mcp add --transport http ftg https://mcp.example.com/mcp --header "Authorization: Bearer $FTG_MCP_KEY"
```

**Claude Desktop** (`claude_desktop_config.json`, 헤더 인증은 `mcp-remote` 브리지)
레포가 있다면 `scripts/claude-desktop-register.sh https://mcp.example.com/mcp`로 등록할 수 있습니다(키는 프롬프트로 입력, 연결 확인 후 설정 파일에 추가). 수동 등록 시:
```jsonc
{
  "mcpServers": {
    "ftg": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://mcp.example.com/mcp", "--header", "Authorization:Bearer ${FTG_MCP_KEY}"],
      "env": { "FTG_MCP_KEY": "<발급받은 키>" }
    }
  }
}
```

**OpenAI Responses API**
```python
client.responses.create(
    model="<model>",
    tools=[{"type": "mcp", "server_label": "ftg", "server_url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": f"Bearer {FTG_MCP_KEY}"}, "require_approval": "never"}],
    input="이번 달 제보 현황을 요약하고 차트와 보고서를 만들어줘",
)
```

**ChatGPT / Claude.ai 커스텀 커넥터** (OAuth)
1. 커넥터 추가 화면에 URL `https://<게이트웨이 호스트>/mcp`를 입력합니다(인증 방식: OAuth).
2. 브라우저에 FindTheGap 연결 화면이 뜨면 **연결하려는 서비스와 돌아갈 주소**를 확인하고, 발급받은 API 키를 입력합니다.
3. 이후 호출은 그 키의 권한으로만 동작합니다. 키가 정지·회전되면 연결도 즉시 끊기며, 다시 연결하려면 새 키로 동의합니다.
> 동의 화면의 "돌아갈 주소"가 사용 중인 서비스(예: chatgpt.com, claude.ai)가 아니면 키를 입력하지 마세요.

**회사 계정(SSO)으로 연결** — 회사 관리자가 콘솔에서 SSO(Google Workspace 등 OIDC)를 등록했다면, 같은 동의 화면의 "회사 계정으로 로그인"에 회사 이메일을 넣고 회사 로그인을 마치면 됩니다. API 키를 받을 필요가 없습니다.
로그인하면 내 이름으로 `sso:<클라이언트>` 이름의 연결용 키가 자동 발급됩니다(30일 만료, 콘솔 키 목록에 보이며 관리자가 정지할 수 있음). 권한은 관리자가 내게 부여한 서버 접근 그대로이고, 접근이 없으면 도구가 보이지 않습니다.
운영자가 회사의 Google Workspace 를 연결해 두었다면 회사 이메일만 넣으면 Google 로그인으로 이어집니다(직원 쪽 별도 설정 불필요).

## 4. 사용 흐름 (예시)
AI에게 자연어로 요청하면 아래 도구들을 알아서 호출합니다. 직접 호출할 때의 인자 형태는 다음과 같습니다. `{pg}`는 서버 별칭(예: `sales-pg-1a2b`), `{artifact:…}`는 앞 단계에서 저장된 산출물 id입니다.

**① 테이블 확인**
```json
{"step": "tables", "tool": "{pg}__list_tables", "arguments": {}}
```

**② 조회** — 읽기 전용, 자기 회사 범위로 자동 제한. 이메일·전화번호 등은 마스킹되어 나옵니다.
```json
{"step": "rows", "tool": "{pg}__run_select", "arguments": {"sql": "select name, email from customers order by id"}}
```

**③ 계산** — 숫자는 AI가 직접 계산하지 않고 `compute`(Rust 엔진)가 계산합니다. `save: true`면 결과를 산출물로 저장해 차트·보고서에 그대로 씁니다(export 권한 필요).
```json
{"step": "stats", "tool": "{pg}__compute", "arguments": {"source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"}, "save": true, "title": "고객 통계"}}
```

**④ 차트** — PNG 이미지로 돌아옵니다. 저장하면 보고서에 넣을 수 있습니다.
```json
{"step": "chart", "tool": "{pg}__chart", "arguments": {"spec": {"type": "bar", "x": {"field": "name"}, "y": {"field": "id"}, "title": "고객별 ID"}, "source": {"sql": "select name, id from customers order by id"}, "save": true}}
```

**⑤ 보고서** — Word(`docx`) / PowerPoint(`pptx`) / PDF(`pdf`). 표·KPI·차트는 **저장된 산출물만** 참조합니다(AI가 숫자를 새로 쓰지 않음). 부록에 데이터 출처가 자동으로 붙습니다.
```json
{"step": "report", "tool": "report", "arguments": {"format": "docx", "outline": {"title": "9월 진행 보고서", "sections": [{"heading": "요약", "blocks": [{"type": "text", "text": "- 고객 현황\n- 추이"}, {"type": "kpi", "artifact_id": "{artifact:stats}", "fields": ["count"], "labels": {"count": "고객 수"}}, {"type": "chart", "artifact_id": "{artifact:chart}"}]}]}}}
```
작은 파일은 응답에 base64로, 큰 파일은 15분짜리 다운로드 링크로 옵니다.

**⑥ 모니터링** — 조건을 저장해 두면 서버가 주기적으로 다시 계산합니다. 결과는 `monitor_status`로 확인합니다(알림 채널은 준비 중).
```json
{"step": "monitor", "tool": "{pg}__monitor_create", "arguments": {"name": "고객 수 확인", "source": {"sql": "select id from customers"}, "op": {"op": "describe", "column": "id"}, "condition": {"path": "count", "op": "gt", "threshold": 100}, "interval_s": 300}}
```

## 5. 권한과 보안
- **자기 회사 데이터만** 보입니다. 다른 회사 스키마·DB를 SQL에 적어도 거부됩니다.
- 쓰기 도구(`run_insert` 등)와 파일 반출(`export`, `report`, `save`)은 관리자가 따로 허용해야 합니다.
- 저장된 산출물은 **본인만** 보고, 7일 뒤 자동 삭제됩니다. 원천 서버 접근이 정지되면 그 산출물도 보이지 않습니다.
- 모니터는 만든 키의 권한으로만 돕니다. 키·사용자·서버 접근이 정지되면 실행되지 않고 이유가 기록됩니다.
- 모든 호출은 회사별 접근 로그에 남습니다. 키당 분당 호출 한도가 있으며, 넘으면 잠시 후 다시 시도하라는 오류가 옵니다.
- 데이터 속 문자열(예: 게시글 본문)에 "이전 지시를 무시하라" 같은 문구가 있어도 **데이터일 뿐**입니다. 보고서·차트는 그런 문자열을 실행하지 않고 글자 그대로 표시합니다.
