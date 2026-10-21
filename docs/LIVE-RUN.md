# Live run

A dated transcript of the contract running on the **Studio development preview** (the
release-candidate environment).  Everything below was observed, not staged.

| | |
|:--|:--|
| date | 2026-10-21 |
| network | Studio development preview (release candidate) |
| canonical RPC | `https://studio-dev.genlayer.com/api` (`studio-next.genlayer.com` serves the same chain) |
| chain ID | **61997** (`eth_chainId` = `0xf22d`) |
| tooling | `genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2` |
| script | `scripts/live_run.py` (raw transcript: `artifacts/live-run-2026-10-21-final.txt`) |
| deployed contract | `0xAb8eeAE77c55dFDFA1cdD945A5100bae3CaAa117` |

The accounts are throwaway keys generated for this run.  They live in `secrets/keys.json`
(git-ignored, mode 600) and only their **addresses** are printed anywhere.

| account | address |
|:--|:--|
| deployer (curator) | `0xF3778E4baF22B54127D48CB23491f7F7FE0E29F4` |
| alice | `0x11Dc1eEb36aEec3B1F4Cda90565d864765EE4805` |
| bob | `0xA8Fc692eC11F2Fe0749235D3c334C685e93845b0` |

---

## 1. Funding

The network's simulated funding method (`sim_fundAccount`, Studio only).  No human step was
needed.

```
sim_fundAccount 0xF3778E4baF22B54127D48CB23491f7F7FE0E29F4 -> {"result": "0x1e0c1a00040eff18b838e55e055bf319989cf249c16cb0447c1344ee30b61e74"}
sim_fundAccount 0x11Dc1eEb36aEec3B1F4Cda90565d864765EE4805 -> {"result": "0x1dbd9278e57d87e6fc076d707909253fbff2420457f6fd88e0c550dea38ed838"}
sim_fundAccount 0xA8Fc692eC11F2Fe0749235D3c334C685e93845b0 -> {"result": "0x91ab7f68048179c95455a44a4831110a5955fd5afb737f5737bbfd978ba08efb"}
```

Balances afterwards: deployer 999998502018999984363 wei, alice 999999842743999998354 wei,
bob 1000000000000000000000 wei.

The fee policy is enabled on this network (`sim_getFeeConfig` -> `"enabled": true`), so every
deploy and write carries a `FeesDistribution` and its fee value.  The estimate for a
deploy was `100000000000010352` GEN-wei.

## 2. Deploy

```
deploy tx: 0xfc6ae8665944b22153bc1b365e56d9d1acc2b14c35f1a742e3f1f6e8836855a6
deploy decided: {"lifecycle": {"state": "decided", "outcome": "accepted"}}
contract address: 0xAb8eeAE77c55dFDFA1cdD945A5100bae3CaAa117
```

## 3. The deployed source is the source in this repository

The code was read back from the node with `gen_getContractCode` (base64) and compared with
the file in the repository:

```
local  sha256: a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796 (29983 bytes)
remote sha256: a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796 (29983 bytes)
identical: True
```

`sha256sum contracts/corroborate.py` in this repository still prints
`a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796`.

## 4. Register a feed and refresh it

The feed is `eur-usd`, three independent public providers, scale 100000 (five decimals of
a rate), a relative tolerance of 150 bp, a staleness limit of 172800 s and a quorum of 2.

| # | source | extraction rule | source timestamp |
|:--|:--|:--|:--|
| 0 | `https://open.er-api.com/v6/latest/USD` | `rates.EUR` | `time_last_update_unix` |
| 1 | `https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR` | `rates.EUR` | none published |
| 2 | `https://api.fxratesapi.com/latest?base=USD&currencies=EUR` | `rates.EUR` | `timestamp` |

```
register_feed tx: 0xdb09ce622c6583cb8878a190597f86db79b3e3868d528d7782f36b56262ccd62
register_feed decided: {"lifecycle": {"state": "decided", "outcome": "accepted"}}

refresh tx: 0xeaf6f8cedffebeedde0cc1c3c696a2050cd156a36734664b02486779ea6d5164
refresh decided: {"lifecycle": {"state": "decided", "outcome": "accepted"}}
```

`latest("eur-usd")` right after the refresh:

