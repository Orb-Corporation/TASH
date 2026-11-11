import threading

import pytest

from master.master_core import MasterServer
from master.task_manager import ChunkStatus, TaskManager
from shared.protocol import Message, MessageType
from tasks.cpu_benchmark_task import cpu_benchmark, sample_digest
from worker.worker_core import WorkerClient


def test_legacy_package_import_paths_are_available():
    assert Message(MessageType.HEARTBEAT, {}).type == "HEARTBEAT"
    assert TaskManager is not None
    assert MasterServer is not None
    assert WorkerClient is not None


def test_task_manager_rejects_invalid_workload_sizes():
    tm = TaskManager()
    with pytest.raises(ValueError):
        tm.start_job("bad-zero-chunk", "cpu_benchmark", 10, 0)
    with pytest.raises(ValueError):
        tm.start_job("bad-negative-n", "cpu_benchmark", -1, 10)


def test_stale_failure_cannot_resurrect_cancelled_work():
    tm = TaskManager()
    tm.start_job("cancelled-job", "cpu_benchmark", 100, 100)
    chunk = tm.next_chunk_for("worker-a")
    assert chunk is not None

    tm.cancel()
    assert chunk.status == ChunkStatus.CANCELLED
    assert tm.mark_failed(chunk.chunk_id, "worker-a") is False
    assert tm.mark_done(chunk.chunk_id, "worker-a", {"count": 100, "sum": 1}) is False
    assert chunk.status == ChunkStatus.CANCELLED
    assert tm.progress_snapshot()["pending"] == 0


def test_failure_from_wrong_worker_is_ignored():
    tm = TaskManager()
    tm.start_job("ownership-job", "cpu_benchmark", 100, 100)
    chunk = tm.next_chunk_for("worker-a")
    assert chunk is not None

    assert tm.mark_failed(chunk.chunk_id, "worker-b") is False
    assert chunk.status == ChunkStatus.ASSIGNED
    assert chunk.worker_id == "worker-a"


def test_master_validates_result_against_assigned_range():
    server = MasterServer.__new__(MasterServer)
    expected = {"start": 10, "end": 30}
    result = cpu_benchmark(expected)

    assert server._validate_result(result, expected)
    wrong_range = dict(result, start=9)
    assert not server._validate_result(wrong_range, expected)
    wrong_count = dict(result, count=999)
    assert not server._validate_result(wrong_count, expected)
    bad_digest = dict(result, digest="0" * 64)
    assert not server._validate_result(bad_digest, expected)


def test_cpu_task_stops_when_cancel_event_is_set():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(InterruptedError):
        cpu_benchmark({"start": 0, "end": 100_000, "_cancel_event": cancel})


def test_expected_params_only_return_for_current_worker():
    tm = TaskManager()
    tm.start_job("ownership-job", "cpu_benchmark", 10, 10)
    chunk = tm.next_chunk_for("worker-a")
    assert tm.expected_params(chunk.chunk_id, "worker-b") is None
    assert tm.expected_params(chunk.chunk_id, "worker-a") == {"start": 0, "end": 10}
