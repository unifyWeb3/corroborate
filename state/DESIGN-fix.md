# DESIGN-FIX - Stage 2: the canonical / invariant-preserving aggregate

Baseline `rejected-v1`, diagnosis in `state/REVIEW-FIX.md`. This design is written
before any contract edit; the red tests in Stage 3 target the rule below.

## 1. The requirement, stated exactly

For every value the contract stores that a consumer can threshold, one of:

* **(a) one canonical value** that every accepting validator recomputes exactly, or
* **(b) an invariant-preserving result** whose uncertain region is explicit and whose
  consumer-facing decision is never the opposite of any accepting validator's own
  reading; the uncertain region must return `INDETERMINATE`, never a hidden point.

The rule must also keep the feed useful: a single unreadable source must not be
silently replaced by a point estimate, and a complete read must not pay an
uncertainty penalty.

## 2. What is already canonical in `rejected-v1`

The per-source value is exact even though the code says `within`:

```python
entry["digest"] == digest_of(index, own["value"])   # keccak256(f"{index}:{value}")
```

The digest is a function of the value, and the validator recomputes it from its own
value, so `entry["value"] == own["value"]` for every reported source (up to a
keccak collision). The `within` check on the same value is redundant. What is *not*
exact is:

1. the aggregate comparison - `within(proposal["value"], my_median, ...)`; and
2. the **set** of sources behind the median - a leader may omit a source it read if
   the source is within tolerance of the proposal value (hidden-dissent check).

So the fix does not need to re-invent per-source agreement. It needs to make the
aggregate exact for the reported set, and make the omitted-set uncertainty explicit.

## 3. Options considered

### 3.1 Interval + compare helper (chosen)

Store `[lo, hi]`. For a complete read (every declared source reported), `lo == hi ==
exact lower median`. For an incomplete read, `lo = median - B`, `hi = median + B`,
where `B` is the largest shift the declared tolerance can produce. The validator
recomputes the band exactly and also requires its own full-set median to lie inside.
Consumers call `compare(feed_id, threshold)`:

```
ABOVE          every possible aggregate is >= threshold
BELOW          every possible aggregate is < threshold
INDETERMINATE  the reading cannot decide
```

**Strongest argument against:** the incomplete case returns `INDETERMINATE` over a
band whose width is the declared tolerance, which can be much wider than the observed
source spread; some consumers will have to handle a third answer. This is the honest
cost of having any tolerance at all. The alternative for an incomplete read is to
publish nothing, which trades liveness for a point that does not exist.

### 3.2 Quantization to a declared step, fixed rounding

Round every reading to a declared step (half away from zero) and store the rounded
values.

**Strongest argument against:** it does not remove the problem it is meant to solve.
Two honest reads that straddle a rounding boundary round to different values; either
the validator rejects (brittle, and the boundary case is exactly the case that
happens when a price ticks) or it accepts both (non-canonical again). It also
introduces a threshold between the raw reading and its rounded value, so a consumer
can still be flipped by the rounding choice unless the interval is published anyway.
Fixed rounding is useful for display, not for consensus.

### 3.3 Threshold-side agreement

Register a threshold (or a set of thresholds) with the feed and store the decision
above/below each.

**Strongest argument against:** a feed cannot know every consumer's threshold, scale
or unit in advance. Registering them turns one feed into an unbounded set of
per-consumer feeds, requires a new transaction per threshold, and still stores a
point for any threshold that was not registered. It rejects the reviewer's actual
requirement (one safe stored result for arbitrary consumers) instead of meeting it.

### 3.4 Per-source canonical readings + exact aggregate of the stored readings

This is already true for the digest-bound readings; make the aggregate equality
exact.

**Strongest argument against (standalone):** the aggregate is exact over the
*reported set*, not over the validator's full set. If a source is omitted, the
validator's own full-set median can be on the other side of a threshold, and the
record is `partial` but still carries a point. Standing alone it fixes the malicious
`value` shift but not the omitted-source shift. It is adopted as the complete-read
half of 3.1, with the interval covering the incomplete half.

## 4. Chosen rule

### 4.1 Definitions (integer scaled units throughout)

```
median          = lower median of the reported values (one of them, deterministic)
complete        = sources_read == sources_declared
M               = max(abs(v) for v in reported values)
B(rel, abs, M)  = max(abs, ceil(rel * M / (10000 - rel)))     for 0 <= rel <= 2000
lo, hi          = (median, median)                            if complete
                = (median - B, median + B)                    otherwise
```

