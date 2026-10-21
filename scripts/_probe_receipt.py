import json
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

tx = "0xe44f836f88c208a2ed0c5b664e12f996182ebf3e3d6cdc66f81fb6e0d173fd99"
receipt = client.w3.eth.get_transaction_receipt(tx)
print("status", receipt["status"])
print("contractAddress", receipt.get("contractAddress"))
print("to", receipt.get("to"))
print("logs:", len(receipt["logs"]))
for log in receipt["logs"]:
    print("  addr", log["address"], "topics", [t.hex() for t in log["topics"]], "data", log["data"].hex()[:80])
print("--- tx info ---")
info = client.w3.eth.get_transaction(tx)
print("to", info["to"], "from", info["from"], "nonce", info["nonce"], "data len", len(info["data"]))
print("--- get_transaction ---")
print(json.dumps(client.get_transaction(tx), indent=1, default=str)[:2000])