```json
{
  "value": 89015,
  "flags": 0,
  "status": "agreed",
  "agree_count": 3,
  "sources_read": 3,
  "sources_declared": 3,
  "quorum_required": 2,
  "read_at": 1792603629,
  "data_ts": 1792540952,
  "age_seconds": 15,
  "readings": [
    {"index": 0, "value": 89075, "digest": "07eddc3d4767b7d3ea0a33279c40abe5de7a8062283a1f3a25817878d64a318d", "data_ts": 1792540952, "agrees": true},
    {"index": 1, "value": 89015, "digest": "c0d38ffdb4959b44b4f5603b3231ef201b3cebc8407260ec2d4ce1d696049eb6", "data_ts": 0, "agrees": true},
    {"index": 2, "value": 89006, "digest": "718cc6a6798b699c30e5155febe2d16e1cd342ea32007575fb6f6b5de863e199", "data_ts": 1792603620, "agrees": true}
  ]
}
```

Three providers, three different JSON shapes, one declared extraction rule each.  The
published value `89015` is the median of the three (0.89015 EUR per USD); the spread is
69 units, 0.077 %, inside the declared 150 bp tolerance, so all three agree.

```
is_trustworthy("eur-usd", 3600): True
status("eur-usd"): agreed
```

## 5. A source that cannot be read is a result, not an error

An earlier run of the same script (`artifacts/live-run-2026-10-21h.txt`) used a third
provider that the node could not reach.  The refresh still succeeded, the record carried the
gap, and the guard refused it:

```json
{
  "value": 89015,
  "flags": 2,
  "status": "partial",
  "agree_count": 2,
  "sources_read": 2,
  "sources_declared": 3,
  "readings": [
    {"index": 0, "value": 89075, "digest": "07eddc...", "data_ts": 1792540952, "agrees": true},
    {"index": 2, "value": 89015, "digest": "432b6f...", "data_ts": 0, "agrees": true}
  ]
}
is_trustworthy("eur-usd", 3600): False
status("eur-usd"): partial
```

The unreachable source simply did not vote, the two that answered did, and the consumer
guard said no.

## 6. Live refusals

Both refusals were executed on the network and both rolled back with the expected message.

A non-curator trying to register a feed:

```
non-curator register_feed tx: 0x7b8120b6a3d4eaf49e95f80960ad75e51a32450bc6f0caa36d44d37035fbe02f
non-curator register_feed outcome: rollback / not the curator
non-curator register_feed REFUSED (expected)
```

Malformed input (a source list that is not JSON):

```
malformed sources_json tx: 0x822287ccbb1a4c18fb720f0b5cd6903a3e3d9eb1fa2896b94938c2af46c08a84
malformed sources_json outcome: rollback / invalid sources
malformed sources_json REFUSED (expected)
```

And the state after both refusals:

```
feed_count: 1
```

The rogue feed was not created: a refused call writes nothing.

---

## What is verified live, and what is only covered by the unit tests

| claim | how it was checked |
|:--|:--|
| the contract deploys and runs on chain 61997 | live deploy, tx above |
| the deployed code is byte-for-byte the code in this repository | `gen_getContractCode` + sha256, above |
| a feed can be registered by the curator | live `register_feed` tx |
| a refresh fetches real public sources and publishes the median | live `refresh` tx, `latest` output above |
| the published value carries per-source provenance (value, digest, source timestamp) | live `latest` output above |
| the agreement status is a first-class result | live `status` = `agreed`, and a live `partial` |
| the consumer guard refuses a partial reading | live `is_trustworthy` = False in the partial run |
| a non-curator is refused | live rollback `not the curator` |
| malformed input is refused and writes nothing | live rollback `invalid sources`, `feed_count` unchanged |
| the committee re-derives the value and rejects a forged one | **unit tests only** - the validator is replayed against tampered leader proposals in `tests/test_corroborate.py`; no live disagreement was produced |
| a leader that omits a source it read, or hides a dissent | **unit tests only** |
| the consumer contract's cross-contract call | **not covered** - the direct test runner cannot execute a cross-contract call; only the consumer's rule (`decide`) is unit tested |

No validator disagreement was observed live.  The live run demonstrates the happy path and
the refusals, not the rejection path.

## Notes on this environment

* The Studio RPC on this release candidate does not answer `sim_call`, so the fee estimate
  for the writes is built from the network fee policy with a raised execution budget
  instead of from a simulation.  The deploy estimate comes from the same policy path.
* The contract pins its runner with `# { "Depends": "py-genlayer:latest" }`.  An explicit
  runner hash is rejected by this network with `invalid_contract runner malformed` unless
  the node knows that exact hash; `latest` is the tag the tooling and the node both
  understand.
* `gl.vm.get_timestamp()` raises on this network (`exit_code 1` on every write that
  called it), so the contract uses the standard library clock, which the GenVM wires to
  the transaction datetime.
* The network link is flaky from this machine (intermittent DNS failures and TLS resets).
  The script retries transport failures; the transcript above is the successful run.
