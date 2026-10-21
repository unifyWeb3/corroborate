import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])


def req(method, params):
    last = None
    for attempt in range(6):
        try:
            return client.provider.make_request(method=method, params=params)["result"]
        except Exception as exc:
            last = exc
            time.sleep(3)
    raise last


code = open("/root/primitive-a/contracts/_probe.py").read()
fees = client.estimate_transaction_fees()
tx = client.deploy_contract(code=code, account=accts["deployer"],
                             fees={"distribution": fees["distribution"], "feeValue": fees["feeValue"]})
print("deploy tx", tx)
for _ in range(40):
    info = client.get_transaction(tx)
    receipts = (info.get("consensus_data") or {}).get("leader_receipt") or []
    if receipts:
        print("result:", json.dumps(receipts[0].get("result"), default=str)[:200])
        break
    time.sleep(3)

addr = None
for _ in range(10):
    info = client.get_transaction(tx)
    data = info.get("data") or {}
    if data.get("contract_address"):
        addr = data["contract_address"]
        break
    time.sleep(3)
print("address", addr)

for method in ("t_datetime", "t_timestamp", "t_json", "t_sender"):
    try:
        out = client.read_contract(address=addr, function_name=method)
        print(method, "->", out)
    except Exception as exc:
        print(method, "FAILED:", str(exc)[:200])
