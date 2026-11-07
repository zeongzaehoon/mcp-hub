/**
 * HTTP layer. Three roles on one Express app:
 *   1. OAuth 2.1 Authorization Server for MCP clients (DCR + PKCE), with Google as the login screen
 *   2. OAuth Resource Server: /mcp accepts only the JWTs issued by (1)
 *   3. The MCP endpoint itself (Streamable HTTP, stateless per request)
 */
import {
  createMcpExpressApp,
  getOAuthProtectedResourceMetadataUrl,
  mcpAuthMetadataRouter,
  requireBearerAuth,
} from "@modelcontextprotocol/express";
import { toNodeHandler } from "@modelcontextprotocol/node";
import {
  createMcpHandler,
  OAuthError,
  OAuthErrorCode,
  type AuthInfo,
  type OAuthMetadata,
} from "@modelcontextprotocol/server";
import express from "express";
import { jwtVerify, SignJWT } from "jose";
import { googleClient } from "./google.js";
import { buildServer } from "./server.js";
import { clientStore, codeStore, pendingStore } from "./store.js";

// ---- config -------------------------------------------------------------------------------------
function need(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`missing env ${name}`);
  return v;
}
const MCP_URL = new URL(need("MCP_URL"));           // e.g. http://localhost:9100/mcp
const ISSUER = MCP_URL.origin;
const HOST = process.env.HOST ?? "127.0.0.1";
const PORT = Number(process.env.PORT ?? 9100);
const JWT_KEY = new TextEncoder().encode(need("JWT_SECRET"));
const ACCESS_TTL_S = 3600;
const SCOPE = "mcp";

const google = googleClient({
  clientId: need("GOOGLE_CLIENT_ID"),
  clientSecret: need("GOOGLE_CLIENT_SECRET"),
  redirectUri: `${ISSUER}/oauth/google/callback`,
});

// ---- app ----------------------------------------------------------------------------------------
const app = createMcpExpressApp({ host: HOST, allowedHosts: [MCP_URL.hostname, "localhost", "127.0.0.1"] });
app.use(express.urlencoded({ extended: false })); // /token is form-encoded per RFC 6749

// 1) Discovery documents: tell clients that THIS origin is the AS and what it supports.
const oauthMetadata: OAuthMetadata = {
  issuer: ISSUER,
  authorization_endpoint: `${ISSUER}/authorize`,
  token_endpoint: `${ISSUER}/token`,
  registration_endpoint: `${ISSUER}/register`,
  response_types_supported: ["code"],
  grant_types_supported: ["authorization_code"],
  code_challenge_methods_supported: ["S256"],
  token_endpoint_auth_methods_supported: ["none"],
  scopes_supported: [SCOPE],
};
app.use(mcpAuthMetadataRouter({ oauthMetadata, resourceServerUrl: MCP_URL, scopesSupported: [SCOPE] }));

// 2a) Dynamic Client Registration (RFC 7591). Public clients only; redirect URIs restricted to
//     loopback http or https so a registered client cannot bounce codes to an arbitrary site.
function redirectAllowed(u: string): boolean {
  try {
    const url = new URL(u);
    if (url.protocol === "https:") return true;
    return url.protocol === "http:" && (url.hostname === "localhost" || url.hostname === "127.0.0.1");
  } catch {
    return false;
  }
}
app.post("/register", express.json(), (req, res) => {
  const uris: unknown = req.body?.redirect_uris;
  if (!Array.isArray(uris) || uris.length === 0 || !uris.every((u) => typeof u === "string" && redirectAllowed(u))) {
    res.status(400).json({ error: "invalid_redirect_uri" });
    return;
  }
  const client = clientStore.register({
    client_name: typeof req.body?.client_name === "string" ? req.body.client_name : undefined,
    redirect_uris: uris,
    token_endpoint_auth_method: "none",
    grant_types: ["authorization_code"],
    response_types: ["code"],
  });
  res.status(201).json(client);
});

