import os, tarfile, shutil, sys
from pathlib import Path
from gltest.direct import sdk_loader
version = os.environ.get("GENVM_VERSION", "v0.6.0-rc10")
tarball = sdk_loader.BUNDLE_CACHE_DIR / f"genvm-universal-{version}.tar.xz"
tree = sdk_loader.TREE_CACHE_DIR / version
if not (tree / ".extracted").exists():
    tmp = Path(str(tree) + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:xz") as outer:
        outer.extractall(tmp, filter="data")
    (tmp / ".extracted").touch()
    os.replace(tmp, tree)
print("TREE", tree, flush=True)
for r in sorted((tree / "runners").glob("*/*")):
    if r.is_dir():
        print("RUNNER", r.relative_to(tree), flush=True)
print("DONE", flush=True)
