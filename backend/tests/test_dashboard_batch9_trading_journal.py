from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web" / "dashboard" / "index.html"


def test_batch9_trading_journal_workspace_contract():
    ui = WEB.read_text(encoding="utf-8")
    for marker in (
        'id="tradingJournalNav"',
        'id="tradingJournalPage"',
        "Trade Journal",
        "Journal Review",
        "Journal Analytics",
        "function showTradingJournal",
        "function saveJournalEntry",
        "function selectJournalEntry",
        "JOURNAL_KEY",
        "journal-mobile-actions",
        "CASH-FUTURE",
        "CALENDAR SPREAD",
        "SYNTHETIC ARBITRAGE",
        "BOX SPREAD",
    ):
        assert marker in ui
