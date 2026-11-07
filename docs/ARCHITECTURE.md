# Architecture

## Components
Only `gateway` (nginx) publishes a host port; every other container is reached by compose service name and nginx routes
by path (`deploy/docker/gateway.nginx.conf.template`, upstreams injected as `MCP_UPSTREAM_*`). The `path` column is under `http://localhost:19000`.

| service | tech | container port / path | role |
|---|---|---|---|
| gateway | nginx 1.27 | 80 → host **19000** | the only published port; path → container-name reverse proxy (Streamable HTTP unbuffered, HMR websocket) |
| frontend | Vite + React + TS | 5173 · `/` | admin console (companies / users / servers / bindings / api-keys / logs / ops) |
| backend | FastAPI (async) | 8080 · `/api/v1`, `/healthz`, `/docs` | source of truth writer, DynamoDB ↔ Redis sync, log consumer, ops API |
| dynamodb | ~/dev/aws-local `aws-local-dynamodb` (external network `aws-local`) | 8000 | single-table `mcp_cluster` + `mcp_cluster_logs` (persistent) |
| redis | redis 7 | 127.0.0.1:6379 (requirepass, env/redis.env) | db1 auth/tenant projections, db2 hot cache + rate-limit windows, db3 access-log stream |
| minio (S3) | ~/dev/aws-local `aws-local-minio` (network `aws-local`) | 9000 | bucket `ftg-mcp`: `mcp/` (artifacts, exports, reports) · `control/` (monitors) · `audit/` (access-log lake) — [S3_LAYOUT.md](S3_LAYOUT.md) |
| local-kms | ~/dev/aws-local `aws-local-kms` | 8002 | KMS for the tenant key envelope |
| mcp-gateway | FastMCP 4 Provider + ProxyTool | 9000 · `/mcp`, `/.well-known/*`, `/authorize`, `/token`, `/register`, `/revoke`, `/oauth/*`, `/metrics` | single endpoint; lists/forwards with the caller's key; OAuth 2.1 (API-key consent) |
| mcp-postgres | FastMCP 4 + asyncpg | 9001 · `/mcp-postgres` (+ `/mcp-<type>/metrics` for every direct server) | tenant-scoped Postgres tools |
| mcp-clickhouse | FastMCP 4 + clickhouse-connect + polars/duckdb | 9002 · `/mcp-clickhouse` | tenant-scoped analytics (database / column mode) |
| mcp-hacker | FastMCP 4 + asyncpg (shared `mcpcluster_common.postgres`) | 9004 · `/mcp-hacker` | read-only bug-bounty data; connection-level read-only |
| mcp-mongo | FastMCP 4 + pymongo async | 9003 · `/mcp-mongo` | tenant-scoped MongoDB (database mode only) |
| mongo | mongo 7 | 27017 (not published) | demo datasource: `acme_mg` / `globex_mg` |
| clickhouse | clickhouse-server 24.8 | 8123 (http) / 9000 (native); host 127.0.0.1:58123 / 59000 | demo datasource: `biz.*` business dataset (column mode by `company_id`: ftg / ftg-academy / ftg-cloud; `fx_rates` untenanted), `weird_db` (exotic types, database mode) |
| pg-sample | postgres 16 | 5432 (not published) | demo datasources: db `sample` (mcp-postgres) and db `hacker` (mcp-hacker), `acme` / `globex` schemas each |

## Domain tree and invariants
Hierarchy **company → user → server**. Permissions live on the user's access to a server; an API key only
acts on behalf of its user and can narrow (never widen) which of those servers it may use.
```
Company ─┬─ TenantBinding (company × McpServer → tenant spec)      McpServer (global catalog)
         └─ User (master|admin|member)
              ├─ ServerAccess (user × McpServer → permissions ⊆ {read,create,update,delete,export},
              │                allowed_tools ⊆ catalog[server_type])
              └─ ApiKey (scope server_ids ⊆ user's accesses | null = all of them, expires_at, rate limit)
```
* A ServerAccess is only granted / re-activated when **company, user, server (type matching) and the company's
  binding to that server are all `active`**, checked as DynamoDB `ConditionCheck`s inside the same
  `TransactWriteItems` as the Put (`backend/app/services/access.py`). No read-then-write window.
* An ApiKey is only issued / re-activated / re-scoped / rotated when company and user are active and **every
  scoped server has an active ServerAccess** for that user — same transaction (`services/apikeys.py`).
  A scope is capped at 50 servers (TransactWriteItems limit). `server_ids: null` is stored explicitly
  (`scope_all`), so a missing field can never be read as "all servers".
* Every `allowed_tool` must be covered by a granted permission (`domain/models.py::ServerAccess`),
  so an access never lists a tool it cannot call.
* **Legacy keys** (pre-hierarchy: one server + own permissions) are migrated at startup by
  `services/migrate.py` (idempotent): each becomes a ServerAccess + a key scoped to exactly that server. When a
  user already has an access to that server with different permissions, the key is **suspended and reported**,
  never merged (no widening). Until migrated, a legacy item is read as scoped to its one server.
