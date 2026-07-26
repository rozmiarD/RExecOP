from __future__ import annotations

import errno
import hashlib
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from rexecop.execution.bounded_subprocess import TimeoutExpired, normalize_result, run

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
_REAL_POPEN = subprocess.Popen


def test_exit_status_counts_and_digests_cover_all_drained_bytes() -> None:
    stdout = b"stdout-bytes\n"
    stderr = b"stderr-bytes\n"
    code = (
        "import os,sys; "
        f"os.write(1, {stdout!r}); os.write(2, {stderr!r}); sys.exit(7)"
    )

    result = run([sys.executable, "-c", code], timeout=2, max_output_bytes=64)

    assert result.returncode == 7
    assert result.output_limit_exceeded is False
    assert result.stdout.total_bytes == len(stdout)
    assert result.stderr.total_bytes == len(stderr)
    assert result.stdout.digest == "sha256:" + hashlib.sha256(stdout).hexdigest()
    assert result.stderr.digest == "sha256:" + hashlib.sha256(stderr).hexdigest()
    assert result.peak_retained_bytes == len(stdout) + len(stderr)


@pytest.mark.parametrize(("fd", "stream_name"), [(1, "stdout"), (2, "stderr")])
def test_independent_stream_flood_is_stopped_during_capture(
    fd: int,
    stream_name: str,
) -> None:
    planned_bytes = 8 * 1024 * 1024
    code = f"import os; os.write({fd}, b'x' * {planned_bytes})"

    result = run([sys.executable, "-c", code], timeout=3, max_output_bytes=1024)
    stream = getattr(result, stream_name)

    assert result.output_limit_exceeded is True
    assert stream.total_bytes > 1024
    assert stream.total_bytes < planned_bytes
    assert stream.truncated is True
    assert result.peak_retained_bytes == 1024


def test_combined_stdout_stderr_flood_does_not_deadlock() -> None:
    code = """
import os
import threading

os.write(1, b'o' * 256)
os.write(2, b'e' * 256)
threads = [
    threading.Thread(target=os.write, args=(1, b'O' * (4 * 1024 * 1024))),
    threading.Thread(target=os.write, args=(2, b'E' * (4 * 1024 * 1024))),
]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
"""
    started = time.monotonic()

    result = run([sys.executable, "-c", code], timeout=3, max_output_bytes=512)

    assert time.monotonic() - started < 2
    assert result.output_limit_exceeded is True
    assert result.stdout.total_bytes >= 256
    assert result.stderr.total_bytes >= 256
    assert result.stdout.total_bytes + result.stderr.total_bytes > 512
    assert result.peak_retained_bytes == 512


def test_utf8_code_point_split_at_retained_boundary_is_deterministic() -> None:
    raw = "A€B".encode()
    code = f"import os; os.write(1, {raw!r})"

    result = run([sys.executable, "-c", code], timeout=2, max_output_bytes=2)

    assert result.output_limit_exceeded is True
    assert result.stdout.text == "A"
    assert result.stdout.total_bytes == len(raw)
    assert result.stdout.retained_bytes == 2
    assert result.stdout.digest == "sha256:" + hashlib.sha256(raw).hexdigest()


def test_timeout_is_distinct_from_output_limit() -> None:
    started = time.monotonic()

    with pytest.raises(TimeoutExpired):
        run(
            [
                sys.executable,
                "-c",
                "import os,time; os.close(1); os.close(2); time.sleep(30)",
            ],
            timeout=0.1,
            max_output_bytes=64,
        )

    assert time.monotonic() - started < 2


def test_output_limit_kills_descendant_group_and_closes_inherited_pipes(
    tmp_path: Path,
) -> None:
    pid_path = tmp_path / "descendant.pid"
    descendant = (
        "import os,time; "
        f"open({str(pid_path)!r}, 'w').write(str(os.getpid())); "
        "time.sleep(30)"
    )
    parent = (
        "import os,subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
        f"p={str(pid_path)!r}; "
        "deadline=time.monotonic()+2; "
        "exec(\"while not os.path.exists(p) and time.monotonic() < deadline:\\n "
        "time.sleep(0.01)\"); "
        "os.write(1, b'x' * (8 * 1024 * 1024))"
    )
    started = time.monotonic()

    result = run([sys.executable, "-c", parent], timeout=3, max_output_bytes=128)

    elapsed = time.monotonic() - started
    assert result.output_limit_exceeded is True
    assert elapsed < 2
    descendant_pid = int(pid_path.read_text(encoding="utf-8"))
    assert _wait_until_not_running(descendant_pid)


