from __future__ import annotations

from feedme import auth


def test_pkce_pair_is_url_safe_and_related():
    verifier, challenge = auth.generate_pkce_pair()
    assert verifier != challenge
    assert len(verifier) >= 43  # RFC 7636 minimum length
    for char in "+/=":
        assert char not in verifier
        assert char not in challenge


def test_build_authorization_url_contains_required_params():
    url = auth.build_authorization_url("challenge123", "state456")
    assert url.startswith(auth.AUTHORIZE_URL)
    assert "response_type=code" in url
    assert "code_challenge=challenge123" in url
    assert "code_challenge_method=S256" in url
    assert "state=state456" in url
    assert "scope=mcp%3Atools" in url


def test_save_and_load_credentials_round_trip(credentials_path, fresh_credentials):
    auth.save_credentials(fresh_credentials)
    loaded = auth.load_credentials()
    assert loaded is not None
    assert loaded.access_token == fresh_credentials.access_token

    mode = credentials_path.stat().st_mode & 0o777
    assert mode == 0o600


def test_load_credentials_missing_file_returns_none(credentials_path):
    assert auth.load_credentials() is None


def test_is_token_expired(fresh_credentials, expired_credentials):
    assert auth.is_token_expired(fresh_credentials) is False
    assert auth.is_token_expired(expired_credentials) is True
