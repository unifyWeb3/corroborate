# How the consensus works

This document is for a builder who wants to use `corroborate`, or wants to copy the
pattern into their own contract.  It walks through one `refresh` transaction from the
caller to the stored record.

The short version: the stored result is **never a point estimate chosen inside a
tolerance**.  A complete read collapses to one exact median; an incomplete read stores
an interval and the only threshold API is `compare`, which can answer `INDETERMINATE`.

---

## 0. The shape of the problem

A contract needs a number that lives outside the chain: a price, a rate, an index.  The
number comes from the web, so:

* two nodes that fetch at different moments can see different values,
* a node can see a page that another node cannot reach,
* the leader of a transaction is just one node - it can be wrong, lazy or hostile,
* and a stored number without evidence cannot be checked later.

GenLayer gives us the tool for the third problem: the **Equivalence Principle**.  A leader
proposes a result, and validators independently decide whether to accept it.  What this
contract adds is the *rule* for that decision, and the *evidence* stored with it.

The rule used here is **exact readings, exact aggregate, explicit uncertainty**:

> Every reported per-source reading is bound to the validator's own reading by a digest
> of that reading.  When every declared source was read, the aggregate is the exact
> lower median of those readings: `lo == hi`.  When a declared source was missing, the
> aggregate is the exact tolerance interval around the median, and the validator must
> also find its own full-set median inside that interval.  A consumer thresholds the
> result with `compare`, never with a point.

---

## 1. What the feed declares

The curator declares, once, per feed:

| field | meaning |
|:--|:--|
| `sources` | 2 to 8 public HTTPS URLs, each with its own extraction rule |
| `value_path` | dotted path to the number inside the JSON document, e.g. `rates.EUR` |
| `time_path` | optional dotted path to the source's own unix timestamp |
| `scale` | decimal multiplier, e.g. `100000` for five decimals of a rate |
| `tol_rel_bps` | relative tolerance in basis points, capped at 2000 (20 %) |
| `tol_abs` | absolute tolerance in scaled units, capped at `MAX_VALUE / 4` |
| `max_age_seconds` | staleness limit for the source-declared timestamps |
| `min_quorum` | how many sources must agree with the median |

The configuration is immutable once registered.  It is *not* consensus-derived: the curator
is trusted for it.  Everything that describes an actual reading *is* consensus-derived.

The caps are part of the safety argument: they keep the interval
`median +/- B` inside `i64` for every value the contract accepts.

---

## 2. The flow

```
        caller
          |  refresh("eur-usd")
          v
   +------------------------------------------+
   |  read the feed configuration (storage)  |
   |  copy it into plain python values       |
   +------------------------------------------+
          |
          |  gl.vm.run_nondet(leader_fn, validator_fn)
          v
   +------------------------------------------+
   |            LEADER (one node)            |
   |  for each declared source:              |
   |    fetch body            (nondet)       |
   |    doc    = json.loads(body)            |
   |    value  = extract(doc, value_path)    |
   |    ts     = extract(doc, time_path)     |
   |  median = lower median of the readings  |
   |  complete = every source was read       |
   |  lo, hi = exact median, or median +/- B |
   |  flags  = disputed / partial / stale    |
   |  proposal = {lo, hi, flags, counts,     |
   |              read_at, data_ts, readings}|
   +------------------------------------------+
          |  proposal
          v
   +------------------------------------------+
   |        VALIDATORS (every other node)    |
   |  fetch every source again (nondet)      |
   |  re-derive values, digests, timestamps  |
   |  recompute median and the exact band    |
   |  require the full-set median inside it  |
   |  recompute agreeing set, quorum, flags  |
   |  -> accept (True) or reject (False)     |
   +------------------------------------------+
          |  accepted proposal
          v
   +------------------------------------------+
   |  store the record (deterministic code)  |
   +------------------------------------------+
```

The whole of "leader" and "validators" happens inside the non-deterministic block.
Storage writes, contract calls and message emission happen **after** it, in deterministic
code - the GenVM linter rejects the other order.

---

## 3. The leader, step by step

