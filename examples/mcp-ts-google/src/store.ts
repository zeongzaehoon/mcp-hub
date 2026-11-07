/**
 * In-memory state for the study server. Everything here is per-process and lost on restart.
 * A real product keeps it in Redis/DynamoDB and encrypts refresh tokens at rest (KMS/Fernet).
 */
import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

const TTL_MS = { pending: 10 * 60_000, code: 60_000 };

export interface RegisteredClient {
  client_id: string;
  client_name?: string;
  redirect_uris: string[];
  token_endpoint_auth_method: "none";
  grant_types: string[];
  response_types: string[];
}

/** An /authorize request parked while the user is at Google. */
export interface PendingAuthorization {
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  state?: string;
  scope: string;
  resource?: string;
  expiresAt: number;
}

/** Authorization code issued by us after Google confirmed who the user is. */
export interface IssuedCode {
  sub: string;
  email: string;
  client_id: string;
  redirect_uri: string;
  code_challenge: string;
  scope: string;
  expiresAt: number;
}

const clients = new Map<string, RegisteredClient>();
const pending = new Map<string, PendingAuthorization>();
const codes = new Map<string, IssuedCode>();
const googleRefreshTokens = new Map<string, string>(); // sub -> refresh token

export function newId(bytes = 24): string {
  return randomBytes(bytes).toString("base64url");
}

function sweep<T extends { expiresAt: number }>(m: Map<string, T>) {
  const now = Date.now();
  for (const [k, v] of m) if (v.expiresAt < now) m.delete(k);
}

export const clientStore = {
  register(c: Omit<RegisteredClient, "client_id">): RegisteredClient {
    const client = { ...c, client_id: newId(16) };
    clients.set(client.client_id, client);
    return client;
  },
  get(id: string): RegisteredClient | undefined {
    return clients.get(id);
  },
};

export const pendingStore = {
  save(p: Omit<PendingAuthorization, "expiresAt">): string {
    sweep(pending);
    const id = newId();
    pending.set(id, { ...p, expiresAt: Date.now() + TTL_MS.pending });
    return id;
  },
  take(id: string): PendingAuthorization | undefined {
    const p = pending.get(id);
    pending.delete(id);
    return p && p.expiresAt > Date.now() ? p : undefined;
  },
};

export const codeStore = {
  issue(c: Omit<IssuedCode, "expiresAt">): string {
    sweep(codes);
    const code = newId(32);
    codes.set(code, { ...c, expiresAt: Date.now() + TTL_MS.code });
    return code;
  },
  /** Single use. Returns undefined when unknown/expired, or when PKCE / client / redirect do not match. */
  redeem(code: string, verifier: string, clientId: string, redirectUri: string): IssuedCode | undefined {
    const c = codes.get(code);
    codes.delete(code);
    if (!c || c.expiresAt < Date.now()) return undefined;
    if (c.client_id !== clientId || c.redirect_uri !== redirectUri) return undefined;
    const expected = createHash("sha256").update(verifier).digest("base64url");
    const a = Buffer.from(expected), b = Buffer.from(c.code_challenge);
    if (a.length !== b.length || !timingSafeEqual(a, b)) return undefined;
    return c;
  },
};

export const googleTokenStore = {
  save(sub: string, refreshToken: string) {
    googleRefreshTokens.set(sub, refreshToken);
  },
  get(sub: string): string | undefined {
    return googleRefreshTokens.get(sub);
  },
};
