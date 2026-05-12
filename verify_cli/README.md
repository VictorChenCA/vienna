# vienna-verify

Independently verify a Vienna turn end-to-end. Trusts only the Intel
attestation chain published at `/attestation` and the source repository
this script was installed from.

## Install

```bash
cd verify_cli
npm install
npm link    # makes `vienna-verify` available globally
```

Or, without installing, just run it directly:

```bash
node verify_cli/index.js <turn-url>
```

## Usage

```bash
vienna-verify https://vienna.eigencompute.xyz/games/demo/turns/3
```

Exits 0 on success, 1 on any failure. Prints a colored four-check
trust chain.

## What it checks

1. The signed delta's `image_digest` matches what `/verify` reports.
2. The signed delta's `signing_public_key` matches what `/attestation`
   publishes for this enclave.
3. The Intel TDX quote is present (warning on local dev).
4. The Ed25519 signature is valid over the canonical-JSON encoding of
   the turn payload.

## Showing tampering

Stand up a fork of Vienna, change one line in `engine.py`, rebuild the
Docker image — the image_digest changes, `vienna-verify` fails check #1,
and exits 1.

## License

AGPL-3.0-only, matching the upstream Vienna server and the
`diplomacy/diplomacy` engine it wraps.
