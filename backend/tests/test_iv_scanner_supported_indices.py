from app.scanner.iv_skew_flip.runtime import CHAIN_CONFIGS
from app.scanner.iv_skew_flip.spot_feed import IVSpotFeedAdapter
from app.scanner.iv_skew_flip.basket_registry import SUPPORTED_UNDERLYINGS


def test_iv_scanner_only_configures_nifty_and_banknifty_option_streams():
    assert {config.symbol for _, config in CHAIN_CONFIGS} == {"NIFTY", "BANKNIFTY"}
    assert {stream for stream, _ in CHAIN_CONFIGS} == {
        "NIFTY_WEEKLY",
        "NIFTY_MONTHLY",
        "BANKNIFTY_MONTHLY",
    }


def test_iv_scanner_spot_and_equity_baskets_only_allow_nifty_and_banknifty():
    assert IVSpotFeedAdapter.DEFAULT_SYMBOLS == ("NIFTY", "BANKNIFTY")
    assert set(SUPPORTED_UNDERLYINGS) == {"NIFTY", "BANKNIFTY"}