* Tenant spec modes are whitelisted per server type (`domain/tenant.py`); identifiers are regex-checked
  so they can be interpolated as `search_path`/database names.
* Status machine: `active ⇄ suspended → deleted` (terminal). Company delete tombstones children in
  chunks; security never depends on that cascade (see below).
* Optimistic locking: every entity has `version`; updates are full-item Puts guarded by `version = :v`.
* Raw API keys are shown once; DB and Redis only hold SHA-256. Datasource credentials are Fernet-encrypted
  at rest and in Redis; MCP servers hold the same key and decrypt in memory. The Fernet key comes from a **KMS envelope**
  (`TENANT_SECRET_KEY_ENC` = CiphertextBlob of a 32-byte data key, `scripts/kms_wrap_key.py`; dev: aws-local local-kms)
  in preference to a plain `TENANT_SECRET_KEY`; only an explicit `APP_ENV=dev` may derive it from `JWT_SECRET` (an unset
APP_ENV is production: strict key, PII redaction on, metrics token required). The envelope carries the KMS
EncryptionContext `purpose=tenant-secret-key`, which unwrap presents and IAM requires. A configured envelope that
  cannot be unwrapped stops the process at startup — never a fallback to a weaker key.

## DynamoDB layout
See `backend/app/infra/dynamo/keys.py` (docstring lists every PK/SK/GSI pattern). Uniqueness of email and
key-hash is enforced with `UNIQ#…` marker items written in the same transaction.

## DB → Redis synchronisation
1. Every mutation transaction also writes an `OUTBOX` row.
2. After commit the service re-reads the entity from DynamoDB and writes its projection to Redis db1 with a
   Lua `SET IF version >= current` guard (`infra/redis/sync.py`), then deletes the outbox row.
3. Background outbox worker retries leftovers; a periodic reconciler scans DynamoDB, rewrites drifted keys,
   forces DB truth over tampered (higher-version) keys, and deletes stray Redis keys with no DB entity.
4. `POST /ops/reconcile` and `POST /ops/resolve` expose the same logic for operators.
5. **Self-healing after a Redis wipe.** Redis is a cache: when it restarts without persistence (or fails over
   to an empty node) the `meta:epoch` marker disappears; the outbox loop notices on its next tick (2 s) and
   rebuilds every projection from DynamoDB before continuing, and the log consumer recreates its stream
   group on `NOGROUP`. Until the rebuild completes MCP servers deny (no record ⇒ no access), never grant.
   Missing server / binding / access records all answer "server not available to this key", so a caller cannot
   probe which server ids exist.

Redis db1 keys (no fan-out — a company or user suspend is one write):
```
company:{cid}              {status, version}
user:{cid}:{uid}           {status, version}
server:{sid}               {status, server_type, name, dsn_enc, version}
binding:{cid}:{sid}        {status, server_type, tenant, version}
access:{cid}:{uid}:{sid}   {status, server_type, permissions, allowed_tools, version}
accessidx:{cid}:{uid}      SET of server ids with an access record (gateway discovery only; never used to authorize)
apikey:{sha256}            {key_id, company_id, user_id, server_ids (null = all), status, expires_at, rate_limit_per_minute, version}
```

## Datasource-agnostic by design
The MCP servers know nothing about the datasources they serve. The sample databases under `infra/` are
test fixtures only; the contract is:

* **Connection per server record.** `McpServer.dsn` (write-only; Fernet-encrypted in DynamoDB and Redis as
  `dsn_enc`, masked as `connection_hint` in responses) tells the process where that server's datasource
  lives. One `mcp-postgres` process therefore serves every registered postgres server; the process env
  `BASE_DSN` is only a fallback for servers without a DSN. Pools are keyed on (company, server) and rebuilt
  when the binding *or* the server DSN version changes. DSN scheme must match the server type.
* **Identifiers are never assumed.** Schema/database/column names accept anything that can be quoted
  (1–63 chars; only control chars, quotes, backslash and semicolon are refused) and are quoted wherever
  they are interpolated (`search_path`, sqlglot identifiers). Engine rules that quoting cannot fix are
  checked per type (MongoDB database names). Collection names follow MongoDB's own rules only.
* **Types are never assumed.** `mcpcluster_common/serialize.py::to_jsonable` turns any driver value into
  JSON: exact ints stay ints, non-integral or > 2^53 numerics become strings (no silent rounding),
  datetimes ISO-8601, bytes `{"$base64": …}`, UUID/IP/enums strings, ranges objects, unknown objects
  `str()`. asyncpg decodes json/jsonb to objects. ClickHouse results use native rows (not the server's
  Arrow output, which changes Enum/UUID semantics); `aggregate` builds Arrow from those rows.
