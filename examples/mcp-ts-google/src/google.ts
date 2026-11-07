/** Google as the identity provider (login) and as an upstream API (Calendar). */
import { OAuth2Client } from "google-auth-library";
import { googleTokenStore } from "./store.js";

export const GOOGLE_SCOPES = [
  "openid",
  "email",
  "https://www.googleapis.com/auth/calendar.readonly",
];

export function googleClient(env: { clientId: string; clientSecret: string; redirectUri: string }) {
  const oauth = new OAuth2Client(env.clientId, env.clientSecret, env.redirectUri);

  return {
    /** Where to send the browser. `state` carries our pending-authorization id back. */
    loginUrl(state: string): string {
      return oauth.generateAuthUrl({
        access_type: "offline",
        prompt: "consent",
        scope: GOOGLE_SCOPES,
        state,
      });
    },

    /** Exchange Google's code; verify the ID token; keep the refresh token for later API calls. */
    async completeLogin(code: string): Promise<{ sub: string; email: string }> {
      const { tokens } = await oauth.getToken(code);
      if (!tokens.id_token) throw new Error("google: no id_token in token response");
      const ticket = await oauth.verifyIdToken({ idToken: tokens.id_token, audience: env.clientId });
      const payload = ticket.getPayload();
      if (!payload?.sub || !payload.email) throw new Error("google: id_token missing sub/email");
      if (tokens.refresh_token) googleTokenStore.save(payload.sub, tokens.refresh_token);
      return { sub: payload.sub, email: payload.email };
    },

    /** Fresh access token for this user, from the stored refresh token. Fail closed when none is stored. */
    async accessTokenFor(sub: string): Promise<string> {
      const refresh = googleTokenStore.get(sub);
      if (!refresh) throw new Error("no Google credentials for this user; sign in again");
      const c = new OAuth2Client(env.clientId, env.clientSecret);
      c.setCredentials({ refresh_token: refresh });
      const { token } = await c.getAccessToken();
      if (!token) throw new Error("google: could not refresh access token");
      return token;
    },
  };
}

export type GoogleClient = ReturnType<typeof googleClient>;
