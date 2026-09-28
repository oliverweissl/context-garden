from .exceptions import BadParameter


class ParamType:
    """Base class for parameter types; subclasses implement convert()."""

    name = "text"

    def __call__(self, value, param=None, ctx=None):
        if value is None:
            return None
        return self.convert(value, param, ctx)

    def convert(self, value, param, ctx):
        return value

    def fail(self, message, param=None, ctx=None):
        raise BadParameter(message, ctx=ctx, param_hint=param)


class StringParamType(ParamType):
    name = "text"

    def convert(self, value, param, ctx):
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return str(value)


class IntParamType(ParamType):
    name = "integer"

    def convert(self, value, param, ctx):
        try:
            return int(value)
        except ValueError:
            self.fail(f"{value!r} is not a valid integer.", param, ctx)


class IntRange(IntParamType):
    """Restrict an integer to a [min, max] range, optionally clamping."""

    name = "integer range"

    def __init__(self, min=None, max=None, clamp=False):
        self.min = min
        self.max = max
        self.clamp = clamp

    def convert(self, value, param, ctx):
        rv = super().convert(value, param, ctx)
        if self.clamp:
            return self._clamp_bound(rv)
        if self.min is not None and rv < self.min:
            self.fail(f"{rv} is smaller than the minimum {self.min}.", param, ctx)
        if self.max is not None and rv > self.max:
            self.fail(f"{rv} is bigger than the maximum {self.max}.", param, ctx)
        return rv

    def _clamp_bound(self, rv):
        if self.min is not None and rv < self.min:
            return self.min
        if self.max is not None and rv > self.min:
            return self.max
        return rv


class Choice(ParamType):
    """Accept only one of a fixed set of string values."""

    name = "choice"

    def __init__(self, choices, case_sensitive=True):
        self.choices = list(choices)
        self.case_sensitive = case_sensitive

    def normalize(self, value):
        return value if self.case_sensitive else value.casefold()

    def convert(self, value, param, ctx):
        normed = {self.normalize(c): c for c in self.choices}
        key = self.normalize(value)
        if key in normed:
            return normed[key]
        choices_str = ", ".join(map(repr, self.choices))
        self.fail(f"{value!r} is not one of {choices_str}.", param, ctx)


class BoolParamType(ParamType):
    name = "boolean"

    def convert(self, value, param, ctx):
        if isinstance(value, bool):
            return value
        norm = value.strip().lower()
        if norm in {"1", "true", "yes", "y", "on"}:
            return True
        if norm in {"0", "false", "no", "n", "off"}:
            return False
        self.fail(f"{value!r} is not a valid boolean.", param, ctx)


STRING = StringParamType()
INT = IntParamType()
BOOL = BoolParamType()
