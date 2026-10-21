#!/bin/bash
cd /root/primitive-a
export PATH="/root/primitive-a/.rcvenv/bin:$PATH"
python - <<'PY'
import os, tarfile, shutil
from pathlib import Path
from gltest.direct import sdk_loader
version = os.environ.get("GENVM_VERSION") or sdk_loader.resolve_version()
print("RESOLVED_VERSION", version, flush=True)
tarball = sdk_loader.download_artifacts(version)
print("TARBALL", tarball, flush=True)
tree = sdk_loader.TREE_CACHE_DIR / version
if not (tree / ".extracted").exists():
    tmp = Path(str(tree) + ".tmp")
    if tmp.exists(): shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:xz") as outer:
        outer.extractall(tmp, filter="data")
    (tmp / ".extracted").touch()
    os.replace(tmp, tree)
print("TREE", tree, flush=True)
for r in sorted((tree / "runners").glob("*/*")):
    if r.is_dir(): print("RUNNER", r.relative_to(tree), flush=True)
print("DONE", flush=True)
PY
