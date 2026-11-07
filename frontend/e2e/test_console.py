"""Admin console smoke: the full tree through the real UI, verified against the API."""

import base64
import json
import re

from playwright.sync_api import Page, expect

from conftest import API_INTERNAL, API_PUBLIC, FRONTEND



def _all(api, path):
    """Every item of a cursor-paged list."""
    items, cursor = [], None
    while True:
        page = api.get(path, params={"limit": 200, **({"cursor": cursor} if cursor else {})}).json()
        items += page["items"]
        cursor = page.get("next_cursor")
        if not cursor:
            return items


def test_login_rejects_bad_password(page: Page):
    page.route(f"{API_PUBLIC}/**", lambda r: r.continue_(url=r.request.url.replace(API_PUBLIC, API_INTERNAL, 1)))
    page.goto(f"{FRONTEND}/login")
    page.fill("#login-email", "master@example.com")
    page.fill("#login-password", "wrong-password-xxx")
    page.click("button[type=submit]")
    expect(page.get_by_text("invalid credentials")).to_be_visible(timeout=10000)
    assert page.url.endswith("/login")


def test_full_tree_via_ui(app: Page, api, pg_server, uid):
    cid = f"ui-{uid}"
    # --- company
    app.get_by_role("button", name=re.compile("회사")).first.click()
    app.fill("#company-id", cid)
    app.fill("#company-name", f"UI Corp {uid}")
    app.click("button[type=submit][form=company-create-form]")
    app.wait_for_url(f"**/companies/{cid}**", timeout=15000)
    expect(app.get_by_text(f"UI Corp {uid}").first).to_be_visible()
    assert api.get(f"/companies/{cid}").json()["status"] == "active"

    # --- user (member)
    app.get_by_role("tab", name="사용자").click()
    app.get_by_role("button", name=re.compile("사용자 추가")).click()
    app.fill("#user-email", f"m-{uid}@ui.example")
    app.fill("#user-name", "UI Member")
    app.select_option("#user-role", "member")
    app.click("button[type=submit][form=user-create-form]")
    expect(app.get_by_text(f"m-{uid}@ui.example")).to_be_visible(timeout=10000)
    users = api.get(f"/companies/{cid}/users").json()["items"]
    assert [u["role"] for u in users] == ["member"] and users[0]["can_login"] is False

    # --- binding (schema mode on the postgres sample)
    app.get_by_role("tab", name="서버 바인딩").click()
    app.get_by_role("button", name=re.compile("바인딩 추가")).click()
    app.select_option("#binding-server", pg_server["server_id"])
    app.select_option("#binding-mode", "schema")
    app.fill("#binding-schema", "acme")
    app.click("button[type=submit][form=binding-create-form]")
    expect(app.get_by_text("acme").first).to_be_visible(timeout=10000)
    b = api.get(f"/companies/{cid}/bindings/{pg_server['server_id']}").json()
    assert b["tenant"] == {"mode": "schema", "schema_name": "acme", "credentials": None}

    # --- server access (user x server): read + list_tables/run_select; permissions/tools live here now
    sid = pg_server["server_id"]
    member_id = users[0]["user_id"]
    app.get_by_role("tab", name="서버 접근").click()
    app.get_by_role("button", name=re.compile("접근 부여")).click()
    app.select_option("#access-create-form-user", member_id)
    expect(app.locator(f"#access-create-form-server option[value='{sid}']")).to_be_attached(timeout=10000)
    app.select_option("#access-create-form-server", sid)
    # `read` is pre-checked; tick two tools; a write tool must be disabled without its permission
    app.get_by_label("list_tables").check()
    app.get_by_label("run_select").check()
    expect(app.get_by_label("run_delete")).to_be_disabled()
    app.click("button[type=submit][form=access-create-form]")
    expect(app.get_by_role("dialog")).to_have_count(0, timeout=10000)
    acc = api.get(f"/companies/{cid}/users/{member_id}/access/{sid}").json()
    assert acc["status"] == "active" and acc["permissions"] == ["read"] and acc["allowed_tools"] == ["list_tables", "run_select"]
    assert [a["server_id"] for a in api.get(f"/companies/{cid}/access").json()["items"]] == [sid]
    assert any(a["company_id"] == cid and a["user_id"] == member_id for a in _all(api, f"/servers/{sid}/access"))

    # --- api key scoped to that server, shown once with the connection hint
    app.get_by_role("tab", name="API 키").click()
    app.get_by_role("button", name=re.compile("API 키 발급")).click()
    app.fill("#apikey-create-form-name", "ui-key")
    app.select_option("#apikey-create-form-user", member_id)
    app.select_option("#apikey-create-form-scope", "selected")
    app.locator(f"input[data-server-id='{sid}']").check()
    app.fill("#apikey-create-form-rate", "5")
    app.click("button[type=submit][form=apikey-create-form]")
    banner = app.get_by_text(re.compile(r"mcpk_[0-9a-f]{8}_"))
    expect(banner).to_be_visible(timeout=10000)
    raw = re.search(r"mcpk_[0-9a-f]{8}_[A-Za-z0-9_-]+", banner.inner_text()).group(0)
    if pg_server.get("endpoint"):  # single-server scope -> plain endpoint, no ?server=
        expect(app.get_by_test_id("key-connection-hint").get_by_text(pg_server["endpoint"], exact=True)).to_be_visible()
    keys = api.get(f"/companies/{cid}/api-keys").json()["items"]
    assert len(keys) == 1 and keys[0]["server_ids"] == [sid] and keys[0]["rate_limit_per_minute"] == 5
    assert "permissions" not in keys[0] and "allowed_tools" not in keys[0]
    res = api.post("/ops/resolve", json={"raw_key": raw}).json()
    assert res["effective"] == "active" and res["servers"] == {sid: "active"} and res["redis_in_sync"] is True

    # --- suspend the user's server access -> key denied on that server; reactivate -> active
    app.get_by_role("tab", name="서버 접근").click()
    row = app.get_by_role("row").filter(has_text=f"m-{uid}@ui.example")
    row.get_by_title("일시중지").click()
    app.get_by_role("dialog").get_by_role("button", name="일시중지").click()
    expect(row.get_by_title("재활성화")).to_be_visible(timeout=10000)
    assert api.get(f"/companies/{cid}/users/{member_id}/access/{sid}").json()["status"] == "suspended"
    res = api.post("/ops/resolve", json={"raw_key": raw}).json()
    assert res["effective"] == "active" and res["servers"][sid].startswith("denied:"), res
    row.get_by_title("재활성화").click()
    app.get_by_role("dialog").get_by_role("button", name="재활성화").click()
    expect(row.get_by_title("일시중지")).to_be_visible(timeout=10000)
    assert api.post("/ops/resolve", json={"raw_key": raw}).json()["servers"] == {sid: "active"}

    # --- suspend company from Overview -> key denied; reactivate -> active
    app.get_by_role("tab", name="개요").click()
    app.get_by_title("일시중지").click()
    app.get_by_role("dialog").get_by_role("button", name="일시중지").click()
    expect(app.get_by_title("재활성화")).to_be_visible(timeout=10000)
    assert api.get(f"/companies/{cid}").json()["status"] == "suspended"
    assert api.post("/ops/resolve", json={"raw_key": raw}).json()["effective"] == "denied:company:suspended"
    app.get_by_title("재활성화").click()
    app.get_by_role("dialog").get_by_role("button", name="재활성화").click()
    expect(app.get_by_title("일시중지")).to_be_visible(timeout=10000)
    assert api.get(f"/companies/{cid}").json()["status"] == "active"
    res = api.post("/ops/resolve", json={"raw_key": raw}).json()
    assert res["effective"] == "active" and res["servers"] == {sid: "active"}


