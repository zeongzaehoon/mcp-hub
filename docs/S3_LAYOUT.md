# S3 레이아웃 (`ftg-mcp`)

mcp-cluster 가 쓰는 객체는 전부 버킷 `ftg-mcp` 하나에 있습니다. 이 문서는 **무엇이 어디에, 어떤 이름·스키마로, 누가 쓰고 누가 읽는지**의 계약서입니다.
코드 쪽 진실은 `libs/common/mcpcluster_common/layout.py`(MCP 서버)와 `backend/app/infra/s3/audit.py`(감사 레이크)입니다. 둘이 이 문서와 어긋나면 코드가 맞고 이 문서를 고칩니다.

규칙은 data-engineering 데이터 레이크(`~/dev/data-engineering/S3_LAYOUT.md`)를 따릅니다. 버킷을 `ftg-report` 와 나눈 이유는 두 가지입니다.
첫째, 그쪽 계약이 루트를 `cdc/`·`control/`·`ethie/` 셋으로 고정합니다. 둘째, 여기에는 **외부 고객 데이터**가 들어가서 KMS·IAM·lifecycle·CSAP 경계를 따로 가져야 합니다.

---

## 0. 한 장 요약

```
s3://ftg-mcp/
├─ mcp/                ← MCP 서버가 만드는 사용자 산출물. 불변(If-None-Match), lifecycle 로 만료
│  ├─ artifacts/       tenant=<company_id>/user=<user_id>/<art_id>.json                          ARTIFACT_TTL_DAYS (7일)
│  ├─ exports/         tenant=<c>/user=<u>/dt=<YYYYMMDD>/<file_id>/<filename>.csv|xlsx            export_ttl_days (7일)
│  └─ reports/         tenant=<c>/user=<u>/dt=<YYYYMMDD>/<file_id>/<filename>.pdf|docx|pptx       report_ttl_days (30일)
├─ control/            ← 영속 설정. 덮어쓰기 허용(마지막 쓰기 우선)
│  ├─ monitors/        server_type=<t>/tenant=<c>/user=<u>/<mon_id>.json                          삭제 시에만 제거
│  └─ monitor_state/   tenant=<c>/user=<u>/<mon_id>.json                                          최근 50개 이벤트
└─ audit/              ← 감사 레이크. parquet, append-only
   └─ access_logs/     tenant=<c|_>/dt=<YYYYMMDD|_>/<first_stream_id>_<last_stream_id>.parquet    365일 (90일 후 Glacier IR)
```

**첫 경로는 `mcp/`·`control/`·`audit/` 셋뿐입니다.** 네 번째를 만들지 않습니다.

---

## 1. 공통 규칙

### 1.1 Hive 스타일 파티션
`<루트>/<데이터셋>/<key>=<value>/…/<파일>` 형식입니다. polars·DuckDB·Athena 가 경로의 `tenant`·`user`·`dt`·`server_type` 을 파티션 컬럼으로 읽습니다. parquet 안에는 이 컬럼이 없습니다(경로가 값입니다).

| 키 | 값 | 의미 |
|---|---|---|
| `tenant` | company_id (`[A-Za-z0-9_-]{1,64}`), 감사 로그만 `_` 허용 | 회사. 권한 계층의 최상위 |
| `user` | user_id | 사용자. 산출물 소유 단위(키가 아니라 사용자) |
| `dt` | `YYYYMMDD` (UTC). 감사 로그만 `_` 허용 | exports·reports 는 생성일, access_logs 는 이벤트 `ts` 의 날짜 |
| `server_type` | `postgres`·`clickhouse`·`mongo`·`hacker` … | monitors 만. 서버마다 스케줄러가 자기 타입 경로만 LIST 하므로 맨 앞에 둡니다 |

- **경로를 바꿀 수 있는 값(`/`, `=`, `..`, 빈 값)은 거부합니다**(`layout.LayoutError`). 바로잡아서 쓰지 않습니다(fail-closed).
  예외는 감사 로그입니다. 인증 전에 거부된 호출처럼 회사가 없거나 비정상이면 `tenant=_` 로 모읍니다. 기록을 버리지 않기 위해서입니다.
