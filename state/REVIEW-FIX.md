# REVIEW-FIX - Stage 1: reproduce the rejection, find a working pairing

Date: 2026-10-23. Baseline: tag `rejected-v1` = `a698b23` = the rejected submission.
No contract, test or script was changed in this stage. This file is the only new
tracked file.

Two reviewer points are treated separately:

1. GenVM lint reports source import errors for the declared SDK.
2. The validator lets the stored aggregate move inside the tolerance, so the exact
   value a consumer thresholds can flip.

## 1. What the repository declares vs what it needs

| thing | value in `rejected-v1` |
|:--|:--|
| contract header | `# { "Depends": "py-genlayer:latest" }` |
| imports | `genlayer as gl`, `genlayer.storage` (`DynArray`, `TreeMap`, `allow`), `genlayer.types` (`u32`, `u64`, `i64`, `Address`) |
| APIs | `gl.contract.Contract`, `gl.contract.get_at`, `gl.vm.run_nondet`, `gl.vm.Return`, `gl.vm.UserError`, `gl.nondet.web.get`, `gl.message.sender_address`, `gl.Keccak256`, `gl.public.view/write`, `genlayer.vm.public_abi.StorageView` |
| tooling in README | `genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2`, `GENVM_VERSION=v0.6.0-rc10` |

Every one of those imports/APIs is from the 0.6 SDK line. The header says `latest`,
which is a tag, not a pinned SDK. Nothing in the repo forced the *linter* to resolve
the same SDK that the tests used.

## 2. Lint runs, exact output

Two virtual environments, both on CPython 3.12:

* `.rcvenv` - the toolchain the README already documented:
  `genvm-lint, version 0.11.1rc2`.
* `/tmp/opencode/fixvenv` - a separate venv where the version was varied:
  `genvm-lint, version 0.11.0` (the current PyPI stable).

### 2.1 The failure reproduces with `genvm-linter 0.11.0`

`0.11.0` is the stable release a reviewer gets from `pip install genvm-linter`. It
resolves `genlayerlabs/genvm` (the legacy repository), whose newest release is
`v0.2.16` / `v0.3.0-rc7`. Its `GENVMROOT` loader is hard-coded to the legacy
`genlayer.py.get_schema` layout.

Pointing it at the legacy studio SDK tree (v0.2.16) with the supported `GENVMROOT`
path:

```
$ GENVMROOT=/tmp/opencode/legacyroot /tmp/opencode/fixvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✗ Validation failed
  Import error: No module named 'genlayer.storage'

Exited with code 1
```

That is the reviewer's "source import errors for the declared SDK": the linter's
loader is old, the contract is written for the 0.6 SDK, and the source import fails
before any check runs.

The same linter cannot rescue the run by pinning the version either:

```
$ GENVM_VERSION=v0.6.0-rc10 /tmp/opencode/fixvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✗ Validation failed
  Failed to load SDK: No GenVM runner bundle for v0.6.0-rc10; tried genvm-runners-all.tar.xz, genvm-universal.tar.xz

Exited with code 1
```

With no environment at all it fails the other way:

```
$ /tmp/opencode/fixvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✗ Validation failed
  Failed to load SDK: Could not find py-genlayer in release
```

The pinned SDK id it needs is not in that repository at all. `0.11.0` is therefore
not a viable linter for this contract.

### 2.2 The pairing that passes

`genvm-linter 0.11.1rc2` resolves `genlayerlabs/genvm-manager`, which is where the
0.6 runner bundles live, and its loader prefers `genlayer._internal.get_schema`
(the 0.6 layout) with a legacy fallback.

```
$ genvm-lint --version
genvm-lint, version 0.11.1rc2

$ GENVM_VERSION=v0.6.0-rc10 .rcvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✓ Validation passed
  Contract: Corroborate
  Methods: 10 (7 view, 3 write)

EXIT=0
```

and the consumer contract:

```
$ GENVM_VERSION=v0.6.0-rc10 .rcvenv/bin/genvm-lint check contracts/example_consumer.py

✓ Lint passed (3 checks)
✓ Validation passed
  Contract: ExampleConsumer
  Methods: 3 (2 view, 1 write)

EXIT=0
```

The same linter passes with no `GENVM_VERSION` at all (it resolves the newest
`genvm-manager` prerelease, `v0.6.0-rc10`), which is useful but not relied on: the
pin is what makes the run reproducible.

### 2.3 The `GENVMROOT` path

