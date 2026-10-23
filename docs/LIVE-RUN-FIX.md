# Live run - fixed deployment

A dated transcript of the fixed contract on the **Studio development preview** (the
release-candidate environment).  Everything below was observed in one scripted run; the
raw log is `transcripts/live-run-fix-2026-10-23.txt` (gitignored).  Nothing is staged.

| | |
|:--|:--|
| date | 2026-10-23 |
| network | Studio development preview (release candidate) |
| canonical RPC | `https://studio-dev.genlayer.com/api` |
| chain ID | **61997** (`eth_chainId` = `0xf22d`) |
| tooling | `genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2` |
| target SDK | `genvm-manager v0.6.0-rc10` (`GENVM_VERSION=v0.6.0-rc10` for lint) |
| script | `scripts/live_run.py --date 2026-10-23` |
| deployed contract | `0x6D0Cd00A0d3eb9C86eEE81f22cb05e3B51E070Ed` |
| deploy transaction | `0x28a5527ac21fc50d9b77549f37b29953600d38a5b293f7ceb17d5872f6f671b3` |

The accounts are fresh throwaway keys generated for this fix cycle.  They live in
`secrets/keys.json` (git-ignored, mode 600); only their **addresses** appear here.

| account | address |
|:--|:--|
| deployer (curator) | `0xa094c862d484E038013e0f44f0E41D80b4C92dD4` |
| alice | `0xe5b0ab3D9F16598b1f236DC535458088b779aA8f` |
| bob | `0x7640a791c4F547806B510914A0feE89EeD34312F` |

---

## 1. Funding

The network's simulated funding method (`sim_fundAccount`, Studio only):

```
sim_fundAccount 0xa094c862d484E038013e0f44f0E41D80b4C92dD4 -> {"jsonrpc":"2.0","result":"0x605489ef82a54738cf8affa64a6d63c912a4a6962fdb8e5f2a4291ab805591bd","id":1792717522021}
sim_fundAccount 0xe5b0ab3D9F16598b1f236DC535458088b779aA8f -> {"jsonrpc":"2.0","result":"0xf153f21b68539b750d0f79d4456ae076e67b43c90634e61268e36f7dd7b4f9f3","id":1792717523939}
sim_fundAccount 0x7640a791c4F547806B510914A0feE89EeD34312F -> {"jsonrpc":"2.0","result":"0x45c96e417d67bc8c8162d10832c18e2d81f273685bd26a19c2a90d11d1b2d037","id":1792717524400}
balance deployer: 199999445937249993710 wei
balance alice: 200000000000000000000 wei
balance bob: 200000000000000000000 wei
```

The fee policy is enabled; the deploy fee estimate was `100000000000010352` GEN-wei.

## 2. Deploy and source identity

```
deploy tx: 0x28a5527ac21fc50d9b77549f37b29953600d38a5b293f7ceb17d5872f6f671b3
deploy decided: {"lifecycle": {"state": "decided", "outcome": "accepted"}}
contract address: 0x6D0Cd00A0d3eb9C86eEE81f22cb05e3B51E070Ed

local  sha256: e2bc11ed31ece8a747520a6824a51438283e532ba33fa9b3b42f9130468c6957 (34992 bytes)
remote sha256: e2bc11ed31ece8a747520a6824a51438283e532ba33fa9b3b42f9130468c6957 (34992 bytes)
identical: True
```

The remote value is the base64 returned by `gen_getContractCode`, decoded and hashed.
It is byte-identical to `contracts/corroborate.py` at commit `3b85a6f`.

## 3. A complete feed: one exact value

`eur-usd` declares three public providers, scale 100000, relative tolerance 150 bp,
staleness limit 172800 s, quorum 2.

| # | source | extraction rule | source timestamp |
|:--|:--|:--|:--|
| 0 | `https://open.er-api.com/v6/latest/USD` | `rates.EUR` | `time_last_update_unix` |
| 1 | `https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR` | `rates.EUR` | none published |
| 2 | `https://api.fxratesapi.com/latest?base=USD&currencies=EUR` | `rates.EUR` | `timestamp` |

```
register_feed tx: 0x6e542d58cf334bbc9f33e05a04aaae4de97e9b961a8ab4463ae6709ab8b11cdc
register_feed outcome: return
refresh tx: 0x2f1a101a3f063236ca4be1c1d73dbd9abf67424103badf9f49583c5f03cd90b5
refresh outcome: return
```

`latest("eur-usd")`, read with `transaction_hash_variant=LATEST_FINAL`:

```json
{
  "lo": 89153, "hi": 89153,
  "flags": 0, "status": "agreed",
  "agree_count": 3, "sources_read": 3, "sources_declared": 3,
  "quorum_required": 2, "read_at": 1792717557, "data_ts": 1792713751,
  "readings": [
    {"index": 0, "value": 89153, "digest": "bd034962605d568b2c539a12a3baba2940fe9b23b5d431d9c58bc408bd1a3f60", "data_ts": 1792713751, "agrees": true},
    {"index": 1, "value": 89246, "digest": "221007011f55e7abcec420220b6bd4f4522457f373dce8e16f3bf2d95e19b016", "data_ts": 0, "agrees": true},
    {"index": 2, "value": 89128, "digest": "05cc52500bb67569ff1b5624d5d4d0038d2ac29e10cf2b890ad755a3ab4ec0fb", "data_ts": 1792717560, "agrees": true}
  ]
}
```

`is_trustworthy("eur-usd", 3600)` -> `true`.  `status("eur-usd")` -> `"agreed"`.

A consumer's threshold queries, pinned to the final state:

