# 배포 계약 — mcp-cluster on AWS ECS

로컬 compose(`deploy/docker/`)와 운영(ECS)의 차이, 태스크별로 넣을 환경변수, Secrets Manager 대상을 적는다. ALB·Cloud Map·IAM·태스크 정의 자체는
`infra/terraform`(DynamoDB·S3·KMS·IAM)과 인프라 담당이 구성한다. 이 문서의 주소·키는 전부 예시다.

## 1. 무엇을 올리나

| 이미지 | Dockerfile | 운영 역할 | 로컬 대응 |
|---|---|---|---|
| `backend` | `deploy/docker/Dockerfile.backend` | 관리 API `/api/v1`, 접근 로그 → DynamoDB + S3 `audit/` | 같음 |
| `frontend` | `deploy/docker/Dockerfile.frontend` **target `serve`** | 정적 콘솔(`serve -s`, :80) | 로컬은 target `dev`(Vite) |
| `mcp-gateway` | `deploy/docker/Dockerfile.mcp-gateway` | 단일 MCP 엔드포인트 `/mcp` + OAuth 2.1 | 같음 |
| `mcp-postgres` · `mcp-clickhouse` · `mcp-mongo` · `mcp-hacker` | `deploy/docker/Dockerfile.mcp-<type>` | datasource 종류별 MCP 서버(테넌트 바인딩으로 고객 구분) | 같음 |

**올리지 않는 것**: nginx `gateway`(ALB + 인프라 게이트웨이가 대신, 라우팅 규칙은 `deploy/docker/gateway.nginx.conf.template` 참고), `redis`(ElastiCache),
`pg-sample`/`clickhouse`/`mongo` 샘플 픽스처(운영 datasource 는 콘솔에서 DSN 으로 등록), `frontend-e2e`.

라우팅(ALB → 게이트웨이): `/` → frontend:80, `/api/`·`/healthz`·`/docs` → backend:8080, `/mcp`·`/.well-known/`·`/authorize`·`/token`·`/register`·`/revoke`·`/oauth/` → mcp-gateway:9000,
`/mcp-<type>` → mcp-<type>:900x`/mcp`. Streamable HTTP 라 **응답 버퍼링 off, 읽기 타임아웃 300s 이상**. TLS 는 ALB 종단.

## 2. 환경변수 — 태스크별로 넣을 것

원칙: `env/<서비스>.env`의 `[ECS 에서도 주입]` 구획이 전부이고, `[로컬 전용]` 구획은 넣지 않는다. 코드 기본값과 같은 값은 생략한다. `env/` 파일을 이미지에 굽지 않는다(루트 `.dockerignore`가 제외).

| 키 | 대상 | 비고 |
|---|---|---|
| `APP_ENV=prod` | backend, mcp-* | dev 가 아니면 기본 시크릿·비밀번호 없는 Redis 거부(기동 실패로 드러남) |
| `AWS_REGION` | backend, mcp-* | 자격은 태스크 IAM 역할(`AWS_ACCESS_KEY_ID`/`SECRET` 주입 금지, `DYNAMO_ENDPOINT`·`KMS_ENDPOINT`·`S3_ENDPOINT` 비움) |
| `REDIS_URL=rediss://<elasticache>:6379` | backend, mcp-* | 비밀번호는 넣지 않고 `REDIS_PASSWORD`가 합성된다 |
| `REDACT_PII`, `S3_BUCKET`, `S3_PUBLIC_ENDPOINT`, `EXPORT_INLINE_MAX_BYTES`, `COMPUTE_ARROW_THRESHOLD_ROWS`, `OTEL_EXPORTER_OTLP_ENDPOINT` | backend, mcp-* | `env/common.env` 참고 |
| `SERVER_TYPE`, `PORT` | mcp-* | compose `environment:`와 같은 값(postgres 9001 · clickhouse 9002 · mongo 9003 · hacker 9004) |
| `MASTER_EMAIL`, `CORS_ORIGINS` | backend | `CORS_ORIGINS`는 콘솔 origin 하나 |
| `GATEWAY_PUBLIC_URL`, `GATEWAY_UPSTREAMS` | mcp-gateway | 공개 https 주소 / `<type>=http://<cloud-map-name>:900x/mcp,...` |
| `GATEWAY_BACKEND_URL` | mcp-gateway | 회사 SSO 핸드오프용 backend 주소(`http://<backend cloud-map>:8080`). 비우면 동의 화면에서 SSO 항목이 빠진다 |
| `CONSOLE_URL`, `GATEWAY_PUBLIC_URL` | backend | 회사 SSO 가 돌아올 공개 주소(둘 다 ALB https). IdP 에 등록하는 redirect URI = `CONSOLE_URL/api/v1/auth/sso/callback` |
| `VITE_API_BASE` | frontend **빌드 인자** | 같은 origin 이면 빈 값(기본). 정적 번들이라 런타임 env 는 효과 없음 |