`0.11.1rc2` supports a prebuilt tree through `GENVMROOT` / `GENVM_PREBUILT_DIR`, but
a *runner bundle* is not a tree. A tree must contain `bin/genvm-modules` and
`runners/`:

```
$ GENVM_SOURCE_MODE=prebuilt GENVMROOT=/root/.cache/gltest-direct/trees-v2/v0.6.0-rc10 \
    .rcvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✗ Validation failed
  Failed to load SDK: GENVM_SOURCE_MODE=prebuilt selected GENVMROOT='.../v0.6.0-rc10',
  but that GenVM tree is unusable: missing bin/genvm-modules. ...

EXIT=1
```

In auto mode (`GENVM_SOURCE_MODE` unset) the same unusable/legacy root is ignored
and the linter falls back to the pinned release bundle, which passes:

```
$ GENVMROOT=/tmp/opencode/legacyroot .rcvenv/bin/genvm-lint check contracts/corroborate.py

✓ Lint passed (3 checks)
✓ Validation passed
  Contract: Corroborate
  Methods: 10 (7 view, 3 write)

EXIT=0
```

The supported local-tree path is the full `genvm-arm64-linux.tar.xz` /
`genvm-amd64-linux.tar.xz` asset from the release, not `genvm-universal.tar.xz`.
The arm64 tree was fetched for this audit (99,104,772 bytes) but the transfer
stopped at 51,482,933 bytes and did not complete; the full-tree run is therefore
**not** claimed.  What is claimed is the reproduced legacy-`GENVMROOT` failure
above, the auto-mode fallback to the pinned release, and the passing
`GENVM_VERSION=v0.6.0-rc10` run in 2.2.

### 2.4 The pinned SDK id

The `v0.6.0-rc10` release asset `genvm-universal.tar.xz` has
sha256 `d3c6ba368dbd5cad01e484e19573906b4ffed41f46a48191bdc579c81cf801af`,
matching the release `SHA256SUMS`. Its `py-genlayer` runner manifest says:

```
py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0
py-lib-genlayer-std:z7fv8bqpetj3wnjd8sn5s5xfqve3rz8nmtq4npwf2qdvns57zjgg
cpython:3t4hs1eyrs8rb0qf538cbc227jskqeke9pfys0vaftapv91xn9v0
```

The linter is pinned with `GENVM_VERSION=v0.6.0-rc10`; `genlayer-test 0.30.0rc2`
requires `genlayer-py>=0.19.0rc2,<0.20.0` (PyPI metadata), which is the matching
client line.

## 3. What the target network actually runs

The network only accepts runner aliases. Deploying a minimal two-line contract with
different headers on Studio devnet (chain 61997):

| header | result |
|:--|:--|
| `py-genlayer:babwp9t...` (the rc10 hash above) | `contract_error / invalid_contract runner malformed` |
| `py-genlayer:deadbeefdeadbeef` | `contract_error / invalid_contract runner malformed` |
| `py-genlayer:v0.6.0-rc10` | `contract_error / invalid_contract runner malformed` |
| `py-genlayer:test` | `return / null` (deployed) |
| `py-genlayer:latest` | `return / null` (deployed) |

On Studionet (chain 61999) both the explicit hash and `latest` return
`contract_error / invalid_contract`, so that network is older and is not the target.

To identify what `latest` resolves to on 61997, a probe contract was deployed with
`latest` and asked the running SDK what exists. Tx
`0xc5b6cc57fd0f51cde13fc28dc91ffe73061c8d1b819c31f54944299273cb2bb8`,
address `0x4703810a1DAB11922BF66eBE92E96448E403194F`:

```
file: /py/libs/genlayer/__init__.py
has_contract_module: true          has_contract_get_at: true
has_contract_at: false             has_run_nondet: true
has_run_nondet_unsafe: false       has_nondet_module: true
has_storage_module: true           storage_allow: true
has_public_abi_storageview: true   storageview_members: [DEFAULT, LATEST_DECIDED, LATEST_FINALIZED]
has_old_public_abi: false          version: ""
```

That surface is the rc10 surface, not the v0.2.16 surface (which has
`gl.get_contract_at`, `run_nondet_unsafe`, no `StorageView`, and no
`genlayer.storage` module). So the usable pairing is:

| layer | pin |
|:--|:--|
| contract header (deployable on 61997) | `py-genlayer:latest`, which the node resolves to the rc10-compatible surface above |
| linter / validator | `genvm-linter 0.11.1rc2` + `GENVM_VERSION=v0.6.0-rc10` |
| Python runner tests | `genlayer-test 0.30.0rc2` (needs `genlayer-py>=0.19.0rc2`) |

