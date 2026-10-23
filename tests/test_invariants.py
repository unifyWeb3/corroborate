"""The Stage 3 invariant tests.

These are written before the fix: run them against tag `rejected-v1` and they fail
because that contract stores a point `value` and has no `compare`.  Run them after
the fix and they pin the canonical / invariant-preserving rule.

Nothing here is skipped.  A refusal assertion is always preceded by a positive
control on the same honest path.
"""

import itertools
import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import body, mock, source, sources_json, three_sources, two_sources  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FEED = "btc-usd"


def deploy(direct_deploy):
    return direct_deploy("contracts/corroborate.py")


def rules():
    """The pure rule functions of the contract under test (loaded by direct_deploy)."""
    module = sys.modules.get("_contract_corroborate")
    assert module is not None, "deploy a contract before reading its rules"
    return module


def register(
    contract,
    feed_id=FEED,
    sources=None,
    tol_rel_bps=0,
    tol_abs=20,
    max_age=0,
    quorum=2,
):
    contract.register_feed(
        feed_id,
        "Bitcoin USD",
        "USD",
        tol_rel_bps,
        tol_abs,
        max_age,
        quorum,
        sources if sources is not None else two_sources(),
    )


def proposal(record):
    """The leader proposal for the new record shape (no point value)."""
    return {
        "lo": record["lo"],
        "hi": record["hi"],
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


def value_of(record):
    """A complete record must have collapsed to one exact value."""
    assert record["lo"] == record["hi"], record
    return record["lo"]


# ------------------------------------------------------- 1. the v1 threshold flip


def test_the_v1_threshold_flip_is_closed(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    assert hasattr(contract, "compare"), "the point value must be replaced by compare"
    register(contract)
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1020))

    contract.refresh(FEED)
    record = contract.latest(FEED)
    assert value_of(record) == 1000

    honest = proposal(record)
    assert direct_vm.run_validator(leader_result=honest) is True  # positive control

    # This is exactly what the rejected contract accepted: the lower median is
    # 1000, but a leader could store 1010 within tol_abs=20.
    shifted = proposal(record)
    shifted["lo"] = 1010
    shifted["hi"] = 1010
    assert direct_vm.run_validator(leader_result=shifted) is False

    # The consumer threshold 1005: the honest reading is 1000 (BELOW), the shifted
    # value would have been 1010 (ABOVE).  The stored result may never say ABOVE.
    assert contract.compare(FEED, 1005) == "BELOW"
    assert contract.compare(FEED, 1004) == "BELOW"
    assert contract.compare(FEED, 1000) == "ABOVE"
    assert contract.compare(FEED, 1001) == "BELOW"


def test_a_non_integer_band_is_rejected(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    register(contract)
    mock(direct_vm, r"one\.example\.test", body(100))
    mock(direct_vm, r"two\.example\.test", body(101))
    contract.refresh(FEED)
    record = contract.latest(FEED)

    assert direct_vm.run_validator(leader_result=proposal(record)) is True  # control

    truncated = proposal(record)
    # int(100.5) == 100 would otherwise compare equal to the exact median.
    truncated["lo"] = 100.5
    truncated["hi"] = 100.5
    assert direct_vm.run_validator(leader_result=truncated) is False


def test_a_tampered_half_band_is_rejected(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    register(contract)
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1020))
    contract.refresh(FEED)
    record = contract.latest(FEED)

    honest = proposal(record)
    assert direct_vm.run_validator(leader_result=honest) is True  # positive control

    asymmetric = proposal(record)
    asymmetric["lo"] = 1000
    asymmetric["hi"] = 1005
    assert direct_vm.run_validator(leader_result=asymmetric) is False


# ------------------------------------------------- 2. the property / exhaustive run


def _validator_readings(rules_mod, leader, tol_abs, tol_rel, rng=None, big=False):
    """Valid values for the reported sources, each within the declared tolerance."""
    out = []
    for value in leader:
        if big:
            deltas = [0, 1, -1, 17, -17]
        else:
            deltas = [0, 1, -1, 2, -2]
        choices = [value]
        for delta in deltas:
            candidate = value + delta
            if candidate != value and rules_mod.within(candidate, value, tol_abs, tol_rel):
                choices.append(candidate)
        out.append(choices)
    return itertools.product(*out)


