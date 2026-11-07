import axios, { AxiosError } from "axios";
import { useAuthStore } from "@/stores/auth";

export const API_BASE: string = import.meta.env.VITE_API_BASE ?? "http://localhost:8080";
export const API_PREFIX = "/api/v1";

export interface ApiError {
  code: string;
  message: string;
}

interface FastApiValidationError {
  detail?: Array<{ loc?: Array<string | number>; msg?: string }> | string;
}

export const api = axios.create({
  baseURL: `${API_BASE}${API_PREFIX}`,
  timeout: 30_000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`);
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      const isLoginRequest = error.config?.url?.includes("/auth/login") ?? false;
      if (!isLoginRequest) {
        useAuthStore.getState().logout();
        if (window.location.pathname !== "/login") {
          window.location.assign("/login");
        }
      }
    }
    return Promise.reject(error);
  },
);

export function isForbidden(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 403;
}

export function isNotFound(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 404;
}

/** Extracts a human readable message from a backend `{code, message}` error or a FastAPI 422. */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as (Partial<ApiError> & FastApiValidationError) | undefined;
    if (data) {
      if (typeof data.message === "string" && data.message) return data.message;
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail)) {
        const parts = data.detail.map((d) => {
          const loc = d.loc?.filter((l) => l !== "body").join(".") ?? "";
          return loc ? `${loc}: ${d.msg ?? ""}` : (d.msg ?? "");
        });
        if (parts.length) return parts.join(", ");
      }
    }
    if (error.response?.status) return `요청 실패 (HTTP ${error.response.status})`;
    if (error.code === "ECONNABORTED") return "요청 시간이 초과되었습니다";
    return "서버에 연결할 수 없습니다";
  }
  if (error instanceof Error) return error.message;
  return "알 수 없는 오류가 발생했습니다";
}
