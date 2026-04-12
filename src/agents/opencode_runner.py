from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from .agent import CmdResult, CmdResultLike


def build_opencode_command(prompt: str, opencode_bin: str | None = None) -> list[str]:
    binary = opencode_bin or os.environ.get("GREMLIN_OPENCODE_BIN", "opencode")
    return [binary, "run", "-m", "pm/gpt-oss-120b", prompt]


def run_opencode(
    prompt: str,
    repo_root: Path,
    opencode_bin: str | None = None,
) -> CmdResultLike:
    command = build_opencode_command(prompt=prompt, opencode_bin=opencode_bin)
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

    for line in proc.stdout:
        stdout_lines.append(line)
        sys.stdout.write(line)
        sys.stdout.flush()

    proc.wait()
    stderr_thread.join()
    return CmdResult(proc.returncode, "".join(stdout_lines), "".join(stderr_lines))