`B` is an upper bound on how far a reading that is `within` tolerance of the median
can be: if `d*10000 <= rel*max(|x|,|median|)` and `|x| <= |median| + d <= M + d`,
then `d <= rel*M/(10000-rel)`. The absolute branch is `d <= abs <= B`. The bound is
computed with integers, no floats. `rel` is capped at 2000 bps (20 %) at
registration, so `B <= M/4` and `median +/- B` stays inside i64 for `MAX_VALUE =
2**62`.

### 4.2 What the leader proposes

```
{ "lo": int, "hi": int, "flags": int, "agree_count": int, "sources_read": int,
  "read_at": int, "data_ts": int, "readings": [ {index, value, digest, data_ts, agrees} ] }
```

There is no `value` key. The leader's readings are exact for the sources it reports.
The leader's band is a pure function of its own values, `complete`, and the declared
tolerance; the validator can recompute it with no extra data.

### 4.3 What the validator recomputes and compares

1. Every required key exists; `read_at` equals the transaction timestamp.
2. Every reported source index is in range, unique, readable by the validator; its
   value has the exact digest; its `data_ts` is within `TS_TOL_SECONDS`.
3. `my_values` over the reported set; `my_median = lower_median(my_values)`.
4. `my_complete = len(reported) == len(sources)`.
5. Expected band:
   * complete: `lo == hi == my_median`;
   * incomplete: `B` from `my_values`, `lo == my_median - B`, `hi == my_median + B`.
6. **Band honesty:** for every declared source the leader omitted but the validator
   could read, it must be inside `[lo, hi]` (stronger than the old
   within-tolerance-of-the-point rule). The validator's **full-set median** (over
   every source it could read) must also be inside `[lo, hi]`. The first check
   rejects hidden dissent; the second makes the consumer invariant true by
   construction for every accepting validator.
7. `agree_count`, per-source `agrees`, `flags`, `sources_read` are recomputed from
   the validator's own median and set, exactly as before; quorum must be met.
8. `data_ts` is the oldest reported source timestamp (within `TS_TOL_SECONDS`).
9. `lo <= hi`, and both fit the stored `i64`.

A proposal that omits a source the validator read is still accepted when the source
is inside the published band and the full-set median is inside the band: that is the
`partial` case, and the band is the explicit statement of what was missed.

### 4.4 What is persisted

One record per feed, overwritten on refresh (unchanged):

```
lo: i64, hi: i64, flags: u32, agree_count: u32, sources_read: u32,
sources_declared: u32, quorum_required: u32, read_at: u64, data_ts: u64,
readings: DynArray[Reading]
```

The point `value` field is gone. `lo`/`hi` are the consequential fields; `readings`
are exact for the reported set and are provenance, not a decision API;
`flags`/`status`/`is_trustworthy` are the quality gate.

### 4.5 Compare

```
ABOVE          iff lo >= threshold
BELOW          iff hi <  threshold
INDETERMINATE  otherwise
```

`ABOVE` answers "the aggregate is at least `threshold`" with certainty over the
whole band; `BELOW` answers "it is strictly below". For a strict `>` predicate a
consumer passes `threshold + 1` in scaled integer units (documented with an
example). `compare` on an unknown feed reverts like `latest`. On a complete record
`lo == hi`, so every threshold is exact; on a partial record the region between the
endpoints is `INDETERMINATE`.

The quality axes stay separate: `is_trustworthy(feed_id, max_age)` already requires
`flags == 0`, quorum met, every source read and fresh enough. `compare` answers the
threshold question; the consumer example gates `compare` on `is_trustworthy` and
counts `UNDECIDED` separately. This is deliberate: "stale" is a consumer-specific
max-age, and "disputed" is source quality, not threshold uncertainty. Both are
visible without being folded into a false point.

### 4.6 Consumer-safe interface

| call | returns | contract |
|:--|:--|:--|
| `compare(feed_id, threshold)` | `"ABOVE"`, `"BELOW"`, `"INDETERMINATE"` | never opposite to any accepting validator's full-set median |
| `latest(feed_id)` | `lo`, `hi`, flags, status, counts, timestamps, readings | no `value` key |
| `status(feed_id)` | agreed / disputed / partial / stale (combinations) | unchanged |
| `is_trustworthy(feed_id, max_age)` | bool | unchanged |
| `feed`, `feeds`, `feed_count`, `source_count`, `register_feed`, `disable_feed`, `refresh` | unchanged | curation/ops |

`value(feed_id)` is removed. Any consumer that wants a displayed number can show
`[lo, hi]` or `lo` when `lo == hi`.

## 5. Boundary and disagreement behaviour

