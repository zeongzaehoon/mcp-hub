import { useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useLogin } from "@/domains/auth/mutations";
import { LoginPresenter } from "./LoginPresenter";

export function LoginContainer() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useLogin();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!email || !password || login.isPending) return;
    login.mutate(
      { email: email.trim(), password },
      {
        onSuccess: () => navigate(from && from !== "/login" ? from : "/companies", { replace: true }),
      },
    );
  };

  return (
    <LoginPresenter
      email={email}
      password={password}
      onEmailChange={setEmail}
      onPasswordChange={setPassword}
      onSubmit={onSubmit}
      isPending={login.isPending}
      error={login.error}
    />
  );
}
