# FTG MCP-Cluster
FTG MCP-Cluster는 회사 리소스를 내부·외부 고객이 AI 클라이언트(ChatGPT / OpenAI API, Claude Desktop, Claude Code 등)로 접근하게 하는 멀티테넌트 MCP 서버 클러스터로 다음과 같은 주요 기능을 가져야 한다.
- 회사 → 사용자 → 서버 권한 계층, 테넌트 바인딩, API 키 관리 콘솔
- 테넌트 격리된 데이터 조회 (postgres / clickhouse / mongo / hacker)
- 데이터 정제(`transform`), Rust 수치 분석(`compute`), 내보내기(`export`)
- S3 산출물 저장소, 시각화(`chart`), 여러 서버를 묶는 보고서 PDF·DOCX·PPTX(`report`), 모니터링(MCP 조회 전용) — 🔜
- 단일 게이트웨이 + OAuth 2.1로 모든 MCP 클라이언트 지원
- 콘솔 대시보드(통계 타일·서버 헬스·24h 메트릭·이벤트 피드·산출물 목록): 읽기 전용, 권한 경로와 무관. 헬스 체크 대상은 env `MCP_INTERNAL_*` 만(서버 레코드 endpoint 는 fetch 금지)
- 커넥터 SSO(회사별 OIDC, Google Workspace 등): 인증만 외부 IdP, 인가는 ServerAccess 그대로. 로그인은 사용자 명의의 만료 세션 키로 표현. **콘솔에는 SSO 로그인이 없다** — 운영자(CTO·개발자)는 이메일·비밀번호. Workspace 디렉터리는 운영자가 등록한 서비스 계정(도메인 전체 위임)으로 동기화

목표·설계·결정 사항·로드맵: [docs/PROJECT.md](docs/PROJECT.md) / 설계 상세: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) / 실행법: [README.md](README.md)


## 0. 작업 지침

> 트레이드오프: 이 지침은 **속도보다 신중함**을 우선한다. 이 시스템은 외부 고객의 민감 데이터를 다룬다. **정합성과 테넌트 격리가 최우선**이다.

### 0.1 코딩 전에 생각하라
- **가정을 명시적으로 진술하라.** 불확실하면 묻는다.
- **해석이 여럿이라면 모두 제시하라.** 조용히 하나를 고르지 마라.
- **더 단순한 방법이 있다면 말하라.** 근거가 있을 땐 밀어붙여라.
- **불분명한 것이 있으면 멈춰라.** 무엇이 헷갈리는지 이름을 붙이고 물어라.
- **여러 서비스에 걸친 변경은 계획 → 승인 → 실행.** 승인 전에는 계획만 제시한다.

### 0.2 단순함이 우선
- 요청된 것 외 기능 추가 금지. 추측성 추상화 금지.
- 일어날 수 없는 시나리오 에러 처리 금지. 단, **권한·격리 경로의 실패는 항상 거부(fail-closed)**로 처리한다.
- *"시니어 엔지니어가 이걸 보고 과설계라고 할까?"* — 그렇다면 단순화하라.

### 0.3 외과적 변경
- 인접 코드/포맷을 "개선"하지 마라. 망가지지 않은 것을 리팩터링하지 마라.
- 죽은 코드를 발견하면 — **언급은 하되 삭제는 하지 마라**.
- **변경된 모든 줄이 사용자 요청까지 직선으로 이어져야 한다.**

### 0.4 목표 주도 실행
다단계 작업은 짧은 계획 먼저:
```
1. [단계] → 검증: [확인]
2. [단계] → 검증: [확인]
```
검증은 `make test` / `make e2e` / `make e2e-ui` / `make rust-parity` 중 해당하는 것을 실제로 돌린 결과로 말한다.

### 0.5 온톨로지 기록
- 개발 작업을 완료하면, 구현한 내용(개념·결정·컴포넌트·API·보안 발견 등)을
- Obsidian 볼트에 온톨로지 노트로 기록한다. 스키마와 절차는 `ontology-note` 스킬을 따른다.


