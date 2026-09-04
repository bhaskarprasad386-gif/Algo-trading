from app.core.database import Base, SessionLocal, engine
from app.models import BacktestJobResultChunk
from app.scanner.result_chunk_store import (
    MAX_RESULT_CHUNK_PAGE,
    delete_result_chunks,
    delete_result_chunks_batched,
    get_result_chunks_after,
)


def test_result_chunks_use_keyset_pagination_and_cleanup():
    Base.metadata.create_all(bind=engine)
    job_id = "__keyset_chunk_test__"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.add_all([
            BacktestJobResultChunk(job_id=job_id, sequence=i, symbol=f"S{i}", result_json=f'{{"i": {i}}}')
            for i in range(5)
        ])
        db.commit()

        first = get_result_chunks_after(db, job_id, limit=2)
        assert [chunk.sequence for chunk in first] == [0, 1]

        second = get_result_chunks_after(db, job_id, after_sequence=first[-1].sequence, limit=2)
        assert [chunk.sequence for chunk in second] == [2, 3]

        third = get_result_chunks_after(db, job_id, after_sequence=second[-1].sequence, limit=2)
        assert [chunk.sequence for chunk in third] == [4]

        assert delete_result_chunks(db, job_id) == 5
        assert get_result_chunks_after(db, job_id, limit=2) == []
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()


def test_result_chunks_page_limit_is_hard_capped():
    Base.metadata.create_all(bind=engine)
    job_id = "__page_cap_chunk_test__"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.add_all([
            BacktestJobResultChunk(job_id=job_id, sequence=i, symbol=f"S{i}", result_json=f'{{"i": {i}}}')
            for i in range(MAX_RESULT_CHUNK_PAGE + 5)
        ])
        db.commit()

        page = get_result_chunks_after(db, job_id, limit=MAX_RESULT_CHUNK_PAGE + 500)
        assert len(page) == MAX_RESULT_CHUNK_PAGE
        assert page[0].sequence == 0
        assert page[-1].sequence == MAX_RESULT_CHUNK_PAGE - 1
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()


def test_result_chunks_batched_cleanup_never_loads_all_rows_at_once():
    Base.metadata.create_all(bind=engine)
    job_id = "__batched_chunk_cleanup_test__"
    db = SessionLocal()
    try:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.add_all([
            BacktestJobResultChunk(job_id=job_id, sequence=i, symbol=f"S{i}", result_json=f'{{"i": {i}}}')
            for i in range(7)
        ])
        db.commit()

        assert delete_result_chunks_batched(db, job_id, batch_size=2) == 7
        assert get_result_chunks_after(db, job_id, limit=2) == []
    finally:
        db.query(BacktestJobResultChunk).filter(BacktestJobResultChunk.job_id == job_id).delete()
        db.commit()
        db.close()
