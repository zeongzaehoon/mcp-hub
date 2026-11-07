import { useEffect, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Field, FieldHint } from "@/components/ui/Label";
import { ErrorAlert } from "@/components/shared/ErrorAlert";
import { usePutSso } from "@/domains/sso/mutations";
import { SSO_DOMAINS_MAX, parseDomains, type SsoProviderIn, type SsoProviderOut } from "@/domains/sso/types";
import { cn } from "@/lib/utils";

type ProviderType = "google" | "oidc";

interface SsoFormDialogProps {
  companyId: string;
  /** existing provider (edit) or null (first create) */
  current: SsoProviderOut | null;
  /** platform Google client configured (`/auth/sso/options`); the platform option is offered only then */
  googleEnabled: boolean;
  open: boolean;
  onClose: () => void;
}

function ProviderTypeOption(p: { value: ProviderType; checked: boolean; onChange: (v: ProviderType) => void; label: string; description: string; testId?: string }) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-start gap-2 rounded-[5px] border px-3 py-2 text-sm hover:bg-muted",
        p.checked ? "border-primary" : "border-border",
      )}
    >
      <input
        type="radio"
        name="sso-provider-type"
        value={p.value}
        checked={p.checked}
        onChange={() => p.onChange(p.value)}
        className="mt-0.5 h-4 w-4 accent-primary"
        data-testid={p.testId}
      />
      <span className="flex flex-col">
        <span className="font-medium">{p.label}</span>
        <span className="text-xs text-muted-foreground">{p.description}</span>
      </span>
    </label>
  );
}

