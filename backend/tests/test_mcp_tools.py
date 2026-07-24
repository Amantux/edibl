"""The Edibl MCP server exposes the expected tools (incl. the search/describe ones
that surface the AI product descriptions), and doesn't expose admin surfaces."""
import asyncio


def test_expected_tools_registered():
    import edibl_mcp

    tools = {t.name for t in asyncio.run(edibl_mcp.mcp.list_tools())}
    for expected in (
        "do_i_have", "whats_in_stock", "add_stock", "update_stock", "move_stock",
        "use_stock", "record_consumption", "shopping_list", "add_to_shopping_list",
        # newly surfaced: AI search/describe + reorder policy
        "search_products", "describe_product", "reorder_suggestions",
    ):
        assert expected in tools
    # Admin / migration / key management stay off the agent surface.
    assert "migrate_postgres" not in tools
    assert not any("token" in t or "migrate" in t for t in tools)


def test_move_stock_unknown_location_does_not_silently_unassign(monkeypatch):
    # Regression: a typo'd location used to move the lot to NO location and report
    # success. It must now error and perform no move.
    import edibl_mcp as m
    monkeypatch.setattr(m, "_find_lot",
                        lambda name: {"id": "1", "product": {"name": "Milk"}, "unit": "l"})
    monkeypatch.setattr(m, "_location_id", lambda loc: None)  # not found
    calls = []
    monkeypatch.setattr(m, "_post", lambda *a, **k: calls.append(a) or {})

    out = m.move_stock("Milk", "Nowhere")

    assert "No location" in out and not calls  # errored, no move performed


def test_use_stock_unknown_name_returns_friendly_message(monkeypatch):
    # Regression: /stock/consume 404s for an unknown name; the raw HTTP error used
    # to surface instead of a friendly message.
    from types import SimpleNamespace

    import edibl_mcp as m
    import httpx

    def boom(*a, **k):
        raise httpx.HTTPStatusError("not found", request=None,
                                    response=SimpleNamespace(status_code=404))
    monkeypatch.setattr(m, "_post", boom)

    assert "No stock matching" in m.use_stock("Nope", 1)
