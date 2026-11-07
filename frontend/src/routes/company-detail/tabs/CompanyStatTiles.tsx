import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { Sparkline } from "@/components/shared/Sparkline";
import { StatTile, StatTileRow } from "@/components/shared/StatTile";
import type { CompanyStats } from "@/domains/companies/types";

/** Top row of the company overview: counts + 24h calls from GET /companies/{cid}/stats. */
export function CompanyStatTiles({ stats, error }: { stats: CompanyStats | undefined; error: unknown }) {
  const c = stats?.counts;
  const n = (v: number | undefined) => (stats ? (v ?? 0).toLocaleString() : "–");
  const exp = stats?.keys_expiring;
  const calls = stats?.calls_24h ?? null;
  const deniedPct = calls ? `${(calls.denied_rate * 100).toFixed(1)}%` : stats ? "0.0%" : "–";
  return (
    <div className="space-y-4 lg:col-span-2" data-testid="company-stat-tiles">
      <StatTileRow>
        <StatTile label="사용자" value={n(c?.users?.active)} dot="accent" />
        <StatTile label="바인딩" value={n(c?.bindings?.active)} dot="accent" />
        <StatTile label="활성 키" value={n(c?.api_keys?.active)} dot="accent" />
        <StatTile
          label="만료 임박 키"
          value={n(exp?.count)}
          dot={exp && exp.count > 0 ? "hollow" : undefined}
          sub={
            exp ? (
              <>
                {exp.days}일 내
                {exp.expired_still_active > 0 ? <span className="text-destructive"> · 만료 후 활성 {exp.expired_still_active}</span> : null}
              </>
            ) : null
          }
        />
        <StatTile label="24h 호출" value={calls ? calls.calls.toLocaleString() : stats ? "0" : "–"}>
          {calls ? <Sparkline series={calls.series} height={24} /> : null}
        </StatTile>
        <StatTile
          label="24h 거부율"
          value={deniedPct}
          dot={calls && calls.denied > 0 ? "hollow" : undefined}
          sub={calls ? `거부 ${calls.denied.toLocaleString()} · 오류 ${calls.errors.toLocaleString()}` : null}
        />
      </StatTileRow>
      <ErrorAlert error={error} />
    </div>
  );
}
