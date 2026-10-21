# DESIGN - "corroborate"

A tolerance-consensus data feed primitive for GenLayer Intelligent Contracts.

Status: **approved design, feature frozen after this document** (only fixes for verified
blockers/security issues may be added afterwards).

---

## 1. Purpose

**One sentence problem statement.** A contract that needs a number from the public web has no way
to know whether that number is real, current, or corroborated: a single HTTP read is one node's
one moment, and a contract that stores it stores an unverified claim.

**What this contract is.** A reusable primitive that turns "read a number from the web" into a
*consensus-backed reading*:

* several public HTTPS **sources** per feed, each with a **declared extraction rule** (a JSON path);
* a **median** aggregation rule plus a **declared tolerance** (relative in basis points and
  absolute in scaled units);
* a **quorum** (minimum number of sources that must agree with the median);
* **staleness limits** derived from a source-declared timestamp;
* validators that **independently re-fetch and re-derive** the value and accept the leader's
  proposal only when it lands inside the declared tolerance and every per-source claim is
  corroborated;
* a stored **provenance** record: per-source value, the keccak256 digest of the exact body that
  was read, the source's own data timestamp, and the read time;
* **agreement status as a first-class value**: disagreement between sources and unreachable
  sources are *results*, not errors.

**Who benefits.** Any Intelligent Contract that needs an external number: a lending contract that
must price a collateral, a settlement contract that must know a reference rate, a trigger
contract, a DAO treasury policy. They call one read method and get a value **plus** the evidence
needed to decide whether to trust it.

**Why it is a primitive and not a demo.** The reusable unit is not the price feed; it is the
*tolerance-consensus reading protocol*: declared sources -> declared extraction -> declared
aggregate -> declared tolerance -> independent re-derivation -> verified provenance. A new feed is
a configuration entry, not new code. The contract is also directly composable: another contract
reads it with `gl.get_contract_at(...).view(state=StorageType.LATEST_FINAL)` and gates on the
agreement status.

**Why it is not a thin LLM wrapper.** There is no LLM in the design at all (research found none
needed - see section 8). The check is arithmetic over values the validator computed itself from
bytes it fetched itself. It is also not format-only: the validator re-derives the *number* and the
*digest* of every source body, recomputes the median, the agreeing set, the quorum and the
flags, and rejects anything that does not match.

**Not escrow, not arbitration, not a court.** No deposits, no bonds, no dispute resolution, no
verdicts about parties. It measures a number and publishes the evidence for it.

---

## 2. Roles, trust model

| actor | power | trusted for |
|:--|:--|:--|
| **curator** (the deployer, `owner`) | registers a feed, disables a feed | the feed *configuration* (which sources, which extraction rule, which tolerance, which quorum). The curator cannot forge a reading: every stored number is re-derived by the validator committee. |
| **anyone** | may call `refresh(feed_id)` for an existing feed | nothing. A refresh can only write a record that the committee re-derived. |
| **validator committee** | re-derives and accepts/rejects | the *readings*. This is the part the contract is about. |
| **consumer** (another contract) | reads `latest(...)` / `is_trustworthy(...)` | nothing; it must still apply its own policy (e.g. max age). |

Plain statement of the remaining trust:

* The curator is trusted for configuration. A malicious curator can point a feed at sources it
  controls, or set an absurdly wide tolerance, or declare a `time_path` that always looks fresh.
  The contract cannot and does not prevent that.
* The committee is trusted to actually re-fetch. A validator that rubber-stamps the leader is out
  of this contract's control - that is GenLayer's base assumption, and appeals are the network's
  answer to it.
* Two stored fields are **administrative, not consensus-derived**: `owner` and the per-feed
  `disabled` flag. They make no claim about the outside world. Every field that describes an
  external reading is consensus-derived and re-checked by validators.
* A leader may omit a source it failed to read. That is visible: the record carries the
  `PARTIAL` flag and `sources_read < sources_declared`. See section 5.6 for the exact disclosure.

