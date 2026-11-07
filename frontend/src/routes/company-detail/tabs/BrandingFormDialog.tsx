import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { ImageUp, RotateCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field, FieldHint, Label } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { usePutBranding } from "@/domains/companies/mutations";
import {
  HEX_COLOR_PATTERN,
  LOGO_MAX_BYTES,
  REPORT_FOOTER_MAX,
  logoDataUri,
  type BrandingIn,
  type Company,
} from "@/domains/companies/types";

const LOGO_TYPES = ["image/png", "image/jpeg"];

interface BrandingFormDialogProps {
  company: Company;
  open: boolean;
  onClose: () => void;
  /** closes this dialog and asks for confirmation before DELETE (only offered when branding exists) */
  onRequestReset: () => void;
}

/** base64 of the file bytes, without the `data:…;base64,` prefix. */
function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? "");
      resolve(url.slice(url.indexOf(",") + 1));
    };
    reader.onerror = () => reject(reader.error ?? new Error("파일을 읽을 수 없습니다"));
    reader.readAsDataURL(file);
  });
}

export function BrandingFormDialog({ company, open, onClose, onRequestReset }: BrandingFormDialogProps) {
  const [color, setColor] = useState("");
  const [logo, setLogo] = useState<string | null>(null);
  const [footer, setFooter] = useState("");
  const [logoError, setLogoError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const put = usePutBranding(company.company_id);
  const current = company.branding;

  // reset form on open
  useEffect(() => {
    if (!open) return;
    setColor(current?.primary_color ?? "");
    setLogo(current?.logo_base64 ?? null);
    setFooter(current?.report_footer ?? "");
    setLogoError(null);
    put.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const colorTrimmed = color.trim();
  const colorInvalid = colorTrimmed !== "" && !HEX_COLOR_PATTERN.test(colorTrimmed);
  const footerTrimmed = footer.trim();
  const footerTooLong = footerTrimmed.length > REPORT_FOOTER_MAX;
  const empty = colorTrimmed === "" && logo === null && footerTrimmed === "";
  const canSubmit = !colorInvalid && !footerTooLong && !empty && !put.isPending;

  const onPickLogo = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file
    if (!file) return;
    if (!LOGO_TYPES.includes(file.type)) {
      setLogoError("PNG 또는 JPEG 파일만 사용할 수 있습니다.");
      return;
    }
    if (file.size > LOGO_MAX_BYTES) {
      setLogoError(`로고는 ${LOGO_MAX_BYTES / 1000} KB 이하여야 합니다 (선택한 파일: ${Math.ceil(file.size / 1000)} KB).`);
      return;
    }
    try {
      setLogo(await readAsBase64(file));
      setLogoError(null);
    } catch {
      setLogoError("파일을 읽을 수 없습니다.");
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const body: BrandingIn = {};
    if (colorTrimmed) body.primary_color = colorTrimmed.toUpperCase();
    if (logo) body.logo_base64 = logo;
    if (footerTrimmed) body.report_footer = footerTrimmed;
    put.mutate(body, { onSuccess: onClose });
  };

  const logoSrc = logoDataUri(logo);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="보고서 브랜딩 편집"
      description="이 회사가 만드는 보고서(PDF·DOCX·PPTX)에 들어갈 브랜드 색상, 로고, 바닥글을 지정합니다."
      size="md"
      locked={put.isPending}
      footer={
        <>
          {current ? (
            <Button variant="ghost" className="mr-auto" onClick={onRequestReset} disabled={put.isPending}>
              <RotateCcw className="h-4 w-4" /> 기본 테마로 되돌리기
            </Button>
          ) : null}
          <Button variant="outline" onClick={onClose} disabled={put.isPending}>
            취소
          </Button>
          <Button type="submit" form="branding-form" loading={put.isPending} disabled={!canSubmit}>
            저장
          </Button>
        </>
      }
    >
      <form id="branding-form" onSubmit={onSubmit} className="space-y-5">
        <div className="space-y-1.5">
          <Label htmlFor="branding-color">브랜드 색상</Label>
          <div className="flex gap-2">
            <input
              type="color"
              aria-label="색상 선택"
              value={HEX_COLOR_PATTERN.test(colorTrimmed) ? colorTrimmed : "#000000"}
              onChange={(e) => setColor(e.target.value.toUpperCase())}
              className="h-9 w-12 cursor-pointer rounded-md border border-border bg-card p-1"
            />
            <Input
              id="branding-color"
              value={color}
              onChange={(e) => setColor(e.target.value)}
              placeholder="#1F7A4D"
              maxLength={7}
              className={colorInvalid ? "border-destructive font-mono" : "font-mono"}
            />
          </div>
          {colorInvalid ? (
            <FieldHint className="text-destructive">#RRGGBB 형식의 16진수 색상이어야 합니다 (예: #1F7A4D)</FieldHint>
          ) : null}
          <FieldHint>
            아주 밝은 색상은 보고서에서 강조(선·배경)에만 쓰이고, 가독성을 위해 제목 등 글자는 FTG 녹색으로 표시됩니다. 비워 두면 기본 색상을 사용합니다.
          </FieldHint>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="branding-logo">로고</Label>
          <div className="flex items-center gap-3">
            <div className="flex h-16 w-32 items-center justify-center rounded-md border border-dashed border-border bg-muted/30">
              {logoSrc ? (
                <img src={logoSrc} alt="로고 미리보기" className="max-h-14 max-w-28 object-contain" />
              ) : (
                <span className="text-xs text-muted-foreground">로고 없음</span>
              )}
            </div>
            <input
              ref={fileRef}
              id="branding-logo"
              type="file"
              accept="image/png,image/jpeg"
              className="hidden"
              onChange={(e) => void onPickLogo(e)}
            />
            <Button variant="outline" size="sm" onClick={() => fileRef.current?.click()} disabled={put.isPending}>
              <ImageUp className="h-4 w-4" /> 파일 선택
            </Button>
            {logo ? (
              <Button variant="ghost" size="sm" onClick={() => setLogo(null)} disabled={put.isPending}>
                <Trash2 className="h-4 w-4" /> 로고 제거
              </Button>
            ) : null}
          </div>
          {logoError ? <FieldHint className="text-destructive">{logoError}</FieldHint> : null}
          <FieldHint>PNG 또는 JPEG, {LOGO_MAX_BYTES / 1000} KB 이하</FieldHint>
        </div>

        <Field label="보고서 바닥글" htmlFor="branding-footer" hint={`${footerTrimmed.length}/${REPORT_FOOTER_MAX}자`}>
          <Input
            id="branding-footer"
            value={footer}
            onChange={(e) => setFooter(e.target.value)}
            placeholder="예: © ACME Corp. 대외비"
            maxLength={REPORT_FOOTER_MAX}
            className={footerTooLong ? "border-destructive" : undefined}
          />
        </Field>

        {empty ? <FieldHint>최소 한 항목을 입력하세요. 모두 비우려면 기본 테마로 되돌리기를 사용하세요.</FieldHint> : null}
        <ErrorAlert error={put.error} />
      </form>
    </Dialog>
  );
}