* **Guards parse, they do not pattern-match.** Multi-statement detection relies on the parser (a `;`
  inside a literal is fine); Postgres blocks the `pg_*`, `lo_*`, `dblink*` families instead of a name list.
* **Fixtures prove it.** Each datasource ships a "weird" fixture (mixed-case and spaced names, reserved
  words, exotic types, views, empty tables) and `tests/test_generic.py` in every server exercises it plus a
  second server registered purely through the API with its own DSN.

## Cleaning and export (`mcpcluster_common/transform.py`, `export.py`, `datatools.py`)
Every server exposes two engine-agnostic tools built on the normalised rows:

* **`transform`** (permission `read`): a whitelisted, pydantic-validated pipeline — select / drop / rename /
  cast / filter / dedupe / fill_null / drop_nulls / trim / lower / upper / replace / to_datetime / sort /
  limit / aggregate / concat / arith / round / mask (PII) / hash (sha256 pseudonymisation). No expression
  language, no evaluated strings; each op is a fixed polars call. Frames are inferred from arrow-friendly
  scalars and fall back to strings for mixed columns, so any datasource shape works.
* **`compute`** (permission `read`): numeric/statistical kernels implemented in Rust (`rust-core/src/compute.rs`,
  rayon, GIL released): describe, percentiles (type-7 linear), histogram, group_stats, top_k, distinct_count,
  correlation (Pearson + Spearman), outliers (IQR / z-score), time_bucket, moving_average, and five business-analysis
  ops: linear_trend (OLS on a row index or a numeric/datetime x, `horizon` forecast points stepped by the mean x
  spacing), growth (lagged abs/pct change plus first→last total), pareto (per-key sums, cumulative share,
  `keys_to_threshold`), ab_test (Welch t-test; two-sided p from the Student t CDF via a regularized incomplete beta
  implemented identically in Rust and Python; Cohen's d on the pooled sd) and seasonality (count/mean/index per UTC
  month | weekday | quarter | hour — Rust converts epoch days with Hinnant's civil_from_days, Python with `datetime`).
  Specs are pydantic models (no expressions); an optional `transform` pipeline runs first. The tool description tells the model to
  use it for any calculation instead of reasoning over numbers, and the response carries `engine: rust`.
  `mcpcluster_common/compute.py` mirrors every kernel in Python for environments without the wheel; the parity
  test compares both on 50k-row inputs. Two hand-offs feed the same kernels: **copy** (Python lists, cheapest
  for small inputs) and **arrow** (polars → pyarrow → Rust via the Arrow C Data Interface, no per-cell Python
  objects). `transfer=auto` switches to arrow at `COMPUTE_ARROW_THRESHOLD_ROWS` (20k) and falls back to copy
  when a column has no supported arrow type; the response reports `transfer` so the choice is auditable.
* **`export`** (permission `export`, deliberately separate from `read`): csv (UTF-8 BOM by default) or xlsx
  of the source rows after the optional pipeline. Results ≤ `EXPORT_INLINE_MAX_BYTES` (2 MB) come back inline
  as base64; larger ones (or `delivery=link`) are uploaded to S3-compatible storage — MinIO from
  `~/dev/aws-local` in dev — under `mcp/exports/tenant=…/user=…/dt=…/` ([S3_LAYOUT.md](S3_LAYOUT.md)) and a presigned GET URL valid for
  `EXPORT_URL_TTL_S` (15 min) is returned. PII redaction applies before the file is written; nested values are
  JSON text; integers beyond 2^53 are written as text in xlsx because Excel would round them.
* Each server contributes only a `fetch_source` coroutine (`{"sql": …}` for SQL engines, `{"collection", filter|pipeline}`
  for MongoDB) that goes through the same guards as its query tools, so a write cannot hide in a source.

## Gateway (`mcp-gateway`)
One endpoint (`:19000/mcp`) for every server a key may use. Per request it rejects unknown/inactive/expired keys,
collects candidate servers (key scope, or `accessidx:{cid}:{uid}` for an all-servers key), keeps those whose server,
binding and access records are active, and lists each upstream's tools **with the caller's own key**
(`<upstream>?server=<id>`) — so the list is exactly what that server allows. Tools are exposed as `<alias>__<tool>`
(alias = slug of the server name + last 4 of its id, always — an alias never moves to another server); `report`, `list_artifacts`, `get_artifact` are exposed once
and routed to the first server offering them. Calls are forwarded the same way (FastMCP `ProxyTool`). The gateway holds
no datasource credentials and makes no authorization decision: guards, rate limits, redaction and access logs stay on
the upstream servers. Upstreams are configured with `GATEWAY_UPSTREAMS`. Upstream tool lists are cached in-process per
(key hash, server, server/binding/access versions) for `GATEWAY_LIST_CACHE_TTL_S` (30 s): any permission change bumps
a version and misses immediately, and discovery always reads the live records, so the cache never extends access. Upstream MCP sessions are pooled
per (key hash, server, permission versions) — idle 60 s, max 1000, dead sessions replaced — so a call costs one
upstream round trip instead of a fresh initialize handshake. The pool lock covers only its dictionaries: connects and
closes happen outside it with timeouts (`GATEWAY_UPSTREAM_CONNECT_TIMEOUT_S` 5 s, call 120 s), concurrent requests for
the same key share one connect, so a hung upstream delays only itself. Discovery also requires the company and user to
be active, and an idle sweeper drops sessions (and the raw keys in them) without waiting for traffic.

## OAuth 2.1 (`mcp-gateway/mcp_gateway/oauth.py`)
For connectors that cannot send a static header (ChatGPT, Claude.ai) the gateway is also the authorization server
(`GATEWAY_PUBLIC_URL` enables it): RFC 8414 / 9728 metadata, dynamic client registration, PKCE S256 (SDK), resource
indicators pinned to the gateway. **Consent is the person entering an issued API key**; the code, access token (1 h)
and refresh token (30 d, rotating) stand for that key — Redis db4 holds only token hashes plus the key encrypted with
the tenant Fernet key. Each request maps token → key and then follows the plain key path (gateway check + upstream
authorization), so suspending, rotating or expiring the key kills its tokens at once and a suspended key cannot refresh.
Registration only accepts https redirect URIs (loopback excepted) and, with `GATEWAY_OAUTH_REDIRECT_HOSTS`, only listed
hosts — open DCR would otherwise let a phishing client receive codes. The consent page shows the client and redirect
host, escapes all client metadata, sends CSP `frame-ancestors 'none'` / no-store, and allows 5 attempts per request.
Plain `mcpk_…` bearers keep working; requests with no credentials get 401 + `WWW-Authenticate: Bearer resource_metadata=…`.

## Company SSO (`backend/app/services/sso.py`, `core/oidc.py`; gateway `SsoBridge`)
The console has **no SSO login**: it is an operations tool for the in-house AX team (CTO, developers), who sign in
with email + password (`can_login` = admin/master with a password). SSO exists for one purpose only — **MCP
connectors**. A company may register one OpenID Connect provider (`IdentityProvider`: issuer, client id, Fernet-encrypted
client secret, allowed email domains — unique across companies — optional Google `hd`, auto-provision flag), or use
the deployment's **platform Google client** (`platform: google`, no client id/secret stored; `GOOGLE_CLIENT_ID/SECRET`,
scopes openid/email/profile only). The provider only answers **who** the person is; **what** they may do stays on
`ServerAccess`, so no authorization code changed.
The backend is the relying party (discovery, PKCE S256 + client secret, ID token verified against the provider's JWKS:
issuer, audience, expiry, nonce; `email_verified` must be true and the email domain / `hd` must match). Identity
resolution, fail closed: linked `(issuer, subject)` → same-company user with that email (links it) → auto-provisioned
member → refused. A subject marker (`UNIQ#IDENTITY#`) is unique across companies, an email never links across companies.
Flow: the OAuth consent page offers "회사 계정으로 로그인" (`/oauth/consent/sso`) → `GET /api/v1/auth/sso/start?email=`
(purpose `gateway` only) → provider → `/api/v1/auth/sso/callback`; the backend issues a **session API key** in the
person's name (`sso:<client>`, all-servers scope, 30-day expiry, visible and revocable in the console) and returns a
one-time 60 s handoff code to `/oauth/sso/return`; the gateway swaps it server-to-server (`GATEWAY_BACKEND_URL` +
`SSO_HANDOFF_SECRET`) and continues exactly as if that key had been pasted. Everything downstream — resolver, upstream
authorization, access logs, suspend propagation — is the ordinary key path. Errors reach the gateway's return page as
short codes (`error=`); provider details stay in the backend log.
Transactions (`sso:txn:`, 10 min) and handoff codes (`sso:handoff:`, sha256) live in Redis db2 and are single use.
Removed 2026-11-07 (PROJECT.md decision 16): console SSO login, "Google Workspace로 시작" self-onboarding (company
created from `hd`, Workspace-admin `isAdmin` check), "Google로 로그인". Companies are created by operators.

