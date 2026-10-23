#!/usr/bin/env python3
"""Live run of the corroborate contract against the GenLayer Studio dev network.

Steps:
  1. create fresh throwaway keys (or load the gitignored file)
  2. fund them with the network's simulated funding method
  3. deploy contracts/corroborate.py
  4. read the deployed source back with gen_getContractCode and compare sha256
  5. register a complete feed and a feed with one unreachable source, refresh both
  6. read the interval, status and blocked/definite threshold answers with the read
     pinned to the final state
  7. deploy the example consumer, call its pinned `relation` view and its gated
     `check` write, and read the counters
  8. exercise the live refusals (non-curator, malformed registration)

Private keys are read from and written to secrets/keys.json (gitignored, mode 600).
They are never printed or written to the transcript; only addresses are.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet
from genlayer_py.types import TransactionHashVariant

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
KEYS_FILE = SECRETS / "keys.json"
CONTRACT = ROOT / "contracts" / "corroborate.py"
CONSUMER = ROOT / "contracts" / "example_consumer.py"
ARTIFACTS = ROOT / "transcripts"

ACCOUNT_NAMES = ["deployer", "alice", "bob"]
FUND_AMOUNT = 100 * 10**18

# The public sources used by the demo feed.  Three independent providers, three
# different JSON shapes, one declared extraction rule each.
FEED_SOURCES = [
    {
        "url": "https://open.er-api.com/v6/latest/USD",
        "value_path": "rates.EUR",
        "time_path": "time_last_update_unix",
        "scale": 100000,
    },
    {
        "url": "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR",
        "value_path": "rates.EUR",
        "time_path": "",
        "scale": 100000,
    },
    {
        "url": "https://api.fxratesapi.com/latest?base=USD&currencies=EUR",
        "value_path": "rates.EUR",
        "time_path": "timestamp",
        "scale": 100000,
    },
]

UNREACHABLE = {
    "url": "https://this-host-does-not-exist.invalid/price",
    "value_path": "rates.EUR",
    "time_path": "",
    "scale": 100000,
}


def log(line: str = "") -> None:
    print(line, flush=True)


def transcript_start(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def transcript(path: Path, line: str = "") -> None:
    with path.open("a") as fh:
        fh.write(line + "\n")


def say(path: Path, line: str = "") -> None:
    log(line)
    transcript(path, line)


# --------------------------------------------------------------------- keys


def create_keys() -> dict:
    SECRETS.mkdir(parents=True, exist_ok=True)
    accounts = {}
    for name in ACCOUNT_NAMES:
        acc = Account.create()
        accounts[name] = {"address": acc.address, "private_key": acc.key.hex()}
    # Create the file with mode 600 before any secret is written to it.
    fd = os.open(KEYS_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(accounts, fh, indent=2)
    os.chmod(KEYS_FILE, 0o600)
    return accounts


def load_or_create_keys(fresh: bool) -> dict:
    SECRETS.mkdir(parents=True, exist_ok=True)
    if fresh and KEYS_FILE.exists():
        KEYS_FILE.unlink()
    if KEYS_FILE.exists():
        data = json.loads(KEYS_FILE.read_text())
        os.chmod(KEYS_FILE, 0o600)
        return data
    return create_keys()


def accounts_from(keys: dict) -> dict:
    return {name: Account.from_key(value["private_key"]) for name, value in keys.items()}


# ------------------------------------------------------------------ helpers


def retry(fn, label, tries=5, delay=4):
    """The Studio RPC is reached over a flaky link; retry transport failures."""
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            log(f"  {label} attempt {attempt} failed: {type(exc).__name__}: {str(exc)[:160]}")
            time.sleep(delay)
    raise last


def wait(client, tx_hash, label, out: Path, seconds: int = 120):
    """Wait at most `seconds` for a decided transaction (the Studios can be slow)."""
    say(out, f"  waiting for {label} {tx_hash}")
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            tx = client.wait_for_transaction_receipt(tx_hash, wait_until="decided", retries=1)
            lifecycle = tx.get("lifecycle", {}) if isinstance(tx, dict) else {}
            state = lifecycle.get("state")
            if state in ("decided", "finalized"):
                say(
                    out,
                    "  "
                    + label
                    + " decided: "
                    + json.dumps(
                        {"lifecycle": lifecycle, "status": tx.get("status")}, default=str
                    )[:500],
                )
                return tx
        except Exception as exc:  # noqa: BLE001
            say(out, f"  {label} not decided yet ({type(exc).__name__})")
        time.sleep(3)
    say(out, f"  {label} did not reach a decision in time")
    return None


def outcome(client, tx_hash):
    """The execution outcome of a decided transaction: (status, payload)."""
    info = client.get_transaction(tx_hash)
    receipts = (info.get("consensus_data") or {}).get("leader_receipt") or []
    if not receipts:
        return ("unknown", "")
    result = receipts[0].get("result") or {}
    return (result.get("status"), result.get("payload"))


def fees_for(client, budget: int, message_fees: int = 0) -> dict:
    options = {"executionBudgetPerRound": budget}
    if message_fees:
        options["totalMessageFees"] = message_fees
    estimate = retry(
        lambda: client.estimate_transaction_fees(options=options),
        "fee estimate",
    )
    return {
        "distribution": estimate["distribution"],
        "feeValue": estimate["feeValue"],
    }


def write_call(
    client,
    address,
    function,
    args,
    account,
    label,
    out,
    budget=5 * 10**17,
    message_fees=0,
    wait_seconds=120,
):
    fees = fees_for(client, budget, message_fees)
    tx = retry(
        lambda: client.write_contract(
            address=address,
            function_name=function,
            account=account,
            args=args,
            fees=fees,
        ),
        label,
    )
    say(out, f"  {label} tx: {tx}")
    wait(client, tx, label, out, seconds=wait_seconds)
    status, payload = outcome(client, tx)
    say(out, f"  {label} outcome: {status} / {str(payload)[:400]}")
    return tx, status, payload


def read_final(client, address, function, args, out, label=None, tries=5):
    """Read with the state pinned to the latest final transaction."""
    result = retry(
        lambda: client.read_contract(
            address=address,
            function_name=function,
            args=args,
            transaction_hash_variant=TransactionHashVariant.LATEST_FINAL,
        ),
        label or function,
        tries=tries,
    )
    rendered = json.dumps(result, default=str)
    say(out, f"  {function}{tuple(args)} @ latest-final -> {rendered[:800]}")
    return result


def deployed_source(client, address) -> str:
    response = client.provider.make_request(
        method="gen_getContractCode", params=[address]
    )
    return response["result"]


def contract_address_from_tx(client, tx_hash):
    tx = client.get_transaction(tx_hash)
    data = tx.get("data") or {}
    address = data.get("contract_address")
    if address:
        return address
    return None


def decode_code(data: str) -> bytes:
    if data.startswith("0x"):
        data = data[2:]
    return base64.b64decode(data)


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="studio_devnet")
    parser.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    parser.add_argument(
        "--fresh-keys",
        action="store_true",
        help="delete the gitignored key file and create throwaway keys for this run",
    )
    args = parser.parse_args()

    out = ARTIFACTS / f"live-run-fix-{args.date}.txt"
    transcript_start(out)

    keys = load_or_create_keys(args.fresh_keys)
    accts = accounts_from(keys)

    say(out, f"corroborate live run (fix) - {args.date}")
    say(out, f"network: {args.network} rpc: {studio_devnet.rpc_urls['default']['http'][0]}")
    say(out, f"chain id: {studio_devnet.id}")
    say(out, "accounts (addresses only; keys stay in secrets/keys.json, mode 600):")
    for name, value in keys.items():
        say(out, f"  {name}: {value['address']}")

    client = create_client(chain=studio_devnet, account=accts["deployer"])

    # ------------------------------------------------------------- funding
    say(out, "")
    say(out, "step 1: fund the throwaway accounts with the simulated method")
    for name in ACCOUNT_NAMES:
        address = accts[name].address
        response = retry(
            lambda a=address: client.provider.make_request(
                method="sim_fundAccount", params=[a, FUND_AMOUNT]
            ),
            f"sim_fundAccount {name}",
        )
        say(out, f"  sim_fundAccount {address} -> {json.dumps(response)[:240]}")
    for name in ACCOUNT_NAMES:
        balance = retry(
            lambda a=accts[name].address: client.w3.eth.get_balance(a),
            f"balance {name}",
        )
        say(out, f"  balance {name}: {balance} wei")

    # --------------------------------------------------------------- deploy
    say(out, "")
    say(out, "step 2: deploy contracts/corroborate.py")
    code = CONTRACT.read_text()
    estimate = retry(lambda: client.estimate_transaction_fees(), "deploy fee estimate")
    say(out, f"  fee policy enabled: {estimate['policy']['enabled']}")
    say(out, f"  deploy fee estimate: {estimate['feeValue']}")
    deploy_tx = retry(
        lambda: client.deploy_contract(
            code=code,
            account=accts["deployer"],
            fees={
                "distribution": estimate["distribution"],
                "feeValue": estimate["feeValue"],
            },
        ),
        "deploy",
    )
    say(out, f"  deploy tx: {deploy_tx}")
    wait(client, deploy_tx, "deploy", out)
    address = retry(
        lambda: contract_address_from_tx(client, deploy_tx), "contract address"
    )
    if address is None:
        say(out, "  could not read the contract address from the receipt")
        return 1
    say(out, f"  contract address: {address}")

    # ------------------------------------------------------- source identity
    say(out, "")
    say(out, "step 3: read the deployed source back and compare sha256")
    remote = retry(lambda: deployed_source(client, address), "gen_getContractCode")
    remote_bytes = decode_code(remote)
    local_bytes = code.encode("utf-8")
    remote_sha = hashlib.sha256(remote_bytes).hexdigest()
    local_sha = hashlib.sha256(local_bytes).hexdigest()
    say(out, f"  local  sha256: {local_sha} ({len(local_bytes)} bytes)")
    say(out, f"  remote sha256: {remote_sha} ({len(remote_bytes)} bytes)")
    say(out, f"  identical: {remote_sha == local_sha and remote_bytes == local_bytes}")

    # ------------------------------------------------------- complete feed
    say(out, "")
    say(out, "step 4: register a complete feed and refresh it")
    sources_json = json.dumps(FEED_SOURCES)
    register_tx, register_status, _ = write_call(
        client,
        address,
        "register_feed",
        [
            "eur-usd",
            "Euro per US dollar",
            "EUR per USD",
            150,
            0,
            172800,
            2,
            sources_json,
        ],
        accts["deployer"],
        "register_feed",
        out,
    )
    refresh_tx, refresh_status, _ = write_call(
        client,
        address,
        "refresh",
        ["eur-usd"],
        accts["deployer"],
        "refresh",
        out,
        budget=10**18,
    )
    record = read_final(client, address, "latest", ["eur-usd"], out)
    trustworthy = read_final(
        client, address, "is_trustworthy", ["eur-usd", 3600], out
    )
    status = read_final(client, address, "status", ["eur-usd"], out)
    say(out, f"  complete feed: lo={record['lo']} hi={record['hi']} status={status} trustworthy={trustworthy}")

    if record["lo"] != record["hi"]:
        say(out, "  WARNING: the complete feed is not a point; not using it for the ACCEPT path")
    m = record["lo"]
    compare_above = read_final(client, address, "compare", ["eur-usd", m], out)
    compare_below = read_final(client, address, "compare", ["eur-usd", m + 1], out)
    say(out, f"  complete compare at median ({m}): {compare_above}")
    say(out, f"  complete compare at median+1 ({m + 1}): {compare_below}")

    # ------------------------------------------- a source that cannot be read
    say(out, "")
    say(out, "step 5: register a feed with one unreachable declared source")
    partial_sources = list(FEED_SOURCES) + [UNREACHABLE]
    partial_register_tx, _, _ = write_call(
        client,
        address,
        "register_feed",
        [
            "eur-usd-partial",
            "Euro per US dollar, one unreachable source",
            "EUR per USD",
            150,
            0,
            172800,
            2,
            json.dumps(partial_sources),
        ],
        accts["deployer"],
        "register partial feed",
        out,
    )
    partial_refresh_tx, _, _ = write_call(
        client,
        address,
        "refresh",
        ["eur-usd-partial"],
        accts["deployer"],
        "partial refresh",
        out,
        budget=10**18,
    )
    partial_record = read_final(
        client, address, "latest", ["eur-usd-partial"], out
    )
    partial_trust = read_final(
        client, address, "is_trustworthy", ["eur-usd-partial", 3600], out
    )
    partial_status = read_final(
        client, address, "status", ["eur-usd-partial"], out
    )
    say(
        out,
        f"  partial feed: lo={partial_record['lo']} hi={partial_record['hi']} "
        f"read={partial_record['sources_read']}/{partial_record['sources_declared']} "
        f"status={partial_status} trustworthy={partial_trust}",
    )
    p_lo = partial_record["lo"]
    p_hi = partial_record["hi"]
    p_mid = (p_lo + p_hi) // 2
    p_above = read_final(client, address, "compare", ["eur-usd-partial", p_lo], out)
    p_mid_relation = read_final(client, address, "compare", ["eur-usd-partial", p_mid], out)
    p_below = read_final(client, address, "compare", ["eur-usd-partial", p_hi + 1], out)
    say(out, f"  partial compare at lo ({p_lo}): {p_above}")
    say(out, f"  partial compare at mid ({p_mid}): {p_mid_relation}")
    say(out, f"  partial compare above hi ({p_hi + 1}): {p_below}")

    # ------------------------------------------------------------- consumer
    say(out, "")
    say(out, "step 6: deploy the example consumer and exercise the pinned path")
    consumer_code = CONSUMER.read_text()
    consumer_estimate = retry(
        lambda: client.estimate_transaction_fees(), "consumer deploy fee estimate"
    )
    consumer_tx = retry(
        lambda: client.deploy_contract(
            code=consumer_code,
            account=accts["deployer"],
            args=[address],
            fees={
                "distribution": consumer_estimate["distribution"],
                "feeValue": consumer_estimate["feeValue"],
            },
        ),
        "deploy consumer",
    )
    say(out, f"  consumer deploy tx: {consumer_tx}")
    wait(client, consumer_tx, "deploy consumer", out)
    consumer = retry(
        lambda: contract_address_from_tx(client, consumer_tx), "consumer address"
    )
    say(out, f"  consumer address: {consumer}")

    if consumer:
        # The nested view read is attempted once: on this release candidate a
        # cross-contract gen_call can exceed the gateway timeout (504).  The
        # feed-level compare with the same LATEST_FINAL pin above is the
        # consumer-facing threshold query and is the authoritative live result.
        try:
            rel_above = read_final(
                client, consumer, "relation", ["eur-usd", m], out, tries=1
            )
            rel_below = read_final(
                client, consumer, "relation", ["eur-usd", m + 1], out, tries=1
            )
            rel_partial = read_final(
                client, consumer, "relation", ["eur-usd-partial", p_mid], out, tries=1
            )
            say(out, f"  consumer relation (complete, {m}): {rel_above}")
            say(out, f"  consumer relation (complete, {m + 1}): {rel_below}")
            say(out, f"  consumer relation (partial, {p_mid}): {rel_partial}")
        except Exception as exc:  # noqa: BLE001
            say(
                out,
                "  consumer relation read did not complete: "
                f"{type(exc).__name__}: {str(exc)[:240]}",
            )

        # One gated check: the cross-contract call needs a message fee allocation
        # on this RC; the observed result (accepted or rollback) is recorded as is.
        try:
            write_call(
                client,
                consumer,
                "check",
                ["eur-usd", 3600, m],
                accts["deployer"],
                "consumer check above",
                out,
                budget=10**18,
                message_fees=10**18,
                wait_seconds=60,
            )
        except Exception as exc:  # noqa: BLE001
            say(
                out,
                "  consumer check submission failed: "
                f"{type(exc).__name__}: {str(exc)[:240]}",
            )
        counters = read_final(client, consumer, "counters", [], out)
        say(out, f"  consumer counters: {json.dumps(counters, default=str)}")

    # ------------------------------------------------------------ refusals
    say(out, "")
    say(out, "step 7: live refusals")
    feed_count_before = read_final(client, address, "feed_count", [], out)

    rogue_args = ["rogue", "rogue", "EUR", 150, 0, 172800, 2, sources_json]
    rogue_fees = fees_for(client, 5 * 10**17)
    try:
        rogue_tx = retry(
            lambda: client.write_contract(
                address=address,
                function_name="register_feed",
                account=accts["alice"],
                args=rogue_args,
                fees=rogue_fees,
            ),
            "non-curator register_feed",
        )
        say(out, f"  non-curator register_feed tx: {rogue_tx}")
        wait(client, rogue_tx, "non-curator register_feed", out)
        status, payload = outcome(client, rogue_tx)
        say(out, f"  non-curator register_feed outcome: {status} / {str(payload)[:300]}")
    except Exception as exc:  # noqa: BLE001
        say(out, f"  non-curator register_feed rejected at submission: {type(exc).__name__}: {str(exc)[:300]}")

    bad_args = ["bad", "bad", "EUR", 150, 0, 172800, 2, "not json"]
    bad_fees = fees_for(client, 5 * 10**17)
    try:
        bad_tx = retry(
            lambda: client.write_contract(
                address=address,
                function_name="register_feed",
                account=accts["deployer"],
                args=bad_args,
                fees=bad_fees,
            ),
            "malformed sources_json",
        )
        say(out, f"  malformed sources_json tx: {bad_tx}")
        wait(client, bad_tx, "malformed sources_json", out)
        status, payload = outcome(client, bad_tx)
        say(out, f"  malformed sources_json outcome: {status} / {str(payload)[:300]}")
    except Exception as exc:  # noqa: BLE001
        say(out, f"  malformed sources_json rejected at submission: {type(exc).__name__}: {str(exc)[:300]}")

    feed_count_after = read_final(client, address, "feed_count", [], out)
    say(out, f"  feed_count before refusals: {feed_count_before}")
    say(out, f"  feed_count after refusals: {feed_count_after}")

    say(out, "")
    say(out, "summary")
    for key, value in {
        "contract": address,
        "deploy_tx": deploy_tx,
        "register_tx": register_tx,
        "refresh_tx": refresh_tx,
        "partial_register_tx": partial_register_tx,
        "partial_refresh_tx": partial_refresh_tx,
        "consumer": consumer,
        "consumer_tx": consumer_tx,
    }.items():
        say(out, f"  {key}: {value}")
    say(out, f"transcript written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
