# PROJECT — MCP-Cluster

> 작성 2026-09-24. 🔜 항목은 설계 확정·구현 전이다. 결정 내역은 [7장](#7-결정-사항), 남은 질문은 [8장](#8-남은-질문).

## 1. 목표
회사 리소스(데이터·운영 정보)를 내부 고객과 외부 고객이 **AI 클라이언트로 직접** 쓰게 한다.

1. **읽기·모니터링** — 자기 회사 데이터를 조회하고, 지표를 주기적으로 지켜본다.
2. **분석** — 통계·집계·이상치·추세를 계산한다. 수치는 모델이 아니라 Rust 도구가 계산한다.
3. **시각화** — 분석 결과를 차트로 보여준다.
4. **보고** — 현재까지 진행상황을 보고서로 만들어 PDF / Word / PPT로 넘긴다. 여러 서버(postgres + clickhouse …)의 결과를 한 보고서에 담을 수 있다.
5. **어디서나** — ChatGPT / OpenAI API, Claude Desktop, Claude Code, 기타 MCP 클라이언트에서 같은 키·같은 도구로 동작한다.

### 비목표
- 고객별 맞춤 MCP 서버 제작 (서버는 datasource 종류별 범용, 고객 차이는 바인딩으로만 표현)
- 샘플 데이터 모양에 맞춘 도구 (임의 스키마·타입에서 동작해야 한다)
- 모델이 직접 숫자를 계산하는 흐름
- 무거운 렌더링 스택 (헤드리스 브라우저, LibreOffice 등)

## 2. 사용자
| 사용자 | 예시 | 접근 범위 |
|---|---|---|
| master (FTG 운영) | 플랫폼 운영자 | 전 회사·서버 관리 |
| 내부 고객 admin/member | 보안인재 양성팀, 클라우드 인프라팀, 컴플라이언스(CSAP·ISO27001) 담당 | 부여받은 내부 서버 |
| 외부 고객 admin/member | 버그바운티 고객사 보안 담당자 | 자기 회사 테넌트의 부여받은 서버만 |

외부 고객이 붙는다는 것이 설계의 최우선 제약이다. **타 테넌트 데이터 노출 = 사고**. 모든 신규 기능은 격리 경로(API 키 → 사용자 → 서버 접근 → 바인딩 → 테넌트 고정 커넥션)를 그대로 타야 한다.

## 3. 현재 상태 (구현 완료)
- 도메인 `회사 → 사용자 → 서버`: `회사 × 서버 = 바인딩`, `사용자 × 서버 = ServerAccess(권한, 허용 도구)`, `API 키 = 사용자 대리 + scope`. DynamoDB 트랜잭션 가드 + outbox → Redis 동기화 + reconciler, 구 키 자동 이관
- MCP 서버 4종(postgres / clickhouse / mongo / hacker), 파서 기반 SQL·파이프라인 가드
- 공용 도구 `transform`(read), `compute`(read, Rust), `export`(export 권한, csv/xlsx, S3 presigned)
- PII 마스킹(Rust), 키별 rate limit, 접근 로그(Redis stream → DynamoDB 90일 + S3 감사 레이크 parquet 1년, 2026-10-01)
- 관리 콘솔 + Playwright E2E, 서버별 e2e / 범용성 테스트

## 4. 기능 설계

### 4.1 권한 계층 — 회사 → 사용자 → 서버 ✅ (2026-09-24)
API 키가 서버 1개에 묶인 현재 구조를, **사용자가 서버 접근을 갖고 키는 사용자를 대리**하는 구조로 바꾼다. 다중 서버 보고서의 전제 조건이다.

```
Company ─┬─ TenantBinding (company × server → tenant spec)        McpServer (전역 카탈로그)
         └─ User ─┬─ ServerAccess (user × server → permissions, allowed_tools)
                  └─ ApiKey (owner user, scope: 전체 | server_ids ⊆ user의 ServerAccess, expires_at, rate_limit)
```

| 엔티티 | 변경 |
|---|---|
| `ServerAccess` (신규) | `company_id, user_id, server_id, server_type, permissions, allowed_tools, status, version`. 권한·도구는 여기로 이동 |
| `ApiKey` | `server_id / server_type / permissions / allowed_tools` 제거 → `scope`(서버 부분집합, 기본 전체) 추가. 키는 권한을 **넓힐 수 없다** |
| `TenantBinding` | 그대로. 회사가 그 서버를 쓸 수 있는지 + 테넌트 값 |

**불변식**
- `ServerAccess` 생성·재활성: 회사·유저·서버·바인딩 `active`를 같은 `TransactWriteItems`의 `ConditionCheck`로 검증
- `ApiKey` 발급·재활성: 회사·유저 `active` + scope의 각 `ServerAccess` `active`를 같은 트랜잭션에서 검증
- `allowed_tools ⊆ permissions가 덮는 도구`는 `ServerAccess`에서 검증
- 호출 시점 유효 권한 = `ServerAccess(user, server)` (키 scope에 server 포함일 때만). **캐시 미스 = 거부**
- 사용자 정지 → 그 사용자의 모든 키·서버 접근 즉시 차단 (Redis `user:` 한 번의 쓰기, 팬아웃 없음)
- 서버 접근 정지 → 그 서버만 차단, 다른 서버는 유지

**Redis db1 투영**
```
company:{cid}                 {status, version}
user:{cid}:{uid}              {status, version}
server:{sid}                  {status, server_type, version}
binding:{cid}:{sid}           {status, server_type, tenant, version}
access:{cid}:{uid}:{sid}      {status, permissions, allowed_tools, version}          # 신규
apikey:{sha256}               {key_id, company_id, user_id, scope, status, expires_at, rate_limit_per_minute, version}
```

**전환**: 기존 키마다 `ServerAccess(user, key.server, key.permissions, key.allowed_tools)`를 만들고 키 scope = `[key.server]`로 옮긴다. 같은 유저·서버에 권한이 다른 키가 여럿이면 자동 병합하지 않고 이관 리포트에 올려 운영자가 정한다([8장 1번](#8-남은-질문)). 이관은 멱등 스크립트 + reconciler로 Redis 재투영. 이관 전후 `make e2e` 격리 테스트 전부 통과가 완료 조건.

### 4.2 공용 산출물 저장소 — S3 ✅ (2026-09-24)
서버 간 결과를 합치는 매개는 **S3 객체저장소**다. 서버끼리 직접 호출하지 않는다.
- 저장소: dev `~/dev/aws-local` MinIO, 운영 AWS S3 / NCP Object Storage (모두 S3 API, `S3_ENDPOINT`로 전환)
- 키 구조 (2026-10-01 재작업, data-engineering 레이크 규칙 준수): 버킷 `ftg-mcp`, 루트 `mcp/`·`control/`·`audit/`, Hive 파티션 `tenant=`/`user=`/`dt=` — [docs/S3_LAYOUT.md](docs/S3_LAYOUT.md)
- 산출물 메타(`artifact_id, kind, company_id, user_id, server_id, key_id, created_at, sha256, expires_at`)는 **S3 object metadata에만** 저장(MCP는 Redis만 쓰는 구조 유지 — DynamoDB 병행 안 함). 읽을 때 **메타의 company_id·user_id = 호출자**, sha256 일치, 만료, **원천 서버 재인가**(키 scope·접근·바인딩 active + read)를 검사 (prefix만 믿지 않는다)
- 저장: `compute`/`transform`의 `save=true` (export 권한), 조회: `list_artifacts`/`get_artifact` (read)
- `artifact_id`는 추측 불가능한 랜덤 ID. 목록 조회는 호출자 prefix 안에서만
- 수명: 산출물 기본 7일(버킷 lifecycle), 다운로드 링크는 presigned GET(TTL 15분)
- 버킷 전용 IAM 계정, 서버측 암호화(SSE-S3/KMS), 퍼블릭 접근 차단

### 4.3 시각화 ✅ — `chart` 도구 (각 서버 공용, 2026-09-24)
- 입력: `source`(서버 가드 경유) + 선택 `transform` / `compute` + 차트 스펙(line / bar / area / scatter / histogram / heatmap / table). 스펙은 pydantic 화이트리스트, 임의 표현식 없음
- 처리: 데이터 → Vega-Lite 스펙 생성 → `vl-convert-python`(Rust 기반 휠, 브라우저 불필요)으로 PNG/SVG 렌더
- 출력: 미리보기 PNG(MCP image content) + `artifact_id` (스펙·데이터 스냅샷·이미지를 S3에 저장)
- 외부 URL 데이터 로딩 차단(SSRF) — 데이터는 인라인만. **vl-convert의 `allowed_base_urls`만으로는 `file://`·image href가 막히지 않음을 확인** → 모델에게 Vega-Lite를 받지 않고 서버가 생성, 렌더 전 외부 참조 키 재검사. 한글 폰트 `fonts-nanum`(NanumGothic) 이미지에 번들
- 권한: 미리보기는 `read`. 산출물 저장은 `export` (데이터 스냅샷이 저장소로 나가므로)

### 4.4 보고서 ✅ — `report` 도구 (각 서버 공용, 2026-09-24)
별도 서버를 두지 않는다. 어느 서버에서 호출해도 같은 도구이며 S3의 **호출자 소유 산출물만** 읽는다.
- 흐름: 모델이 서버별로 `chart` / `compute`를 호출해 `artifact_id`를 모은다 → `report`에 아웃라인 전달 → 렌더 → S3 업로드 → presigned 링크
- 아웃라인 JSON: 섹션별 `heading | text | kpi(artifact) | table(artifact) | chart(artifact) | monitor(ref)`. **수치 블록은 산출물 참조만 허용**, 모델이 적은 숫자를 그대로 넣지 않는다
- 렌더러 (가벼운 순수 라이브러리만)

  | 형식 | 도구 | 비고 |
  |---|---|---|
  | pdf | `typst` Python 휠 (Rust, 단일 바이너리 내장) | 브라우저·LaTeX 불필요, 템플릿 1개 관리 |
  | docx | `python-docx` | |
  | pptx | `python-pptx` | 슬라이드 = 섹션 |

- FTG 템플릿(표지·색상·폰트)은 FTG 디자인 토큰 기준. 한글 폰트 번들
- **회사별 브랜딩** ✅(결정 9): 로고·브랜드 색·푸터. 밝은 색은 장식에만(가독성), 차트는 검증된 FTG 팔레트 유지
- Typst에는 내용을 JSON 데이터 파일로 넘겨 템플릿이 `json()`으로 읽는다 — 데이터 문자열은 마크업으로 평가되지 않음(`#read`·`#import` 리터럴 확인), compile root = 전용 임시 디렉터리
- 권한: `export` (데이터가 파일로 나간다). 인라인(≤2MB) 또는 presigned 링크
- 보고서 부록/메타에 생성자·사용 산출물·원천 서버·생성 시각을 남겨 감사 가능하게 한다
- PII 마스킹은 산출물 생성 시점에 이미 적용 — 보고서는 마스킹된 산출물만 받는다

### 4.5 모니터링 ✅ — MCP 조회 전용, 알림 채널 확장 가능 (2026-09-24)
- 정의·최근 50회 이벤트는 **S3**(`control/monitors/server_type=…/tenant=…/user=…/…`, `control/monitor_state/…`) — MCP는 DynamoDB를 쓰지 않고 Redis는 캐시이므로
- 각 MCP 서버의 스케줄러가 실행, 다중 레플리카는 Redis 잠금. **생성 키로 실행**: 매 실행 `resolve_hash(key_hash, server)`로 키·회사·사용자·서버·바인딩·접근 + `read` + `monitor_create` 허용 재확인, 아니면 `skipped`(사유 기록)
- 실행 결과(`ok | breach | skipped | error`, 값) → S3 상태 + Redis stream `monitor:events` → breach 시 `dispatch(channels)`
- 도구: `monitor_create`(생성 시 1회 즉시 실행) / `monitor_list` / `monitor_status` / `monitor_pause` / `monitor_delete` (모두 read)
- **지금은 조회만.** 알림은 `Notifier` 포트로 분리:
  ```
  monitor:events ─▶ notification dispatcher ─▶ Notifier(channel)   # 현재 등록된 채널 없음
                                                ├─ SlackNotifier   (추후)
                                                └─ MailNotifier    (추후)
  ```
  채널 추가 = `Notifier` 구현 + `channels` 값 허용 + 전송 로그. 모니터·스케줄러 코드는 바뀌지 않는다. 알림 본문도 PII 마스킹·회사 경계 규칙을 동일하게 따른다

### 4.6 게이트웨이 — `mcp-gateway` (단일 엔드포인트 ✅, OAuth 2.1 ✅ 2026-09-24)
- **단일 엔드포인트** `https://mcp.<domain>/mcp`. 키 scope의 서버들을 뒤에 두고 도구를 합쳐 노출
  - 도구 이름 충돌은 서버 별칭 접두사로 해결: `{server_alias}__run_select` (별칭 = 서버 이름 slug, 충돌 시 id 끝 4자)
  - 게이트웨이는 권한을 판단하지 않는다: 사용자 키로 업스트림 `?server=`에 목록·호출을 위임 → 인가·가드·rate limit·로그는 기존 서버가 수행. 전체 scope 키의 후보 서버는 Redis `accessidx:{cid}:{uid}`(발견용 인덱스)
  - `report`, `monitor_*`처럼 서버 무관 도구는 한 번만 노출
- **OAuth 2.1 인가 서버** (MCP authorization spec: PKCE S256, DCR, RFC 8414/9728 메타데이터, RFC 8707 리소스 지시자) — **동의 = 발급받은 API 키 입력**(결정 6). 토큰은 그 키를 대리하므로 4.1 계층·불변식이 그대로 적용되고, 키 정지 = 토큰 즉시 무효. redirect는 https만(루프백 예외) + 운영 허용 호스트 목록
- 기존 Bearer `mcpk_…`도 계속 허용 (Claude Code, API 호출용)
- TLS 종단, 공개 URL (OpenAI Responses API는 원격 공개 URL 필요)
- 현재(게이트웨이 전) 서버 선택은 MCP URL `?server=<server_id>`. 키 scope가 서버 1개면 생략 가능, 모호하면 거부

## 5. 클라이언트 호환성
| 클라이언트 | 전송 | 인증 | 현재 | 게이트웨이 후 |
|---|---|---|---|---|
| Claude Code | Streamable HTTP | `--header` Bearer | ✅ | ✅ |
| Claude Desktop (config) | stdio → `mcp-remote` 브리지 | 헤더 전달 | ✅ | ✅ |
| Claude.ai / Desktop 커스텀 커넥터 | Streamable HTTP | OAuth 권장 (정적 헤더는 제한적) | ✅ OAuth(API 키 동의) | ✅ |
| OpenAI Responses API / Agents SDK | Streamable HTTP, 공개 URL | `headers` / `authorization` | 공개 URL 필요 | ✅ |
| ChatGPT Developer mode / Apps | Streamable HTTP | OAuth 2.1 또는 무인증 | ✅ OAuth(API 키 동의) | ✅ |

- 도구 결과는 모든 클라이언트가 읽을 수 있게 **텍스트(JSON) + 이미지 + 링크** 조합으로만 준다
- 도구 설명은 클라이언트 무관하게 "숫자는 compute로", "차트는 chart로", "보고서는 report로" 유도
- 클라이언트 사양은 자주 바뀐다. 구현 시점에 공식 문서로 재확인할 것
- 상호운용 검증: Python SDK(FastMCP) 외에 **공식 MCP Inspector CLI(Node/TypeScript SDK)**로 게이트웨이·직접 엔드포인트의 tools/list·tools/call(텍스트·이미지)·Bearer 거부를 e2e로 확인 (`mcp-gateway/tests/test_inspector_compat.py`)

## 6. 보안 체크리스트 (신규 기능 공통)
- [x] 신규 도구는 `TOOL_CATALOG` 등록 + 권한 매핑 + 서버 테스트의 카탈로그 동일성 검사 통과
- [x] 저장소로 데이터가 나가는 도구(export, chart 저장, report)는 모두 `export` 권한
- [x] S3 산출물 읽기는 메타의 company_id·user_id·sha256 검증. 타 사용자 artifact_id를 넣으면 **존재 여부도 드러내지 않고** 동일 오류
- [x] 데이터 속 문자열이 보고서·차트에 들어갈 때 프롬프트 인젝션 전제. 렌더러에 스크립트·외부 리소스 로딩 없음 (차트: 서버 생성 스펙 + 외부 참조 재검사, Typst: JSON 데이터·전용 root)
- [x] csv/xlsx 수식 인젝션(`=`, `+`, `-`, `@`, 탭·개행 시작 셀) 이스케이프 — 순수 숫자 문자열 제외 (`export.py`)
- [ ] presigned URL은 짧은 TTL, 버킷 전용 계정, 퍼블릭 차단, SSE
- [x] 모니터 실행·알림은 소유 사용자 authz를 매 실행 재확인 (캐시 미스 = 거부)
- [x] OAuth: PKCE 필수, redirect URI https + 허용 호스트, 토큰 audience = 게이트웨이(RFC 8707), 토큰 → 키 매핑 후 매 요청 키·회사·사용자 재검증
- [ ] 접근 로그에 신규 도구 이벤트 포함 (어떤 보고서가 어떤 산출물·서버에서 만들어졌는지)

## 7. 결정 사항
| # | 주제 | 결정 (2026-09-24) | 근거 |
|---|---|---|---|
| 1 | 다중 서버 보고서 | 권한 계층을 **회사 → 사용자 → 서버**로. 키는 사용자를 대리, scope로 서버 제한 (4.1) | 사용자 단위로 여러 서버를 묶어야 한 보고서에 담을 수 있다 |
| 2 | 서버 간 결과 전달 | **S3 객체저장소**에 산출물 저장, `artifact_id`로 참조 (4.2). chart / report는 별도 서버 없이 공용 도구 | 서버 간 직접 호출 없이 합칠 수 있고, 격리 검사를 저장소 읽기 한 곳에 모은다 |
| 3 | 렌더 엔진 | 가벼운 쪽 — PDF `typst`, DOCX `python-docx`, PPTX `python-pptx`, 차트 `vl-convert` | 모두 pip 휠, 브라우저·오피스 런타임 불필요 |
| 4 | 파일 반출 권한 | 새 권한 없이 **`export`로 통합** | 데이터가 밖으로 나가는 행위는 하나의 권한으로 통제 |
| 5 | 모니터 알림 | **MCP 조회만**. `Notifier` 포트 + `channels` 필드로 Slack·메일 추가 여지 (4.5) | 우선 범위 축소, 확장은 구현 추가만으로 |

| 6 | OAuth 로그인 주체 (T13) | **발급받은 API 키로 동의** — OAuth authorize 화면에서 `mcpk_…` 입력 → 그 키에 묶인 토큰 (2026-09-24) | 기존 모델·불변식 그대로, 가장 가벼움. 외부 IdP는 필요 시 추가 |
| 7 | NCP 데이터 계층 (T34) | **AWS 전용 배포**. NCP는 Object Storage 모듈만 유지 | NCP엔 DynamoDB·AWS KMS 호환 없음 |
| 8 | 산출물 보관 기간 회사별 조정 (T32) | **안 함** (7일 고정) | 필요 시 재논의 |
| 9 | 보고서 회사별 템플릿 (T33) | **함** — 회사별 로고·브랜드 색 | 외부 고객에게 보고서 전달 |
| 10 | 회사 SSO 연동 | **회사별 OIDC 공급자 + 세션 키** (2026-11-07) — 인증만 외부 IdP, 인가는 ServerAccess 그대로. 커넥터 SSO 로그인은 사용자 명의 `sso:<client>` 만료 키를 발급해 기존 키 경로로 흐른다. 미등록 사용자는 도메인이 맞으면 MEMBER 자동 생성(회사별 on/off). SAML·IdP 그룹→권한 매핑은 안 함 | 결정 6의 "외부 IdP는 필요 시 추가" 이행. 새 principal 없이 리졸버·감사·정지 전파 무변경 |
| 11 | Workspace 자동 온보딩 | **플랫폼 Google 클라이언트 + 회사 자동 생성** (2026-11-07) — 1단계. Workspace 관리자가 "Google Workspace로 시작"을 누르면 `hd` 도메인으로 회사(id=도메인 슬러그)·플랫폼 Google 공급자·ADMIN 을 한 번에 생성, Admin SDK `isAdmin` 으로 관리자 확인. 비밀번호 없는 SSO 관리자 허용. 후속 2~4단계: 디렉터리 동기화(Team) → 팀 정책(AccessPolicy→ServerAccess 물질화) → 콘솔 축소. **기존 수동 경로는 유지**: 동기화 산출물은 `source` 로 표시하고 수동 레코드는 절대 덮어쓰지 않는다 | "설치하고 Workspace 연동하면 회사·팀·직원·권한이 자동으로" 요구. 고객이 Google Cloud 에 OAuth 앱을 만들 필요가 없어야 "쉽게 연결" |
| 12 | 디렉터리 동기화 (2단계) | **Admin SDK 읽기 + refresh token 암호화 저장, 사용자·그룹 → 사용자·Team·멤버십 미러링** (2026-11-07). 상태는 디렉터리→여기 한 방향(`suspended_by_sync`), 수동 레코드 불변, 사라진 그룹은 팀 정지. 엔티티 3종은 Redis 투영 없음 | 관리자가 직원·팀을 손으로 넣지 않게. 3단계 팀 정책의 입력 |
| 13 | 팀 정책 (3단계) | **AccessPolicy(팀\|전원 → 서버 → 권한·도구) → ServerAccess 물질화** (2026-11-07). 사용자·서버별로 덮는 정책의 합집합을 `source=policy` 행으로 생성·갱신·정지. 수동 행 불변, 운영자 편집 시 수동 전환, 미커버 사용자는 정지 행 유지. 동기화 후·정책 변경 후·온디맨드 실행 | 관리자가 사용자 N명이 아니라 팀 정책 몇 개만 다루도록. 리졸버·MCP 경로·불변식 무변경 |
| 14 | 콘솔 디자인 | **무채색 + 강조 1색(블루), 빨강은 위험·오류 전용** (2026-11-07) — 토큰은 `frontend/src/index.css` `@theme` 한 곳. 상태는 색이 아니라 점(활성=강조점, 정지=빈 점, 삭제=회색 점)으로, 활성 탭·활성 내비·주요 버튼만 강조색. 형태는 사이드바 브랜드 블록·상단 브레드크럼 바·세그먼트 탭·모노 ID·대문자 모노 섹션 라벨 같은 날 후반 다듬기: 셸은 Pretendard + 64px 아이콘 레일(브랜드·사용자 블록 없음) + 64px 헤더, 바탕은 흰색 단일(회색 캔버스·그림자 없음, 가는 선으로만 구획, 어두운 요소는 레일·표 머리뿐), 헤더 제목·레일 첫 아이콘·콘텐츠 좌측 가장자리를 같은 그리드(x=88, 중심 y=32)에 측정해 맞춤 | 색이 여러 개면 무엇이 중요한지 안 보인다. 새 화면은 토큰·공용 컴포넌트만 쓰고 hue 클래스(emerald/amber/sky…)를 직접 쓰지 않는다 |
| 15 | 콘솔 대시보드 데이터 | **읽기 전용 집계·메트릭·헬스·산출물 목록** (2026-11-07) — 전역 카운트는 엔티티 스캔을 Redis db2 에 캐시(`STATS_REFRESH_S`), 회사 카운트는 파티션 1회 조회. 24h 호출·거부·p50/p99 는 로그 컨슈머가 ack 뒤에 채우는 시간 단위 Redis 버킷(근사치, 재처리 시 중복 가능; 기록은 감사 lake). 헬스는 env 의 내부 URL(`MCP_INTERNAL_*`)로 `/healthz` 만, 서버 레코드의 공개 endpoint 는 절대 fetch 안 함(SSRF). 산출물은 S3 메타만, 다운로드 없음. 키 연장은 기존 PATCH | 화면 타일·스파크라인·피드에 필요한 데이터를 권한 경로를 건드리지 않고 제공. 정확한 통계는 감사 lake(parquet)에서 |
| 16 | 콘솔 로그인·Workspace 연동 방식 | **콘솔에는 SSO 로그인 없음, Workspace 는 운영자가 서비스 계정으로 연결** (2026-11-07) — 이 콘솔은 사내 AX팀(CTO·개발자)이 쓰는 운영 도구라 로그인은 이메일·비밀번호(admin/master + 비밀번호만 `can_login`). "Google Workspace로 시작"·"Google로 로그인"·`purpose=console` SSO·Workspace 관리자 `isAdmin` 확인·도메인으로 회사 자동 생성은 제거(결정 11 대체). 회사는 운영자가 만들고, 회사 상세에서 **서비스 계정 JSON(도메인 전체 위임) + 위임 관리자 이메일**을 등록하면 디렉터리 동기화와 플랫폼 Google 공급자(직원 커넥터 SSO)가 설정된다. 키는 Fernet 암호화·응답 미포함, 토큰 URL 은 키 파일의 `token_uri` 가 아니라 `GOOGLE_TOKEN_URL`(SSRF 방지). 커넥터 SSO(동의 화면 "회사 계정으로 로그인")는 유지 | 고객사 관리자가 콘솔에 들어올 일이 없는데 셀프서비스 온보딩이 비즈니스 로직을 꼬이게 함. 서비스 계정 방식은 대화형 동의가 없어 Entra·Okta 등 다른 디렉터리도 같은 "운영자가 자격증명 입력" 모델로 확장 가능. 플랫폼 OAuth 클라이언트가 민감 스코프를 요청하지 않게 되어 Google 앱 검증도 불필요 |

## 8. 남은 질문
1. ~~같은 서버에 권한이 다른 구 키 여럿~~ → 이관 시 첫 키 기준 접근 생성, 나머지는 **정지 + 리포트**(권한 확대 없음)로 처리함(2026-09-24, dev 데이터 281개 중 53개 정지). 키 단위 권한 축소가 실제로 필요해지면 재논의
2. 산출물 보관 기간 기본값(7일 제안)과 회사별 조정 가능 여부
3. 보고서 템플릿을 회사별로 커스터마이즈(로고 등)할지

## 9. 로드맵
| 단계 | 내용 | 완료 조건 |
|---|---|---|
| P1 ✅ | 멀티테넌트 골격, mcp-postgres, 콘솔 | 격리·권한·정지 전파 e2e |
| P2 ✅ | clickhouse / mongo / hacker, polars·duckdb, rate limit | 서버별 e2e + 범용성 테스트 |
| P3 ✅ | Rust core(PII, compute), transform / export | Rust ↔ Python parity |
| P4 ✅ | 권한 계층 전환 (`ServerAccess`, 키 scope) + 이관 + 콘솔 | 기존 e2e 전부 통과 + 사용자/서버 접근 정지 전파 e2e |
| P5 ✅ | S3 산출물 저장소 + `chart` | 4종 서버에서 차트, 타 사용자 artifact 접근 거부, SSRF 차단 테스트 |
| P6 ✅ | `report` (docx → pptx → pdf 순) | 두 서버 산출물을 한 보고서에, 한글·표·차트 포함 3형식, 수치 = 산출물 값 검증 |
| P7 ✅ | `mcp-gateway` + OAuth 2.1 | ChatGPT·Claude 커넥터·OpenAI API 실제 연결 확인 |
| P8 ✅ | 모니터링 + 스케줄러 + `Notifier` 포트(채널 없음) | 사용자/접근 정지 시 모니터 즉시 중단 e2e |
| P9 ◐ | 운영 강화 — CI ✅(워크플로 작성, 원격 실행 전), 메트릭 ✅, KMS 봉투 키 ✅, IaC(AWS) ✅, 회사별 DB role ✅, NCP IaC ◐(Object Storage만 — DynamoDB·KMS 대응 없음, T34 결정 필요), 트레이싱 ✅, Slack·메일 채널 | CSAP·ISO27001 통제 항목 매핑 |
