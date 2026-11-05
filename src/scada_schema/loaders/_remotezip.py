"""Pick members out of a large remote zip with HTTP range requests, so a 1.4 GB yearly archive costs only the
tables that are needed (tens of MB)."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["RemoteFile", "extract_members"]


class RemoteFile(io.RawIOBase):
    """Read-only, seekable view of a remote file."""

    def __init__(self, session, url: str, size: int):
        self.session, self.url, self.size, self.pos = session, url, size, 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def readinto(self, buffer) -> int:
        n = min(len(buffer), self.size - self.pos)
        if n <= 0:
            return 0
        r = self.session.get(self.url, headers={"Range": f"bytes={self.pos}-{self.pos + n - 1}"}, timeout=300)
        r.raise_for_status()
        data = r.content
        if r.status_code != 206 or len(data) != n:
            raise OSError(f"range request failed ({r.status_code}, {len(data)} of {n} bytes)")
        buffer[:n] = data
        self.pos += n
        return n


def extract_members(session, info: dict, pattern: re.Pattern, dest: Path) -> Path:
    """Copy the members of a remote zip whose names match ``pattern`` into the local zip ``dest``.

    Every member is read through ``ZipFile.read``, which checks its CRC-32.
    """
    dest = Path(dest)
    if dest.exists():
        log.info("cached %s", dest.name)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    remote = zipfile.ZipFile(io.BufferedReader(RemoteFile(session, info["url"], info["size"]), 8 << 20))
    members = [m for m in remote.infolist() if pattern.search(m.filename)]
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for m in members:
            out.writestr(m.filename, remote.read(m))
    tmp.replace(dest)
    return dest
