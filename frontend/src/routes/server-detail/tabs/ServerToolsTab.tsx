import { useMemo } from "react";
import type { ColumnDef } from "@tanstack/react-table";
import { Code } from "@/components/ui/Code";
import { DataTable } from "@/components/shared/DataTable";
import { PermissionBadges } from "@/components/shared/PermissionBadges";
import type { Permission } from "@/domains/common/enums";
import type { Server } from "@/domains/servers/types";

interface ToolRow {
  tool: string;
  permission: Permission;
}

export function ServerToolsTab({ server }: { server: Server }) {
  const rows = useMemo<ToolRow[]>(
    () =>
      Object.entries(server.tools)
        .map(([tool, permission]) => ({ tool, permission }))
        .sort((a, b) => a.tool.localeCompare(b.tool)),
    [server.tools],
  );

  const columns = useMemo<ColumnDef<ToolRow>[]>(
    () => [
      { accessorKey: "tool", header: "도구", cell: ({ row }) => <Code>{row.original.tool}</Code> },
      {
        accessorKey: "permission",
        header: "필요 권한",
        size: 160,
        cell: ({ row }) => <PermissionBadges permissions={[row.original.permission]} />,
      },
    ],
    [],
  );

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        이 서버 타입이 제공하는 MCP 도구와 각 도구 호출에 필요한 권한입니다. 사용자 서버 접근의 allowed_tools는 이 목록의 부분집합이어야 합니다.
      </p>
      <DataTable data={rows} columns={columns} getRowId={(r) => r.tool} height={480} emptyMessage="도구가 없습니다" />
    </div>
  );
}