// 2b) /authorize: validate the client, park the request, send the browser to Google.
app.get("/authorize", (req, res) => {
  const q = req.query as Record<string, string | undefined>;
  const client = q.client_id ? clientStore.get(q.client_id) : undefined;
  if (!client || !q.redirect_uri || !client.redirect_uris.includes(q.redirect_uri)) {
    res.status(400).send("unknown client or redirect_uri"); // never redirect on these: RFC 6749 §4.1.2.1
    return;
  }
  const fail = (error: string) => res.redirect(`${q.redirect_uri}?error=${error}${q.state ? `&state=${encodeURIComponent(q.state)}` : ""}`);
  if (q.response_type !== "code") return fail("unsupported_response_type");
  if (!q.code_challenge || q.code_challenge_method !== "S256") return fail("invalid_request");
  if (q.resource && q.resource !== MCP_URL.href) return fail("invalid_target");

  const id = pendingStore.save({
    client_id: client.client_id,
    redirect_uri: q.redirect_uri,
    code_challenge: q.code_challenge,
    state: q.state,
    scope: SCOPE,
    resource: q.resource,
  });
  res.redirect(google.loginUrl(id));
});

// 2c) Google sends the user back here. Mint OUR authorization code and return to the MCP client.
app.get("/oauth/google/callback", async (req, res) => {
  const { code, state, error } = req.query as Record<string, string | undefined>;
  const p = state ? pendingStore.take(state) : undefined;
  if (!p) {
    res.status(400).send("login session expired or unknown");
    return;
  }
  const back = (params: Record<string, string>) => {
    const u = new URL(p.redirect_uri);
    for (const [k, v] of Object.entries(params)) u.searchParams.set(k, v);
    if (p.state) u.searchParams.set("state", p.state);
    res.redirect(u.href);
  };
  if (error || !code) return back({ error: "access_denied" });
  try {
    const { sub, email } = await google.completeLogin(code);
    const ours = codeStore.issue({ sub, email, client_id: p.client_id, redirect_uri: p.redirect_uri, code_challenge: p.code_challenge, scope: p.scope });
    back({ code: ours });
  } catch (e) {
    console.error("google login failed:", e instanceof Error ? e.message : e);
    back({ error: "server_error" });
  }
});

// 2d) /token: code + PKCE verifier -> our JWT. Audience pinned to this server (RFC 8707).
app.post("/token", async (req, res) => {
  const b = req.body as Record<string, string | undefined>;
  if (b.grant_type !== "authorization_code") {
    res.status(400).json({ error: "unsupported_grant_type" });
    return;
  }
  if (!b.code || !b.code_verifier || !b.client_id || !b.redirect_uri) {
    res.status(400).json({ error: "invalid_request" });
    return;
  }
  const grant = codeStore.redeem(b.code, b.code_verifier, b.client_id, b.redirect_uri);
  if (!grant) {
    res.status(400).json({ error: "invalid_grant" });
    return;
  }
  const access_token = await new SignJWT({ email: grant.email, scope: grant.scope, client_id: grant.client_id })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(grant.sub)
    .setIssuer(ISSUER)
    .setAudience(MCP_URL.href)
    .setIssuedAt()
    .setExpirationTime(`${ACCESS_TTL_S}s`)
    .sign(JWT_KEY);
  res.json({ access_token, token_type: "Bearer", expires_in: ACCESS_TTL_S, scope: grant.scope });
});

// 3) Resource server: verify our JWT, hand identity to tools as authInfo.extra.
const auth = requireBearerAuth({
  verifier: {
    async verifyAccessToken(token): Promise<AuthInfo> {
      try {
        const { payload } = await jwtVerify(token, JWT_KEY, { issuer: ISSUER, audience: MCP_URL.href, algorithms: ["HS256"] });
        return {
          token,
          clientId: String(payload.client_id ?? ""),
          scopes: String(payload.scope ?? "").split(" ").filter(Boolean),
          expiresAt: payload.exp,
          resource: MCP_URL,
          extra: { sub: payload.sub, email: payload.email },
        };
      } catch {
        throw new OAuthError(OAuthErrorCode.InvalidToken, "invalid or expired token");
      }
    },
  },
  requiredScopes: [SCOPE],
  resourceMetadataUrl: getOAuthProtectedResourceMetadataUrl(MCP_URL),
  expectedResource: MCP_URL,
});

const mcp = toNodeHandler(createMcpHandler(() => buildServer(google)));
app.all(MCP_URL.pathname, auth, (req, res) => void mcp(req, res, req.body));

app.get("/healthz", (_req, res) => res.json({ ok: true }));

app.listen(PORT, HOST, () => console.log(`mcp-ts-google-example on http://${HOST}:${PORT}${MCP_URL.pathname} (issuer ${ISSUER})`));
