import sys

from .exceptions import ClickyException, UsageError
from .types import STRING
from .utils import echo, get_terminal_width


class Context:
    """Holds state for one invocation of a command."""

    def __init__(self, command, parent=None, info_name=None, obj=None):
        self.command = command
        self.parent = parent
        self.info_name = info_name
        self.obj = obj
        self.params = {}
        self.args = []

    def find_root(self):
        node = self
        while node.parent is not None:
            node = node.parent
        return node

    def fail(self, message):
        raise UsageError(message, self)


class Option:
    def __init__(self, name, type=None, default=None, required=False, help=None):
        self.name = name
        self.type = type or STRING
        self.default = default
        self.required = required
        self.help = help

    def process_value(self, ctx, value):
        if value is None:
            if self.required:
                ctx.fail(f"Missing option '--{self.name}'.")
            return self.default
        return self.type(value, self.name, ctx)


class Command:
    """A single CLI command: a callback plus the options it accepts."""

    def __init__(self, name, callback=None, params=None, help=None):
        self.name = name
        self.callback = callback
        self.params = params or []
        self.help = help

    def make_context(self, info_name, args, parent=None):
        ctx = Context(self, parent=parent, info_name=info_name)
        self.parse_args(ctx, args)
        return ctx

    def parse_args(self, ctx, args):
        raw = {}
        rest = []
        it = iter(args)
        for arg in it:
            if arg.startswith("--"):
                key, sep, val = arg[2:].partition("=")
                if not sep:
                    val = next(it, None)
                raw[key.replace("-", "_")] = val
            else:
                rest.append(arg)
        for param in self.params:
            ctx.params[param.name] = param.process_value(ctx, raw.pop(param.name, None))
        if raw:
            ctx.fail(f"No such option: --{sorted(raw)[0]}")
        ctx.args = rest
        return rest

    def invoke(self, ctx):
        if self.callback is not None:
            return self.callback(**ctx.params)
        return None

    def format_usage(self):
        opts = " ".join(f"[--{p.name}]" for p in self.params)
        return f"Usage: {self.name} {opts}".rstrip()

    def get_help(self):
        width = get_terminal_width()
        lines = [self.format_usage(), ""]
        if self.help:
            lines.append(self.help[:width])
        for p in self.params:
            lines.append(f"  --{p.name:<20} {p.help or ''}"[:width])
        return "\n".join(lines)

    def main(self, args=None, prog_name=None):
        args = sys.argv[1:] if args is None else list(args)
        try:
            ctx = self.make_context(prog_name or self.name, args)
            return self.invoke(ctx)
        except ClickyException as e:
            echo(f"Error: {e.format_message()}", err=True)
            sys.exit(e.exit_code)


class Group(Command):
    """A command that dispatches to named subcommands."""

    def __init__(self, name, commands=None, **kwargs):
        super().__init__(name, **kwargs)
        self.commands = dict(commands or {})

    def add_command(self, cmd, name=None):
        self.commands[name or cmd.name] = cmd

    def get_command(self, ctx, name):
        return self.commands.get(name)

    def invoke(self, ctx):
        if not ctx.args:
            ctx.fail("Missing command.")
        name, *rest = ctx.args
        cmd = self.get_command(ctx, name)
        if cmd is None:
            ctx.fail(f"No such command '{name}'.")
        sub_ctx = cmd.make_context(name, rest, parent=ctx)
        return cmd.invoke(sub_ctx)
