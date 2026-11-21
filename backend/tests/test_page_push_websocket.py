from types import SimpleNamespace
import asyncio

from app.scanner import page_push_websocket as push


def test_page_channels_cover_all_scanner_pages():
    expected = {
        "cash-future", "box-spread", "calendar-spread",
        "synthetic-arbitrage", "iv-skew-flip",
        "credit-butterfly-condor-weekly",
        "credit-butterfly-condor-monthly", "weekly-swing",
    }
    assert expected.issubset(push.PAGE_CHANNELS)


def test_page_sequence_gap_is_detected_from_zero_baseline():
    assert not push._sequence_gap(0, 1)
    assert push._sequence_gap(0, 2)
    assert not push._sequence_gap(8, 9)
    assert push._sequence_gap(8, 10)


def test_duplicate_and_stale_frames_do_not_trigger_resync():
    assert not push._requires_resync(1, 1)
    assert not push._requires_resync(3, 2)
    assert not push._requires_resync(0, 1)
    assert push._requires_resync(0, 3)
    assert push._requires_resync(3, 3, snapshot_required=True)



def test_keyed_collection_change_publisher_emits_add_update_delete_contract(monkeypatch):
    published = []
    monkeypatch.setattr(push, "publish_page_update", lambda page, event, data=None: published.append((page, event, data)))
    push.publish_collection_change("box-spread", "opportunities", "ADD", "id", item={"id": "x", "edge": 1.0})
    push.publish_collection_change("box-spread", "opportunities", "UPDATE", "id", key="x", item={"id": "x", "edge": 2.0})
    push.publish_collection_change("box-spread", "opportunities", "DELETE", "id", key="x")
    push.publish_collection_change("unknown-page", "opportunities", "ADD", "id", item={"id": "x"})
    assert len(published) == 3
    assert all(page == "box-spread" and event == "PATCH" for page, event, _ in published)
    changes = [payload["collection_changes"][0] for _, _, payload in published]
    assert [change["operation"] for change in changes] == ["ADD", "UPDATE", "DELETE"]
    assert changes[0]["item"] == {"id": "x", "edge": 1.0}
    assert changes[1]["key"] == "x"
    assert changes[2]["key"] == "x"


def test_keyed_collection_change_publisher_rejects_invalid_identity_and_reserved_names(monkeypatch):
    published = []
    monkeypatch.setattr(push, "publish_page_update", lambda page, event, data=None: published.append((page, event, data)))
    push.publish_collection_change("box-spread", "rows", "ADD", "id", item={"id": "ok"})
    push.publish_collection_change("box-spread", "rows", "UPDATE", "id", key="other", item={"id": "wrong"})
    push.publish_collection_change("box-spread", "__proto__", "ADD", "id", item={"id": "x"})
    push.publish_collection_change("box-spread", "rows", "ADD", "__proto__", item={"__proto__": "x"})
    push.publish_collection_change("box-spread", "rows", "ADD", "id", item={"id": True})
    push.publish_collection_change("box-spread", "rows", "ADD", "id", item={"id": ""})
    assert len(published) == 1
    change = published[0][2]["collection_changes"][0]
    assert change["operation"] == "ADD" and change["key"] == "ok"

def test_missing_or_invalid_snapshot_provider_is_unavailable(monkeypatch):
    page = "box-spread"
    monkeypatch.delitem(push._snapshot_providers, page, raising=False)
    assert push._snapshot(page) == {"snapshot_status": "UNAVAILABLE"}
    monkeypatch.setitem(push._snapshot_providers, page, lambda: [])
    assert push._snapshot(page) == {"snapshot_status": "UNAVAILABLE"}


def test_page_snapshot_frame_uses_explicit_durable_sequence():
    frame = push._snapshot_frame("box-spread", {"rows": []}, sequence=17)
    assert frame["type"] == "page_update"
    assert frame["page"] == "box-spread"
    assert frame["sequence"] == 17
    assert frame["event"] == "SNAPSHOT"
    assert frame["data"] == {"rows": []}




def test_snapshot_sequence_retries_until_page_is_stable(monkeypatch):
    sequence = {"value": 0}
    captured = []

    def snapshot():
        captured.append(sequence["value"])
        # Simulate a scanner push landing during the first snapshot capture.
        if len(captured) == 1:
            sequence["value"] += 1
        return {"observed_sequence": sequence["value"]}


    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: sequence["value"])
    monkeypatch.setattr(push, "_snapshot", lambda page: snapshot())
    frame = push._consistent_snapshot_frame("box-spread", retries=3)
    assert frame["event"] == "SNAPSHOT"
    assert frame["sequence"] == 1
    assert frame["data"]["observed_sequence"] == 1
    assert len(captured) == 2

