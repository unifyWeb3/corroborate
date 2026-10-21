import json, time
from eth_account import Account
from genlayer_py import create_client
from genlayer_py.chains import studio_devnet

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
args = ["eur-usd", "Euro per US dollar", "EUR per USD", 150, 0, 172800, 2, json.dumps(sources)]

for attempt in range(6):
    try:
        sim = client.simulate_write_contract(
            address=addr, function_name="register_feed", account=accts["deployer"], args=args
        )
        break
    except Exception as exc:
        print("retry", str(exc)[:200])
        time.sleep(3)
else:
    raise SystemExit(1)

print(json.dumps(sim, default=str, indent=1)[:3000])
