from refresh import refresh_token


def auth_middleware(request):
    request.headers["Authorization"] = f"Bearer {refresh_token(request.session)}"
    return request
