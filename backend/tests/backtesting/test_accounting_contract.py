from app.backtesting.contracts import AccountingProtocol
from app.backtesting.portfolio import Portfolio


def test_portfolio_satisfies_accounting_contract():
    portfolio = Portfolio(100_000.0)
    assert isinstance(portfolio, AccountingProtocol)
    state = portfolio.export_state()
    assert state["initial_cash"] == 100_000.0
    portfolio.restore_state(state)
    assert portfolio.snapshot().cash == 100_000.0
