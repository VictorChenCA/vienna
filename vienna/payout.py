"""
Game-end programmatic payout.

When `Game.is_game_done` is true the enclave signs a small attestation:

    {
      "kind": "vienna_winner_attestation",
      "game_id": ...,
      "winner_power": "FRANCE",
      "winner_address": "0x...",
      "final_state_hash": "...",
      "image_digest": "sha256:..."
    }

Anyone can submit this attestation to a Solidity escrow that:
  1. recovers the signer pubkey,
  2. checks it matches a pubkey pinned via TDX quote, and
  3. releases the pot to `winner_address`.

For the prototype we just produce the signed payload — wiring it to a
testnet contract is a swap-in of `viem` + an ABI in a few lines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from vienna.crypto import EnclaveKeys
from vienna.engine import TurnDelta


@dataclass
class WinnerAttestation:
    """The signed payload that triggers escrow release."""

    game_id: str
    winner_power: str
    winner_address: str
    final_state_hash: str

    def to_payload(self) -> dict:
        return {
            "kind": "vienna_winner_attestation",
            "game_id": self.game_id,
            "winner_power": self.winner_power,
            "winner_address": self.winner_address,
            "final_state_hash": self.final_state_hash,
        }


def winner_attestation(
    last_turn: TurnDelta,
    address_book: Mapping[str, str],
    keys: EnclaveKeys,
) -> dict | None:
    """If the game has ended on `last_turn`, return a signed winner
    attestation. Returns None otherwise.

    `address_book` maps POWER -> wallet address (provided at game
    creation, sealed to the enclave). A draw (multiple winners) is
    flagged as a draw payload; the escrow contract handles the split.
    """
    if not last_turn.is_done or not last_turn.winner:
        return None

    winners = last_turn.winner.split(",")
    if len(winners) == 1:
        winner_addr = address_book.get(winners[0])
        if not winner_addr:
            raise KeyError(f"no escrow address for winner {winners[0]}")
        att = WinnerAttestation(
            game_id=last_turn.game_id,
            winner_power=winners[0],
            winner_address=winner_addr,
            final_state_hash=last_turn.new_state_hash,
        )
        return keys.sign(att.to_payload())

    # Multi-power draw: emit a different payload shape the contract
    # interprets as "split the pot evenly across these addresses".
    draw_payload = {
        "kind": "vienna_draw_attestation",
        "game_id": last_turn.game_id,
        "winners": [
            {"power": p, "address": address_book[p]}
            for p in winners
            if p in address_book
        ],
        "final_state_hash": last_turn.new_state_hash,
    }
    return keys.sign(draw_payload)
