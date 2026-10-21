#!/usr/bin/env python3
"""Fetch the GenVM bundle that the linter and the direct test runner need.

The bundle is the official release asset of `genlayerlabs/genvm-manager`, and it is
verified against the `SHA256SUMS` file published with that release before it is used.
The download is resumable, because the release is ~310 MiB.

    python scripts/fetch_genvm_bundle.py

Afterwards the tooling finds it in its own cache:

    GENVM_VERSION=v0.6.0-rc10 pytest tests/test_corroborate.py -v
    GENVM_VERSION=v0.6.0-rc10 genvm-lint check contracts/corroborate.py
"""

import hashlib
import os
import sys
import time
import urllib.request

VERSION = os.environ.get("GENVM_VERSION", "v0.6.0-rc10")
ASSET = os.environ.get("GENVM_ASSET", "genvm-universal.tar.xz")
BASE = "https://github.com/genlayerlabs/genvm-manager/releases/download"
CACHE = os.path.expanduser("~/.cache/gltest-direct/bundles-v2")
DEST = os.path.join(CACHE, f"genvm-universal-{VERSION}.tar.xz")


def expected_sha() -> str:
    request = urllib.request.Request(
        f"{BASE}/{VERSION}/SHA256SUMS", headers={"User-Agent": "corroborate-build"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        for line in response.read().decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ASSET:
                return parts[0]
    raise SystemExit(f"{ASSET} is not listed in the SHA256SUMS of {VERSION}")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def size_of(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def main() -> int:
    os.makedirs(CACHE, exist_ok=True)
    want = expected_sha()
    print(f"expected sha256 ({VERSION}/{ASSET}): {want}", flush=True)
    for attempt in range(1, 41):
        have = size_of(DEST)
        if have and sha256_file(DEST) == want:
            print(f"verified: {DEST}", flush=True)
            return 0
        headers = {"User-Agent": "corroborate-build"}
        if have:
            headers["Range"] = f"bytes={have}-"
        request = urllib.request.Request(f"{BASE}/{VERSION}/{ASSET}", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                mode = "ab" if (have and response.status == 206) else "wb"
                with open(DEST, mode) as handle:
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                        have += len(chunk)
                        if have % (20 << 20) < (1 << 20):
                            print(f"  {have // (1 << 20)} MiB", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"attempt {attempt}: {exc!r}", flush=True)
            time.sleep(3)
    print("download failed", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
