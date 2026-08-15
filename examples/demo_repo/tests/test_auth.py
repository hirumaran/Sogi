from auth import Session, redirect_for_session


def test_expired_refresh_token_redirects_to_login() -> None:
    assert redirect_for_session(Session(refresh_token="expired")) == "/login"
