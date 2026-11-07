import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User } from "@/domains/users/types";

interface AuthState {
  token: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setSession: (token, user) => set({ token, user }),
      logout: () => set({ token: null, user: null }),
    }),
    { name: "mcp-cluster-auth" },
  ),
);

export function useIsMaster(): boolean {
  return useAuthStore((s) => s.user?.role === "master");
}

export function useIsAuthenticated(): boolean {
  return useAuthStore((s) => s.token !== null);
}
