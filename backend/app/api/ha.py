"""Home Assistant sensor feed + notification trigger.

`GET /ha/sensors` — a flat snapshot the Edibl HACS integration (or an HA `rest`
sensor) polls to expose entities (items in stock, expiring soon, to restock, …).
`POST /ha/notify` — push a "use soon / restock" persistent notification to HA; wire
an HA automation (e.g. daily at 17:00) to call it.
"""
from flask import Blueprint, jsonify

from ..auth import login_required, current_group
from ..services import ha

bp = Blueprint("ha", __name__)


@bp.get("/ha/sensors")
@login_required
def sensors():
    return jsonify(ha.sensor_snapshot(current_group().id))


@bp.post("/ha/notify")
@login_required
def notify():
    """Trigger a Home Assistant persistent notification summarizing what needs
    attention. Returns {sent, ...}. No-op with a reason when nothing to report or
    not running under the Supervisor."""
    return jsonify(ha.push_notification(current_group().id))
