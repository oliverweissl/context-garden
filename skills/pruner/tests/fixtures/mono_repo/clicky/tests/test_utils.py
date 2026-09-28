from clicky.utils import format_filename


def test_format_filename_shorten():
    assert format_filename("/tmp/dir/data.csv", shorten=True) == "data.csv"


def test_format_filename_bytes():
    assert format_filename(b"/tmp/x.txt") == "/tmp/x.txt"
