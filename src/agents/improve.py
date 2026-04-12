#!/usr/bin/env python3
"""Self-improvement agent with multi-phase lifecycle.

Runs Claude Code through three phases per iteration:
  1. IMPROVE — make one focused change, document in changes.md
  2. REVIEW  — review the changes, fix issues, run tests
  3. PLAN    — update plan.md with revised strategy

Each phase transition is persisted to state, so crashes can resume.
All work happens on a branch (auto-improve/iter-N) for safe review.

Usage:
    python -m src.agents.improve                    # single iteration
    python -m src.agents.improve --daemon           # loop (default: 24h)
    python -m src.agents.improve --interval 12      # loop every 12h
    python -m src.agents.improve --dry-run          # show what would happen
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .claude_runner import StreamBuffer, run_claude
from .state import ImproveState

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_INTERVAL_HOURS = 24
BRANCH_PREFIX = "auto-improve"
STATE_FILENAME = "data/improve-state.json"
LOG_DIR_NAME = "data/improve-logs"
ERROR_RETRY_SECONDS = 30


# ─── Git helpers ─────────────────────────────────────────────────────────────

def _repo_root() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent
    raise RuntimeError("Could not find git repository root")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def _current_branch(cwd: Path) -> str:
    return _git(["branch", "--show-current"], cwd).stdout.strip()


def _has_uncommitted_changes(cwd: Path) -> bool:
    return bool(_git(["status", "--porcelain"], cwd).stdout.strip())


def _branch_exists(cwd: Path, branch: str) -> bool:
    return _git(["rev-parse", "--verify", branch], cwd).returncode == 0


def _branch_has_new_commits(cwd: Path, base: str, branch: str) -> bool:
    return bool(_git(["log", f"{base}..{branch}", "--oneline"], cwd).stdout.strip())


def _checkout(cwd: Path, branch: str) -> bool:
    result = _git(["checkout", branch], cwd)
    if result.returncode != 0:
        logger.error("Failed to checkout %s: %s", branch, result.stderr.strip())
        return False
    return True


def _create_branch(cwd: Path, branch: str) -> bool:
    result = _git(["checkout", "-b", branch], cwd)
    if result.returncode != 0:
        logger.error("Failed to create branch %s: %s", branch, result.stderr.strip())
        return False
    return True


# ─── Phase runner ────────────────────────────────────────────────────────────

def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text().strip()


def _run_phase(
    phase_name: str,
    repo_root: Path,
    claude_bin: str | None = None,
    output_buffer: StreamBuffer | None = None,
) -> int:
    """Run a single Claude phase. Returns the exit code."""
    prompt = _load_prompt(phase_name)
    logger.info("── Phase: %s ──", phase_name.upper())
    result = run_claude(
        prompt=prompt,
        repo_root=repo_root,
        claude_bin=claude_bin,
        output_buffer=output_buffer,
    )
    return result.returncode


# ─── Iteration lifecycle ─────────────────────────────────────────────────────

def run_iteration(
    repo_root: Path,
    state: ImproveState,
    state_path: Path,
    dry_run: bool = False,
    claude_bin: str | None = None,
    output_buffer: StreamBuffer | None = None,
) -> bool:
    """Run one full iteration (IMPROVE → REVIEW → PLAN).

    Returns True if changes were made.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    is_resume = state.is_resumable

    if is_resume:
        # Crash recovery — resume where we left off
        logger.info(
            "Resuming iteration %d from phase '%s' on branch %s",
            state.iteration, state.phase, state.branch,
        )
        branch_name = state.branch
        base_branch = state.base_branch
    else:
        # Fresh iteration
        state.iteration += 1
        branch_name = f"{BRANCH_PREFIX}/iter-{state.iteration}"
        base_branch = _current_branch(repo_root)

        if not base_branch:
            logger.error("Not on a branch — aborting")
            return False

        if _has_uncommitted_changes(repo_root):
            logger.error("Uncommitted changes in working tree — aborting")
            state.iteration -= 1
            return False

        state.branch = branch_name
        state.base_branch = base_branch
        state.last_run = timestamp

    logger.info(
        "Iteration %d: branch=%s base=%s",
        state.iteration, branch_name, base_branch,
    )

    if dry_run:
        logger.info("[dry-run] Would run 3-phase iteration on branch %s", branch_name)
        state.iteration -= 1
        return False

    # Ensure we're on the right branch
    if is_resume:
        if not _branch_exists(repo_root, branch_name):
            logger.error("Resume branch %s no longer exists — resetting", branch_name)
            state.phase = "idle"
            state.save(state_path)
            return False
        if _current_branch(repo_root) != branch_name:
            if not _checkout(repo_root, branch_name):
                return False
    else:
        if not _create_branch(repo_root, branch_name):
            state.iteration -= 1
            return False

    made_changes = False
    try:
        # Phase 1: IMPROVE
        if state.phase in ("idle", "improving"):
            state.phase = "improving"
            state.save(state_path)
            exit_code = _run_phase("improve", repo_root, claude_bin, output_buffer)
            if exit_code != 0:
                logger.warning("IMPROVE phase exited with code %d", exit_code)

        # Check if improve phase produced any commits
        has_commits = _branch_has_new_commits(repo_root, base_branch, branch_name)

        if has_commits:
            # Phase 2: REVIEW
            if state.phase in ("improving", "reviewing"):
                state.phase = "reviewing"
                state.save(state_path)
                exit_code = _run_phase("review", repo_root, claude_bin, output_buffer)
                if exit_code != 0:
                    logger.warning("REVIEW phase exited with code %d", exit_code)

            # Phase 3: PLAN
            if state.phase in ("reviewing", "planning"):
                state.phase = "planning"
                state.save(state_path)
                exit_code = _run_phase("plan", repo_root, claude_bin, output_buffer)
                if exit_code != 0:
                    logger.warning("PLAN phase exited with code %d", exit_code)

        made_changes = _branch_has_new_commits(repo_root, base_branch, branch_name)

        # Record completion
        state.phase = "idle"
        state.error_count = 0
        state.last_error = None
        state.record_completion(made_changes, timestamp)
        state.save(state_path)

        if made_changes:
            logger.info("Iteration %d complete — branch %s has changes", state.iteration, branch_name)
        else:
            logger.info("Iteration %d complete — no changes", state.iteration)

    except Exception as exc:
        state.phase = "error"
        state.last_error = str(exc)
        state.error_count += 1
        state.save(state_path)
        raise

    finally:
        # Always return to base branch
        _checkout(repo_root, base_branch)

        # Clean up empty branches
        if not made_changes:
            _git(["branch", "-d", branch_name], repo_root)
            logger.info("Deleted empty branch %s", branch_name)

    return made_changes