- `mcp/artifacts` 에는 `dt` 가 없습니다. id 하나로 다시 읽는 객체이고 분석 대상이 아니기 때문입니다.
- `server_id` 와 `key_id` 는 경로가 아니라 **객체 메타데이터**에 둡니다. report 는 원천 서버가 여럿이라 경로 하나로 표현할 수 없습니다.

### 1.2 불변·멱등
- `mcp/**` 와 `audit/**` 는 `PutObject If-None-Match: *` 로 **한 번만** 씁니다(`ObjectStorage.put_new`).
  - `mcp/` 키에는 랜덤 id 가 들어가므로 412 는 버그입니다. 덮어쓰지 않고 오류로 드러냅니다.
  - `audit/` 키는 그 파일이 담은 Redis stream id 범위입니다. 같은 배치를 다시 쓰면 같은 키가 되고, 412 는 "이미 기록됨"으로 보고 넘어갑니다.
- 덮어쓰기 예외는 `control/**` 뿐입니다. 모니터의 `next_run_at` 갱신과 이벤트 50개 링버퍼 때문입니다. 레이크 데이터가 아니라 설정입니다.
- 경로의 파티션은 **정리 수단이지 보안 경계가 아닙니다.** artifact 를 읽을 때마다 다음을 다시 확인합니다(불변식 7).
  - 메타데이터의 company_id·user_id 가 호출자와 같은지
  - sha256 이 맞는지
  - 만료되지 않았는지
  - 원천 서버(`sources`) 전부에 대해 지금도 권한이 있는지

### 1.3 누가 쓰고 누가 읽나 (IAM 도 이 표대로)

| 경로 | 쓰기 | 읽기 | 권한(운영 IAM) |
|---|---|---|---|
| `mcp/artifacts/**` | MCP 서버 (`save=true`, chart) | MCP 서버 (`get_artifact`, report) | MCP 역할: Get/Put/Delete `mcp/*`, List `mcp/artifacts/*` |
| `mcp/exports/**`, `mcp/reports/**` | MCP 서버 (`export`, `report`) | 사용자 (presigned GET, 15분) | 위와 같음 |
| `control/**` | MCP 서버 (monitor_*) | MCP 서버 스케줄러 | MCP 역할: Get/Put/Delete `control/*`, List `control/monitors/*` |
| `audit/access_logs/**` | backend 로그 consumer | 감사(Athena·DuckDB, 운영자) | backend 역할: **PutObject `audit/*` 만**. 읽기·삭제·목록 없음 |

backend 는 감사 기록을 쓸 수만 있고 고치거나 지울 수 없습니다. MCP 역할은 `audit/` 에 아예 접근하지 못합니다(`backend/tests/test_iac_contract.py` 가 검사).

---

## 2. `mcp/artifacts` — 서버 간 결과 전달

```
mcp/artifacts/tenant=co_1a2b…/user=usr_3c4d…/art_<32 hex>.json
```
- 본문: `{"artifact_id", "kind": "table|compute|chart", "title", "server_id", "server_type", "sources": [...], "created_at", "payload"}`
- 메타데이터: `artifact_id, kind, company_id, user_id, server_id, server_type, key_id, title(ASCII), created_at, expires_at, sha256, bytes, sources`
- 상한 `ARTIFACT_MAX_BYTES`(5 MB). 목록 조회는 호출자 prefix 안에서 최대 500개입니다.

## 3. `mcp/exports`, `mcp/reports` — 반출 파일

```
mcp/exports/tenant=<c>/user=<u>/dt=20261001/<32 hex>/sales-q3.csv
mcp/reports/tenant=<c>/user=<u>/dt=20261001/<32 hex>/monthly.pdf
```
- `filename` 은 `export.safe_filename` 으로 `[A-Za-z0-9._-]` 만 남긴 값입니다. 앞자리 `.` 는 거부합니다.
- 메타데이터: `company_id, user_id, server_id, key_id, sha256`. 응답의 `object_key` 가 이 키입니다.
- 2 MB 이하는 inline base64 로 돌려주고 저장하지 않습니다.

## 4. `control/monitors`, `control/monitor_state`

```
control/monitors/server_type=postgres/tenant=<c>/user=<u>/mon_<32 hex>.json
control/monitor_state/tenant=<c>/user=<u>/mon_<32 hex>.json      {"events": [최근 50개]}
```
- 정의 문서에 `company_id·user_id·server_id·key_hash` 가 들어 있습니다. 실행할 때마다 생성 키로 authz 를 다시 확인합니다(불변식 11).

