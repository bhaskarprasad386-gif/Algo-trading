from app.market_data.provider import AngelOneHistoricalProvider, HistoricalRequest


class FakeClient:
    def get_client(self):
        return self

    def getCandleData(self, request):
        assert request["exchange"] == "NSE"
        assert request["symboltoken"] == "99926000"
        return {"status": True, "data": [["2026-09-01 09:15", "1", "2", "0.5", "1.5", "100"]]}


def test_angel_one_historical_provider_maps_existing_client_contract():
    provider = AngelOneHistoricalProvider(FakeClient())
    rows = provider.historical(
        HistoricalRequest(
            "NSE", "99926000", "ONE_MINUTE",
            "2026-09-01 09:15", "2026-09-01 15:30"
        )
    )
    assert len(rows) == 1
    assert rows[0][0] == "2026-09-01 09:15"