def test_exact_argv_is_not_interpreted_by_a_shell(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    literal = f"$(touch {marker});still-literal"

    result = run(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", literal],
        timeout=2,
        max_output_bytes=1024,
    )

    assert result.returncode == 0
    assert result.stdout.text == literal
    assert not marker.exists()


@pytest.mark.parametrize(
    "limit",
    [-1, 0, True, 1.0, "1", float("nan"), float("inf")],
)
def test_invalid_limit_is_rejected_before_popen(limit: object) -> None:
    with patch("rexecop.execution.bounded_subprocess._subprocess.Popen") as popen:
        with pytest.raises(ValueError):
            run([sys.executable, "-c", "pass"], timeout=1, max_output_bytes=limit)

    popen.assert_not_called()
    completed = subprocess.CompletedProcess(["fixture"], 0, "", "")
    with pytest.raises(ValueError):
        normalize_result(completed, max_output_bytes=limit)


@pytest.mark.parametrize("limit", [1, 1024 * 1024])
def test_positive_limits_are_accepted(limit: int) -> None:
    result = run(
        [sys.executable, "-c", "pass"],
        timeout=2,
        max_output_bytes=limit,
    )

    assert result.returncode == 0
    assert result.output_limit_exceeded is False


def test_completed_process_combined_output_above_limit_is_overflow() -> None:
    completed = subprocess.CompletedProcess(
        args=["fixture"],
        returncode=0,
        stdout="ab",
        stderr="cde",
    )

    result = normalize_result(completed, max_output_bytes=4)

    assert result.output_limit_exceeded is True
    assert result.stdout.text == "ab"
    assert result.stderr.text == "cd"
    assert result.stdout.total_bytes + result.stderr.total_bytes == 5
    assert result.peak_retained_bytes == 4


def test_selector_registration_failure_closes_and_reaps_child() -> None:
    tracked: list[tuple[subprocess.Popen[bytes], tuple[int, ...]]] = []
    injected = _InjectedSelector(
        selectors.DefaultSelector(),
        register_failure_at=2,
    )

    with (
        patch(
            "rexecop.execution.bounded_subprocess._subprocess.Popen",
            side_effect=_tracking_popen(tracked),
        ),
        patch(
            "rexecop.execution.bounded_subprocess.selectors.DefaultSelector",
            return_value=injected,
        ),
        pytest.raises(OSError) as raised,
    ):
        run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=2,
            max_output_bytes=64,
        )

    assert raised.value.errno == errno.EBADF
    _assert_process_and_fds_cleaned(tracked)


def test_selector_failure_preserves_original_error_and_reaps_child() -> None:
    tracked: list[tuple[subprocess.Popen[bytes], tuple[int, ...]]] = []
    injected = _InjectedSelector(
        selectors.DefaultSelector(),
        select_error=OSError(errno.EBADF, "injected select failure"),
        close_error=RuntimeError("secondary cleanup failure"),
    )

    with (
        patch(
            "rexecop.execution.bounded_subprocess._subprocess.Popen",
            side_effect=_tracking_popen(tracked),
        ),
        patch(
            "rexecop.execution.bounded_subprocess.selectors.DefaultSelector",
            return_value=injected,
        ),
        pytest.raises(OSError) as raised,
    ):
        run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=2,
            max_output_bytes=64,
        )

    assert raised.value.errno == errno.EBADF
    assert "injected select failure" in str(raised.value)
    _assert_process_and_fds_cleaned(tracked)


def test_read_failure_closes_and_reaps_child() -> None:
    tracked: list[tuple[subprocess.Popen[bytes], tuple[int, ...]]] = []

    with (
        patch(
            "rexecop.execution.bounded_subprocess._subprocess.Popen",
            side_effect=_tracking_popen(tracked),
        ),
        patch(
            "rexecop.execution.bounded_subprocess._read_pipe",
            side_effect=OSError(errno.EIO, "injected read failure"),
        ),
        pytest.raises(OSError) as raised,
    ):
        run(
            [
                sys.executable,
                "-c",
                "import os,time; os.write(1, b'x'); time.sleep(30)",
            ],
            timeout=2,
            max_output_bytes=64,
        )

    assert raised.value.errno == errno.EIO
    _assert_process_and_fds_cleaned(tracked)


class _InjectedSelector:
    def __init__(
        self,
        delegate: selectors.BaseSelector,
        *,
        register_failure_at: int = 0,
        select_error: OSError | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.delegate = delegate
        self.register_failure_at = register_failure_at
        self.select_error = select_error
        self.close_error = close_error
        self.register_calls = 0

    def register(self, fileobj: Any, events: int, data: Any = None) -> Any:
        self.register_calls += 1
        if self.register_calls == self.register_failure_at:
            raise OSError(errno.EBADF, "injected registration failure")
        return self.delegate.register(fileobj, events, data)

    def unregister(self, fileobj: Any) -> Any:
        return self.delegate.unregister(fileobj)

    def select(self, timeout: float | None = None) -> Any:
        if self.select_error is not None:
            raise self.select_error
        return self.delegate.select(timeout)

    def get_map(self) -> Any:
        return self.delegate.get_map()

    def close(self) -> None:
        self.delegate.close()
        if self.close_error is not None:
            raise self.close_error


def _tracking_popen(
    tracked: list[tuple[subprocess.Popen[bytes], tuple[int, ...]]],
) -> Any:
    def launch(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        process = _REAL_POPEN(*args, **kwargs)
        assert process.stdout is not None
        assert process.stderr is not None
        tracked.append(
            (process, (process.stdout.fileno(), process.stderr.fileno()))
        )
        return process

    return launch


def _assert_process_and_fds_cleaned(
    tracked: list[tuple[subprocess.Popen[bytes], tuple[int, ...]]],
) -> None:
    assert len(tracked) == 1
    process, fds = tracked[0]
    assert process.returncode is not None
    with pytest.raises(ChildProcessError):
        os.waitpid(process.pid, os.WNOHANG)
    for fd in fds:
        with pytest.raises(OSError) as raised:
            os.fstat(fd)
        assert raised.value.errno == errno.EBADF


def _wait_until_not_running(pid: int) -> bool:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        stat_path = Path(f"/proc/{pid}/stat")
        if not stat_path.exists():
            return True
        try:
            fields = stat_path.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return True
        if len(fields) > 2 and fields[2] == "Z":
            return True
        time.sleep(0.01)
    return False
