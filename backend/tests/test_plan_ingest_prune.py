"""A push is the authoritative statement of what a recipe needs.

`sourceRef` names the RECIPE and is shared by all of its ingredients, so `name`
has to be part of the upsert key to tell them apart. That means a renamed
ingredient does not match its old row — and without pruning, both rows survive
and the recipe's demand silently doubles.
"""
import pytest


def push(client, items, **kw):
    return client.post("/api/v1/integrations/mymeal/plan",
                       json={"items": items, **kw})


def planned(client, ref=None):
    rows = client.get("/api/v1/plan").get_json()["planned"]
    return [r for r in rows if ref is None or r.get("sourceRef") == ref]


R1 = "mymeal:recipe:R1"
R2 = "mymeal:recipe:R2"


def test_a_renamed_ingredient_replaces_its_old_row(auth_client):
    """The bug this fixes: 'flour' → 'plain flour' used to leave both, so the
    plan asked for 400 g of flour and the shopping order had two lines."""
    push(auth_client, [{"name": "flour", "quantity": 200, "unit": "g",
                        "sourceRef": R1}])

    r = push(auth_client, [{"name": "plain flour", "quantity": 200, "unit": "g",
                            "sourceRef": R1}])

    assert r.get_json()["pruned"] == 1
    rows = planned(auth_client, R1)
    assert [x["name"] for x in rows] == ["plain flour"]


def test_an_unchanged_repush_prunes_nothing(auth_client):
    """The common case — pushing the same plan twice must be a no-op."""
    items = [{"name": "cinnamon", "quantity": 2, "unit": "tsp", "sourceRef": R1},
             {"name": "salt", "quantity": 1, "unit": "tsp", "sourceRef": R1}]
    push(auth_client, items)

    r = push(auth_client, items)

    assert r.get_json()["pruned"] == 0
    assert len(planned(auth_client, R1)) == 2


def test_a_dropped_ingredient_is_removed(auth_client):
    """Editing an ingredient out of a recipe should stop it being bought."""
    push(auth_client, [{"name": "cinnamon", "quantity": 2, "sourceRef": R1},
                       {"name": "nutmeg", "quantity": 1, "sourceRef": R1}])

    push(auth_client, [{"name": "cinnamon", "quantity": 2, "sourceRef": R1}])

    assert [x["name"] for x in planned(auth_client, R1)] == ["cinnamon"]


def test_a_recipe_not_in_this_push_is_untouched(auth_client):
    """Pushes are windowed and per-plan; pruning must be scoped to what was
    actually sent, or one recipe's push wipes another's demand."""
    push(auth_client, [{"name": "cinnamon", "quantity": 2, "sourceRef": R1}])
    push(auth_client, [{"name": "butter", "quantity": 100, "sourceRef": R2}])

    push(auth_client, [{"name": "cassia", "quantity": 2, "sourceRef": R1}])

    assert [x["name"] for x in planned(auth_client, R2)] == ["butter"]


def test_items_with_no_source_ref_are_never_pruned(auth_client):
    """Hand-added demand carries no sourceRef. A mass-delete of those would be
    the worst possible failure mode of this change."""
    push(auth_client, [{"name": "hand added", "quantity": 1}])

    push(auth_client, [{"name": "cinnamon", "quantity": 2, "sourceRef": R1}])

    assert any(x["name"] == "hand added" for x in planned(auth_client))


def test_pruning_is_scoped_to_the_household(auth_client):
    """The delete is a filtered bulk DELETE — the group filter is the only thing
    stopping it reaching another household's plan.

    NOTE: `auth_client` IS `client` with a header set, so a second household is
    made by swapping the header on the same client and swapping back. Taking two
    fixtures instead silently tests one user against themselves — which is how
    this test failed the first time, on the test rather than the code."""
    a_token = auth_client.environ_base["HTTP_AUTHORIZATION"]
    push(auth_client, [{"name": "flour", "quantity": 200, "sourceRef": R1}])

    auth_client.post("/api/v1/users/register",
                     json={"email": "b@b.com", "password": "password2", "name": "B"})
    b_token = auth_client.post(
        "/api/v1/users/login",
        json={"email": "b@b.com", "password": "password2"}).get_json()["token"]
    auth_client.environ_base["HTTP_AUTHORIZATION"] = b_token
    push(auth_client, [{"name": "sugar", "quantity": 50, "sourceRef": R1}])
    assert [x["name"] for x in planned(auth_client, R1)] == ["sugar"]

    auth_client.environ_base["HTTP_AUTHORIZATION"] = a_token
    assert [x["name"] for x in planned(auth_client, R1)] == ["flour"]


@pytest.mark.parametrize("bad", [[], None])
def test_an_empty_push_changes_nothing(auth_client, bad):
    """A 422 must not be a way to silently clear the plan."""
    push(auth_client, [{"name": "cinnamon", "quantity": 2, "sourceRef": R1}])

    auth_client.post("/api/v1/integrations/mymeal/plan", json={"items": bad})

    assert len(planned(auth_client, R1)) == 1
