import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

addr = "0x462C09057ad8B44f731633325a4Ceb2FE1a6Fc9e"


def attempt(label, fn):
    for i in range(5):
        try:
            out = fn()
            print(label, "->", json.dumps(out, default=str)[:400])
            return out
        except Exception as exc:
            last = exc
            time.sleep(3)
    print(label, "FAILED:", str(last)[:200])
    return None


attempt("feed_count", lambda: client.read_contract(address=addr, function_name="feed_count"))
attempt("feed_count raw", lambda: client.provider.make_request(
    method="gen_call",
    params=[{"to": addr, "from": accts["deployer"].address, "data": ""}],
))
attempt("schema", lambda: client.provider.make_request(method="gen_getContractSchema", params=[addr]))
