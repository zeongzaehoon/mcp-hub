import { RequireMaster } from "@/components/layout/RequireAuth";
import { useIsMaster } from "@/stores/auth";
import { OpsContainer } from "./OpsContainer";

export function OpsRoute() {
  const isMaster = useIsMaster();
  return (
    <RequireMaster isMaster={isMaster}>
      <OpsContainer />
    </RequireMaster>
  );
}
