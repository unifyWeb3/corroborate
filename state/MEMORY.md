# MEMORY - Contract Brief A (tolerance-consensus data feed with provenance)

Decision log. Appended after every stage. Resumable: read this file first.

Repo root: `/root/primitive-a` (git repo, branch `main`, remote will be `unifyWeb3/<name>`).
Working dirs kept out of git: `.rcvenv/` (RC python tooling), `.rcnode/` (RC node tooling),
`.research/` (docs snapshots).

---

## STAGE 0 - inventory, research, network probe

### 0.1 Machine / tooling inventory [VERIFIED]

| item | result |
|:--|:--|
| OS | Linux 6.17.0-PRoot-Distro aarch64, Proot userland on Android |
| disk | 224G total, 90G free (60% used) |
| node | v22.22.1 |
| npm | 9.2.0 |
| git | 2.53.0 |
| gh | 2.46.0, **authenticated** as `unifyWeb3`, scopes: gist, read:org, repo, workflow |
| python3 | 3.14.4 (system) - too new for the RC SDK; **uv-managed CPython 3.12.15** used instead |
| uv | 0.13.1 at `/root/.local/bin/uv` |
| jq | MISSING (not needed) |

### 0.2 RC tooling installed in a SEPARATE venv/dir [VERIFIED]

* `/root/primitive-a/.rcvenv` (uv venv, CPython 3.12.15)
  * `genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0rc2`, `genvm-linter 0.11.1rc2`, `pytest 9.1.1`, `web3 8.0.0`
  * binaries: `gltest`, `genvm-lint`
* `/root/primitive-a/.rcnode` (npm dir): `genlayer-js 2.0.0-rc.1`
* Nothing was installed into system python or into the sibling project venvs.
* Both new dirs are in `.gitignore`.

### 0.3 Network probe [VERIFIED by live RPC calls]

| endpoint | `eth_chainId` | `net_version` | result |
|:--|:--|:--|:--|
| `https://studio-next.genlayer.com/api` | `0xf22d` | - | reachable |
| `https://studio-next.genlayer.com/api/rpc` | `0xf22d` | - | reachable |
| `https://studio-dev.genlayer.com/api` | `0xf22d` | - | reachable, **canonical** |
| `https://studio-dev.genlayer.com/api/rpc` | `0xf22d` | - | reachable |
| `https://studio.genlayer.com/api` | `0xf22f` | `61999` | reachable (Studionet fallback) |

* `0xf22d` = **61997** = the chain ID announced for the release-candidate network. Confirmed
  independently by live RPC, not taken from the brief.
* Official docs (`https://docs.genlayer.com/developers/networks`, last_updated 2026-06-11) name the
  RC environment **"Studio development preview"**: web app `studio-dev.genlayer.com`, canonical RPC
  `https://studio-dev.genlayer.com/api`, **chain ID 61997**, and state explicitly that
  *"`studio-next.genlayer.com` may be enabled as a browser alias for this same environment;
  integrations should still use the canonical `studio-dev.genlayer.com/api` RPC."*
  -> Decision: use `studio-dev.genlayer.com/api` (canonical). `studio-next` verified as the same
  chain id, kept only as a fallback alias. Recorded here, not hidden.
* `genlayer-py 0.19.0rc2` ships a `studio_devnet` preset: id 61997, rpc
  `https://studio-dev.genlayer.com/api`. `genlayer-js 2.0.0-rc.1` ships a `studioDevnet` preset
  with the same id/url (verified by grepping the installed dist).
* Fallback if studio-dev proves unusable: **Studionet, chain 61999** (`studio.genlayer.com/api`).
  Nothing has needed the fallback so far.
* Fee model on 61997: `sim_getFeeConfig` returns `"enabled": true` -> **fee-charging deployment**.
  Deploys and writes must carry a `FeesDistribution` + fee value (genlayer-py 0.19 exposes
  `estimate_transaction_fees` / `estimate_transaction_fees_for_write`).
* Accounts: `sim_fundAccount` (flat positional params `[address, amount]`), Studio-only. The RPC
  endpoint used by `genlayer-py` is a *node* RPC; the chain RPC (`rpc.testnet-chain.genlayer.com`,
  chain 4221) is only for EVM ops.
* `gen_getContractCode` **exists** on 61997 (returns "Contract ... not found" for an unknown
  address) -> usable for the byte-for-byte source proof.
* `sim_getConsensusContract` works with `params:["ConsensusMain"]` -> address
  `0xb7278A61aa25c888815aFC32Ad3cC52fF24fE575` (same as the SDK preset).

### 0.4 What the GenVM/SDK actually does (read from installed sources, not memory)

