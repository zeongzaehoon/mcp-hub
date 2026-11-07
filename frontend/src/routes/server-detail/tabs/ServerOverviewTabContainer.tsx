import { useEffect, useState } from "react";
import { usePatchServer } from "@/domains/servers/mutations";
import { useServerMetrics } from "@/domains/servers/queries";
import type { Server, ServerPatch } from "@/domains/servers/types";
import { TRANSITION_TARGET, type StatusTransition } from "@/components/shared/StatusActions";
import { ServerOverviewTabPresenter, type ServerEditForm } from "./ServerOverviewTabPresenter";

export function ServerOverviewTabContainer({ server, isMaster }: { server: Server; isMaster: boolean }) {
  const [form, setForm] = useState<ServerEditForm>({ name: server.name, description: server.description, endpoint: server.endpoint, dsn: "" });
  const [pendingTransition, setPendingTransition] = useState<StatusTransition | null>(null);
  const patch = usePatchServer(server.server_id);
  const metrics = useServerMetrics(server.server_id);

  useEffect(() => {
    setForm({ name: server.name, description: server.description, endpoint: server.endpoint, dsn: "" });
  }, [server.name, server.description, server.endpoint, server.has_connection]);

  const dirty =
    form.name.trim() !== server.name ||
    form.description.trim() !== server.description ||
    form.endpoint.trim() !== server.endpoint ||
    form.dsn.trim().length > 0;

  const save = () => {
    if (!dirty || form.name.trim().length === 0) return;
    const body: ServerPatch = {};
    if (form.name.trim() !== server.name) body.name = form.name.trim();
    if (form.description.trim() !== server.description) body.description = form.description.trim();
    if (form.endpoint.trim() !== server.endpoint) body.endpoint = form.endpoint.trim();
    if (form.dsn.trim()) body.dsn = form.dsn.trim();
    patch.mutate(body);
  };

  const clearConnection = () => patch.mutate({ clear_dsn: true });

  const confirmTransition = () => {
    if (!pendingTransition) return;
    patch.mutate({ status: TRANSITION_TARGET[pendingTransition] }, { onSuccess: () => setPendingTransition(null) });
  };

  return (
    <ServerOverviewTabPresenter
      server={server}
      isMaster={isMaster}
      metrics={metrics.data}
      metricsError={metrics.error}
      form={form}
      onFormChange={(patchForm) => setForm((f) => ({ ...f, ...patchForm }))}
      onClearConnection={clearConnection}
      onSave={save}
      dirty={dirty && form.name.trim().length > 0}
      isSaving={patch.isPending}
      error={patch.error}
      pendingTransition={pendingTransition}
      onRequestTransition={setPendingTransition}
      onCancelTransition={() => {
        setPendingTransition(null);
        patch.reset();
      }}
      onConfirmTransition={confirmTransition}
    />
  );
}
