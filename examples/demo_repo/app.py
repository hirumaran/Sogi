from auth import Session, redirect_for_session


def handle_request(token: str) -> str:
    return redirect_for_session(Session(refresh_token=token))
