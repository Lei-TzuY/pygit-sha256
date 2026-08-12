"""
pygit/bundle.py
===============
Git Bundle Creator and Verifier
===============================

Packaged binary bundle format:
------------------------------
# v2 git bundle
<sha> <refname>
...
<empty line>
<embedded .pack payload>
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple


class BundleEngine:
    """Creates and verifies Git Bundle binary files."""

    HEADER = b"# v2 git bundle\n"

    def create_bundle(
        self,
        output_file: Path,
        ref_map: Dict[str, str],
        pack_data: bytes,
    ) -> Path:
        """
        Write bundle header, ref map, and packfile bytes into *output_file*.
        """
        output_file.parent.mkdir(parents=True, exist_ok=True)
        data = bytearray()
        data.extend(self.HEADER)

        for sha, ref in ref_map.items():
            line = f"{sha} {ref}\n".encode("utf-8")
            data.extend(line)

        # Empty line delimiter
        data.extend(b"\n")
        data.extend(pack_data)

        output_file.write_bytes(data)
        return output_file

    def verify_bundle(self, bundle_file: Path) -> Dict[str, object]:
        """
        Verify bundle binary format and extract ref table.
        """
        if not bundle_file.exists():
            raise FileNotFoundError(f"Bundle file not found: {bundle_file}")

        content = bundle_file.read_bytes()
        if not content.startswith(self.HEADER):
            raise ValueError("Invalid bundle header.")

        pos = len(self.HEADER)
        ref_map: Dict[str, str] = {}

        while pos < len(content):
            newline_idx = content.find(b"\n", pos)
            if newline_idx == -1:
                break

            line = content[pos:newline_idx].decode("utf-8")
            pos = newline_idx + 1

            if not line:
                # End of header / start of packfile
                break

            parts = line.split()
            if len(parts) == 2:
                ref_map[parts[1]] = parts[0]

        pack_bytes = content[pos:]
        if not pack_bytes.startswith(b"PACK"):
            raise ValueError("Invalid embedded packfile stream in bundle.")

        return {
            "status": "valid",
            "refs": ref_map,
            "pack_size": len(pack_bytes),
        }