def test_the_invariant_holds_exhaustively(direct_vm, direct_deploy):
    deploy(direct_deploy)
    rules_mod = rules()
    assert hasattr(rules_mod, "relation")
    assert hasattr(rules_mod, "band")
    assert hasattr(rules_mod, "tolerance_bound")

    cases = 0
    bound_cases = 0
    for tol_rel, tol_abs in [(0, 0), (0, 3), (500, 0), (1000, 2), (2000, 1)]:
        for size in (1, 2, 3):
            for leader_tuple in itertools.product(range(-2, 3), repeat=size):
                leader = list(leader_tuple)

                # Lemma: any reader whose values are each within the tolerance can
                # move the median by at most B.  This is why B is the band half-width.
                magnitude = max(abs(v) for v in leader)
                bound = rules_mod.tolerance_bound(magnitude, tol_abs, tol_rel)
                for readings in _validator_readings(
                    rules_mod, leader, tol_abs, tol_rel
                ):
                    shifted = list(readings)
                    moved = abs(
                        rules_mod.lower_median(shifted)
                        - rules_mod.lower_median(leader)
                    )
                    assert moved <= bound, (leader, shifted, moved, bound)
                    bound_cases += 1

                # Complete reads are canonical: the validator's exact readings are
                # the leader's readings (the digest binds them), so lo == hi == median.
                lo, hi = rules_mod.band(leader, True, tol_abs, tol_rel)
                assert lo == hi == rules_mod.lower_median(leader)
                for threshold in range(-6, 7):
                    relation = rules_mod.relation(lo, hi, threshold)
                    own = (
                        "ABOVE"
                        if rules_mod.lower_median(leader) >= threshold
                        else "BELOW"
                    )
                    assert relation == own, (leader, threshold, relation, own)
                    cases += 1

                # Partial reads: the omitted sources the validator can read must be
                # inside the published band; the full-set median must be inside too.
                lo, hi = rules_mod.band(leader, False, tol_abs, tol_rel)
                assert lo <= hi
                for omitted in range(-6, 7):
                    if not (lo <= omitted <= hi):
                        continue
                    bigger = leader + [omitted]
                    if not (lo <= rules_mod.lower_median(bigger) <= hi):
                        continue
                    for threshold in range(-8, 9):
                        relation = rules_mod.relation(lo, hi, threshold)
                        own = (
                            "ABOVE"
                            if rules_mod.lower_median(bigger) >= threshold
                            else "BELOW"
                        )
                        assert relation == own or relation == "INDETERMINATE", (
                            leader,
                            bigger,
                            threshold,
                            relation,
                            own,
                        )
                        cases += 1

    assert bound_cases > 5_000, bound_cases
    assert cases > 20_000, cases
    print("exhaustive invariant cases:", cases, "tolerance-bound cases:", bound_cases)


def test_the_invariant_holds_on_randomised_large_values(direct_vm, direct_deploy):
    deploy(direct_deploy)
    rules_mod = rules()
    rng = random.Random(20261023)
    cases = 0

    for _ in range(1500):
        size = rng.randint(1, 6)
        leader = [rng.randint(-1_000_000, 1_000_000) for _ in range(size)]
        tol_rel = rng.choice([0, 1, 50, 500, 1000, 2000])
        tol_abs = rng.choice([0, 1, 7, 100, 10_000])
        complete = rng.choice([True, False])
        lo, hi = rules_mod.band(leader, complete, tol_abs, tol_rel)
        assert lo <= hi

        full = list(leader)
        for _ in range(rng.randint(0, 3)):
            if complete:
                break
            candidate = rng.randint(lo, hi)
            full.append(candidate)
        if complete:
            assert lo == hi
        if not (lo <= rules_mod.lower_median(full) <= hi):
            continue  # this validator would reject the proposal

        own = rules_mod.lower_median(full)
        for _ in range(20):
            threshold = own + rng.randint(-5, 5)
            relation = rules_mod.relation(lo, hi, threshold)
            expected = "ABOVE" if own >= threshold else "BELOW"
            assert relation == expected or relation == "INDETERMINATE", (
                leader,
                full,
                threshold,
                relation,
                expected,
            )
            cases += 1

    assert cases > 20_000, cases
    print("randomised invariant cases:", cases)