export function SsoFormDialog({ companyId, current, googleEnabled, open, onClose }: SsoFormDialogProps) {
  const [providerType, setProviderType] = useState<ProviderType>("oidc");
  const [issuer, setIssuer] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [domains, setDomains] = useState("");
  const [hostedDomain, setHostedDomain] = useState("");
  const [autoProvision, setAutoProvision] = useState(true);
  const put = usePutSso(companyId);

  // reset form on open
  useEffect(() => {
    if (!open) return;
    // existing provider keeps its type; a new one defaults to the platform client when the deployment has one
    setProviderType(current ? (current.platform === "google" ? "google" : "oidc") : googleEnabled ? "google" : "oidc");
    setIssuer(current?.issuer ?? "");
    setClientId(current?.client_id ?? "");
    setClientSecret("");
    setDomains(current?.domains.join(", ") ?? "");
    setHostedDomain(current?.hosted_domain ?? "");
    setAutoProvision(current?.auto_provision ?? true);
    put.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const isPlatform = providerType === "google";
  const issuerTrimmed = issuer.trim();
  const issuerInvalid = issuerTrimmed !== "" && !/^https:\/\/\S+$/.test(issuerTrimmed);
  const parsedDomains = parseDomains(domains);
  const domainsTooMany = parsedDomains.length > SSO_DOMAINS_MAX;
  // a stored secret only counts for an own-client provider; switching from platform to OIDC needs a new one
  const secretRequired = !(current?.has_secret && !current.platform);
  const secretMissing = secretRequired && clientSecret.trim() === "";
  const oidcValid = issuerTrimmed !== "" && !issuerInvalid && clientId.trim() !== "" && !secretMissing;
  const canSubmit = (isPlatform || oidcValid) && parsedDomains.length > 0 && !domainsTooMany && !put.isPending;

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    const common = {
      domains: parsedDomains,
      hosted_domain: hostedDomain.trim() || null,
      auto_provision: autoProvision,
      status: current?.status === "suspended" ? "suspended" : "active",
    } as const;
    let body: SsoProviderIn;
    if (isPlatform) {
      body = { platform: "google", ...common };
    } else {
      body = { issuer: issuerTrimmed, client_id: clientId.trim(), ...common };
      // write-only: omitted keeps the stored secret
      if (clientSecret.trim()) body.client_secret = clientSecret.trim();
    }
    put.mutate(body, { onSuccess: onClose });
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={current ? "직원 로그인 공급자 편집" : "직원 로그인 공급자 설정"}
      description="직원이 MCP 클라이언트의 동의 화면에서 회사의 OpenID Connect 제공자(Google Workspace, Okta 등)로 로그인합니다. 인증만 담당하며, 권한은 서버 접근에서 그대로 관리됩니다."
      size="md"
      locked={put.isPending}
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={put.isPending}>
            취소
          </Button>
          <Button type="submit" form="sso-form" loading={put.isPending} disabled={!canSubmit}>
            저장
          </Button>
        </>
      }
    >
      <form id="sso-form" data-testid="sso-form" onSubmit={onSubmit} className="space-y-5">
        {googleEnabled || isPlatform ? (
          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-medium">제공자 종류</legend>
            {googleEnabled ? (
              <ProviderTypeOption
                value="google"
                checked={isPlatform}
                onChange={setProviderType}
                label="플랫폼 Google (권장)"
                description="플랫폼에 등록된 Google 클라이언트를 씁니다. 회사가 따로 클라이언트를 만들 필요가 없습니다."
                testId="sso-platform-google"
              />
            ) : null}
            <ProviderTypeOption
              value="oidc"
              checked={!isPlatform}
              onChange={setProviderType}
              label="직접 등록 (OIDC)"
              description="회사가 만든 OIDC 앱(Google Cloud, Okta 등)의 issuer·client id·secret을 입력합니다."
              testId="sso-platform-oidc"
            />
            {isPlatform ? (
              <FieldHint>회사가 Google Cloud 에 OAuth 앱을 만들 필요가 없습니다. 도메인과 Workspace 도메인(hd)만 입력하세요.</FieldHint>
            ) : null}
          </fieldset>
        ) : null}

        {isPlatform ? null : (
          <>
            <Field label="Issuer" htmlFor="sso-issuer" hint="예: https://accounts.google.com — OIDC discovery(.well-known)를 제공하는 URL">
              <Input
                id="sso-issuer"
                value={issuer}
                onChange={(e) => setIssuer(e.target.value)}
                placeholder="https://accounts.google.com"
                required
                autoFocus
                className={issuerInvalid ? "border-destructive font-mono" : "font-mono"}
              />
              {issuerInvalid ? <FieldHint className="text-destructive">https:// 로 시작하는 URL이어야 합니다</FieldHint> : null}
            </Field>

            <Field label="Client ID" htmlFor="sso-client-id">
              <Input id="sso-client-id" value={clientId} onChange={(e) => setClientId(e.target.value)} required className="font-mono" />
            </Field>

            <Field
              label="Client Secret"
              htmlFor="sso-client-secret"
              hint={secretRequired ? "처음 설정할 때는 필수입니다. 저장 후에는 다시 표시되지 않습니다." : "저장된 시크릿을 유지하려면 비워 두세요."}
            >
              <Input
                id="sso-client-secret"
                type="password"
                autoComplete="new-password"
                value={clientSecret}
                onChange={(e) => setClientSecret(e.target.value)}
                placeholder={secretRequired ? "" : "변경할 때만 입력"}
                required={secretRequired}
              />
            </Field>
          </>
        )}

        <Field
          label="이메일 도메인"
          htmlFor="sso-domains"
          hint={`쉼표로 구분, 1~${SSO_DOMAINS_MAX}개. 이 도메인의 이메일만 이 회사의 SSO로 로그인합니다. (${parsedDomains.length}개)`}
        >
          <Input
            id="sso-domains"
            value={domains}
            onChange={(e) => setDomains(e.target.value)}
            placeholder="example.com, example.co.kr"
            required
            className={domainsTooMany ? "border-destructive" : undefined}
          />
          {domainsTooMany ? <FieldHint className="text-destructive">도메인은 최대 {SSO_DOMAINS_MAX}개까지 등록할 수 있습니다</FieldHint> : null}
        </Field>

        <Field label="Hosted domain (선택)" htmlFor="sso-hosted-domain" hint="Google Workspace의 hd 값. 지정하면 해당 Workspace 계정만 허용합니다.">
          <Input id="sso-hosted-domain" value={hostedDomain} onChange={(e) => setHostedDomain(e.target.value)} placeholder="example.com" />
        </Field>

        <Checkbox
          id="sso-auto-provision"
          label="자동 프로비저닝"
          description="도메인이 일치하는 미등록 사용자가 처음 로그인하면 멤버로 생성합니다. 끄면 등록된 사용자만 로그인할 수 있습니다."
          checked={autoProvision}
          onChange={(e) => setAutoProvision(e.target.checked)}
        />

        <ErrorAlert error={put.error} />
      </form>
    </Dialog>
  );
}
