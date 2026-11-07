import { Palette } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Code } from "@/components/ui/Code";
import { KeyValueList } from "@/components/shared/KeyValueList";
import { HEX_COLOR_PATTERN, logoDataUri, type Branding } from "@/domains/companies/types";

interface BrandingCardProps {
  branding: Branding | null;
  canEdit: boolean;
  /** shown when editing is not allowed */
  readOnlyHint?: string;
  onEdit: () => void;
}

const none = <span className="text-muted-foreground">없음</span>;

export function BrandingCard({ branding, canEdit, readOnlyHint, onEdit }: BrandingCardProps) {
  const color = branding?.primary_color && HEX_COLOR_PATTERN.test(branding.primary_color) ? branding.primary_color : null;
  const logoSrc = logoDataUri(branding?.logo_base64);

  return (
    <Card data-testid="branding-card">
      <CardHeader>
        <CardTitle>보고서 브랜딩</CardTitle>
        {canEdit ? (
          <Button variant="outline" size="sm" onClick={onEdit}>
            <Palette className="h-4 w-4" /> 편집
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {branding ? (
          <KeyValueList
            items={[
              {
                label: "브랜드 색상",
                value: color ? (
                  <span className="inline-flex items-center gap-2">
                    <span className="h-4 w-4 rounded border border-border" style={{ backgroundColor: color }} aria-hidden />
                    <Code>{color}</Code>
                  </span>
                ) : (
                  none
                ),
              },
              {
                label: "로고",
                value: logoSrc ? <img src={logoSrc} alt="회사 로고" className="max-h-12 max-w-40 object-contain" /> : none,
              },
              { label: "바닥글", value: branding.report_footer ? branding.report_footer : none },
            ]}
          />
        ) : (
          <p className="text-sm text-muted-foreground">기본(FTG) 테마 사용 중</p>
        )}
        {!canEdit && readOnlyHint ? <p className="mt-3 text-xs text-muted-foreground">{readOnlyHint}</p> : null}
      </CardContent>
    </Card>
  );
}