---

## 3. State design

Storage types follow the GenVM rules: `TreeMap` for maps, `DynArray` for arrays, sized integers
(`u32`, `u64`, `i64`), `str`, `bool`. No `list`, `dict` or raw `int` in storage.

```
owner: Address                                   # curator address (deployer)

feeds: TreeMap[str, Feed]                       # curator-registered, immutable after creation
    Feed:
        label: str                               # human name of the feed
        unit: str                                # unit of the scaled value, e.g. "USD"
        tol_rel_bps: u32                         # relative tolerance, basis points (1 bp = 0.01%)
        tol_abs: i64                             # absolute tolerance, in scaled units
        max_age_seconds: u64                     # staleness limit for source-declared timestamps
        min_quorum: u32                          # min sources that must agree with the median
        sources: DynArray[Source]                # 2..8 entries, order is meaningful
        created_at: u64                          # curator-side creation time (tx timestamp)
        disabled: bool                           # administrative kill switch (curator only)
        Source:
            url: str                             # https URL, <= 256 chars
            value_path: str                      # dotted JSON path to the number, e.g. "data.price"
            time_path: str                       # dotted JSON path to the source's own unix ts, "" = none
            scale: u32                           # decimal multiplier, e.g. 100 for cents

records: TreeMap[str, Record]                   # latest verified reading per feed (overwritten)
    Record:
        value: i64                               # aggregate (median of the voting set), scaled
        flags: u8                                # bitmask: DISPUTED=1, PARTIAL=2, STALE=4
        agree_count: u32                         # sources that agree with the median
        sources_read: u32                        # sources successfully read
        sources_declared: u32                    # = len(feed.sources)
        quorum_required: u32                    # = feed.min_quorum (copied for the consumer)
        read_at: u64                            # transaction timestamp of the refresh (pinned clock)
        data_ts: u64                            # oldest source-declared timestamp among voters (0 = none)
        readings: DynArray[Reading]              # provenance, one entry per successfully read source
        Reading:
            index: u32                          # index into feed.sources
            value: i64                          # scaled value read from that source
            digest: str                         # keccak256 hex of the raw response body
            data_ts: u64                        # source-declared timestamp (0 if the source declares none)
            agrees: bool                        # within tolerance of the median?
```

### Who may write what

| field | writer | validated by the committee before it is stored |
|:--|:--|:--|
| `owner` | deployer | no (administrative) |
| `feeds[*].*` (configuration) | curator only, once | no (administrative; the curator is trusted for the configuration) |
| `feeds[*].disabled` | curator only | no (administrative) |
| `records[*].*` | any caller of `refresh`, after consensus | **yes, every field** - see section 5 |

### Why every stored record field is committee-checked

`records[feed_id]` is written only after `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)`
returns, and the validator function recomputes every field from its own network reads:

* `value` - the validator recomputes the median of its own readings of the same source set and
  requires `|leader_value - my_median| <= tolerance`;
* `readings[i].value` - the validator re-reads that source and requires
  `|leader_value_i - my_value_i| <= tolerance`;
* `readings[i].digest` - the validator recomputes keccak256 of the body it fetched; the digest
  must be identical, or (body drifted) the values must be identical;
* `readings[i].data_ts` - the validator re-reads the source's own timestamp and requires it to be
  within `ts_tol_seconds` of the leader's;
* `readings[i].agrees` - recomputed by the validator from its own values against the median;
* `agree_count` - recomputed, must be **exactly** equal;
* `sources_read` - the number of entries in the leader's reading table, each one corroborated;
* `sources_declared` - equals `len(feed.sources)` (configuration, committee re-reads it too);
* `quorum_required` - equals `feed.min_quorum`;
* `data_ts` - the minimum source timestamp over the voting set, recomputed by the validator;
* `flags` - recomputed bit by bit from the validator's own observations;
* `read_at` - the transaction timestamp. Inside the GenVM the clock is pinned to the transaction
  timestamp, so the validator computes the same value and can check it.

