"""Barcode identification chain (Open Food Facts → product DB → web search).

External HTTP is monkeypatched — no network here; the chain ordering, parsing, and
the /products/barcode endpoint are what's covered.
"""
import httpx

from app.services import barcode


def test_lookup_disabled_returns_none(app):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = False
        assert barcode.lookup_barcode("012345678905") is None


def test_off_hit_short_circuits(app, monkeypatch):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr(barcode, "_from_off",
                            lambda c: {"name": "Milk", "source": "openfoodfacts"})
        seen = []
        monkeypatch.setattr(barcode, "_from_product_db", lambda c: seen.append("db"))
        res = barcode.lookup_barcode("1")
        assert res["source"] == "openfoodfacts" and not seen  # DB never consulted


def test_falls_through_to_product_db(app, monkeypatch):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr(barcode, "_from_off", lambda c: None)
        monkeypatch.setattr(barcode, "_from_product_db",
                            lambda c: {"name": "Drill", "source": "productdb"})
        assert barcode.lookup_barcode("1")["source"] == "productdb"


def test_web_search_is_last_resort(app, monkeypatch):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = True
        monkeypatch.setattr(barcode, "_from_off", lambda c: None)
        monkeypatch.setattr(barcode, "_from_product_db", lambda c: None)
        monkeypatch.setattr(barcode, "_from_web_search",
                            lambda c: {"name": "X", "source": "websearch"})
        assert barcode.lookup_barcode("1")["source"] == "websearch"


def test_all_sources_miss_returns_none(app, monkeypatch):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = True
        for fn in ("_from_off", "_from_product_db", "_from_web_search"):
            monkeypatch.setattr(barcode, fn, lambda c: None)
        assert barcode.lookup_barcode("1") is None


def test_product_db_parses_response(app, monkeypatch):
    with app.app_context():
        app.config["BARCODE_LOOKUP"] = True

        class _R:
            def raise_for_status(self): pass
            def json(self):
                return {"items": [{"title": "DeWalt Drill", "brand": "DeWalt",
                                   "category": "Tools & Hardware"}]}
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())
        r = barcode._from_product_db("012345678905")
        assert r["name"] == "DeWalt Drill" and r["brand"] == "DeWalt"
        assert r["source"] == "productdb" and r["barcode"] == "012345678905"


def test_by_barcode_endpoint_returns_suggestion(app, auth_client, monkeypatch):
    from app.services import barcode as bc
    monkeypatch.setattr(bc, "lookup_barcode",
                        lambda code: {"name": "Drill", "brand": "DeWalt",
                                      "category": "other", "barcode": code, "source": "productdb"})
    r = auth_client.get("/api/v1/products/barcode/999").get_json()
    assert r["found"] is False and r["suggestion"]["name"] == "Drill"


def test_clean_title_extracts_product_segment():
    assert barcode._clean_title("UPC 0123 | Barcode Lookup", "0123") is None
    assert (barcode._clean_title("Heinz Ketchup 32oz | Walmart", "0")
            == "Heinz Ketchup 32oz")


def test_web_search_prefers_clean_segment(app, monkeypatch):
    with app.app_context():
        from app.services import enrich
        monkeypatch.setattr(enrich, "enabled", lambda: True)
        monkeypatch.setattr(enrich, "_search_key", lambda: "k")
        monkeypatch.setattr(enrich, "web_search", lambda q, key: [
            {"title": "UPC 012345678905 | Barcode Lookup"},   # junk → skipped
            {"title": "Organic Whole Milk 1qt | Amazon"},     # real product
        ])
        monkeypatch.setattr(enrich, "extract_product", lambda results: None)  # no LLM
        hit = barcode._from_web_search("012345678905")
    assert hit["name"] == "Organic Whole Milk 1qt" and hit["source"] == "websearch"


def test_web_search_uses_llm_extraction(app, monkeypatch):
    with app.app_context():
        from app.services import enrich
        monkeypatch.setattr(enrich, "enabled", lambda: True)
        monkeypatch.setattr(enrich, "_search_key", lambda: "k")
        monkeypatch.setattr(enrich, "web_search",
                            lambda q, key: [{"title": "junk", "url": "http://upcitemdb.com/x"}])
        monkeypatch.setattr(enrich, "extract_product",
                            lambda results: {"name": "Heinz Ketchup", "brand": "Heinz"})
        hit = barcode._from_web_search("012345678905")
    assert hit["name"] == "Heinz Ketchup" and hit["brand"] == "Heinz"
    assert hit["source"] == "websearch" and hit["category"] == "other"


def test_rank_results_sinks_aggregators():
    from app.services import enrich
    ranked = enrich.rank_results([{"url": "http://barcodelookup.com/a"},
                                  {"url": "http://target.com/b"}])
    assert "target" in ranked[0]["url"]  # retailer ranked ahead of the aggregator
