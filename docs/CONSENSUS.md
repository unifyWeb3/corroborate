# How the consensus works

This document is for a builder who wants to use `corroborate`, or wants to copy the
pattern into their own contract.  It walks through one `refresh` transaction from the
caller to the stored record.

---

## 0. The shape of the problem

A contract needs a number that lives outside the chain: a price, a rate, an index.  The
number comes from the web, so:

* two nodes that fetch at different moments can see different values,
* a node can see a page that another node cannot reach,
* the leader of a transaction is just one node - it can be wrong, lazy or hostile,
* and a stored number without evidence cannot be checked later.

GenLayer already gives us the tool for the third problem: the **Equivalence Principle**.
A leader proposes a result, and validators independently decide whether to accept it.  What
this contract adds is the *rule* for that decision, and the *evidence* that is stored with
the result.

The rule used here is **tolerance equivalence**:

> A reading is accepted only if an independently re-derived reading, computed by a
> validator from bytes it fetched itself, agrees with the leader's proposal inside the
> feed's declared tolerance, and every per-source claim in the proposal is corroborated by
> the validator's own read of that same source.

It is numeric, not textual: the compared values are scaled integers, so the comparison is
integer arithmetic, and it is identical on every node.

---

## 1. What the feed declares

The curator declares, once, per feed:

| field | meaning |
|:--|:--|
| `sources` | 2 to 8 public HTTPS URLs, each with its own extraction rule |
| `value_path` | dotted path to the number inside the JSON document, e.g. `rates.EUR` |
| `time_path` | optional dotted path to the source's own unix timestamp |
| `scale` | decimal multiplier, e.g. `100000` for five decimals of a rate |
| `tol_rel_bps` | relative tolerance in basis points (100 bp = 1 %) |
| `tol_abs` | absolute tolerance in scaled units (for values near zero) |
| `max_age_seconds` | staleness limit for the source-declared timestamps |
| `min_quorum` | how many sources must agree with the median |

The configuration is immutable once registered.  It is *not* consensus-derived: the curator
is trusted for it.  Everything that describes an actual reading *is* consensus-derived.

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
   |  value = median(values)                 |
   |  agree = sources within tolerance       |
   |  flags = disputed / partial / stale     |
   |  proposal = {value, flags, agree_count, |
   |              sources_read, read_at,     |
   |              data_ts, readings[]}       |
   +------------------------------------------+
          |  proposal
          v
   +------------------------------------------+
   |        VALIDATORS (every other node)    |
   |  fetch every source again (nondet)      |
   |  re-derive values, digests, timestamps  |
   |  recompute median, agreeing set, quorum,|
   |  flags and read time                    |
   |  compare with the proposal              |
   |  -> accept (True) or reject (False)     |
   +------------------------------------------+
          |  accepted proposal
          v
   +------------------------------------------+
   |  store the record (deterministic code)  |
   +------------------------------------------+
