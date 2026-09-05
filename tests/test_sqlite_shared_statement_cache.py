from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import sqlite3
import threading

from hermes_state import SessionDB, _connect_tracked_db


def test_shared_connections_disable_statement_cache(tmp_path):
    original = sqlite3.connect
    seen = []
    def spy(*args, **kwargs):
        seen.append(kwargs.copy())
        return original(*args, **kwargs)
    with patch('hermes_state.sqlite3.connect', spy):
        conn = _connect_tracked_db(tmp_path / 'shared.db', check_same_thread=False)
        conn.close()
    assert seen and all(kw['cached_statements'] == 0 for kw in seen)


def test_pool_handoff_and_shutdown_stress(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    barrier = threading.Barrier(8)
    def query(worker):
        barrier.wait(timeout=10)
        for i in range(1000):
            with db._read_ctx() as conn:
                assert conn.execute('SELECT ?', (worker*1000+i,)).fetchone()[0] == worker*1000+i
        return 1000
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(query, range(8))) == 8000
    finally:
        db.close()
    assert db._conn is None
    assert db._read_pool.empty()
