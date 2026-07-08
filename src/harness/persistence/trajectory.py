# src/harness/persistence/trajectory.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..events import RunStarted
from .serialize import event_to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TrajectoryStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS trajectory_events("
            "run_id TEXT, seq INTEGER, type TEXT, data TEXT, created_at TEXT, "
            "PRIMARY KEY(run_id, seq))")
        self._conn.commit()

    def append(self, run_id: str, seq: int, event_dict: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO trajectory_events(run_id, seq, type, data, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, seq, event_dict.get("type", ""),
             json.dumps(event_dict, ensure_ascii=False), _now()))
        self._conn.commit()

    def load(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT data FROM trajectory_events WHERE run_id = ? ORDER BY seq",
            (run_id,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class TrajectorySink:
    """事件流包装器：透传事件，同时按序落库。run_id 从 RunStarted 捕获。"""

    def __init__(self, store: TrajectoryStore) -> None:
        self._store = store

    async def wrap(self, events):
        run_id, seq = None, 0
        async for ev in events:
            if isinstance(ev, RunStarted):
                run_id = ev.run_id
            if run_id is not None:
                self._store.append(run_id, seq, event_to_dict(ev))
                seq += 1
            yield ev
