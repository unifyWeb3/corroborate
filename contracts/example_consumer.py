# { "Depends": "py-genlayer:latest" }
"""
Example consumer of the corroborate feed contract.

It shows the three things a consumer has to get right:

  1. read the feed with a pinned state (StorageView.LATEST_FINALIZED), so it never reads
     a half finished state right after a receipt,
  2. gate on `is_trustworthy` - agreement, quorum, a complete read and freshness - and
  3. ask `compare(feed_id, threshold)` instead of reading a point value.  The feed
     stores an interval: `ABOVE` and `BELOW` are certain over the whole interval, and
     `INDETERMINATE` means the reading cannot decide.  Acting on a midpoint would
     reintroduce exactly the point estimate the feed refuses to publish.

`decide` is the decision rule of this consumer, kept as a plain function so it can be
tested on its own.  The GenLayer direct test runner cannot execute a cross contract
call, so `check` cannot be exercised end to end in unit tests - only `decide` and
`evaluate` can.  See docs/LIVE-RUN.md for the live path.
"""

import genlayer as gl
from genlayer.types import Address, u64, i64, u32
from genlayer.vm.public_abi import StorageView

UNTRUSTED = "UNTRUSTED"
ACCEPT = "ACCEPT"
REJECT = "REJECT"
UNDECIDED = "UNDECIDED"


def decide(trusted: bool, relation: str) -> str:
    """The consumer's rule: act only on a reading the feed vouches for.

    `relation` is exactly what `compare` returns.  There is no numeric fallback: an
    untrusted reading is never acted on, and an `INDETERMINATE` reading is not guessed.
    """
    if not trusted:
        return UNTRUSTED
    if relation == "ABOVE":
        return ACCEPT
    if relation == "BELOW":
        return REJECT
    return UNDECIDED


class ExampleConsumer(gl.contract.Contract):
    feed_address: Address
    accepted: u32
    rejected: u32
    undecided: u32
    last_outcome: str

    def __init__(self, feed_address: str):
        self.feed_address = Address(feed_address)
        self.accepted = 0
        self.rejected = 0
        self.undecided = 0
        self.last_outcome = UNDECIDED

    @gl.public.view
    def evaluate(self, trusted: bool, relation: str) -> str:
        """The decision rule, exposed so it can be tested without a cross contract call."""
        return decide(trusted, relation)

    @gl.public.write
    def check(self, feed_id: str, max_age: u64, min_value: i64):
        """Read the feed and act only when the threshold is decided and trustworthy."""
        feed = gl.contract.get_at(self.feed_address)
        view = feed.view(state=StorageView.LATEST_FINALIZED)
        trusted = view.is_trustworthy(feed_id, max_age)
        relation = view.compare(feed_id, min_value)
        outcome = decide(trusted, relation)
        if outcome == ACCEPT:
            self.accepted += 1
        elif outcome == REJECT:
            self.rejected += 1
        else:
            self.undecided += 1
        self.last_outcome = outcome

    @gl.public.view
    def counters(self) -> dict:
        return {
            "accepted": int(self.accepted),
            "rejected": int(self.rejected),
            "undecided": int(self.undecided),
            "last_outcome": self.last_outcome,
        }