* **Complete read**: `lo == hi` exactly; a threshold equal to the value is `ABOVE`;
  `value + 1` is `BELOW`; no `INDETERMINATE` is possible.
* **Incomplete read**: `lo`/`hi` are exact integer endpoints; threshold `== lo` is
  `ABOVE`, `== hi` is `INDETERMINATE`, `hi + 1` is `BELOW`.
* **Zero tolerance** (`rel == 0`, `abs == 0`): `B == 0`; an incomplete band collapses
  to the median and every omitted readable source must equal the median exactly, so
  the full-set aggregate is canonical.
* **One source**: complete -> exact; unreadable -> no values -> the validator
  rejects (rotation), as before.
* **Negative values**: `B` uses `abs`; integer addition/subtraction is symmetric; the
  exhaustive tests include negative ranges and exact half-way bounds.
* **Extreme values**: registration caps `abs` at `MAX_VALUE // 4`, `rel` at 2000;
  `lo/hi` stay inside i64 for values up to `MAX_VALUE = 2**62`.
* **Disagreement**: a complete read with disagreeing sources keeps the exact lower
  median and sets `DISPUTED`; the validator recomputes the same median and flag.
  `is_trustworthy` returns false, and the consumer example refuses. A partial read
  additionally sets `PARTIAL`.
* **Out-of-band source**: if the leader omits a source that lies outside `[lo, hi]`,
  the proposal is rejected (hidden dissent). If it omitted nothing and a source is an
  outlier, the median is still exact and the record is `DISPUTED`.
* **Read time**: unchanged - the validator requires the exact transaction timestamp.
* **Two validators with different readable sets**: each accepting one checks its own
  full-set median inside the same stored band; therefore both are inside the band,
  so no threshold inside the band can be decided the same way for one and the
  opposite way for the other.
* **A source no validator can read**: no tolerance can bound what it would have
  said.  The record is `PARTIAL` and `is_trustworthy` is false, which is the refusal
  signal.  `compare` describes the stored interval over the readable sources and is
  not a guarantee about the unreadable one; the consumer example gates on
  `is_trustworthy` before acting.  This is the one case where the interval is not a
  bound on the full universe, and it is disclosed structurally rather than hidden in
  a point value.
* **Migration**: existing feeds/records in the rejected deployment are not migrated;
  the new deployment starts empty (documented as superseding `rejected-v1`).

## 6. Red-team cases

| # | attack | expected |
|:--|:--|:--|
| 1 | leader shifts `lo/hi` by `+tol` on a complete read | reject: `lo == hi == my_median` exactly |
| 2 | leader sets `lo != hi` on a complete read to create a false band | reject |
| 3 | leader widens a partial band (`hi = median + 2B`) to hide a dissent | reject: width recomputed exactly |
| 4 | leader narrows a partial band so an omitted source is outside | reject: omitted readable source must be inside |
| 5 | leader omits a source inside the band but the full-set median moves outside | reject: full-set median must be inside |
| 6 | leader reports a value off by one with a self-consistent digest | reject: digest recomputed from the validator's own value |
| 7 | leader forges `lo > hi` | reject |
| 8 | leader forges a duplicate/extra source index | reject (existing duplicate/range checks) |
| 9 | leader lies about `sources_read` to look complete | reject: must equal the reported table length |
| 10 | leader ignores a source that makes the quorum | reject: `agree_count < quorum` |
| 11 | leader reports an old `read_at` | reject: exact timestamp |
| 12 | leader reorders/omits the oldest `data_ts` | reject: exact oldest |
| 13 | consumer probes threshold `lo - 1`, `lo`, `hi`, `hi + 1` | ABOVE, ABOVE, INDETERMINATE, BELOW |
| 14 | consumer probes on a zero-width band | every integer threshold decidable |
| 15 | threshold at `MAX_VALUE` boundaries / negative values | no overflow, integer results |
| 16 | validator reads a subset (some sources fail) of a complete proposal | reject (cannot reproduce the set) |
| 17 | a partial record is passed to `is_trustworthy` | false; consumer counts UNDECIDED |
| 18 | stale/disputed record with an exact median | `compare` exact, `is_trustworthy` false |

The Stage 3 tests cover these; each refusal case has a positive control.

## 7. Docs updated with this design

* `docs/CONSENSUS.md` - the 13 checks become explicit median/band/full-set checks,
  with the invariant proof and the compare table.
* `README.md` - purpose, corrected rule, pinned SDK/lint, live vs tests, new address.
* `contracts/example_consumer.py` - `compare` + `is_trustworthy` gate, `UNDECIDED`
  counter.
* `submission/contribution-text.md` - the corrected claim, not the tolerance point.
