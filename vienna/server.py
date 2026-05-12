"""
FastAPI surface for Vienna.

Endpoints (full list):

  POST /games                          create a new game
  POST /games/{game_id}/orders         submit sealed-box-encrypted orders
  POST /games/{game_id}/resolve        adjudicate one phase, return signed delta
  GET  /games/{game_id}/state          public state of the game
  GET  /games/{game_id}/turns/{n}      signed delta for turn n
  GET  /attestation                    TDX quote + enclave pubkeys
  GET  /verify                         image digest + commit SHA
  GET  /healthz                        liveness probe

The signing key only exists *inside* the enclave; this server is the
only thing that can produce a valid TurnDelta envelope.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from vienna import __version__
from vienna.ai_seat import AISeat, Personality
from vienna.crypto import EnclaveKeys, boot_keys
from vienna.engine import GameEngine, UnknownGame
from vienna.payout import winner_attestation

app = FastAPI(
    title="Vienna",
    description="Verifiable Diplomacy on EigenCompute.",
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Process-local state (lifecycle: enclave instance lifetime)
# ---------------------------------------------------------------------------

ENGINE = GameEngine()
KEYS: EnclaveKeys = boot_keys()

# game_id -> { POWER -> wallet address }, pinned at game creation.
ADDRESS_BOOK: dict[str, dict[str, str]] = {}

# game_id -> { POWER -> AISeat }
AI_SEATS: dict[str, dict[str, AISeat]] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateGameRequest(BaseModel):
    game_id: str | None = Field(default=None, description="Optional override")
    address_book: dict[str, str] = Field(
        default_factory=dict,
        description="POWER -> wallet address. Used for payout at game end.",
    )


class CreateGameResponse(BaseModel):
    game_id: str
    enclave: dict[str, str]
    state: dict


class SubmitOrdersRequest(BaseModel):
    ciphertext: str = Field(description="Base64 sealed-box envelope")


class SubmitOrdersResponse(BaseModel):
    receipt: dict


class ResolveResponse(BaseModel):
    turn_number: int
    signed_delta: dict
    new_state: dict
    ai_seat_actions: list[dict] = Field(default_factory=list)
    winner_attestation: dict | None = None


class AddSeatRequest(BaseModel):
    power: str
    player_address: str
    player_signature: str = Field(description="Player sig over personality fingerprint")
    personality: dict
    deposit_usdc: str = Field(default="1.00")
    x402_signature: str = Field(default="0xstub")


class AddSeatResponse(BaseModel):
    seat_id: str
    personality_fingerprint: str
    balance_usdc: str


class GameStateResponse(BaseModel):
    game_id: str
    state: dict


class TurnResponse(BaseModel):
    turn_number: int
    signed_delta: dict


class AttestationResponse(BaseModel):
    enclave: dict[str, str]
    tdx_quote: str
    note: str


class VerifyResponse(BaseModel):
    image_digest: str
    commit_sha: str
    version: str
    reproducible_build_command: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _not_found_if_unknown(game_id: str) -> None:
    try:
        ENGINE.get_state(game_id)
    except UnknownGame as exc:
        raise HTTPException(status_code=404, detail=f"unknown game {exc}") from exc


def _sign(payload: dict[str, Any]) -> dict:
    return KEYS.sign(payload)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.post("/games", response_model=CreateGameResponse)
def create_game(req: CreateGameRequest) -> CreateGameResponse:
    game_id = req.game_id or f"g_{uuid.uuid4().hex[:12]}"
    try:
        state = ENGINE.create_game(game_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ADDRESS_BOOK[game_id] = {k.upper(): v for k, v in req.address_book.items()}
    AI_SEATS[game_id] = {}
    return CreateGameResponse(
        game_id=game_id,
        enclave=KEYS.identity().to_dict(),
        state=state,
    )


@app.post("/games/{game_id}/ai-seats", response_model=AddSeatResponse)
def add_ai_seat(game_id: str, req: AddSeatRequest) -> AddSeatResponse:
    _not_found_if_unknown(game_id)
    try:
        personality = Personality(**req.personality)
    except TypeError as exc:
        raise HTTPException(status_code=400, detail=f"bad personality: {exc}")

    seat = AISeat(
        game_id=game_id,
        power=req.power.upper(),
        player_address=req.player_address,
        personality=personality,
        player_signature=req.player_signature,
    )
    seat.ledger.topup(Decimal(req.deposit_usdc), x402_signature=req.x402_signature)
    AI_SEATS.setdefault(game_id, {})[seat.power] = seat
    return AddSeatResponse(
        seat_id=f"{game_id}:{seat.power}",
        personality_fingerprint=personality.fingerprint(),
        balance_usdc=str(seat.ledger.balance),
    )


@app.post("/games/{game_id}/orders", response_model=SubmitOrdersResponse)
def submit_orders(game_id: str, req: SubmitOrdersRequest) -> SubmitOrdersResponse:
    _not_found_if_unknown(game_id)
    try:
        decoded = KEYS.decrypt_order_envelope(req.ciphertext)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"decryption failed: {exc}")

    power = decoded.get("power")
    orders = decoded.get("orders")
    if not isinstance(power, str) or not isinstance(orders, list):
        raise HTTPException(
            status_code=400,
            detail="envelope must decode to {'power': str, 'orders': list[str]}",
        )

    try:
        ENGINE.submit_orders(game_id, power, orders)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Sign a receipt so the player can later prove they submitted on time.
    receipt = _sign(
        {
            "kind": "order_receipt",
            "game_id": game_id,
            "power": power.upper(),
            "ciphertext_sha256": _sha256(req.ciphertext),
        }
    )
    return SubmitOrdersResponse(receipt=receipt)


@app.post("/games/{game_id}/resolve", response_model=ResolveResponse)
def resolve(game_id: str) -> ResolveResponse:
    _not_found_if_unknown(game_id)

    # AI seats fill in for any power they hold that hasn't submitted yet.
    ai_actions = _run_ai_seats(game_id)

    try:
        delta = ENGINE.resolve_turn(game_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Fold AI-seat attestation fields into the signed delta so a verifier
    # can confirm which powers were AI-played and which player signed
    # off on each AI personality.
    payload = delta.signing_payload()
    for seat in AI_SEATS.get(game_id, {}).values():
        payload.update(seat.attestation_fields())
    signed = _sign(payload)

    winner_att: dict | None = None
    if delta.is_done:
        try:
            winner_att = winner_attestation(
                delta, ADDRESS_BOOK.get(game_id, {}), KEYS
            )
        except KeyError as exc:
            # Missing address for a winner — surface but don't fail the resolve.
            winner_att = {"error": str(exc)}

    return ResolveResponse(
        turn_number=delta.turn_number,
        signed_delta=signed,
        new_state=delta.new_state,
        ai_seat_actions=ai_actions,
        winner_attestation=winner_att,
    )


def _run_ai_seats(game_id: str) -> list[dict]:
    """For each attached AI seat whose power hasn't submitted, generate
    orders and stage them via the engine. Returns a per-seat audit log
    suitable for the demo UI."""
    seats = AI_SEATS.get(game_id, {})
    if not seats:
        return []

    submitted = set(ENGINE.get_pending_powers(game_id))
    record = ENGINE._record(game_id)  # internal access — keeps deps simple
    game = record.game

    actions: list[dict] = []
    for power, seat in seats.items():
        if power in submitted:
            continue
        try:
            orderable = game.get_orderable_locations(power)
            possible = {loc: game.get_all_possible_orders().get(loc, []) for loc in orderable}
            chosen = seat.choose_orders(
                game_state={
                    "phase": game.get_current_phase(),
                    "your_units": list(game.get_units(power)),
                    "centers": {p: list(game.get_centers(p)) for p in game.powers},
                },
                orderable_locations=list(orderable),
                possible_orders=possible,
            )
            ENGINE.submit_orders(game_id, power, chosen)
            actions.append(
                {
                    "power": power,
                    "orders": chosen,
                    "personality_fingerprint": seat.personality.fingerprint(),
                    "remaining_balance_usdc": str(seat.ledger.balance),
                }
            )
        except Exception as exc:
            actions.append({"power": power, "error": str(exc)})
    return actions


@app.get("/games/{game_id}/state", response_model=GameStateResponse)
def get_state(game_id: str) -> GameStateResponse:
    _not_found_if_unknown(game_id)
    return GameStateResponse(game_id=game_id, state=ENGINE.get_state(game_id))


@app.get("/games/{game_id}/turns/{n}", response_model=TurnResponse)
def get_turn(game_id: str, n: int) -> TurnResponse:
    _not_found_if_unknown(game_id)
    try:
        delta = ENGINE.get_turn(game_id, n)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return TurnResponse(turn_number=n, signed_delta=_sign(delta.signing_payload()))


@app.get("/attestation", response_model=AttestationResponse)
def attestation() -> AttestationResponse:
    # On EigenCompute proper, the TDX quote is fetched from a sibling
    # process or read from /sys/. We stub it here for local dev and
    # surface a clear note so the demo can call it out honestly.
    tdx_quote = os.environ.get("VIENNA_TDX_QUOTE", "")
    note = (
        "TDX quote populated by EigenCompute at runtime."
        if tdx_quote
        else "Local dev: no TDX quote available. On EigenCompute this field "
        "is populated automatically and verifiable against Intel's CA chain."
    )
    return AttestationResponse(
        enclave=KEYS.identity().to_dict(),
        tdx_quote=tdx_quote,
        note=note,
    )


@app.get("/verify", response_model=VerifyResponse)
def verify() -> VerifyResponse:
    commit = os.environ.get("VIENNA_COMMIT", "dev-local")
    image_digest = os.environ.get("VIENNA_IMAGE_DIGEST", f"sha256:dev-{commit}")
    return VerifyResponse(
        image_digest=image_digest,
        commit_sha=commit,
        version=__version__,
        reproducible_build_command=(
            f"docker build --build-arg GIT_COMMIT={commit} -t vienna:{commit} ."
        ),
    )


# ---------------------------------------------------------------------------
# Internal utils
# ---------------------------------------------------------------------------


def _sha256(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode()).hexdigest()
