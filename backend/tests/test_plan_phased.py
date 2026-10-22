"""Tests for time-phased demand reconciliation."""

from datetime import date, timedelta

from app.extensions import db
from app.models import StockLot, Product, Group
from app.services.planning import analyze_demand_phased


def test_one_butter_two_meals(app):
    """The one-butter-two-meals case: 1 kg on hand, two planned items of 1 kg
    with neededBy D+0 and D+3. Second item should shortfall with firstShortfallDate = D+3."""
    with app.app_context():
        # Create test group
        g = Group(name="test-group")
        db.session.add(g)
        db.session.commit()

        # Create butter product
        p = Product(name="Butter", group_id=g.id, default_unit="kg")
        db.session.add(p)
        db.session.commit()

        # Add 1 kg butter lot
        lot = StockLot(product_id=p.id, group_id=g.id, quantity=1, unit="kg")
        db.session.add(lot)
        db.session.commit()

        # Two meals need 1 kg butter each
        today = date.today()
        demand = [
            {
                "name": "Butter",
                "quantity": 1,
                "unit": "kg",
                "neededBy": today.isoformat(),
            },
            {
                "name": "Butter",
                "quantity": 1,
                "unit": "kg",
                "neededBy": (today + timedelta(days=3)).isoformat(),
            },
        ]

        result = analyze_demand_phased(g.id, demand)

        # First item should have enough
        assert result["items"][0]["name"] == "Butter"
        assert result["items"][0]["have"] is True
        assert result["items"][0]["onHand"] == 1.0
        assert result["items"][0]["shortfall"] == 0.0
        assert result["items"][0]["onHandAtNeed"] == 1.0
        assert "firstShortfallDate" not in result["items"][0]

        # Second item should shortfall
        assert result["items"][1]["name"] == "Butter"
        assert result["items"][1]["have"] is False
        assert result["items"][1]["onHand"] == 0.0
        assert result["items"][1]["shortfall"] == 1.0
        assert result["items"][1]["onHandAtNeed"] == 0.0
        assert (
            result["items"][1]["firstShortfallDate"]
            == (today + timedelta(days=3)).isoformat()
        )

        # Shortfall list should have one entry with neededBy
        assert len(result["shortfall"]) == 1
        assert result["shortfall"][0]["name"] == "Butter"
        assert result["shortfall"][0]["quantity"] == 1.0
        assert (
            result["shortfall"][0]["neededBy"]
            == (today + timedelta(days=3)).isoformat()
        )

        db.session.delete(g)
        db.session.commit()


def test_cross_dimension_lots_cannot_satisfy(app):
    """Cross-dimension lots (g vs ml) still cannot satisfy a demand they cannot be converted into."""
    with app.app_context():
        g = Group(name="test-group-2")
        db.session.add(g)
        db.session.commit()

        # Create olive oil product (typically measured in volume)
        p = Product(name="Olive Oil", group_id=g.id, default_unit="ml")
        db.session.add(p)
        db.session.commit()

        # Add 500g lot (weight-based, not volume)
        lot = StockLot(product_id=p.id, group_id=g.id, quantity=500, unit="g")
        db.session.add(lot)
        db.session.commit()

        # Demand in ml (volume) - cannot convert from g without density
        demand = [
            {"name": "Olive Oil", "quantity": 500, "unit": "ml"},
        ]

        result = analyze_demand_phased(g.id, demand)

        # Should not be able to satisfy (g vs ml is cross-dimension)
        assert result["items"][0]["have"] is False
        assert result["items"][0]["onHand"] == 0.0
        assert result["items"][0]["shortfall"] == 500.0

        db.session.delete(g)
        db.session.commit()


def test_undated_demand_back_compat(app):
    """Undated demand produces keys identical to analyze_demand's current output."""
    with app.app_context():
        g = Group(name="test-group-3")
        db.session.add(g)
        db.session.commit()

        p = Product(name="Milk", group_id=g.id, default_unit="l")
        db.session.add(p)
        db.session.commit()

        lot = StockLot(product_id=p.id, group_id=g.id, quantity=2, unit="l")
        db.session.add(lot)
        db.session.commit()

        # Undated demand
        demand = [
            {"name": "Milk", "quantity": 1, "unit": "l"},
        ]

        result = analyze_demand_phased(g.id, demand)

        # Should have the same keys as the original analyze_demand
        assert "name" in result["items"][0]
        assert "need" in result["items"][0]
        assert "unit" in result["items"][0]
        assert "onHand" in result["items"][0]
        assert "onHandUnit" in result["items"][0]
        assert "have" in result["items"][0]
        assert "shortfall" in result["items"][0]
        assert "expiryConcern" in result["items"][0]

        # Should NOT have phased-specific keys for undated items
        assert "onHandAtNeed" not in result["items"][0]
        assert "firstShortfallDate" not in result["items"][0]

        db.session.delete(g)
        db.session.commit()


def test_plan_order_writes_needed_by_note(app):
    """POST /plan/order writes 'needed by YYYY-MM-DD' notes on dated shortfall shopping items."""
    with app.app_context():
        g = Group(name="test-group-4")
        db.session.add(g)
        db.session.commit()

        p = Product(name="Cheese", group_id=g.id, default_unit="kg")
        db.session.add(p)
        db.session.commit()

        # Only 0.5 kg on hand
        lot = StockLot(product_id=p.id, group_id=g.id, quantity=0.5, unit="kg")
        db.session.add(lot)
        db.session.commit()

        from app.api.integrations import add_shortfall_to_shopping

        today = date.today()
        demand = [
            {
                "name": "Cheese",
                "quantity": 1,
                "unit": "kg",
                "neededBy": today.isoformat(),
            },
        ]

        result = add_shortfall_to_shopping(g.id, demand)

        assert len(result) == 1
        assert result[0].note == f"needed by {today.isoformat()}"

        db.session.delete(g)
        db.session.commit()


def test_satisfied_item_no_note(app):
    """Satisfied items should not get a 'needed by' note."""
    with app.app_context():
        g = Group(name="test-group-5")
        db.session.add(g)
        db.session.commit()

        p = Product(name="Eggs", group_id=g.id, default_unit="count")
        db.session.add(p)
        db.session.commit()

        # 12 eggs on hand
        lot = StockLot(product_id=p.id, group_id=g.id, quantity=12, unit="count")
        db.session.add(lot)
        db.session.commit()

        from app.api.integrations import add_shortfall_to_shopping

        today = date.today()
        demand = [
            {
                "name": "Eggs",
                "quantity": 6,
                "unit": "count",
                "neededBy": today.isoformat(),
            },
        ]

        result = add_shortfall_to_shopping(g.id, demand)

        # No shortfall, so no items added
        assert len(result) == 0

        db.session.delete(g)
        db.session.commit()
