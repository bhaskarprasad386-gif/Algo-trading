from types import SimpleNamespace

import app.main as main
from app.core.config import Settings


def test_box_spread_paper_auto_cycle_is_disabled_by_default():
    settings = Settings()
    assert settings.PAPER_BOX_SPREAD_AUTO_CYCLE_ENABLED is False
    assert settings.PAPER_BOX_SPREAD_AUTO_CYCLE_INTERVAL_SECONDS >= 1
    assert settings.PAPER_BOX_SPREAD_AUTO_CYCLE_MIN_PNL >= 0


def test_box_spread_paper_cycle_processes_only_active_paper_accounts(monkeypatch):
    class FakeQuery:
        def filter(self, *args):
            return self

        def all(self):
            return [
                SimpleNamespace(user_id=11, is_active=True, mode="PAPER", box_spread_auto_lots=1),
                SimpleNamespace(user_id=22, is_active=True, mode="PAPER", box_spread_auto_lots=1),
            ]

    class FakeDB:
        def query(self, model):
            assert model is main.TradingAccount
            return FakeQuery()

        def rollback(self):
            pass

        def close(self):
            pass

    calls = []

    monkeypatch.setattr(main, "SessionLocal", lambda: FakeDB())
    monkeypatch.setattr(
        main,
        "datetime",
        SimpleNamespace(now=lambda tz: SimpleNamespace(time=lambda: main.MARKET_OPEN)),
    )
    monkeypatch.setattr(
        main,
        "box_spread_paper_cycle",
        lambda **kwargs: calls.append(kwargs) or {"status": "hold"},
    )

    assert main._run_box_spread_paper_cycle_once() == 2
    assert [item["user"] for item in calls] == [11, 22]
    assert all(item["lots"] == 1 for item in calls)


def test_box_spread_paper_cycle_skips_outside_market_hours(monkeypatch):
    class FailSession:
        def __call__(self):
            raise AssertionError("database must not be opened outside market hours")

    monkeypatch.setattr(main, "SessionLocal", FailSession())
    monkeypatch.setattr(
        main,
        "datetime",
        SimpleNamespace(now=lambda tz: SimpleNamespace(time=lambda: main.MARKET_CLOSE.replace(hour=16))),
    )

    assert main._run_box_spread_paper_cycle_once() == 0
