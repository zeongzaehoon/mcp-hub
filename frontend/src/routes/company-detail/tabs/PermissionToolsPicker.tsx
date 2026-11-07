import { useEffect, useMemo } from "react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { FieldHint, Label } from "@/components/ui/Label";
import { Spinner } from "@/components/ui/Spinner";
import { PERMISSION_LABEL, PERMISSION_VALUES, type Permission, type ServerType } from "@/domains/common/enums";
import { useToolCatalog } from "@/domains/servers/queries";
import type { Server } from "@/domains/servers/types";

export function sameSet<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a.length !== b.length) return false;
  const sb = new Set(b);
  return a.every((x) => sb.has(x));
}

export type ToolMap = Record<string, Permission>;

/** tool -> required permission, from the server object, else from the static catalog by server_type. */
export function useToolMap(server: Server | undefined, serverType: ServerType | undefined) {
  const catalog = useToolCatalog();
  const toolMap: ToolMap = useMemo(() => {
    if (server) return server.tools;
    if (serverType && catalog.data) return catalog.data[serverType] ?? {};
    return {};
  }, [server, serverType, catalog.data]);
  const toolEntries = useMemo(() => Object.entries(toolMap).sort(([a], [b]) => a.localeCompare(b)), [toolMap]);
  return { toolMap, toolEntries, catalogPending: catalog.isPending };
}

/** Mirrors the backend rule: every allowed tool must be covered by a granted permission. */
export function toolsCovered(tools: readonly string[], permissions: readonly Permission[], toolMap: ToolMap): boolean {
  return tools.every((t) => toolMap[t] !== undefined && permissions.includes(toolMap[t]!));
}

interface PermissionToolsPickerProps {
  serverType: ServerType | undefined;
  toolMap: ToolMap;
  toolEntries: [string, Permission][];
  catalogPending: boolean;
  permissions: Permission[];
  tools: string[];
  onPermissionsChange: (next: Permission[]) => void;
  onToolsChange: (next: string[]) => void;
}

/** Permissions + allowed tools pickers shared by the user access form and the team policy form. */
export function PermissionToolsPicker(p: PermissionToolsPickerProps) {
  const { toolMap, toolEntries, permissions, tools, onToolsChange, onPermissionsChange } = p;

  // when the server (and thus the catalog) changes, drop tools no longer in it.
  // Skipped while the catalog is unknown (empty) so an in-flight load never wipes the selection.
  useEffect(() => {
    if (Object.keys(toolMap).length === 0) return;
    const kept = tools.filter((t) => t in toolMap);
    if (kept.length !== tools.length) onToolsChange(kept);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [toolMap]);

  const togglePermission = (perm: Permission, checked: boolean) => {
    onPermissionsChange(checked ? Array.from(new Set([...permissions, perm])) : permissions.filter((x) => x !== perm));
    // auto-remove tools that require the permission being revoked
    if (!checked) onToolsChange(tools.filter((t) => toolMap[t] !== perm));
  };

  const toggleTool = (tool: string, checked: boolean) => {
    onToolsChange(checked ? Array.from(new Set([...tools, tool])) : tools.filter((x) => x !== tool));
  };

  const selectAllTools = () => onToolsChange(toolEntries.filter(([, perm]) => permissions.includes(perm)).map(([t]) => t));

  return (
    <>
      <div className="space-y-2">
        <Label>권한</Label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {PERMISSION_VALUES.map((perm) => (
            <Checkbox
              key={perm}
              label={PERMISSION_LABEL[perm]}
              description={perm}
              checked={permissions.includes(perm)}
              onChange={(e) => togglePermission(perm, e.target.checked)}
            />
          ))}
        </div>
        {permissions.length === 0 ? <FieldHint className="text-destructive">최소 1개의 권한이 필요합니다</FieldHint> : null}
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label>허용 도구 {p.serverType ? <Badge variant="outline" className="ml-1">{p.serverType}</Badge> : null}</Label>
          {toolEntries.length > 0 ? (
            <span className="flex gap-2">
              <Button variant="ghost" size="sm" onClick={selectAllTools}>
                권한 내 전체 선택
              </Button>
              <Button variant="ghost" size="sm" onClick={() => onToolsChange([])}>
                해제
              </Button>
            </span>
          ) : null}
        </div>
        {!p.serverType ? (
          <FieldHint>서버를 선택하면 도구 목록이 표시됩니다.</FieldHint>
        ) : toolEntries.length === 0 ? (
          p.catalogPending ? (
            <Spinner className="h-4 w-4" />
          ) : (
            <FieldHint>이 서버 타입의 도구 카탈로그를 찾을 수 없습니다.</FieldHint>
          )
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {toolEntries.map(([tool, perm]) => {
              const allowed = permissions.includes(perm);
              return (
                <Checkbox
                  key={tool}
                  label={<span className="font-mono text-xs">{tool}</span>}
                  description={allowed ? `${PERMISSION_LABEL[perm]} 권한` : `${PERMISSION_LABEL[perm]}(${perm}) 권한을 먼저 체크하세요`}
                  checked={tools.includes(tool)}
                  disabled={!allowed}
                  onChange={(e) => toggleTool(tool, e.target.checked)}
                />
              );
            })}
          </div>
        )}
        {p.serverType && tools.length === 0 ? <FieldHint className="text-destructive">최소 1개의 도구를 선택하세요</FieldHint> : null}
      </div>
    </>
  );
}
