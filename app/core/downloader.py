from __future__ import annotations

from pathlib import Path

import httpx


class Downloader:
    def __init__(self, http_client=None, timeout: float = 120.0) -> None:
        self.http_client = http_client or httpx.Client(follow_redirects=True)
        self.timeout = timeout

    def download(self, url: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        part_path = destination.with_suffix(destination.suffix + ".part")
        if destination.exists() and destination.stat().st_size > 0:
            return destination
        if part_path.exists():
            part_path.unlink()
        with self.http_client.stream("GET", url, timeout=self.timeout) as response:
            response.raise_for_status()
            with part_path.open("wb") as fh:
                for chunk in response.iter_bytes():
                    if chunk:
                        fh.write(chunk)
        if not part_path.exists() or part_path.stat().st_size <= 0:
            raise RuntimeError(f"Downloaded file is empty: {destination}")
        part_path.replace(destination)
        return destination
