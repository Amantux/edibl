"""Shopping-list helpers: export in a paste-friendly form and auto-suggest."""


def _fmt_qty(q, unit):
    q = q or 1
    q = int(q) if float(q).is_integer() else q
    if unit in ("count", "", None):
        return f"{q}x"
    return f"{q} {unit}"


def format_for_delivery(items) -> str:
    """One item per line, quantity-first — the shape Uber Eats / Instacart /
    grocery apps parse most reliably when you paste a list into their search.
    e.g.  '2x Whole milk'  /  '500 g Ground beef'."""
    lines = []
    for it in items:
        name = it.name.strip()
        note = f" ({it.note})" if it.note else ""
        lines.append(f"{_fmt_qty(it.quantity, it.unit)} {name}{note}".strip())
    return "\n".join(lines)
