import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet
from genlayer_py.contracts.utils import make_calldata_object
from genlayer_py.abi.transactions import serialize
from genlayer_py.abi import calldata

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

addr = "0x462C09057ad8B44f731633325a4Ceb2FE1a6Fc9e"
sources = [
    {"url": "https://open.er-api.com/v6/latest/USD", "value_path": "rates.EUR",
     "time_path": "time_last_update_unix", "scale": 100000},
    {"url": "https://api.exchangerate-api.com/v4/latest/USD", "value_path": "rates.EUR",
     "time_path": "time_last_updated", "scale": 100000},
    {"url": "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR",
     "value_path": "rates.EUR", "time_path": "", "scale": 100000},
]


def sim(label, function_name, args):
    data = [calldata.encode(make_calldata_object(method=function_name, args=args)), False]
    payload = {
        "type": "write",
        "to": addr,
        "from": accts["deployer"].address,
        "data": serialize(data),
        "transaction_hash_variant": 2,
    }
    for i in range(5):
        try:
            res = client.provider.make_request(method="sim_call", params=[payload])
            print(label, "->", json.dumps(res, default=str)[:1500])
            return res
        except Exception as exc:
            print(label, "attempt", i, "failed:", str(exc)[:200])
            time.sleep(3)
    return None


sim("register_feed", "register_feed", [
    "eur-usd", "Euro per US dollar", "EUR per USD", 150, 0, 172800, 2, json.dumps(sources)])
sim("register_feed minimal", "register_feed", [
    "eur-usd", "Euro per US dollar", "EUR per USD", 150, 0, 172800, 2,
    json.dumps([{"url": "https://a.example.com/x", "value_path": "p"},
                {"url": "https://b.example.com/x", "value_path": "p"}]),
])
sim("disable_feed unknown", "disable_feed", ["nope", True])
