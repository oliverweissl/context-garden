import pytest

from clicky.core import Command, Group, Option
from clicky.exceptions import UsageError


def test_parse_args_equals_syntax():
    cmd = Command("greet", params=[Option("name")])
    ctx = cmd.make_context("greet", ["--name=bob"])
    assert ctx.params["name"] == "bob"


def test_unknown_option_fails():
    cmd = Command("greet", params=[])
    with pytest.raises(UsageError):
        cmd.make_context("greet", ["--nope", "1"])


def test_group_dispatch():
    sub = Command("hello", callback=lambda: "hi")
    grp = Group("cli", commands={"hello": sub})
    ctx = grp.make_context("cli", ["hello"])
    assert grp.invoke(ctx) == "hi"