# ─── Daemon loop ─────────────────────────────────────────────────────────────

def daemon_loop(
    repo_root: Path,
    state_path: Path,
    interval_hours: float,
    claude_bin: str | None = None,
) -> None:
    interval_seconds = interval_hours * 3600
    logger.info("Starting daemon: interval=%.1fh repo=%s", interval_hours, repo_root)

    output_buffer = StreamBuffer()

    while True:
        state = ImproveState.load(state_path)

        try:
            changed = run_iteration(
                repo_root, state, state_path,
                claude_bin=claude_bin,
                output_buffer=output_buffer,
            )
            logger.info("Run complete: iteration=%d changes=%s", state.iteration, changed)
        except KeyboardInterrupt:
            logger.info("Interrupted — exiting daemon")
            break
        except Exception:
            logger.exception("Improvement run failed (error_count=%d)", state.error_count)
            if state.error_count >= 3:
                logger.error("Too many consecutive errors — resetting state to idle")
                state.phase = "idle"
                state.error_count = 0
                state.save(state_path)
            logger.info("Retrying in %d seconds", ERROR_RETRY_SECONDS)
            try:
                time.sleep(ERROR_RETRY_SECONDS)
            except KeyboardInterrupt:
                logger.info("Interrupted during error backoff — exiting")
                break
            continue

        logger.info("Sleeping %.1f hours until next run", interval_hours)
        try:
            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Interrupted during sleep — exiting daemon")
            break


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Self-improvement agent: IMPROVE → REVIEW → PLAN",
    )
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL_HOURS,
        help=f"Hours between runs in daemon mode (default: {DEFAULT_INTERVAL_HOURS})",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    parser.add_argument("--claude-bin", type=str, default=None, help="Path to claude binary")
    args = parser.parse_args()

    repo_root = _repo_root()
    state_path = repo_root / STATE_FILENAME

    if args.daemon:
        daemon_loop(repo_root, state_path, args.interval, claude_bin=args.claude_bin)
        return 0

    state = ImproveState.load(state_path)
    changed = run_iteration(
        repo_root, state, state_path,
        dry_run=args.dry_run,
        claude_bin=args.claude_bin,
    )
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
