"""File-backed database-store wrapper for the research side.

The host application injects its own store object (``ResearchStore(...)``
wrapping ``get_aleggo_service().store``); this module provides the small
stand-in the research tree itself needs.  The evaluation harness
(:mod:`aleggo.study.research_eval`) runs every problem "inside a throw-away
store", and the research test-suite builds stores from plain temp directories --
neither can depend on the host app's real store.  The interface below is
exactly what the research tree uses (all call sites surveyed):

* ``root`` -- workspace root directory (created on demand),
* ``path`` -- SQLite database file inside ``root``,
* ``snapshot_database(name)`` -- timestamped backup of the database file,
  best-effort (returns the backup path, or ``None`` when there is nothing to
  back up or the copy failed).

Pure standard library, zero new dependencies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import shutil


class AleggoStore:
    """A store root on the local filesystem.

    ``root`` is a directory; the SQLite database lives at
    ``root / "aleggo.sqlite3"``.  Directories are created on demand so
    throw-away stores (evaluation runs, tests) work from an empty temp dir.
    """

    DB_FILENAME = "aleggo.sqlite3"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """Filesystem path of the SQLite database file."""
        return self.root / self.DB_FILENAME

    def snapshot_database(self, name: str) -> Path | None:
        """Copy the database to a timestamped backup; never raises.

        Returns the backup path, or ``None`` when there is no database file
        yet or the copy failed.  Callers (schema upgrades) treat backups as
        best-effort: a failed backup must not take down startup.
        """
        source = self.path
        if not source.is_file():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(name or "snapshot"))[:80] or "snapshot"
        dest = self.root / f"{source.stem}-{safe}-{stamp}{source.suffix}.bak"
        try:
            shutil.copy2(source, dest)
        except OSError:
            return None
        return dest
