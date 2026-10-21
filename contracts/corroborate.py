# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }
"""
corroborate - a tolerance-consensus data feed with provenance.

A feed is declared once by the curator (a set of public HTTPS sources, a JSON path per
source, a tolerance, a staleness limit and a quorum).  Any caller may then trigger a
refresh.  The leader fetches every source, derives one number per source and aggregates
them with the median.  Every validator re-fetches every source itself, re-derives the
numbers, and accepts the leader's proposal only when

  * each per-source value is within the declared tolerance of the validator's own value,
  * each per-source digest matches the digest of the body the validator itself read
    (or the values are exactly equal, which is what a body that ticks between the two
    reads looks like),
  * the aggregate equals the validator's own re-derived median within the tolerance,
  * the quorum, the flags and the read time are exactly what the validator computes.

Only then is anything written to the chain.  What is written is the value plus its
provenance: the per-source values, the keccak256 digest of every body that was read, the
source's own data timestamp and the transaction timestamp of the refresh.

The read side is deliberately small: `latest`, `value`, `status` and `is_trustworthy`,
the last one being the guard a consuming contract should use.
"""

import genlayer as gl
from genlayer.storage import DynArray, TreeMap

# The release candidate SDK calls this decorator `allow`; the matching linter
# (genvm-linter 0.11.1rc2) still looks for the name `allow_storage`.  It is the same
# decorator, imported under the name the linter knows.
from genlayer.storage import allow as allow_storage

from genlayer.types import u32, u64, i64, Address
from dataclasses import dataclass
from datetime import datetime, timezone
import json

# --------------------------------------------------------------------- limits

MAX_SOURCES = 8
MIN_SOURCES = 2
MAX_FEEDS = 32
MAX_BODY_BYTES = 32768
MAX_URL_LEN = 256
MAX_PATH_LEN = 128
MAX_FEED_ID_LEN = 64
MAX_LABEL_LEN = 64
MAX_UNIT_LEN = 16
MAX_SOURCES_JSON = 8192
MAX_SCALE = 1000000000
MAX_VALUE = 4611686018427387904  # 2 ** 62
MAX_TOL_REL_BPS = 10000  # 100 %
TS_TOL_SECONDS = 120

# ---------------------------------------------------------------------- flags

FLAG_DISPUTED = 1  # a source that was read disagrees with the median
FLAG_PARTIAL = 2  # a declared source could not be read
FLAG_STALE = 4  # a source carries a timestamp older than max_age_seconds

REASON_OK = 0
REASON_HTTP = 1
REASON_NETWORK = 2
REASON_MALFORMED = 3
REASON_MISSING = 4
REASON_BAD_VALUE = 5
REASON_OVERSIZE = 6


# ------------------------------------------------------------------- storage


@allow_storage
@dataclass
class Source:
    url: str
    value_path: str
    time_path: str
    scale: u32


@allow_storage
@dataclass
class Feed:
    label: str
    unit: str
    tol_rel_bps: u32
    tol_abs: i64
    max_age_seconds: u64
    min_quorum: u32
    sources: DynArray[Source]
    created_at: u64
    disabled: bool


@allow_storage
@dataclass
class Reading:
    index: u32
    value: i64
    digest: str
    data_ts: u64
    agrees: bool


@allow_storage
@dataclass
class Record:
    value: i64
    flags: u32
    agree_count: u32
    sources_read: u32
    sources_declared: u32
    quorum_required: u32
    read_at: u64
    data_ts: u64
    readings: DynArray[Reading]


# ------------------------------------------------------------ pure helpers


def now_ts() -> int:
    """The transaction timestamp.

    In the deterministic context `gl.vm.get_timestamp()` is the transaction timestamp,
    and it is the same value on the leader and on every validator that re-executes the
    transaction, so it can be stored and re-checked.  The standard library clock is
    wired to the same value, and is used as a fallback."""
    stamp = gl.vm.get_timestamp()
    if stamp is None:
        stamp = datetime.now(timezone.utc)
    return int(stamp.timestamp())


def is_word_char(ch: str) -> bool:
    o = ord(ch)
    return 48 <= o <= 57 or 65 <= o <= 90 or 97 <= o <= 122


