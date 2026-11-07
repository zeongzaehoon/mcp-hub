import { Navigate, useParams } from "react-router-dom";
import { ServerDetailContainer } from "./ServerDetailContainer";

export function ServerDetailRoute() {
  const { serverId } = useParams<{ serverId: string }>();
  if (!serverId) return <Navigate to="/servers" replace />;
  return <ServerDetailContainer key={serverId} serverId={serverId} />;
}
