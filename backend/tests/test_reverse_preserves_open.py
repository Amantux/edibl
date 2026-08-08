"""Reversing a non-consume event must not touch the package's open state.

The reverse dispatch had `if type == "consume": ... else: # open`, so the
`else` ran for adjust/move/split/merge/freeze/thaw too — unconditionally
re-sealing the lot and wiping opened_date before the real type-specific block
ran. Corrupts prefer-open FEFO, the open-packages count, and opened-based
shelf life.
"""


def _add(c, name="Rice", quantity=5, unit="kg"):
    return c.post("/api/v1/stock",
                  json={"name": name, "quantity": quantity, "unit": unit,
                        "category": "dry_goods"}).get_json()


def test_reversing_an_adjust_keeps_the_package_opened(auth_client):
    lot = _add(auth_client)
    auth_client.post(f"/api/v1/stock/{lot['id']}/open", json={})
    opened = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert opened["packageState"] == "opened"
    assert opened.get("openedDate")

    adj = auth_client.post(f"/api/v1/stock/{lot['id']}/adjust",
                           json={"quantity": 3}).get_json()
    auth_client.post(f"/api/v1/inventory/events/{adj['eventId']}/reverse")

    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["packageState"] == "opened", "reversing the adjust re-sealed it"
    assert after.get("openedDate"), "reversing the adjust wiped opened_date"
    assert after["quantity"] == 5   # the adjust itself was reverted


def test_reversing_a_move_keeps_the_package_opened(auth_client):
    lot = _add(auth_client)
    auth_client.post(f"/api/v1/stock/{lot['id']}/open", json={})
    loc = auth_client.post("/api/v1/locations",
                           json={"name": "Shelf", "kind": "pantry"}).get_json()
    mv = auth_client.post(f"/api/v1/stock/{lot['id']}/move",
                          json={"locationId": loc["id"]}).get_json()
    auth_client.post(f"/api/v1/inventory/events/{mv['eventId']}/reverse")

    after = auth_client.get(f"/api/v1/stock/{lot['id']}").get_json()
    assert after["packageState"] == "opened"
