import { useQuery } from "@tanstack/react-query";
import { getSsoOptions, me } from "./api";

export const authKeys = {
  me: ["auth", "me"] as const,
  ssoOptions: ["auth", "sso-options"] as const,
};

export function useMe(enabled: boolean) {
  return useQuery({ queryKey: authKeys.me, queryFn: me, enabled, staleTime: 60_000, retry: false });
}

/** Public; fetched once per session (the answer only changes with a redeploy). */
export function useSsoOptions() {
  return useQuery({ queryKey: authKeys.ssoOptions, queryFn: getSsoOptions, staleTime: Infinity, retry: false });
}
