#!/usr/bin/env python3
"""Verify the deployed contract against this repository, and read the last readings.

    python scripts/verify_deployment.py

Reads the deployed source back with `gen_getContractCode`, compares it with
`contracts/corroborate.py` byte for byte (and prints both sha256 sums), then prints the
last reading of every feed and the threshold answers a consumer would get, with the read
pinned to the latest final state.
"""

import base64
import hashlib
import json
import os
import time
from pathlib import Path

from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet
from genlayer_py.types import TransactionHashVariant

ROOT = Path(__file__).resolve().parent.parent
ADDRESS = os.environ.get(
    "CORROBORATE_ADDRESS", "0x6D0Cd00A0d3eb9C86eEE81f22cb05e3B51E070Ed"
)
CONTRACT = ROOT / "contracts" / "corroborate.py"
KEYS_FILE = ROOT / "secrets" / "keys.json"
FEEDS = ("eur-usd", "eur-usd-partial")


def main() -> int:
    if not KEYS_FILE.exists():
        print("secrets/keys.json is missing; run scripts/live_run.py first")
        return 1
    keys = json.loads(KEYS_FILE.read_text())
    accounts = {name: Account.from_key(v["private_key"]) for name, v in keys.items()}
    client = create_client(chain=studio_devnet, account=accounts["deployer"])

    def request(method, params):
        last = None
        for _ in range(8):
            try:
                return client.provider.make_request(method=method, params=params)["result"]
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(3)
        raise last

    def read(function, args):
        return client.read_contract(
            address=ADDRESS,
            function_name=function,
            args=args,
            transaction_hash_variant=TransactionHashVariant.LATEST_FINAL,
        )

    remote = base64.b64decode(request("gen_getContractCode", [ADDRESS]))
    local = CONTRACT.read_bytes()
    print("deployed sha256:", hashlib.sha256(remote).hexdigest())
    print("repo     sha256:", hashlib.sha256(local).hexdigest())
    print("byte identical:", remote == local)

    for feed in FEEDS:
        try:
            record = read("latest", [feed])
            trust = read("is_trustworthy", [feed, 3600])
            status = read("status", [feed])
            lo, hi = record["lo"], record["hi"]
            print(
                f"{feed}: lo={lo} hi={hi} status={status} "
                f"agree={record['agree_count']}/{record['sources_read']} "
                f"declared={record['sources_declared']} flags={record['flags']} "
                f"trustworthy={trust} read_at={record['read_at']} "
                f"data_ts={record['data_ts']}"
            )
            if lo == hi:
                points = [lo, lo + 1]
            else:
                points = [lo, (lo + hi) // 2, hi + 1]
            for threshold in points:
                answer = read("compare", [feed, threshold])
                print(f"  compare({feed}, {threshold}) -> {answer}")
        except Exception as exc:  # noqa: BLE001
            print(f"{feed} -> {str(exc)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