## 1. 프로젝트 구조
```text
┌──────────────────────────────────────────────────────────────────────┐
│  MCP Clients: ChatGPT · OpenAI API · Claude Desktop · Claude Code …  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ Streamable HTTP + Bearer mcpk_… / OAuth 2.1
                                ▼
                    ┌───────────────────────┐
                    │  gateway (nginx) :19000 │  유일한 공개 포트: / 콘솔 · /api/v1 · /mcp · /mcp-<type>
                    └───────────┬───────────┘
                    ┌───────────┴───────────┐
                    │  mcp-gateway :9000     │  단일 MCP 엔드포인트 / OAuth (/mcp)
                    └───────────┬───────────┘
        ┌───────────────┬───────┴───────┬────────────────┐
        ▼               ▼               ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌─────────────┐
│ mcp-postgres │ │mcp-clickhouse│ │  mcp-mongo  │ │ mcp-hacker  │   FastMCP
│ /mcp-postgres│ │/mcp-clickhouse│ │  /mcp-mongo │ │ /mcp-hacker │
└──────┬───────┘ └──────┬───────┘ └──────┬──────┘ └──────┬──────┘
       └────────────────┴───────┬────────┴───────────────┘
                                │ mcpcluster_common: TenantAuthMiddleware · guards ·
                                │ transform · compute · export · chart🔜 · report🔜 · monitor🔜
                                │ mcpcluster_core (Rust/PyO3): PII · hash · compute kernels
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ Redis            │  │ 고객 datasource   │  │ S3 객체저장소     │
│ db1 authz 투영    │  │ 테넌트 고정 커넥션  │  │ export·artifact· │
│                  │  │                  │  │ report           │
│ db2 캐시·rate    │  └──────────────────┘  │ presigned URL    │
│ db3 접근로그 stream│                        └──────────────────┘
└────────▲─────────┘
         │ outbox → projection (version guard) / reconciler
┌────────┴─────────┐      ┌──────────────────┐      ┌──────────────────┐
│ backend FastAPI  │◀────▶│ DynamoDB         │      │ frontend React   │
│  /api/v1 (:8080) │      │ 단일테이블 + logs  │      │ 관리 콘솔 / (:5173)│
└────────▲─────────┘      └──────────────────┘      └────────┬─────────┘
         └───────────────────────────────────────────────────┘
```

| 분류 | 사용할 도구 | 고려사항 |
|------|------------|---------|
| UI | React + TS (Vite), React Query, Zustand, shadcn | 대량 데이터 목록 — 검색·필터·커서 페이지네이션. 디자인: 무채색 + 강조 1색(블루), 빨강은 위험·오류 전용. 토큰은 `frontend/src/index.css` `@theme`, 화면 코드에 hue 클래스 직접 사용 금지(docs/PROJECT.md 결정 14) |
| API | FastAPI (async) | DynamoDB 단일 진실 원천, 쓰기는 `TransactWriteItems` + `ConditionCheck` |
| Database | DynamoDB (`~/dev/aws-local` 공유) | 단일 테이블, 낙관적 잠금(`version`), `UNIQ#` 마커 |
| Cache | Redis 7 | db1 authz 투영 / db2 핫캐시·rate limit / db3 접근 로그 stream |
| MCP | FastMCP (Streamable HTTP, stateless) | 서버는 datasource 종류별 범용, 고객 차이는 바인딩으로 |
| Dataframe | polars, duckdb | 정제는 polars, 큰 집계는 duckdb |
| CPU 연산 | Rust (PyO3 + rayon, maturin) | 수치 연산은 Rust 커널, Python 미러 + parity 테스트 |
| Storage | S3 API (dev: `~/dev/aws-local` MinIO, 운영: AWS S3 / NCP Object Storage) | 서버 간 결과는 `artifact_id`로 S3 경유, 반출은 presigned URL |
| 시각화 🔜 | Vega-Lite + `vl-convert-python` (Rust 휠) | 브라우저 없이 PNG/SVG, 외부 URL 로딩 금지 |
| 보고서 🔜 | PDF `typst` 휠 / DOCX `python-docx` / PPTX `python-pptx` | 가벼운 pip 휠만. 헤드리스 브라우저·LibreOffice 금지 |
| 알림 🔜 | `Notifier` 포트 (현재 채널 없음) | Slack·메일은 구현 추가만으로 확장 |


## 2. 반드시 지킬 불변식
위반 시 타 고객 데이터 유출로 이어진다. 변경이 필요하면 먼저 사용자와 합의한다.

