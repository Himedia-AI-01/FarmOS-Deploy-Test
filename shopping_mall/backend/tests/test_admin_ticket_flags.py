"""Admin ticket flag response tests."""
import json
from unittest.mock import MagicMock

from app.routers.admin import _enrich_ticket, _parse_ticket_flags


def test_parse_ticket_flags_returns_valid_flags_only():
    raw = json.dumps([
        {
            "code": "high_value_review",
            "label": "5만원 이상 운영팀 우선 확인",
            "severity": "warning",
        },
        {"code": "broken"},
        "not-a-flag",
    ], ensure_ascii=False)

    flags = _parse_ticket_flags(raw)

    assert len(flags) == 1
    assert flags[0].code == "high_value_review"
    assert flags[0].label == "5만원 이상 운영팀 우선 확인"
    assert flags[0].severity == "warning"


def test_enrich_ticket_returns_flags_as_array():
    ticket = MagicMock()
    ticket.id = 7
    ticket.user_id = 99
    ticket.session_id = 1
    ticket.order_id = 17
    ticket.action_type = "exchange"
    ticket.reason = "신선도·품질 문제"
    ticket.refund_method = None
    ticket.items = None
    ticket.status = "received"
    ticket.created_at = "2026-05-04T12:00:00+09:00"
    ticket.flags = json.dumps([{
        "code": "high_value_review",
        "label": "5만원 이상 운영팀 우선 확인",
        "severity": "warning",
    }], ensure_ascii=False)

    user = MagicMock()
    user.name = "홍길동"
    order = MagicMock()
    order.total_price = 50000

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [user, order]

    response = _enrich_ticket(ticket, db)

    assert response.flags[0].code == "high_value_review"
    assert response.order_total == 50000
