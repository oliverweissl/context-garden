def refresh_token(session):
    """Renew the auth token when it is within 60 s of expiry."""
    if session.expires_in < 60:
        session.token = session.client.renew(session.token)
    return session.token
