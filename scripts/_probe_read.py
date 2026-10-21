import json
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

addr = "0x51B286F71351103138aecA1DDC9C891Fd1c6a6E6"
for method, params in [
    ("gen_getContractSchema", [addr]),
    ("gen_getContractSchemaForCode", None),
]:
    if params is None:
        continue
    try:
        res = client.provider.make_request(method=method, params=params)["result"]
        print(method, "->", json.dumps(res)[:400])
    except Exception as exc:
        print(method, "error:", str(exc)[:200])

try:
    print("feed_count:", client.read_contract(address=addr, function_name="feed_count"))
except Exception as exc:
    print("read error:", str(exc)[:300])

try:
    print("latest:", client.read_contract(address=addr, function_name="latest", args=["eur-usd"]))
except Exception as exc:
    print("latest error:", str(exc)[:300])