Sources read: `genlayer_py` 0.19.0rc2 (`client/genlayer_client.py`, `contracts/actions.py`,
`accounts/actions.py`, `transactions/fees.py`, `chains/`), `genvm_linter` 0.11.1rc2
(`lint/safety.py`, `lint/structure.py`), `gltest` 0.30.0rc2 (`direct/sdk_loader.py`,
`direct/pytest_plugin.py`), and the extracted SDK tree
`~/.cache/gltest-direct/extracted/v0.2.16/py-lib-genlayer-std/.../genlayer` (gl/, py/keccak.py).

Confirmed:

1. **Non-determinism** must live inside a non-deterministic block. `gl.nondet.*` outside such a
   block is linter error **E010**. Storage writes, `gl.get_contract_at()` calls and `.emit()`
   inside a nondet block are linter errors too. -> leader/validator functions may only fetch and
   compute; every state write happens after `gl.vm.run_nondet_unsafe` returns.
2. **Equivalence**: `gl.vm.run_nondet_unsafe(leader_fn, validator_fn)` is the recommended pattern;
   `gl.eq_principle.strict_eq` is exact-match only. Docs are explicit that a validator which only
   inspects the leader's payload shape/format "is not performing consensus" - the validator must
   re-derive the value from independent evidence. -> this is exactly the check this contract needs.
3. **Web access**: `gl.nondet.web.get(url)` / `gl.nondet.web.request(url, method=...)` return a
   `Response` with fields `status`, `headers`, `body` (bytes) in the SDK tree read above.
4. **Time**: `datetime.now()` / `time.time()` inside the GenVM are pinned to the **transaction
   timestamp** and are identical for leader and validators. There is no block number, no block
   hash, no host wall clock. -> a "read time" can only be stored as the transaction timestamp;
   per-source freshness must come from a timestamp **inside the source document**. This is a
   hard constraint on the design and is documented as such.
5. **Storage**: `DynArray[T]`, `TreeMap[K,V]`, sized ints (`u32`, `i64`, `u256`), `bigint`, `str`,
   `bytes`, `bool`; `list`/`dict`/`int` are forbidden storage types (linter). `@allow_storage`
   dataclasses allowed. `gl.storage.copy_to_memory()` to read storage inside a nondet block.
6. **Hashing**: `hashlib` is not part of the sandbox stdlib, but `genlayer.py.keccak` ships a
   pure-python `Keccak256` with a hashlib-compatible interface (`update`/`hexdigest`). -> use
   keccak256 for on-chain digests; sha256 is used off-chain for the deployed-source proof.
7. **Testing**: `gltest` direct mode runs the contract in-process; fixtures `direct_vm`,
   `direct_deploy`, `direct_alice`...; cheatcodes `mock_web`, `mock_llm`, `prank`,
   `expect_revert`, `snapshot`/`revert`; `direct_vm.run_validator()` runs the captured validator
   function against the last leader result and returns True/False.
8. **Cross-contract calls**: `gl.get_contract_at(addr)` is deterministic-only; a consumer contract
   reads the feed with a normal view call. There is no block number in context, so a consumer
   cannot pin "a block"; finality is observed through the SDK receipt status.

### 0.5 SDK release resolution (for the linter / direct mode)

`gltest`/`genvm-lint` fetch the GenVM bundle from `genlayerlabs/genvm-manager` releases.
Latest published release seen: **v0.6.0-rc10 (2026-10-16)** and it *does* publish
`genvm-universal.tar.xz` (plus arch-specific archives). The loader prefers the newest **stable**
release that has a bundle asset, otherwise the newest prerelease. Stage 2 will record the exact
outcome (documented command vs `GENVMROOT`).

### 0.6 Name decision

Shortlist checked for GitHub availability under `unifyWeb3` and for clashes with well-known
projects (see Stage 1 for the final pick). Rejected options and reasons are recorded in
`state/DESIGN.md`.

### 0.7 Stage 0 status

**DONE.** No human action needed so far. Next: Stage 1 design.

---

## STAGE 1 - design

* `state/DESIGN.md` written before any code: purpose, roles and trust model, the full state
  design (every field, who may write it, what the committee verified), the interface with a
  caller example, the consensus design with the exact tolerance formula, the error table and
  an 18 case red team analysis.
* Name: **`corroborate`**.  Shortlist checked for availability under `unifyWeb3`:
  one candidate was already taken by another project in the same account (rejected, and not
  named here), `ratify` is a well known security project (rejected), `epsilon` is a known
  crypto network (rejected), `barometer`/`gauge`/`multiplex`/`quorum` are overused or too
  generic (rejected), `plumb`, `probe`, `witness`, `tally`, `accord`, `corroborate` were
  free.  `corroborate` was kept because it names the mechanism exactly: independent
  corroboration of one reading.
* Feature freeze declared at the end of the design; corrections are recorded in
  `state/DESIGN.md` section 11.
* The ecosystem survey subagent was cancelled by the environment before it reported; the
  survey was therefore done by reading the official documentation and the installed SDK
  sources instead (see 0.4).  Nothing in the ecosystem was copied, and no other project or
  author is named anywhere in this repository.

## STAGE 2 - build

