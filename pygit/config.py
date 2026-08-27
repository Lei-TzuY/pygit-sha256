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
        # Git permits multi-valued keys such as push.pushOption.  ConfigParser's
        # default strict mode rejects duplicate keys while reading, so use
        # strict=False and expose get_all() below for callers that need every
        # value rather than the final one.
        self.parser = configparser.ConfigParser(strict=False)
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

    def get_all(self, section: str, key: str) -> list[str]:
        """Return all values for a potentially multi-valued config key.

        ConfigParser keeps only the last duplicate key, so this method scans the
        local config file in source order.  An empty value clears values seen
        earlier, matching Git's multi-valued reset convention used by
        ``push.pushOption``.
        """
        if not self.config_path.exists():
            return []

        target_section = section.strip().lower()
        target_key = key.strip().lower()
        current_section: str | None = None
        values: list[str] = []

        for raw_line in self.config_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(("#", ";")):
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                current_section = stripped[1:-1].strip().lower()
                continue
            if current_section != target_section:
                continue

            if "=" in raw_line:
                raw_key, raw_value = raw_line.split("=", 1)
                parsed_key = raw_key.strip().lower()
                value = raw_value.strip()
            else:
                parts = stripped.split(None, 1)
                parsed_key = parts[0].lower()
                value = parts[1].strip() if len(parts) == 2 else ""

            if parsed_key != target_key:
                continue
            if value == "":
                values.clear()
            else:
                values.append(value)

        return values

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
