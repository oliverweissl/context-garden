class ClickyException(Exception):
    """Base class for all errors raised by clicky."""

    exit_code = 1

    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def format_message(self):
        return self.message


class UsageError(ClickyException):
    """Raised when the command line could not be parsed."""

    exit_code = 2

    def __init__(self, message, ctx=None):
        super().__init__(message)
        self.ctx = ctx


class BadParameter(UsageError):
    """Raised when a parameter value fails conversion or validation."""

    def __init__(self, message, ctx=None, param_hint=None):
        super().__init__(message, ctx)
        self.param_hint = param_hint

    def format_message(self):
        if self.param_hint is None:
            return f"Invalid value: {self.message}"
        return f"Invalid value for {self.param_hint}: {self.message}"