Nothing else is persisted. There is no counter, no registry of callers, no reputation, no
per-caller state: the record slot is overwritten in place, so repeated refreshes cannot grow
storage.

---

## 4. Interface

### Write methods

| method | who | effect |
|:--|:--|:--|
| `register_feed(feed_id, label, unit, tol_rel_bps, tol_abs, max_age_seconds, min_quorum, sources_json)` | curator only | creates an immutable feed definition. Rejects a bad id, a bad URL, a bad path, a bad tolerance, a bad quorum, a malformed or oversize `sources_json`. |
| `refresh(feed_id)` | anyone | fetches every declared source, derives the reading, and stores it **only if** the committee accepts the leader's proposal. |
| `disable_feed(feed_id, disabled)` | curator only | administrative kill switch. A disabled feed refuses `refresh`. |

### View methods

| method | returns |
|:--|:--|
| `latest(feed_id)` | the full `Record` (value, flags, counts, timestamps, per-source provenance) |
| `value(feed_id)` | the scaled integer value only |
| `status(feed_id)` | a human-readable status string: `agreed`, `disputed`, `partial`, `stale`, `disabled`, `unknown`, or a combination such as `disputed+partial` |
| `is_trustworthy(feed_id, max_age_seconds)` | **the consumer guard**: true only when the record exists, the feed is enabled, `flags == 0`, the quorum was met and the reading is younger than `max_age_seconds` |
| `feed(feed_id)` | the feed definition |
| `feed_count()` | number of registered feeds |
| `source_count(feed_id)` | number of declared sources |

`sources_json` is a JSON array of objects, one per source:

```json
[{"url": "https://api.example.com/price",
  "value_path": "data.price",
  "time_path": "data.updated_at",
  "scale": 100}]
```

`value_path` and `time_path` are dotted paths: `a.b.c` walks nested objects and array indices
(`a.0.b` is `a[0].b`). A path segment that is not present makes the source **fail** (it is not
silently zero).

### Minimal caller example

```python
# contracts/ExampleConsumer.py
from genlayer import *
from genlayer.py.types import Address, u256

FEED_ADDRESS = Address("0x...")     # the deployed corroborate contract

class ExampleConsumer(gl.Contract):
    last_seen: i64
    accepted: u32
    rejected: u32

    @gl.public.write
    def check(self, feed_id: str, max_age: u64):
        feed = gl.get_contract_at(FEED_ADDRESS)
        # pin the read to the final state, not the newest non-final one
        record = feed.view(state=StorageType.LATEST_FINAL).is_trustworthy(feed_id, max_age)
        if record:
            self.accepted += 1
        else:
            self.rejected += 1
```

A consumer that ignores `is_trustworthy` and reads `value(feed_id)` directly gets the last
stored reading, whatever its status. That is why the guard exists and why the status is a
first-class field.

---

## 5. Consensus design

### 5.1 The rule being enforced

> **Tolerance equivalence.** A reading is accepted only if an independently re-derived reading,
> computed by a validator from bytes it fetched itself, agrees with the leader's proposal inside
> the feed's declared tolerance, and every per-source claim in the proposal is corroborated by
> the validator's own read of that same source.

The equivalence is *numeric tolerance*, not string equality and not an LLM judgement.

### 5.2 What the leader computes

Inside the non-deterministic block the leader, for every declared source:

1. `gl.nondet.web.get(url)` -> `response.status`, `response.body` (bytes);
2. reject bodies larger than `MAX_BODY_BYTES` (65536) as `OVERSIZE`;
3. `digest = keccak256(body).hex()`;
4. `doc = json.loads(body)`; on a parse error the source fails as `MALFORMED`;
5. `raw = extract(doc, value_path)`; a missing path or a non-numeric leaf fails as `MISSING`;
6. `value = to_scaled_integer(raw, scale)`; a non-integral or out-of-range number fails as
   `BAD_VALUE`;
