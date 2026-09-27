"""Live Box Spread scanner -> durable history -> notifications pipeline."""
from app.scanner.live_box_spread_scanner import LiveBoxSpreadScanner
from app.notifications.box_spread_alerts import BoxSpreadAlertService
class LiveBoxSpreadPipeline:
    def __init__(self,scanner,session_factory=None,alerts=None,on_results=None):
        self.scanner=scanner; self.session_factory=session_factory; self.alerts=alerts or BoxSpreadAlertService(); self.on_results=on_results
    def observe(self,payload):
        results=self.scanner.observe(payload)
        if results and self.on_results:
            try:self.on_results(results)
            except Exception:pass
        if results and self.session_factory:
            try:
                with self.session_factory() as db:
                    self.alerts.persist(db,results); self.alerts.notify_users(db,results)
            except Exception:pass
        return results
__all__=["LiveBoxSpreadPipeline"]
