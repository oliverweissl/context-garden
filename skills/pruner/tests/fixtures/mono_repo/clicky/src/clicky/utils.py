import os
import sys


def make_str(value):
    """Convert bytes or any object to a text string."""
    if isinstance(value, bytes):
        return value.decode(sys.getfilesystemencoding() or "utf-8", "replace")
    return str(value)


def format_filename(filename, shorten=False):
    """Format a filename for display, optionally keeping only the basename."""
    if shorten:
        filename = os.path.basename(filename)
    return make_str(filename)


def get_terminal_width(default=80):
    try:
        return os.get_terminal_size().columns
    except OSError:
        return default


def echo(message=None, file=None, nl=True, err=False):
    """Print a message to stdout (or stderr), appending a newline."""
    if file is None:
        file = sys.stderr if err else sys.stdout
    text = make_str(message) if message is not None else ""
    if nl:
        text += "\n"
    file.write(text)
    file.flush()
