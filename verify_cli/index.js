#!/usr/bin/env node
/**
 * vienna-verify
 * -------------
 *
 * Independently verify a Vienna turn.
 *
 *   $ vienna-verify <turn-url>
 *
 *   # examples:
 *   vienna-verify https://vienna.eigencompute.xyz/games/demo/turns/3
 *   vienna-verify http://localhost:8765/games/demo/turns/3
 *
 * This script does NOT trust the Vienna server. It verifies, in order:
 *
 *   1. The signed delta's `image_digest` matches what /verify reports.
 *   2. The signed delta's `signing_public_key` matches what /attestation
 *      publishes for this enclave.
 *   3. The Ed25519 signature is valid over the canonical-JSON encoding
 *      of the payload.
 *
 * If you can run this script and it exits 0, you have cryptographic
 * proof that the turn output came from the exact binary published at
 * the commit /verify reports — even if the server is hostile.
 *
 * Pure CommonJS-free, no build step, only `tweetnacl` + node fetch.
 */

import nacl from "tweetnacl";
import naclUtil from "tweetnacl-util";
import { URL } from "node:url";

const RED = "\x1b[31m";
const GREEN = "\x1b[32m";
const DIM = "\x1b[2m";
const BOLD = "\x1b[1m";
const RESET = "\x1b[0m";
const YELLOW = "\x1b[33m";

function log(...args) { console.log(...args); }
function ok(msg, detail) {
  log(`  ${GREEN}✓${RESET} ${msg}`);
  if (detail) log(`    ${DIM}${detail}${RESET}`);
}
function fail(msg, detail) {
  log(`  ${RED}✗${RESET} ${BOLD}${msg}${RESET}`);
  if (detail) log(`    ${DIM}${detail}${RESET}`);
}
function warn(msg, detail) {
  log(`  ${YELLOW}⚠${RESET} ${msg}`);
  if (detail) log(`    ${DIM}${detail}${RESET}`);
}

function canonicalJSON(obj) {
  if (Array.isArray(obj)) return "[" + obj.map(canonicalJSON).join(",") + "]";
  if (obj !== null && typeof obj === "object") {
    const keys = Object.keys(obj).sort();
    return "{" + keys.map(k => JSON.stringify(k) + ":" + canonicalJSON(obj[k])).join(",") + "}";
  }
  return JSON.stringify(obj);
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`);
  return res.json();
}

function deriveBase(turnUrl) {
  const u = new URL(turnUrl);
  return `${u.protocol}//${u.host}`;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 1 || args[0] === "--help" || args[0] === "-h") {
    log(`${BOLD}vienna-verify${RESET}  —  independently verify a Vienna turn`);
    log("");
    log("Usage:");
    log("  vienna-verify <turn-url>");
    log("");
    log("Example:");
    log("  vienna-verify https://vienna.eigencompute.xyz/games/demo/turns/3");
    log("");
    process.exit(args.length === 0 ? 2 : 0);
  }

  const turnUrl = args[0];
  const base = deriveBase(turnUrl);

  log("");
  log(`${BOLD}Verifying${RESET} ${turnUrl}`);
  log("");

  let turn, verify, attestation;
  try {
    [turn, verify, attestation] = await Promise.all([
      fetchJSON(turnUrl),
      fetchJSON(`${base}/verify`),
      fetchJSON(`${base}/attestation`),
    ]);
  } catch (e) {
    fail(`fetch error: ${e.message}`);
    process.exit(1);
  }

  const delta = turn.signed_delta;
  let passed = 0, failed = 0;

  // 1. Image digest match
  if (delta.image_digest === verify.image_digest) {
    ok(
      "Code matches public source",
      `image_digest: ${delta.image_digest}  ·  commit: ${verify.commit_sha}`
    );
    passed++;
  } else {
    fail(
      "Image digest mismatch — running binary diverges from claimed commit",
      `delta says ${delta.image_digest}, /verify says ${verify.image_digest}`
    );
    failed++;
  }

  // 2. Enclave identity match
  if (delta.signing_public_key === attestation.enclave.signing_public_key) {
    ok(
      "Signed by the published enclave",
      `pubkey: ${delta.signing_public_key.slice(0, 32)}…`
    );
    passed++;
  } else {
    fail(
      "Signing key mismatch — signature is not from the published enclave",
      `delta key: ${delta.signing_public_key}\n    /attestation key: ${attestation.enclave.signing_public_key}`
    );
    failed++;
  }

  // 3. Attestation surface (informational on local dev)
  if (attestation.tdx_quote && attestation.tdx_quote.length > 0) {
    ok(
      "Intel TDX quote present",
      `${attestation.tdx_quote.length} bytes  ·  verify against Intel CA chain offline`
    );
    passed++;
  } else {
    warn(
      "TDX quote absent (local dev?)",
      "On EigenCompute this field is populated automatically and re-verifiable."
    );
  }

  // 4. Signature
  try {
    const msg = naclUtil.decodeUTF8(canonicalJSON(delta.payload));
    const sig = naclUtil.decodeBase64(delta.signature);
    const pk = naclUtil.decodeBase64(delta.signing_public_key);
    const valid = nacl.sign.detached.verify(msg, sig, pk);
    if (valid) {
      ok(
        "Ed25519 signature valid over canonical payload",
        `turn ${delta.payload.turn_number}  ·  ${delta.payload.phase_from} → ${delta.payload.phase_to}`
      );
      passed++;
    } else {
      fail(
        "Signature does NOT verify — payload was tampered or signed by a different key",
      );
      failed++;
    }
  } catch (e) {
    fail(`signature verify error: ${e.message}`);
    failed++;
  }

  log("");
  if (failed === 0) {
    log(`${GREEN}${BOLD}✓ VERIFIED${RESET}  (${passed} checks passed)`);
    log("");
    log(`${DIM}This turn output came from the exact binary at commit ${verify.commit_sha}${RESET}`);
    log(`${DIM}running in the enclave whose pubkey is published at /attestation.${RESET}`);
    process.exit(0);
  } else {
    log(`${RED}${BOLD}✗ FAILED${RESET}  (${failed} of ${passed + failed} checks failed)`);
    log("");
    log(`${DIM}Do NOT trust this turn. The server is misconfigured, downgraded,${RESET}`);
    log(`${DIM}or actively malicious.${RESET}`);
    process.exit(1);
  }
}

main().catch(e => {
  fail(`unexpected error: ${e.message}`);
  process.exit(1);
});
