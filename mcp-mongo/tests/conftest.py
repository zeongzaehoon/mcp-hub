"""Tests run as dev explicitly: an unset APP_ENV is treated as production (strict keys, redaction, metrics token)."""

import os

os.environ.setdefault("APP_ENV", "dev")