def test_activating_page_channel_requests_snapshot_recovery(monkeypatch):
    queue = asyncio.Queue(maxsize=push.QUEUE_SIZE)
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: 27 if page == "box-spread" else 0)
    push._set_channels(queue, frozenset({"box-spread"}))
    marker = queue.get_nowait()
    assert marker["page"] == "box-spread"
    assert marker["event"] == "SNAPSHOT_REQUIRED"
    assert marker["sequence"] == 27


def test_reactivating_existing_channel_does_not_enqueue_recovery(monkeypatch):
    queue = asyncio.Queue(maxsize=push.QUEUE_SIZE)
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: 3)
    push._set_channels(queue, frozenset({"box-spread"}))
    first = queue.get_nowait()
    assert first["event"] == "SNAPSHOT_REQUIRED"
    push._set_channels(queue, frozenset({"box-spread", "iv-skew-flip"}))
    second = queue.get_nowait()
    assert second["page"] == "iv-skew-flip"
    assert second["event"] == "SNAPSHOT_REQUIRED"
    assert queue.empty()


def test_channel_activation_queue_overflow_marks_all_active_pages_for_resync(monkeypatch):
    queue = asyncio.Queue(maxsize=2)
    queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 8})
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: 9 if page == "box-spread" else 4)
    push._set_channels(queue, frozenset({"box-spread"}))
    first = queue.get_nowait()
    assert first["event"] == "SNAPSHOT_REQUIRED"
    queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 10})
    queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 11})
    push._set_channels(queue, frozenset({"box-spread", "iv-skew-flip"}))
    markers = [queue.get_nowait(), queue.get_nowait()]
    assert {frame["page"] for frame in markers} == {"box-spread", "iv-skew-flip"}
    assert all(frame["event"] == "SNAPSHOT_REQUIRED" for frame in markers)

def test_snapshot_provider_is_read_only_and_page_scoped():
    observed = []
    push.configure_snapshot_provider(
        "credit-butterfly-condor-weekly",
        lambda: observed.append("read") or {"rows": []},
    )
    assert push._snapshot("credit-butterfly-condor-weekly") == {"rows": []}
    assert observed == ["read"]
    push.configure_snapshot_provider("credit-butterfly-condor-weekly", None)
    try:
        push.configure_snapshot_provider("unknown-page", lambda: {})
    except ValueError as exc:
        assert "unknown page" in str(exc)
    else:
        raise AssertionError("unknown page must be rejected")




def test_missing_snapshot_provider_is_not_an_authoritative_empty_snapshot():
    page = "credit-butterfly-condor-weekly"
    push.configure_snapshot_provider(page, None)
    assert push._snapshot(page) == {"snapshot_status": "UNAVAILABLE"}
    frame = push._consistent_snapshot_frame(page)
    assert frame["event"] == "SNAPSHOT"
    assert frame["data"] == {"snapshot_status": "UNAVAILABLE"}


def test_non_object_snapshot_provider_is_not_authoritative(monkeypatch):
    page = "credit-butterfly-condor-monthly"
    push.configure_snapshot_provider(page, lambda: [])
    assert push._snapshot(page) == {"snapshot_status": "UNAVAILABLE"}
    push.configure_snapshot_provider(page, None)


def test_origin_policy_rejects_untrusted_hosts(monkeypatch):
    monkeypatch.setenv("SCANNER_PAGE_WS_ALLOWED_ORIGINS", "https://algo-trading.pages.dev,https://terminal.example.com")
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://algo-trading.vercel.app"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "https://algo-trading.bhaskarprasad386.workers.dev"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "http://localhost:3000"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "https://algo-trading.pages.dev"}))
    assert push._origin_allowed(SimpleNamespace(headers={"origin": "https://terminal.example.com"}))
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://evil.example"}))
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://sub.algo-trading.pages.dev"}))
    assert not push._origin_allowed(SimpleNamespace(headers={"origin": "https://terminal.example.com/path"}))
    assert push._origin_allowed(SimpleNamespace(headers={}))


