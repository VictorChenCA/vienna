# Demo walkthrough

The 3-minute demo Vienna is built around. Each beat names the file or
endpoint it exercises, so you can rehearse off-script.

**Repo:** https://github.com/VictorChenCA/vienna

## Prep (before going on stage)

```bash
# Local (always-on backup):
cd /Users/victor/Documents/GitHub/eigen/vienna
.venv/bin/uvicorn vienna.server:app --port 8765
# Browser → http://localhost:8765
# Second terminal kept open for the verify CLI.
```

For the live EigenCompute URL (post-deploy):
```bash
ecloud compute app list             # see your apps
ecloud compute app logs vienna -w   # tail the enclave logs on stage
```

Keep two terminals visible:
1. **Server terminal** — uvicorn logs (proves resolution is happening).
2. **Verifier terminal** — empty until the live verify moment.

Have a tab open at the repo so you can scroll to `vienna/engine.py` if
asked "what's the engine?"

---

## Beat 1 — Hook (0:00–0:15)

> "Online Diplomacy has had the same trust problem since 2002: you have
> to trust the server. Diplomacy is the only major game whose rules
> structurally require a trusted third party. Today we kill that
> problem with an Intel TDX enclave."

(No clicks. Just say it.)

---

## Beat 2 — Encrypted orders moment (0:15–1:00)

1. Click **Create**. The map renders, the enclave pill in the header
   updates with the signing pubkey + image digest. **Say:** *"The
   server published its identity. Anyone can verify the binary signing
   from now on."*

2. Pick **FRANCE**. Type:
   ```
   A PAR - BUR
   F BRE - MAO
   A MAR S A PAR - BUR
   ```
   Click **Encrypt + submit**.

3. The "Latest ciphertext" panel reveals the hybrid envelope (epk +
   nonce + ct) — base64 blob. **Say, pointing at the blob:**
   *"This is what the server's disk sees. This is what I, the developer,
   see. I cannot read these orders. Neither can the host OS, even with
   root. Only the enclave's private key can decrypt this, and that key
   never left the TEE."*

4. Open dev tools → Network. Show the `POST /games/<id>/orders` request
   body is the same ciphertext. **Say:** *"Same blob on the wire."*

---

## Beat 3 — Resolution + verify panel (1:00–1:45)

1. Click **Resolve turn →**. The map updates: units move from PAR→BUR,
   BRE→MAO, MUN→RUH if Germany also submitted.

2. The **Turn log** on the right shows `Turn 1 · S1901M → F1901M`
   with a green `✓ Verified` chip. **Click the chip.**

3. The modal pops up with four checks:
   - ✓ Code matches public source — `image_digest: …` + commit
   - ✓ Running in attested enclave
   - ✓ Turn signed by enclave key — `signature: …`
   - ✓ Order receipts: your submissions were timestamped

   **Say:** *"Four things just got proven without trusting me. The
   running binary is the source on GitHub. The signing key is bound to
   that binary. This turn is signed by that key. And the submission
   came from the same key I signed up with."*

---

## Beat 4 — The kill moment (1:45–2:30)

Switch to **verifier terminal**.

```bash
node verify_cli/index.js http://localhost:8765/games/demo/turns/1
```

3 green checks (with the yellow TDX warning that you call out: *"On
EigenCompute this becomes a fourth green check with the Intel
attestation"*).

**Now the tamper.** Two ways to land this:

### Easier — runtime tamper (works without a rebuild)

The CLI catches tampered payloads. From a second terminal:

```bash
# Fetch the legit response, edit the turn number, save to a file:
curl -s http://localhost:8765/games/demo/turns/1 > /tmp/legit.json
jq '.signed_delta.payload.turn_number = 99' /tmp/legit.json > /tmp/tamper.json

# Serve the tampered version through a tiny mock that proxies /verify
# and /attestation to the real server:
node -e '
const http = require("http");
const fetch = (await import("node-fetch")).default;
const orig = "http://localhost:8765";
http.createServer(async (req, res) => {
  if (req.url.includes("/turns/")) {
    res.end(require("fs").readFileSync("/tmp/tamper.json"));
  } else {
    const u = await fetch(orig + req.url);
    res.end(await u.text());
  }
}).listen(9876);'

# In the verifier terminal:
node verify_cli/index.js http://localhost:9876/games/demo/turns/1
```

The signature check flips red and the script exits 1.

### Harder — rebuild tamper (the rhetorical kill)

```bash
# Edit one line in vienna/engine.py — e.g. change a comparison.
# Rebuild the docker image:
docker build --build-arg GIT_COMMIT=tampered -t vienna:tampered .

# The image digest is now different. Standing up this image will mint
# a different signing key (different boot), so the published pubkey
# won't match either.
```

**Say:** *"If I had tried to do this on Backstabbr — you wouldn't know.
There's no verifier you can run. On Vienna, every player can do this
from a clean terminal in two seconds. The trust moved from 'the
company hosting the server' to 'Intel's CPU plus a published commit.'"*

---

## Beat 5 — AI seats + payout (2:30–2:50)

Back to the UI.

> "One more thing — Diplomacy games die when players drop. Vienna's AI
> seats run on Claude, pay their own inference via x402 through a
> dual402-compatible flow, and run exactly the personality the player
> signed."

1. Click **Add AI seat**. Aggression 0.9, notes "rush Burgundy".
2. The seat appears in the middle pane with its personality fingerprint
   and remaining balance.
3. Click **Resolve turn →** again. **Say:** *"This turn was played by
   Claude. The personality fingerprint in the signed delta matches what
   I signed off on. The seat's balance dropped by 2 cents — that's the
   inference cost."*
4. Show the `winner_attestation` field in the resolve response.
   **Say:** *"When the game ends, this signed payload triggers an
   on-chain escrow. The winning power's wallet gets the pot. No admin.
   No customer service. No trust."*

---

## Beat 6 — Close (2:50–3:00)

> "Vienna is `pip install diplomacy` plus 500 lines of attestation
> plumbing. Same primitives — encrypted orders, signed transitions,
> attested AI agents, on-chain payouts — generalize to any hidden-info
> game. Poker. Werewolf. Sealed-bid auctions. Repo's open, AGPL,
> verify it yourself. Thanks."

---

## Backup video script

If anything goes wrong live, fall back to a recording of the same flow
saved as `demo-backup.mp4`. Record this on rehearsal night. Keep it
short — 90 seconds is plenty.

## Failure modes to anticipate

- **`tweetnacl` import 404** in the browser if the user is offline:
  preload the page once before going on stage so esm.run is cached.
- **`/games/{id}/map.svg` slow on first call** — the diplomacy renderer
  has a one-time setup cost. Create a game during prep so it's warm.
- **AI seat 500s** if Claude rate-limits — the deterministic stub
  fires by default; only enable `VIENNA_CLAUDE_LIVE=1` if rehearsed.
