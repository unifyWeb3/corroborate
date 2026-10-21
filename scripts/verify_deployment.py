#!/usr/bin/env python3
"""Verify the deployed contract against this repository, and read the last readings.

    python scripts/verify_deployment.py

Reads the deployed source back with `gen_getContractCode`, compares it with
`contracts/corroborate.py` byte for byte (and prints both sha256 sums), then prints the
last reading of every feed with its agreement status.
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

ROOT = Path(__file__).resolve().parent.parent
ADDRESS = os.environ.get("CORROBORATE_ADDRESS", "0x81ffeD2e3b5558e67D6dCEa79B5Cead6E247d68F")
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

    remote = base64.b64decode(request("gen_getContractCode", [ADDRESS]))
    local = CONTRACT.read_bytes()
    print("deployed sha256:", hashlib.sha256(remote).hexdigest())
    print("repo     sha256:", hashlib.sha256(local).hexdigest())
    print("byte identical:", remote == local)

    for feed in FEEDS:
        try:
            record = client.read_contract(address=ADDRESS, function_name="latest", args=[feed])
            trust = client.read_contract(
                address=ADDRESS, function_name="is_trustworthy", args=[feed, 3600]
            )
            status = client.read_contract(address=ADDRESS, function_name="status", args=[feed])
            print(
                f"{feed}: value={record['value']} status={status} "
                f"agree={record['agree_count']}/{record['sources_read']} "
                f"flags={record['flags']} trustworthy={trust} read_at={record['read_at']}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{feed} -> {str(exc)[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
