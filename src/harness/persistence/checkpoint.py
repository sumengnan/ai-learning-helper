# src/harness/persistence/checkpoint.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..state import RunState
from .serialize import runstate_from_dict, runstate_to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS checkpoints("
            "run_id TEXT PRIMARY KEY, state TEXT, step INTEGER, updated_at TEXT)")
        self._conn.commit()

    def save(self, state: RunState) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints(run_id, state, step, updated_at) VALUES (?, ?, ?, ?)",
            (state.run_id, json.dumps(runstate_to_dict(state), ensure_ascii=False), state.step, _now()))
        self._conn.commit()

    def load(self, run_id: str) -> RunState | None:
        row = self._conn.execute(
            "SELECT state FROM checkpoints WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return runstate_from_dict(json.loads(row[0]))

    def delete(self, run_id: str) -> None:
        self._conn.execute("DELETE FROM checkpoints WHERE run_id = ?", (run_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
