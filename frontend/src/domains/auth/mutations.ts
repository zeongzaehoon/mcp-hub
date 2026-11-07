import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/auth";
import { login } from "./api";
import type { LoginIn } from "./types";

export function useLogin() {
  const qc = useQueryClient();
  const setSession = useAuthStore((s) => s.setSession);
  return useMutation({
    mutationFn: (body: LoginIn) => login(body),
    onSuccess: (out) => {
      qc.clear();
      setSession(out.access_token, out.user);
    },
  });
}
