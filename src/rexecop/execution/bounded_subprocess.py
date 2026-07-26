from __future__ import annotations

import hashlib
import os
import selectors
import signal
import subprocess as _subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, BinaryIO, cast

OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
TimeoutExpired = _subprocess.TimeoutExpired

_READ_CHUNK_BYTES = 64 * 1024
_TERMINATE_GRACE_SECONDS = 0.1
_CLEANUP_GRACE_SECONDS = 1.0
_OUTPUT_LIMIT_UNSET = object()


@dataclass(frozen=True)
class CapturedStream:
    text: str
    digest: str
    total_bytes: int
    retained_bytes: int
    truncated: bool


@dataclass(frozen=True)
class BoundedSubprocessResult:
    args: tuple[str, ...]
    returncode: int
    stdout: CapturedStream
    stderr: CapturedStream
    output_limit_exceeded: bool
    peak_retained_bytes: int


@dataclass
class _StreamAccumulator:
    retained: bytearray = field(default_factory=bytearray)
    digest: Any = field(default_factory=hashlib.sha256)
    total_bytes: int = 0

    def consume(self, chunk: bytes, *, retain_bytes: int) -> None:
        self.digest.update(chunk)
        self.total_bytes += len(chunk)
        if retain_bytes > 0:
            self.retained.extend(chunk[:retain_bytes])

    def result(self) -> CapturedStream:
        return CapturedStream(
            text=bytes(self.retained).decode("utf-8", errors="ignore").strip(),
            digest="sha256:" + self.digest.hexdigest(),
            total_bytes=self.total_bytes,
            retained_bytes=len(self.retained),
            truncated=self.total_bytes > len(self.retained),
        )


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    max_output_bytes: int,
) -> BoundedSubprocessResult:
    """Run exact argv with one combined, incrementally enforced output ceiling."""

    normalized_argv = tuple(str(item) for item in argv)
    output_limit = validate_output_limit(max_output_bytes)
    popen_kwargs: dict[str, Any] = {
        "stdin": _subprocess.DEVNULL,
        "stdout": _subprocess.PIPE,
        "stderr": _subprocess.PIPE,
        "shell": False,
        "bufsize": 0,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True
    elif hasattr(_subprocess, "CREATE_NEW_PROCESS_GROUP"):
        popen_kwargs["creationflags"] = _subprocess.CREATE_NEW_PROCESS_GROUP

    process = _subprocess.Popen(list(normalized_argv), **popen_kwargs)
    raw_pipes: tuple[BinaryIO, ...] = tuple(
        cast(BinaryIO, pipe)
        for pipe in (process.stdout, process.stderr)
        if pipe is not None
    )
    streams = {
        "stdout": _StreamAccumulator(),
        "stderr": _StreamAccumulator(),
    }
    selector: selectors.BaseSelector | None = None
    termination_reason = ""
    peak_retained_bytes = 0
    process_reaped = False

    try:
        if process.stdout is None or process.stderr is None:  # pragma: no cover
            raise RuntimeError("bounded subprocess pipes are unavailable")
        selector = selectors.DefaultSelector()
        for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, data=name)

        deadline = time.monotonic() + timeout
        termination_started = 0.0
        kill_sent = False
        cleanup_deadline = 0.0
        while selector.get_map():
            now = time.monotonic()
            if not termination_reason and now >= deadline:
                termination_reason = "timeout"
                termination_started = now
                _terminate_process_group(process)
            if (
                termination_reason
                and not kill_sent
                and now >= termination_started + _TERMINATE_GRACE_SECONDS
            ):
                _kill_process_group(process)
                kill_sent = True
                cleanup_deadline = now + _CLEANUP_GRACE_SECONDS
            if kill_sent and now >= cleanup_deadline:
                break

            wake_at = deadline
            if termination_reason and not kill_sent:
                wake_at = termination_started + _TERMINATE_GRACE_SECONDS
            elif kill_sent:
                wake_at = cleanup_deadline
            events = selector.select(max(0.0, wake_at - now))
            for key, _mask in sorted(events, key=lambda event: str(event[0].data)):
                pipe = cast(BinaryIO, key.fileobj)
                total_drained = sum(stream.total_bytes for stream in streams.values())
                read_size = _READ_CHUNK_BYTES
                if not termination_reason:
                    read_size = min(read_size, max(1, output_limit - total_drained + 1))
                try:
                    chunk = _read_pipe(pipe.fileno(), read_size)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(pipe)
                    pipe.close()
                    continue

                retained_total = sum(len(stream.retained) for stream in streams.values())
                retain_bytes = min(len(chunk), max(0, output_limit - retained_total))
                streams[str(key.data)].consume(chunk, retain_bytes=retain_bytes)
                retained_total += retain_bytes
                peak_retained_bytes = max(peak_retained_bytes, retained_total)

                total_drained += len(chunk)
                if not termination_reason and total_drained > output_limit:
                    termination_reason = OUTPUT_LIMIT_EXCEEDED
                    termination_started = time.monotonic()
                    _terminate_process_group(process)

        if not termination_reason and process.poll() is None:
            try:
                process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except _subprocess.TimeoutExpired:
                termination_reason = "timeout"
                _terminate_process_group(process)
        if termination_reason:
            _kill_process_group(process)
        _close_capture_resources(selector, raw_pipes)
        _wait_for_process(process)
        process_reaped = True
        result = BoundedSubprocessResult(
            args=normalized_argv,
            returncode=int(process.returncode),
            stdout=streams["stdout"].result(),
            stderr=streams["stderr"].result(),
            output_limit_exceeded=termination_reason == OUTPUT_LIMIT_EXCEEDED,
            peak_retained_bytes=peak_retained_bytes,
        )
    except BaseException:
        _abort_after_popen(
            process,
            selector=selector,
            raw_pipes=raw_pipes,
            process_reaped=process_reaped,
        )
        raise

    if termination_reason == "timeout":
        raise TimeoutExpired(normalized_argv, timeout)
    return result