**Workspace connection (`services/workspace.py`, `core/google_directory.py`).** An operator connects a company's
Google Workspace from the company page: `PUT /companies/{cid}/workspace` with the domain, the Workspace admin to
impersonate and the **service-account JSON key** (write-only, validated — `type: service_account`, readable PEM — and
stored Fernet-encrypted as `WorkspaceConnection.service_account_enc`; the response carries `client_email`,
`client_id`, the three directory scopes and `has_credentials`, never the key). The customer's Workspace admin
authorises that client id for those scopes under Google Admin → 보안 → API 제어 → 도메인 전체 위임; nothing in the
flow is interactive. The domain is unique across companies (`UNIQ#WSDOMAIN#`). Connecting also gives the company the
platform-Google provider when it has none (`SsoService.ensure_platform_provider`), so employees' connector logins work
at once. Directory access is a JWT bearer grant (RS256 assertion signed with the key, `sub` = the admin,
`aud` = `GOOGLE_TOKEN_URL` — the setting, never the key file's `token_uri`, so an uploaded file cannot point the backend
at an arbitrary URL). `invalid_grant` / `unauthorized_client` / 401 from the API set `needs_reconnect` with the reason
in `sync_error`; the operator fixes the delegation or replaces the key (a re-PUT without `service_account_json` keeps
the stored key) and may retry. `DELETE` wipes the key and frees the domain; synced records stay.

