# Contribution text

Two versions: the detailed one and the short one (under 1000 characters, all lowercase).
The short version is also in `short-version.txt`, byte for byte, so it can be copied
without touching the file.

---

## Detailed version

**corroborate - a corroborated data feed for GenLayer, with an explicit interval**

A standalone GenLayer Intelligent Contract that lets another contract ask a threshold
question about a number from the public web and get `ABOVE`, `BELOW` or `INDETERMINATE`,
backed by evidence the committee re-derived.

**The problem.** A contract that needs an external number has no way to know whether that
number is real, current or corroborated. One HTTP read is one node, at one moment. If the
contract stores it, it stores an unverified claim that later consumers have to take on
faith. The usual answers are a trusted feed operator (a single point of failure) or a
language model summarising a page (a different point of failure, and not a number).

**What this contract does.** A curator declares a feed: two to eight public HTTPS sources,
a JSON path per source (the extraction rule), a scale, a relative and an absolute
tolerance, a staleness limit and a quorum. Any caller can then call `refresh(feed_id)`.
The leader fetches every source and derives one scaled integer per source. Every validator
re-fetches every source itself and re-derives the whole reading. The stored result is an
interval, never a point chosen within a tolerance:

* a complete read (every declared source was read) stores the exact lower median:
  `lo == hi`;
* an incomplete read stores `[median - B, median + B]`, the exact bound the declared
  tolerance permits, and the validator's own full-set median must lie inside it;
* a consumer calls `compare(feed_id, threshold)` and gets `ABOVE` (every admitted value is
  at least the threshold), `BELOW` (every admitted value is strictly below) or
  `INDETERMINATE` (the reading cannot decide).  There is no point `value` accessor.

**The validator, precisely.** For every reported source, the validator requires the
leader's value to equal its own (the tolerance check is a redundant guard; the digest is
the binding constraint), the leader's digest to equal
`keccak256("<source index>:<scaled value>")`, and the source timestamp to agree.  It then
recomputes the aggregate: complete -> `lo == hi == its own lower median`; incomplete ->
the exact band from its own values.  It requires its own full-set median inside the band,
recomputes the agreeing count, the quorum, the flags, `sources_read` and `data_ts`, and
requires a source the leader omitted but the validator could read to lie inside the band.
A non-integer band (for example `100.5`, which would truncate to a valid integer) is
rejected.  If any check fails the proposal is rejected and the leader is rotated; if no
leader satisfies the committee, the transaction ends undetermined and no state is written.

**Why the tolerance still exists.** It classifies a reported source as agreeing or
disagreeing with the median (`DISPUTED` and the quorum) and it sets the width of the
interval for an incomplete read:
`B = max(tol_abs, ceil(tol_rel_bps * M / (10000 - tol_rel_bps)))`, `M = max|value|`.
The proof that every within-tolerance source is inside the band is in
`docs/CONSENSUS.md`.  It is **not** used to accept an aggregate that is merely close to
the median; that was the rejected v1 bug.

**How consensus is used.** Integer arithmetic over values the validator computed itself
from bytes it fetched itself.  There is no language model in the loop, and this is not a
format check: the validator re-derives the number, the digest, the median, the interval,
the agreeing set, the quorum and the status, and rejects anything that does not match.

**Disagreement is a result, not an error.** A source that disagrees with the median stays
in the provenance table and the record is flagged `disputed`.  A source that could not be
read does not vote, the record is flagged `partial` and the gap is disclosed
(`sources_read < sources_declared`).  A reading whose sources are too old is flagged
`stale`.  A reading with no quorum is not published at all.

**Pinned target SDK and lint.** The imports are from the 0.6 SDK line.  The stable
`genvm-linter 0.11.0` resolves the legacy SDK and fails with
`Import error: No module named 'genlayer.storage'`; that was the reviewer's first point.
The pinned pairing is `genvm-linter 0.11.1rc2` with `GENVM_VERSION=v0.6.0-rc10`
(`genvm-manager`), runner
`py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0`, standard library
`py-lib-genlayer-std:z7fv8bqpetj3wnjd8sn5s5xfqve3rz8nmtq4npwf2qdvns57zjgg`.
`scripts/lint.sh` refuses any other linter or SDK version and passes both contracts:
`Lint passed (3 checks)` and `Validation passed`.  `scripts/check_sdk_surface.py` resolves
the pinned SDK with the linter's own loader and checks every `import genlayer...` and
every `gl.*` API used by the contracts against the real package; a negative control with
a missing API proves the check can fail.  The deployable header remains
`# { "Depends": "py-genlayer:latest" }` because the target network rejects every explicit
runner hash with `invalid_contract runner malformed`; a live probe of `latest` reports the
rc10 capability surface, and `tests/test_invariants.py` pins that surface.

**The consumer side.** `latest` returns the interval and the provenance; `compare`
answers a threshold; `status` and `is_trustworthy(feed_id, max_age)` are the guard.
A consumer should pin the read with `StorageView.LATEST_FINALIZED`, gate on
`is_trustworthy`, and act only on `ABOVE` or `BELOW`.  `contracts/example_consumer.py`
shows the full pattern and exposes its decision rule for unit testing.