# ----------------------------------------- 3. the validator rejects a bad aggregate


def _partial_two_of_three(direct_vm, contract, feed_id=FEED, omitted_value=1030):
    """Leader reads 0/1; source 2 fails for the leader and succeeds for the validator."""
    register(
        contract,
        feed_id=feed_id,
        sources=three_sources(),
        tol_rel_bps=0,
        tol_abs=30,
        quorum=2,
    )
    direct_vm.clear_mocks()
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1005))
    mock(direct_vm, r"three\.example\.test", "down", status=503)

    contract.refresh(feed_id)
    record = contract.latest(feed_id)
    assert record["sources_read"] == 2
    assert record["sources_declared"] == 3
    assert record["status"] == "partial"

    # The validator can read the third source.  Mocks are first-match, so the
    # leader's 503 must be cleared, not shadowed.
    direct_vm.clear_mocks()
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1005))
    mock(direct_vm, r"three\.example\.test", body(omitted_value))
    return record


def test_a_partial_band_must_be_exact(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    record = _partial_two_of_three(direct_vm, contract)
    assert (record["lo"], record["hi"]) == (970, 1030)

    honest = proposal(record)
    assert direct_vm.run_validator(leader_result=honest) is True  # positive control
    assert contract.compare(FEED, 1003) == "INDETERMINATE"
    assert contract.compare(FEED, 969) == "ABOVE"
    assert contract.compare(FEED, 1030) == "INDETERMINATE"
    assert contract.compare(FEED, 1031) == "BELOW"

    shifted = proposal(record)
    shifted["lo"] = 971
    shifted["hi"] = 1031
    assert direct_vm.run_validator(leader_result=shifted) is False

    narrowed = proposal(record)
    narrowed["lo"] = 970
    narrowed["hi"] = 1020
    assert direct_vm.run_validator(leader_result=narrowed) is False

    inverted = proposal(record)
    inverted["lo"] = 1030
    inverted["hi"] = 970
    assert direct_vm.run_validator(leader_result=inverted) is False

    liar = proposal(record)
    liar["sources_read"] = 3
    assert direct_vm.run_validator(leader_result=liar) is False


def test_a_partial_band_rejects_a_hidden_outlier(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)

    # positive control on a second feed: omitted 1030 is inside its band
    control = _partial_two_of_three(
        direct_vm, contract, feed_id="control", omitted_value=1030
    )
    assert direct_vm.run_validator(leader_result=proposal(control)) is True

    record = _partial_two_of_three(
        direct_vm, contract, feed_id="hidden", omitted_value=1200
    )
    # 1200 is outside the honest band [970, 1030], so the leader's omission is a
    # hidden dissent (and the full median is outside too).
    assert direct_vm.run_validator(leader_result=proposal(record)) is False


# ----------------------------------------------------------- 4. boundaries and extremes


def test_compare_boundaries(direct_vm, direct_deploy):
    deploy(direct_deploy)
    rules_mod = rules()
    relation = rules_mod.relation
    assert relation(100, 100, 99) == "ABOVE"
    assert relation(100, 100, 100) == "ABOVE"
    assert relation(100, 100, 101) == "BELOW"
    assert relation(90, 110, 89) == "ABOVE"
    assert relation(90, 110, 90) == "ABOVE"
    assert relation(90, 110, 91) == "INDETERMINATE"
    assert relation(90, 110, 110) == "INDETERMINATE"
    assert relation(90, 110, 111) == "BELOW"


def test_tolerance_bound_is_an_exact_integer(direct_vm, direct_deploy):
    deploy(direct_deploy)
    rules_mod = rules()
    bound = rules_mod.tolerance_bound
    assert bound(0, 0, 0) == 0
    assert bound(0, 7, 0) == 7
    assert bound(1000, 0, 100) == 11  # ceil(100 * 1000 / 9900)
    assert bound(1_000_000_000, 0, 2000) == 250_000_000
    assert bound(1000, 3, 100) == 11
    assert bound(1, 0, 1) == 1  # ceil(1 / 9999)


def test_negative_values(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    register(contract, tol_abs=20)
    mock(direct_vm, r"one\.example\.test", body(-1000))
    mock(direct_vm, r"two\.example\.test", body(-1020))
    contract.refresh(FEED)
    record = contract.latest(FEED)
    assert value_of(record) == -1020
    assert contract.compare(FEED, -1021) == "ABOVE"
    assert contract.compare(FEED, -1020) == "ABOVE"
    assert contract.compare(FEED, -1019) == "BELOW"

    rules_mod = rules()
    assert rules_mod.band([-1000, -1020], False, 20, 0) == (-1040, -1000)


def test_zero_tolerance_partial_band_is_a_point(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    register(contract, tol_rel_bps=0, tol_abs=0, quorum=1)
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", "down", status=503)
    contract.refresh(FEED)
    record = contract.latest(FEED)
    assert (record["lo"], record["hi"]) == (1000, 1000)

    direct_vm.clear_mocks()
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1000))
    assert direct_vm.run_validator(leader_result=proposal(record)) is True
    assert contract.compare(FEED, 1000) == "ABOVE"
    assert contract.compare(FEED, 1001) == "BELOW"


def test_zero_tolerance_partial_band_refuses_a_different_source(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    register(contract, tol_rel_bps=0, tol_abs=0, quorum=1)
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", "down", status=503)
    contract.refresh(FEED)
    record = contract.latest(FEED)

    direct_vm.clear_mocks()
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1001))
    assert direct_vm.run_validator(leader_result=proposal(record)) is False

    # positive control: the equal source is accepted
    direct_vm.clear_mocks()
    mock(direct_vm, r"one\.example\.test", body(1000))
    mock(direct_vm, r"two\.example\.test", body(1000))
    assert direct_vm.run_validator(leader_result=proposal(record)) is True


