        # A failed checkpoint transaction rolls back its pending journal batch.
        # Discard any uncommitted records before a retry/resume on this wrapper.
        self._pending_journal.clear()
        source_events = iter(events)
        checkpoint = self.ledger.load_checkpoint(self.run_id) if resume else None
        context_state = dict(state or {})
        start_cursor = 0; resume_timestamp = 0; expected_identity = None
        if resume:
            self.ledger.validate_resume(self.run_id, schema_version=schema_version,
                                        data_source_fingerprint=data_source_fingerprint)
            if self.ledger.record_count(self.run_id, "RUN_END") > 0:
                raise ValueError("cannot resume completed run")
            if checkpoint is None: raise ValueError("no checkpoint available for resume")
            saved = checkpoint.state
            start_cursor = int(saved.get("source_cursor", checkpoint.event_index))
            if start_cursor < 0: raise ValueError("invalid checkpoint source_cursor")
            expected_identity = saved.get("source_event_identity")
            if start_cursor > 0:
                if not isinstance(expected_identity, Mapping):
                    raise ValueError("checkpoint missing source_event_identity; restart required")
                expected_identity = tuple(expected_identity.get(k) for k in
                                          ("timestamp_ns", "instrument", "event_type", "sequence", "source"))
            resume_timestamp = checkpoint.timestamp_ns
            saved_portfolio = saved.get("portfolio_state")
            if saved_portfolio is not None and self.engine.portfolio is not None:
                self.engine.portfolio.restore_state(saved_portfolio)