```
compare('eur-usd', 89153) @ latest-final -> "ABOVE"
compare('eur-usd', 89154) @ latest-final -> "BELOW"
```

## 4. A feed with one unreachable source: an interval

A second feed declares the same three sources plus
`https://this-host-does-not-exist.invalid/price`.  The fourth source is declared but
can never be read by the leader or the validators.

```
register partial feed tx: 0x9ddd0057fd033cf5f4bb1dc32507e13f2c2bd6a651f9cb8ee88eba996bf0e154
partial refresh tx: 0x23eb8ebab81024dd4521a75fc79586fc768d096b3f20358464259bcccae193a0
```

`latest("eur-usd-partial")`:

```json
{
  "lo": 87793, "hi": 90513,
  "flags": 2, "status": "partial",
  "agree_count": 3, "sources_read": 3, "sources_declared": 4,
  "quorum_required": 2, "read_at": 1792717596, "data_ts": 1792713751,
  "readings": [
    {"index": 0, "value": 89153, "digest": "bd034962605d568b2c539a12a3baba2940fe9b23b5d431d9c58bc408bd1a3f60", "data_ts": 1792713751, "agrees": true},
    {"index": 1, "value": 89246, "digest": "221007011f55e7abcec420220b6bd4f4522457f373dce8e16f3bf2d95e19b016", "data_ts": 0, "agrees": true},
    {"index": 2, "value": 89128, "digest": "05cc52500bb67569ff1b5624d5d4d0038d2ac29e10cf2b890ad755a3ab4ec0fb", "data_ts": 1792717560, "agrees": true}
  ]
}
```

The band is exactly the tolerance bound: median `89153`, `M = 89246`,
`B = ceil(150 * 89246 / 9850) = 1360`, so `[87793, 90513]`.

`is_trustworthy("eur-usd-partial", 3600)` -> `false`.  `status` -> `"partial"`.

```
compare('eur-usd-partial', 87793) @ latest-final -> "ABOVE"
compare('eur-usd-partial', 89153) @ latest-final -> "INDETERMINATE"
compare('eur-usd-partial', 90514) @ latest-final -> "BELOW"
```

This is the explicit `INDETERMINATE` region: a threshold inside the band cannot be
decided from this reading, and the record is not trustworthy.

## 5. The consumer example: deployed; cross-contract call not completed

The example consumer was deployed with `eur-usd` as its feed:

```
consumer deploy tx: 0x191b58ea6b1da777303a604e7d230a3bd7eac74763939fb8580b6d015b3caf2b
consumer address: 0xE0F981ccea23B8Da0c9d52773b3E9403715FA09B
```

The consumer's `relation` view (a `gen_call` that itself calls the feed with
`StorageView.LATEST_FINALIZED`) did not complete: the gateway returned HTTP 504 after the
nested call timed out.  The gated `check` write was submitted:

```
consumer check above tx: 0x70b069650ac9665e94a83d8f37a98c1b829d3a8e93d9d37cd270551bc97e50c6
consumer check above outcome: rollback /
  Mode1MessageFeesRequireGenVMPerEmissionSupport: fee-bearing GenVM messages require a message allocation tree
counters() @ latest-final -> {"accepted": 0, "last_outcome": "UNDECIDED", "rejected": 0, "undecided": 0}
```

So the live consumer cross-contract path is **not** demonstrated on this RC.  The
consumer's decision rule (`decide`) is unit tested, and the feed-level threshold query a
consumer uses is the `compare` result in sections 3 and 4, read with the same
`LATEST_FINAL` pin.  The example remains a correct integration sketch, not live proof of
a cross-contract call.

## 6. Refusals

```
feed_count() @ latest-final -> 2

non-curator register_feed tx: 0x8c4fedf8c7c476e391bcd5801b67630cc45f251e08ca98e930fb3967a64acf06
non-curator register_feed outcome: rollback / not the curator

malformed sources_json tx: 0xaa5ba9e104c546753c7454960ba5682b22dbc8594fb0c2ab7ea0aa9e582e4c54
malformed sources_json outcome: rollback / invalid sources

feed_count() @ latest-final -> 2
```

Both refusals wrote nothing: the feed count is unchanged.

## 7. What this transcript does and does not show

| | |
|:--|:--|
| **observed live** | new deploy; deployed bytes = repository bytes (sha256 above); `register_feed`; `refresh` of a complete feed and of a feed with an unreachable source; `lo == hi` for the complete read; the exact `[87793, 90513]` band for the partial read; `compare` returning `ABOVE`, `BELOW`, `INDETERMINATE` with `LATEST_FINAL`; `is_trustworthy` true/false; non-curator and malformed-input refusals; no state written by either |
| **not observed live** | a validator disagreement (two nodes with different views); the example consumer's cross-contract read/write (504 and the message-allocation rollback above) |
| **covered by unit tests** | the validator rejecting a shifted aggregate, a shifted/narrowed/inverted band, a float band, a forged per-source value or digest, an inflated quorum, a forged status, a forged read time, a dropped source and a hidden dissent; the committee refusing a reading without a quorum; the injected-body and page-drift cases; the exhaustive/random interval invariant; the production of `lo == hi` and of the exact band |

## 8. Superseded deployment

The v1 deployment is **rejected** and is not used by this document: address
`0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F`, sha256
`a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796`, deploy tx
`0x769efbd55e18e256468de8e88bf25f598a766525be730bb3ff3450c2f2c76ee8`.  Its dated
transcript is `docs/LIVE-RUN.md`; the diagnosis is `state/REVIEW-FIX.md`, the corrected
design is `state/DESIGN-fix.md`, and the mapping from review point to fix is
`docs/CHANGES-SINCE-REJECTION.md`.
