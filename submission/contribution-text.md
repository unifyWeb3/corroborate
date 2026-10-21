# Contribution text

Two versions: the detailed one and the short one (under 1000 characters, all lowercase).
The short version is also in `short-version.txt`, byte for byte, so it can be copied
without touching the file.

---

## Detailed version

**corroborate - a tolerance-consensus data feed with provenance**

A standalone GenLayer Intelligent Contract that lets other contracts read a number from the
public web and get back the evidence that the number was corroborated.

**The problem.** A contract that needs an external number has no way to know whether that
number is real, current, or corroborated. One HTTP read is one node, at one moment. If the
contract stores it, it stores an unverified claim that later consumers have to take on
faith. The usual answers are a trusted feed operator (a single point of failure) or a
language model summarising a page (a different point of failure, and not a number).

**What this contract does.** A curator declares a feed: two to eight public HTTPS sources,
a JSON path per source (the extraction rule), a scale, a relative and an absolute
tolerance, a staleness limit and a quorum. Any caller can then call `refresh(feed_id)`. The
leader fetches every source, derives one scaled integer per source and aggregates them with
the median. Every validator re-fetches every source itself, re-derives the whole reading,
and accepts the leader's proposal only when every per-source value is inside the declared
tolerance of the validator's own value, every per-source digest of the reading equals the
digest the validator computed itself, the aggregate equals the validator's own re-derived
median inside the tolerance, and the quorum, the flags and the read time are exactly what
the validator computes. Only then is anything written: the value plus its provenance - the
per-source values, the keccak256 digest of every reading, the source's own data timestamp
and the read time.

**How consensus is used.** Numeric tolerance equivalence, and nothing else. There is no
language model in the loop: the check is integer arithmetic over values the validator
computed itself from bytes it fetched itself. It is also not a format check. A validator
that only inspected the leader's payload would prove that the leader formatted an answer,
not that the answer is true; here the validator re-derives the number, the digest, the
median, the agreeing set, the quorum and the status, and rejects anything that does not
match. The comparison rule is exact integer arithmetic:

    diff = |a - b|
    within(a, b) = diff <= tol_abs  OR  diff * 10000 <= tol_rel_bps * max(|a|, |b|)

**Disagreement is a result, not an error.** A source that disagrees with the median stays
in the provenance table and the record is flagged `disputed`. A source that could not be
read does not vote, the record is flagged `partial` and the gap is disclosed
(`sources_read < sources_declared`). A reading whose sources are too old is flagged
`stale`. A reading with no quorum is not published at all: the proposal is rejected, the
leader is rotated, and if nobody satisfies the committee the transaction ends undetermined
and no state is written. The consumer side is a small read interface - `latest`, `value`,
`status` and `is_trustworthy(feed_id, max_age)` - and `is_trustworthy` is the guard a
consuming contract should use, with a pinned final-state read so it never acts on a state
that can still change.

**Trust, stated plainly.** The curator is trusted for the configuration: it can point a feed
at sources it controls or declare a tolerance wide enough to accept anything. The committee
is trusted to actually re-read. Two stored fields are administrative rather than
consensus-derived (`owner` and the per-feed `disabled` flag); every field that describes an
external reading is re-derived by the committee. A leader may omit a source it failed to
read, and that is disclosed structurally as `partial`; a leader that omits a source it *did*
read and which disagrees with the aggregate is rejected. Inside the GenVM the clock is
pinned to the transaction timestamp, so freshness comes from a timestamp the source itself
publishes, declared per source. One trusted key is still one key.

**Network and address.** Studio development preview (release candidate), chain ID 61997,
canonical RPC `https://studio-dev.genlayer.com/api`. Deployed contract
`0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F`. The deployed source was read back with
`gen_getContractCode` and is byte-for-byte the file in the repository (sha256
`a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796`).

**Repository.** https://github.com/unifyWeb3/corroborate - `contracts/corroborate.py`,
`contracts/example_consumer.py`, `tests/test_corroborate.py` (78 tests, all passing, every
refusal with a positive control), `docs/CONSENSUS.md` (the step-by-step walkthrough for
another builder), `docs/LIVE-RUN.md` (the dated transcript with every transaction hash) and
`scripts/live_run.py` (the deploy, fund, refresh and read-back script). MIT licensed, no
frontend.

**What was verified live.** The deploy, the byte-identical source read-back, a feed
registration, a refresh against three real public FX providers, the published median with
its per-source provenance, the `agreed` and `partial` statuses, the consumer guard refusing a
partial reading, a non-curator refused (`rollback / not the curator`), malformed input
refused (`rollback / invalid sources`) and no state written by a refused call. The
rejection path - the committee rejecting a forged value, digest, quorum, status, read time
or a hidden dissent - is covered by the unit tests, which replay the validator against
tampered leader proposals; no validator disagreement was observed live. The consumer
contract's cross-contract call is not covered by an integration test, because the direct
test runner cannot execute one.

**Limits.** Numeric feeds only, one scaled integer per source. A structured value would
need a per-field tolerance and quorum. 2 to 8 sources per feed, 32 feeds per contract, 32
KiB response bodies, values within +/- 2^62. The feed configuration is curator-declared and
immutable. A boundary case, where a source sits exactly on the tolerance edge, can make the
committee reject a correct reading; the result is a rotation, not a wrong number.

---

## Short version (994 characters, lowercase)

```
corroborate lets other genlayer contracts read a web number, with the evidence that it was corroborated.

a curator declares a feed: 2 to 8 https sources, a json path each, a tolerance, a quorum. the leader fetches them all and takes the median. every validator re-fetches them all, re-derives the reading and accepts it only if each value is inside the tolerance of its own value, each digest matches, the aggregate matches its own median, and the quorum matches. only then are the value and provenance written.

no language model: the check is integer arithmetic over independently read values. disagreement is a result, not an error: a dissenting source is kept and flagged, an unreadable one flagged partial, no quorum means no publication.

deployed on studio dev preview, chain id 61997, at 0x81ffed2e3b5558e67d6dcea79b5cead6e247d68f; the source reads back byte for byte as the repo.

78 unit tests pass, each refusal has a positive control. repo: https://github.com/unifyWeb3/corroborate
```

The only capital letter in it is the `W` in the repository URL.
