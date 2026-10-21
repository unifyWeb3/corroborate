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
        print("retry", str(exc)[:120]); time.sleep(3)

receipts = (info.get("consensus_data") or {}).get("leader_receipt") or []
if receipts:
    print(json.dumps(receipts[0], default=str, indent=1)[:4000])