1. **권한 계층은 회사 → 사용자 → 서버** — 권한·허용 도구는 사용자의 서버 접근(`ServerAccess`)에 있고, API 키는 사용자를 대리하며 scope로 서버를 **줄일 수만** 있다. 대상 서버는 MCP URL `?server=<server_id>` → 프로세스 핀 → 키 scope가 1개일 때만 추론. **모호하면 거부**한다 (`mcpcluster_common/authz.py`).
2. **deny-by-default** — Redis에 키/회사/유저/서버 접근/서버/바인딩 중 하나라도 없거나 `active`가 아니면 거부. 캐시 미스를 허용으로 처리하지 않는다.
3. **발급·재활성은 한 트랜잭션** — 상위 엔티티 `active`를 `ConditionCheck`로 같은 `TransactWriteItems`에서 검증. 서버 접근은 회사·유저·서버·바인딩(`services/access.py`), 키는 회사·유저 + scope의 각 서버 접근(`services/apikeys.py`).
4. **allowed_tools ⊆ permissions가 덮는 도구** — 신규 도구는 `backend/app/domain/tool_catalog.py`에 권한과 함께 등록하고, 각 서버 테스트의 카탈로그 동일성 검사를 통과시킨다.
5. **datasource 접근은 가드를 통해서만** — 신규 도구도 서버의 `fetch_source`(파서 기반 가드 + read-only 트랜잭션)를 거친다. 문자열 패턴 매칭으로 가드하지 않는다.
6. **데이터가 나가면 `export` 권한** — export, chart 산출물 저장, report 모두 `export`. 새 반출 권한을 만들지 않는다. `read`만으로 파일을 가져갈 수 없다.
7. **S3 산출물은 소유자 검증 후 읽기** — 객체 메타의 company_id·user_id = 호출자, sha256 일치. prefix만 믿지 않고, 타인 artifact는 존재 여부도 드러내지 않는다. 서버끼리 직접 호출하지 않는다. 파생 산출물(차트 등)은 **모든 원천 서버**(`sources`)를 물려받고, 읽을 때 원천 전부를 재인가한다 — 보기는 `read`, 파일·새 산출물로 복사(report, save)는 원천 전부에 `export`.
8. **PII 마스킹은 결과·산출물 생성 전에** — 행은 `redact_rows()`, compute 결과(그룹 키·top-k 값 등)는 `redact_value()`. 입력 프레임이 아니라 결과를 마스킹한다(집계 정확도 유지).
9. **수치는 Rust 도구가 계산** — 모델이 숫자를 계산하거나, 보고서에 모델이 쓴 숫자를 그대로 넣게 하지 않는다. 신규 커널은 Rust + Python 미러 + parity 테스트.
10. **샘플 데이터에 맞춘 코드 금지** — `infra/` 픽스처는 범용성 증명용. 임의 식별자·타입에서 동작해야 하며 `tests/test_generic.py`로 확인.
11. **모니터링은 MCP 조회 전용** — 실행마다 소유 사용자 authz 재확인. 알림은 `Notifier` 포트 뒤로만 붙이고 모니터·스케줄러에 채널별 코드를 넣지 않는다.
12. **시크릿은 더미값만** — 원시 API 키는 1회 노출, 저장은 SHA-256. DSN은 Fernet 암호화.


## 3. 개발 환경 규칙
- 모든 개발·테스트는 docker-compose 기준. 먼저 `(cd ~/dev/aws-local && docker compose up -d)`.
- 네트워크에 열리는 포트는 nginx `gateway` 19000 하나다. 그 외 호스트 포트는 전부 127.0.0.1 전용: redis 6379(호스트측 테스트), 샘플 datasource 확인용 pg-sample 55432 · clickhouse 58123/59000 · mongo 57017. 다른 프로젝트와의 충돌 때문에 고른 값이니 **기본값으로 되돌리지 말고, 0.0.0.0 으로 열지 마라.** 나머지 컨테이너는 서비스 이름으로만 통신하고 `deploy/docker/gateway.nginx.conf.template`이 경로(`/`, `/api/v1`, `/mcp`, `/mcp-<type>`)로 라우팅한다. 새 서버를 붙이면 nginx `location`과 compose의 `MCP_UPSTREAM_*`도 추가한다.
- 도커 파일은 전부 `deploy/docker/`(compose·`Dockerfile.<서비스>`·nginx 템플릿, 빌드 컨텍스트는 레포 루트). `docker compose`를 직접 치지 말고 `make up/down/ps/logs`를 쓴다. 운영 계약은 `docs/DEPLOY.md`.
- 컨테이너 env는 `env/<서비스>.env`를 **추적**한다(시크릿 없는 로컬 값, 서비스명 기준, `[ECS 에서도 주입]`/`[로컬 전용]` 구획, 코드 기본값과 같은 값은 적지 않음). 실 시크릿은 `env/secrets/<서비스>.env`(gitignore, 키 목록은 추적되는 `.example`)에만 두고 compose가 `required: false`로 뒤에 겹친다. 진짜 비밀번호·키를 `env/*.env`에 넣지 마라. 주석은 반드시 별도 줄에. docker compose는 인라인 `# 주석`을 값에 포함시킨다.
- Python은 `uv`, 린트는 `ruff` (`make lint`). Rust 휠은 Dockerfile의 maturin 스테이지에서 빌드 (호스트 Rust 불필요).
- 클라이언트(ChatGPT·Claude·OpenAI) MCP 사양은 자주 바뀐다. 호환 작업 전 공식 문서로 최신 사양을 확인한다.


