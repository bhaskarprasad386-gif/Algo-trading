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
            app_logger.info(f"Successfully downloaded {len(data)} instruments.")
            return data
        except Exception as e:
            app_logger.error(f"Failed to fetch instrument master: {e}")
            raise

    def sync_instruments(self):
        """Download and store/update instruments in the database."""
        instruments_data = self.fetch_master_json()
        
        with Session(engine) as session:
            try:
                count = 0
                for item in instruments_data:
                    token = item.get("token")
                    symbol = item.get("symbol")
                    name = item.get("name")
                    expiry = item.get("expiry")
                    strike = item.get("strike")
                    lotsize = item.get("lotsize")
                    instrumenttype = item.get("instrumenttype")
                    exch_seg = item.get("exch_seg")

                    existing = session.query(Instrument).filter_by(token=token).first()
                    if existing:
                        existing.symbol = symbol
                        existing.name = name
                        existing.expiry = expiry
                        existing.strike = strike
                        existing.lotsize = lotsize
                        existing.instrumenttype = instrumenttype
                        existing.exch_seg = exch_seg
                    else:
                        new_instrument = Instrument(
                            token=token,
                            symbol=symbol,
                            name=name,
                            expiry=expiry,
                            strike=strike,
                            lotsize=lotsize,
                            instrumenttype=instrumenttype,
                            exch_seg=exch_seg,
                        )
                        session.add(new_instrument)
                    
                    count += 1
                    if count % 5000 == 0:
                        session.commit()
                
                session.commit()
                app_logger.info(f"Successfully synced {count} instruments into database.")
                return {"status": "success", "total_synced": count}
            except Exception as e:
                session.rollback()
                app_logger.error(f"Error syncing instruments to database: {e}")
                raise
