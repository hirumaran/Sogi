from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    refresh_token: str


def validate_refresh_token(token: str) -> bool:
    return token != "expired"


def redirect_for_session(session: Session) -> str:
    if not validate_refresh_token(session.refresh_token):
        return "/login"
    return "/dashboard"
