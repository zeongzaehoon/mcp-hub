import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { RequireAuth } from "@/components/layout/RequireAuth";
import { LoginRoute } from "@/routes/login/LoginRoute";
import { CompaniesRoute } from "@/routes/companies/CompaniesRoute";
import { CompanyDetailRoute } from "@/routes/company-detail/CompanyDetailRoute";
import { ServersRoute } from "@/routes/servers/ServersRoute";
import { ServerDetailRoute } from "@/routes/server-detail/ServerDetailRoute";
import { OpsRoute } from "@/routes/ops/OpsRoute";

export const router = createBrowserRouter([
  { path: "/login", element: <LoginRoute /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/companies" replace /> },
      { path: "companies", element: <CompaniesRoute /> },
      { path: "companies/:companyId", element: <CompanyDetailRoute /> },
      { path: "servers", element: <ServersRoute /> },
      { path: "servers/:serverId", element: <ServerDetailRoute /> },
      { path: "ops", element: <OpsRoute /> },
    ],
  },
  { path: "*", element: <Navigate to="/companies" replace /> },
]);
