import json

from clicky import command, echo, option

from .render import format_filename, render_table


def load_rows(path):
    with open(path) as fh:
        data = json.load(fh)
    return [(k, data[k]) for k in sorted(data)]


@command()
@option("--input", required=True, help="JSON file with the metrics")
@option("--width", default="60", help="table width")
def run(input, width):
    rows = load_rows(input)
    echo(format_filename(input))
    echo(render_table(rows, width=int(width)))
