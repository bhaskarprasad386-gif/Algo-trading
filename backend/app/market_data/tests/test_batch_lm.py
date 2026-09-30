from app.backtesting.historical_catalog import HistoricalRecord
from app.market_data.bounded_buffer import BoundedPriorityBuffer,BufferPriority
from app.market_data.contracts import InstrumentKey,InstrumentType,MarketDataRecord
from app.market_data.ingestion import BoundedMarketDataIngestor
from app.market_data.persistence import DailySQLiteMarketDataRepository

def rec(token="1",ts=1):return MarketDataRecord(instrument=InstrumentKey("NSE","EQ",token),symbol=f"S{token}",instrument_type=InstrumentType.EQUITY,timestamp_ns=ts,ltp=100.)
def test_critical_priority_is_drained_first():
    b=BoundedPriorityBuffer(maxsize=4,critical_timeout_seconds=.1,normal_timeout_seconds=.1);assert b.put("n",priority=BufferPriority.NORMAL);assert b.put("c",priority=BufferPriority.CRITICAL);assert b.get(.1)=="c";b.task_done();assert b.get(.1)=="n"
def test_normal_drops_when_full():
    b=BoundedPriorityBuffer(maxsize=1,critical_timeout_seconds=.01,normal_timeout_seconds=.01);assert b.put("c",priority=BufferPriority.CRITICAL);assert b.put("n",priority=BufferPriority.NORMAL) is False;assert b.snapshot()["normal_drops"]==1
class FakeRepository:
    def __init__(self):self.batches=[]
    def write_batch(self,records,*,ingested_at_ns=0):self.batches.append(list(records));return len(self.batches[-1])
def test_ingestor_is_bounded_and_observable():
    r=FakeRepository();i=BoundedMarketDataIngestor(r,max_queue=2,batch_size=2,flush_seconds=.02)
    for t in ("1","2","3"):assert i.submit(rec(t,int(t)))
    i.close(2);s=i.snapshot();assert sum(map(len,r.batches))==3;assert s["queue_capacity"]==2;assert s["db_write_latency_ms"]>=0;assert s["records_per_second"]>0
def test_daily_repository_deduplicates_and_shards(tmp_path):
    r=DailySQLiteMarketDataRepository(str(tmp_path/"market.sqlite3"));x=HistoricalRecord("angelone-live-1s","NSE:EQ:1","1s",1758000000000000000,{"ltp":100})
    assert r.write_batch([x,x])==1;assert r.stats()["duplicates"]==1;assert len(r.catalog.shard_paths())==1;r.checkpoint();r.close()
