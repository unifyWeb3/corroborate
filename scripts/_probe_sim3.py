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


def raw(label, payload):
    for i in range(4):
        try:
            res = client.provider.make_request(method="sim_call", params=[payload])
            print(label, "->", json.dumps(res, default=str)[:1200])
            return res
        except Exception as exc:
            print(label, "attempt", i, "failed:", str(exc)[:160])
            time.sleep(3)
    return None


read_data = serialize([calldata.encode(make_calldata_object(method="feed_count", args=[])), False])
raw("read feed_count", {"type": "read", "to": addr, "from": accts["deployer"].address, "data": read_data})

write_data = serialize([
    calldata.encode(make_calldata_object(method="disable_feed", args=["nope", True])), False])
raw("write no variant", {"type": "write", "to": addr, "from": accts["deployer"].address, "data": write_data})
raw("write with fees", {
    "type": "write", "to": addr, "from": accts["deployer"].address, "data": write_data,
    "transaction_hash_variant": 2,
    "fees": {
        "distribution": {
            "leaderTimeunitsAllocation": 100, "validatorTimeunitsAllocation": 200,
            "appealRounds": 0, "executionBudgetPerRound": 25000000000000000,
            "executionConsumed": 0, "totalMessageFees": 0, "rotations": [3],
            "maxPriceGenPerTimeUnit": 2, "storageFeeMaxGasPrice": 300000000,
            "receiptFeeMaxGasPrice": 300000000,
        },
        "messageAllocations": [],
        "feeValue": 100000000000010352,
    },
})
