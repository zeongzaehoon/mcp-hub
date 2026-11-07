# mcp-ts-google — TypeScript MCP 서버 스터디 예제

**스터디용이다.** 외부 고객사에 제공할 실제 TS MCP 서버는 별도 저장소에서 만들며, 이 예제는 클러스터 compose·nginx·권한 체계(mcpk 키, Redis 투영, tool_catalog)와 **연결되어 있지 않다**.
루트 CLAUDE.md의 불변식은 사내 클러스터에 대한 것이고, 이 디렉터리에는 적용되지 않는다.

공식 TypeScript SDK v2(`@modelcontextprotocol/server` · `/express` · `/node`)로 다음을 한 프로세스에 묶는다.

| 역할 | 경로 | 구현 |
|---|---|---|
| 인가 서버 (OAuth 2.1, MCP 클라이언트용) | `/register` (DCR) · `/authorize` · `/token` · `/.well-known/*` | `src/index.ts` — 동의 화면 대신 **Google 로그인**으로 사용자 확인 후 자체 JWT 발급 |
| 리소스 서버 | `/mcp` | `requireBearerAuth` 가 자체 JWT 검증 → `ctx.http.authInfo.extra` 에 `sub`·`email` |
| 도구 | `whoami` · `list_calendar_events` | `src/server.ts` — 저장된 Google refresh token으로 Calendar API 호출 |

```
Claude/Inspector ─GET /.well-known/oauth-protected-resource/mcp─▶ 이 서버
   │ POST /register (DCR)
   │ GET  /authorize?code_challenge=…  ──302──▶ accounts.google.com ──302──▶ /oauth/google/callback
   │                                              (Google code → id_token 검증 → refresh token 보관 → 우리 code 발급)
   │ POST /token (code + PKCE verifier) ──▶ 자체 JWT (aud = MCP_URL)
   └ POST /mcp  Authorization: Bearer <JWT> ──▶ tools/call → Google Calendar API
```

Google이 DCR을 지원하지 않아 Claude Desktop이 Google에 직접 붙을 수 없다. 그래서 SaaS 벤더가 흔히 하듯 **자체 인가 서버를 앞에 두고 Google은 로그인 화면으로만** 쓴다.

## 실행
```bash
cp .env.sample .env     # GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / JWT_SECRET 채우기
docker compose up -d --build
curl -s localhost:9100/.well-known/oauth-protected-resource/mcp
```
Google Cloud Console → APIs & Services → Credentials → OAuth client (Web application). 승인된 리디렉션 URI는 정확히 `http://localhost:9100/oauth/google/callback`. Calendar API를 프로젝트에서 활성화한다.

클라이언트 연결 (OAuth 흐름을 자동으로 밟는다):
```bash
claude mcp add --transport http ts-google http://localhost:9100/mcp
npx @modelcontextprotocol/inspector   # URL에 http://localhost:9100/mcp
```
호스트에 Node가 없으므로 개발 명령은 컨테이너로: `docker run --rm -v "$PWD":/app -w /app node:22-alpine npm run typecheck`.

## 일부러 단순화한 것 (실제 제품에서는 바꿔야 함)
- 저장소가 전부 메모리(`src/store.ts`). 재시작하면 등록 클라이언트·refresh token이 사라진다. 실제로는 Redis/DynamoDB + refresh token 암호화(KMS).
- JWT가 HS256 공유 비밀. 실제로는 비대칭 키 + JWKS 엔드포인트, 그래야 리소스 서버를 분리할 수 있다.
- refresh token 발급 없음(access 1시간). 로그인 상태 유지·토큰 폐기(`/revoke`)·클라이언트 정리 없음.
- `/register` rate limit 없음. 사용자별 Google 토큰 폐기(연결 해제) 화면 없음.

## 지킨 보안 사항
- `/authorize`에서 client_id·redirect_uri가 등록값과 다르면 **리다이렉트하지 않고** 400 (open redirect 방지).
- DCR redirect_uri는 https 또는 loopback http만. PKCE S256 필수, 코드는 1회용·60초, `timingSafeEqual` 비교.
- JWT `aud`를 `MCP_URL`로 고정하고 `expectedResource`로 재검증 (타 서버 토큰 재사용 방지).
- 도구는 `authInfo.extra.sub`가 없으면 거부, Google 토큰이 없으면 호출하지 않는다 (fail closed).
- 외부 API URL은 코드에 고정. 입력으로 URL을 받지 않는다 (SSRF 방지).
- 포트는 127.0.0.1:9100만 바인딩.
