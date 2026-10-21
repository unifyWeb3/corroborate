#!/usr/bin/env python3
"""Resumable download of the official GenVM bundle, verified against SHA256SUMS.

Uses urllib with a Range header so a restart can continue where it stopped.
"""
import hashlib
import os
import sys
import time
import urllib.request

VERSION = os.environ.get("GENVM_VERSION", "v0.6.0-rc10")
ASSET = os.environ.get("GENVM_ASSET", "genvm-universal.tar.xz")
BASE = "https://github.com/genlayerlabs/genvm-manager/releases/download"
DEST = f"/root/.cache/gltest-direct/bundles-v2/genvm-universal-{VERSION}.tar.xz"
SUMS = f"{BASE}/{VERSION}/SHA256SUMS"


def expected_sha() -> str:
    req = urllib.request.Request(SUMS, headers={"User-Agent": "corroborate-build"})
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r.read().decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ASSET:
                return parts[0]
    raise SystemExit(f"{ASSET} not found in SHA256SUMS")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def download(want: str) -> bool:
    for attempt in range(1, 41):
        have = size_of(DEST)
        if have and sha256_file(DEST) == want:
            print(f"verified after {have} bytes", flush=True)
            return True
        headers = {"User-Agent": "corroborate-build"}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(f"{BASE}/{VERSION}/{ASSET}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                with open(DEST, mode) as fh:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        if have % (20 << 20) < (1 << 20):
                            print(f"  {have // (1 << 20)} MiB", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"attempt {attempt}: {exc!r}", flush=True)
            time.sleep(3)
            continue
        print(f"attempt {attempt}: downloaded {size_of(DEST)} bytes", flush=True)
    return False


def main() -> int:
    want = expected_sha()
    print("expected", want, flush=True)
    if not download(want):
        print("DOWNLOAD FAILED", flush=True)
        return 1
    print("SHA256 OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