def test_admin_scope_in_console(app: Page, api, pg_server, uid):
    """A company admin logs in and only sees their own company; master-only controls are hidden."""
    cid, other = f"adm-{uid}", f"oth-{uid}"
    for c in (cid, other):
        api.post("/companies", json={"company_id": c, "name": c})
    api.post(f"/companies/{cid}/users", json={"email": f"admin-{uid}@ui.example", "name": "Adm", "role": "admin", "password": "adminpassword1"})
    app.goto(f"{FRONTEND}/login")
    app.evaluate("localStorage.clear()")
    app.goto(f"{FRONTEND}/login")
    app.fill("#login-email", f"admin-{uid}@ui.example")
    app.fill("#login-password", "adminpassword1")
    app.click("button[type=submit]")
    app.wait_for_url("**/companies", timeout=15000)
    expect(app.get_by_text(cid).first).to_be_visible()
    expect(app.get_by_text(other)).to_have_count(0)
    expect(app.get_by_role("button", name=re.compile("회사 (추가|생성|만들기)"))).to_have_count(0)
    # own company: the admin manages server access; the per-server access list stays master-only
    app.goto(f"{FRONTEND}/companies/{cid}?tab=access")
    expect(app.get_by_role("button", name=re.compile("접근 부여"))).to_be_visible(timeout=10000)
    app.goto(f"{FRONTEND}/servers/{pg_server['server_id']}?tab=access")
    expect(app.get_by_role("tab", name="개요")).to_be_visible(timeout=10000)
    expect(app.get_by_role("tab", name="사용자 접근")).to_have_count(0)
    app.goto(f"{FRONTEND}/companies/{other}")
    expect(app.get_by_text(re.compile("no access|접근|권한"))).to_be_visible(timeout=10000)


