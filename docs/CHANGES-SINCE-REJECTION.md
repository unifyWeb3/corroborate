# Changes since the rejection

The reviewer's rejection, verbatim:

> The submitted contract cannot be accepted in its current form. GenVM lint completes
> with source import errors for the declared SDK, and the validator allows different
> aggregate values within tolerance even though the exact stored value can change a
> consumer's threshold decision. Please use imports supported by a pinned target SDK and
> make the validator bind the consequential aggregate to one canonical value or
> invariant-preserving result before redeploying matching source.

Baseline: tag `rejected-v1` (`a698b23`).  Fix commit: `3b85a6f` (code, tests, design and
consensus docs).  The live deployment and this document were added afterwards; the new
address is pinned in `README.md`, `scripts/verify_deployment.py` and
`docs/LIVE-RUN-FIX.md`.

---

## Point 1 - pinned SDK and lint

**What was wrong.**  The v1 header declared
`# { "Depends": "py-genlayer:latest" }` (line 1) and imported `genlayer.contract`,
`genlayer.storage` and `genlayer.vm`, but the lint environment resolved the legacy
`genlayerlabs/genvm` line.  The stable `genvm-linter 0.11.0` therefore ended with:

```
✗ Validation failed
Import error: No module named 'genlayer.storage'
EXIT=1
```

The docstring also claimed the aggregate was "the validator's own re-derived median
within the tolerance" (line 15), which was the second defect.  The exact reproductions
under three linter/SDK pairings are in `state/REVIEW-FIX.md` section 2.

**What changed.**

| | |
|:--|:--|
| linter | `genvm-linter 0.11.1rc2` (the 0.11.0 line cannot load the 0.6 SDK) |
| SDK | `GENVM_VERSION=v0.6.0-rc10` (`genvm-manager`) |
| runner | `py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0` |
| standard library | `py-lib-genlayer-std:z7fv8bqpetj3wnjd8sn5s5xfqve3rz8nmtq4npwf2qdvns57zjgg` |
| runner pin in the header | stays `py-genlayer:latest`; the target network rejects every explicit hash with `invalid_contract runner malformed`, so the runtime surface is probed live and pinned in a test instead |
| toolchain pin in the repo | `requirements-dev.txt`, `scripts/lint.sh` (refuses any other linter/SDK version) |

**Tests.**

* `tests/test_invariants.py::test_every_import_and_api_exists_in_the_pinned_sdk` runs
  `scripts/check_sdk_surface.py`, which resolves the pinned SDK with the linter's own
  loader and checks every `import genlayer...` and every `gl.*` API used by the contracts
  against the real package.  It checks 22 names in `corroborate.py` and 12 in
  `example_consumer.py`; a negative control (`gl.contract.get_att`) fails with
  `MISSING ... AttributeError`, exit 1.  It is derived from the pinned SDK files, not a
  hand-written allow list.
* `scripts/lint.sh` with the pinned binary: `genvm-lint 0.11.1rc2`,
  `GENVM_VERSION=v0.6.0-rc10`, both contracts `Lint passed (3 checks)` and
  `Validation passed`, exit 0.  With the 0.11.0 binary it refuses:
  `refusing to lint with genvm-lint 0.11.0; the pin is 0.11.1rc2`.

**Live evidence.**  The new deployment (section below) runs with the `latest` header.  A
live probe of that surface reports the rc10 capability map: `contract.get_at` yes,
`contract.at` no, `run_nondet` yes, `run_nondet_unsafe` no, `storage.allow` yes,
`StorageView` includes `LATEST_FINALIZED`, old `genlayer.py` no.  The probe transaction
is `0xc5b6cc57fd0f51cde13fc28dc91ffe73061c8d1b819c31f54944299273cb2bb8` at
`0x4703810a1DAB11922BF66eBE92E96448E403194F`; details in `state/REVIEW-FIX.md`.

**Not verified.**  The full `genvm-arm64-linux.tar.xz` tree was not run under
`GENVMROOT`: the download stopped at 51,482,933 of 99,104,772 bytes and was not used.  The
reproduced legacy-`GENVMROOT` failure and the release-path pass are both documented; the
full-tree run is not claimed.

---

## Point 2 - the aggregate must be canonical or invariant-preserving

**What was wrong.**  v1 stored a point `Record.value` (line 101), computed as the lower
median (`value = lower_median(values)`, line 731), but the validator accepted **any**
proposal within the tolerance of its own median:

```python
my_median = lower_median(my_values)
if not within(int(proposal["value"]), my_median, tol_abs, tol_rel):
    return False
```

(line 844).  The omitted-source check had the same shape (line 873).  The accessor
`value()` (line 548) returned that point, and the example consumer thresholded it
(`decide(trusted, value, min_value)`, consumer lines 22/50).

The executable counterexample, against the tagged source: sources 1000 and 1020 with
`tol_abs = 20`.  The honest stored value is 1000.  A leader that proposes 1010 is accepted
because `|1010 - 1000| <= 20`.  With the consumer's threshold at 1005, the stored value is
`ABOVE` while the validator's own reading is `BELOW`; the status stays `agreed` and
`is_trustworthy` stays true.  That is a consumer-visible decision flip caused by a value
the validators did not agree on.  The full reproduction is in `state/REVIEW-FIX.md`
section 4.

