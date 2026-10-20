from __future__ import annotations

from pathlib import Path

import pytest

from novel.models import Canon, ProjectMeta
from novel.storage import CanonNotFound, CanonStore


@pytest.fixture
def store(tmp_path: Path) -> CanonStore:
    instance = CanonStore(tmp_path / "novel.db")
    yield instance
    instance.close()


def _canon() -> Canon:
    return Canon(meta=ProjectMeta(title="断碑"))


def test_load_before_init_raises(store: CanonStore) -> None:
    with pytest.raises(CanonNotFound):
        store.load()


def test_initialize_then_load(store: CanonStore) -> None:
    store.initialize(_canon())
    assert store.exists()
    assert store.load().meta.title == "断碑"


def test_second_initialize_is_refused(store: CanonStore) -> None:
    store.initialize(_canon())
    with pytest.raises(RuntimeError):
        store.initialize(_canon())


def test_save_bumps_version_and_keeps_history(store: CanonStore) -> None:
    canon = store.initialize(_canon())
    assert canon.version == 1

    canon.meta.logline = "一句话"
    store.save(canon)
    assert canon.version == 2

    store.save(canon)
    assert store.load().version == 3
    assert [v for v, _ in store.history()] == [3, 2, 1]


def test_run_log_totals(store: CanonStore) -> None:
    store.initialize(_canon())
    store.log_run("writer", "mock", 100, 50)
    store.log_run("planner", "mock", 20, 10)
    assert store.run_totals() == (2, 120, 60)


def test_history_is_pruned_to_bounded_set(tmp_path: Path) -> None:
    """全量快照 × 几百章会把库撑到 GB 级，所以历史必须是有界的。"""
    with CanonStore(tmp_path / "h.db", keep_recent=5, keep_every=10) as store:
        canon = store.initialize(_canon())
        for _ in range(100):
            store.save(canon)

        assert canon.version == 101
        # 保留：第 1 版 + 10 的倍数 + 最近 5 版
        assert store.history_payload(1) is not None
        assert store.history_payload(50) is not None
        assert store.history_payload(55) is None
        assert store.history_payload(101) is not None
        assert store.history_stats()[0] == 15


def test_history_not_pruned_below_threshold(tmp_path: Path) -> None:
    with CanonStore(tmp_path / "h.db", keep_recent=30, keep_every=50) as store:
        canon = store.initialize(_canon())
        for _ in range(3):
            store.save(canon)

        assert store.history_stats()[0] == 4


def test_history_keeps_first_version_forever(tmp_path: Path) -> None:
    with CanonStore(tmp_path / "h.db", keep_recent=2, keep_every=1000) as store:
        canon = store.initialize(_canon())
        for _ in range(20):
            store.save(canon)

        counts = {v for v, _ in store.history(limit=100)}
        assert 1 in counts
        assert counts == {1, 20, 21}


def test_reopening_same_file_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "novel.db"
    with CanonStore(path) as first:
        first.initialize(_canon())
    with CanonStore(path) as second:
        assert second.load().meta.title == "断碑"
