"""Tests for AI seat wiring into resolve + winner attestation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from vienna.crypto import seal_for, verify_signature
from vienna.server import AI_SEATS, app


def test_resolve_runs_attached_ai_seats() -> None:
    client = TestClient(app)
    client.post(
        "/games",
        json={
            "game_id": "g_ai",
            "address_book": {"FRANCE": "0xVictor", "ENGLAND": "0xNeel"},
        },
    )

    # Attach a France AI seat with $1 deposit.
    r = client.post(
        "/games/g_ai/ai-seats",
        json={
            "power": "FRANCE",
            "player_address": "0xVictor",
            "player_signature": "0xsigfra",
            "personality": {
                "aggression": 0.9,
                "loyalty": 0.2,
                "opening": "open",
                "notes": "rush Burgundy",
            },
            "deposit_usdc": "1.00",
            "x402_signature": "0xtop",
        },
    )
    assert r.status_code == 200, r.text
    fingerprint = r.json()["personality_fingerprint"]

    # Resolve without anyone submitting human orders — France should be
    # auto-played by the seat.
    r = client.post("/games/g_ai/resolve")
    assert r.status_code == 200
    body = r.json()
    actions = body["ai_seat_actions"]
    assert any(a.get("power") == "FRANCE" for a in actions)
    france_action = next(a for a in actions if a["power"] == "FRANCE")
    assert france_action["personality_fingerprint"] == fingerprint
    assert len(france_action["orders"]) >= 1

    # Signed delta contains the personality fingerprint — this is the
    # core rubric beat for "AI seats are attested to the player's config".
    delta = body["signed_delta"]
    assert delta["payload"]["ai_seat_FRANCE_personality"] == fingerprint
    assert verify_signature(delta)


def test_winner_attestation_field_is_none_mid_game() -> None:
    client = TestClient(app)
    client.post(
        "/games",
        json={"game_id": "g_short", "address_book": {"FRANCE": "0xVictor"}},
    )
    france = seal_for(
        client.post("/games", json={"game_id": "g_short2"}).json()["enclave"][
            "encryption_public_key"
        ],
        {"power": "FRANCE", "orders": []},
    )
    # Use first game's enclave (same process, same pubkey)
    client.post("/games/g_short/orders", json={"ciphertext": france})
    r = client.post("/games/g_short/resolve")
    assert r.status_code == 200
    body = r.json()
    # Game won't end in one turn — winner_attestation should be None.
    assert body["winner_attestation"] is None


def teardown_function() -> None:
    AI_SEATS.clear()
