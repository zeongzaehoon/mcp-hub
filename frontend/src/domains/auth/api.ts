import { api } from "@/lib/api";
import type { User } from "@/domains/users/types";
import type { LoginIn, LoginOut, SsoOptions } from "./types";

export async function login(body: LoginIn): Promise<LoginOut> {
  const { data } = await api.post<LoginOut>("/auth/login", body);
  return data;
}

export async function me(): Promise<User> {
  const { data } = await api.get<User>("/auth/me");
  return data;
}

/** Public: which platform providers this deployment offers (the SSO card's "플랫폼 Google" option). */
export async function getSsoOptions(): Promise<SsoOptions> {
  const { data } = await api.get<SsoOptions>("/auth/sso/options");
  return data;
}
