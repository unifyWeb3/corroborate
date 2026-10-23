# corroborate

**A corroborated data feed for GenLayer: exact readings, an explicit interval, and no
point estimate hiding inside a tolerance.**

`corroborate` turns "read a number from the web" into a reading that a committee of
validators re-derived from the same sources, with the evidence kept on chain: which
sources agreed, the keccak256 digest of every reading, the source's own timestamp and the
transaction's read time.

The stored result is an **interval**, never a point chosen within a tolerance:

* a complete read (every declared source was read) collapses to the exact lower median:
  `lo == hi`;
* an incomplete read stores `[median - B, median + B]`, the exact bound the declared
  tolerance permits, and the validator must also find its own full-set median inside it;
* consumers call `compare(feed_id, threshold)` and get `ABOVE`, `BELOW` or
  `INDETERMINATE`.  There is no point `value` accessor.

```python
feed = gl.contract.get_at(FEED_ADDRESS)
view = feed.view(state=StorageView.LATEST_FINALIZED)   # pin the final state
if view.is_trustworthy("eur-usd", 3600):
    relation = view.compare("eur-usd", threshold)       # ABOVE / BELOW / INDETERMINATE
```

**Pinned target SDK:** `genvm-manager v0.6.0-rc10`
(`py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0`,
`py-lib-genlayer-std:z7fv8bqpetj3wnjd8sn5s5xfqve3rz8nmtq4npwf2qdvns57zjgg`).
Lint/validation with the pinned pairing (`genvm-linter 0.11.1rc2`,
`GENVM_VERSION=v0.6.0-rc10`) passes both contracts; see
[Pinned SDK and lint](#pinned-sdk-and-lint).

**Network:** Studio development preview, chain **61997**,
`https://studio-dev.genlayer.com/api`.  The current deployment address is in
[Network and deployment](#network-and-deployment); the rejected v1 address is kept there
for the record.

---

## The problem

A contract that needs an external number has no way to know whether that number is real,
current or corroborated.  One HTTP read is one node, at one moment, and if the contract
stores it, it stores an unverified claim that later consumers have to take on faith.

The usual answers are a trusted feed operator (a single point of failure) or an LLM that
summarises a page (a different point of failure, and not a number).  GenLayer's
Equivalence Principle gives a third way: a leader proposes, and every validator
independently decides whether to accept.  What is usually missing is *what the validators
should check* and *what evidence should be kept*.

## What this contract does

For each feed the curator declares:

* **several public HTTPS sources** (2 to 8), each with its own **extraction rule** (a JSON
  path such as `rates.EUR`),
* an **aggregation rule** (the lower median) and a **tolerance** (relative in basis
  points, absolute in scaled units),
* a **quorum**: how many sources must agree with the median,
* a **staleness limit**, applied to a timestamp the source itself publishes.

Any caller can then call `refresh(feed_id)`.  The leader fetches every source, derives one
number per source and aggregates them.  **Every validator re-fetches every source itself**
and re-derives the whole reading before it accepts the leader's proposal:

* each reported per-source value is bound exactly to the validator's own value by the
  digest `keccak256("<index>:<scaled value>")`;
* the aggregate is the **exact** lower median of the reported set - not a value within
  the tolerance of it;
* when a declared source was not reported, the interval is exactly
  `[median - B, median + B]`, and the validator's own full-set median must lie inside;
* the quorum, the flags and the read time must be exactly what the validator computes.

Only then is anything written.  What is written is the interval **and** its provenance.

Disagreement is a first-class result, not an error: a source that disagrees with the
median is kept in the provenance table and the record is flagged `disputed`; a source that
could not be read is disclosed as `partial` and produces an interval; a reading whose
sources are too old is flagged `stale`.  A reading with no quorum is not published at all.

## Why this is not a thin wrapper

* **There is no LLM in the loop.**  No prompt, no model, no closed set to reduce.  The
  check is integer arithmetic over values that the validator computed itself from bytes
  it fetched itself.
* **It is not a format check.**  A validator that only inspected the leader's payload
  would prove that the leader formatted an answer, not that the answer is true.  Here the
  validator re-derives the number, the digest, the median, the interval, the agreeing set,
  the quorum and the status, and rejects anything that does not match.
* **It is not escrow, arbitration or a court.**  No deposits, no bonds, no dispute
  resolution between parties.  It measures a number and publishes the evidence for it.

## Why it is a primitive

The reusable unit is not "the EUR/USD feed".  It is the *reading protocol*: declared
sources, declared extraction, declared aggregate, declared tolerance, independent
re-derivation, verified provenance, first-class disagreement.  A new feed is a
configuration entry, not new code.  Another contract consumes it through a small read
interface and a guard.

## Interface

### Write

| method | who | effect |
|:--|:--|:--|
| `register_feed(feed_id, label, unit, tol_rel_bps, tol_abs, max_age_seconds, min_quorum, sources_json)` | curator only | declares a feed, once per id |
| `refresh(feed_id)` | anyone | reads the sources, derives the reading, publishes it **only if the committee accepts** |
| `disable_feed(feed_id, disabled)` | curator only | administrative kill switch |

`sources_json` is a JSON array, one object per source:

```json
[{"url": "https://open.er-api.com/v6/latest/USD",
  "value_path": "rates.EUR",
  "time_path": "time_last_update_unix",
  "scale": 100000}]
```

`value_path` and `time_path` are dotted paths (`a.b.0.c` walks objects and array
indices).  A path that is not present makes that source fail - it is never silently zero.
`tol_rel_bps` is capped at 2000 and `tol_abs` at `MAX_VALUE / 4`, which keeps the interval
inside `i64`.

### Read

| method | returns |
|:--|:--|
| `latest(feed_id)` | the record: `lo`, `hi`, flags, status, counts, timestamps and the per-source provenance.  No point `value`. |
| `compare(feed_id, threshold)` | `ABOVE` if every admitted value is `>= threshold`, `BELOW` if every admitted value is `< threshold`, otherwise `INDETERMINATE` |
| `status(feed_id)` | `agreed`, `disputed`, `partial`, `stale`, or a combination |
| `is_trustworthy(feed_id, max_age_seconds)` | **the guard**: true only for an enabled feed with an agreed, complete, fresh reading |
| `feed(feed_id)` | the declared configuration |
| `feed_count()`, `source_count(feed_id)` | counts |

`compare` is a statement about the stored interval.  It is only consequential together
with `is_trustworthy`: a partial or unreadable source is disclosed by the flag and the
guard, not hidden in a number.  For a strict `value > threshold` rule pass `threshold + 1`
(the values are scaled integers).

### A caller example

`contracts/example_consumer.py` is a complete consumer:

```python
import genlayer as gl
from genlayer.types import Address, u64, i64, u32
from genlayer.vm.public_abi import StorageView


def decide(trusted: bool, relation: str) -> str:
    if not trusted:
        return "UNTRUSTED"
    if relation == "ABOVE":
        return "ACCEPT"
    if relation == "BELOW":
        return "REJECT"
    return "UNDECIDED"


class ExampleConsumer(gl.contract.Contract):
    feed_address: Address
    accepted: u32
    rejected: u32
    undecided: u32
    last_outcome: str

    @gl.public.write
    def check(self, feed_id: str, max_age: u64, min_value: i64):
        feed = gl.contract.get_at(self.feed_address)
        view = feed.view(state=StorageView.LATEST_FINALIZED)   # pin to the final state
        trusted = view.is_trustworthy(feed_id, max_age)
        relation = view.compare(feed_id, min_value)
        outcome = decide(trusted, relation)
        ...
```

Three things a consumer has to get right, and all are in that snippet: pin the read to
the final state, gate on `is_trustworthy`, and ask `compare` instead of inventing a point.

## State design

```
owner: Address                          the curator (deployer)

feeds: TreeMap[str, Feed]                curator declared, immutable after creation
  Feed   { label, unit, tol_rel_bps, tol_abs, max_age_seconds, min_quorum,
           sources: DynArray[Source], created_at, disabled }
  Source { url, value_path, time_path, scale }

records: TreeMap[str, Record]            the last verified reading per feed
  Record   { lo, hi, flags, agree_count, sources_read, sources_declared,
             quorum_required, read_at, data_ts, readings: DynArray[Reading] }
  Reading  { index, value, digest, data_ts, agrees }
```

* Only the curator can create or change a feed, and only once per id.
* Anyone may refresh, but a refresh can only write a record the committee re-derived.
* A refresh **overwrites** one slot: no counters, no per-caller state, no arrays that
  grow.  Spam cannot grow the contract.
* Every field of `Record` is recomputed by the validator from its own reads before it is
  stored.  The only two stored fields that are *not* consensus-derived are `owner` and the
  per-feed `disabled` flag, and they are administrative.
* `digest` is the keccak256 of the **reading** (source index and scaled value), not of the
  raw response body.  A body digest could not be verified whenever the page changes
  between the leader's read and the validator's read, and an unverifiable digest is a
  leader claim rather than a fact.  The reading digest is computed by both sides and must
  be identical.

## The trust model, stated plainly

* **The curator is trusted for the configuration.**  A malicious curator can point a feed
  at sources it controls, declare a tolerance as wide as the cap allows, or declare a
  `time_path` that always looks fresh.  The contract cannot prevent that.  What the
  contract guarantees is that the stored reading was read by the committee from the
  declared sources, and that the committee agreed on the interval exactly.
* **The committee is trusted to actually re-read.**  A validator that rubber-stamps the
  leader is outside this contract's control - that is GenLayer's base assumption and
  appeals are the network's answer to it.
* **A leader may omit a source it failed to read.**  The committee cannot tell that from
  a deliberate omission, so the disclosure is structural: an omitted source means
  `sources_read < sources_declared`, therefore the `PARTIAL` flag, therefore
  `is_trustworthy = false`.  A consumer that wants every source to have contributed
  checks `status == "agreed"`.  The committee also catches a leader that omits a source
  it could read and that lies outside the published interval: that is a hidden dissent
  and the proposal is rejected.
* **The clock inside the GenVM is pinned to the transaction timestamp.**  There is no
  block number, no block hash and no host wall clock in the contract context.  The stored
  `read_at` is the transaction timestamp, not the moment the HTTP request was served.
  Freshness therefore comes from a timestamp the *source* publishes, declared per source
  as `time_path`.
* **One trusted key is still one key.**  The curator is a single address.  A council of
  curators would be a natural extension and would not change the consensus mechanism.
* **A stale or disputed reading is published, not hidden.**  A consumer that ignores the
  status is using a reading the committee did not fully vouch for.  That is why the status
  is a first-class field and why `is_trustworthy` exists.

## Pinned SDK and lint

The contracts import `genlayer.contract`, `genlayer.storage` and `genlayer.vm`, which
exist in the 0.6 SDK line and do not exist in the legacy `v0.2.16` / `v0.3.0-rc7` line.
The stable `genvm-linter 0.11.0` resolves the legacy `genlayerlabs/genvm` repository and
fails with `Import error: No module named 'genlayer.storage'` - the reviewer's rejection.
The pinned pairing that passes:

| layer | pin |
|:--|:--|
| linter | `genvm-linter 0.11.1rc2` |
| SDK | `GENVM_VERSION=v0.6.0-rc10` (`genvm-manager`) |
| test runner | `genlayer-test 0.30.0rc2` (`genlayer-py 0.19.0rc2`) |
| network header | `# { "Depends": "py-genlayer:latest" }` - the node rejects every explicit runner hash with `invalid_contract runner malformed`; its `latest` surface was probed live and matches rc10 |

```bash
uv venv --python 3.12 .rcvenv
uv pip install --python .rcvenv/bin/python -r requirements-dev.txt
GENVM_LINT_BIN=.rcvenv/bin/genvm-lint scripts/lint.sh
```

The last command refuses any linter other than `0.11.1rc2` or any
`GENVM_VERSION` other than `v0.6.0-rc10`, then runs `genvm-lint check` on both contracts.
The observed result:

```
genvm-lint 0.11.1rc2, GENVM_VERSION=v0.6.0-rc10
== contracts/corroborate.py
✓ Lint passed (3 checks)
✓ Validation passed
  Contract: Corroborate
  Methods: 10 (7 view, 3 write)

== contracts/example_consumer.py
✓ Lint passed (3 checks)
✓ Validation passed
  Contract: ExampleConsumer
  Methods: 3 (2 view, 1 write)
```

`tests/test_invariants.py::test_every_import_and_api_exists_in_the_pinned_sdk` runs
`scripts/check_sdk_surface.py`, which resolves the pinned SDK with the linter's own
loader and checks every `import genlayer...` and every `gl.*` API used by the contracts
against the real package.  It is not a hand-written allow list, and a negative control
with a missing API proves the check can fail.

## Network and deployment

| | |
|:--|:--|
| network | Studio development preview (release candidate) |
| canonical RPC | `https://studio-dev.genlayer.com/api` |
| chain ID | **61997** |
| deployed contract | `0xNEW_CONTRACT_ADDRESS` |
| source identity | `gen_getContractCode` read back, sha256 `NEW_SHA256`, byte-identical to `contracts/corroborate.py` |
| deploy transaction | `NEW_DEPLOY_TX` |
| live transcript | `docs/LIVE-RUN-FIX.md` |

The release-candidate environment is separate from the stable Studionet (chain 61999).
The tools used here are the matching release candidates: `genlayer-py 0.19.0rc2`,
`genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2`.

The **rejected v1** deployment is kept for the record: address
`0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F`, sha256
`a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796`, deploy tx
`0x769efbd55e18e256468de8e88bf25f598a766525be730bb3ff3450c2f2c76ee8`.  Its dated
transcript is `docs/LIVE-RUN.md`; it is superseded and must not be used.  The review
findings and the fix are in `state/REVIEW-FIX.md`, `state/DESIGN-fix.md` and
`docs/CHANGES-SINCE-REJECTION.md`.

### What is verified live and what is only covered by the tests

| | |
|:--|:--|
| **live, with transaction hashes** | the new deploy, the deployed source matches the repository byte for byte, `register_feed`, `refresh` against real public APIs, the published interval and its provenance, `compare` returning `ABOVE` / `BELOW` / `INDETERMINATE` with reads pinned to `LATEST_FINALIZED`, the consumer's threshold outcomes, a refused non-curator call, a refused malformed registration, and no state written by a refused call |
| **unit tests only** | the validator rejecting a shifted aggregate, a shifted/narrowed band, a float band, a forged per-source value/digest, an inflated quorum, a forged status, a forged read time, a dropped source and a hidden dissent; the committee rejecting a reading without a quorum; the injected-body case; the page-drift case; the exhaustive/random interval invariant |
| **not covered** | the consumer contract's cross-contract call - the direct test runner cannot execute one, so only the consumer's rule is unit tested; the live consumer call is in the transcript.  No live validator *disagreement* (two validators with different views) was observed. |

## Running the tests

```bash
uv venv --python 3.12 .rcvenv
uv pip install --python .rcvenv/bin/python -r requirements-dev.txt

# the GenVM bundle the runner needs (~310 MiB, verified against the release SHA256SUMS)
python scripts/fetch_genvm_bundle.py

# the full suite (direct mode: the contract runs in process, no network)
GENVM_VERSION=v0.6.0-rc10 .rcvenv/bin/python -m pytest tests/ -q
```

The suite has no skips.  The unit tests use the GenLayer direct test runner.  Web
responses are mocked, the leader and the validator are executed separately, and the
validator is replayed against honest and tampered leader proposals.  The Stage 3 tests
were first run against tag `rejected-v1` and failed there (see `state/MEMORY.md`).

The live scripts need the keys in `secrets/keys.json` (gitignored, chmod 600):

```bash
python scripts/live_run.py
python scripts/verify_deployment.py
```

## Limits

* 2 to 8 sources per feed, 32 feeds per contract, 256 character urls, 8192 character
  source list, 32 KiB response bodies, values within +/- 2^62, `tol_rel_bps <= 2000`,
  `tol_abs <= 2^60`.
* Numeric feeds only: one scaled integer per source.  A structured value would need a
  per-field tolerance and quorum - an extension, not a redesign.
* The interval bounds an omitted source that is within the declared tolerance.  A source
  that *no* validator can read has no bound; the record is `PARTIAL` and
  `is_trustworthy` is false, which is the refusal signal.
* A source that cannot be read does not vote.  A reading whose agreeing sources do not
  meet the quorum is not published.
* The direct test runner cannot execute a cross-contract call, so the consumer's rule is
  tested as a pure function; the cross-contract path is covered by the live run.

## Layout

```
contracts/corroborate.py       the contract
contracts/example_consumer.py  an example consumer
tests/test_corroborate.py      the curation, refresh and validator tests
tests/test_invariants.py       the canonical/interval invariants and the pinned-SDK check
tests/helpers.py               test helpers
docs/CONSENSUS.md              how the consensus works, step by step
docs/LIVE-RUN.md               the dated transcript of the rejected v1 run
docs/LIVE-RUN-FIX.md           the dated transcript of the redeploy (new deployment)
docs/CHANGES-SINCE-REJECTION.md reviewer point -> fix -> evidence
requirements-dev.txt           the pinned linter/test toolchain
scripts/lint.sh                version-pinned lint + validation of both contracts
scripts/check_sdk_surface.py   import/API check derived from the pinned SDK files
scripts/live_run.py            deploy, fund, register, refresh, read back
scripts/verify_deployment.py   read the deployed source back and compare it
scripts/fetch_genvm_bundle.py  fetch and checksum the GenVM bundle the runner needs
scripts/pre_push_sweep.py      the pre-push secret sweep
state/REVIEW-FIX.md            Stage 1: reproduced rejection and the working pairing
state/DESIGN-fix.md            Stage 2: the chosen interval design and red-team cases
state/DESIGN.md                the original v1 design (superseded for the aggregate rule)
state/MEMORY.md                the decision log
submission/contribution-text.md  the portal submission text
```

## License

MIT. See `LICENSE`.
