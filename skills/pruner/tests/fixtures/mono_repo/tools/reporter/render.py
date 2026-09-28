def format_filename(path):
    """Render a report path relative to the reports directory."""
    return path.split("reports/", 1)[-1]


def render_table(rows, width=60):
    """Render rows of (label, value) pairs as a fixed-width text table."""
    out = []
    for label, value in rows:
        out.append(f"{label:<20}{value:>{width - 20}}")
    return "\n".join(out)