def test_page_event_and_sequence_journals_are_durable_tables():
    assert push.events.name == "algo_scanner_page_ws_events"
    assert "page_sequence" in push.events.c
    assert push.sequences.name == "algo_scanner_page_ws_sequences"
    assert "sequence" in push.sequences.c


def test_websocket_subscribe_publish_and_resync_round_trip(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    journal = []
    page_sequences = {"box-spread": 0}
    provider_calls = []
    snapshot_state = {"opportunities": [{"id": "initial"}]}
    injected_frames = []
    gap_checks = []
    snapshot_frames = []
    original_sequence_gap = push._sequence_gap
    original_snapshot_frame = push._consistent_snapshot_frame

    def trace_sequence_gap(last, incoming):
        result = original_sequence_gap(last, incoming)
        gap_checks.append((last, incoming, result))
        return result

    def trace_snapshot_frame(snapshot_page, *args, **kwargs):
        frame = original_snapshot_frame(snapshot_page, *args, **kwargs)
        snapshot_frames.append(frame)
        return frame


    def fake_events_after(target_page, cursor_sequence, limit=100):
        return [row for row in journal if row["page"] == target_page and row["sequence"] > cursor_sequence][:limit + 1]

    def fake_latest_page_sequence(page):
        return page_sequences.get(page, 0)

    def fake_publish(page, event, data=None):
        page_sequences[page] = page_sequences.get(page, 0) + 1
        frame = {            "type": "page_update", "page": page, "sequence": page_sequences[page],
            "event": event, "data": data or {},
        }
        # This round-trip test injects exactly one controlled frame into the
        # subscriber queue. Journal dispatcher delivery is covered separately;
        # duplicating a journal row here would race the direct queue injection.
        injected_frames.append(frame.copy())
        if page == "box-spread" and event == "PATCH":
            snapshot_state["opportunities"].append({"id": "fresh"})
        for queue, channels in tuple(push._subscribers):
            if page in channels:
                queue.put_nowait(frame)

    monkeypatch.setattr(push, "_sequence_gap", trace_sequence_gap)
    monkeypatch.setattr(push, "_consistent_snapshot_frame", trace_snapshot_frame)
    monkeypatch.setattr(push, "_latest_page_sequence", lambda target_page: page_sequences.get(target_page, 0))
    monkeypatch.setattr(push, "_events_after", fake_events_after)
    monkeypatch.setattr(push, "_latest_page_sequence", fake_latest_page_sequence)
    monkeypatch.setattr(push, "publish_page_update", fake_publish)
    monkeypatch.setattr(push, "_subscribers", set())
    monkeypatch.setattr(push, "_dispatch_task", None)
    monkeypatch.setattr(push, "_dispatch_loop", None)

    async def controlled_subscribe():
        # Isolate this handler round-trip from the durable journal tailer.
        # The tailer has separate tests; here only one injected frame is expected.
        queue = asyncio.Queue(maxsize=push.QUEUE_SIZE)
        # Production registers an empty channel set first; subscribe action
        # assigns the requested channel. Mirror that lifecycle in this test.
        push._subscribers.add((queue, frozenset()))
        return queue

    monkeypatch.setattr(push, "_subscribe_local", controlled_subscribe)

    app = FastAPI()
    app.include_router(push.router)
    page = "box-spread"
    push.configure_snapshot_provider(
        page,
        lambda: provider_calls.append("snapshot") or {"opportunities": list(snapshot_state["opportunities"])},
    )
    try:
        with TestClient(app).websocket_connect(
            "/ws/page-updates",
            headers={"origin": "https://algo-trading.bhaskarprasad386.workers.dev"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "connected"
            websocket.send_json({"action": "subscribe", "page": page})
            initial = websocket.receive_json()
            assert initial["event"] == "SNAPSHOT"
            assert initial["sequence"] == 0
            assert initial["data"]["opportunities"][0]["id"] == "initial"

            push.publish_page_update(page, "PATCH", {"opportunity_updates": [{"id": "fresh"}]})
            # Ignore any duplicate/stale frame left by a concurrent journal
            # dispatcher; only a strictly-forward frame may advance this test.
            patch = websocket.receive_json()
            while patch.get("sequence", 0) <= initial["sequence"]:
                patch = websocket.receive_json()
            assert patch["sequence"] == initial["sequence"] + 1
            if patch["event"] == "PATCH":
                assert patch["data"]["opportunity_updates"][0]["id"] == "fresh"
                expected_snapshot_calls = 1
            else:
                # Recovery may replace a queued frame with an authoritative snapshot
                # if the journal dispatcher signals a reset concurrently.
                assert patch["event"] == "SNAPSHOT"
                assert any(row["id"] == "fresh" for row in patch["data"]["opportunities"])
                expected_snapshot_calls = 2

            websocket.send_json({"action": "resync", "page": page})
            resynced = websocket.receive_json()
            assert resynced["event"] == "SNAPSHOT"
            assert resynced["sequence"] == patch["sequence"]
            # The journal tailer may request an additional authoritative snapshot
            # when a concurrent cursor/reset signal races this isolated round-trip.
            # We still require the initial snapshot plus explicit resync snapshots.
            assert len(provider_calls) >= expected_snapshot_calls + 1
    finally:
        push.configure_snapshot_provider(page, None)


def test_page_resync_preserves_other_subscribed_channel_events():
    async def scenario():
        queue = asyncio.Queue(maxsize=4)
        queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 2})
        queue.put_nowait({"page": "iv-skew-flip", "event": "PATCH", "sequence": 8})
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        assert waiter.done()
        next_waiter = await push._restart_event_waiter(queue, waiter, "box-spread")
        preserved = await asyncio.wait_for(next_waiter, timeout=0.1)
        assert preserved["page"] == "iv-skew-flip"
        next_waiter.cancel()
        await asyncio.gather(next_waiter, return_exceptions=True)
    asyncio.run(scenario())


def test_page_push_dispatcher_marks_queue_overflow_for_snapshot(monkeypatch):
    async def scenario():
        page = "cash-future"
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"page": page, "event": "PATCH", "sequence": 1})
        latest_reads = []
        monkeypatch.setattr(push, "_latest_page_sequence", lambda target_page: latest_reads.append(target_page) or 2)
        monkeypatch.setattr(push, "_events_after", lambda target_page, cursor, limit=100: [
            {"id": 91, "page": page, "sequence": 2, "event": "PATCH", "data": {"rows": []}}
        ] if target_page == page and cursor == 0 else [])
        monkeypatch.setattr(push, "_subscribers", {(queue, frozenset({page}))})
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        task = asyncio.create_task(push._journal_dispatch_loop())
        try:
            await asyncio.wait_for(push._dispatch_ready.wait(), timeout=0.5)
            await asyncio.sleep(0.05)
            assert queue.qsize() == 1
            item = queue.get_nowait()
            assert item["event"] == "SNAPSHOT_REQUIRED"
            assert item["sequence"] == 2
        finally:
            monkeypatch.setattr(push, "_subscribers", set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_unstable_snapshot_is_not_marked_authoritative(monkeypatch):
    sequence = {"value": 0}

    def unstable_snapshot():
        sequence["value"] += 1
        return {"rows": [{"captured": sequence["value"]}]}

    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: sequence["value"])
    monkeypatch.setattr(push, "_snapshot", lambda page: unstable_snapshot())
    frame = push._consistent_snapshot_frame("box-spread", retries=3)
    assert frame["event"] == "SNAPSHOT"
    # Last capture started at sequence 2; don't advance to sequence 3
    # because its snapshot is unstable and cannot represent that cursor.
    assert frame["sequence"] == 2
    assert frame["data"] == {"snapshot_status": "UNAVAILABLE"}


