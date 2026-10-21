# corroborate

**A tolerance-consensus data feed with provenance, for GenLayer Intelligent Contracts.**

`corroborate` turns "read a number from the web" into a number that a committee of
validators re-derived from the same sources, within a tolerance the feed declares, and
that comes back with the evidence: which sources agreed, the keccak256 digest of every
reading, the source's own timestamp and the read time.

It is a primitive, not an application.  Another contract asks it for a number and gets a
number plus the agreement status it needs to decide whether to use it.

```python
feed = gl.contract.get_at(FEED_ADDRESS)
view = feed.view(state=StorageView.LATEST_FINALIZED)
if view.is_trustworthy("eur-usd", 3600):
    price = view.value("eur-usd")
```

---

## The problem

A contract that needs an external number has no way to know whether that number is real,
current or corroborated.  One HTTP read is one node, at one moment, and if the contract
stores it, it stores an unverified claim that later consumers have to take on faith.

The usual answers are a trusted feed operator (a single point of failure) or an LLM that
summarises a page (a different point of failure, and not a number).  GenLayer's Equivalence
Principle gives a third way: a leader proposes, and every validator independently decides
whether to accept.  What is usually missing is *what the validators should check* and
*what evidence should be kept*.

## What this contract does

For each feed the curator declares:

* **several public HTTPS sources** (2 to 8), each with its own **extraction rule** (a JSON
  path such as `rates.EUR`),
* an **aggregation rule** (the median) and a **tolerance** (relative in basis points,
  absolute in scaled units),
* a **quorum**: how many sources must agree with the median,
* a **staleness limit**, applied to a timestamp the source itself publishes.

Any caller can then call `refresh(feed_id)`.  The leader fetches every source, derives one
number per source and aggregates them.  **Every validator re-fetches every source itself**
and re-derives the whole reading before it accepts the leader's proposal:

* each per-source value must be within the declared tolerance of the validator's own value,
* each per-source **digest of the reading** must equal the digest the validator computed
  for its own reading,
* the aggregate must equal the validator's own re-derived median within the tolerance,
* the quorum, the flags and the read time must be exactly what the validator computes.

Only then is anything written.  What is written is the value **and** its provenance.

Disagreement is a first-class result, not an error: a source that disagrees with the
median is kept in the provenance table and the record is flagged `disputed`; a source that
could not be read is disclosed as `partial`; a reading whose sources are too old is
flagged `stale`.  A reading with no quorum is not published at all.

## Why this is not a thin wrapper

* **There is no LLM in the loop.**  No prompt, no model, no closed set to reduce.  The
  check is integer arithmetic over values that the validator computed itself from bytes
  it fetched itself.  Research found no need for a model (see `docs/CONSENSUS.md`).
* **It is not a format check.**  A validator that only inspected the leader's payload
  would prove that the leader formatted an answer, not that the answer is true.  Here the
  validator re-derives the number, the digest, the median, the agreeing set, the quorum
  and the status, and rejects anything that does not match.
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

### Read

| method | returns |
|:--|:--|
| `latest(feed_id)` | the record: value, flags, status, counts, timestamps and the per-source provenance |
| `value(feed_id)` | the scaled value only |
| `status(feed_id)` | `agreed`, `disputed`, `partial`, `stale`, or a combination |
| `is_trustworthy(feed_id, max_age_seconds)` | **the guard**: true only for an enabled feed with an agreed, complete, fresh reading |
| `feed(feed_id)` | the declared configuration |
| `feed_count()`, `source_count(feed_id)` | counts |

### A caller example

`contracts/example_consumer.py` is a complete consumer:

```python
import genlayer as gl
from genlayer.types import Address, u64, i64, u32
from genlayer.vm.public_abi import StorageView


def decide(trusted: bool, value: int, min_value: int) -> bool:
    return trusted and value >= min_value


class ExampleConsumer(gl.contract.Contract):
    feed_address: Address
    accepted: u32
    rejected: u32
    last_value: i64

    def __init__(self, feed_address: str):
        self.feed_address = Address(feed_address)

    @gl.public.write
    def check(self, feed_id: str, max_age: u64, min_value: i64):
        feed = gl.contract.get_at(self.feed_address)
        view = feed.view(state=StorageView.LATEST_FINALIZED)   # pin to the final state
        trusted = view.is_trustworthy(feed_id, max_age)
        value = view.value(feed_id)
        if decide(trusted, value, min_value):
            self.accepted += 1
            self.last_value = value
        else:
            self.rejected += 1
```

Two things a consumer has to get right, and both are in that snippet: pin the read to the
final state (a public RPC can serve a not yet final state right after a receipt), and gate
on the agreement status instead of using the number blindly.

## State design

