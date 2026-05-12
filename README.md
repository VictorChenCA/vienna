# Vienna

Verifiable Diplomacy on EigenCompute.

Vienna replaces the trusted-GM role every online Diplomacy server still requires.
Player orders are encrypted so even the server operator cannot read them, every
turn's resolution is signed by a key that lives inside an Intel TDX enclave, and
the binary running in the enclave is cryptographically linked to this public
repo. AI seats — backed by Claude — keep games alive when humans drop, with
personality configs signed by the player who configured them.

## Why this needs EigenCompute

Run on AWS or Vercel, every trust property collapses:

- The operator can read everyone's secret orders.
- The operator can fudge adjudication.
- AI seats are opaque — no one can verify they ran the personality the player set.
- Post-game disputes have no resolution path.

EigenCompute gives us:

| Property                  | What it buys us                                                 |
| ------------------------- | --------------------------------------------------------------- |
| Source code verifiability | Players rebuild from this repo and check the image digest.       |
| Attestations              | Every turn delta is signed by an enclave-bound key.              |
| Encrypted memory          | Orders are sealed-box encrypted to the enclave's public key.     |
| Agent commerce            | AI seats pay their own inference via x402/MPP (`dual402`).       |
| Programmatic payouts      | Game-end signed attestation triggers an on-chain escrow release. |

## Architecture (one screen)

```
Browser  -- HTTPS -->  Enclave (Intel TDX on EigenCompute)
   |                       |
   |                       +-- vienna/server.py     FastAPI surface
   |                       +-- vienna/engine.py     diplomacy.Game wrapper
   |                       +-- vienna/crypto.py     sealed-box + Ed25519
   |                       +-- vienna/ai_seat.py    Claude + dual402
   |                       +-- vienna/payout.py     game-end attestation
   |
   +-- npx vienna-verify <url> -- TDX quote + image digest + signature check
```

## Engine: wrap, don't fork

Vienna depends on [`diplomacy`](https://pypi.org/project/diplomacy/) (the
`diplomacy/diplomacy` reference implementation used by Meta's CICERO research)
as a pinned pip dependency. We do **not** fork the engine. The attestation
surface is the small FastAPI server we wrote — DATC-compliant adjudication is
inherited verbatim.

## Verifying a game locally

```bash
# 1. Compare the published source to the running image
git clone https://github.com/<you>/vienna && cd vienna
git checkout <commit-sha-from-/verify>
docker build --build-arg GIT_COMMIT=$(git rev-parse HEAD) -t vienna:local .
docker inspect --format='{{.Id}}' vienna:local
# compare to:
curl https://<your-deployment>/verify

# 2. Check the per-turn signature
curl https://<your-deployment>/games/<id>/turns/4 | jq
# verify signature against the enclave pubkey returned at /attestation
```

## Status

Prototype built during the EigenCloud Private Preview (April–May 2026). Not
audited. Not for production funds.

## License

AGPL-3.0, inherited from upstream `diplomacy/diplomacy`. Network-accessible
deployments must publish their source under the same terms.