* `contracts/corroborate.py` - the contract.  `contracts/example_consumer.py` - an example
  consumer that pins its read to the final state and gates on `is_trustworthy`.
* `tests/test_corroborate.py` - 78 tests, all passing, each refusal with a positive control.
* The contract was **ported to the v0.6.0-rc10 SDK** after the first version failed to load:
  `import genlayer as gl`, `gl.contract.Contract`, `gl.storage.DynArray/TreeMap`,
  `gl.vm.run_nondet`, `gl.nondet.web.get`, `gl.contract.get_at`,
  `StorageView.LATEST_FINALIZED`.
* The release candidate linter still looks for the decorator name `allow_storage`, so
  `genlayer.storage.allow` is imported under that name (same decorator, one line of
  explanation in the contract).
* The runner is pinned with `# { "Depends": "py-genlayer:latest" }`: an explicit hash is
  rejected by the node with `invalid_contract runner malformed` unless the node knows it.
* SDK bundle: the documented `genvm-lint check` command tried `genvm-runners-all.tar.xz`
  first (404 in v0.6.0-rc10) and then `genvm-universal.tar.xz` (310.5 MiB, present).  The
  download was resumed with a Range request and verified against the release's published
  `SHA256SUMS` (`d3c6ba36...cf801af`); the same verified file was placed in the linter's
  cache so the linter and the direct runner share one artifact.  No cache was hand seeded
  with anything but the official, checksum verified bundle.

## STAGE 3 - self review

Attacked the diff as an adversary; every finding was fixed with a test:

| finding | fix |
|:--|:--|
| the per source `agrees` verdict was stored but not re-checked by the validator | the validator now recomputes it and rejects a mismatch (test: `test_the_validator_rejects_a_forged_per_source_verdict`) |
| a leader could omit a source it read and thereby hide a dissent | the validator now rejects a proposal that leaves out a source it could read whose value disagrees with the aggregate (test: `test_the_validator_rejects_a_leader_that_hid_a_dissent`), and still accepts an omitted source that agrees (test: `test_the_validator_accepts_a_leader_that_dropped_an_agreeing_source`) |
| a body digest could not be verified when the page changes between reads | the digest now covers the reading (`keccak256("<index>:<value>")`), which both sides compute independently |
| a stale source was excluded from the aggregate, which made the voting set depend on a freshness decision | stale sources vote and the record is flagged `STALE` |
| `gl.vm.get_timestamp()` raises on the network | the contract uses the wired standard library clock |
| no test covered the value extraction edge cases | tests for arrays, floats, booleans, non numeric strings, out of range values and oversize bodies |
| the quorum refusal test had no positive control | it now also shows a reading with a met quorum being accepted |

## STAGE 4 - live run

* Network: **studio-dev** (chain 61997), canonical RPC `https://studio-dev.genlayer.com/api`.
* Keys: three throwaway accounts in `secrets/keys.json` (git ignored, mode 600, addresses
  only in the docs).  Funded with `sim_fundAccount`; no human step was needed.
* Deployed: `0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F`, tx
  `0x769efbd55e18e256468de8e88bf25f598a766525be730bb3ff3450c2f2c76ee8`.
* Source read back with `gen_getContractCode`: sha256
  `a9081ddfc22aafe1055c043d815be785fc5bddfc3fe94cf91986c3948aaf3796` on both sides, 29983
  bytes, byte identical.
* `register_feed` tx `0x311925540feea6c654c60524c68d6abed6978a84b5ad170839130d2860301bc5`,
  `refresh` tx `0xd6b9b9129c00982c27dae03ee09bb7d677dcbb34086c814fcd71147aecbe6aaf` against
  three real public FX providers: value 89015, all three agree, status `agreed`,
  `is_trustworthy` true.
* A second feed with a fourth, unreachable source produced the `partial` status and
  `is_trustworthy = false` in the same run.
* Refusals: a non-curator `register_feed` rolled back with `not the curator`, a malformed
  source list rolled back with `invalid sources`, and `feed_count` showed only the two
  curator registered feeds.
* **No validator disagreement was observed live.**  The rejection path is covered by the unit
  tests only, and `docs/LIVE-RUN.md` says so.

## STAGE 5 - repository

* Pre-push sweep (`scripts/pre_push_sweep.py`): no key file tracked, every 64 hex candidate
  in the working tree and in every ref compared against the three real private keys (no
  match), no endpoint secret, no other project's name.  The forbidden name list is read
  from a file outside the repository so that the repository never names another project.
* Public repository created: **https://github.com/unifyWeb3/corroborate** (default branch
  `main`), MIT licence, no frontend, no secrets.
* `README.md`, `docs/CONSENSUS.md`, `docs/LIVE-RUN.md`, `state/DESIGN.md`,
  `state/MEMORY.md`, `submission/contribution-text.md` (a detailed version and a 994
  character lowercase version) are in the repository.
* The submission text was **not** submitted anywhere; it is in the repository for the user
  to paste.