def test_unsubscribe_consumes_already_cancelled_dispatcher(monkeypatch):
    async def scenario():
        async def dispatcher():
            await asyncio.sleep(10)
        task = asyncio.create_task(dispatcher())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        monkeypatch.setattr(push, "_subscribers", set())
        monkeypatch.setattr(push, "_dispatch_task", task)
        monkeypatch.setattr(push, "_dispatch_loop", asyncio.get_running_loop())
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        await push._unsubscribe_local(asyncio.Queue())
        assert push._dispatch_task is None
        assert push._dispatch_loop is None
        assert push._dispatch_ready is None
    asyncio.run(scenario())


def test_unsubscribe_cancels_dispatcher_after_timeout(monkeypatch):
    async def scenario():
        async def dispatcher():
            await asyncio.sleep(10)
        task = asyncio.create_task(dispatcher())
        monkeypatch.setattr(push, "_subscribers", set())
        monkeypatch.setattr(push, "_dispatch_task", task)
        monkeypatch.setattr(push, "_dispatch_loop", asyncio.get_running_loop())
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        await push._unsubscribe_local(asyncio.Queue())
        assert task.cancelled()
        assert push._dispatch_task is None
    asyncio.run(scenario())


def test_unsubscribe_does_not_clear_dispatcher_restarted_by_new_subscriber(monkeypatch):
    async def scenario():
        old_task = asyncio.create_task(asyncio.sleep(0))
        await old_task
        old_queue = asyncio.Queue()
        new_queue = None
        monkeypatch.setattr(push, "_subscribers", {(old_queue, frozenset())})
        monkeypatch.setattr(push, "_dispatch_task", old_task)
        monkeypatch.setattr(push, "_dispatch_loop", asyncio.get_running_loop())
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        original_wait = asyncio.wait
        subscribed = False

        async def wait_with_subscribe(tasks, timeout=None, **kwargs):
            nonlocal new_queue, subscribed
            if not subscribed:
                subscribed = True
                new_queue = await push._subscribe_local()
            return await original_wait(tasks, timeout=timeout, **kwargs)

        monkeypatch.setattr(push.asyncio, "wait", wait_with_subscribe)
        await push._unsubscribe_local(old_queue)
        assert new_queue is not None
        assert push._dispatch_task is not old_task
        assert push._dispatch_task is not None and not push._dispatch_task.done()
        assert any(queue is new_queue for queue, _ in push._subscribers)
        monkeypatch.setattr(push.asyncio, "wait", original_wait)
        await push._unsubscribe_local(new_queue)
    asyncio.run(scenario())


