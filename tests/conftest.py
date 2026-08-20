from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from models import Credentials


@pytest.fixture
def fresh_credentials() -> Credentials:
    return Credentials(access_token="tok-123", token_type="Bearer", expires_in=432000)


@pytest.fixture
def expired_credentials() -> Credentials:
    return Credentials(
        access_token="tok-old",
        token_type="Bearer",
        expires_in=100,
        obtained_at=datetime.now(UTC) - timedelta(seconds=1000),
    )


@pytest.fixture
def credentials_path(tmp_path, monkeypatch):
    path = tmp_path / "credentials.json"
    monkeypatch.setattr("feedme.auth.CREDENTIALS_PATH", path)
    return path
