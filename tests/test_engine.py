"""Smoke test for the engine wrapper.

Confirms a 7-power game can be created, orders staged for two powers,
and a turn resolved — producing a non-empty TurnDelta with a state hash
that differs from the prior hash.

Run:
    pip install -r requirements.txt
    python -m pytest tests/ -v
"""

from __future__ import annotations

from vienna.engine import POWERS, GameEngine, state_hash


def test_create_game_returns_seven_powers() -> None:
    eng = GameEngine()
    state = eng.create_game("g1")
    assert state["phase"].startswith("S1901")
    assert state["turns_resolved"] == 0
    assert set(state["powers"]) == set(POWERS)
    # No units owned yet outside the initial home centers, but every
    # power should start with a non-empty unit list.
    for p in POWERS:
        assert len(state["units"][p]) > 0, f"{p} has no starting units"


def test_resolve_turn_advances_phase_and_records_delta() -> None:
    eng = GameEngine()
    eng.create_game("g2")

    # A pair of canonical-looking opening moves. Other powers default to
    # hold (no orders submitted = hold per upstream behaviour).
    eng.submit_orders("g2", "FRANCE", ["A PAR - BUR", "F BRE - MAO", "A MAR S A PAR - BUR"])
    eng.submit_orders("g2", "GERMANY", ["A MUN - RUH", "F KIE - DEN", "A BER - KIE"])

    delta = eng.resolve_turn("g2")
    assert delta.turn_number == 1
    assert delta.phase_from != delta.phase_to
    assert delta.prior_state_hash != delta.new_state_hash
    assert "FRANCE" in delta.orders_revealed
    assert "GERMANY" in delta.orders_revealed
    assert delta.is_done is False
    assert delta.winner is None


def test_state_hash_is_deterministic() -> None:
    eng_a = GameEngine()
    eng_b = GameEngine()
    eng_a.create_game("a")
    eng_b.create_game("b")
    # Fresh games on the same map must hash identically — this is the
    # core property that makes turn deltas replay-verifiable.
    assert state_hash(eng_a._record("a").game.get_state()) == state_hash(
        eng_b._record("b").game.get_state()
    )


def test_unknown_game_raises() -> None:
    import pytest

    from vienna.engine import UnknownGame

    eng = GameEngine()
    with pytest.raises(UnknownGame):
        eng.get_state("nope")


def test_signing_payload_excludes_full_state() -> None:
    eng = GameEngine()
    eng.create_game("g3")
    eng.submit_orders("g3", "ENGLAND", ["F LON - NTH", "F EDI - NWG", "A LVP - YOR"])
    delta = eng.resolve_turn("g3")
    payload = delta.signing_payload()
    assert "new_state" not in payload
    assert payload["new_state_hash"] == delta.new_state_hash
    assert payload["orders_revealed"]["ENGLAND"] == [
        "F LON - NTH",
        "F EDI - NWG",
        "A LVP - YOR",
    ]