def within(a: int, b: int, tol_abs: int, tol_rel_bps: int) -> bool:
    """True when two scaled integers agree inside the declared tolerance.

    The absolute part covers values near zero, the relative part covers proportional
    drift.  Either one being satisfied is enough."""
    diff = a - b
    if diff < 0:
        diff = -diff
    if diff <= tol_abs:
        return True
    mag = a if a >= 0 else -a
    other = b if b >= 0 else -b
    if other > mag:
        mag = other
    if mag == 0:
        return diff == 0
    return diff * 10000 <= tol_rel_bps * mag


def lower_median(values: list) -> int:
    """The lower median of a non empty list of integers.

    The lower median is always one of the observed values, so the published number is
    always a number that some source actually reported."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def extract_path(doc, path: str):
    """Walk a dotted path through a decoded JSON document.

    Returns (found, value).  A missing segment is not an error here, it is simply not
    found: the caller turns that into a failed source."""
    if path == "":
        return True, doc
    current = doc
    for segment in path.split("."):
        if isinstance(current, dict):
            if segment not in current:
                return False, None
            current = current[segment]
        elif isinstance(current, list):
            if not segment.isdigit():
                return False, None
            idx = int(segment)
            if idx >= len(current):
                return False, None
            current = current[idx]
        else:
            return False, None
    return True, current


def to_scaled(raw, scale: int):
    """Convert an extracted JSON number into a scaled integer.

    Returns (ok, scaled).  A float is multiplied by the scale and rounded half away
    from zero, so the scale declares the resolution of the feed and the rounding is
    identical on every node.  A decimal string is accepted as well, because several
    public APIs quote prices as strings."""
    if isinstance(raw, bool):
        return False, 0
    if isinstance(raw, int):
        value = raw * scale
    elif isinstance(raw, float):
        if raw != raw or raw > 1e18 or raw < -1e18:
            return False, 0
        scaled = raw * scale
        if scaled >= 0:
            value = int(scaled + 0.5)
        else:
            value = -int(-scaled + 0.5)
    elif isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return False, 0
        negative = False
        if text[0] == "-":
            negative = True
            text = text[1:]
        elif text[0] == "+":
            text = text[1:]
        if text == "" or not text.replace(".", "", 1).isdigit():
            return False, 0
        if text.count(".") > 1:
            return False, 0
        scaled = float(text) * scale
        if scaled >= 0:
            value = int(scaled + 0.5)
        else:
            value = -int(-scaled + 0.5)
        if negative:
            value = -value
    else:
        return False, 0
    if value > MAX_VALUE or value < -MAX_VALUE:
        return False, 0
    return True, value


def digest_of(index: int, value: int) -> str:
    """The digest of a reading: which source produced it and what it produced.

    It is deliberately *not* a digest of the raw response body.  A page that changes
    between the leader's read and the validator's read would give two different body
    digests even though both read the same value, and a digest that the committee
    cannot confirm is a leader claim, not a verified fact.  The reading digest is
    computed by the committee on both sides and must be identical."""
    hasher = gl.Keccak256()
    hasher.update(f"{index}:{value}".encode("utf-8"))
    return hasher.hexdigest()


def flags_for(agree_count: int, read_count: int, declared_count: int, stale: bool) -> int:
    flags = 0
    if agree_count < read_count:
        flags |= FLAG_DISPUTED
    if read_count < declared_count:
        flags |= FLAG_PARTIAL
    if stale:
        flags |= FLAG_STALE
    return flags


def status_text(flags: int) -> str:
    parts = []
    if flags & FLAG_DISPUTED:
        parts.append("disputed")
    if flags & FLAG_PARTIAL:
        parts.append("partial")
    if flags & FLAG_STALE:
        parts.append("stale")
    if len(parts) == 0:
        return "agreed"
    return "+".join(parts)


# ------------------------------------------------------------------ contract


class Corroborate(gl.contract.Contract):
    """A tolerance-consensus data feed with provenance."""

    owner: Address
    feeds: TreeMap[str, Feed]
    records: TreeMap[str, Record]

    def __init__(self):
        self.owner = gl.message.sender_address

    # ------------------------------------------------------------- curation

    @gl.public.write
    def register_feed(
        self,
        feed_id: str,
        label: str,
        unit: str,
        tol_rel_bps: u32,
        tol_abs: i64,
        max_age_seconds: u64,
        min_quorum: u32,
        sources_json: str,
    ):
        """Declare a feed. Curator only, and only once per id."""
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("not the curator")
        self._register_feed(
            feed_id,
            label,
            unit,
            tol_rel_bps,
            tol_abs,
            max_age_seconds,
            min_quorum,
            sources_json,
        )

    def _register_feed(
        self,
        feed_id: str,
        label: str,
        unit: str,
        tol_rel_bps: u32,
        tol_abs: i64,
        max_age_seconds: u64,
        min_quorum: u32,
        sources_json: str,
    ):
        self._check_feed_id(feed_id)
        if len(label) > MAX_LABEL_LEN:
            raise gl.vm.UserError("invalid label")
        if len(unit) > MAX_UNIT_LEN:
            raise gl.vm.UserError("invalid unit")
        if tol_rel_bps > MAX_TOL_REL_BPS:
            raise gl.vm.UserError("invalid tolerance")
        if tol_abs < 0:
            raise gl.vm.UserError("invalid tolerance")
        if len(self.feeds) >= MAX_FEEDS:
            raise gl.vm.UserError("too many feeds")
        if feed_id in self.feeds:
            raise gl.vm.UserError("feed exists")

        sources = self._parse_sources(sources_json)
        if min_quorum < 1 or min_quorum > len(sources):
            raise gl.vm.UserError("invalid quorum")

        self.feeds[feed_id] = Feed(
            label=label,
            unit=unit,
            tol_rel_bps=tol_rel_bps,
            tol_abs=tol_abs,
            max_age_seconds=max_age_seconds,
            min_quorum=min_quorum,
            sources=sources,
            created_at=now_ts(),
            disabled=False,
        )

    @gl.public.write
    def disable_feed(self, feed_id: str, disabled: bool):
        """Administrative kill switch. Curator only."""
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("not the curator")
        feed = self.feeds.get(feed_id)
        if feed is None:
            raise gl.vm.UserError("unknown feed")
        feed.disabled = disabled

    def _parse_sources(self, sources_json: str) -> list:
        if len(sources_json) > MAX_SOURCES_JSON:
            raise gl.vm.UserError("invalid sources")
        try:
            parsed = json.loads(sources_json)
        except Exception:
            raise gl.vm.UserError("invalid sources")
        if not isinstance(parsed, list):
            raise gl.vm.UserError("invalid sources")
        if len(parsed) < MIN_SOURCES or len(parsed) > MAX_SOURCES:
            raise gl.vm.UserError("invalid source count")

        sources = []
        for entry in parsed:
            if not isinstance(entry, dict):
                raise gl.vm.UserError("invalid sources")
            url = entry.get("url", "")
            value_path = entry.get("value_path", "")
            time_path = entry.get("time_path", "")
            scale = entry.get("scale", 1)
            if not isinstance(url, str) or not isinstance(value_path, str):
                raise gl.vm.UserError("invalid sources")
            if not isinstance(time_path, str):
                raise gl.vm.UserError("invalid sources")
            if not isinstance(scale, int) or isinstance(scale, bool):
                raise gl.vm.UserError("invalid scale")
            self._check_url(url)
            self._check_path(value_path, False)
            self._check_path(time_path, True)
            if scale < 1 or scale > MAX_SCALE:
                raise gl.vm.UserError("invalid scale")
            sources.append(
                Source(
                    url=url,
                    value_path=value_path,
                    time_path=time_path,
                    scale=scale,
                )
            )
        return sources

    def _check_feed_id(self, feed_id: str):
        if len(feed_id) == 0 or len(feed_id) > MAX_FEED_ID_LEN:
            raise gl.vm.UserError("invalid feed id")
        for ch in feed_id:
            if not (is_word_char(ch) or ch == "-" or ch == "_" or ch == "."):
                raise gl.vm.UserError("invalid feed id")

    def _check_url(self, url: str):
        if len(url) <= 8 or len(url) > MAX_URL_LEN:
            raise gl.vm.UserError("invalid source url")
        if url[0:8] != "https://":
            raise gl.vm.UserError("invalid source url")
        for ch in url:
            o = ord(ch)
            if o <= 32 or o == 127:
                raise gl.vm.UserError("invalid source url")

    def _check_path(self, path: str, allow_empty: bool):
        if len(path) > MAX_PATH_LEN:
            raise gl.vm.UserError("invalid path")
        if path == "":
            if allow_empty:
                return
            raise gl.vm.UserError("invalid path")
        for segment in path.split("."):
            if len(segment) == 0 or len(segment) > 64:
                raise gl.vm.UserError("invalid path")
            for ch in segment:
                if not (is_word_char(ch) or ch == "_" or ch == "-"):
                    raise gl.vm.UserError("invalid path")

    # -------------------------------------------------------------- refresh

    @gl.public.write
    def refresh(self, feed_id: str):
        """Fetch every declared source, derive the reading and publish it.

        The leader proposes, every validator re-derives the reading from its own reads
        and accepts only a proposal that survives every check.  A rejected proposal is
        not written: the network rotates the leader and, if nobody can satisfy the
        committee, the transaction ends undetermined."""
        feed = self.feeds.get(feed_id)
        if feed is None:
            raise gl.vm.UserError("unknown feed")
        if feed.disabled:
            raise gl.vm.UserError("feed disabled")

        # The configuration is copied into plain values before the non deterministic
        # block: inside such a block the contract may not read storage.
        sources = []
        for i in range(len(feed.sources)):
            src = feed.sources[i]
            sources.append((src.url, src.value_path, src.time_path, int(src.scale)))
        tol_rel = int(feed.tol_rel_bps)
        tol_abs = int(feed.tol_abs)
        max_age = int(feed.max_age_seconds)
        quorum = int(feed.min_quorum)
        read_at = now_ts()

        def leader_fn():
            readings = fetch_sources(sources)
            return build_proposal(
                readings, sources, tol_rel, tol_abs, max_age, quorum, read_at
            )

        def validator_fn(result) -> bool:
            if not isinstance(result, gl.vm.Return):
                return False
            proposal = result.calldata
            if not isinstance(proposal, dict):
                return False
            mine = fetch_sources(sources)
            return verify_proposal(
                proposal, mine, sources, tol_rel, tol_abs, max_age, quorum, read_at
            )

        proposal = gl.vm.run_nondet(leader_fn, validator_fn)

        # Only now, after the committee accepted the proposal, is anything written.
        self.records[feed_id] = self._record_from_proposal(proposal, feed)

    def _record_from_proposal(self, proposal: dict, feed: Feed) -> Record:
        readings = []
        for entry in proposal["readings"]:
            readings.append(
                Reading(
                    index=int(entry["index"]),
                    value=int(entry["value"]),
                    digest=str(entry["digest"]),
                    data_ts=int(entry["data_ts"]),
                    agrees=bool(entry["agrees"]),
                )
            )
        return Record(
            value=int(proposal["value"]),
            flags=int(proposal["flags"]),
            agree_count=int(proposal["agree_count"]),
            sources_read=int(proposal["sources_read"]),
            sources_declared=len(feed.sources),
            quorum_required=int(feed.min_quorum),
            read_at=int(proposal["read_at"]),
            data_ts=int(proposal["data_ts"]),
            readings=readings,
        )

    # ----------------------------------------------------------- read side

    @gl.public.view
    def latest(self, feed_id: str) -> dict:
        """The last verified reading with its provenance."""
        record = self.records.get(feed_id)
        if record is None:
            raise gl.vm.UserError("unknown feed")
        readings = []
        for i in range(len(record.readings)):
            entry = record.readings[i]
            readings.append(
                {
                    "index": int(entry.index),
                    "value": int(entry.value),
                    "digest": entry.digest,
                    "data_ts": int(entry.data_ts),
                    "agrees": bool(entry.agrees),
                }
            )
        return {
            "value": int(record.value),
            "flags": int(record.flags),
            "status": status_text(int(record.flags)),
            "agree_count": int(record.agree_count),
            "sources_read": int(record.sources_read),
            "sources_declared": int(record.sources_declared),
            "quorum_required": int(record.quorum_required),
            "read_at": int(record.read_at),
            "data_ts": int(record.data_ts),
            "age_seconds": now_ts() - int(record.read_at),
            "readings": readings,
        }

    @gl.public.view
    def value(self, feed_id: str) -> int:
        """The last verified value, scaled. Read `status` before you trust it."""
        record = self.records.get(feed_id)
        if record is None:
            raise gl.vm.UserError("unknown feed")
        return int(record.value)

    @gl.public.view
    def status(self, feed_id: str) -> str:
        """agreed, disputed, partial, stale or a combination of them."""
        record = self.records.get(feed_id)
        if record is None:
            raise gl.vm.UserError("unknown feed")
        return status_text(int(record.flags))

    @gl.public.view
    def is_trustworthy(self, feed_id: str, max_age_seconds: u64) -> bool:
        """The guard a consuming contract should use.

        True only when the feed is enabled, a reading exists, the committee found no
        disagreement, the quorum was met, every declared source contributed and the
        reading is younger than max_age_seconds."""
        feed = self.feeds.get(feed_id)
        if feed is None or feed.disabled:
            return False
        record = self.records.get(feed_id)
        if record is None:
            return False
        if int(record.flags) != 0:
            return False
        if int(record.agree_count) < int(record.quorum_required):
            return False
        if int(record.sources_read) < int(record.sources_declared):
            return False
        age = now_ts() - int(record.read_at)
        if age < 0 or age > int(max_age_seconds):
            return False
        return True

    @gl.public.view
    def feed(self, feed_id: str) -> dict:
        """The declared configuration of a feed."""
        feed = self.feeds.get(feed_id)
        if feed is None:
            raise gl.vm.UserError("unknown feed")
        sources = []
        for i in range(len(feed.sources)):
            src = feed.sources[i]
            sources.append(
                {
                    "url": src.url,
                    "value_path": src.value_path,
                    "time_path": src.time_path,
                    "scale": int(src.scale),
                }
            )
        return {
            "feed_id": feed_id,
            "label": feed.label,
            "unit": feed.unit,
            "tol_rel_bps": int(feed.tol_rel_bps),
            "tol_abs": int(feed.tol_abs),
            "max_age_seconds": int(feed.max_age_seconds),
            "min_quorum": int(feed.min_quorum),
            "sources": sources,
            "created_at": int(feed.created_at),
            "disabled": bool(feed.disabled),
        }

    @gl.public.view
    def feed_count(self) -> int:
        return len(self.feeds)

    @gl.public.view
    def source_count(self, feed_id: str) -> int:
        feed = self.feeds.get(feed_id)
        if feed is None:
            raise gl.vm.UserError("unknown feed")
        return len(feed.sources)


# -------------------------------------------------------------- the reading


def fetch_sources(sources: list) -> list:
    """Read every declared source and derive one value per source.

    Runs inside the non deterministic block, on the leader and on every validator, so
    it must not touch storage and must not emit anything."""
    out = []
    for i in range(len(sources)):
        url = sources[i][0]
        value_path = sources[i][1]
        time_path = sources[i][2]
        scale = sources[i][3]
        entry = {
            "index": i,
            "ok": False,
            "value": 0,
            "digest": "",
            "data_ts": 0,
            "reason": REASON_NETWORK,
        }
        try:
            response = gl.nondet.web.get(url)
        except Exception:
            out.append(entry)
            continue
        if response.status != 200:
            entry["reason"] = REASON_HTTP
            out.append(entry)
            continue
        body = response.body
        if body is None:
            entry["reason"] = REASON_NETWORK
            out.append(entry)
            continue
        if len(body) > MAX_BODY_BYTES:
            entry["reason"] = REASON_OVERSIZE
            out.append(entry)
            continue
        try:
            document = json.loads(body.decode("utf-8"))
        except Exception:
            entry["reason"] = REASON_MALFORMED
            out.append(entry)
            continue
        found, raw = extract_path(document, value_path)
        if not found:
            entry["reason"] = REASON_MISSING
            out.append(entry)
            continue
        ok, scaled = to_scaled(raw, scale)
        if not ok:
            entry["reason"] = REASON_BAD_VALUE
            out.append(entry)
            continue
        if time_path != "":
            found_ts, raw_ts = extract_path(document, time_path)
            if not found_ts or not isinstance(raw_ts, int) or isinstance(raw_ts, bool):
                entry["reason"] = REASON_MISSING
                out.append(entry)
                continue
            if raw_ts < 0:
                entry["reason"] = REASON_BAD_VALUE
                out.append(entry)
                continue
            entry["data_ts"] = raw_ts
        entry["ok"] = True
        entry["value"] = scaled
        entry["digest"] = digest_of(i, scaled)
        entry["reason"] = REASON_OK
        out.append(entry)
    return out


def build_proposal(
    readings: list,
    sources: list,
    tol_rel: int,
    tol_abs: int,
    max_age: int,
    quorum: int,
    read_at: int,
) -> dict:
    """Turn the leader's own reads into the proposal the committee checks."""
    values = []
    for entry in readings:
        if entry["ok"]:
            values.append(entry["value"])
    if len(values) == 0:
        # Nothing was read at all. The committee will reject this and the network will
        # rotate the leader; there is nothing sensible to publish.
        return {
            "value": 0,
            "flags": FLAG_PARTIAL,
            "agree_count": 0,
            "sources_read": 0,
            "read_at": read_at,
            "data_ts": 0,
            "readings": [],
        }

    value = lower_median(values)

    table = []
    agree = 0
    stale = False
    oldest = 0
    for entry in readings:
        if not entry["ok"]:
            continue
        agrees = within(entry["value"], value, tol_abs, tol_rel)
        if agrees:
            agree += 1
        if entry["data_ts"] != 0 and max_age > 0:
            if read_at - int(entry["data_ts"]) > max_age:
                stale = True
        if entry["data_ts"] != 0:
            if oldest == 0 or int(entry["data_ts"]) < oldest:
                oldest = int(entry["data_ts"])
        table.append(
            {
                "index": int(entry["index"]),
                "value": int(entry["value"]),
                "digest": entry["digest"],
                "data_ts": int(entry["data_ts"]),
                "agrees": bool(agrees),
            }
        )

    flags = flags_for(agree, len(table), len(sources), stale)

    return {
        "value": int(value),
        "flags": int(flags),
        "agree_count": int(agree),
        "sources_read": int(len(table)),
        "read_at": int(read_at),
        "data_ts": int(oldest),
        "readings": table,
    }