## 5. `audit/access_logs` — 접근 로그 레이크

```
audit/access_logs/tenant=co_1a2b…/dt=20261001/1727740800000-0_1727740800450-3.parquet
```
- 흐름: MCP 서버 → Redis stream `logs:access` → backend `AccessLogConsumer` → **S3 parquet + DynamoDB `mcp_cluster_logs`**.
  두 쓰기가 모두 성공해야 stream 을 ack 합니다.
- DynamoDB 는 콘솔 조회용으로 90일(TTL)만 둡니다. S3 는 **1년 이상** 보관합니다(「개인정보의 안전성 확보조치 기준」 제8조. 5만 명 이상·고유식별/민감정보 처리 시 2년). lifecycle 은 생성 후 365일 만료, 90일 후 Glacier IR 이며, terraform `audit_retention_days`(≥ 365 검증)와 `layout.AUDIT_RETENTION_DAYS` 가 같은 값입니다.
- 배치 하나는 (tenant, UTC 일) 마다 파일 1개로 나뉩니다.

parquet 스키마(`schema_version = 1`, zstd):

| 컬럼 | 타입 | 설명 |
|---|---|---|
| `schema_version` | int32 | 1 |
| `stream_id` | string | Redis stream id. 중복 제거 키 |
| `ts` | string | 이벤트 시각 ISO-8601 |
| `key_id`, `user_id`, `server_id`, `server_type`, `tool` | string | 누가 어느 서버의 어떤 도구를 |
| `args_summary` | string | DynamoDB 행과 같은 요약(원문 아님) |
| `status` | string | `ok` / `denied` / `error` |
| `error` | string (nullable) | |
| `duration_ms` | int64 | |
| `extra_json` | string | `extra` 를 JSON 으로 |

```python
import polars as pl
pl.scan_parquet("s3://ftg-mcp/audit/access_logs/**/*.parquet", hive_partitioning=True) \
  .filter((pl.col("tenant") == "co_1a2b…") & (pl.col("dt") >= "20261001")).collect()
```

---

## 6. 경로 규칙을 바꿀 때
1. `layout.py`·`backend/app/infra/s3/audit.py`·`infra/terraform{,-ncp}`(lifecycle·IAM)·`backend/tests/test_iac_contract.py` 를 **같이** 고칩니다.
2. 영속 객체(`control/`)는 옛 경로에서 새 경로로 옮깁니다. `mcp/` 는 TTL 이 짧아 옮기지 않고 만료를 기다립니다. `audit/` 는 옮기지 않습니다(불변 기록).
3. 이 문서와 `docs/ARCHITECTURE.md` 를 갱신합니다.

### 6.1 2026-10-01 이전 (`mcp-exports` 버킷 → `ftg-mcp`)
옛 키는 `exports/{cid}/{server}/{key}/…`, `artifacts/{cid}/{uid}/…`, `reports/…`, `monitors/{type}/{cid}/{uid}/…`, `monitor-state/…` 입니다. monitor 만 옮깁니다.
```bash
docker compose run --rm --no-deps mcp-postgres python -m mcpcluster_common.migrate_layout                      # dry-run
docker compose run --rm --no-deps mcp-postgres python -m mcpcluster_common.migrate_layout --execute --delete-legacy
```
- 경로의 회사·사용자·monitor id 가 문서 안의 id 와 다르면 건너뜁니다.
- 복사한 뒤 sha256 을 비교하고 나서야 원본을 지웁니다. 다시 돌려도 안전합니다.
- 옛 버킷은 TTL 객체가 다 만료되면 지웁니다.

## 7. 하지 말 것
- 첫 경로에 `mcp/`·`control/`·`audit/` 외 폴더 만들기.
- `key=value` 없는 경로, layout 모듈을 거치지 않고 키 문자열 조립하기.
- `mcp/`·`audit/` 객체 덮어쓰기. `audit/` 에 삭제 권한 주기.
- 파티션 경로만 믿고 소유자 검증 생략하기.
- `ftg-report`(data-engineering) 버킷에 쓰기.