def normalize_result(
    completed: object,
    *,
    max_output_bytes: int,
) -> BoundedSubprocessResult:
    """Normalize patched CompletedProcess-like fixtures used by connector tests."""

    output_limit = validate_output_limit(max_output_bytes)
    if isinstance(completed, BoundedSubprocessResult):
        return completed
    stdout_raw = _as_bytes(getattr(completed, "stdout", b""))
    stderr_raw = _as_bytes(getattr(completed, "stderr", b""))
    stdout_kept = stdout_raw[:output_limit]
    stderr_kept = stderr_raw[: max(0, output_limit - len(stdout_kept))]
    return BoundedSubprocessResult(
        args=(),
        returncode=int(getattr(completed, "returncode")),
        stdout=_completed_stream(stdout_raw, stdout_kept),
        stderr=_completed_stream(stderr_raw, stderr_kept),
        output_limit_exceeded=len(stdout_raw) + len(stderr_raw) > output_limit,
        peak_retained_bytes=len(stdout_kept) + len(stderr_kept),
    )


def resolve_output_limit(
    *,
    configured: object,
    policy: object = _OUTPUT_LIMIT_UNSET,
) -> int:
    """Resolve one positive configured limit and optional positive policy limit."""

    try:
        configured_limit = validate_output_limit(configured)
        if policy is _OUTPUT_LIMIT_UNSET:
            return configured_limit
        policy_limit = validate_output_limit(policy)
    except ValueError as exc:
        raise ValueError("invalid max_output_bytes") from exc
    return min(configured_limit, policy_limit)


def validate_output_limit(value: object) -> int:
    """Require an exact positive built-in int; never coerce bool/text/float."""

    if type(value) is not int:
        raise ValueError("max_output_bytes must be an exact integer")
    if value <= 0:
        raise ValueError("max_output_bytes must be greater than zero")
    return value


def _as_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8", errors="replace")


def _completed_stream(raw: bytes, retained: bytes) -> CapturedStream:
    return CapturedStream(
        text=retained.decode("utf-8", errors="ignore").strip(),
        digest="sha256:" + hashlib.sha256(raw).hexdigest(),
        total_bytes=len(raw),
        retained_bytes=len(retained),
        truncated=len(raw) > len(retained),
    )


def _terminate_process_group(process: _subprocess.Popen[bytes]) -> None:
    _signal_process_group(process, signal.SIGTERM)


def _kill_process_group(process: _subprocess.Popen[bytes]) -> None:
    _signal_process_group(process, signal.SIGKILL)


def _signal_process_group(process: _subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            if sig == signal.SIGTERM:
                process.terminate()
            else:
                process.kill()
    except ProcessLookupError:
        pass


def _wait_for_process(process: _subprocess.Popen[bytes]) -> None:
    try:
        process.wait(timeout=_CLEANUP_GRACE_SECONDS)
    except _subprocess.TimeoutExpired:
        _kill_process_group(process)
        process.wait(timeout=_CLEANUP_GRACE_SECONDS)


def _close_capture_resources(
    selector: selectors.BaseSelector,
    raw_pipes: tuple[BinaryIO, ...],
) -> None:
    for key in list(selector.get_map().values()):
        pipe = cast(BinaryIO, key.fileobj)
        selector.unregister(pipe)
        pipe.close()
    for pipe in raw_pipes:
        pipe.close()
    selector.close()


def _read_pipe(fd: int, size: int) -> bytes:
    return os.read(fd, size)


def _abort_after_popen(
    process: _subprocess.Popen[bytes],
    *,
    selector: selectors.BaseSelector | None,
    raw_pipes: tuple[BinaryIO, ...],
    process_reaped: bool,
) -> None:
    if not process_reaped:
        _ignore_cleanup_error(_terminate_process_group, process)
        _ignore_cleanup_error(_kill_process_group, process)
    for pipe in raw_pipes:
        _ignore_cleanup_error(pipe.close)
    if selector is not None:
        _ignore_cleanup_error(selector.close)
    if not process_reaped:
        _ignore_cleanup_error(process.wait, _CLEANUP_GRACE_SECONDS)
        if process.returncode is None:
            _ignore_cleanup_error(_kill_process_group, process)
            _ignore_cleanup_error(process.wait, _CLEANUP_GRACE_SECONDS)


def _ignore_cleanup_error(callback: Any, *args: Any) -> None:
    try:
        callback(*args)
    except BaseException:
        pass
