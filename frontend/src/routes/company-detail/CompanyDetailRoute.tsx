import { Navigate, useParams } from "react-router-dom";
import { CompanyDetailContainer } from "./CompanyDetailContainer";

export function CompanyDetailRoute() {
  const { companyId } = useParams<{ companyId: string }>();
  if (!companyId) return <Navigate to="/companies" replace />;
  return <CompanyDetailContainer key={companyId} companyId={companyId} />;
}