def test_key_scope_all_then_edit_scope(app: Page, api, pg_server, uid):
    """All-servers key: the banner tells the client to add ?server=; editing narrows to one server and back to all
    (scope_all), each change verified against the API."""
    cid = f"scope-{uid}"
    sid = pg_server["server_id"]
    assert api.post("/companies", json={"company_id": cid, "name": f"Scope {uid}"}).status_code == 201
    member = api.post(f"/companies/{cid}/users", json={"email": f"s-{uid}@ui.example", "name": "Scope", "role": "member"}).json()
    assert api.post(f"/companies/{cid}/bindings", json={"server_id": sid, "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
    assert api.post(f"/companies/{cid}/users/{member['user_id']}/access", json={"server_id": sid, "permissions": ["read"], "allowed_tools": ["run_select"]}).status_code == 201

    app.goto(f"{FRONTEND}/companies/{cid}")
    app.get_by_role("tab", name="API 키").click()
    app.get_by_role("button", name=re.compile("API 키 발급")).click()
    app.fill("#apikey-create-form-name", "all-key")
    app.select_option("#apikey-create-form-user", member["user_id"])
    app.select_option("#apikey-create-form-scope", "all")
    app.click("button[type=submit][form=apikey-create-form]")
    expect(app.get_by_text(re.compile(r"mcpk_[0-9a-f]{8}_"))).to_be_visible(timeout=10000)
    hint = app.get_by_test_id("key-connection-hint")
    expect(hint).to_contain_text("?server=")
    if pg_server.get("endpoint"):
        expect(hint).to_contain_text(f"server={sid}")
    key = api.get(f"/companies/{cid}/api-keys").json()["items"][0]
    assert key["server_ids"] is None

    def edit(scope, check_sid=None):
        row = app.get_by_role("row").filter(has_text="all-key")
        row.get_by_title("편집").click()
        app.select_option("#apikey-edit-form-scope", scope)
        if check_sid:
            app.locator(f"input[data-server-id='{check_sid}']").check()
        app.click("button[type=submit][form=apikey-edit-form]")
        expect(app.get_by_role("dialog")).to_have_count(0, timeout=10000)

    app.keyboard.press("Escape")  # close the copy-once banner if it is a dialog
    edit("selected", sid)
    assert api.get(f"/companies/{cid}/api-keys/{key['key_id']}").json()["server_ids"] == [sid]
    edit("all")
    assert api.get(f"/companies/{cid}/api-keys/{key['key_id']}").json()["server_ids"] is None


# 1x1 PNG (the e2e image has no Pillow)
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="


def test_report_branding_edit_and_reset(app: Page, api, uid, tmp_path):
    """Overview > 보고서 브랜딩: colour + logo + footer saved via PUT, verified against the API; reset -> null."""
    cid = f"brand-{uid}"
    assert api.post("/companies", json={"company_id": cid, "name": f"Brand {uid}"}).status_code == 201
    logo = tmp_path / "logo.png"
    logo.write_bytes(base64.b64decode(_TINY_PNG_B64))
    too_big = tmp_path / "big.png"
    too_big.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 250_000)

    app.goto(f"{FRONTEND}/companies/{cid}")
    card = app.get_by_test_id("branding-card")
    expect(card.get_by_text("기본(FTG) 테마 사용 중")).to_be_visible(timeout=10000)
    card.get_by_role("button", name=re.compile("편집")).click()
    dialog = app.get_by_role("dialog")

    # client-side checks: bad hex blocks save; >200 KB logo is rejected with a message
    app.fill("#branding-color", "#12345")
    expect(dialog.get_by_text("#RRGGBB 형식")).to_be_visible()
    expect(app.locator("button[type=submit][form=branding-form]")).to_be_disabled()
    app.set_input_files("#branding-logo", str(too_big))
    expect(dialog.get_by_text(re.compile("로고는 200 KB 이하"))).to_be_visible()

    app.fill("#branding-color", "#1f7a4d")
    app.set_input_files("#branding-logo", str(logo))
    expect(dialog.get_by_alt_text("로고 미리보기")).to_be_visible()
    app.fill("#branding-footer", f"Confidential {uid}")
    app.click("button[type=submit][form=branding-form]")
    expect(app.get_by_role("dialog")).to_have_count(0, timeout=10000)

    assert api.get(f"/companies/{cid}").json()["branding"] == {
        "primary_color": "#1F7A4D",
        "logo_base64": _TINY_PNG_B64,
        "report_footer": f"Confidential {uid}",
    }
    expect(card.get_by_text("#1F7A4D")).to_be_visible()
    expect(card.get_by_alt_text("회사 로고")).to_have_attribute("src", f"data:image/png;base64,{_TINY_PNG_B64}")
    expect(card.get_by_text(f"Confidential {uid}")).to_be_visible()

    # reset: edit dialog -> 기본 테마로 되돌리기 -> ConfirmDialog -> DELETE
    card.get_by_role("button", name=re.compile("편집")).click()
    app.get_by_role("dialog").get_by_role("button", name=re.compile("기본 테마로 되돌리기")).click()
    app.get_by_role("dialog").get_by_role("button", name="되돌리기", exact=True).click()
    expect(app.get_by_role("dialog")).to_have_count(0, timeout=10000)
    assert api.get(f"/companies/{cid}").json()["branding"] is None
    expect(card.get_by_text("기본(FTG) 테마 사용 중")).to_be_visible()


def _service_account_json(uid: str) -> dict:
    """A throwaway Google service-account key file: real 2048-bit RSA PEM, fake project/ids."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    proj = f"e2e-{uid}"
    return {
        "type": "service_account",
        "project_id": proj,
        "private_key_id": f"kid-{uid}",
        "private_key": pem,
        "client_email": f"sync@{proj}.iam.gserviceaccount.com",
        "client_id": "1234567890",
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def test_workspace_policy_via_ui(app: Page, api, pg_server, uid):
    """Master registers a Workspace service account through the Overview card (the checklist, the SSO card and the
    Workspace card pick it up) > a master-made binding > an "전원" (everyone) team policy through the 정책 tab > a plain
    member (made via API, as a directory sync would) gets policy-made access after 지금 적용 (source=policy, badge in the
    access tab). SSO hint on the keys tab; 지금 동기화 records a sync error (the e2e stack has no Google token endpoint)."""
    page = app
    domain = f"wp-{uid}.example"
    cid = domain.replace(".", "-")
    sid = pg_server["server_id"]
    admin_email, member_email = f"boss-{uid}@{domain}", f"staff-{uid}@{domain}"
    sa = _service_account_json(uid)

    def step(n: int) -> str:
        return page.get_by_test_id("onboarding-checklist").get_by_test_id(f"onboarding-step-{n}").get_attribute("data-done")

    # --- company via API, opened in the UI; the operator registers the service account in the Workspace dialog
    r = api.post("/companies", json={"company_id": cid, "name": f"WP {uid}"})
    assert r.status_code == 201, r.text
    page.goto(f"{FRONTEND}/companies/{cid}")
    expect(page.get_by_test_id("onboarding-checklist")).to_be_visible(timeout=10000)
    assert (step(1), step(2), step(3)) == ("false", "false", "false")
    card = page.get_by_test_id("workspace-card")
    card.get_by_test_id("workspace-connect-button").click()
    form = page.get_by_test_id("workspace-form")
    expect(form).to_be_visible()
    page.fill("#workspace-form-domain", domain)
    page.fill("#workspace-form-admin-email", f"boss-{uid}@elsewhere.example")  # wrong domain: client-side rejection
    page.fill("#workspace-form-json", json.dumps(sa))
    expect(form.locator("code", has_text=sa["client_email"])).to_be_visible()  # parsed client-side (the textarea also contains it)
    expect(page.get_by_test_id("workspace-form-submit")).to_be_disabled()
    page.fill("#workspace-form-admin-email", admin_email)
    page.get_by_test_id("workspace-form-submit").click()
    expect(page.get_by_role("dialog")).to_have_count(0, timeout=15000)
    expect(card.get_by_text(domain, exact=True)).to_be_visible(timeout=10000)
    expect(card.get_by_text(sa["client_email"], exact=True)).to_be_visible()
    expect(card.get_by_test_id("workspace-client-id")).to_have_text(sa["client_id"])
    expect(card.get_by_test_id("workspace-needs-reconnect")).to_have_count(0)
    ws = api.get(f"/companies/{cid}/workspace").json()
    assert ws["domain"] == domain and ws["admin_email"] == admin_email and ws["client_id"] == sa["client_id"], ws
    assert ws["has_credentials"] and ws["client_email"] == sa["client_email"] and len(ws["scopes"]) == 3, ws
    assert "private_key" not in json.dumps(ws)
    # connecting auto-creates the platform-Google SSO provider; the checklist's first step is done
    expect(page.get_by_test_id("sso-card").get_by_test_id("sso-provider-type")).to_have_text("플랫폼 Google", timeout=10000)
    expect(page.get_by_test_id("onboarding-step-1")).to_have_attribute("data-done", "true", timeout=10000)
    assert (step(2), step(3)) == ("false", "false")

    # --- master binds the company to the postgres server; the checklist picks it up on reload
    r = api.post(f"/companies/{cid}/bindings", json={"server_id": sid, "tenant": {"mode": "schema", "schema_name": "acme"}})
    assert r.status_code == 201, r.text
    page.reload()
    expect(page.get_by_test_id("onboarding-step-2")).to_have_attribute("data-done", "true", timeout=10000)
    assert step(3) == "false"

    # --- 정책 tab: an "전원" policy on that server with read + list_tables/run_select
    page.get_by_role("tab", name="정책", exact=True).click()
    expect(page.get_by_test_id("policies-tab")).to_be_visible(timeout=10000)
    page.get_by_role("button", name=re.compile("정책 추가")).click()
    form = page.get_by_test_id("policy-form")
    expect(form).to_be_visible()
    target = page.locator("#policy-create-form-target")
    assert target.input_value() == "*" and "전원" in target.locator("option:checked").inner_text()
    expect(page.locator(f"#policy-create-form-server option[value='{sid}']")).to_be_attached(timeout=10000)
    page.select_option("#policy-create-form-server", sid)
    assert "postgres" in page.locator("#policy-create-form-server option:checked").inner_text()
    page.get_by_label("list_tables").check()
    page.get_by_label("run_select").check()
    expect(page.get_by_label("run_delete")).to_be_disabled()  # write tool without the write permission
    page.get_by_test_id("policy-form-submit").click()
    expect(page.get_by_role("dialog")).to_have_count(0, timeout=10000)
    prow = page.get_by_role("row").filter(has_text="전원")
    expect(prow).to_have_count(1, timeout=10000)
    expect(prow).to_contain_text(pg_server["name"])
    policies = api.get(f"/companies/{cid}/policies").json()["items"]
    assert len(policies) == 1, policies
    pol = policies[0]
    assert pol["team_id"] is None and pol["server_id"] == sid and pol["status"] == "active"
    assert pol["permissions"] == ["read"] and sorted(pol["allowed_tools"]) == ["list_tables", "run_select"]
    page.get_by_role("tab", name="개요", exact=True).click()
    expect(page.get_by_test_id("onboarding-step-3")).to_have_attribute("data-done", "true", timeout=10000)

    # --- an employee as a plain member (no password; what a directory sync or a connector login would create)
    r = api.post(f"/companies/{cid}/users", json={"email": member_email, "name": "Staff", "role": "member"})
    assert r.status_code == 201, r.text
    member_id = r.json()["user_id"]
    assert r.json()["can_login"] is False

    # --- the operator re-applies the policies: the member now has policy-made access on the server
    page.get_by_role("tab", name="정책", exact=True).click()
    page.get_by_test_id("policies-apply-button").click()
    expect(page.get_by_test_id("policies-apply-result")).to_be_visible(timeout=15000)
    expect(page.get_by_test_id("policies-apply-result")).to_contain_text("정책 적용 완료")
    r = api.get(f"/companies/{cid}/users/{member_id}/access/{sid}")
    assert r.status_code == 200, r.text
    acc = r.json()
    assert acc["status"] == "active" and acc["source"] == "policy", acc
    assert acc["permissions"] == ["read"] and sorted(acc["allowed_tools"]) == ["list_tables", "run_select"], acc
    assert acc["policy_ids"] == [pol["policy_id"]], acc
    page.get_by_role("tab", name="서버 접근", exact=True).click()
    arow = page.get_by_role("row").filter(has_text=re.compile(re.escape(member_email) + "|" + re.escape(member_id)))
    expect(arow).to_have_count(1, timeout=10000)
    expect(arow.get_by_text("정책", exact=True)).to_be_visible()

    # --- SSO companies: keys are issued by connector logins (hint); Workspace card: the sync fails against the e2e
    # stack (no Google token endpoint) and the error is recorded on the connection
    page.get_by_role("tab", name="API 키", exact=True).click()
    expect(page.get_by_test_id("apikeys-sso-hint")).to_be_visible(timeout=10000)
    page.get_by_role("tab", name="개요", exact=True).click()
    card = page.get_by_test_id("workspace-card")
    expect(card.get_by_text(domain, exact=True)).to_be_visible(timeout=10000)
    card.get_by_test_id("workspace-sync-button").click()
    expect(card.get_by_test_id("workspace-sync-error")).to_be_visible(timeout=20000)
    ws = api.get(f"/companies/{cid}/workspace").json()
    assert ws["domain"] == domain and ws["sync_error"], ws


# --- dashboard: stat tiles, health, event stream, key expiry, artifacts -------------------------------------------


def _iso(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_master_dashboard_tiles(app: Page, api, pg_server):
    """Master sees the /ops/stats tile row on the companies and servers lists; the servers table gains 헬스/바인딩."""
    tiles = app.get_by_test_id("stat-tiles")
    expect(tiles).to_be_visible(timeout=10000)
    stats = api.get("/ops/stats").json()
    row = tiles.get_by_test_id("stat-tile").filter(has_text="회사")
    expect(row.first).to_contain_text(str(stats["counts"]["companies"]["active"]), timeout=10000)
    expect(tiles.get_by_test_id("stat-tile").filter(has_text="만료 임박 키")).to_contain_text(f"{stats['keys_expiring']['days']}일 내")
    app.get_by_role("button", name=re.compile("새로고침")).click()
    expect(tiles).to_be_visible()

    app.goto(f"{FRONTEND}/servers")
    tiles = app.get_by_test_id("stat-tiles")
    expect(tiles).to_be_visible(timeout=10000)
    health = api.get("/ops/health").json()
    ok = sum(1 for h in health if h["ok"] is True)
    expect(tiles.get_by_test_id("stat-tile").filter(has_text="정상 서비스")).to_contain_text(f"{ok} / {len(health)}", timeout=10000)
    # Chromium exposes these <th> as "cell", not "columnheader" — match the header text directly
    expect(app.locator("thead th", has_text="헬스")).to_be_visible()
    expect(app.locator("thead th", has_text="바인딩")).to_be_visible(timeout=10000)
    srow = app.get_by_role("row").filter(has_text=pg_server["server_id"])
    expect(srow).to_contain_text(str(stats["bindings_per_server"].get(pg_server["server_id"], 0)))


def test_company_overview_tiles_fresh_company(app: Page, api, uid):
    """A fresh company with no calls renders the stat row with zeros (calls_24h is empty, not an error)."""
    cid = f"tile-{uid}"
    assert api.post("/companies", json={"company_id": cid, "name": f"Tile {uid}"}).status_code == 201
    app.goto(f"{FRONTEND}/companies/{cid}")
    tiles = app.get_by_test_id("company-stat-tiles")
    expect(tiles).to_be_visible(timeout=10000)
    expect(tiles.get_by_test_id("stat-tile")).to_have_count(6)
    expect(tiles.get_by_test_id("stat-tile").filter(has_text="24h 호출")).to_contain_text("0", timeout=10000)
    expect(tiles.get_by_test_id("stat-tile").filter(has_text="24h 거부율")).to_contain_text("0.0%")
    expect(tiles.get_by_test_id("stat-tile").filter(has_text="사용자")).to_contain_text("0")
    st = api.get(f"/companies/{cid}/stats").json()
    assert st["company_id"] == cid and (st["calls_24h"] or {}).get("calls", 0) == 0, st


def test_ops_page_health_and_events(app: Page, api):
    """Ops page: service health card from /ops/health and the auto-refreshing event stream card."""
    app.goto(f"{FRONTEND}/ops")
    expect(app.get_by_test_id("ops-health")).to_be_visible(timeout=10000)
    expect(app.get_by_test_id("ops-events")).to_be_visible()
    expect(app.get_by_test_id("ops-events")).to_contain_text("10초마다 갱신")
    health = api.get("/ops/health").json()
    for h in health:
        expect(app.get_by_test_id("ops-health").get_by_text(h["kind"], exact=True)).to_be_visible(timeout=10000)
    if not health:
        expect(app.get_by_test_id("ops-health")).to_contain_text("헬스 체크 대상이 없습니다")
    events = api.get("/ops/events", params={"limit": 5}).json()
    card = app.get_by_test_id("ops-events")
    if events:  # rows are virtualized, so only check the newest one (header row is nth(0))
        newest = card.get_by_role("row").nth(1)
        expect(newest).to_be_visible(timeout=10000)
        expect(newest).to_contain_text({"ok": "성공", "denied": "거부", "error": "오류"}.get(events[0]["status"], "-"))
    else:
        expect(card).to_contain_text("최근 이벤트가 없습니다")


def test_api_key_expiry_badge_and_extend(app: Page, api, pg_server, uid):
    """Key expiring in 5 days shows a D-n badge; '30일 연장' PATCHes expires_at = old + 30d and the badge goes away."""
    from datetime import datetime, timedelta, timezone

    cid, sid = f"exp-{uid}", pg_server["server_id"]
    assert api.post("/companies", json={"company_id": cid, "name": f"Exp {uid}"}).status_code == 201
    member = api.post(f"/companies/{cid}/users", json={"email": f"e-{uid}@ui.example", "name": "Exp", "role": "member"}).json()
    assert api.post(f"/companies/{cid}/bindings", json={"server_id": sid, "tenant": {"mode": "schema", "schema_name": "acme"}}).status_code == 201
    assert api.post(f"/companies/{cid}/users/{member['user_id']}/access", json={"server_id": sid, "permissions": ["read"], "allowed_tools": ["run_select"]}).status_code == 201
    expires = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=5)
    r = api.post(f"/companies/{cid}/api-keys", json={"user_id": member["user_id"], "name": "exp-key", "server_ids": [sid], "expires_at": expires.isoformat()})
    assert r.status_code == 201, r.text
    key = r.json()["key"]
    before = _iso(key["expires_at"])

    app.goto(f"{FRONTEND}/companies/{cid}?tab=apikeys")
    row = app.get_by_role("row").filter(has_text="exp-key")
    badge = row.get_by_test_id("apikey-expiry")
    expect(badge).to_be_visible(timeout=10000)
    expect(badge).to_have_text(re.compile(r"^D-[45]$"))  # 5 days minus the seconds since issue -> whole days
    row.get_by_test_id("apikey-extend").click()
    app.get_by_role("dialog").get_by_role("button", name="연장", exact=True).click()
    expect(app.get_by_role("dialog")).to_have_count(0, timeout=10000)
    after = _iso(api.get(f"/companies/{cid}/api-keys/{key['key_id']}").json()["expires_at"])
    assert abs((after - before) - timedelta(days=30)) < timedelta(minutes=1), (before, after)
    expect(row.get_by_test_id("apikey-expiry")).to_have_count(0, timeout=10000)  # D-34 is outside the 14-day window
    expect(row.get_by_test_id("apikey-extend")).to_be_visible()  # still active with an expiry -> still extendable


def test_artifacts_tab_empty(app: Page, api, uid):
    """산출물 tab: metadata list from /companies/{cid}/artifacts, empty for a fresh company."""
    cid = f"art-{uid}"
    assert api.post("/companies", json={"company_id": cid, "name": f"Art {uid}"}).status_code == 201
    assert api.get(f"/companies/{cid}/artifacts").json() == []
    app.goto(f"{FRONTEND}/companies/{cid}")
    app.get_by_role("tab", name="산출물", exact=True).click()
    tab = app.get_by_test_id("artifacts-tab")
    expect(tab).to_be_visible(timeout=10000)
    expect(tab).to_contain_text("산출물이 없습니다")
    expect(tab).to_contain_text("export")
    tab.get_by_label("만료된 산출물 포함").check()
    expect(tab).to_contain_text("산출물이 없습니다")
