import { useQuery } from "@tanstack/react-query";
import { getSso } from "./api";

export const ssoKeys = {
  detail: (companyId: string) => ["sso", "detail", companyId] as const,
};

export function useSso(companyId: string) {
  return useQuery({
    queryKey: ssoKeys.detail(companyId),
    queryFn: () => getSso(companyId),
  });
}
