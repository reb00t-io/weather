"""Persistent state for the self-improvement agent."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

PHASES = ("idle", "improving", "reviewing", "planning", "error")


@dataclass
class ImproveState:
    iteration: int = 0
    phase: str = "idle"
    branch: str = ""
    base_branch: str = ""
    last_run: str = ""
    last_error: str | None = None
    error_count: int = 0
    history: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> ImproveState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            return cls(
                iteration=data.get("iteration", 0),
                phase=data.get("phase", "idle"),
                branch=data.get("branch", ""),
                base_branch=data.get("base_branch", ""),
                last_run=data.get("last_run", ""),
                last_error=data.get("last_error"),
                error_count=data.get("error_count", 0),
                history=data.get("history", []),
            )
        except Exception:
            logger.warning("Corrupt state file at %s — starting fresh", path)
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @property
    def is_resumable(self) -> bool:
        return self.phase not in ("idle", "error") and bool(self.branch)

    def record_completion(self, changed: bool, timestamp: str) -> None:
        self.history.append({
            "iteration": self.iteration,
            "timestamp": timestamp,
            "changed": changed,
            "branch": self.branch,
        })
        # Keep last 50 entries
        if len(self.history) > 50:
            self.history = self.history[-50:]