For every declared source, in order:

1. `gl.nondet.web.get(url)` - the only non-deterministic call.
   * a transport failure, a non-200 status, a body over `MAX_BODY_BYTES`, a body that is
     not JSON, a missing path, a non-numeric leaf or a value outside the integer range
     all mark **that source** as failed.  They never fail the whole reading.
2. `digest = keccak256("<index>:<scaled value>")` - the digest of the reading.
3. `value = extract(doc, value_path) * scale`, rounded half away from zero.
   The scale is what makes the comparison integer arithmetic.
4. `ts = extract(doc, time_path)` when the source declares one.

Then the aggregate:

* `R` = the sources that were read successfully,
* `m` = the **lower median** of the values in `R`,
* `A` = the sources in `R` whose value is within the tolerance of `m`,
* `flags`:
  * `DISPUTED` - some source that was read is not within the tolerance of the median,
  * `PARTIAL` - fewer sources were read than were declared,
  * `STALE` - a source's own timestamp is older than `max_age_seconds`.
* `complete = len(R) == len(sources)`.
* `lo, hi`:
  * complete: `lo = hi = m`;
  * incomplete: `lo = m - B`, `hi = m + B`, where
    `B = max(tol_abs, ceil(tol_rel_bps * M / (10000 - tol_rel_bps)))` and
    `M = max(|v| for v in R)`.

The lower median is used on purpose: it is always one of the observed values, so the
interval is centered on a number some source actually reported.

A stale source still votes: excluding it would make the aggregate depend on a freshness
decision that the leader and the validator could make differently.  Instead the reading is
flagged and the consumer guard refuses it.

**Why `B` is the tolerance bound.**  If a reading `x` is within tolerance of `m`, then
either `|x - m| <= tol_abs`, or `|x - m| * 10000 <= tol_rel_bps * max(|x|, |m|)`.  In
the second case, with `d = |x - m|` and `|x| <= |m| + d <= M + d`:

```
d * 10000 <= rel * (M + d)   =>   d <= rel * M / (10000 - rel)
```

so `d <= B`.  Every value that the declared tolerance would call "in agreement with the
median" is inside the interval.  No float is used: `B` is computed with integer ceiling.

---

## 4. The validator, step by step

The validator does **not** look at the leader's numbers and ask whether they look
reasonable.  It re-reads the world, re-derives the reading, and compares.

| # | check | what it protects against |
|:--|:--|:--|
| 1 | the proposal has every expected field and `read_at` equals the pinned transaction timestamp | a fabricated read time, a malformed proposal |
| 2 | the reading table names declared sources only, once each | a duplicated or invented source |
| 3 | the validator also read every source the leader reported | an aggregate the validator cannot reproduce |
| 4 | `|leader_value_i - validator_value_i| <= tolerance` for every reported source | a leader that reports a number the source never showed |
| 5 | the leader's digest equals `keccak256(index:validator_value_i)` | a leader that reports a reading the validator did not make (this is the exact binding) |
| 6 | the source timestamps agree within `TS_TOL_SECONDS` | a leader that invents a freshness timestamp |
| 7 | **the interval is exact**: complete -> `lo == hi == validator_median`; incomplete -> `lo == m - B`, `hi == m + B` with the validator's own `B` | an aggregate shifted inside the tolerance |
| 8 | `lo <= hi`, and both are integers (a float such as `100.5` is rejected, not truncated) | a malformed or type-confused band |
| 9 | the validator's **full-set median** is inside `[lo, hi]` | a band that does not cover what the validator actually read |
| 10 | the recomputed agreeing count equals the leader's `agree_count` | an inflated quorum |
| 11 | the recomputed agreeing count satisfies `min_quorum` | publishing a number nobody agrees on |
| 12 | the recomputed flags equal the leader's flags | a status that hides a disagreement |
| 13 | `sources_read` and `data_ts` match the recomputation | an incomplete or invented provenance record |
| 14 | the per-source verdict equals the validator's own verdict | a leader that calls a dissenting source an agreeing one |
| 15 | a source the leader omitted but the validator could read lies inside `[lo, hi]` | a leader that hid a dissent by omission |