7. `ts = extract(doc, time_path)` when the source declares one (0 otherwise); a missing or
   non-integer timestamp fails as `MISSING`;
8. the source's **verdict** is then computed: `FRESH` or `STALE` (see 5.4).

Then the aggregate:

* `R` = the sources that were read successfully;
* `V` = `R` minus the stale ones (a stale source does not vote);
* `m` = the **lower median** of the values in `V` (always one of the observed values - no
  interpolation, so the stored value is always a value that some source actually reported);
* `A` = the sources in `V` whose value is within the tolerance of `m`;
* the quorum holds when `len(A) >= min_quorum`.

The leader's proposal contains: the aggregate, the flags, the counts, the read time and the full
per-source table (value, digest, timestamp, agrees).

### 5.3 What each validator independently re-derives

The validator function **does not trust the leader's numbers**. It re-fetches every declared
source itself and recomputes the whole reading from its own bytes. Then it checks, in order:

| # | check | failure means |
|:--|:--|:--|
| 1 | the proposal's reading table has exactly one entry per source the leader read, and no entry for a source the leader failed to read | leader-output-only / malformed proposal -> reject |
| 2 | for every source the leader read, the validator also read it successfully | the validator cannot re-derive the aggregate -> reject |
| 3 | `|leader_value_i - validator_value_i| <= tolerance` for every source | the sources disagree beyond the declared tolerance -> reject |
| 4 | `digest_i == validator_digest_i`, **or** (`digest` differs **and** the two values are exactly equal) | the leader did not read the body it claims, or the body moved and the value moved with it -> reject |
| 5 | source timestamps agree within `TS_TOL_SECONDS` (120) and the validator's own freshness verdict equals the leader's | freshness disagreement -> reject |
| 6 | `|leader_value - validator_median| <= tolerance` over the same source set | the aggregate was not re-derivable -> reject |
| 7 | the validator's agreeing set has exactly the same size as the leader's `agree_count` | the quorum was misreported -> reject |
| 8 | the validator's own agreeing set satisfies `>= min_quorum` | no quorum -> reject (the network rotates the leader) |
| 9 | the leader's flags equal the flags the validator recomputed | the status was misreported -> reject |
| 10 | the leader's `read_at` equals the transaction timestamp the validator computes | the read time was fabricated -> reject |

If all ten hold, the validator returns `True` and the leader's proposal becomes the stored
record. If any fails, the validator returns `False`: the proposal is rejected, GenLayer rotates
the leader, and if no leader can satisfy the committee the transaction ends **undetermined** and
**no state is written**.

### 5.4 The tolerance, precisely

For two scaled integers `a` (leader) and `b` (validator):

```
diff = |a - b|
within(a, b) = diff <= tol_abs  OR  diff * 10000 <= tol_rel_bps * max(|a|, |b|)
```

Both components are declared per feed. `tol_abs` covers values near zero (exchange rates,
interest rates), `tol_rel_bps` covers proportional drift. A relative tolerance of 50 bp = 0.5 %.

### 5.5 Freshness

The GenVM clock is pinned to the transaction timestamp, so it cannot tell us when a source was
read. The only honest freshness signal is a timestamp **inside the source document**, so a feed
may declare `time_path`. The contract stores the **oldest** timestamp among the voting sources
(`data_ts`) and flags the record `STALE` when `tx_time - data_ts > max_age_seconds`. A source
whose data timestamp is stale does not vote; it is excluded from the aggregate and the `STALE`
flag is raised. If no source declares a timestamp, no staleness limit can be enforced - this is
stated in the README and in the feed configuration, and the curator is trusted to pick sources
that expose their own timestamps.

### 5.6 Disagreement, outliers and unreachable sources

These are **results**, not errors:

* **Outlier source** - read successfully but further than the tolerance from the median. It stays
  in the provenance table with `agrees = false`, the record is flagged `DISPUTED`, and the value is
  still the median of the agreeing/voting set.
