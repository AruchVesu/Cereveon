# TLS certificate pin rotation

## Scope

The Android client pins three SPKI hashes for `cereveon.com` via
`<pin-set>` in
[`network_security_config.xml`](../android/app/src/main/res/xml/network_security_config.xml).
This runbook documents when to rotate the pins, how to compute new
ones, and what to do if pinning ever bricks the app on a release.

The pinned set today:

| # | Cert | SPKI sha256 (base64) | Validity horizon |
|---|------|----------------------|------------------|
| 1 | Let's Encrypt **YE1** ECDSA intermediate | `brzvtCELCIZUo4sD/qPX0ccRtPsd3DY6RfmxpOU9oB4=` | Rotates periodically (Let's Encrypt schedule) |
| 2 | **ISRG Root X1** (RSA root) | `C5+lpZ7tcVwmwQIMcRtPbsQtWLABXhQzejna0wHFr8M=` | Valid until 2030-06 |
| 3 | **ISRG Root X2** (ECDSA root) | `diGVwiVYbubAI3RW4hB9xU8e/CH2GnkuvVFZE8zmgzI=` | Valid until 2035-09 |

Pin-set expiration: **2028-05-20** (brick-recovery floor — see § Brick recovery).

> **2026-06-24** — rotated pin #1 from the retired **E8** to **YE1**. Let's
> Encrypt migrated cereveon.com's chain to the YE1 → *ISRG Root YE* hierarchy
> (leaf → YE1 → ISRG Root YE → ISRG Root X2 → X1). The chain still cross-signs up
> to ISRG Root X1/X2, so the two root pins kept pinning alive across the change —
> only the intermediate pin needed refreshing.

## When to rotate

Rotation is triggered by, in order of urgency:

1. **The pin-set expiration is approaching.** A 6-month warning
   period is comfortable. The source-pin test
   (`NetworkSecurityCertPinningTest::EXPIRATION_FUTURE`) catches this
   if the expiration has already lapsed; rotate before then.
2. **Let's Encrypt announces an intermediate rotation that retires
   YE1.** Pin set #1 must be replaced with the new intermediate's
   SPKI before the rotation takes effect on production.
3. **The Caddy / Let's Encrypt setup migrates to a different CA**
   (e.g., Sectigo, Google Trust Services). Every pin in the set
   must be replaced.
4. **Routine quarterly review** as part of `THREAT_MODEL.md`
   audit cycle.

## How to compute new pins

Run these commands on a machine that can reach the public
production endpoint:

```bash
# 1. Fetch the live cert chain.
echo | openssl s_client -showcerts -servername cereveon.com \
    -connect cereveon.com:443 2>/dev/null > /tmp/chain.txt

# 2. Split into individual PEM files.
mkdir -p /tmp/certs
awk '/-----BEGIN CERTIFICATE-----/{i++} {print > "/tmp/certs/cert_"i".pem"}' /tmp/chain.txt

# 3. For each cert, extract the SPKI and compute its SHA-256.
for f in /tmp/certs/cert_*.pem; do
  if [ ! -s "$f" ]; then continue; fi
  echo "=== $f ==="
  openssl x509 -in "$f" -noout -subject -issuer
  pin=$(openssl x509 -in "$f" -pubkey -noout 2>/dev/null \
       | openssl pkey -pubin -outform der 2>/dev/null \
       | openssl dgst -sha256 -binary \
       | openssl enc -base64)
  echo "SPKI sha256 pin: $pin"
done
```

For Let's Encrypt roots (not in the live chain, only in the system
trust store), download from <https://letsencrypt.org/certificates/>
and run the same `openssl x509 -pubkey -noout | ...` pipeline.

Each pin must be **exactly 44 characters** (base64 of 32 bytes).

## What changes in the codebase

A pin rotation touches three files; all three MUST be updated in
the same commit:

1. **`android/app/src/main/res/xml/network_security_config.xml`** —
   add the new pin to `<pin-set>` BEFORE removing the old one, so
   the next release ships with both pins active. The next-but-one
   release can then drop the retired pin.
2. **`android/app/src/test/java/ai/chesscoach/app/NetworkSecurityCertPinningTest.kt`** —
   update the `EXPECTED_PINS` set to match. The test enforces
   EXACT equality (not subset), so a forgotten update fails CI.
3. **`docs/CERT_PIN_ROTATION.md`** (this file) — update the pin
   table above so the next reviewer sees what's currently shipped.

If the rotation is purely a pin refresh (no expiration bump), the
expiration attribute can stay as-is. If you bump the expiration,
also update `EXPECTED_EXPIRATION_FLOOR` in the test if a hard
floor exists.

## Pin coverage strategy

The three-pin design covers four rotation cases without a release:

| Rotation case | Survival |
|---|---|
| Leaf cert renews (every ~90 days) | ✅ YE1 still matches the new leaf's chain |
| Let's Encrypt rotates intermediate (YE1 → successor) | ✅ ISRG Root X1/X2 still chain the new intermediate |
| Let's Encrypt switches chain root (X1 → X2) | ✅ ISRG Root X2 in the pin set matches |
| Let's Encrypt → entirely different CA | ❌ Release required; brick-recovery floor (expiration) provides graceful fallback to system-CA trust |

Pinning a **leaf** would brick the app every ~90 days. The source-pin
test has a `LEAF_NOT_PINNED` guard against this anti-pattern.

## Brick recovery

If a release ships with broken pins (e.g., the pin set was wrong, or
Let's Encrypt rotated the chain between commit and release), every
Android client refuses TLS to `cereveon.com` and the app cannot reach
the backend. Two layers of protection:

1. **Source-pin test in CI.** The test fails if `EXPECTED_PINS`
   diverges from the XML. This catches most accidental drift before
   release.
2. **Pin-set `expiration` attribute.** When the expiration date
   passes, NetworkSecurityConfig falls back to system-CA trust —
   the same posture as before pinning landed. The pin set is then
   effectively disabled until a new release ships with refreshed
   pins.

If the app does ship with broken pins, the recovery path is a hotfix
release. There is no remote-config kill-switch. The expiration date
is the only automatic recovery vector, which is why it MUST be set
to a value in the future every time the pin set is touched.

## Audit hooks

- `THREAT_MODEL.md` § T2 documents the threat the pinning closes
  and references this runbook.
- The source-pin test
  (`android/app/src/test/java/ai/chesscoach/app/NetworkSecurityCertPinningTest.kt`)
  pins both the values and the structure (3 pins minimum,
  base64-of-32-bytes shape, future-dated expiration,
  `includeSubdomains="true"` scoping).
- A grep for the SPKI hashes above across the repo turns up exactly
  three references each (XML + test + this doc); a drift between
  any two surfaces is detectable with `grep -r '<hash>='`.
