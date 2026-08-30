import requests
from typing import Any, Dict, List, Optional

from app.core.exceptions import TradingAppException
from app.core.logger import app_logger


class InstrumentMaster:
    """Angel One instrument master manager."""

    MASTER_URL = (
        "https://margincalculator.angelbroking.com/"
        "OpenAPI_File/files/OpenAPIScripMaster.json"
    )

    def __init__(self):
        self.instruments: List[Dict[str, Any]] = []

    def download(self) -> List[Dict[str, Any]]:
        """Download the latest Angel One instrument master."""

        try:
            response = requests.get(
                self.MASTER_URL,
                timeout=30,
            )
            response.raise_for_status()

            data = response.json()

            if not isinstance(data, list):
                raise TradingAppException(
                    "InvalidInstrumentMaster",
                    "Angel One instrument master format is invalid.",
                    502,
                )

            self.instruments = data

            app_logger.info(
                f"Loaded {len(self.instruments)} instruments "
                "from Angel One instrument master"
            )

            return self.instruments

        except TradingAppException:
            raise

        except Exception as e:
            app_logger.error(
                f"Failed to download instrument master: {str(e)}"
            )
            raise TradingAppException(
                "InstrumentMasterDownloadError",
                str(e),
                502,
            )

    def search(
        self,
        tradingsymbol: Optional[str] = None,
        exchange: Optional[str] = None,
        symboltoken: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Search instruments by symbol, exchange, or token."""

        if not self.instruments:
            self.download()

        results = self.instruments

        if tradingsymbol:
            results = [
                item
                for item in results
                if item.get("symbol", "").upper()
                == tradingsymbol.upper()
            ]

        if exchange:
            results = [
                item
                for item in results
                if item.get("exch_seg", "").upper()
                == exchange.upper()
            ]

        if symboltoken:
            results = [
                item
                for item in results
                if str(item.get("token", "")) == str(symboltoken)
            ]

        return results

    def get_token(
        self,
        tradingsymbol: str,
        exchange: str,
    ) -> Optional[str]:
        """Return the Angel One token for a trading symbol."""

        results = self.search(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
        )

        if not results:
            return None

        return str(results[0].get("token"))

    def get_instrument(
        self,
        tradingsymbol: str,
        exchange: str,
    ) -> Optional[Dict[str, Any]]:
        """Return complete instrument information."""

        results = self.search(
            tradingsymbol=tradingsymbol,
            exchange=exchange,
        )

        return results[0] if results else None