def test_journal_dispatch_uses_page_sequence_cursor_not_global_event_id(monkeypatch):
    async def scenario():
        page = "box-spread"
        queue = asyncio.Queue(maxsize=4)
        monkeypatch.setattr(push, "_latest_page_sequence", lambda target: 0)
        calls = []
        def events_after(target_page, cursor, limit=100):
            calls.append((target_page, cursor))
            if target_page == page and cursor == 0:
                # Deliberately high global id; dispatch correctness is based on
                # this page's sequence, not the cross-page event identity id.
                return [{"id": 9001, "page": page, "sequence": 1, "event": "PATCH", "data": {"edge": 4}}]
            return []
        monkeypatch.setattr(push, "_events_after", events_after)
        monkeypatch.setattr(push, "_subscribers", {(queue, frozenset({page}))})
        monkeypatch.setattr(push, "_dispatch_task", None)
        monkeypatch.setattr(push, "_dispatch_loop", None)
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        task = asyncio.create_task(push._journal_dispatch_loop())
        try:
            await asyncio.wait_for(push._dispatch_ready.wait(), timeout=0.5)
            frame = await asyncio.wait_for(queue.get(), timeout=0.5)
            assert frame["page"] == page and frame["sequence"] == 1
            assert frame["event"] == "PATCH" and frame["data"] == {"edge": 4}
            await asyncio.sleep(0.05)
            assert (page, 1) in calls
        finally:
            monkeypatch.setattr(push, "_subscribers", set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_restart_waiter_overflow_marks_active_channels_for_snapshot(monkeypatch):
    async def scenario():
        queue = asyncio.Queue(maxsize=3)
        pages = {"box-spread", "iv-skew-flip", "weekly-swing"}
        for page in pages:
            queue.put_nowait({"page": page, "event": "PATCH", "sequence": 1})
        monkeypatch.setattr(push, "_subscribers", {(queue, frozenset(pages))})
        monkeypatch.setattr(push, "_latest_page_sequence", lambda page: 7)
        waiter = asyncio.create_task(queue.get())
        # Await completion deterministically: helper needs the completed task's
        # result to restore the consumed frame, not a scheduling assumption.
        await asyncio.wait_for(waiter, timeout=0.5)
        # Add one more so restoring the consumed frame plus retained queue
        # exceeds this test queue size.
        queue.put_nowait({"page": "box-spread", "event": "PATCH", "sequence": 2})
        next_waiter = await push._restart_event_waiter(queue, waiter, "cash-future")
        # Cancel the restarted consumer before inspecting the recovery queue so
        # it cannot race the test and steal one of the snapshot markers.
        next_waiter.cancel()
        await asyncio.gather(next_waiter, return_exceptions=True)
        markers = [queue.get_nowait() for _ in range(3)]
        assert {frame["page"] for frame in markers} == pages
        assert all(frame["event"] == "SNAPSHOT_REQUIRED" and frame["sequence"] == 7 for frame in markers)
    asyncio.run(scenario())


def test_dispatcher_replays_retained_events_from_zero_on_worker_start(monkeypatch):
    async def scenario():
        page = "box-spread"
        queue = asyncio.Queue(maxsize=4)
        latest_reads = []
        monkeypatch.setattr(push, "_latest_page_sequence", lambda target: latest_reads.append(target) or 5)
        calls = []
        def events_after(target_page, cursor, limit=100):
            calls.append((target_page, cursor))
            if target_page == page and cursor == 0:
                return [{"id": 812, "page": page, "sequence": 1, "event": "PATCH", "data": {"replay": True}}]
            return []
        monkeypatch.setattr(push, "_events_after", events_after)
        monkeypatch.setattr(push, "_subscribers", {(queue, frozenset({page}))})
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        task = asyncio.create_task(push._journal_dispatch_loop())
        try:
            await asyncio.wait_for(push._dispatch_ready.wait(), timeout=0.5)
            frame = await asyncio.wait_for(queue.get(), timeout=0.5)
            assert frame["sequence"] == 1 and frame["data"] == {"replay": True}
            assert (page, 0) in calls
            assert latest_reads == []  # startup must not skip to latest sequence
        finally:
            monkeypatch.setattr(push, "_subscribers", set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_journal_dispatch_bounded_queue_recovers_slow_consumer(monkeypatch):
    async def scenario():
        page = "box-spread"
        slow_queue = asyncio.Queue(maxsize=8)
        journal_rows = [
            {"id": index + 1000, "page": page, "sequence": index + 1,
             "event": "PATCH", "data": {"n": index + 1}}
            for index in range(250)
        ]
        cursor_reads = []
        def events_after(target_page, cursor, limit=100):
            cursor_reads.append((target_page, cursor))
            return [row for row in journal_rows if row["page"] == target_page and row["sequence"] > cursor][:limit + 1]
        monkeypatch.setattr(push, "_events_after", events_after)
        monkeypatch.setattr(push, "_latest_page_sequence", lambda target_page: 250)
        monkeypatch.setattr(push, "_subscribers", {(slow_queue, frozenset({page}))})
        monkeypatch.setattr(push, "_dispatch_ready", asyncio.Event())
        task = asyncio.create_task(push._journal_dispatch_loop())
        try:
            await asyncio.wait_for(push._dispatch_ready.wait(), timeout=0.5)
            # Intentionally do not drain the socket queue while 250 updates replay.
            # Wait for bounded pagination to reach the final retained row rather
            # than relying on a fixed sleep that can flake on slow CI workers.
            async def replay_finished():
                while not any(target_page == page and cursor >= 200 for target_page, cursor in cursor_reads):
                    await asyncio.sleep(0)
            await asyncio.wait_for(replay_finished(), timeout=2.0)
            async def overflow_marker_ready():
                while not any(frame.get("event") == "SNAPSHOT_REQUIRED" and frame.get("sequence") == 250
                              for frame in slow_queue._queue):
                    await asyncio.sleep(0)
            await asyncio.wait_for(overflow_marker_ready(), timeout=2.0)
            assert slow_queue.qsize() <= slow_queue.maxsize
            frames = []
            while not slow_queue.empty():
                frames.append(slow_queue.get_nowait())
            recovery = [frame for frame in frames if frame["event"] == "SNAPSHOT_REQUIRED"]
            assert recovery, "slow subscriber must be explicitly told to recover"
            assert recovery[-1]["sequence"] == 250
            assert len(frames) <= slow_queue.maxsize
            # Replay pagination itself is bounded and progresses by cursor.
            assert any(cursor > 0 for target_page, cursor in cursor_reads if target_page == page)
        finally:
            monkeypatch.setattr(push, "_subscribers", set())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_snapshot_handoff_keeps_cursor_before_unstable_capture(monkeypatch):
    values = iter([41, 42, 42, 43, 43, 44])
    captured = []
    def snapshot():
        captured.append(len(captured))
        return {"partial": True}
    monkeypatch.setattr(push, "_latest_page_sequence", lambda page: next(values))
    monkeypatch.setattr(push, "_snapshot", lambda page: snapshot())
    frame = push._consistent_snapshot_frame("box-spread", retries=3)
    assert frame["event"] == "SNAPSHOT"
    assert frame["sequence"] == 43
    assert frame["data"]["snapshot_status"] == "UNAVAILABLE"
    assert len(captured) == 3