from .core import interpolate


def public_api(x):
    return interpolate(x, 0.0, 1.0)
