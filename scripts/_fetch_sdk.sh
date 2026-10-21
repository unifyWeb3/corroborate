#!/bin/bash
# Fetch the official GenVM v0.6.0-rc10 bundle with resume support and verify it
# against the release's published SHA256SUMS, then let the gltest/genvm-lint
# tooling extract it (the tooling's own extractor, not a hand-made tree).
set -u
VER=v0.6.0-rc10
DEST=/root/.cache/gltest-direct/bundles-v2/genvm-universal-${VER}.tar.xz
URL="https://github.com/genlayerlabs/genvm-manager/releases/download/${VER}/genvm-universal.tar.xz"
SUMS_URL="https://github.com/genlayerlabs/genvm-manager/releases/download/${VER}/SHA256SUMS"
WANT=$(curl -s -m 60 -L "$SUMS_URL" | awk '/genvm-universal.tar.xz/{print $1}')
echo "expected sha256: $WANT"
for i in 1 2 3 4 5 6 7 8; do
  if [ -f "$DEST" ]; then
    GOT=$(sha256sum "$DEST" | awk '{print $1}')
    if [ "$GOT" = "$WANT" ]; then
      echo "bundle verified"
      break
    fi
  fi
  echo "attempt $i: resuming download"
  curl -sSL -C - --retry 5 --retry-delay 5 --max-time 1800 -o "$DEST" "$URL"
done
GOT=$(sha256sum "$DEST" | awk '{print $1}')
if [ "$GOT" != "$WANT" ]; then
  echo "SHA256 MISMATCH got=$GOT want=$WANT"
  exit 1
fi
echo "SHA256 OK"
cd /root/primitive-a
GENVM_VERSION=${VER} /root/primitive-a/.rcvenv/bin/python - <<'PY'
import os, tarfile, shutil
from pathlib import Path
from gltest.direct import sdk_loader
version = os.environ["GENVM_VERSION"]
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
PY
