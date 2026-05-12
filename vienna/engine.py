"""
Thin wrapper around `diplomacy.Game` (the DATC-compliant reference engine).

We do not modify the upstream engine — Vienna's attestation surface is this
file plus the FastAPI server. DATC compliance is inherited verbatim from
the `diplomacy` package pinned in requirements.txt.

The public surface is deliberately small:

    eng = GameEngine()
    eng.create_game("game-abc")
    eng.submit_orders("game-abc", "FRANCE", ["A PAR - BUR", "F BRE - MAO"])
    delta = eng.resolve_turn("game-abc")
    state = eng.get_state("game-abc")
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from diplomacy import Game

POWERS = ("AUSTRIA", "ENGLAND", "FRANCE", "GERMANY", "ITALY", "RUSSIA", "TURKEY")


# `diplomacy.Game.get_state()` includes a microsecond `timestamp` field
# that breaks deterministic hashing. Strip non-game-state fields before
# canonicalising. `zobrist_hash` is engine-internal and already covered
# by the data we keep, but we keep it for cross-checking.
_VOLATILE_STATE_KEYS = frozenset({"timestamp"})


def canonical_state(state: dict) -> dict:
    """Return a state dict suitable for hashing/signing."""
    return {k: v for k, v in state.items() if k not in _VOLATILE_STATE_KEYS}


def canonical_json(obj: Any) -> bytes:
    """Deterministic JSON encoding for hashing/signing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def state_hash(state: dict) -> str:
    """SHA-256 of the canonical state encoding. Two enclaves running the
    same binary on the same inputs must produce byte-identical output."""
    return hashlib.sha256(canonical_json(canonical_state(state))).hexdigest()


@dataclass
class TurnDelta:
    """One resolved turn — the unit that gets signed and published.

    Replaying (prior_state_hash, orders_revealed) through the same pinned
    engine version must yield byte-identical `new_state`.
    """

    game_id: str
    turn_number: int
    phase_from: str
    phase_to: str
    prior_state_hash: str
    new_state_hash: str
    orders_revealed: dict[str, list[str]]
    new_state: dict
    is_done: bool
    winner: str | None

    def signing_payload(self) -> dict:
        """The exact fields covered by the enclave signature.

        Excludes `new_state` (large) — verifiers re-derive it from
        (prior_state_hash, orders_revealed) and compare hashes.
        """
        return {
            "game_id": self.game_id,
            "turn_number": self.turn_number,
            "phase_from": self.phase_from,
            "phase_to": self.phase_to,
            "prior_state_hash": self.prior_state_hash,
            "new_state_hash": self.new_state_hash,
            "orders_revealed": self.orders_revealed,
            "is_done": self.is_done,
            "winner": self.winner,
        }


@dataclass
class GameRecord:
    """Holds an in-memory game plus its append-only turn log."""

    game: Game
    turns: list[TurnDelta] = field(default_factory=list)
    pending_orders: dict[str, list[str]] = field(default_factory=dict)

    def turn_count(self) -> int:
        return len(self.turns)


class GameEngineError(Exception):
    pass


class UnknownGame(GameEngineError):
    pass


class UnknownPower(GameEngineError):
    pass


class GameEngine:
    """Process-local game store. Games are keyed by string id.

    In production this would persist to disk (sealed to the enclave) so
    games survive restarts. For the prototype, in-memory is fine —
    EigenCompute instances are long-lived and a crash means everyone
    redeals.
    """

    def __init__(self) -> None:
        self._games: dict[str, GameRecord] = {}

    def create_game(self, game_id: str) -> dict:
        if game_id in self._games:
            raise GameEngineError(f"game {game_id!r} already exists")
        game = Game()  # default map: classic 7-power standard
        record = GameRecord(game=game)
        self._games[game_id] = record
        return self._public_state(record)

    def _record(self, game_id: str) -> GameRecord:
        if game_id not in self._games:
            raise UnknownGame(game_id)
        return self._games[game_id]

    def submit_orders(
        self, game_id: str, power: str, orders: list[str]
    ) -> None:
        """Stage orders for a power. Overwrites any previous submission.

        Order strings use the diplomacy.Game DSL, e.g. "A PAR - BUR".
        Validation defers to the engine at resolve time.
        """
        power = power.upper()
        if power not in POWERS:
            raise UnknownPower(power)
        record = self._record(game_id)
        record.pending_orders[power] = list(orders)

    def get_pending_powers(self, game_id: str) -> list[str]:
        """Powers that have submitted orders for the current phase."""
        return sorted(self._record(game_id).pending_orders.keys())

    def resolve_turn(self, game_id: str) -> TurnDelta:
        """Apply all staged orders, advance one phase, return a TurnDelta.

        Powers without staged orders submit empty (hold) — this matches
        upstream Diplomacy behavior for missed orders.
        """
        record = self._record(game_id)
        game = record.game

        prior_state = game.get_state()
        prior_hash = state_hash(prior_state)
        phase_from = game.get_current_phase()

        # Apply staged orders to the engine.
        for power, orders in record.pending_orders.items():
            game.set_orders(power, orders)

        revealed = dict(record.pending_orders)
        record.pending_orders.clear()

        game.process()

        new_state = game.get_state()
        new_hash = state_hash(new_state)
        phase_to = game.get_current_phase()

        delta = TurnDelta(
            game_id=game_id,
            turn_number=record.turn_count() + 1,
            phase_from=phase_from,
            phase_to=phase_to,
            prior_state_hash=prior_hash,
            new_state_hash=new_hash,
            orders_revealed=revealed,
            new_state=new_state,
            is_done=bool(game.is_game_done),
            winner=self._winner(game),
        )
        record.turns.append(delta)
        return delta

    @staticmethod
    def _winner(game: Game) -> str | None:
        if not game.is_game_done:
            return None
        # The diplomacy package marks the solo winner under .outcome[1:].
        # When a draw is declared, multiple powers may be tied — we return
        # them as a comma-joined string for now (sufficient for the demo).
        outcome = getattr(game, "outcome", None) or []
        winners = [w for w in outcome if w in POWERS]
        if not winners:
            return None
        return ",".join(sorted(winners))

    def get_state(self, game_id: str) -> dict:
        return self._public_state(self._record(game_id))

    def get_turn(self, game_id: str, n: int) -> TurnDelta:
        record = self._record(game_id)
        if n < 1 or n > record.turn_count():
            raise GameEngineError(f"turn {n} not found (have {record.turn_count()})")
        return record.turns[n - 1]

    def _public_state(self, record: GameRecord) -> dict:
        game = record.game
        return {
            "phase": game.get_current_phase(),
            "powers": sorted(POWERS),
            "units": {p: list(game.get_units(p)) for p in POWERS},
            "centers": {p: list(game.get_centers(p)) for p in POWERS},
            "submitted_powers": self.get_pending_powers(
                next(k for k, v in self._games.items() if v is record)
            ),
            "turns_resolved": record.turn_count(),
            "is_done": bool(game.is_game_done),
            "state_hash": state_hash(game.get_state()),
        }