def verify_proposal(
    proposal: dict,
    mine: list,
    sources: list,
    tol_rel: int,
    tol_abs: int,
    max_age: int,
    quorum: int,
    read_at: int,
) -> bool:
    """The validator: re-derive everything and check the leader's proposal.

    Nothing the leader says is taken on trust.  Every per-source value is compared with
    the value this validator read itself, every digest with the digest of the body this
    validator read itself, and every aggregate with the aggregate this validator
    computed itself."""
    for key in (
        "value",
        "flags",
        "agree_count",
        "sources_read",
        "read_at",
        "data_ts",
        "readings",
    ):
        if key not in proposal:
            return False
    if int(proposal["read_at"]) != read_at:
        return False

    table = proposal["readings"]
    if not isinstance(table, list):
        return False

    my_by_index = {}
    for entry in mine:
        my_by_index[int(entry["index"])] = entry

    seen = []
    for entry in table:
        if not isinstance(entry, dict):
            return False
        for key in ("index", "value", "digest", "data_ts", "agrees"):
            if key not in entry:
                return False
        index = int(entry["index"])
        if index < 0 or index >= len(sources):
            return False
        if index in seen:
            return False
        seen.append(index)
        own = my_by_index.get(index)
        if own is None or not own["ok"]:
            # The leader used a source this validator could not read: the aggregate
            # cannot be re-derived, so the proposal is not verifiable.
            return False
        if not within(int(entry["value"]), int(own["value"]), tol_abs, tol_rel):
            return False
        if entry["digest"] != digest_of(index, int(own["value"])):
            # The leader reports a reading the validator did not make.
            return False
        if abs(int(entry["data_ts"]) - int(own["data_ts"])) > TS_TOL_SECONDS:
            return False

    # Recompute the aggregate from the validator's own values, over the same source
    # set the leader used.
    my_values = []
    for index in seen:
        my_values.append(int(my_by_index[index]["value"]))
    if len(my_values) == 0:
        return False
    my_median = lower_median(my_values)
    if not within(int(proposal["value"]), my_median, tol_abs, tol_rel):
        return False

    my_agree = 0
    for value in my_values:
        if within(value, my_median, tol_abs, tol_rel):
            my_agree += 1
    if int(proposal["agree_count"]) != my_agree:
        return False
    if my_agree < quorum:
        return False

    # Recompute the flags.
    my_stale = False
    my_oldest = 0
    for index in seen:
        own = my_by_index[index]
        if int(own["data_ts"]) != 0 and max_age > 0:
            if read_at - int(own["data_ts"]) > max_age:
                my_stale = True
        if int(own["data_ts"]) != 0:
            if my_oldest == 0 or int(own["data_ts"]) < my_oldest:
                my_oldest = int(own["data_ts"])
    my_flags = flags_for(my_agree, len(seen), len(sources), my_stale)
    if int(proposal["flags"]) != my_flags:
        return False
    if int(proposal["sources_read"]) != len(seen):
        return False
    if abs(int(proposal["data_ts"]) - my_oldest) > TS_TOL_SECONDS:
        return False

    return True
