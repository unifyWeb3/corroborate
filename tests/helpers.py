"""Shared helpers for the corroborate test suite."""

import json

CONTRACT = "contracts/corroborate.py"
CONSUMER = "contracts/example_consumer.py"


def source(url, value_path="price", time_path="", scale=1):
    return {
        "url": url,
        "value_path": value_path,
        "time_path": time_path,
        "scale": scale,
    }


def sources_json(*entries):
    return json.dumps(list(entries))


def two_sources(scale=1, time_path=""):
    return sources_json(
        source("https://one.example.test/price", "price", time_path, scale),
        source("https://two.example.test/price", "price", time_path, scale),
    )


def three_sources(scale=1, time_path=""):
    return sources_json(
        source("https://one.example.test/price", "price", time_path, scale),
        source("https://two.example.test/price", "price", time_path, scale),
        source("https://three.example.test/price", "price", time_path, scale),
    )


def body(value, extra=""):
    """A minimal JSON body carrying one number."""
    return '{"price": %s%s}' % (value, extra)


def mock(direct_vm, url, payload, status=200):
    direct_vm.mock_web(url, {"status": status, "body": payload})


def proposal_from_record(record):
    """Rebuild the leader's proposal from the stored record.

    The stored record carries the same fields the proposal carries, so a test can take
    the honest proposal apart and hand a tampered one to the validator."""
    return {
        "value": record["value"],
        "flags": record["flags"],
        "agree_count": record["agree_count"],
        "sources_read": record["sources_read"],
        "read_at": record["read_at"],
        "data_ts": record["data_ts"],
        "readings": [
            {
                "index": r["index"],
                "value": r["value"],
                "digest": r["digest"],
                "data_ts": r["data_ts"],
                "agrees": r["agrees"],
            }
            for r in record["readings"]
        ],
    }


def keccak256_hex(data: bytes) -> str:
    """An independent keccak256, so the test does not check the contract against
    itself."""
    from Crypto.Hash import keccak

    digest = keccak.new(digest_bits=256)
    digest.update(data)
    return digest.hexdigest()