**Directory sync (`services/workspace.py`, `core/google_directory.py`).** A sync — right after the key is
registered (`WORKSPACE_SYNC_ON_CONNECT`), on demand (`POST /companies/{cid}/workspace/sync`) and every
`WORKSPACE_SYNC_INTERVAL_S` — lists the Workspace users and groups of the connection's domain and mirrors them: users
become members (`source: workspace`, `external_id` = Google user id, which equals the OIDC `sub`, so the identity is
linked up front), groups become `Team`s (deterministic id from the group id) with `TeamMembership` rows (gsi2 `USER#uid`
for "teams of a user", what access policies resolve from).
Coexistence rules: the sync only creates/updates `workspace` records; a `manual` user matched by email just gains its
directory id and identity. Status flows one way — suspended or vanished in the directory ⇒ suspended here with
`suspended_by_sync`, and only such users are reactivated by the sync; an operator's suspension is never undone. Synced
memberships that vanished are removed, manual ones stay; a vanished group suspends its team. These three entities have no
Redis shape (`project()` → None): nothing on the MCP path reads them.

**Access policies (`services/policies.py`).** An `AccessPolicy` grants a team — or everyone in the company
(`team_id` null) — permissions and tools on a server the company is bound to (one policy per team/server, id derived
from the pair). `apply()` materialises policies into ordinary `ServerAccess` rows with `source: policy` and the
contributing `policy_ids`: per user and server the union of every covering policy. It runs after each directory sync,
after a policy change and on demand. The MCP path, resolver, access logs and every invariant keep reading
`ServerAccess` only, so policies never bypass them. Manual rows win: a manual `ServerAccess` on the same (user, server)
is left alone, and an operator editing a policy row through the API/console turns it into a manual row. A user no
longer covered keeps a suspended policy row (reactivated when covered again); suspended users and inactive bindings are
skipped. With Workspace connected, the operator's remaining work is: register datasources, bind them, write a handful of
team policies — new employees get their access on the next sync and their first connector login already works. The
console reflects that: an onboarding checklist on the company overview (Workspace → bindings → policies → employee
connection), read-only teams, a policies tab, source badges on users and access rows (an edit of a policy row warns
that it becomes manual), and a hint on the API-key tab that connector logins issue `sso:*` keys by themselves.
Outside dev the issuer and the directory/token URLs must be https; the dev/e2e stacks use a mock provider (`mock-idp`,
`ghcr.io/navikt/mock-oauth2-server`), which has no Directory API, so a dev sync records an error.

## Charts (`mcpcluster_common/chart.py`)
`chart` (read) takes a whitelisted pydantic spec (bar/line/area/scatter/histogram/heatmap, x/y/color fields, title,
sort, size) — never raw Vega-Lite. The server builds the Vega-Lite spec with **inline data only**, re-checks it for
external references (`url`, `href`, `transform`, `expr`, …) and renders PNG in-process with `vl-convert` (no browser;
`allowed_base_urls=[]`). This matters because vl-convert still loads `file://` data URLs and image hrefs even with
an empty allow-list. Rows are plotted as given (aggregate with compute/transform first); histograms are counted by
the Rust kernel. Input is `source` (+ pipeline) or an `artifact_id` (table rows, compute rows, histogram); max 5000
points. The result is an MCP image block + JSON; `save=true` (export) stores PNG, spec and rows as a chart artifact.
Images ship `fonts-nanum` so Korean text renders.

## Reports (`mcpcluster_common/report.py`)
`report` (permission **export**) turns an outline into docx (python-docx), pptx (python-pptx) or pdf (typst). The
outline is a whitelisted pydantic model: text blocks plus kpi / table / chart blocks that only **reference artifacts**
— there is no field for a number. Every referenced artifact goes through `ArtifactStore.get` (owner, hash, expiry,
source server still readable), so one report can combine results saved on different servers/engines by the same user.
The server only formats for display (thousands separators, 4 decimals); an appendix lists each artifact's server,
time and source query. Typst receives the content as `data.json` read by a server-owned template, so untrusted strings
render as text and are never evaluated; the compile root and package cache are private temp dirs. Delivery reuses the
export path (inline ≤ 2 MB, else presigned link under `mcp/reports/`). Theme = FTG design tokens, font NanumGothic,
**per-company branding** (`PUT /companies/{cid}/branding`: #RRGGBB colour, PNG/JPEG logo ≤ 200 KB, footer; company admins
may set their own). It is projected to `branding:{cid}` — not into `company:` so authorization reads stay small — and the
report reads only the caller's company. The renderer re-opens the logo with Pillow (pixel cap, normalised to PNG) and
uses a light brand colour for accents only (text falls back to FTG green below 3:1 on white). Charts keep the validated
FTG palette.

