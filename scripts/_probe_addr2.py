import json
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

txs = [
    "0x9414e080d95434c77c61ef602221acca28a779053d72a2dd2f6c32dbaa571d44",
    "0xe44f836f88c208a2ed0c5b664e12f996182ebf3e3d6cdc66f81fb6e0d173fd99",
]
for tx in txs:
    print("=== tx", tx)
    try:
        receipt = client.provider.make_request(method="eth_getTransactionReceipt", params=[tx])["result"]
        print("  status", receipt.get("status"), "contractAddress", receipt.get("contractAddress"))
        for log in receipt.get("logs", []) or []:
            print("   log addr", log["address"], "topics", [t.hex() for t in log["topics"]])
    except Exception as exc:
        print("  receipt error", exc)
    try:
        data = client.get_transaction(tx).get("data") or {}
        print("  payload contract_address:", data.get("contract_address"))
        print("  payload salt_nonce:", data.get("salt_nonce"))
    except Exception as exc:
        print("  tx error", exc)

for addr in ["0x51B286F71351103138aecA1DDC9C891Fd1c6a6E6", "0xc6b9361bA54d9E201656cADf4FAaD80Df9EdA7Cb"]:
    try:
        code = client.provider.make_request(method="gen_getContractCode", params=[addr])["result"]
        print("FOUND code for", addr, "len", len(code))
    except Exception as exc:
        print("not found", addr, str(exc)[:120])
