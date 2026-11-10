from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.iv_option_observation import IVOptionObservation
from app.scanner.iv_skew_flip.angelone_option_snapshot import capture_angelone_option_snapshot


class FakeAngelOneClient:
    def option_greeks(self, underlying, expirydate):
        assert underlying == "NIFTY"
        assert expirydate == "17NOV2026"
        return {
            "status": True,
            "data": [
                {
                    "name": "NIFTY",
                    "expiry": "17NOV2026",
                    "strikePrice": "25000.000000",
                    "optionType": "CE",
                    "impliedVolatility": "14.250000",
                    "delta": "0.51",
                    "tradeVolume": "1000",
                }
            ],
        }

    def quote_many(self, exchange, instruments):
        assert exchange == "NFO"
        assert len(instruments) == 2
        return {
            "status": True,
            "data": {
                "fetched": [
                    {
                        "symbolToken": "ce-token",
                        "ltp": 125.5,
                        "opnInterest": 123456,
                        "tradeVolume": 1100,
                        "depth": {
                            "buy": [{"price": 125.0, "quantity": 10}],
                            "sell": [{"price": 126.0, "quantity": 20}],
                        },
                    }
                ],
                "unfetched": [],
            },
        }


def test_snapshot_persists_broker_iv_without_calculating_it():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[IVOptionObservation.__table__])
    Session = sessionmaker(bind=engine, autoflush=False)
    universe = SimpleNamespace(
        underlying="NIFTY",
        expiry="2026-11-17",
        expiry_type="WEEKLY",
        option_exchange="NFO",
        contracts=(
            SimpleNamespace(token="ce-token", strike=25000.0, option_type="CE"),
            SimpleNamespace(token="pe-token", strike=25000.0, option_type="PE"),
        ),
    )
    captured_ns = 1_795_000_000_000_000_000
    spot = SimpleNamespace(price=25010.0, timestamp_ns=captured_ns - 1_000_000_000)
    result = capture_angelone_option_snapshot(
        universe=universe,
        spot_snapshot=spot,
        client=FakeAngelOneClient(),
        session_factory=Session,
        captured_at_ns=captured_ns,
    )
    assert result["saved_contracts"] == 2
    assert result["iv_received_contracts"] == 1
    assert result["full_quote_received_contracts"] == 1
    with Session() as db:
        rows = db.query(IVOptionObservation).order_by(IVOptionObservation.option_type).all()
        ce, pe = rows
        assert ce.iv == 14.25  # Exactly the Angel One response; no local IV solver.
        assert ce.iv_source == "ANGEL_ONE"
        assert ce.ltp == 125.5
        assert ce.oi == 123456
        assert ce.volume == 1100
        assert ce.bid == 125.0
        assert ce.ask == 126.0
        assert pe.iv is None
        assert pe.ltp is None
        assert pe.oi is None
        assert ce.spot_price == 25010.0
        assert ce.spot_timestamp_ns == captured_ns - 1_000_000_000
        assert ce.source_timestamp_ns == captured_ns
    engine.dispose()


def test_snapshot_keeps_broker_iv_if_full_quote_request_fails():
    class GreeksOnlyClient:
        def option_greeks(self, underlying, expirydate):
            return {"status": True, "data": [{
                "strikePrice": "25000", "optionType": "PE",
                "impliedVolatility": "18.75",
            }]}

        def quote_many(self, exchange, instruments):
            raise RuntimeError("FULL quote temporarily unavailable")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=[IVOptionObservation.__table__])
    Session = sessionmaker(bind=engine, autoflush=False)
    universe = SimpleNamespace(
        underlying="NIFTY", expiry="2026-11-17", expiry_type="WEEKLY",
        option_exchange="NFO",
        contracts=(SimpleNamespace(token="pe-token", strike=25000.0, option_type="PE"),),
    )
    result = capture_angelone_option_snapshot(
        universe=universe, spot_snapshot=None, client=GreeksOnlyClient(),
        session_factory=Session, captured_at_ns=1_795_000_000_000_000_001,
    )
    assert "FULL quote temporarily unavailable" in result["quote_error"]
    with Session() as db:
        row = db.query(IVOptionObservation).one()
        assert row.iv == 18.75
        assert row.ltp is None
        assert row.oi is None
        assert row.spot_price is None
        assert row.spot_timestamp_ns == 0
    engine.dispose()
