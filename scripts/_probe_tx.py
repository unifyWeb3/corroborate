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


def walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            walk(v, f"{path}[{i}]")
    else:
        s = str(obj)
        if s.startswith("0x") and len(s) == 42:
            print("ADDRESS", path, s)


walk(json.loads(json.dumps(info, default=str)))
print("--- top level keys:", list(info.keys()))
