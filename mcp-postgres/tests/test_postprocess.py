"""Pure-Python redaction semantics + Rust parity (parity part runs only where the wheel is installed)."""

import os

import pytest

from mcpcluster_common import postprocess as pp

CASES = {
    "contact alice@acme.example now": "contact [EMAIL] now",
    "call 010-1234-5678 or 02 123 4567": "call [PHONE] or [PHONE]",
    "card 4111 1111 1111 1111 ok": "card [CARD] ok",           # Luhn-valid test number
    "not a card 1234 5678 9012 3456": "not a card 1234 5678 9012 3456",  # Luhn-invalid: kept
    "rrn 900101-1234567": "rrn [RRN]",
    "order id 1234567890123 stays": "order id 1234567890123 stays",  # 13 digits, invalid Luhn
    "amex 3782 822463 10005 ok": "amex [CARD] ok",
    "unbroken 4111111111111111 ok": "unbroken [CARD] ok",
    "glued 010-1234-5678 4111 1111 1111 1111": "glued [PHONE] [CARD]",  # phone must not bridge into the card
    "plain text": "plain text",
    "": "",
    # --- secrets
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY----- tail": "[PRIVATE_KEY] tail",
    "tok eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk": "tok [JWT]",
    "key AKIAIOSFODNN7EXAMPLE used": "key [AWS_KEY] used",
    "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789": "Authorization: Bearer [TOKEN]",
    "authorization: bearer short": "authorization: bearer short",  # too short to be a token: kept
    "our key mcpk_1a2b3c4d_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 leaked": "our key [API_KEY] leaked",
    "sk-abcdefghijklmnopqrstuvwxyz12345 and ghp_abcdefghijklmnopqrstuvwxyz0123456789": "[API_KEY] and [API_KEY]",
    # --- network identifiers
    "from 192.168.10.25 and 8.8.8.8": "from [IP] and [IP]",
    "not an ip 999.1.1.1 or 1.2.3": "not an ip 999.1.1.1 or 1.2.3",
    "v6 2001:0db8:85a3:0000:0000:8a2e:0370:7334 and fe80::1ff:fe23:4567:890a": "v6 [IP] and [IP]",
    "time 12:30:45 and mac aa:bb:cc:dd:ee:ff stay": "time 12:30:45 and mac aa:bb:cc:dd:ee:ff stay",
    # --- national / financial identifiers
    "biz 123-45-67890 ssn 123-45-6789": "biz [BIZ_NO] ssn [SSN]",
    "iban GB82WEST12345698765432 ok": "iban [IBAN] ok",
    "iban GB82WEST12345698765433 bad": "iban GB82WEST12345698765433 bad",  # checksum fails: kept
    "intl +82 10-1234-5678 / +1 415 555 0132": "intl [PHONE] / [PHONE]",
    # --- mixed line: rule order must be stable
    "alice@acme.example 010-1234-5678 4111 1111 1111 1111 900101-1234567 10.0.0.1": "[EMAIL] [PHONE] [CARD] [RRN] [IP]",
}


@pytest.mark.parametrize("src,expected", CASES.items())
def test_python_fallback(src, expected):
    assert pp.redact_batch([src], force_python=True) == [expected]


def test_rows_recursive_and_none_passthrough():
    rows = [{"a": "x@y.io", "n": 3, "nested": {"l": ["010-1111-2222", None, 1.5]}}]
    out = pp.redact_rows(rows, enabled=True)
    assert out == [{"a": "[EMAIL]", "n": 3, "nested": {"l": ["[PHONE]", None, 1.5]}}]
    assert pp.redact_rows(rows, enabled=False) is rows


def test_luhn():
    assert pp.luhn_valid("4111111111111111") and not pp.luhn_valid("4111111111111112") and not pp.luhn_valid("123")


def test_iban_checksum():
    assert pp.iban_valid("GB82WEST12345698765432") and pp.iban_valid("DE89370400440532013000")
    assert not pp.iban_valid("GB82WEST12345698765433") and not pp.iban_valid("GB82west12345698765432")


def test_sha256_fallback():
    assert pp.sha256_hex_batch(["a"], force_python=True) == ["ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"]


@pytest.mark.skipif(not pp.USING_RUST and not os.environ.get("EXPECT_RUST"), reason="rust wheel not installed")
def test_rust_parity():
    assert pp.USING_RUST, "EXPECT_RUST set but mcpcluster_core not importable"
    srcs = list(CASES) + ["mix a@b.co 010-0000-0000 4111111111111111 900101-2345678"]
    assert pp.redact_batch(srcs) == pp.redact_batch(srcs, force_python=True)
    assert pp.redact_batch([None, "x"]) == [None, "x"]
    assert pp.sha256_hex_batch(srcs) == pp.sha256_hex_batch(srcs, force_python=True)
    import mcpcluster_core

    assert mcpcluster_core.IMPLEMENTATION == "rust" and mcpcluster_core.luhn_valid("4111111111111111")
    assert mcpcluster_core.iban_ok("GB82WEST12345698765432") and not mcpcluster_core.iban_ok("GB82WEST12345698765433")
    # every case individually, so a divergence names the exact input
    for src, expected in CASES.items():
        assert pp.redact_batch([src]) == [expected] == pp.redact_batch([src], force_python=True), src


def test_redact_value_covers_compute_results():
    # security review #1: group keys / top-k values of compute results are data and must be masked
    from mcpcluster_common.postprocess import redact_value

    out = redact_value([{"key": "alice@acme.example", "count": 1}, {"key": "010-1234-5678", "count": 2}], enabled=True)
    assert out == [{"key": "[EMAIL]", "count": 1}, {"key": "[PHONE]", "count": 2}]
    assert redact_value({"mean": 1.5, "p50": 2}, enabled=True) == {"mean": 1.5, "p50": 2}  # numbers untouched


def test_unset_app_env_is_treated_as_production(monkeypatch):
    # security review 2 #8: forgetting APP_ENV must not silently weaken the key or switch redaction off
    import importlib

    from mcpcluster_common import postprocess, secrets
    from mcpcluster_common.settings import CommonSettings

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("REDACT_PII", raising=False)
    with pytest.raises(RuntimeError, match="must be set outside dev"):
        secrets.fernet(CommonSettings())
    assert importlib.reload(postprocess).ENABLED is True
    monkeypatch.setenv("APP_ENV", "dev")
    importlib.reload(postprocess)