## 3. Secrets Manager 로 주입

| 키 | 대상 | 조건 |
|---|---|---|
| `JWT_SECRET` | backend, mcp-* | 32바이트 이상. mcp-* 는 TENANT_SECRET_KEY 파생에만 쓰므로 `TENANT_SECRET_KEY(_ENC)`를 주면 불필요 |
| `TENANT_SECRET_KEY` 또는 `TENANT_SECRET_KEY_ENC` | backend, mcp-* | **같은 값**. 바꾸면 저장된 DSN 을 못 읽는다. `_ENC`는 KMS envelope(`scripts/kms_wrap_key.py`) |
| `REDIS_PASSWORD` | backend, mcp-* | ElastiCache AUTH 토큰 |
| `MASTER_PASSWORD` | backend | 12자 이상 |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | backend, mcp-* | 비우면 IAM 역할(권장). IAM 정책은 `infra/terraform/iam.tf`: mcp 는 `mcp/`·`control/`만, backend 는 `audit/` PutObject + `mcp/artifacts/` ListBucket·GetObject(콘솔 산출물 목록, HEAD 메타만) |
| `SSO_HANDOFF_SECRET` | backend, mcp-gateway | **같은 값**, 32자 이상. 비우면 커넥터 SSO 비활성(콘솔에는 SSO 로그인이 없다 — 운영자는 이메일·비밀번호). 회사별 OIDC client secret 은 env 가 아니라 콘솔에서 등록(DynamoDB 에 Fernet 암호화) |
| `MCP_INTERNAL_POSTGRES` / `..._CLICKHOUSE` / `..._MONGO` / `..._HACKER` / `..._GATEWAY` | backend | 콘솔 헬스 체크가 `GET <url>/healthz` 를 치는 내부 주소(ECS 서비스 디스커버리). 서버 레코드의 공개 `endpoint` 는 쓰지 않는다. 비우면 그 타입은 점검 안 함. 주기 `HEALTH_CHECK_INTERVAL_S`(60), 전역 집계 캐시 `STATS_REFRESH_S`(300) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | backend | 플랫폼 Google OAuth 웹 클라이언트 — Workspace 를 연결한 회사 직원의 **커넥터 SSO 로그인** 전용. 승인된 리디렉션 URI = `CONSOLE_URL/api/v1/auth/sso/callback`, 스코프 openid·email·profile 뿐(민감 스코프 없음 → Google 앱 검증 불필요). 비우면 회사가 자체 OIDC 앱을 등록해야 한다. `GOOGLE_ISSUER`·`GOOGLE_DIRECTORY_URL`·`GOOGLE_TOKEN_URL` 은 운영 기본값(https) 그대로 — dev 전용 mock 값을 주입하면 기동 거부 |
| (콘솔 등록) Workspace 서비스 계정 | backend | 디렉터리 동기화는 env 가 아니라 **회사별로 콘솔(회사 상세 → Google Workspace 연동)에 등록하는 서비스 계정 키**로 돈다. 절차: ① Google Cloud 콘솔에서 서비스 계정 생성 + JSON 키 다운로드, ② 고객사 Google Admin → 보안 → API 제어 → 도메인 전체 위임에 그 서비스 계정의 **클라이언트 ID** 와 스코프 3종(`admin.directory.user.readonly`·`group.readonly`·`group.member.readonly`) 추가, ③ 콘솔에 도메인·위임 대상 관리자 이메일·JSON 등록. 키는 DynamoDB 에 Fernet 암호화, 응답에 절대 나가지 않음. 키 파일 안의 `token_uri` 는 쓰지 않고 `GOOGLE_TOKEN_URL` 만 쓴다. `WORKSPACE_SYNC_INTERVAL_S`(기본 3600) 로 동기화 주기 |

로컬에서 같은 키는 `env/secrets/<서비스>.env`(gitignore, 키 목록은 `.example`)에 둔다.

## 4. 로컬과 다른 점 체크리스트

- 이미지 안에 소스 마운트가 없다 — compose 의 `volumes:`(핫리로드)와 `--reload` 커맨드는 로컬 전용. 운영 커맨드는 Dockerfile `CMD`.
- Redis 는 db1 authz 투영의 유일한 원천이라 ElastiCache 는 backend·mcp-* 가 **같은 인스턴스**를 봐야 한다.
- DynamoDB 테이블·GSI 는 `infra/terraform/dynamodb.tf`가 코드 스키마와 같은지 `backend/tests/test_iac_contract.py`로 고정된다.
- 헬스체크: backend `/healthz`, mcp-* `/metrics`(200), mcp-gateway `/metrics`.