If any check fails the validator returns `False`.  GenLayer then rotates the leader.  If no
leader can satisfy the committee the transaction ends **undetermined** and **nothing is
written**: a reading without agreement is not published.

In code, the validator receives the leader's result and has to look at it before trusting
anything in it:

```python
def validator_fn(result) -> bool:
    if not isinstance(result, gl.vm.Return):   # the leader failed
        return False
    proposal = result.calldata
    ...
```

### What the digest covers, and why

The digest is `keccak256("<source index>:<scaled value>")` - the digest of the *reading*.
It is deliberately not a digest of the raw response body.

The reason is verification.  A page that ticks between the leader's read and the
validator's read has two different bodies, so two different body digests, even though both
nodes derive the same value.  Accepting that case would mean accepting a digest the
committee cannot confirm, and a stored digest that nobody verified is a leader claim
rather than a fact.  The reading digest has neither problem: both sides compute it from
their own extraction, and it must be identical.  A consequence worth stating plainly: the
stored record identifies *what was read* (which source, which value), not *which bytes
came back over the wire*.

The digest is also what makes the per-source reading exact (under the standard
collision-resistance assumption for keccak256).  The `within` check on the same value
(check 4) is a redundant guard; the digest is the binding constraint.

### The tolerance, precisely

```
diff = |a - b|
within(a, b) = diff <= tol_abs  OR  diff * 10000 <= tol_rel_bps * max(|a|, |b|)
B(M) = max(tol_abs, ceil(tol_rel_bps * M / (10000 - tol_rel_bps)))
```

Both sides are scaled integers, so this is exact integer arithmetic and every node
computes the same boolean.  The tolerance has exactly two jobs:

1. it classifies a reported source as agreeing or disagreeing with the median
   (`DISPUTED` and the quorum), and
2. it sets the width of the interval for an incomplete read.

It is **not** used to accept an aggregate that is merely close to the median.  That was
the rejected v1 bug: a leader could store `median + tol` and every validator accepted it.

---

## 5. What is stored, and why it is safe to trust

```
Record {
  lo, hi           the exact median when complete, else [median-B, median+B]
  flags            DISPUTED | PARTIAL | STALE
  agree_count      sources that agree with the median
  sources_read     sources that were read
  sources_declared sources the curator declared
  quorum_required  the curator's minimum quorum
  read_at          the transaction timestamp of the refresh
  data_ts          the oldest source-declared timestamp among the voters
  readings[]       index, value, digest, data_ts, agrees  (one per source read)
}
```

Every field of `Record` is recomputed by the validator from its own reads (section 4).
There is no counter, no caller registry, no reputation, no per-caller state.  A refresh
overwrites one slot, so spam cannot grow the contract.

Two fields of the contract are **not** consensus-derived, and they are the only ones:
`owner` and the per-feed `disabled` flag.  They are administrative: they make no claim
about the outside world.

---

## 6. Why the clock is not used for freshness

Inside the GenVM the clock is pinned to the transaction timestamp: `datetime.now()`
returns the same value on the leader and on every validator that re-executes the
transaction.  There is no block number, no block hash and no host wall clock in the
contract context.

That has two consequences, and the design follows from them:

1. **The read time cannot be the wall clock.**  The contract stores the *transaction
   timestamp* as `read_at`.  It is identical for every node and it is exactly what the
   transaction happened at - but it is not the moment the HTTP request was served.
2. **Freshness must come from the data.**  A source that wants to be checked for staleness
   declares a `time_path` pointing at its own timestamp.  The contract compares that
   timestamp with the transaction timestamp and flags the reading `STALE` when it is older
   than `max_age_seconds`.

A feed whose sources expose no timestamp simply cannot enforce a staleness limit.  That
is a property of the sources, and it is the curator's job to pick sources that publish
their own time.

---

## 7. Disagreement is a result, not an error

