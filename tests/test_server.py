"""End-to-end test of the FastAPI surface.

Walks through:
  1. create game
  2. submit sealed-box-encrypted orders for two powers
  3. resolve the turn
  4. fetch the signed delta
  5. verify the signature against the enclave's published pubkey
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from vienna.crypto import seal_for, verify_signature
from vienna.server import app


def test_full_turn_round_trip() -> None:
    client = TestClient(app)

    # 1. Create a game
    r = client.post("/games", json={"game_id": "demo"})
    assert r.status_code == 200, r.text
    body = r.json()
    enclave_enc_pub = body["enclave"]["encryption_public_key"]
    enclave_sig_pub = body["enclave"]["signing_public_key"]
    assert body["state"]["phase"].startswith("S1901")

    # 2. Submit encrypted orders for two powers
    france = seal_for(
        enclave_enc_pub,
        {"power": "FRANCE", "orders": ["A PAR - BUR", "F BRE - MAO", "A MAR S A PAR - BUR"]},
    )
    germany = seal_for(
        enclave_enc_pub,
        {"power": "GERMANY", "orders": ["A MUN - RUH", "F KIE - DEN", "A BER - KIE"]},
    )
    r = client.post("/games/demo/orders", json={"ciphertext": france})
    assert r.status_code == 200, r.text
    receipt = r.json()["receipt"]
    assert verify_signature(receipt, expected_pubkey=enclave_sig_pub)

    r = client.post("/games/demo/orders", json={"ciphertext": germany})
    assert r.status_code == 200, r.text

    # 3. Resolve
    r = client.post("/games/demo/resolve")
    assert r.status_code == 200, r.text
    body = r.json()
    signed = body["signed_delta"]
    assert verify_signature(signed, expected_pubkey=enclave_sig_pub)

    # 4. Fetch turn 1 and re-verify
    r = client.get("/games/demo/turns/1")
    assert r.status_code == 200
    fetched = r.json()["signed_delta"]
    assert verify_signature(fetched, expected_pubkey=enclave_sig_pub)
    assert fetched["payload"]["orders_revealed"]["FRANCE"][0] == "A PAR - BUR"


def test_unknown_game_returns_404() -> None:
    client = TestClient(app)
    r = client.get("/games/nope/state")
    assert r.status_code == 404


def test_garbage_ciphertext_rejected() -> None:
    client = TestClient(app)
    client.post("/games", json={"game_id": "g_garbage"})
    r = client.post("/games/g_garbage/orders", json={"ciphertext": "!!!not-base64!!!"})
    assert r.status_code == 400
    assert "decryption failed" in r.json()["detail"]


def test_verify_and_attestation_endpoints() -> None:
    client = TestClient(app)
    r = client.get("/verify")
    assert r.status_code == 200
    assert "image_digest" in r.json()
    assert "commit_sha" in r.json()

    r = client.get("/attestation")
    assert r.status_code == 200
    enclave = r.json()["enclave"]
    assert "encryption_public_key" in enclave
    assert "signing_public_key" in enclave
    assert "image_digest" in enclave


def test_healthz() -> None:
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
