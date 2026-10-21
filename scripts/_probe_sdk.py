"""Stage 2 helper: resolve and extract the GenVM SDK bundle used by gltest/genvm-lint.

This uses the tooling's own downloader (never a hand-seeded cache).
"""

import json
import os
import sys
from pathlib import Path

from gltest.direct import sdk_loader


def main() -> int:
    version = os.environ.get("GENVM_VERSION") or sdk_loader.resolve_version()
    print(f"resolved genvm version: {version}")
    tarball = sdk_loader.download_artifacts(version)
    print(f"tarball: {tarball}")
    tree = sdk_loader.TREE_CACHE_DIR / version
    if not (tree / ".extracted").exists():
        tmp = Path(str(tree) + ".tmp")
        import tarfile

        if tmp.exists():
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tarball, "r:xz") as outer:
            outer.extractall(tmp, filter="data")
        (tmp / ".extracted").touch()
        os.replace(tmp, tree)
    print(f"tree: {tree}")
    runners = sorted(p for p in (tree / "runners").glob("*/*") if p.is_dir())
    print("runner dirs:")
    for r in runners:
        print("  ", r.relative_to(tree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
