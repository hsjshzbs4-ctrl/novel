"""SQLite 持久化。

存储策略是"文档 + 追加式历史"，而不是把每个实体拆成表：
- 一本书的状态天然是一张图，拆表会让"读一次全量"变成 N 次 JOIN，收益不抵成本；
- 真正的价值在版本历史（可回滚）和运行日志（可核算 token）；
- 等出现"跨书检索"需求时再拆表，现在不做。

canon 表恒为单行（id=1），每次 save 自增 version 并追加一条历史快照。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from .models import Canon, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS canon (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    version    INTEGER NOT NULL,
    payload    TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS canon_history (
    version    INTEGER PRIMARY KEY,
    payload    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task              TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    chapter           INTEGER,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    cached            INTEGER NOT NULL DEFAULT 0
);

-- 断点恢复：每次 LLM 调用的产物按 (命名空间, 任务, prompt 指纹) 落盘。
-- 进程中途死掉后重跑，已完成的调用直接复用，不重复烧 token。
CREATE TABLE IF NOT EXISTS checkpoints (
    key         TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""

#: 老库升级：新增列名 -> DDL 片段
_RUN_MIGRATIONS = {
    "chapter": "INTEGER",
    "duration_ms": "INTEGER NOT NULL DEFAULT 0",
    "cached": "INTEGER NOT NULL DEFAULT 0",
}


class CanonNotFound(RuntimeError):
    """数据库存在但尚未初始化 canon。"""


class CanonStore:
    def __init__(
        self,
        path: Path | str,
        *,
        keep_recent: int | None = None,
        keep_every: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 版本历史是有界保留的：全量快照 × 几百章会把库撑到 GB 级
        self._keep_recent = (
            keep_recent if keep_recent is not None else int(os.environ.get("NOVEL_HISTORY_RECENT") or 30)
        )
        self._keep_every = (
            keep_every if keep_every is not None else int(os.environ.get("NOVEL_HISTORY_EVERY") or 50)
        )
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """老库补列。CREATE TABLE IF NOT EXISTS 不会给已有表加字段。"""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(runs)")}
        for column, ddl in _RUN_MIGRATIONS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE runs ADD COLUMN {column} {ddl}")
        self._conn.commit()

    def _prune_history(self) -> int:
        """保留最近 N 版 + 每 K 版一个锚点 + 第 1 版，其余删掉。

        版本历史的用途是"刚刚写坏了一批章纲，退回去"，不是考古。
        有界保留能把 500 章的库从 GB 级压到几十 MB。
        """
        row = self._conn.execute("SELECT MAX(version) AS v FROM canon_history").fetchone()
        latest = int(row["v"] or 0)
        cutoff = latest - self._keep_recent
        if cutoff <= 1 or self._keep_every <= 1:
            return 0

        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM canon_history"
                " WHERE version <= ? AND version != 1 AND version % ? != 0",
                (cutoff, self._keep_every),
            )
        return int(cursor.rowcount or 0)

    # -- 生命周期 -----------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CanonStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- canon --------------------------------------------------------------

    def exists(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM canon WHERE id = 1").fetchone()
        return row is not None

    def initialize(self, canon: Canon) -> Canon:
        """首次写入。已初始化时报错，避免误覆盖整本书。"""
        if self.exists():
            raise RuntimeError("canon 已初始化，拒绝覆盖；如需重建请删除 novel.db")
        canon.version = 1
        canon.touch()
        payload = canon.model_dump_json()
        with self._conn:
            self._conn.execute(
                "INSERT INTO canon (id, version, payload, updated_at) VALUES (1, ?, ?, ?)",
                (canon.version, payload, canon.meta.updated_at),
            )
            self._conn.execute(
                "INSERT INTO canon_history (version, payload, created_at) VALUES (?, ?, ?)",
                (canon.version, payload, utcnow()),
            )
        return canon

    def load(self) -> Canon:
        row = self._conn.execute("SELECT payload FROM canon WHERE id = 1").fetchone()
        if row is None:
            raise CanonNotFound("尚未初始化：先执行 `novel init`")
        return Canon.model_validate_json(row["payload"])

    def save(self, canon: Canon) -> Canon:
        """自增版本并落盘，同时保留快照。返回同一个对象（version 已更新）。"""
        canon.version += 1
        canon.touch()
        payload = canon.model_dump_json()
        with self._conn:
            self._conn.execute(
                "UPDATE canon SET version = ?, payload = ?, updated_at = ? WHERE id = 1",
                (canon.version, payload, canon.meta.updated_at),
            )
            self._conn.execute(
                "INSERT INTO canon_history (version, payload, created_at) VALUES (?, ?, ?)",
                (canon.version, payload, utcnow()),
            )
        self._prune_history()
        return canon

    def history(self, limit: int = 10) -> list[tuple[int, str]]:
        rows = self._conn.execute(
            "SELECT version, created_at FROM canon_history ORDER BY version DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [(int(r["version"]), str(r["created_at"])) for r in rows]

    def history_payload(self, version: int) -> str | None:
        row = self._conn.execute(
            "SELECT payload FROM canon_history WHERE version = ?", (version,)
        ).fetchone()
        return str(row["payload"]) if row is not None else None

    def history_stats(self) -> tuple[int, int]:
        """(保留的版本数, 约占字节数)。"""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(payload)),0) AS total FROM canon_history"
        ).fetchone()
        return int(row["n"]), int(row["total"])

    # -- 运行日志 -----------------------------------------------------------

    def log_run(
        self,
        task: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        *,
        chapter: int | None = None,
        duration_ms: int = 0,
    ) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO runs (task, model, prompt_tokens, completion_tokens,"
                " created_at, chapter, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task,
                    model,
                    prompt_tokens,
                    completion_tokens,
                    utcnow(),
                    chapter,
                    duration_ms,
                ),
            )

    def run_totals(self) -> tuple[int, int, int]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(prompt_tokens),0) AS p,"
            " COALESCE(SUM(completion_tokens),0) AS c FROM runs"
        ).fetchone()
        return int(row["n"]), int(row["p"]), int(row["c"])

    def cost_by_task(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT task, COUNT(*) AS calls,"
            " COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
            " COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
            " COALESCE(SUM(duration_ms),0) AS duration_ms"
            " FROM runs GROUP BY task ORDER BY completion_tokens DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def cost_by_chapter(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT chapter, COUNT(*) AS calls,"
            " COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
            " COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
            " COALESCE(SUM(duration_ms),0) AS duration_ms"
            " FROM runs WHERE chapter IS NOT NULL"
            " GROUP BY chapter ORDER BY chapter"
        ).fetchall()
        return [dict(row) for row in rows]

    def cost_totals(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS calls,"
            " COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
            " COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
            " COALESCE(SUM(duration_ms),0) AS duration_ms FROM runs"
        ).fetchone()
        return dict(row)

    # -- 断点 ---------------------------------------------------------------

    def get_checkpoint(self, key: str, fingerprint: str) -> str | None:
        """指纹不匹配视为未命中 —— prompt 变了，旧结果就不能再用。"""
        row = self._conn.execute(
            "SELECT fingerprint, payload FROM checkpoints WHERE key = ?", (key,)
        ).fetchone()
        if row is None or row["fingerprint"] != fingerprint:
            return None
        return str(row["payload"])

    def put_checkpoint(self, key: str, fingerprint: str, payload: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO checkpoints (key, fingerprint, payload, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET"
                " fingerprint = excluded.fingerprint,"
                " payload = excluded.payload,"
                " created_at = excluded.created_at",
                (key, fingerprint, payload, utcnow()),
            )

    def clear_checkpoints(self, prefix: str = "") -> int:
        with self._conn:
            cursor = self._conn.execute(
                "DELETE FROM checkpoints WHERE key LIKE ?", (prefix + "%",)
            )
        return int(cursor.rowcount or 0)

    def checkpoint_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM checkpoints").fetchone()
        return int(row["n"])

    # -- 调试 ---------------------------------------------------------------

    def dump_json(self) -> str:
        """便于人工排查：导出当前 canon。"""
        return json.dumps(self.load().model_dump(mode="json"), ensure_ascii=False, indent=2)
