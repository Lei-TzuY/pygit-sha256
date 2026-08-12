"""
Git configuration management module.

Handles INI-style configuration files stored in `.pygit/config`.
"""
from __future__ import annotations
import configparser
from pathlib import Path


class GitConfig:
    """Manages pygit configuration settings."""

    def __init__(self, pygit_dir: Path) -> None:
        """
        Initialize GitConfig with the .pygit directory path.

        Args:
            pygit_dir: Path to the .pygit directory.
        """
        self.config_path = pygit_dir / "config"
        self.parser = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        """Load configuration from the config file if it exists."""
        if self.config_path.exists():
            self.parser.read(self.config_path)

    def save(self) -> None:
        """Save the current configuration to the config file."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w") as f:
            self.parser.write(f)

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        """
        Retrieve a configuration value.

        Args:
            section: The configuration section.
            key: The configuration key.
            default: The default value to return if not found.

        Returns:
            The configuration value, or the default if not present.
        """
        return self.parser.get(section, key, fallback=default)

    def set(self, section: str, key: str, value: str) -> None:
        """
        Set a configuration value.

        Args:
            section: The configuration section.
            key: The configuration key.
            value: The value to set.
        """
        if not self.parser.has_section(section):
            self.parser.add_section(section)
        self.parser.set(section, key, value)
        self.save()

    def unset(self, section: str, key: str) -> bool:
        """
        Unset a configuration value.

        Args:
            section: The configuration section.
            key: The configuration key.

        Returns:
            True if the key was removed, False otherwise.
        """
        if self.parser.has_section(section):
            removed = self.parser.remove_option(section, key)
            if removed:
                self.save()
                return True
        return False

    def list_all(self) -> list[tuple[str, str, str]]:
        """
        List all configuration values.

        Returns:
            A list of tuples containing (section, key, value).
        """
        result = []
        for section in self.parser.sections():
            for key, value in self.parser.items(section):
                result.append((section, key, value))
        return result
