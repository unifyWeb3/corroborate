import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])


def req(method, params):
    for attempt in range(8):
        try:
            return client.provider.make_request(method=method, params=params)["result"]
        except Exception as exc:
            print("  retry", attempt, str(exc)[:100])
            time.sleep(4)
    raise SystemExit("rpc unreachable")


tx = "0x9414e080d95434c77c61ef602221acca28a779053d72a2dd2f6c32dbaa571d44"
receipt = req("eth_getTransactionReceipt", [tx])
print(json.dumps(receipt, indent=1)[:4000])
