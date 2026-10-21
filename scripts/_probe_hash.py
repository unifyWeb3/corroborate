import json, sys, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

keys = json.load(open("/root/primitive-a/secrets/keys.json"))
accts = {n: Account.from_key(v["private_key"]) for n, v in keys.items()}
client = create_client(chain=studio_devnet, account=accts["deployer"])

code = open("/root/primitive-a/contracts/corroborate.py").read()
hashes = sys.argv[1:] or ["latest", "1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6"]


def req(method, params):
    for attempt in range(6):
        try:
            return client.provider.make_request(method=method, params=params)["result"]
        except Exception as exc:
            last = exc
            time.sleep(3)
    raise last


for h in hashes:
    body = code.replace(
        '# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }',
        '# { "Depends": "py-genlayer:%s" }' % h,
    )
    assert h in body, "header replace failed"
    estimate = req("sim_getFeeConfig", [])
    fees = client.estimate_transaction_fees()
    try:
        tx = client.deploy_contract(
            code=body,
            account=accts["deployer"],
            fees={"distribution": fees["distribution"], "feeValue": fees["feeValue"]},
        )
    except Exception as exc:
        print(h, "deploy failed:", str(exc)[:200])
        continue
    print(h, "tx", tx)
    for _ in range(30):
        info = client.get_transaction(tx)
        receipts = (info.get("consensus_data") or {}).get("leader_receipt") or []
        if receipts:
            res = receipts[0].get("result") or {}
            print("   result:", json.dumps(res, default=str)[:300])
            break
        time.sleep(4)
    time.sleep(2)
