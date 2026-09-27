from sqlalchemy import Column,Integer,String,Float,DateTime
from datetime import datetime,timezone
from app.core.database import Base

class LiveBoxSpreadPaperPosition(Base):
 __tablename__="live_box_spread_paper_positions"
 id=Column(Integer,primary_key=True)
 user_id=Column(Integer,index=True,nullable=False)
 underlying=Column(String,index=True,nullable=False)
 instrument_class=Column(String,nullable=False)
 expiry=Column(String,nullable=False)
 low_strike=Column(Float,nullable=False)
 high_strike=Column(Float,nullable=False)
 direction=Column(String,nullable=False)
 lot_size=Column(Integer,nullable=False)
 lots=Column(Integer,nullable=False)
 low_call_entry=Column(Float,nullable=False)
 low_put_entry=Column(Float,nullable=False)
 high_call_entry=Column(Float,nullable=False)
 high_put_entry=Column(Float,nullable=False)
 realized_pnl=Column(Float,default=0.0)
 is_open=Column(Integer,default=1)
 created_at=Column(DateTime,default=lambda:datetime.now(timezone.utc).replace(tzinfo=None))