## Monitors (`mcpcluster_common/monitor.py`, `notifiers.py`)
`monitor_create` saves {source, pipeline, compute op, condition {path, op, threshold}, interval} on S3 under the owner
(`control/monitors/server_type=…/tenant=…/user=…/mon_….json`; last 50 events in `control/monitor_state/…`) and runs it once inline.
Each MCP server runs a scheduler (`MONITOR_TICK_S`, started from the server lifespan) over its own server type; a Redis
`SET NX` lock per monitor keeps replicas from double-running. A monitor runs **as the key that created it**: the
definition stores the key's sha256, and every run calls `Resolver.resolve_hash` for the monitor's server — key,
company, user, server, binding and access active, `read` granted and `monitor_create` still allowed — otherwise the
run is recorded as `skipped` with the reason. Runs count against the key's rate limit, appear in the access log as
`monitor:<id>`, re-read the definition under the lock (a concurrent delete/pause wins), and are capped per user
(`MONITOR_MAX_PER_USER`, 20 — enforced under a per-user create lock) and in rows (`MONITOR_MAX_ROWS`, 100k). Each tick
runs due monitors with bounded concurrency (`MONITOR_CONCURRENCY`, 8), a per-run timeout (`MONITOR_RUN_TIMEOUT_S`, 30 s)
and round-robin order across companies. Runs go through the same guarded `fetch_source`, pipeline and Rust
kernels as `compute`. Events are appended to S3, published to Redis stream `monitor:events`, and on breach handed to
`notifiers.dispatch` — a port with no registered channel yet (a channel is added by registering a `Notifier`; channel
names in a monitor are validated against the registry). Minimum interval: `MONITOR_MIN_INTERVAL_S` (60 in prod, 2 in
dev compose).

## Artifacts (`mcpcluster_common/artifacts.py`, `storage.py`)
Stored results are the hand-off between servers (servers never call each other). `compute` / `transform` with
`save=true` (requires the **export** permission — data leaves the request into shared storage) write
`mcp/artifacts/tenant={company}/user={user}/art_<32 hex>.json` (write-once) to the same S3 bucket as exports, with owner/server/sha256/expiry in
object metadata. `list_artifacts` / `get_artifact` (read) return them. Ownership is company → user, and every read
re-proves: path pinned to the caller, metadata company/user = caller, body hash matches, not expired, and the caller
may still read the **source server** (`Resolver.server_readable`: in key scope, server/binding/access active, `read`).
Artifacts carry `sources` (every server the data came from; a chart of another server's artifact inherits its
sources) in both metadata and the hashed body, and every source is re-authorized — `read` to view, `export` to copy
into a file or a new artifact (report, save). Compute results are PII-masked (`redact_value`) before being returned
or saved. Every failure is the same "artifact not found". Bucket lifecycle expires `mcp/artifacts/` after `ARTIFACT_TTL_DAYS` (7);
bodies are capped at `ARTIFACT_MAX_BYTES` (5 MB). Listing examines at most 500 objects per call (HEADs run 16 at a time);
beyond that the sample is by key order, not recency. Metadata lives only in S3 (no DynamoDB record) so the MCP side
stays Redis-only.

## MCP request path (`libs/common`)
`TenantAuthMiddleware` on every request: header `Authorization: Bearer <key>` (or `X-API-Key`) → sha256 →
`apikey:*` → **target server** = `?server=<server_id>` in the MCP URL, else the process pin (`SERVER_ID`), else
the key's scope when it names exactly one server — otherwise deny (never guess) → server id format check →
scope check → one pipeline company/user/server/binding/access → **deny unless all six are active, key not
expired and server_type consistent with the endpoint**. Permissions/tools come from the access record.
The URL parameter is used because every MCP client lets users set the URL, not all allow custom headers; it is
an identifier, not a credential. `list_tools` is filtered to `allowed_tools ∩ permitted`; `call_tool` re-checks the
tool's required permission, binds a `TenantContext` (contextvar) for the duration of the call, and publishes
an access event (ok / denied / error, duration, args summary) to Redis stream `logs:access`. The backend
consumes the stream into `mcp_cluster_logs` (TTL 90d) queryable per key / per company, and into the S3 audit lake
`audit/access_logs/tenant=…/dt=…/*.parquet` (append-only, kept 365 days — the >= 1 year access record; the backend role
may only PutObject there). A batch is acked only after both writes.

Connections are singletons per `(company, server)` and are rebuilt when the binding version changes.

