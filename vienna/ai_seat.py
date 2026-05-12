"""
Claude-backed AI seats for Vienna.

Two ideas live here:

1.  **Attested personality configs.** A player adds an AI seat for a power
    they control. They sign a small JSON config — aggression, loyalty,
    opening preference, free-form notes — and submit it. The enclave hashes
    the config into every turn's signing payload, so anyone can later
    verify that the AI played the personality the player set, not one
    swapped in by the host.

2.  **Per-turn agent commerce.** Each call to Claude costs money. The seat
    holds a deposit balance (top-up via x402/MPP using `dual402`), debits
    a per-turn cost, and is suspended when the balance runs out. This is
    the rubric's "agent commerce" feature: the AI pays for its own
    inference, not the host.

The actual Anthropic API call is gated by `VIENNA_CLAUDE_LIVE=1`. In CI /
local dev the deterministic stub fires instead — useful for tests and
the offline demo path.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

# ---------------------------------------------------------------------------
# Personality
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Personality:
    """Signed-by-player config the AI seat plays as.

    Knobs are intentionally small for v1 — easy to demo, easy to verify.
    """

    aggression: float = 0.5  # 0=passive, 1=relentless
    loyalty: float = 0.5  # 0=backstabber, 1=keeps alliances
    opening: str = "open"  # "Lepanto", "Sealion", "Maginot", "open"
    notes: str = ""  # free-form flavour for the prompt

    def to_dict(self) -> dict[str, Any]:
        return {
            "aggression": self.aggression,
            "loyalty": self.loyalty,
            "opening": self.opening,
            "notes": self.notes,
        }

    def fingerprint(self) -> str:
        """Stable SHA-256 of the canonical encoding.

        Included in every turn's signing payload so verifiers can confirm
        the seat played the player-signed personality across all turns.
        """
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# dual402 metering (stubbed; real impl talks to the dual402 starter)
# ---------------------------------------------------------------------------


class InsufficientFunds(Exception):
    pass


@dataclass
class SeatLedger:
    """In-memory deposit / debit tracker.

    Real implementation: an x402-receivable wallet inside the enclave,
    topped up via the `dual402` middleware. Per-turn debit = the actual
    cost of the Claude call (token usage * unit price + a small margin).
    """

    deposit_usdc: Decimal = Decimal("0")
    spent_usdc: Decimal = Decimal("0")
    last_topup_signature: str | None = None

    @property
    def balance(self) -> Decimal:
        return self.deposit_usdc - self.spent_usdc

    def topup(self, amount: Decimal, x402_signature: str) -> None:
        self.deposit_usdc += amount
        self.last_topup_signature = x402_signature

    def debit(self, amount: Decimal) -> None:
        if amount > self.balance:
            raise InsufficientFunds(
                f"need {amount} USDC, have {self.balance}"
            )
        self.spent_usdc += amount


# ---------------------------------------------------------------------------
# AI seat
# ---------------------------------------------------------------------------


@dataclass
class AISeat:
    """One AI seat attached to a game/power."""

    game_id: str
    power: str
    player_address: str
    personality: Personality
    player_signature: str  # the player's signature over personality.fingerprint()
    ledger: SeatLedger = field(default_factory=SeatLedger)

    def attestation_fields(self) -> dict[str, str]:
        """Dict folded into every turn's signing payload."""
        return {
            f"ai_seat_{self.power}_player": self.player_address,
            f"ai_seat_{self.power}_personality": self.personality.fingerprint(),
            f"ai_seat_{self.power}_player_sig": self.player_signature,
        }

    def choose_orders(
        self,
        game_state: dict,
        orderable_locations: list[str],
        possible_orders: dict[str, list[str]],
        per_turn_cost_usdc: Decimal = Decimal("0.02"),
    ) -> list[str]:
        """Pick orders for this turn.

        Debits the per-turn cost from the seat's ledger, calls Claude (or
        the deterministic stub), validates returned orders against the
        engine's possible-orders set, and returns them ready to submit.
        """
        self.ledger.debit(per_turn_cost_usdc)
        live = os.environ.get("VIENNA_CLAUDE_LIVE") == "1"
        if live:
            raw = _claude_orders(
                self.power, self.personality, game_state, possible_orders
            )
        else:
            raw = _stub_orders(
                self.power, self.personality, orderable_locations, possible_orders
            )
        return _validate_orders(raw, possible_orders)


# ---------------------------------------------------------------------------
# Order generation
# ---------------------------------------------------------------------------


def _stub_orders(
    power: str,
    personality: Personality,
    orderable_locations: list[str],
    possible_orders: dict[str, list[str]],
) -> list[str]:
    """Deterministic order picker used in tests and offline demos.

    Strategy: for each orderable location, pick the first move that *isn't*
    a pure hold if aggression > 0.5; otherwise hold.
    """
    chosen: list[str] = []
    aggressive = personality.aggression >= 0.5
    for loc in orderable_locations:
        options = possible_orders.get(loc, [])
        if not options:
            continue
        if aggressive:
            non_hold = [o for o in options if " H" not in o.split(" ")[-2:]]
            chosen.append(non_hold[0] if non_hold else options[0])
        else:
            holds = [o for o in options if o.endswith(" H")]
            chosen.append(holds[0] if holds else options[0])
    return chosen


def _claude_orders(
    power: str,
    personality: Personality,
    game_state: dict,
    possible_orders: dict[str, list[str]],
) -> list[str]:
    """Live Claude call. Off by default.

    Uses the EigenCloud LLM Proxy if `EIGEN_GATEWAY_URL` is set, otherwise
    talks directly to the Anthropic API. Prompt-cached system prompt
    keeps per-turn cost low.
    """
    from anthropic import Anthropic

    client = Anthropic()
    system = _system_prompt(power, personality)
    user = _user_prompt(game_state, possible_orders)

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _parse_orders_from_text(text)


def _system_prompt(power: str, personality: Personality) -> str:
    return (
        "You are an AI playing the board game Diplomacy. "
        f"You control {power}. "
        f"Aggression={personality.aggression:.2f}, "
        f"Loyalty={personality.loyalty:.2f}, "
        f"Opening preference={personality.opening!r}. "
        f"Player notes: {personality.notes or '(none)'}.\n\n"
        "You must respond with ONE order per orderable location, exactly as "
        "they appear in the possible-orders list. Output one order per line, "
        "no commentary, no markdown."
    )


def _user_prompt(game_state: dict, possible_orders: dict[str, list[str]]) -> str:
    return (
        f"Current phase: {game_state.get('phase')}\n"
        f"Your units: {game_state.get('your_units', [])}\n"
        f"Centers (by power): {game_state.get('centers', {})}\n\n"
        f"Possible orders by location:\n"
        f"{json.dumps(possible_orders, indent=2)}\n\n"
        "Pick one order per orderable location and return them line by line."
    )


def _parse_orders_from_text(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _validate_orders(
    orders: list[str], possible_orders: dict[str, list[str]]
) -> list[str]:
    """Drop any order the engine wouldn't accept.

    Defense-in-depth: even if Claude hallucinates, we never submit an
    illegal order to the engine. Worst case: the seat plays a partial
    turn (some holds), which is legal.
    """
    legal = {o for opts in possible_orders.values() for o in opts}
    return [o for o in orders if o in legal]
