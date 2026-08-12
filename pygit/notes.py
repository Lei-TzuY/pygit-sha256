"""
Git notes management module.

Handles attaching notes to commits without changing their SHAs.
"""
from __future__ import annotations
import json
from pathlib import Path

from .objects import BlobObject
from .store import ObjectStore


class NoteStore:
    """Manages notes attached to commits."""

    def __init__(self, store: ObjectStore, pygit_dir: Path) -> None:
        """
        Initialize NoteStore with the object store and .pygit directory path.

        Args:
            store: The object store instance.
            pygit_dir: Path to the .pygit directory.
        """
        self.store = store
        self.notes_file = pygit_dir / "notes.json"
        self._mapping: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        """Load the notes mapping from the notes JSON file."""
        if self.notes_file.exists():
            with open(self.notes_file, "r", encoding="utf-8") as f:
                self._mapping = json.load(f)

    def _save(self) -> None:
        """Save the notes mapping to the notes JSON file."""
        self.notes_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.notes_file, "w", encoding="utf-8") as f:
            json.dump(self._mapping, f, indent=4)

    def add(self, commit_sha: str, message: str) -> str:
        """
        Add a note to a commit.

        Args:
            commit_sha: The SHA of the commit to attach the note to.
            message: The note message.

        Returns:
            The SHA of the saved note blob.
        """
        blob = BlobObject(message.encode("utf-8"))
        note_sha = self.store.write(blob)
        self._mapping[commit_sha] = note_sha
        self._save()
        return note_sha

    def show(self, commit_sha: str) -> str | None:
        """
        Show the note for a commit.

        Args:
            commit_sha: The SHA of the commit.

        Returns:
            The note text if it exists, otherwise None.
        """
        note_sha = self._mapping.get(commit_sha)
        if not note_sha:
            return None
        
        try:
            blob = self.store.read(note_sha)
            if isinstance(blob, BlobObject):
                return blob.data.decode("utf-8")
        except Exception:
            pass
        return None

    def list_all(self) -> list[tuple[str, str]]:
        """
        List all notes.

        Returns:
            A list of tuples containing (commit_sha, note_sha).
        """
        return list(self._mapping.items())

    def remove(self, commit_sha: str) -> bool:
        """
        Remove a note from a commit.

        Args:
            commit_sha: The SHA of the commit.

        Returns:
            True if the note was removed, False otherwise.
        """
        if commit_sha in self._mapping:
            del self._mapping[commit_sha]
            self._save()
            return True
        return False
