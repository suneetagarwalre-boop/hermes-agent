from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


@pytest.mark.parametrize(
    "bad_key",
    [
        "dispatch-feed:job\nforged-log-line",
        "\ndispatch-feed:leading-newline",
        "dispatch-feed:trailing-next-line\u0085",
        "dispatch-feed:job\u0085next-line",
        "dispatch-feed:job\u202econcealed",
    ],
)
def test_rejects_control_characters_in_job_identity(kanban_home, bad_key):
    with kb.connect_closing() as conn:
        with pytest.raises(ValueError, match="printable characters"):
            kb.create_task(
                conn,
                title="bad identity",
                body="Reject log-injecting job identities before insert.",
                assignee="stark",
                idempotency_key=bad_key,
            )


def test_twelve_create_race_returns_one_task(kanban_home):
    """Replay the 5609a458 cascade: one job must create one card."""
    workers = 12
    barrier = threading.Barrier(workers)
    key = "discord-dispatch-feed:5609a458"

    def create_once(index: int) -> str:
        with kb.connect_closing() as conn:
            barrier.wait(timeout=10)
            return kb.create_task(
                conn,
                title=f"cascade attempt {index}",
                body="Execute the same dispatch job without opening a second card.",
                assignee="stark",
                created_by="discord-dispatch-feed",
                idempotency_key=key,
            )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        ids = list(pool.map(create_once, range(workers)))

    with kb.connect_closing() as conn:
        rows = conn.execute(
            "SELECT id FROM tasks WHERE idempotency_key = ? AND status != 'archived'",
            (key,),
        ).fetchall()

    assert len(set(ids)) == 1
    assert [row["id"] for row in rows] == [ids[0]]