**Trust, stated plainly.** The curator is trusted for the configuration: it can point a
feed at sources it controls or declare a tolerance as wide as the 2000 bp cap allows.  The
committee is trusted to actually re-read.  Two stored fields are administrative rather
than consensus-derived (`owner` and the per-feed `disabled` flag); every field that
describes an external reading is re-derived.  A leader may omit a source it failed to
read, and that is disclosed as `partial` + `is_trustworthy = false`; a leader that omits a
source it *did* read and that lies outside the published interval is rejected.  A source
that no validator can read has no bound; `compare` describes the readable set and the
guard is the refusal signal.  Inside the GenVM the clock is pinned to the transaction
timestamp, so freshness comes from a timestamp the source publishes.  One trusted key is
still one key.

**Network and address.** Studio development preview (release candidate), chain ID 61997,
canonical RPC `https://studio-dev.genlayer.com/api`.  The fixed deployment is
`0x6D0Cd00A0d3eb9C86eEE81f22cb05e3B51E070Ed`; its source was read back with
`gen_getContractCode` and is byte-for-byte the file in the repository (sha256
`e2bc11ed31ece8a747520a6824a51438283e532ba33fa9b3b42f9130468c6957`, 34992 bytes, deploy
tx `0x28a5527ac21fc50d9b77549f37b29953600d38a5b293f7ceb17d5872f6f671b3`).  The rejected
v1 deployment is `0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F`; it is kept only for the
record and is not used.

**Repository.** https://github.com/unifyWeb3/corroborate - `contracts/corroborate.py`,
`contracts/example_consumer.py`, `tests/test_corroborate.py` and
`tests/test_invariants.py` (95 tests, all passing, no skips, every refusal with a
positive control), `docs/CONSENSUS.md` (the step-by-step walkthrough),
`docs/LIVE-RUN-FIX.md` (the dated transcript with every transaction hash),
`docs/CHANGES-SINCE-REJECTION.md` (review point -> fix -> evidence),
`state/REVIEW-FIX.md` and `state/DESIGN-fix.md` (the diagnosis and the chosen design),
`scripts/lint.sh` and `scripts/check_sdk_surface.py` (the pinned toolchain checks).
MIT licensed, no frontend.

**What was verified live.** The new deploy, the byte-identical source read-back, the feed
registration, a refresh against three real public FX providers, `lo == hi = 89153` for a
complete read, the exact `[87793, 90513]` interval for a feed with one unreachable source
(`sources_read 3/4`, `partial`, `is_trustworthy = false`), `compare` returning `ABOVE`,
`BELOW` and `INDETERMINATE` with the read pinned to `LATEST_FINAL`, a non-curator refused
(`rollback / not the curator`), malformed input refused (`rollback / invalid sources`) and
no state written by a refused call (`feed_count` 2 before and after).  The example
consumer's cross-contract call did **not** complete live: the nested `relation` read
returned an HTTP 504 gateway timeout and the gated `check` write rolled back with
`Mode1MessageFeesRequireGenVMPerEmissionSupport: fee-bearing GenVM messages require a
message allocation tree`.  The consumer's `decide` rule is unit tested; the feed-level
threshold query a consumer uses is the live `compare` result.  No live validator
disagreement was observed; the rejection path is covered by unit tests that replay the
validator against tampered proposals.

**Limits.** Numeric feeds only, one scaled integer per source.  A structured value would
need a per-field tolerance and quorum.  2 to 8 sources per feed, 32 feeds per contract, 32
KiB response bodies, values within +/- 2^62, `tol_rel_bps <= 2000`, `tol_abs <= 2^60`.  The
feed configuration is curator-declared and immutable.  A source that no validator can read
has no bound; the record is `partial` and the guard is false.  A boundary case, where a
source sits exactly on the tolerance edge, can make the committee reject a correct
reading; the result is a rotation, not a wrong number.

---

## Short version (995 characters, lowercase)

```
corroborate is a genlayer feed: ask for a threshold, get above, below or indeterminate, never a point inside a tolerance.

a curator declares 2-8 https sources, a json path each, a tolerance and a quorum. the leader takes the exact lower median. a complete read stores that value (lo == hi); a missing source stores the exact band [median - b, median + b]. every validator re-reads every source, binds each by keccak256(index:value), recomputes the median and band, and rejects any inexact result; an omitted source it could read must be inside.

no language model: integer arithmetic over independent reads. a consumer calls compare(feed, threshold) and gates on is_trustworthy. 95 tests pass, no skips, with exhaustive/random invariants and positive controls.

pinned: genvm-linter 0.11.1rc2 + genvm-manager v0.6.0-rc10. deployed on studio dev preview (chain 61997) at 0x6d0cd00a0d3eb9c86eee81f22cb05e3b51e070ed; source reads back byte for byte. repo: https://github.com/unifyWeb3/corroborate
```

The only capital letter in it is the `W` in the repository URL.
