import { useMemo } from "react";
import { Badge } from "@/components/ui/Badge";
import { Code } from "@/components/ui/Code";
import { Dialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/Table";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { StatusBadge } from "@/components/shared/StatusBadge";
import { useUsersPage } from "@/domains/users/queries";
import { useTeamMembers } from "@/domains/workspace/queries";
import type { Team } from "@/domains/workspace/types";
import { SourceBadge } from "./SourceBadge";

interface TeamMembersDialogProps {
  companyId: string;
  team: Team | null;
  onClose: () => void;
}

export function TeamMembersDialog({ companyId, team, onClose }: TeamMembersDialogProps) {
  const open = team !== null;
  const members = useTeamMembers(companyId, team?.team_id ?? null);
  // names/emails from the first users page (up to 200); unknown ids fall back to the id
  const users = useUsersPage(companyId, {}, open);
  const byId = useMemo(() => new Map((users.data?.items ?? []).map((u) => [u.user_id, u])), [users.data]);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={team ? `팀 멤버 — ${team.name}` : ""}
      description={members.data ? `${members.data.length}명` : undefined}
      size="lg"
    >
      <div className="space-y-3" data-testid="team-members-dialog">
        <ErrorAlert error={members.error} />
        {members.isPending ? (
          <div className="flex justify-center py-6">
            <Spinner />
          </div>
        ) : members.data && members.data.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">멤버가 없습니다</p>
        ) : members.data ? (
          <div className="overflow-auto rounded-lg border border-border">
            <Table>
              <THead>
                <TR className="border-b border-border">
                  <TH>이메일</TH>
                  <TH>이름</TH>
                  <TH>출처</TH>
                  <TH>상태</TH>
                  <TH>사용자 ID</TH>
                </TR>
              </THead>
              <TBody>
                {members.data.map((m) => {
                  const u = byId.get(m.user_id);
                  return (
                    <TR key={m.user_id}>
                      <TD className="whitespace-nowrap">{u?.email ?? <span className="text-muted-foreground">-</span>}</TD>
                      <TD className="whitespace-nowrap">{u?.name ?? <span className="text-muted-foreground">-</span>}</TD>
                      <TD>
                        <SourceBadge source={m.source} />
                      </TD>
                      <TD>
                        <StatusBadge status={m.status} />
                      </TD>
                      <TD>
                        <Code>{m.user_id}</Code>
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          </div>
        ) : null}
        {users.data?.next_cursor ? (
          <p className="text-xs text-muted-foreground">
            사용자가 많아 일부 멤버는 이름 없이 ID만 표시될 수 있습니다. <Badge variant="outline">사용자</Badge> 탭에서 ID로 검색하세요.
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