* **Unreachable source** - the leader could not read it. The source contributes nothing, the
  record is flagged `PARTIAL`, and `sources_read < sources_declared` makes the gap explicit. The
  curator can set `min_quorum` so that a partial record is still acceptable to them.
* **No quorum** - fewer than `min_quorum` sources agree with the median. The committee rejects
  the proposal, the leader rotates, and eventually the transaction is undetermined. Nothing is
  stored. This is the correct behaviour: a number nobody agrees on is not published.
* **The leader may omit a source it failed to read.** The committee cannot tell "I could not
  reach it" from "I chose not to report it", so the disclosure is structural: an omitted source
  means `sources_read < sources_declared` and therefore the `PARTIAL` flag. A consumer that
  requires full coverage checks `status(feed_id) == "agreed"`.

### 5.7 Flags

```
DISPUTED = 1   some source that was read disagrees with the median
PARTIAL  = 2   some declared source could not be read
STALE    = 4   the oldest voting source's own timestamp is older than max_age_seconds
```

`flags == 0` means "agreed, complete and fresh". `is_trustworthy` requires `flags == 0`.

### 5.8 Noise between validators

Two kinds of noise exist and both are handled explicitly:

* *The page changed between the leader and the validator* (price feeds tick). The digest differs.
  The contract accepts it **only if the extracted values are exactly equal**; otherwise the
  validator rejects the proposal. This is the "CDN copy" case as well.
* *The validator's own read differs slightly from the leader's* (rounding, a different moment).
  This is the normal case and is covered by the tolerance in check 3 and 6.

---

## 6. Errors and refusals

`gl.vm.UserError` is used for every refusal (never a bare Python exception - the linter rejects
those, and they break consensus error decoding).

| refusal | message |
|:--|:--|
| feed does not exist | `unknown feed` |
| feed is disabled | `feed disabled` |
| caller is not the curator | `not the curator` |
| feed id is empty / too long / has illegal characters | `invalid feed id` |
| `sources_json` is not valid JSON / not a list / too long | `invalid sources` |
| a source url is not https / too long | `invalid source url` |
| a path is too long or has an empty segment | `invalid path` |
| fewer than 2 or more than 8 sources | `invalid source count` |
| `tol_rel_bps > 10000` or `tol_abs < 0` | `invalid tolerance` |
| `min_quorum` is 0 or greater than the source count | `invalid quorum` |
| `scale` is 0 or greater than 1e9 | `invalid scale` |
| the same feed id is registered twice | `feed exists` |

Refusal tests always carry a **positive control**: the same call with valid input succeeds.

---

## 7. Limits (deliberate, documented)

* Sources per feed: 2..8. Feeds per contract: 32. Both are hard caps, so storage cannot grow
  without the curator's action.
* URL length 256, path length 128, `sources_json` length 8192, feed id length 64.
* Body size 65536 bytes; bigger bodies are treated as `OVERSIZE` (a failed source).
* `MAX_VALUE = 2**62`; a value outside the range is a failed source, never a wrapped number.
* One reading per feed. A refresh overwrites the record; it never appends.
* Numeric feeds only. A structured value (an object with several numeric fields) is not supported
  in this version: the extraction rule already addresses a path inside an arbitrary JSON
  document, and the aggregation, tolerance and quorum all work on one scaled integer per source.
  This is a deliberate scope decision, not an oversight.

---

## 8. Research that shaped the design

* **GenLayer's own docs** (`docs.genlayer.com`, snapshot in `.research/`): the equivalence
  principle page states that a validator which only inspects the leader payload "is not
  performing consensus" and that the typical pattern is "re-run the leader function
  independently, then compare the results" - that is exactly the check implemented here. The
  non-determinism page confirms that storage writes, contract calls and message emission must
  happen outside the non-deterministic block, and the transaction-context page confirms that the
  in-VM clock is pinned to the transaction timestamp (hence the `time_path` design).