## 4. Agent
작업내용이 Agent Team이나 Sub-Agent을 활용해야하는지 먼저 판단해라. 가능하면 복수의 에이전트가 함께 작업 내용을 공유하며 일하게끔 해라.
에이전트 이름(`subagent_type` 에 그대로 사용, 정의는 `.claude/agents/`):
- `ux-planner`: 화면 설계, 사용자 편리성 여부 판단 전문가
- `product-owner`: 제품 기획. 필요한 기능 파악, 계획 수립 전문가
- `frontend-developer`: web 화면 개발 전문가
- `backend-developer`: api 개발, api 수정, api 내용 파악 및 공유 전문가 (backend + MCP 서버)
- `infra-engineer`: docker, ecs 통한 서비스 환경 구성 및 관리 전문가
- `qa-engineer`: 서비스 qa 진단 전문가 — 격리·권한·정지 전파 e2e 우선
- `ai-engineer`: MCP 도구 설계(설명·스키마), 클라이언트 호환성, LLM 유도 전문가
- `dba`: 서비스에 필요한 데이터 아키텍쳐 설계 전문가 (DynamoDB 키 설계, Redis 투영, S3 키 구조)

### 4.1 Agent Team vs Sub-Agent — 무엇을 띄울지 먼저 판단
"에이전트 팀으로" 라는 말은 **자동 트리거가 아니다.** 토폴로지는 어떤 도구를 호출하느냐로만 정해진다.
tmux·터미널 환경은 이 선택과 무관하다.

| 상황 | 메커니즘 | 특징 |
|------|---------|------|
| **독립 병렬 작업** (서로 대화 불필요) | `Agent()` 여러 번 (subagent fan-out) | 일회성·격리·결과 텍스트만 반환 |
| **협업·토론·합의 필요** (서로 메시지 주고받음) | `TeamCreate` → `Agent(name, team_name, run_in_background:true)` → `SendMessage` | 지속·주소지정·팀 컨텍스트 공유 |

판단 기준: **팀원끼리 서로의 중간 결과를 보고 반응해야 하면 Team, 각자 받은 일만 끝내면 되면 Sub-Agent.**

### 4.2 Agent Team 표준 실행 프로토콜 (협업이 필요할 때 반드시 이 순서로 도구 호출)
1. `TeamCreate` 로 팀 컨텍스트 생성.
2. 역할별 에이전트를 `Agent(name="<역할>", team_name="<팀>", subagent_type="<해당 전문가>", run_in_background:true)` 로 띄운다.
   - 한 메시지에서 여러 `Agent` 호출을 동시에 보내 동시 기동한다.
3. 각 에이전트에게 **작업 전 계획표**를 먼저 작성하게 하고, 취합해 `/history/dev_log.md` 에 추가.
4. 에이전트 간 의존·검토·합의는 `SendMessage({to: "<역할>"})` 로 진행한다. 단순 새 `Agent()` 호출은 컨텍스트가 단절되므로 협업에는 쓰지 않는다.
5. 주고받은 메시지를 `/history/conversation_log.md` 에 **"누가 → 누구: 내용"** 형식으로 기록.
6. 완료분은 `/history/complete_log.md` 에 취합.
7. 작업 종료 시 `TeamDelete` 로 팀 정리.

> 모든 팀원은 위 0.1~0.4 작업 지침(가정 명시·단순함·외과적 변경)과 2장 불변식을 동일하게 따른다.

### 4.3 사용자가 그냥 "에이전트 팀으로 해줘" 라고만 했을 때
구현 메커니즘을 되묻지 말고 **4.2 프로토콜을 기본값으로 실행**한다(팀원끼리 협업이 명백히 불필요한 단순 병렬이면 sub-agent fan-out으로 처리하고 그 사실을 한 줄로 알린다).