| situation | what the committee does | what the consumer sees |
|:--|:--|:--|
| the sources disagree, but the quorum still holds | the reading is published, the outlier is kept in the provenance table with `agrees = false` | `flags = DISPUTED`, `status = "disputed"`, `is_trustworthy = false` |
| a source cannot be read | the source does not vote; the reading is published as an interval with the gap disclosed | `flags = PARTIAL`, `sources_read < sources_declared`, `is_trustworthy = false`, `compare` may return `INDETERMINATE` |
| the sources are so far apart that the quorum fails | the proposal is rejected, the leader is rotated | nothing is written; the reading keeps its previous value |
| the leader's interval is not the exact median/ball | the proposal is rejected | nothing is written |
| the page changed between the leader and the validator | accepted only if the derived value did not change; the reading digest is identical | the digest of the reading is stored |
| the leader omits a source the validator can read | accepted only if the source is inside `[lo, hi]` and the full-set median is inside too | `PARTIAL` plus the interval |

The consumer decides what to do with a `DISPUTED` or `PARTIAL` reading.  The primitive
publishes the evidence and the status; the policy belongs to the consumer.

**A note on the missing-source case.**  If no validator can read a declared source, no
tolerance can bound what that source would have said.  The record is still `PARTIAL` and
`is_trustworthy` is false, which is the refusal signal.  `compare` is a statement about
the stored interval over the readable sources; it is not a guarantee about an unreadable
one.  This is why the consumer example gates on `is_trustworthy` before acting on
`compare`, and why the gate is part of the documented interface rather than an
optional nicety.

---

## 8. Reading it from another contract

```python
feed = gl.contract.get_at(FEED_ADDRESS)
view = feed.view(state=StorageView.LATEST_FINALIZED)   # pin the read to the final state
if view.is_trustworthy("eur-usd", 3600):
    relation = view.compare("eur-usd", threshold)       # ABOVE / BELOW / INDETERMINATE
    if relation == "ABOVE":
        ...  # every value the reading admits is >= threshold
    elif relation == "BELOW":
        ...  # every value the reading admits is < threshold
    else:
        ...  # the reading cannot decide; do not act
```

Two things matter here:

* `StorageView.LATEST_FINALIZED` - a public RPC can serve a not yet final state right after
  a receipt, and the default view reads that.  A consumer that must not act on a reading
  that can still change pins the final state.
* `is_trustworthy` - `compare` alone is a statement about the stored interval, not a
  statement that the feed is fresh or that every source was read.  The guard requires
  `flags == 0`, the quorum, a complete read and fresh data within the caller's max age.

For a strict `value > threshold` rule, pass `threshold + 1`: the stored values are scaled
integers and `ABOVE` means `value >= threshold`.

The GenLayer direct test runner cannot execute a cross-contract call, so the consumer's
decision rule is exposed as a plain function (`decide`) and is unit tested that way.  The
cross-contract call itself is exercised by the live run.

---

## 9. Limits, and what a stronger version would change

* **Numeric feeds only.**  The extraction rule can point anywhere inside a JSON document,
  but the aggregate is one scaled integer per source.  A structured value (several fields
  per source) would need a per-field tolerance and a per-field quorum - a natural
  extension, not a redesign.
* **The leader may omit a source it failed to read.**  The committee cannot tell "I could
  not reach it" from "I chose not to report it".  The disclosure is structural: an
  omitted source means `sources_read < sources_declared`, therefore the `PARTIAL` flag,
  therefore `is_trustworthy = false`.  A consumer that wants full coverage checks
  `status == "agreed"`.
* **The interval is only as good as the declared tolerance.**  A curator can declare a
  tolerance wide enough to accept almost anything; the committee verifies the readings
  against the declared sources, not the declaration.  The 2000 bp cap bounds how wide the
  interval can get, but it does not make a bad source good.
* **A boundary case can make the committee reject a correct reading.**  If a source sits
  exactly on the tolerance boundary, the leader and the validator can disagree about
  whether it agrees.  The result is a rotation, not a wrong number.
* **One trusted key is still one key.**  The curator is a single address.  A multi-curator
  scheme (a council, or a quorum of curators) would be the natural next step, and it would
  not change the consensus mechanism at all.
