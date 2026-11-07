import type { User } from "@/domains/users/types";

export interface LoginIn {
  email: string;
  password: string;
}

export interface LoginOut {
  access_token: string;
  token_type: string;
  user: User;
}

/** `GET /auth/sso/options` (public) */
export interface SsoOptions {
  /** platform Google client configured on this deployment */
  google: boolean;
}
