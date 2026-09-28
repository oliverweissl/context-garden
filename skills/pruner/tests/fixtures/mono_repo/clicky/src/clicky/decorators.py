from .core import Command, Group, Option


def _param_memo(f, param):
    if not hasattr(f, "__clicky_params__"):
        f.__clicky_params__ = []
    f.__clicky_params__.append(param)


def option(name, **attrs):
    """Attach an --option to the decorated command callback."""

    def decorator(f):
        _param_memo(f, Option(name.lstrip("-").replace("-", "_"), **attrs))
        return f

    return decorator


def command(name=None, cls=Command, **attrs):
    """Turn a function into a Command."""

    def decorator(f):
        params = list(reversed(getattr(f, "__clicky_params__", [])))
        return cls(name or f.__name__.replace("_", "-"), callback=f, params=params, **attrs)

    return decorator


def group(name=None, **attrs):
    return command(name, cls=Group, **attrs)
