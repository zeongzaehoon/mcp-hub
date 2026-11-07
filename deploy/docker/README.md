# mcp-cluster 로컬 스택 (Docker)

이 디렉터리가 도커 관련 파일의 전부다. 레포 루트의 `Makefile`이 `-f deploy/docker/docker-compose.yml`로 호출하므로 **명령은 루트에서 `make`로** 친다.
실행 절차·env 규약·포트 표는 루트 `README.md`가 원본이고, 운영(ECS) 계약은 `docs/DEPLOY.md`다. 여기는 파일 역할만 적는다.

| 파일 | 역할 |
|---|---|
| `docker-compose.yml` | dev 전체 스택. 빌드 컨텍스트는 레포 루트(`../..`), env 는 `../../env/<서비스>.env` + 선택적 `../../env/secrets/<서비스>.env`(`required: false`) |
| `docker-compose.e2e.yml` | `scripts/e2e.sh`가 위에 겹치는 일회용 격리 스택(자체 DynamoDB Local·MinIO·Redis·DB, 호스트 포트 +10000, 프로젝트 `mcp-cluster-e2e`) |
| `Dockerfile.backend` | FastAPI 관리 API |
| `Dockerfile.frontend` | 관리 콘솔. `dev` 타깃(Vite + HMR, compose 가 사용) / `serve` 타깃(정적 빌드를 `serve -s`로, ECS 용). 정적 번들은 빌드 인자 `VITE_API_BASE`(기본 빈 값 = 같은 origin 상대경로) |
| `Dockerfile.frontend-e2e` | Playwright 러너(profile `e2e`) |
| `Dockerfile.mcp-{postgres,clickhouse,mongo,hacker}` | FastMCP 서버. maturin 스테이지에서 Rust 휠(`libs/rust-core`)을 빌드해 설치 |
| `Dockerfile.mcp-gateway` | 단일 MCP 엔드포인트 + OAuth 2.1. 드라이버·Rust 없음 |
| `gateway.nginx.conf.template` | **유일한 입구**(호스트 19000). stock `nginx:1.27-alpine`이 기동 시 envsubst 로 렌더하며 업스트림은 compose 의 `MCP_UPSTREAM_*` 환경변수로 주입. `resolver` + 변수형 `proxy_pass`라 컨테이너 재생성 후 IP 변화를 재시작 없이 따라간다 |

- 샘플 DB 픽스처(sql·js)는 `infra/{postgres,clickhouse,mongo}-sample/`에 그대로 두고 initdb 볼륨으로 마운트한다. 운영엔 샘플 DB 컨테이너가 없다.
- 루트 `.dockerignore` 하나가 모든 이미지에 적용된다(`.git`, venv, node_modules, `env/`, `deploy/`, 문서 제외).
- 새 MCP 서버를 붙이면: `Dockerfile.mcp-<type>` + compose 서비스 + nginx `location` 두 개(`/mcp-<type>`, `/mcp-<type>/metrics`) + `MCP_UPSTREAM_<TYPE>` + `env/mcp-<type>.env` + 게이트웨이 `GATEWAY_UPSTREAMS`.