**Rate limiting** (`mcpcluster_common/ratelimit.py`): after authz, each `call_tool` increments a fixed
one-minute window counter `rl:{key_id}:{minute}` in Redis db2 (single Lua round trip). The limit is the
key's `rate_limit_per_minute` or the server default (`RATE_LIMIT_PER_MINUTE`, 600; 0 disables). Exceeding
it returns JSON-RPC error -32002 with `retry_after_s` and is logged as `denied`. The limiter is fail-open on
Redis errors because authz (db1) already fails closed in that case.

### mcp-postgres / mcp-hacker SQL guard (`libs/common/mcpcluster_common/postgres/sqlguard.py`)
sqlglot-parsed, single statement, kind must match the tool, no nested writes, no DDL/SET, denylisted
functions, no `pg_catalog`/`information_schema`, and in schema mode every table reference is pinned to the
tenant schema (unqualified → qualified, foreign schema → rejected). SELECTs run in a read-only transaction
with a statement timeout and row cap. mcp-hacker reuses the same guard and pool factory with a read-only
catalog (`list_tables`, `describe_table`, `run_select`) and `default_transaction_read_only=on` on every
connection, so even a write smuggled through a function call (e.g. `setval`) is refused by Postgres.

### mcp-clickhouse SQL guard (`mcp-clickhouse/mcp_clickhouse/sqlguard.py`)
SELECT only; no SETTINGS / FORMAT / INTO OUTFILE; table functions (`url`, `s3`, `remote`, `mysql`, …) and
`dictGet*` denied; `system` / `information_schema` denied. **Column mode** rewrites every real table
reference (CTE names excluded) into `(SELECT * FROM db.t WHERE <col> = '<value>') AS alias`, so joins,
subqueries, `IN (...)` and CTE bodies are all filtered — there is no code path that runs an unfiltered
table. `list_tables` only shows tables that carry the tenant column; tables without it fail closed.
**Database mode** pins every table to the tenant database. Column mode may pin its own `database`
(binding field) — otherwise the DSN/env default is used. Queries run with `readonly=1`,
`max_result_rows`, `max_execution_time`, `allow_ddl=0`. `aggregate` pulls the tenant-scoped rows as Arrow
and groups them in DuckDB with a whitelisted metric grammar (`sum(col)`, `count(*)`, `count_distinct(col)`, …).

### mcp-mongo guard (`mcp-mongo/mcp_mongo/guard.py`)
Isolation is the connection: every op runs on `client[tenant.database]` and find/aggregate cannot read other
databases. The guard blocks what could escape or execute code: `$where`, `$function`, `$accumulator`
(server-side JS), aggregation stages `$out` / `$merge` (cross-database writes), `$currentOp`,
`$listSessions`, `$collStats`, … ; `system.*` collections; full-document replacement in `update_many`
(operators only); empty filters on update/delete unless `allow_empty_filter=true`. Inputs accept Extended
JSON (`{"$oid": …}`, `{"$date": …}`) and outputs use relaxed Extended JSON. Row caps and `maxTimeMS` apply.

## Metrics (`mcpcluster_common/metrics.py`)
Every MCP server and the gateway expose Prometheus `/metrics`: `mcp_tool_calls_total{server_type,tool,status}`,
`mcp_tool_duration_seconds{server_type,tool}`, `mcp_monitor_runs_total{server_type,status}`,
`mcp_gateway_list_cache_total{result}`. Labels never carry company/user/key ids (cardinality, and a scrape must not
reveal which customers are active — that is what the access log is for); tool names outside the catalog collapse to
`other`. Outside `APP_ENV=dev` a `METRICS_TOKEN` is mandatory (bearer, constant-time compare) and startup fails without it.

## Console dashboards (`backend/app/services/{stats,metrics,health}.py`, `infra/s3/artifacts.py`)
Read-only data for the console tiles, server cards, the ops event feed and the artifact tab. None of it feeds
authorization; the MCP path is unchanged.
- **Counts** — global (`GET /ops/stats`, master): one entity scan folded into counts by status, active bindings per
  server and keys expiring within 30 days, cached in Redis db2 (`stats:global`) and refreshed every `STATS_REFRESH_S`
  (300) or on `?refresh=1`. Company (`GET /companies/{cid}/stats`): one partition query per request, live.
- **24h metrics** — the access-log consumer, after the durable writes (S3 lake + DynamoDB) are acknowledged, adds each
  event to hourly Redis hashes `m:all:{h}`, `m:srv:{sid}:{h}`, `m:cmp:{cid}:{h}` (n/ok/denied/error, duration sum and
  a fixed latency histogram; TTL 48h) and pushes a trimmed copy onto the capped list `events:recent` (500, master feed).
  `GET /servers/{sid}/metrics`, `/companies/{cid}/metrics` sum the last 24 buckets; p50/p99 are histogram upper bounds.
  Best-effort: a crash between ack and record replays the batch and double-counts it. The audit lake is the record.
