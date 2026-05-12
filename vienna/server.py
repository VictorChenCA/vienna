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
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from vienna import __version__
from vienna.crypto import EnclaveKeys, boot_keys
from vienna.engine import GameEngine, UnknownGame

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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateGameRequest(BaseModel):
    game_id: str | None = Field(default=None, description="Optional override")


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
    return CreateGameResponse(
        game_id=game_id,
        enclave=KEYS.identity().to_dict(),
        state=state,
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
    try:
        delta = ENGINE.resolve_turn(game_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    signed = _sign(delta.signing_payload())
    return ResolveResponse(
        turn_number=delta.turn_number,
        signed_delta=signed,
        new_state=delta.new_state,
    )


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