This is a two-level pin, not one string. The header cannot carry the hash because
the node rejects every explicit hash (`runner malformed`) - evidence above, not an
assumption. The fix will make the toolchain pin explicit and enforced
(`requirements-dev.txt`, `scripts/lint.sh`), keep the header compatible with the
node, and add a test that reads the pinned SDK files and proves every import/API
used by the contracts exists in them.

### 3.1 Imports that the legacy SDK does not support

From the v0.2.16 tree (the SDK `0.11.0` loads), these are absent:

| used by | import / API | legacy v0.2.16 |
|:--|:--|:--|
| corroborate | `from genlayer.storage import DynArray, TreeMap, allow` | no `genlayer.storage` module; names are top-level or under `genlayer.py.storage` |
| corroborate | `from genlayer.types import u32, u64, i64, Address` | no `genlayer.types` module; `genlayer/py/types.py` |
| corroborate | `gl.contract.Contract` | `gl.Contract` |
| corroborate | `gl.contract.get_at` | `gl.get_contract_at` |
| corroborate | `gl.vm.run_nondet` | present, but the legacy client also has `run_nondet_unsafe`; the rc10 client does not |
| consumer | `from genlayer.vm.public_abi import StorageView` | no `genlayer.vm`; no `StorageView` |
| consumer | `StorageView.LATEST_FINALIZED` | absent |

Under rc10 every one of these is present: the linter's own validation imports the
contract with `genlayer._internal.get_schema` and reports 10 methods, so the source
is import-clean against the pinned SDK.

## 4. The aggregate flaw, executable

`contracts/corroborate.py` at `rejected-v1`:

```python
# verify_proposal, line 843
my_median = lower_median(my_values)
if not within(int(proposal["value"]), my_median, tol_abs, tol_rel):
    return False
```

Per-source readings are already exact: the digest is `keccak256(f"{index}:{value}")`
and the validator recomputes it from its own value, so any different value fails
the digest check. The median is therefore exact too - but the aggregate comparison
still uses the tolerance instead of equality. A leader can publish `median + tol`
and every validator accepts it.

Executed against the tagged contract, two sources `1000` and `1020`,
`tol_abs = 20`:

```
leader stored : 1000
tampered with : 1010
validator own : 1000 (lower median of [1000, 1020])
validator accepts tampered aggregate: True
consumer threshold: 1005
decision from stored 1010 : ABOVE
decision from own 1000    : BELOW
```

The status stays `agreed` and `is_trustworthy` stays true, because the flags and
counts are recomputed honestly and do not depend on the aggregate. The tampered
proposal passes all 13 checks.

Consequential consumer-facing fields:

* `latest(feed_id)["value"]` - the exact integer a consumer thresholds;
* `value(feed_id)` - the same integer;
* `example_consumer.ExampleConsumer.check` - reads `value(feed_id)` and feeds it to
  `decide(trusted, value, min_value)`, which increments `accepted` / `rejected` and
  writes `last_value`.
* `status` / `is_trustworthy` are **not** consequential for this bug: they remain
  `agreed` / true under the tampered aggregate.

There is a second, smaller version of the same problem: a leader may omit a source
it read only if that source is within tolerance of the proposal value
(`verify_proposal`, hidden-dissent check at line 873). The stored value is then the
median of the reported subset, not of the validator's full set, and the record is
marked `partial`, but the exact stored integer is still returned by `value()`. A
consumer that ignores the flag can threshold the subset median while the validator's
own reading is on the other side.

## 5. Decision for the fix

* Pin the linter as `genvm-linter 0.11.1rc2` and the SDK as
  `GENVM_VERSION=v0.6.0-rc10`; add a pinned `requirements-dev.txt` and a
  `scripts/lint.sh` that refuses to run with a different linter. Document the
  required pairing and the two-level header/network pin.
* Add an automated SDK-surface test that reads the pinned SDK files and checks every
  import/API used by both contracts (no hand-written allow list).
* Replace the stored point aggregate with an invariant-preserving result:
  - exact canonical `lo == hi == median` when every declared source was read;
  - otherwise a canonical tolerance band `[median - B, median + B]` and
    `compare(feed_id, threshold)` returning `ABOVE` / `BELOW` / `INDETERMINATE`;
  - no public point-value accessor.
* Prove the property with a concrete flip test, an exhaustive/property test, and a
  boundary test; show every red test failing first against `rejected-v1`.

Stage 2 has the full design, the options that were rejected and the red-team cases.
