"""Verified Citi Bike discovery, bounded downloads, and GBFS envelopes."""

from __future__ import annotations

import json
import shutil
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

DISCOVERY = "https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json"
BUCKET = "https://s3.amazonaws.com/tripdata"
UTC = timezone.utc  # noqa: UP017 -- local Python 3.10 verification


class RateLimited(RuntimeError):
    def __init__(self, seconds: int) -> None:
        self.retry_after = seconds
        super().__init__(f"Source rate limited; retry after {seconds} seconds")


def retry_seconds(value: str, fallback: int) -> int:
    if value.isdigit():
        return int(value)
    try:
        return max(0, int((parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()))
    except (ValueError, TypeError):
        return fallback


class SourceClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=60,
            follow_redirects=True,
            headers={"User-Agent": "will-there-be-a-bike/0.1 (open-source research importer)"},
        )

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in range(4):
            fallback = 2**attempt
            try:
                response = self.client.get(url, **kwargs)
                if response.status_code not in (429, 500, 502, 503, 504):
                    response.raise_for_status()
                    return response
                delay = response.headers.get("Retry-After", "")
                if response.status_code == 429:
                    fallback = 60 * 2**attempt
                # Never hammer a service asking for a long backoff.
                if retry_seconds(delay, fallback) > 120:
                    raise RateLimited(retry_seconds(delay, fallback))
            except httpx.TransportError:
                if attempt == 3:
                    raise
                delay = ""
            time.sleep(retry_seconds(delay, fallback))
        raise RuntimeError(f"Source unavailable after bounded retries: {url}")

    def feeds(self, url: str = DISCOVERY) -> dict[str, str]:
        document = self.get(url).json()
        data = document["data"]
        if "feeds" in data:
            feeds = data["feeds"]
        else:
            language = data.get("en") or next(
                (value for value in data.values() if isinstance(value, dict) and "feeds" in value),
                {},
            )
            feeds = language.get("feeds", [])
        result = {item["name"]: item["url"] for item in feeds}
        if not {"station_information", "station_status"} <= result.keys():
            raise ValueError("GBFS discovery missing station information/status")
        return result

    def archives(self, month: str) -> list[tuple[str, int]]:
        """List bucket keys instead of constructing assumed archive filenames."""
        prefix = month.replace("-", "")
        token: str | None = None
        found: list[tuple[str, int]] = []
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        while True:
            params = {"list-type": "2", "prefix": prefix}
            if token:
                params["continuation-token"] = token
            root = ET.fromstring(self.get(BUCKET, params=params).content)
            for item in root.findall("s:Contents", ns):
                key = item.findtext("s:Key", namespaces=ns) or ""
                if key.endswith(".zip"):
                    found.append((key, int(item.findtext("s:Size", namespaces=ns) or 0)))
            token = root.findtext("s:NextContinuationToken", namespaces=ns)
            if not token:
                break
        if not found:
            raise ValueError(f"No official archives found for {month}")
        return found

    def download(self, key: str, directory: Path, max_bytes: int = 2_000_000_000) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / Path(key).name
        if target.exists():
            return target  # ZIP CRC/schema validation still occurs in the importer.
        partial = target.with_suffix(".zip.partial")
        validator_file = partial.with_suffix(".validator.json")
        url = f"{BUCKET}/{quote(key)}"
        failures = 0
        for _attempt in range(128):
            offset = partial.stat().st_size if partial.exists() and validator_file.exists() else 0
            headers = {"Range": f"bytes={offset}-{offset + 32 * 1024 * 1024 - 1}"}
            if offset:
                validator = json.loads(validator_file.read_text())["etag"]
                headers["If-Range"] = validator
            try:
                with self.client.stream("GET", url, headers=headers) as response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        retry = response.headers.get("Retry-After", "")
                        failures += 1
                        fallback = (
                            60 * 2 ** (failures - 1) if response.status_code == 429 else 2**failures
                        )
                        if retry_seconds(retry, fallback) > 120:
                            raise RateLimited(retry_seconds(retry, fallback))
                        if failures >= 4:
                            raise RuntimeError("Download source unavailable after bounded retries")
                        time.sleep(retry_seconds(retry, fallback))
                        continue
                    response.raise_for_status()
                    if response.status_code == 206:
                        if not response.headers.get("Content-Range", "").startswith(
                            f"bytes {offset}-"
                        ):
                            raise ValueError("Invalid source range response")
                        expected = int(response.headers["Content-Range"].split("/")[1])
                        etag = response.headers.get("ETag", "")
                        if offset and etag != headers["If-Range"]:
                            raise ValueError("Source validator changed during ranged download")
                        if not etag and expected > 32 * 1024 * 1024:
                            raise ValueError("Source cannot safely resume without a validator")
                    else:
                        offset = 0
                        expected = int(response.headers.get("Content-Length", "0"))
                    if expected > max_bytes:
                        raise ValueError("Archive exceeds configured disk budget")
                    validator_file.write_text(
                        json.dumps({"etag": response.headers.get("ETag", "")})
                    )
                    total = offset
                    if shutil.disk_usage(directory).free < max_bytes + 1_000_000_000:
                        raise ValueError("Insufficient disk headroom for bounded download")
                    with partial.open("ab" if offset else "wb") as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > max_bytes:
                                raise ValueError("Archive exceeded configured disk budget")
                            output.write(chunk)
                failures = 0
                if expected and total < expected:
                    continue
                partial.replace(target)
                validator_file.unlink(missing_ok=True)
                return target
            except httpx.TransportError:
                failures += 1
                if failures >= 4:
                    raise
                time.sleep(2**failures)
        raise RuntimeError("Download exhausted bounded retries")
