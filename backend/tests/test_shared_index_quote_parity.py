from datetime import date
from app.market_data.provider_contracts import CanonicalContractKey, BrokerContractToken, ContractMapping
from app.market_data.shared_index_quote_parity import SharedIndexQuoteParity, QuoteSourceObservation


def key():
    return CanonicalContractKey("NFO", "NFO", "NIFTY", "OPTION", date(2026,11,26), "25000", "CE")


def mapping(broker, token):
    return ContractMapping(key(), BrokerContractToken(broker, "NFO", "NFO", token),
        "NIFTY26NOV25000CE", 65, "0.05", True)


def obs(broker, token, ltp, recv, epoch=2, exchange_ts=1_000_000, auth=True):
    return QuoteSourceObservation(broker, key(), ltp, ltp-0.05 if ltp else None, ltp+0.05 if ltp else None,
        "NFO", "NFO", token, exchange_ts, recv, epoch, auth)


def test_parity_ok_when_both_sources_fresh_within_one_tick():
    monitor = SharedIndexQuoteParity([mapping("ANGELONE", "a1"), mapping("SHOONYA", "s1")])
    monitor.observe(obs("ANGELONE", "a1", 100.0, 1_000_000_000))
    status = monitor.observe(obs("SHOONYA", "s1", 100.04, 1_010_000_000))
    assert status.state == "PARITY_OK"
    assert status.ltp_delta == 0.04
    assert status.sources_fresh == ("ANGELONE", "SHOONYA")


def test_parity_mismatch_and_stale_single_source_are_distinguished():
    monitor = SharedIndexQuoteParity([mapping("ANGELONE", "a1"), mapping("SHOONYA", "s1")])
    status = monitor.observe(obs("ANGELONE", "a1", 100, 1_000_000_000))
    assert status.state == "SINGLE_SOURCE_FRESH"
    status = monitor.observe(obs("SHOONYA", "s1", 101, 1_010_000_000))
    assert status.state == "PARITY_MISMATCH"
    assert status.ltp_delta == 1.0


def test_unknown_or_wrong_provider_token_is_rejected():
    monitor = SharedIndexQuoteParity([mapping("ANGELONE", "a1"), mapping("SHOONYA", "s1")])
    assert monitor.observe(obs("SHOONYA", "wrong", 100, 1_000_000_000)) is None


def test_duplicate_source_contract_mapping_fails_closed():
    import pytest
    with pytest.raises(ValueError, match="duplicate source mapping"):
        SharedIndexQuoteParity([mapping("SHOONYA", "s1"), mapping("SHOONYA", "s2")])


def test_stale_quote_is_not_parity_ok():
    monitor = SharedIndexQuoteParity([mapping("ANGELONE", "a1"), mapping("SHOONYA", "s1")], stale_after_ms=100)
    monitor.observe(obs("ANGELONE", "a1", 100, 1_000_000_000))
    status = monitor.observe(obs("SHOONYA", "s1", 100, 1_010_000_000))
    # Status uses the most recent observation's receive timestamp; check current-time API
    status = monitor.status(key(), now_monotonic_ns=1_500_000_000)
    assert status.state == "BOTH_STALE"
