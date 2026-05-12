"""Tests for the AI seat module — personality fingerprinting, ledger
accounting, and the deterministic stub order picker."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vienna.ai_seat import AISeat, InsufficientFunds, Personality, SeatLedger


def test_personality_fingerprint_is_stable() -> None:
    a = Personality(aggression=0.8, loyalty=0.3, opening="Lepanto", notes="risky")
    b = Personality(aggression=0.8, loyalty=0.3, opening="Lepanto", notes="risky")
    assert a.fingerprint() == b.fingerprint()


def test_personality_fingerprint_differs() -> None:
    a = Personality(aggression=0.8)
    b = Personality(aggression=0.7)
    assert a.fingerprint() != b.fingerprint()


def test_ledger_topup_and_debit() -> None:
    led = SeatLedger()
    led.topup(Decimal("1.00"), x402_signature="0xdead")
    assert led.balance == Decimal("1.00")
    led.debit(Decimal("0.02"))
    assert led.balance == Decimal("0.98")


def test_ledger_insufficient_funds() -> None:
    led = SeatLedger()
    led.topup(Decimal("0.01"), x402_signature="0xdead")
    with pytest.raises(InsufficientFunds):
        led.debit(Decimal("0.02"))


def test_seat_attestation_fields_present() -> None:
    seat = AISeat(
        game_id="g",
        power="FRANCE",
        player_address="0xvictor",
        personality=Personality(aggression=0.9),
        player_signature="0xsig",
    )
    fields = seat.attestation_fields()
    assert fields["ai_seat_FRANCE_player"] == "0xvictor"
    assert "personality" in next(k for k in fields if "personality" in k)


def test_stub_order_picker_returns_legal_orders() -> None:
    seat = AISeat(
        game_id="g",
        power="FRANCE",
        player_address="0xv",
        personality=Personality(aggression=0.9, opening="open"),
        player_signature="0xsig",
    )
    seat.ledger.topup(Decimal("1.00"), x402_signature="0xt")

    orderable = ["PAR", "BRE", "MAR"]
    possible = {
        "PAR": ["A PAR H", "A PAR - BUR", "A PAR - GAS"],
        "BRE": ["F BRE H", "F BRE - MAO", "F BRE - ENG"],
        "MAR": ["A MAR H", "A MAR - BUR", "A MAR - SPA"],
    }
    out = seat.choose_orders(
        game_state={"phase": "S1901M", "your_units": ["A PAR", "F BRE", "A MAR"]},
        orderable_locations=orderable,
        possible_orders=possible,
    )
    assert len(out) == 3
    legal = {o for opts in possible.values() for o in opts}
    for order in out:
        assert order in legal
