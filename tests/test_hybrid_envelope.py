"""Round-trip the hybrid envelope format used by the browser UI."""

from __future__ import annotations

import base64
import json

from nacl.public import Box, PrivateKey, PublicKey

from vienna.crypto import EnclaveKeys


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def test_hybrid_envelope_round_trip() -> None:
    keys = EnclaveKeys(image_digest="sha256:hyb")
    recipient_pub = PublicKey(
        base64.b64decode(keys.identity().encryption_public_key)
    )

    # Simulate the browser path: ephemeral keypair, random nonce, Box.
    ephem = PrivateKey.generate()
    nonce = b"\x07" * 24
    msg = json.dumps({"power": "ITALY", "orders": ["A VEN - TYR"]}).encode()
    ct = Box(ephem, recipient_pub).encrypt(msg, nonce).ciphertext

    envelope = json.dumps(
        {
            "epk": _b64(bytes(ephem.public_key)),
            "nonce": _b64(nonce),
            "ct": _b64(ct),
        }
    )
    decoded = keys.decrypt_order_envelope(envelope)
    assert decoded == {"power": "ITALY", "orders": ["A VEN - TYR"]}