```
owner: Address                          the curator (deployer)

feeds: TreeMap[str, Feed]                curator declared, immutable after creation
  Feed   { label, unit, tol_rel_bps, tol_abs, max_age_seconds, min_quorum,
           sources: DynArray[Source], created_at, disabled }
  Source { url, value_path, time_path, scale }

records: TreeMap[str, Record]            the last verified reading per feed
  Record   { value, flags, agree_count, sources_read, sources_declared,
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
  at sources it controls, declare a tolerance wide enough to accept anything, or declare
  a `time_path` that always looks fresh.  The contract cannot prevent that.  What the
  contract does guarantee is that the stored number was read by the committee from the
  declared sources, and that the committee agreed on it within the declared tolerance.
* **The committee is trusted to actually re-read.**  A validator that rubber-stamps the
  leader is outside this contract's control - that is GenLayer's base assumption and
  appeals are the network's answer to it.
* **A leader may omit a source it failed to read.**  The committee cannot tell that from
  a deliberate omission, so the disclosure is structural: an omitted source means
  `sources_read < sources_declared`, therefore the `PARTIAL` flag, therefore
  `is_trustworthy = false`.  A consumer that wants every source to have contributed
  checks `status == "agreed"`.  What the committee *does* catch is a leader that omits a
  source it could read and that disagrees with the aggregate: that is a hidden dissent and
  the proposal is rejected.
* **The clock inside the GenVM is pinned to the transaction timestamp.**  There is no
  block number, no block hash and no host wall clock in the contract context.  The stored
  `read_at` is the transaction timestamp, not the moment the HTTP request was served.
  Freshness therefore comes from a timestamp the *source* publishes, declared per source
  as `time_path`.
* **One trusted key is still one key.**  The curator is a single address.  A council of
  curators would be a natural extension and would not change the consensus mechanism.
* **A stale or disputed reading is published, not hidden.**  A consumer that ignores the
  status is using a number the committee did not fully vouch for.  That is why the status
  is a first-class field and why `is_trustworthy` exists.

## Network and deployment

| | |
|:--|:--|
| network | Studio development preview (release candidate) |
| canonical RPC | `https://studio-dev.genlayer.com/api` |
| chain ID | **61997** |
| deployed contract | `0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F` |
| source identity | `gen_getContractCode` read back, sha256 `a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796`, byte-identical to `contracts/corroborate.py` |
| live transcript | `docs/LIVE-RUN.md` |

The release-candidate environment is separate from the stable Studionet (chain 61999).
The tools used here are the matching release candidates: `genlayer-py 0.19.0rc2`,
`genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2`, `genlayer-js 2.0.0-rc.1`.

The contract pins its runner with `# { "Depends": "py-genlayer:latest" }`: an explicit runner
hash is rejected by this network unless the node knows that exact hash, and `latest` is the
tag that the tooling, the linter and the node all understand.

### What is verified live and what is only covered by the tests

| | |
|:--|:--|
| **live, with transaction hashes** | deploy, the deployed source matches the repository byte for byte, `register_feed`, `refresh` against three real public APIs, the published value and its per-source provenance, the `agreed` and `partial` statuses, the consumer guard refusing a partial reading, a non-curator refused (`rollback / not the curator`), malformed input refused (`rollback / invalid sources`), and no state written by a refused call |
| **unit tests only** | the validator rejecting a forged value, a forged digest, an inflated quorum, a forged status, a forged read time, a forged per source verdict, a dropped source and a hidden dissent; the committee rejecting a reading without a quorum; the injected-body case; the page-drift case |
| **not covered** | the consumer contract's cross-contract call - the direct test runner cannot execute one, so only the consumer's rule is unit tested.  No live validator disagreement was observed. |

`docs/LIVE-RUN.md` has the dated transcript with every transaction hash.

## Running the tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install genlayer-test==0.30.0rc2 genvm-linter==0.11.1rc2 pytest

# the GenVM bundle the runner needs (~310 MiB, verified against the release SHA256SUMS)
python scripts/fetch_genvm_bundle.py

# unit tests (direct mode: the contract runs in process, no network)
GENVM_VERSION=v0.6.0-rc10 pytest tests/test_corroborate.py -v

# the linter
GENVM_VERSION=v0.6.0-rc10 genvm-lint check contracts/corroborate.py
```

The unit tests use the GenLayer direct test runner.  Web responses are mocked, the leader
and the validator are executed separately, and the validator is replayed against honest and
tampered leader proposals.  `docs/LIVE-RUN.md` records what was additionally run against
the live network.

The live script needs `genlayer-py` and the keys in `secrets/keys.json`:

```bash
pip install genlayer-py==0.19.0rc2
python scripts/live_run.py
```

## Limits

* 2 to 8 sources per feed, 32 feeds per contract, 256 character urls, 8192 character
  source list, 32 KiB response bodies, values within +/- 2^62.
* Numeric feeds only: one scaled integer per source.  A structured value would need a
  per-field tolerance and quorum - an extension, not a redesign.
* A source that cannot be read does not vote.  A reading whose agreeing sources do not
  meet the quorum is not published.
* The direct test runner cannot execute a cross-contract call, so the consumer's rule is
  tested as a pure function; the cross-contract path is covered by the live run.

## Layout

```
contracts/corroborate.py       the contract
contracts/example_consumer.py  an example consumer
tests/test_corroborate.py      the unit tests
tests/helpers.py               test helpers
docs/CONSENSUS.md              how the consensus works, step by step
docs/LIVE-RUN.md               the dated transcript of the live run
scripts/live_run.py            deploy, fund, refresh, read back
state/DESIGN.md                the design, the red team and the feature freeze
state/MEMORY.md                the decision log
submission/contribution-text.md  the portal submission text
```

## License

MIT. See `LICENSE`.
