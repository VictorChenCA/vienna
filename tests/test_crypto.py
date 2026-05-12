"""Round-trip tests for the enclave crypto module."""

from __future__ import annotations

from vienna.crypto import EnclaveKeys, seal_for, verify_signature


def test_round_trip_sealed_box() -> None:
    keys = EnclaveKeys(image_digest="sha256:test")
    enclave_pub = keys.identity().encryption_public_key

    orders = {"power": "FRANCE", "orders": ["A PAR - BUR", "F BRE - MAO"]}
    envelope = seal_for(enclave_pub, orders)
    decrypted = keys.decrypt_order_envelope(envelope)
    assert decrypted == orders


def test_signature_verifies() -> None:
    keys = EnclaveKeys(image_digest="sha256:test")
    payload = {"turn": 1, "phase_to": "F1901M", "new_state_hash": "abc"}
    signed = keys.sign(payload)
    assert verify_signature(signed)


def test_signature_fails_on_tamper() -> None:
    keys = EnclaveKeys(image_digest="sha256:test")
    signed = keys.sign({"turn": 1})
    signed["payload"]["turn"] = 2  # tamper after signing
    assert not verify_signature(signed)


def test_pubkey_pinning() -> None:
    a = EnclaveKeys(image_digest="sha256:a")
    b = EnclaveKeys(image_digest="sha256:b")
    signed = a.sign({"turn": 1})
    a_pub = a.identity().signing_public_key
    b_pub = b.identity().signing_public_key
    assert verify_signature(signed, expected_pubkey=a_pub)
    # Client pinned to enclave B refuses A's signature.
    assert not verify_signature(signed, expected_pubkey=b_pub)


def test_different_enclaves_have_different_keys() -> None:
    a = EnclaveKeys(image_digest="sha256:x")
    b = EnclaveKeys(image_digest="sha256:x")  # same digest, different boot
    assert a.identity().signing_public_key != b.identity().signing_public_key
    assert a.identity().encryption_public_key != b.identity().encryption_public_key
