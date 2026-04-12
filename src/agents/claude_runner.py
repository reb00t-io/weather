"""Claude Code CLI wrapper with streaming output capture."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from .agent import CmdResult, CmdResultLike

logger = logging.getLogger(__name__)

MAX_OUTPUT_LINES = 2000


class StreamBuffer:
    """Bounded buffer for Claude's streaming output.

    Captures lines to a deque while forwarding to stdout. The buffer can
    be polled externally (e.g. by a web UI) to show live agent output.
    """

    def __init__(self, maxlen: int = MAX_OUTPUT_LINES):
        self._lines: deque[str] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            self._lines.append(line)

    def get_lines(self, last_n: int | None = None) -> list[str]:
        with self._lock:
            if last_n is None:
                return list(self._lines)
            return list(self._lines)[-last_n:]

    @property
    def line_count(self) -> int:
        with self._lock:
            return len(self._lines)


def build_claude_command(prompt: str, claude_bin: str | None = None) -> list[str]:
    binary = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
    return [
        binary,
        "-p",
        prompt,
        "--dangerously-skip-permissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def run_claude(
    prompt: str,
    repo_root: Path,
    claude_bin: str | None = None,
    output_buffer: StreamBuffer | None = None,
) -> CmdResultLike:
    """Run Claude Code CLI and return the result.

    If output_buffer is provided, streaming output is captured into it
    (in addition to being forwarded to stdout).
    """
    command = build_claude_command(prompt=prompt, claude_bin=claude_bin)
    logger.info("Running: %s (cwd=%s)", " ".join(command[:3]) + " ...", repo_root)
    stdout_lines: list[str] = []

    proc = subprocess.Popen(
        command,
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    stderr_lines: list[str] = []

    def collect_stderr() -> None:
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=collect_stderr, daemon=True)
    stderr_thread.start()

    def emit(line: str) -> None:
        try:
            event = json.loads(line)
            event_type = event.get("type")
            text = ""
            if event_type == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "text":
                        text = block["text"]
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta["text"]

            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
                if output_buffer is not None:
                    output_buffer.append(text)
        except json.JSONDecodeError:
            pass

    for line in proc.stdout:
        stdout_lines.append(line)
        emit(line)

    proc.wait()
    stderr_thread.join()

    result = CmdResult(proc.returncode, "".join(stdout_lines), "".join(stderr_lines))
    logger.info("Claude exited with code %d", result.returncode)
    return result
