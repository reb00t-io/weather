"""Tests for the self-improvement agent."""
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.agent import CmdResult
from src.agents.claude_runner import StreamBuffer
from src.agents.improve import (
    BRANCH_PREFIX,
    _branch_has_new_commits,
    _current_branch,
    _has_uncommitted_changes,
    run_iteration,
)
from src.agents.state import ImproveState


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_repo(tmp_path):
    """Create a minimal git repo with a clean working tree."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.fixture()
def state_path():
    # State file must be outside the git repo to avoid checkout conflicts
    with tempfile.TemporaryDirectory() as d:
        yield Path(d) / "state.json"


def _noop_claude(prompt, repo_root, claude_bin=None, output_buffer=None):
    return CmdResult(returncode=0, stdout="", stderr="")


def _committing_claude(prompt, repo_root, claude_bin=None, output_buffer=None):
    """Simulate Claude making a commit."""
    marker = repo_root / f"change-{hash(prompt) % 10000}.txt"
    marker.write_text(f"change from: {prompt[:40]}")
    subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "auto: improvement"], cwd=repo_root, check=True, capture_output=True)
    return CmdResult(returncode=0, stdout="", stderr="")


# ─── Git helper tests ────────────────────────────────────────────────────────

def test_current_branch(tmp_repo):
    assert _current_branch(tmp_repo) in ("main", "master")


def test_has_uncommitted_changes_false(tmp_repo):
    assert not _has_uncommitted_changes(tmp_repo)


def test_has_uncommitted_changes_true(tmp_repo):
    (tmp_repo / "new.txt").write_text("dirty")
    assert _has_uncommitted_changes(tmp_repo)


def test_branch_has_new_commits_false(tmp_repo):
    base = _current_branch(tmp_repo)
    subprocess.run(["git", "checkout", "-b", "test-branch"], cwd=tmp_repo, check=True, capture_output=True)
    assert not _branch_has_new_commits(tmp_repo, base, "test-branch")


def test_branch_has_new_commits_true(tmp_repo):
    base = _current_branch(tmp_repo)
    subprocess.run(["git", "checkout", "-b", "test-branch"], cwd=tmp_repo, check=True, capture_output=True)
    (tmp_repo / "change.txt").write_text("new")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "change"], cwd=tmp_repo, check=True, capture_output=True)
    assert _branch_has_new_commits(tmp_repo, base, "test-branch")


# ─── State persistence ──────────────────────────────────────────────────────

def test_state_save_and_load(tmp_path):
    path = tmp_path / "state.json"
    state = ImproveState(iteration=3, phase="reviewing", branch="auto-improve/iter-3")
    state.save(path)

    loaded = ImproveState.load(path)
    assert loaded.iteration == 3
    assert loaded.phase == "reviewing"
    assert loaded.branch == "auto-improve/iter-3"


def test_state_load_missing_file(tmp_path):
    state = ImproveState.load(tmp_path / "missing.json")
    assert state.iteration == 0
    assert state.phase == "idle"


def test_state_load_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json{{{")
    state = ImproveState.load(path)
    assert state.iteration == 0


def test_state_is_resumable():
    assert not ImproveState(phase="idle").is_resumable
    assert not ImproveState(phase="error").is_resumable
    assert not ImproveState(phase="improving", branch="").is_resumable
    assert ImproveState(phase="improving", branch="auto-improve/iter-1").is_resumable
    assert ImproveState(phase="reviewing", branch="auto-improve/iter-1").is_resumable
    assert ImproveState(phase="planning", branch="auto-improve/iter-1").is_resumable


def test_state_record_completion():
    state = ImproveState(iteration=1, branch="auto-improve/iter-1")
    state.record_completion(changed=True, timestamp="2026-01-01")
    assert len(state.history) == 1
    assert state.history[0]["changed"] is True


def test_state_history_bounded():
    state = ImproveState()
    for i in range(60):
        state.record_completion(changed=False, timestamp=f"t-{i}")
    assert len(state.history) == 50


# ─── StreamBuffer ────────────────────────────────────────────────────────────

def test_stream_buffer_basic():
    buf = StreamBuffer(maxlen=5)
    for i in range(10):
        buf.append(f"line-{i}")
    assert buf.line_count == 5
    assert buf.get_lines() == ["line-5", "line-6", "line-7", "line-8", "line-9"]
    assert buf.get_lines(last_n=2) == ["line-8", "line-9"]


# ─── Iteration lifecycle ─────────────────────────────────────────────────────

def test_dry_run_no_branch(tmp_repo, state_path):
    state = ImproveState()
    result = run_iteration(tmp_repo, state, state_path, dry_run=True)
    assert result is False
    assert state.iteration == 0  # rolled back

    branches = subprocess.run(
        ["git", "branch", "--list", f"{BRANCH_PREFIX}/*"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert branches.stdout.strip() == ""


def test_aborts_on_dirty_tree(tmp_repo, state_path):
    (tmp_repo / "dirty.txt").write_text("uncommitted")
    state = ImproveState()
    result = run_iteration(tmp_repo, state, state_path)
    assert result is False
    assert state.iteration == 0


def test_no_changes_cleans_up_branch(tmp_repo, state_path):
    """When Claude makes no commits, branch is deleted, state is idle."""
    base = _current_branch(tmp_repo)
    state = ImproveState()

    with patch("src.agents.improve.run_claude", side_effect=_noop_claude):
        changed = run_iteration(tmp_repo, state, state_path)

    assert changed is False
    assert _current_branch(tmp_repo) == base
    assert state.phase == "idle"
    assert state.iteration == 1
    assert len(state.history) == 1
    assert state.history[0]["changed"] is False

    branches = subprocess.run(
        ["git", "branch", "--list", f"{BRANCH_PREFIX}/*"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert branches.stdout.strip() == ""


def test_with_changes_keeps_branch(tmp_repo, state_path):
    """When Claude commits, branch is kept, all 3 phases run."""
    base = _current_branch(tmp_repo)
    state = ImproveState()
    phase_calls = []

    def tracking_claude(prompt, repo_root, claude_bin=None, output_buffer=None):
        phase_calls.append(prompt[:20])
        return _committing_claude(prompt, repo_root, claude_bin, output_buffer)

    with patch("src.agents.improve.run_claude", side_effect=tracking_claude):
        changed = run_iteration(tmp_repo, state, state_path)

    assert changed is True
    assert _current_branch(tmp_repo) == base
    assert state.phase == "idle"
    assert state.iteration == 1
    assert len(phase_calls) == 3  # improve, review, plan
    assert state.history[0]["changed"] is True

    branches = subprocess.run(
        ["git", "branch", "--list", f"{BRANCH_PREFIX}/*"],
        cwd=tmp_repo, capture_output=True, text=True,
    )
    assert BRANCH_PREFIX in branches.stdout


def test_iteration_counter_increments(tmp_repo, state_path):
    state = ImproveState()

    with patch("src.agents.improve.run_claude", side_effect=_noop_claude):
        run_iteration(tmp_repo, state, state_path)
        run_iteration(tmp_repo, state, state_path)

    assert state.iteration == 2
    assert len(state.history) == 2


def test_state_persisted_between_phases(tmp_repo, state_path):
    """State file is written at each phase transition."""
    state = ImproveState()
    saved_phases = []

    original_save = ImproveState.save

    def tracking_save(self, path):
        saved_phases.append(self.phase)
        original_save(self, path)

    with patch("src.agents.improve.run_claude", side_effect=_committing_claude), \
         patch.object(ImproveState, "save", tracking_save):
        run_iteration(tmp_repo, state, state_path)

    # Should see: improving, reviewing, planning, idle
    assert "improving" in saved_phases
    assert "reviewing" in saved_phases
    assert "planning" in saved_phases
    assert saved_phases[-1] == "idle"


def test_crash_recovery_resumes_from_phase(tmp_repo, state_path):
    """If state says 'reviewing', skip improve and start from review."""
    base = _current_branch(tmp_repo)

    # Set up: create the branch with a commit as if improve already ran
    branch = f"{BRANCH_PREFIX}/iter-1"
    subprocess.run(["git", "checkout", "-b", branch], cwd=tmp_repo, check=True, capture_output=True)
    (tmp_repo / "from_improve.txt").write_text("change")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "improve phase"], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", base], cwd=tmp_repo, check=True, capture_output=True)

    # State says we crashed during reviewing
    state = ImproveState(iteration=1, phase="reviewing", branch=branch, base_branch=base)
    state.save(state_path)

    phase_calls = []

    def tracking_claude(prompt, repo_root, claude_bin=None, output_buffer=None):
        phase_calls.append(prompt[:30])
        return _noop_claude(prompt, repo_root, claude_bin, output_buffer)

    with patch("src.agents.improve.run_claude", side_effect=tracking_claude):
        changed = run_iteration(tmp_repo, state, state_path)

    assert changed is True  # the pre-existing commit counts
    assert _current_branch(tmp_repo) == base
    assert state.phase == "idle"
    # Should only run review and plan, not improve
    assert len(phase_calls) == 2


def test_error_sets_error_state(tmp_repo, state_path):
    state = ImproveState()

    def failing_claude(prompt, repo_root, claude_bin=None, output_buffer=None):
        raise RuntimeError("Claude crashed")

    with patch("src.agents.improve.run_claude", side_effect=failing_claude):
        with pytest.raises(RuntimeError, match="Claude crashed"):
            run_iteration(tmp_repo, state, state_path)

    # State should be saved as error
    loaded = ImproveState.load(state_path)
    assert loaded.phase == "error"
    assert loaded.error_count == 1
    assert "Claude crashed" in (loaded.last_error or "")