```

The whole of steps "leader" and "validators" happens inside the non-deterministic block.
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

A stale source still votes: excluding it would make the aggregate depend on a freshness
decision that the leader and the validator could make differently.  Instead the reading is
flagged and the consumer guard refuses it.

The lower median is used on purpose: it is always one of the observed values, so the
published number is always a number that some source actually reported, and the median
cannot be a value nobody saw.

---

## 4. The validator, step by step

The validator does **not** look at the leader's numbers and ask whether they look
reasonable.  It re-reads the world and re-derives the reading, then compares.

| # | check | what it protects against |
|:--|:--|:--|
| 1 | the proposal has every expected field and `read_at` equals the pinned transaction timestamp | a fabricated read time, a malformed proposal |
| 2 | the proposal's reading table names declared sources only, once each | a duplicated or invented source |
| 3 | the validator also read every source the leader used | an aggregate the validator cannot reproduce |
| 4 | `|leader_value_i - validator_value_i| <= tolerance` for every source | a leader that reports a number the source never showed |
| 5 | the leader's digest equals the digest of the validator's own reading of that source | a leader that reports a reading the validator did not make |
| 6 | the source timestamps agree within `TS_TOL_SECONDS` | a leader that invents a freshness timestamp |
| 7 | `|leader_value - validator_median| <= tolerance` over the same source set | an aggregate that is not the median of the readings |
| 8 | the recomputed agreeing count equals the leader's `agree_count` | an inflated quorum |
| 9 | the recomputed agreeing count satisfies `min_quorum` | publishing a number nobody agrees on |
| 10 | the recomputed flags equal the leader's flags | a status that hides a disagreement |
| 11 | `sources_read` and `data_ts` match the recomputation | an incomplete or invented provenance record |
| 12 | the per source verdict equals the validator's own verdict | a leader that calls a dissenting source an agreeing one |
| 13 | a source the leader left out but the validator could read agrees with the aggregate | a leader that hid a dissent by omission |

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

A source the leader could not read is not held against it: only a source the validator
*could* read and that disagrees with the aggregate turns the omission into a rejection.

### What the digest covers, and why

The digest is `keccak256("<source index>:<scaled value>")` - the digest of the *reading*.
It is deliberately not a digest of the raw response body.

The reason is verification.  A page that ticks between the leader's read and the
validator's read has two different bodies, so two different body digests, even though both
nodes read the same value.  Accepting that case means accepting a digest the committee
cannot confirm, and a stored digest that nobody verified is a leader claim rather than a
fact.  The reading digest has neither problem: both sides compute it from their own
extraction, and it must be identical.  A consequence worth stating plainly: the stored
record identifies *what was read* (which source, which value), not *which bytes came back
over the wire*.

### The tolerance, precisely

```
diff = |a - b|
within(a, b) = diff <= tol_abs  OR  diff * 10000 <= tol_rel_bps * max(|a|, |b|)
```

`a` is the leader's value, `b` the validator's.  Both sides are scaled integers, so this
is exact integer arithmetic and every node computes the same boolean.

The absolute term exists for values near zero (an interest rate of 0.0001 would need an
unusable relative tolerance), the relative term for values that move proportionally.

---

## 5. What is stored, and why it is safe to trust

```
Record {
  value            the aggregate (median of the sources that voted)
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
| a source cannot be read | the source does not vote, the reading is published with the gap disclosed | `flags = PARTIAL`, `sources_read < sources_declared`, `is_trustworthy = false` |
| the sources are so far apart that the quorum fails | the proposal is rejected, the leader is rotated | nothing is written; the reading keeps its previous value |
| the leader's value is not reproducible by the validator | the proposal is rejected | nothing is written |
| the page changed between the leader and the validator | accepted only if the derived value did not change | the digest of the reading is stored |

The consumer decides what to do with a `DISPUTED` or `PARTIAL` reading.  The primitive
publishes the evidence and the status; the policy belongs to the consumer.

---

## 8. Reading it from another contract

```python
feed = gl.contract.get_at(FEED_ADDRESS)
view = feed.view(state=StorageView.LATEST_FINALIZED)   # pin the read to the final state
if view.is_trustworthy("eur-usd", 3600):
    price = view.value("eur-usd")
```

Two things matter here:

* `StorageView.LATEST_FINALIZED` - a public RPC can serve a not yet final state right after a
  receipt, and `LATEST_NON_FINAL` (the default) reads that.  A consumer that must not act
  on a reading that can still change pins the final state.
* `is_trustworthy` - reading `value` alone gives the last stored number whatever its
  status.  The guard is what turns "a number" into "a number the committee vouched for".

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
* **A boundary case can make the committee reject a correct reading.**  If a source sits
  exactly on the tolerance boundary, the leader and the validator can disagree about
  whether it agrees.  The result is a rotation, not a wrong number.
* **The curator is trusted for the configuration.**  A curator can point a feed at sources
  it controls, or declare a tolerance wide enough to accept anything.  The committee
  verifies the readings against the declared sources; it cannot verify the declaration.
* **One trusted key is still one key.**  The curator is a single address.  A multi-curator
  scheme (a council, or a quorum of curators) would be the natural next step, and it would
  not change the consensus mechanism at all.
