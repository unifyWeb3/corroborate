#!/usr/bin/env python3
"""Live run of the corroborate contract against a GenLayer Studio network.

Steps:
  1. create (or load) throwaway testnet keys
  2. fund them with the network's simulated funding method
  3. deploy contracts/corroborate.py
  4. read the deployed source back with gen_getContractCode and compare sha256
  5. register a feed, refresh it, read the record
  6. exercise the refusals that can be exercised live

Private keys are read from and written to secrets/keys.json (gitignored, chmod 600).
They are never printed.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
KEYS_FILE = SECRETS / "keys.json"
CONTRACT = ROOT / "contracts" / "corroborate.py"
ARTIFACTS = ROOT / "artifacts"

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
        "url": "https://api.exchangerate-api.com/v4/latest/USD",
        "value_path": "rates.EUR",
        "time_path": "time_last_updated",
        "scale": 100000,
    },
    {
        "url": "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR",
        "value_path": "rates.EUR",
        "time_path": "",
        "scale": 100000,
    },
]


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


def load_or_create_keys() -> dict:
    SECRETS.mkdir(parents=True, exist_ok=True)
    if KEYS_FILE.exists():
        data = json.loads(KEYS_FILE.read_text())
        return data
    accounts = {}
    for name in ACCOUNT_NAMES:
        acc = Account.create()
        accounts[name] = {"address": acc.address, "private_key": acc.key.hex()}
    KEYS_FILE.write_text(json.dumps(accounts, indent=2))
    os.chmod(KEYS_FILE, 0o600)
    return accounts


def accounts_from(keys: dict) -> dict:
    return {
        name: Account.from_key(value["private_key"])
        for name, value in keys.items()
    }


# ------------------------------------------------------------------ helpers


def retry(fn, label, tries=6, delay=5):
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


def wait(client, tx_hash, label, out: Path, tries: int = 40):
    say(out, f"  waiting for {label} {tx_hash}")
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            tx = client.wait_for_transaction_receipt(tx_hash, wait_until="decided", retries=2)
            lifecycle = tx.get("lifecycle", {}) if isinstance(tx, dict) else {}
            state = lifecycle.get("state")
            if state in ("decided", "finalized"):
                say(out, f"  {label} decided: {json.dumps({k: v for k, v in tx.items() if k in ('status', 'lifecycle')}, default=str)[:400]}")
                return tx
        except Exception as exc:  # noqa: BLE001
            say(out, f"  {label} not decided yet ({type(exc).__name__})")
        time.sleep(3)
    say(out, f"  {label} did not reach a decision in time")
    return None


def contract_address_from_tx(client, tx_hash):
    """The address of a freshly deployed contract.

    The deploy transaction carries the new address in its decoded payload
    (`contract_address`), so it is read back from the node instead of guessed."""
    tx = client.get_transaction(tx_hash)
    data = tx.get("data") or {}
    address = data.get("contract_address")
    if address:
        return address
    return None


def deployed_source(client, address) -> str:
    response = client.provider.make_request(
        method="gen_getContractCode", params=[address]
    )
    return response["result"]


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", default="studio_devnet")
    parser.add_argument("--date", default=time.strftime("%Y-%m-%d"))
    args = parser.parse_args()

    out = ARTIFACTS / f"live-run-{args.date}.txt"
    transcript_start(out)

    keys = load_or_create_keys()
    accts = accounts_from(keys)

    say(out, f"corroborate live run - {args.date}")
    say(out, f"network: {args.network} rpc: {studio_devnet.rpc_urls['default']['http'][0]}")
    say(out, f"chain id: {studio_devnet.id}")
    say(out, "accounts (addresses only):")
    for name, value in keys.items():
        say(out, f"  {name}: {value['address']}")

    client = create_client(chain=studio_devnet, account=accts["deployer"])

    # ------------------------------------------------------------- funding
    say(out, "")
    say(out, "step 1: fund the test accounts")
    for name in ACCOUNT_NAMES:
        address = accts[name].address
        response = retry(
            lambda a=address: client.provider.make_request(
                method="sim_fundAccount", params=[a, FUND_AMOUNT]
            ),
            f"sim_fundAccount {name}",
        )
        say(out, f"  sim_fundAccount {address} -> {json.dumps(response)[:200]}")
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
    estimate = retry(lambda: client.estimate_transaction_fees(), "fee estimate")
    say(out, f"  fee policy enabled: {estimate['policy']['enabled']}")
    say(out, f"  fee value estimate: {estimate['feeValue']}")
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
    address = retry(lambda: contract_address_from_tx(client, deploy_tx), "contract address")
    if address is None:
        say(out, "  could not read the contract address from the receipt")
        return 1
    say(out, f"  contract address: {address}")

    # ------------------------------------------------------- source identity
    say(out, "")
    say(out, "step 3: read the deployed source back and compare sha256")
    remote = retry(lambda: deployed_source(client, address), "gen_getContractCode")
    remote_bytes = base64_decode(remote)
    local_bytes = code.encode("utf-8")
    remote_sha = hashlib.sha256(remote_bytes).hexdigest()
    local_sha = hashlib.sha256(local_bytes).hexdigest()
    say(out, f"  local  sha256: {local_sha} ({len(local_bytes)} bytes)")
    say(out, f"  remote sha256: {remote_sha} ({len(remote_bytes)} bytes)")
    say(out, f"  identical: {remote_sha == local_sha and remote_bytes == local_bytes}")

    # ---------------------------------------------------------- the reading
    say(out, "")
    say(out, "step 4: register a feed and refresh it")
    sources_json = json.dumps(FEED_SOURCES)
    feed_args = [
        "eur-usd",
        "Euro per US dollar",
        "EUR per USD",
        150,
        0,
        172800,
        2,
        sources_json,
    ]
    feed_estimate = retry(
        lambda: client.estimate_transaction_fees_for_write(
            address=address,
            function_name="register_feed",
            account=accts["deployer"],
            args=feed_args,
        ),
        "register_feed estimate",
    )
    register_tx = retry(
        lambda: client.write_contract(
            address=address,
            function_name="register_feed",
            account=accts["deployer"],
            args=feed_args,
            fees={
                "distribution": feed_estimate["distribution"],
                "feeValue": feed_estimate["feeValue"],
            },
        ),
        "register_feed",
    )
    say(out, f"  register_feed tx: {register_tx}")
    wait(client, register_tx, "register_feed", out)

    refresh_estimate = client.estimate_transaction_fees_for_write(
        address=address,
        function_name="refresh",
        account=accts["deployer"],
        args=["eur-usd"],
    )
    refresh_tx = retry(
        lambda: client.write_contract(
            address=address,
            function_name="refresh",
            account=accts["deployer"],
            args=["eur-usd"],
            fees={
                "distribution": refresh_estimate["distribution"],
                "feeValue": refresh_estimate["feeValue"],
            },
        ),
        "refresh",
    )
    say(out, f"  refresh tx: {refresh_tx}")
    wait(client, refresh_tx, "refresh", out)

    record = retry(
        lambda: client.read_contract(
            address=address, function_name="latest", args=["eur-usd"]
        ),
        "latest",
    )
    say(out, "  latest(eur-usd):")
    say(out, "    " + json.dumps(record, indent=2, default=str).replace("\n", "\n    "))
    trustworthy = retry(
        lambda: client.read_contract(
            address=address, function_name="is_trustworthy", args=["eur-usd", 3600]
        ),
        "is_trustworthy",
    )
    say(out, f"  is_trustworthy(eur-usd, 3600): {trustworthy}")
    status = retry(
        lambda: client.read_contract(
            address=address, function_name="status", args=["eur-usd"]
        ),
        "status",
    )
    say(out, f"  status(eur-usd): {status}")

    # ------------------------------------------------------------ refusals
    say(out, "")
    say(out, "step 5: live refusals")
    rogue_args = ["rogue", "rogue", "EUR", 150, 0, 172800, 2, sources_json]
    refusal_estimate = retry(
        lambda: client.estimate_transaction_fees_for_write(
            address=address,
            function_name="register_feed",
            account=accts["alice"],
            args=rogue_args,
        ),
        "refusal estimate",
    )
    try:
        client.write_contract(
            address=address,
            function_name="register_feed",
            account=accts["alice"],
            args=rogue_args,
            fees={
                "distribution": refusal_estimate["distribution"],
                "feeValue": refusal_estimate["feeValue"],
            },
        )
        say(out, "  non-curator register_feed: ACCEPTED (unexpected)")
    except Exception as exc:  # noqa: BLE001
        say(out, f"  non-curator register_feed rejected: {type(exc).__name__}: {exc}")

    bad_args = ["bad", "bad", "EUR", 150, 0, 172800, 2, "not json"]
    bad_estimate = retry(
        lambda: client.estimate_transaction_fees_for_write(
            address=address,
            function_name="register_feed",
            account=accts["deployer"],
            args=bad_args,
        ),
        "bad input estimate",
    )
    try:
        client.write_contract(
            address=address,
            function_name="register_feed",
            account=accts["deployer"],
            args=bad_args,
            fees={
                "distribution": bad_estimate["distribution"],
                "feeValue": bad_estimate["feeValue"],
            },
        )
        say(out, "  malformed sources_json: ACCEPTED (unexpected)")
    except Exception as exc:  # noqa: BLE001
        say(out, f"  malformed sources_json rejected: {type(exc).__name__}: {exc}")

    say(out, "")
    say(out, f"transcript written to {out}")
    return 0


def base64_decode(data: str) -> bytes:
    import base64

    if data.startswith("0x"):
        data = data[2:]
    return base64.b64decode(data)


if __name__ == "__main__":
    sys.exit(main())
