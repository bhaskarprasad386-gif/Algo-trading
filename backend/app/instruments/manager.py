import math

import requests
from sqlalchemy.orm import Session
from app.core.logger import app_logger
from app.core.database import engine
from app.models.instrument import Instrument


class InstrumentManager:
    """Manager to download, parse, and sync Angel One instrument master list."""

    SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

    def fetch_master_json(self):
        """Fetch the latest scrip master json from Angel One."""
        try:
            app_logger.info("Downloading Angel One Instrument Master list...")
            response = requests.get(self.SCRIP_MASTER_URL, timeout=30)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("instrument master response must be a JSON list")
            if any(not isinstance(item, dict) for item in data):
                raise ValueError("instrument master response contains a non-object item")
            app_logger.info(f"Successfully downloaded {len(data)} instruments.")
            return data
        except Exception as e:
            app_logger.error(f"Failed to fetch instrument master: {e}")
            raise

    @staticmethod
    def _positive_number(value, name: str, *, integer: bool = False):
        if value is None:
            raise ValueError(f"{name} is required")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be numeric") from exc
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(f"{name} must be finite and positive")
        if integer:
            if numeric != int(numeric):
                raise ValueError(f"{name} must be an integer")
            return int(numeric)
        return numeric

    def sync_instruments(self):
        """Download and store/update instruments in the database atomically."""
        instruments_data = self.fetch_master_json()

        with Session(engine) as session:
            try:
                count = 0
                for item in instruments_data:
                    token = item.get("token")
                    symbol = item.get("symbol")
                    name = item.get("name")
                    exchange = item.get("exch_seg")
                    instrument_type = item.get("instrumenttype")
                    segment = item.get("segment") or exchange
                    raw_lot_size = item.get("lotsize")
                    raw_tick_size = item.get("tick_size")
                    lot_size = self._positive_number(raw_lot_size if raw_lot_size is not None else 1, "lot_size", integer=True)
                    tick_size = self._positive_number(raw_tick_size if raw_tick_size is not None else 0.05, "tick_size")
                    expiry = item.get("expiry")
                    strike = item.get("strike")

                    if not token or not symbol or not exchange:
                        continue
                    if not segment:
                        raise ValueError(f"missing segment for instrument token {token}")

                    existing = session.query(Instrument).filter_by(token=str(token)).first()
                    if existing:
                        existing.symbol = str(symbol)
                        existing.name = name
                        existing.exchange = str(exchange)
                        existing.instrument_type = instrument_type
                        existing.segment = segment
                        existing.lot_size = lot_size
                        existing.tick_size = tick_size
                        existing.expiry = expiry
                        existing.strike = strike
                    else:
                        session.add(Instrument(
                            token=str(token),
                            symbol=str(symbol),
                            name=name,
                            exchange=str(exchange),
                            instrument_type=instrument_type,
                            segment=segment,
                            lot_size=lot_size,
                            tick_size=tick_size,
                            expiry=expiry,
                            strike=strike,
                        ))

                    count += 1

                # One transaction prevents a failed/invalid master from leaving
                # a partially refreshed instrument universe in the database.
                session.commit()
                app_logger.info(f"Successfully synced {count} instruments into database.")
                return {"status": "success", "total_synced": count}
            except Exception as e:
                session.rollback()
                app_logger.error(f"Error syncing instruments to database: {e}")
                raise
