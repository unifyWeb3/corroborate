# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
Example consumer of the corroborate feed contract.

It shows the two things a consumer has to get right:

  1. read the feed with a pinned state (StorageType.LATEST_FINAL), so it never reads a
     half finished state right after a receipt, and
  2. gate on the agreement status instead of using the number blindly.

`decide` is the decision rule of this consumer, kept as a plain function so it can be
tested on its own.  The GenLayer direct test runner cannot execute a cross contract
call, so `check` cannot be exercised end to end in unit tests - only `decide` and
`evaluate` can.  See docs/LIVE-RUN.md for the live path.
"""

from genlayer import *
from genlayer.py.types import Address
from genlayer.py.public_abi import StorageType


def decide(trusted: bool, value: int, min_value: int) -> bool:
    """The consumer's rule: act only on a reading the feed vouches for."""
    return trusted and value >= min_value


class ExampleConsumer(gl.Contract):
    feed_address: Address
    accepted: u32
    rejected: u32
    last_value: i64

    def __init__(self, feed_address: str):
        self.feed_address = Address(feed_address)
        self.accepted = 0
        self.rejected = 0
        self.last_value = 0

    @gl.public.view
    def evaluate(self, trusted: bool, value: int, min_value: int) -> bool:
        """The decision rule, exposed so it can be tested without a cross contract call."""
        return decide(trusted, value, min_value)

    @gl.public.write
    def check(self, feed_id: str, max_age: u64, min_value: i64):
        """Read the feed and act only when the reading is corroborated and fresh."""
        feed = gl.get_contract_at(self.feed_address)
        view = feed.view(state=StorageType.LATEST_FINAL)
        trusted = view.is_trustworthy(feed_id, max_age)
        value = view.value(feed_id)
        if decide(trusted, value, min_value):
            self.accepted += 1
            self.last_value = value
        else:
            self.rejected += 1

    @gl.public.view
    def counters(self) -> dict:
        return {
            "accepted": int(self.accepted),
            "rejected": int(self.rejected),
            "last_value": int(self.last_value),
        }
