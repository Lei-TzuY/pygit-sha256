"""
pygit/remote_ssh.py
===================
SSH Remote Transport Protocol Parser & Runner
=============================================

Parses SSH URLs and prepares subprocess streams for git-upload-pack and git-receive-pack.

Supported formats:
  - ``git@github.com:user/repo.git``
  - ``ssh://user@host:22/path/to/repo.git``
"""

from __future__ import annotations

import re
import subprocess
from typing import List, NamedTuple, Optional, Tuple


class SSHUrl(NamedTuple):
    user: str
    host: str
    port: Optional[int]
    path: str


def parse_ssh_url(url: str) -> Optional[SSHUrl]:
    """Parse an SSH URL into user, host, port, and path components."""

    # Format 1: git@github.com:user/repo.git
    m1 = re.match(r"^(?:([^@]+)@)?([^:]+):(.+)$", url)
    if m1 and not url.startswith("http://") and not url.startswith("https://") and not url.startswith("ssh://"):
        user = m1.group(1) or "git"
        host = m1.group(2)
        path = m1.group(3)
        return SSHUrl(user=user, host=host, port=None, path=path)

    # Format 2: ssh://user@host:22/path/to/repo.git
    m2 = re.match(r"^ssh://(?:([^@]+)@)?([^:/]+)(?::(\d+))?/(.+)$", url)
    if m2:
        user = m2.group(1) or "git"
        host = m2.group(2)
        port = int(m2.group(3)) if m2.group(3) else None
        path = m2.group(4)
        return SSHUrl(user=user, host=host, port=port, path=path)

    return None


class SSHRemoteClient:
    """Executes remote git-upload-pack or git-receive-pack commands over SSH."""

    def __init__(self, url: str) -> None:
        parsed = parse_ssh_url(url)
        if not parsed:
            raise ValueError(f"Invalid SSH remote URL: '{url}'")
        self.spec = parsed

    def build_ssh_command(self, git_command: str) -> List[str]:
        target = f"{self.spec.user}@{self.spec.host}"
        cmd = ["ssh"]
        if self.spec.port:
            cmd.extend(["-p", str(self.spec.port)])
        cmd.extend([target, f"{git_command} '{self.spec.path}'"])
        return cmd

    def open_process(self, git_command: str) -> subprocess.Popen:
        """Spawn SSH subprocess with stdout/stdin pipes for pkt-line exchange."""
        cmd = self.build_ssh_command(git_command)
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