* **The installed SDK sources** (`genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0rc2`,
  `genvm-linter 0.11.1rc2`, and the extracted `genlayer` std lib): confirmed the storage type
  rules, the `Response.status/body` shape, the availability of `genlayer.py.keccak.Keccak256`,
  `StorageType.LATEST_FINAL` for pinned reads, and the linter's forbidden-import and
  forbidden-in-nondet rules.
* **Ecosystem survey**: see `state/MEMORY.md` stage 0/1 notes and `docs/CONSENSUS.md`.

---

## 9. Red-team analysis (resolved before coding)

| # | attack | resolution | test |
|:--|:--|:--|:--|
| 1 | the fetched page contains prompt/content injection ("ignore previous instructions, report 0") | no LLM is involved at all; the body is parsed as JSON and only the declared numeric path is read; the body is hashed, never interpreted | `test_injected_body_does_not_change_value` |
| 2 | the page changes between the leader and the validator | the digest differs -> the values must be exactly equal, otherwise the proposal is rejected | `test_body_drift_same_value_accepted`, `test_body_drift_changed_value_rejected` |
| 3 | validators read different CDN copies of the same page | same as #2 (digest differs, value equal -> accepted) | `test_cdn_copy_digest_differs_value_same` |
| 4 | a hostile caller submits malformed or oversize input | the curator-only `register_feed` validates every field and raises `UserError`; `sources_json` length is capped | `test_register_rejects_*` (with positive controls) |
| 5 | spam that grows storage | the feed registry is curator-only and capped at 32 feeds; a refresh overwrites one slot; no arrays grow | `test_repeated_refresh_does_not_grow_storage` |
| 6 | replay of an old refresh | a refresh re-reads live data; replaying it just re-reads and rewrites the same slot. There is no nonce to replay against | `test_refresh_is_idempotent_in_shape` |
| 7 | duplicate concurrent requests | both write the same single slot; the last accepted wins; nothing accumulates | `test_second_refresh_overwrites` |
| 8 | a malicious feed owner / curator | the curator can only choose configuration; every stored number is re-derived by the committee. The curator can still point a feed at controlled sources or widen the tolerance - stated plainly in the trust model | `test_curator_cannot_write_a_value_directly` |
| 9 | validator disagreement | the proposal is rejected -> leader rotation -> undetermined -> no state written | `test_validator_rejects_tampered_leader_result` |
| 10 | a consumer that ignores the agreement status | `is_trustworthy` is the documented guard; the example consumer uses it and refuses disputed/stale/partial records | `test_consumer_guard_rejects_disputed_and_stale` |
| 11 | a stale result | `STALE` flag + `data_ts` + `is_trustworthy(max_age)` | `test_stale_source_is_flagged_and_excluded` |
| 12 | an unreachable source | the source fails, the record is `PARTIAL`, and if the quorum still holds the value is published with the gap disclosed | `test_unreachable_source_yields_partial_record`, `test_quorum_failure_rejects` |
| 13 | a source returning HTML or a 500 instead of JSON | the source fails (`MALFORMED` / `HTTP_ERROR`); the record is `PARTIAL` | `test_html_body_is_a_failed_source` |
| 14 | a leader that reports a value it did not read | the validator re-reads every source and checks the value and the digest | `test_leader_value_not_matching_source_rejected` |
| 15 | a leader that inflates the agreeing count | the validator recomputes the count and requires exact equality | `test_inflated_agree_count_rejected` |
| 16 | a leader that reports a fake read time | `read_at` must equal the pinned transaction timestamp | `test_fake_read_at_rejected` |
| 17 | a feed with a single source | rejected at registration (minimum 2 sources): one source is not corroboration | `test_register_rejects_single_source` |
| 18 | a source whose value overflows the integer range | `MAX_VALUE` check -> the source fails instead of wrapping | `test_out_of_range_value_fails_source` |

---

## 10. Feature freeze

Frozen after this document. Allowed afterwards: bug fixes, security fixes, extra tests, extra
documentation. Not allowed: new public methods, new state fields, new configuration knobs, new
aggregation rules.
