import json, sys, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

tx = sys.argv[1]
for attempt in range(6):
    try:
        info = client.get_transaction(tx)
        break
    except Exception as exc:
        print("retry", str(exc)[:120])
        time.sleep(3)

receipts = (info.get("consensus_data") or {}).get("leader_receipt") or []
for i, r in enumerate(receipts):
    res = r.get("result") or {}
    print(f"receipt[{i}] status={res.get('status')} payload={json.dumps(res.get('payload'), default=str)[:1200]}")
    for k in ("vm_outcome", "state", "genvm", "stdout", "stderr"):
        if k in r:
            print("   ", k, json.dumps(r[k], default=str)[:800])
print("--- eq outputs")
for k in ("eq_blocks_outputs", "genvm_blocks_outputs"):
    if k in (info.get("data") or {}):
        print(k, json.dumps(info["data"][k], default=str)[:1500])
print("--- transaction data keys:", list((info.get("data") or {}).keys()))
