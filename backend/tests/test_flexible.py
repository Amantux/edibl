"""User-driven categories/units/freshness, product grouping (family), the
suggestions feed, a source field, and agent-style stock CRUD."""


def _add(client, name, **kw):
    return client.post("/api/v1/stock", json={"productName": name, **kw}).get_json()


# --- free-form category / unit / freshness ---------------------------------
def test_freeform_category_and_unit_accepted(auth_client):
    lot = _add(auth_client, "Dragonfruit", category="exotic-fruit", unit="each",
               quantity=3, storageMethod="refrigerated")
    assert lot["product"]["category"] == "exotic-fruit"
    assert lot["unit"] == "each"
    # custom category still auto-estimates via the storage-only fallback
    assert lot["expiryEstimated"] is True and lot["daysToExpiry"] is not None


def test_freeform_category_via_product_update(auth_client):
    lot = _add(auth_client, "Kimchi", category="ferments")
    pid = lot["product"]["id"]
    r = auth_client.put(f"/api/v1/products/{pid}", json={"category": "korean-sides"})
    assert r.get_json()["category"] == "korean-sides"


def test_freshness_field_freeform(auth_client):
    lot = _add(auth_client, "Peach", category="produce", freshness="a little soft")
    assert lot["freshness"] == "a little soft" and lot["state"] == "a little soft"
    upd = auth_client.put(f"/api/v1/stock/{lot['id']}", json={"freshness": "very ripe"}).get_json()
    assert upd["freshness"] == "very ripe"


def test_source_field(auth_client):
    lot = _add(auth_client, "Eggs", category="dairy", source="Farmer's market")
    assert lot["source"] == "Farmer's market"


# --- product grouping (family) ----------------------------------------------
def test_grouped_stock_families_and_lots(auth_client):
    _add(auth_client, "Organic milk", category="dairy", family="Milk", quantity=1, unit="l")
    _add(auth_client, "Organic milk", family="Milk", quantity=1, unit="l")   # 2nd buy date
    _add(auth_client, "Filtered milk", category="dairy", family="Milk", quantity=2, unit="l")
    _add(auth_client, "Cheddar", category="dairy", quantity=1)               # its own group
    g = auth_client.get("/api/v1/stock/grouped").get_json()["groups"]
    by = {x["group"]: x for x in g}
    assert "Milk" in by and "Cheddar" in by
    milk = by["Milk"]
    assert milk["totalQuantity"] == 4.0 and milk["lotCount"] == 3
    assert milk["productCount"] == 2
    assert set(milk["products"]) == {"Organic milk", "Filtered milk"}
    # each lot keeps its own product + expiry
    assert len(milk["lots"]) == 3


def test_family_assigned_when_product_first_seen(auth_client):
    _add(auth_client, "Basil", category="produce")            # no family yet
    _add(auth_client, "Basil", family="Herbs")                # assigns family
    g = auth_client.get("/api/v1/stock/grouped").get_json()["groups"]
    assert any(x["group"] == "Herbs" for x in g)


# --- suggestions feed -------------------------------------------------------
def test_suggestions_merges_seeds_and_in_use(auth_client):
    _add(auth_client, "Dragonfruit", category="exotic-fruit", unit="each", family="Fruit")
    s = auth_client.get("/api/v1/products/suggestions").get_json()
    assert "dairy" in s["categories"] and "exotic-fruit" in s["categories"]   # seed + custom
    assert "each" in s["units"]
    assert "Fruit" in s["families"]
    assert "Dragonfruit" in s["names"]
    assert "fresh" in s["freshness"]


# --- export / import --------------------------------------------------------
def test_export_snapshot_and_csv(auth_client):
    loc = auth_client.post("/api/v1/locations",
                           json={"name": "Fridge", "kind": "fridge"}).get_json()
    _add(auth_client, "Milk", category="dairy", family="Milk", quantity=2, unit="l",
         locationId=loc["id"], source="Costco")
    auth_client.post("/api/v1/shopping", json={"name": "Eggs", "quantity": 12})
    exp = auth_client.get("/api/v1/export").get_json()
    assert exp["edibl"] == "1"
    assert len(exp["stock"]) == 1 and len(exp["shopping"]) == 1 and len(exp["locations"]) == 1
    csv = auth_client.get("/api/v1/export/stock.csv")
    assert csv.status_code == 200
    text = csv.get_data(as_text=True)
    assert text.splitlines()[0].startswith("product,group,category")
    assert "Milk" in text


def test_import_creates_and_is_additive(auth_client):
    snap = {
        "products": [{"name": "Tofu", "category": "protein", "family": "Soy"}],
        "locations": [{"name": "Pantry", "kind": "pantry"}],
        "stock": [{"product": {"name": "Tofu", "category": "protein"},
                   "quantity": 3, "unit": "block", "storageMethod": "refrigerated",
                   "location": {"name": "Pantry"}}],
        "shopping": [{"name": "Rice", "quantity": 1, "status": "needed"}],
    }
    imp = auth_client.post("/api/v1/import", json=snap).get_json()["imported"]
    assert imp == {"products": 1, "locations": 1, "stock": 1, "shopping": 1}
    items = auth_client.get("/api/v1/stock").get_json()["items"]
    assert any(i["product"]["name"] == "Tofu" for i in items)
    # re-import: additive — products/locations/shopping deduped, only stock appends
    imp2 = auth_client.post("/api/v1/import", json=snap).get_json()["imported"]
    assert imp2["products"] == 0 and imp2["locations"] == 0 and imp2["shopping"] == 0
    assert imp2["stock"] == 1


# --- agent-style CRUD via the REST surface ----------------------------------
def test_stock_update_and_delete(auth_client):
    lot = _add(auth_client, "Butter", category="dairy", quantity=2, unit="stick")
    upd = auth_client.put(f"/api/v1/stock/{lot['id']}",
                          json={"quantity": 5, "freshness": "opened", "source": "Aldi"}).get_json()
    assert upd["quantity"] == 5 and upd["freshness"] == "opened" and upd["source"] == "Aldi"
    assert auth_client.delete(f"/api/v1/stock/{lot['id']}").status_code == 204
    assert auth_client.get(f"/api/v1/stock/{lot['id']}").status_code == 404
