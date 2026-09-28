import pytest

from clicky.exceptions import BadParameter
from clicky.types import Choice, IntRange


def test_intrange_rejects_out_of_range():
    with pytest.raises(BadParameter):
        IntRange(0, 5).convert("9", None, None)


def test_intrange_clamps_to_max():
    assert IntRange(0, 5, clamp=True).convert("9", None, None) == 5


def test_intrange_clamps_to_min():
    assert IntRange(0, 5, clamp=True).convert("-3", None, None) == 0


def test_choice_case_insensitive():
    assert Choice(["Red", "Green"], case_sensitive=False).convert("red", None, None) == "Red"


def test_choice_rejects_unknown():
    with pytest.raises(BadParameter):
        Choice(["a", "b"]).convert("c", None, None)
