import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

tx = "0x9414e080d95434c77c61ef602221acca28a779053d72a2dd2f6c32dbaa571d44"
for attempt in range(6):
    try:
        info = client.get_transaction(tx)
        break
    except Exception as exc:
        print("retry", str(exc)[:100])
        time.sleep(3)

cd = info.get("consensus_data") or {}
receipts = cd.get("leader_receipt") or []
for i, r in enumerate(receipts):
    print(f"--- leader_receipt[{i}]")
    for k in ("result", "vm_outcome", "state", "genvm", "eq_blocks_outputs", "stdout", "stderr"):
        if k in r:
            print(" ", k, ":", json.dumps(r[k], default=str)[:1500])
print("--- consensus_results")
for res in (info.get("consensus_history") or {}).get("consensus_results", []):
    for lr in res.get("leader_result", [])[:1]:
        print(json.dumps({k: v for k, v in lr.items() if k in ("result", "state", "vm_outcome")}, default=str)[:2000])
