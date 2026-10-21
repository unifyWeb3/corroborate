import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])


def req(method, params, allow_error=False):
    last = None
    for attempt in range(6):
        try:
            return client.provider.make_request(method=method, params=params)["result"]
        except Exception as exc:
            last = exc
            time.sleep(3)
    if allow_error:
        return None
    raise last


tx = "0x9414e080d95434c77c61ef602221acca28a779053d72a2dd2f6c32dbaa571d44"
addr = "0x51B286F71351103138aecA1DDC9C891Fd1c6a6E6"

for i in range(12):
    txinfo = req("eth_getTransactionByHash", [tx], allow_error=True) or {}
    print(f"[{i}] tx status={txinfo.get('status')} name={txinfo.get('status_name')}")
    code = req("gen_getContractCode", [addr], allow_error=True)
    if code:
        print("FOUND code, len", len(code))
        print(code[:120])
        break
    schema = req("gen_getContractSchema", [addr], allow_error=True)
    if schema:
        print("FOUND schema:", json.dumps(schema)[:200])
        break
    time.sleep(10)
else:
    print("never found")