- **Health** — `HealthChecker` GETs `<internal url>/healthz` for each configured `MCP_INTERNAL_<TYPE>` / `_GATEWAY`
  every `HEALTH_CHECK_INTERVAL_S` (60) and stores ok/latency/checked_at/error in `health:<kind>` (TTL 10 min). The
  targets come from env only — the server record's public `endpoint` is admin-editable and is never fetched (SSRF).
  Every MCP server and the gateway serve an open `/healthz` (`{"ok": true}`, no version/host details) next to the
  token-protected `/metrics`. `ServerOut.health` and `GET /ops/health` expose the snapshot.
- **Artifact index** — `GET /companies/{cid}/artifacts` lists `mcp/artifacts/tenant={cid}/…` and HEADs the newest
  objects for their metadata (kind, title, server, sources, user, bytes, created/expires). No payload and no
  presigned URL: data leaves only through the MCP `export` path. The backend role gets ListBucket (prefix
  `mcp/artifacts/`) + GetObject there; `audit/` stays PutObject-only (`backend/tests/test_iac_contract.py`).
- Key expiry extension is the existing `PATCH …/api-keys/{id}` with a new `expires_at` (console "30일 연장").

## Tracing (`mcpcluster_common/tracing.py`)
FastMCP emits client/server spans and propagates W3C trace context through MCP `_meta`, so a gateway call and the
upstream server's handling form one trace. `setup_tracing()` installs the OpenTelemetry SDK + OTLP/HTTP exporter only
when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (otherwise a no-op). Spans carry method and tool names only — no arguments,
SQL, rows or company/user/key ids (asserted in `tests/test_tracing.py`). Caveat: the gateway's tool names contain the
server alias (derived from the server name), so name servers neutrally. No collector ships with the compose stack: point
`OTEL_EXPORTER_OTLP_ENDPOINT` (env/common.env) at any OTLP/HTTP collector reachable from the `mcp-cluster` network.

## Rust core (`libs/rust-core`, PyO3 + rayon)
`mcpcluster_core` holds the CPU-bound helpers: batched PII redaction (email, Korean phone, Luhn-valid card
numbers, resident registration numbers) and batched SHA-256, parallelised with rayon and released from the
GIL. `mcpcluster_common/postprocess.py` calls it when installed and otherwise falls back to a pure-Python
implementation with identical semantics (`tests/test_postprocess.py` asserts parity inside the container).
Every server's result rows pass through `redact_rows()`; `REDACT_PII=1` (compose default) turns it on.
The wheel is built in a `ghcr.io/pyo3/maturin` stage of each server Dockerfile — no Rust toolchain needed
on the host.

## Roles
* master — system company only; manages companies, servers, bindings, anything.
* admin — own company: users, server accesses (only to servers bound to the company), api-keys, read bindings/logs.
* member — key owner; no console login. SSO auto-provisioning creates members; admins stay operator-created (SSO only signs them in).

## Testing layers
* `make test` — backend integration (aws-local DynamoDB + Redis) and MCP unit suites (SQL/pipeline guards, catalog parity).
* `make e2e` / `make e2e-ui` run in a throwaway compose project (`scripts/e2e.sh`, `deploy/docker/docker-compose.e2e.yml`: its own Redis, DBs,
  in-memory DynamoDB Local and MinIO on private networks, host ports +10000) that is removed with its volumes afterwards, so
  nothing accumulates in the dev stack. It must not join the shared `aws-local` network: service names would resolve to the
  dev stack's containers too.
* `make e2e` — every MCP server through the real stack: tenant isolation, permission filtering, suspend propagation, rate limit, redaction, access logs.
* `make e2e-ui` — Playwright (`frontend/e2e`) drives the admin console in a container: login, company → user → binding → API key (copy-once banner), suspend/reactivate, admin scoping; each step is cross-checked against the API.
* `make rust-parity` — Rust wheel vs Python fallback inside the image.

## Planned (decided 2026-09-24, not implemented — see [PROJECT.md](PROJECT.md))
* **chart / report** are shared tools in `mcpcluster_common` (no extra server): Vega-Lite + `vl-convert`,
  PDF via `typst`, DOCX `python-docx`, PPTX `python-pptx`. Writing artifacts or files requires `export`.

## Not yet
IaC: `infra/terraform` (DynamoDB, bucket, KMS, least-privilege IAM; MCP servers run with `S3_MANAGE_BUCKET=0`).

**Per-company datasource roles.** A binding may carry `credentials` {username, password} (Fernet-encrypted, masked in
responses): the MCP server then connects as that role instead of the server login — postgres (DSN, user/password/database
percent-encoded so metacharacters cannot change host or database), clickhouse (client params), mongo (URI credentials quoted; `authSource` passed as a client keyword, never in the URI, and database names may
not contain URI metacharacters).
A wrong password fails closed; there is no fallback to the server login. Recommended for external customers: a role
that can only read its own schema/database, so isolation also holds at the database level beneath the SQL guards.

Production hardening: CI is written (`.github/workflows/ci.yml` + `ci/aws-local.ci.yml`) but has not run on a remote runner yet.
