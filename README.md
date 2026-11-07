# MCP-Cluster

데이터·리소스를 내부·외부 고객이 AI 클라이언트에서 안전하게 사용하는 멀티테넌트 MCP 서버 클러스터입니다.

- **관리 콘솔:** 회사·사용자·서버·테넌트 바인딩·API 키·접근 로그 관리, 상태·헬스·호출 통계 대시보드.
- **인증·인가:** 회사 → 사용자 → 서버 권한 계층, 키 scope 제한, 허용 도구·rate limit·정지 즉시 전파.
- **데이터 조회:** PostgreSQL·ClickHouse·MongoDB·버그바운티 데이터 연결과 테넌트 격리.
- **정제·분석:** `transform`의 PII 마스킹·해시와 `compute`의 Rust 통계·시계열 연산.
- **내보내기·산출물:** CSV·XLSX 내보내기, S3 저장·조회, 서버 간 산출물 결합과 원천 서버 재인가.
- **시각화·보고서·모니터링:** PNG 차트, DOCX·PPTX·PDF 보고서, 주기 실행 모니터링(MCP 조회 전용).
- **클라이언트·직원 연결:** 단일 MCP 게이트웨이, OAuth 2.1·커넥터 SSO, Workspace 사용자·그룹 동기화와 팀 접근 정책. 콘솔 로그인은 운영자 이메일·비밀번호 방식입니다.

## 아키텍처

nginx `gateway`가 **유일한 네트워크 공개 포트 19000**에서 콘솔·API·MCP 요청을 경로별로 라우팅합니다.\
React 콘솔과 FastAPI 백엔드가 관리 정보를 다루고, 백엔드는 DynamoDB 저장·Redis 동기화·로그 적재를 담당합니다.\
MCP 게이트웨이는 사용자 키로 데이터소스별 MCP 서버에 호출을 위임하며, 서버는 테넌트별 데이터에 접근하고 Rust 연산·S3 산출물을 활용합니다.\
나머지 서비스는 내부 네트워크에서 통신하고, 테스트·샘플 데이터 확인용 호스트 접근은 loopback 전용입니다. 상세는 [아키텍처 문서](docs/ARCHITECTURE.md)를 참고하세요.

## 빠른 시작

Docker Compose와 `make`를 사용할 수 있어야 하며, **`~/dev/aws-local`을 먼저 기동**합니다. DynamoDB Local과 MinIO(S3)를 공유합니다.

```bash
(cd ~/dev/aws-local && docker compose up -d)
# 이 저장소 루트에서 실행
make up
```

- **관리 콘솔:** http://localhost:19000
- **기본 master 계정:** `env/backend.env` 참조.
- 로컬 기본 환경은 `env/<서비스>.env`를 사용합니다. 실제 시크릿은 Git에서 제외된 `env/secrets/<서비스>.env`에만 두며 기본 설정을 덮어씁니다.
- 새 데이터소스는 콘솔에서 서버 등록 → 회사 바인딩 → 사용자 서버 접근 부여 → API 키 발급 순서로 연결합니다.

## 자주 쓰는 명령

저장소 루트에서 실행합니다.

| 명령 | 용도 |
|---|---|
| `make up` | 전체 스택 빌드·기동 |
| `make down` | 스택 종료·볼륨 삭제; 다음 기동 시 샘플 데이터 재적재 |
| `make ps` | 컨테이너 상태 확인 |
| `make logs` | 서비스 로그 확인 |
| `make test` | 백엔드·MCP 서버 단위/통합 테스트; aws-local과 Redis 필요 |
| `make e2e` | 일회용 격리 스택에서 MCP E2E 실행 후 폐기 |
| `make e2e-ui` | 일회용 격리 스택에서 Playwright 관리 콘솔 E2E 실행 후 폐기 |
| `make lint` | 린트 검사 |

## 디렉터리 안내

주요 최상위 디렉터리입니다.

- `backend/` — FastAPI 관리 API, 데이터 저장·동기화·로그 적재.
- `frontend/` — React·TypeScript 관리 콘솔.
- `mcp-gateway/` — 단일 MCP 엔드포인트, 도구 네임스페이스, OAuth·업스트림 위임.
- `mcp-postgres/` — PostgreSQL MCP 서버, schema·database 격리.
- `mcp-clickhouse/` — ClickHouse MCP 서버, database·column 격리.
- `mcp-mongo/` — MongoDB MCP 서버, database 격리.
- `mcp-hacker/` — 버그바운티 데이터 MCP 서버, 연결 수준 읽기 전용.
- `libs/` — MCP 서버 공용 라이브러리(`common`: 인증·가드·transform·export)와 Rust 연산 커널(`rust-core`).
- `examples/` — 클러스터와 분리된 스터디 예제(TypeScript MCP + 자체 OAuth).
- `deploy/` — `docker/` 아래 Compose·Dockerfile·nginx 설정.
- `env/` — 서비스별 로컬 환경 설정과 시크릿 오버라이드.
- `infra/` — 샘플 데이터 픽스처, AWS·NCP Terraform 인프라.
- `ci/` — CI에서 사용하는 aws-local 대체 스택.
- `.github/` — lint·test·E2E·Rust parity CI 워크플로.
- `scripts/` — Claude Desktop 연결 등록 등 실행 스크립트.
- `docs/` — 설계·프로젝트·운영·클라이언트 연결 문서.

## 더 읽기

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 아키텍처 상세.
- [docs/PROJECT.md](docs/PROJECT.md) — 결정 사항·로드맵.
- [docs/DEPLOY.md](docs/DEPLOY.md) — ECS 운영.
- [docs/CLIENTS.md](docs/CLIENTS.md) — 클라이언트 연결.
- [CLAUDE.md](CLAUDE.md) — 작업 규칙·불변식.