**What changed.**  `Record.value` is gone.  The record stores `lo` and `hi` (and the
provenance); the validator recomputes them exactly:

| read | stored result | validator requirement |
|:--|:--|:--|
| complete (`sources_read == sources_declared`) | `lo == hi == lower median` | its own lower median equals both endpoints |
| incomplete | `lo = median - B`, `hi = median + B`, `B = max(tol_abs, ceil(tol_rel_bps * M / (10000 - tol_rel_bps)))`, `M = max|reported|` | it recomputes the same band from its own values, its own full-set median is inside, and every source the leader omitted but it could read is inside |

`compare(feed_id, threshold)` returns `ABOVE`, `BELOW` or `INDETERMINATE`; there is no
point accessor.  `is_trustworthy` requires a complete, agreed, quorum-met, fresh record,
so a partial record is refused.  The tolerance still classifies agreement and sets the
band width; it is no longer an acceptance slack for the aggregate.  The proof that every
within-tolerance value lies inside the band is in `docs/CONSENSUS.md` section 3; the caps
(`tol_rel_bps <= 2000`, `tol_abs <= MAX_VALUE / 4`) keep the band inside `i64`.

**Tests.**

* `tests/test_invariants.py::test_the_v1_threshold_flip_is_closed`: the exact v1 scenario;
  the shifted 1010 proposal is refused, the honest 1000 is accepted (positive control),
  and `compare(feed, 1005)` on the honest record is `BELOW`, not `ABOVE`.
* `test_a_partial_band_must_be_exact` and `test_a_partial_band_rejects_a_hidden_outlier`:
  a shifted, asymmetric, narrowed or inverted band is refused; an omitted readable source
  outside the band is refused; the honest band and an omitted source inside it are
  accepted (positive controls).
* `test_a_non_integer_band_is_rejected`: `int(100.5) == 100` would otherwise truncate to
  a valid band; the refusal has a positive control.
* `test_the_invariant_holds_exhaustively` (60,038 generated cases) and
  `test_the_invariant_holds_on_randomised_large_values` (30,000 cases): for every
  generated reading, the consumer's decision from the stored result matches the
  validator's own reading or is `INDETERMINATE`, never the opposite.  The
  tolerance-bound lemma adds 36,475 cases.
* `test_the_validator_rejects_a_tampered_value` (now tampering `lo`/`hi`), the forged
  digest/value, inflated quorum, forged status and forged read-time tests all still pass
  with positive controls.

**Live evidence.**  New deployment
`0x6D0Cd00A0d3eb9C86eEE81f22cb05e3B51E070Ed`, source sha256
`e2bc11ed31ece8a747520a6824a51438283e532ba33fa9b3b42f9130468c6957` (34992 bytes,
byte-identical to the repository):

| feed | stored result | threshold query (pinned `LATEST_FINAL`) |
|:--|:--|:--|
| `eur-usd` (3/3 read) | `lo = hi = 89153`, `agreed`, trustworthy | `compare(89153)` = `ABOVE`; `compare(89154)` = `BELOW` |
| `eur-usd-partial` (3/4 read, one unreachable) | `lo = 87793`, `hi = 90513`, `partial`, not trustworthy | `compare(87793)` = `ABOVE`; `compare(89153)` = `INDETERMINATE`; `compare(90514)` = `BELOW` |

The exact `[87793, 90513]` band equals `median 89153 +/- ceil(150 * 89246 / 9850) =
+/- 1360`.  The complete feed's compare is the exact threshold answer; the partial feed's
middle query is the explicit `INDETERMINATE` region.  Transaction hashes are in
`docs/LIVE-RUN-FIX.md`.

---

## Other fixes in the same change

* **Float band truncation.**  `int(proposal["lo"])` would turn `100.5` into `100` and
  accept it as the exact median.  The validator now requires an actual integer (and
  rejects `bool`).  Test: `test_a_non_integer_band_is_rejected`, with a positive control.
* **Tolerance caps.**  `MAX_TOL_REL_BPS` went from 10000 to 2000 and
  `MAX_TOL_ABS` is `MAX_VALUE // 4`, so the band cannot leave `i64`.  Test:
  `test_registration_tolerance_boundaries`.
* **Consumer example.**  `value()`/`last_value` replaced by `compare`, `is_trustworthy`
  and `UNDECIDED` counters; the rule is exposed as `evaluate` for unit tests.  Live
  cross-contract calls did not complete on this RC (see below).
* **Docs.**  `docs/CONSENSUS.md` rewritten to the 15 validator checks and the band proof;
  `state/DESIGN.md` marked superseded for the aggregate and the SDK pin; the rejected
  transcript `docs/LIVE-RUN.md` left untouched.

## What is still not verified

* The example consumer's **cross-contract call**: the nested `relation` read returned an
  HTTP 504 gateway timeout, and the gated `check` write rolled back with
  `Mode1MessageFeesRequireGenVMPerEmissionSupport: fee-bearing GenVM messages require a
  message allocation tree`.  The consumer's `decide` rule is unit tested; the
  feed-level `compare` that it calls is verified live.  The example is an integration
  sketch, not a live-proven cross-contract consumer.
* **Live validator disagreement** (two nodes with different views of the same source) was
  not observed.  The rejection path is covered by the unit tests, which replay the
  validator against tampered proposals.
* **Full `GENVMROOT` tree run**: not claimed (see point 1).