def test_extreme_values_stay_inside_i64(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    rules_mod = rules()
    assert hasattr(rules_mod, "MAX_TOL_ABS")
    top = rules_mod.MAX_VALUE
    lo, hi = rules_mod.band([top], False, 0, 2000)
    assert lo >= -(2**63)
    assert hi <= 2**63 - 1
    assert hi == top + rules_mod.tolerance_bound(top, 0, 2000)

    register(contract, tol_rel_bps=0, tol_abs=20)
    mock(direct_vm, r"one\.example\.test", body(top))
    mock(direct_vm, r"two\.example\.test", body(top))
    contract.refresh(FEED)
    assert value_of(contract.latest(FEED)) == top


def test_registration_tolerance_boundaries(direct_vm, direct_deploy):
    contract = deploy(direct_deploy)
    rules_mod = rules()
    assert hasattr(rules_mod, "MAX_TOL_ABS")

    register(contract, feed_id="rel-ok", tol_rel_bps=2000)
    register(contract, feed_id="abs-ok", tol_abs=rules_mod.MAX_TOL_ABS)

    with direct_vm.expect_revert("invalid tolerance"):
        register(contract, feed_id="rel-bad", tol_rel_bps=2001)
    with direct_vm.expect_revert("invalid tolerance"):
        register(contract, feed_id="abs-bad", tol_abs=rules_mod.MAX_TOL_ABS + 1)

    # controls: the two accepted registrations are both readable
    assert contract.feed("rel-ok")["tol_rel_bps"] == 2000
    assert contract.feed("abs-ok")["tol_abs"] == rules_mod.MAX_TOL_ABS


# ------------------------------------------------ 5. the pinned SDK import surface


def _run_sdk_check(contract_path):
    env = dict(os.environ)
    env["GENVM_VERSION"] = "v0.6.0-rc10"
    return subprocess.run(
        [sys.executable, "scripts/check_sdk_surface.py", str(contract_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )


def test_every_import_and_api_exists_in_the_pinned_sdk():
    for path in ("contracts/corroborate.py", "contracts/example_consumer.py"):
        result = _run_sdk_check(path)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "names checked against the pinned SDK; all present" in result.stdout


def test_the_sdk_check_rejects_a_missing_api(tmp_path):
    source_text = (ROOT / "contracts/example_consumer.py").read_text()
    broken = source_text.replace("gl.contract.get_at", "gl.contract.get_att")
    assert broken != source_text
    path = tmp_path / "broken_consumer.py"
    path.write_text(broken)

    result = _run_sdk_check(path)
    assert result.returncode != 0
    assert "get_att" in result.stderr
