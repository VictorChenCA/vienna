"""
Enclave cryptographic primitives.

Two keypairs live for the lifetime of an enclave instance:

  * `encryption_keypair` (Curve25519) — clients seal-box-encrypt their
    orders to its public half. Only this enclave can decrypt.
  * `signing_keypair` (Ed25519) — every TurnDelta is signed with this
    key. Players verify against the public half pinned in the TDX quote.

In production both private halves would be:
  (a) generated *inside* the enclave at first boot, and
  (b) sealed to the enclave's measurement so only this image_digest can
      re-derive them across restarts.

For the prototype they are freshly generated on each process start.
That's fine — the demo doesn't need cross-restart key persistence and
the on-disk story would only obscure the trust diagram.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from nacl.public import Box, PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _canonical(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()


@dataclass(frozen=True)
class EnclaveIdentity:
    """Public material that a client uses to talk to the enclave.

    `image_digest` ties the keys to a specific binary — if the enclave
    were re-deployed from a different commit, the digest changes and
    clients refuse to send orders.
    """

    encryption_public_key: str  # base64 Curve25519
    signing_public_key: str  # base64 Ed25519
    image_digest: str  # e.g. "sha256:abc..."

    def to_dict(self) -> dict[str, str]:
        return {
            "encryption_public_key": self.encryption_public_key,
            "signing_public_key": self.signing_public_key,
            "image_digest": self.image_digest,
        }


class EnclaveKeys:
    """Holds the enclave's long-lived secrets."""

    def __init__(self, image_digest: str) -> None:
        self._encryption_priv = PrivateKey.generate()
        self._signing = SigningKey.generate()
        self.image_digest = image_digest

    # -- identity ----------------------------------------------------

    def identity(self) -> EnclaveIdentity:
        return EnclaveIdentity(
            encryption_public_key=_b64e(bytes(self._encryption_priv.public_key)),
            signing_public_key=_b64e(bytes(self._signing.verify_key)),
            image_digest=self.image_digest,
        )

    # -- decryption --------------------------------------------------

    def decrypt_order_envelope(self, envelope: str) -> dict:
        """Decrypt an order envelope and parse the JSON inside.

        Two envelope formats are accepted:

        1. **Sealed box** (PyNaCl / libsodium clients): base64 of the
           libsodium ``crypto_box_seal`` ciphertext. Compact, used by
           the Python `seal_for` helper.

        2. **Hybrid envelope** (browser / tweetnacl clients): a JSON
           document ``{"epk": <ephemeral pubkey b64>, "nonce": <b64>,
           "ct": <b64>}``. Equivalent to a sealed box but assembled
           manually because tweetnacl doesn't ship sealed-box.

        Returns the decoded order document. Shape is up to the caller —
        Vienna uses ``{"power": "FRANCE", "orders": ["A PAR - BUR", ...]}``.
        """
        stripped = envelope.strip()
        if stripped.startswith("{"):
            doc = json.loads(stripped)
            ephem_pub = PublicKey(_b64d(doc["epk"]))
            nonce = _b64d(doc["nonce"])
            box = Box(self._encryption_priv, ephem_pub)
            plaintext = box.decrypt(_b64d(doc["ct"]), nonce)
        else:
            box = SealedBox(self._encryption_priv)
            plaintext = box.decrypt(_b64d(envelope))
        return json.loads(plaintext.decode())

    # -- signing -----------------------------------------------------

    def sign(self, payload: dict) -> dict:
        """Sign a canonical JSON encoding of `payload` and return an
        envelope containing the original payload, the signature, the
        signer pubkey, and the image_digest the signature is bound to.
        """
        encoded = _canonical(payload)
        sig = self._signing.sign(encoded).signature
        return {
            "payload": payload,
            "signature": _b64e(sig),
            "signing_public_key": _b64e(bytes(self._signing.verify_key)),
            "image_digest": self.image_digest,
            "algorithm": "ed25519",
            "encoding": "json-canonical-sorted",
        }


def verify_signature(envelope: dict, expected_pubkey: str | None = None) -> bool:
    """Standalone verifier used by the client / CLI / tests.

    `envelope` is the dict returned by `EnclaveKeys.sign`. If
    `expected_pubkey` is provided, the signer pubkey must match (this is
    how a client pins trust to a specific enclave identity).
    """
    pubkey_b64 = envelope["signing_public_key"]
    if expected_pubkey is not None and expected_pubkey != pubkey_b64:
        return False
    verify_key = VerifyKey(_b64d(pubkey_b64))
    encoded = _canonical(envelope["payload"])
    try:
        verify_key.verify(encoded, _b64d(envelope["signature"]))
        return True
    except Exception:
        return False


def seal_for(pubkey_b64: str, payload: dict) -> str:
    """Client-side helper: seal-box-encrypt `payload` for an enclave.

    Useful for tests and the order-submission CLI.
    """
    pubkey = PublicKey(_b64d(pubkey_b64))
    box = SealedBox(pubkey)
    return _b64e(box.encrypt(_canonical(payload)))


def boot_keys() -> EnclaveKeys:
    """Construct an EnclaveKeys instance using the build-time image
    digest (passed in via the VIENNA_COMMIT env var)."""
    digest = os.environ.get("VIENNA_COMMIT", "dev-local")
    return EnclaveKeys(image_digest=digest)
