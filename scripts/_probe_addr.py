import json
from eth_abi import decode as abi_decode
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

tx = "0xe44f836f88c208a2ed0c5b664e12f996182ebf3e3d6cdc66f81fb6e0d173fd99"
info = client.w3.eth.get_transaction(tx)
data = bytes(info["data"])
print("data len", len(data), "selector", data[:4].hex())
(payload,) = abi_decode(["bytes"], data[4:])
import genlayer.calldata as calldata
decoded = calldata.decode(payload)
print("keys:", list(decoded.keys()))
print("contract_address:", decoded.get("contract_address"))
print("salt_nonce:", decoded.get("salt_nonce"))
