import { Navigate } from "react-router-dom";
import { useIsAuthenticated } from "@/stores/auth";
import { LoginContainer } from "./LoginContainer";

export function LoginRoute() {
  const authed = useIsAuthenticated();
  if (authed) return <Navigate to="/companies" replace />;
  return <LoginContainer />;
}
