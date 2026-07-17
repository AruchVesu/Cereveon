# Cereveon Privacy Policy

> **DRAFT / MOCK — v0.1, 2026-07-17. Not published, not legal advice.**
> Working draft for internal review. Every statement about system behaviour
> below was verified against the code in this repository on 2026-07-17
> (evidence map in Annex B). Before publication: fill every
> `[BRACKETED PLACEHOLDER]`, resolve or formally accept every item in
> Annex A, have counsel review the legal-basis choices in §6, and
> **delete Annexes A and B** — they are internal engineering material.

**Effective date:** [DATE — set at publication]
**Applies to:** the Cereveon apps for Android and iOS and the Cereveon API
at `cereveon.com` (together, "the Service").

---

## 1. Who we are

Cereveon is a chess-coaching service operated by
**[LEGAL ENTITY NAME]**, [REGISTERED ADDRESS], [COMPANY / TRADE REGISTER
NO.] ("we", "us"). We are the data controller for the personal data
described in this policy.

Privacy contact: **[privacy@cereveon.com]**
We have not appointed a Data Protection Officer. [Counsel to confirm no
DPO is required under Art. 37 GDPR — small scale, no special-category
data, no large-scale systematic monitoring.]

## 2. The short version

- You need an account (email + password, or a Lichess sign-in). We never
  see your Lichess password, and we don't ask for your name, phone
  number, address, or location.
- Your games, puzzles, and coach conversations are stored in your account
  so the coach can track your progress. Chess analysis runs on **our own
  servers** with a local Stockfish engine — no analysis service sees your
  games.
- Coach replies are written by an AI language model (DeepSeek). To
  generate a reply we send the current board position, engine context,
  your recent coach messages, and a rough skill sketch — **never your
  email, name, account ID, or IP address**.
- The apps contain **no advertising, analytics, or tracking SDKs** and
  request a single permission (internet access). We do not sell or share
  your data for advertising.
- Payments are handled by Google Play. We never see your card details.
- You can ask us for a copy of your data or ask us to delete your
  account at any time: [privacy@cereveon.com].

## 3. Data we collect and store

### 3.1 Account and sign-in

| Data | Details |
|---|---|
| Email address | Your login identity. Not shown to other users (there are no public profiles). |
| Password | Stored only as a salted PBKDF2-SHA256 hash (600,000 iterations). We cannot read it. |
| Lichess identity | If you sign in with Lichess: your Lichess user ID and username. We receive them through Lichess's OAuth consent screen. The temporary Lichess access token is used once to confirm who you are and is **revoked immediately** — we never store Lichess tokens, and a Lichess sign-in account has no email on file. |
| Sessions | A hash of your current login token, its expiry, and an app-supplied device label (e.g. app version/platform), so you can stay signed in and we can revoke stolen tokens. |

### 3.2 Chess play and training

Games you play or import (moves, results, board positions), computed
accuracy and mistake categories, post-game AI reviews, puzzle and drill
attempts, study plans, XP and level, and your opening repertoire. The
app also keeps a small in-app notification feed about your own account
(e.g. "your imported games were analysed", "reconnect your Lichess
account"); notifications are generated server-side from your existing
data, never from third-party sources, and are deleted with your
account.

### 3.3 Skill profile (profiling)

To coach you, we continuously derive a skill profile from your play: a
rating estimate, a confidence value, a per-phase weakness/strength
vector, a numeric player embedding, and per-game analytics records. This
profile personalises coaching (e.g. the coach knows endgames are your
weak spot). It is used **only** to adapt coaching content and difficulty
— see §7 on automated decision-making.

### 3.4 Coach conversations

Your messages to the coach and the coach's replies are stored verbatim
in your account, together with the board position each message referred
to, so your conversation survives reinstalls and device changes.

### 3.5 Feedback

If you use "Send feedback", we store your message text (up to 2,000
characters) and app version. Feedback is read by the operator only; it
is never fed into the AI, never used for coaching, and its content is
kept out of server logs.

### 3.6 Subscription

Your plan tier (free/Pro). For Pro purchases, Google Play sends us an
opaque purchase token and product ID which we verify with Google. **We
never receive your payment details** — Google processes the payment.

### 3.7 Technical data

Server request logs (timestamp, endpoint, status, latency, request ID,
and connection IP address) for security, rate limiting, and abuse
prevention. Logs are size-rotated on the server ([RETENTION —
see Annex A.6]). We build no advertising or cross-service profiles from
this data.

### 3.8 On your device

The app stores your login token in encrypted storage (AES-256-GCM via
the platform keystore) and caches your current game locally in
app-private storage. App data is **excluded from cloud backups and
device-to-device transfer**. The apps use no cookies and no advertising
identifiers.

### 3.9 What we do NOT collect

No real name, no phone number, no postal address, no location, no
contacts, no photos/camera/microphone access, no advertising ID, no
third-party analytics or crash-reporting SDKs on either platform. The
Android app declares exactly one permission: `INTERNET`.

## 4. Where your data lives

The Service runs on a server rented from **Hetzner** ([Hetzner Online
GmbH, Germany — CONFIRM DATACENTRE LOCATION, see Annex A.10]), including
the database that holds all account data. Chess-engine analysis
(Stockfish) runs locally on that server. A short-lived engine cache
(Redis, on the same server) is keyed by board position only and contains
no personal data.

## 5. Recipients and processors

We share personal data only with the processors and services below,
only for the purposes stated. We do not sell personal data and we share
nothing for advertising.

| Recipient | What they receive | Why |
|---|---|---|
| **Hetzner** (hosting, [Germany]) | All service data (as our infrastructure provider) | Running the Service |
| **DeepSeek** (LLM API, China — see §8) | Board position, engine context, your recent coach messages, a derived skill sketch, coach-voice preference. **No identifiers** (no email, account ID, token, or IP) | Generating coach replies |
| **Lichess** (lichess.org) | Your Lichess username (only if you link/import); OAuth exchange data during Lichess sign-in | Sign-in with Lichess, importing your public Lichess games, fetching practice puzzles |
| **Google — Play Billing** | Purchase verification: we send Google your purchase token; Google tells us the subscription state | Pro subscriptions |
| **Google — Gmail SMTP** | A weekly operator digest containing feedback texts, app version, and a truncated (8-character) internal ID — no email addresses | Reviewing user feedback |
| **Grafana Labs** (only if operator monitoring is enabled) | Server logs/metrics, which can include pseudonymous internal IDs — never chat content or emails [CONFIRM CURRENT STATE — Annex A.8] | Service monitoring |

Puzzle fetching from Lichess is done by our server on your behalf; your
identity is not attached to those requests.

## 6. Purposes and legal bases (Art. 6 GDPR)

| Purpose | Data | Legal basis |
|---|---|---|
| Providing the Service: account, play, training, progress | §3.1, §3.2 | Contract (Art. 6(1)(b)) |
| AI coaching, including the skill profile that personalises it | §3.3, §3.4 | Contract (Art. 6(1)(b)) [counsel: consider whether profiling is better placed on legitimate interest with opt-out] |
| Pro subscription management | §3.6 | Contract (Art. 6(1)(b)) |
| Security, rate limiting, abuse prevention, service logs | §3.7 | Legitimate interest (Art. 6(1)(f)) — keeping the Service secure and available |
| Handling feedback you send us | §3.5 | Legitimate interest (Art. 6(1)(f)) — improving the Service at your initiative |
| Legal obligations (e.g. accounting records of transactions) | Minimal billing records | Legal obligation (Art. 6(1)(c)) |

We currently rely on **consent for nothing**; if we ever add marketing
or optional analytics, we will ask first. Providing an email (or a
Lichess identity) is required to create an account — without it the
Service cannot maintain your progress.

## 7. AI transparency and automated decisions

The Cereveon coach is an **AI system**: replies are generated by a large
language model and are clearly presented as coming from the app's coach
(EU AI Act transparency, Art. 50). The model is constrained by our
engine-grounded pipeline: chess facts come from a local Stockfish
engine, and every AI reply passes validation filters — including a
firewall that blocks personal-data patterns — before you see it. When
the AI is unavailable or fails validation, a deterministic (non-AI)
coach reply is served instead.

We make **no decisions about you with legal or similarly significant
effects** based solely on automated processing (Art. 22 GDPR). The skill
profile only tunes coaching content and difficulty; free-tier limits are
fixed contractual quotas applied to everyone equally, not profiling
outcomes.

## 8. International transfers

Our servers are in [Germany/the EU — confirm]. Two transfers leave that
boundary:

- **DeepSeek (People's Republic of China).** Coach replies are generated
  by DeepSeek's API. What is sent is listed in §5; by design it contains
  no direct identifiers, and chess positions and coaching text are not
  linkable to you by DeepSeek. Transfer safeguard: **[GAP — SCCs/DPA
  with DeepSeek plus a Transfer Impact Assessment must be executed and
  referenced here before publication; see Annex A.3.]**
- **Google (Play billing verification; digest email).** Google may
  process data outside the EU under its own certified safeguards
  (EU–US Data Privacy Framework / SCCs).

Lichess is operated from within the EU [confirm], so linking and imports
stay within the EEA.

## 9. Retention

| Data | Kept |
|---|---|
| Account, games, chat, skill profile, training history | While your account exists; deleted immediately when you delete your account (server-side erasure endpoint, contract §41) **[remaining: in-app entry point — Annex A.1]** |
| Feedback messages | [24 months] [PROPOSED — no automatic purge exists yet, Annex A.5] |
| Server request logs (incl. IP) | Size-rotated on-server (~250 MB window); [30 days] target [Annex A.6]; if operator monitoring is enabled, 14 days at Grafana Cloud |
| Sessions | Until expiry/logout; stale sessions pruned at next login (max 10 per account) |
| Engine cache (Redis) | ≤ 6 hours, position-keyed, no personal data, never written to disk |
| Backups | [DOCUMENT BACKUP REGIME — Annex A.9] |

## 10. Your rights

Under the GDPR you can ask us to: **access** your data (and receive a
copy in a portable format), **rectify** it, **erase** it, **restrict**
or **object to** processing based on legitimate interest, and **not be
subject to** solely automated decisions with legal effect (we make
none). Write to [privacy@cereveon.com] from your account email (or with
your Lichess username for Lichess sign-ins) and we will respond within
one month. Deleting your account removes your games, chat history,
skill profile, feedback, and linked-account records immediately — the
server erases every table your account touches in one transaction
(implemented: contract §41; in-app button pending, Annex A.1).

You also have the right to lodge a complaint with your local supervisory
authority, or with our lead authority, [SUPERVISORY AUTHORITY OF THE
CONTROLLER'S ESTABLISHMENT — e.g. the competent German Landes-DPA if
established in Germany].

## 11. Security

- All traffic is TLS-encrypted (HTTPS-only with HSTS); both apps
  additionally **pin our server's certificate keys**, so they refuse to
  talk to an impostor server even if a device's trust store is
  compromised.
- Passwords: salted PBKDF2-SHA256, 600,000 iterations (NIST SP 800-132
  compliant). Login tokens: signed, expire after 24 hours, rotate on
  every request, and are revocable server-side; changing your password
  signs out all sessions.
- On-device: tokens in hardware-backed encrypted storage; app data
  excluded from cloud backups.
- AI boundary: model output is validated before display, including a
  firewall for personal-data patterns; user input is sanitised against
  prompt injection.
- Server: engine analysis is local (no third-party analysis service),
  request rate limiting, containerised services, structured logs that
  exclude chat content, emails, and passwords.
- Breach response: if a breach is likely to risk your rights, we will
  notify the supervisory authority within 72 hours (Art. 33) and
  affected users where required (Art. 34).

## 12. Children

The Service is not directed at children under [13]. We do not knowingly
collect data from children under [13]; if you believe a child has
created an account, contact us and we will delete it. [Counsel: confirm
age threshold per target markets; consent-based features would trigger
Art. 8 GDPR (16, or 13–15 per member state).]

## 13. Changes to this policy

We will announce material changes in the app before they take effect and
update the date above. Earlier versions will remain available on request.

## 14. Contact

[LEGAL ENTITY NAME] · [ADDRESS] · [privacy@cereveon.com]

---
---

## Annex A — INTERNAL: gaps to close before publication (REMOVE)

Status as verified in-repo on 2026-07-17. Each item blocks or weakens a
statement made above; publish only after each is fixed or the policy
text is weakened to match reality.

| # | Gap | GDPR hook | Reality in code | Required fix |
|---|---|---|---|---|
| A.1 | ~~No account-deletion path.~~ **Server-side CLOSED 2026-07-17**: `DELETE /auth/me` erases the player row + every linked table via the explicit purge in `llm/seca/auth/erasure.py` (contract §41). | Art. 17; Google Play account-deletion policy | Endpoint + `AuthService.delete_account` implemented; discovery-driven test pins plan completeness. | Remaining: in-app "Delete account" entry point (Android/iOS Settings) + Play Console deletion URL once the policy is hosted. |
| A.2 | ~~Erasure cascade not wired.~~ **CLOSED 2026-07-17**: every FK edge into the player graph now declares `ondelete="CASCADE"`; `init_schema` retrofits live Postgres constraints (`_ensure_fk_delete_cascade`); the erasure service performs explicit ordered deletes so SQLite (no FK pragma) and the three no-FK `player_id` tables are covered regardless. | Art. 17 | Model FKs + catalog-driven retrofit + `test_auth_account_deletion.py` (zero-rows + CASCADE pins). | None — keep the discovery tripwire green when adding models. |
| A.3 | **DeepSeek transfer has no documented safeguard.** No DPA/SCCs referenced anywhere; no retention/opt-out flag set on API calls; DeepSeek-side prompt caching is relied on. | Art. 44–46, Ch. V; Schrems II TIA | Payload verified identifier-free (see Annex B) — strong minimisation, but minimisation ≠ transfer mechanism. | Execute DeepSeek DPA + SCCs, run a TIA; alternatively route via an EU-hosted OpenAI-compatible gateway (`COACH_DEEPSEEK_API_BASE` already supports it) or make AI coaching opt-in. |
| A.4 | **"⏸ Tracking paused" UI copy is misleading.** SAFE_MODE pauses only the RL/adaptive-learning loop; deterministic profiling (rating, confidence, weakness vector, player embedding, bandit experience capture, analytics events) runs on every game finish. | Art. 5(1)(a) transparency; Art. 13(2)(f) | `SkillUpdater.update_from_event` unconditional at `llm/seca/events/router.py:605`. | Reword the UI copy (e.g. "Adaptive learning off"), and keep §3.3's profiling disclosure. |
| A.5 | **No retention/purge automation.** No TTL, cron, or scheduled deletion for any content table; rows persist indefinitely. | Art. 5(1)(e) | Startup janitors only flip stale job status. | Pick the §9 numbers, implement purge jobs, then publish the numbers. |
| A.6 | **Log retention undefined; IP handling nuance.** Request-end log line includes `client_ip`; behind Caddy this is the proxy IP unless `TRUSTED_PROXIES` is set (currently warned-unset in prod), while the limiter's real-IP use is in-memory only. Docker rotation ≈50 MB×5 is the only bound; Loki shipping optional. | Art. 5(1)(e); 6(1)(f) balancing | `llm/server.py:744-754`; `llm/seca/shared_limiter.py`. | Decide and document a log-retention window; confirm whether real client IPs actually reach logs in the prod proxy setup and align §3.7. |
| A.7 | **No in-app privacy-policy link** on either platform (none in Android strings/settings; iOS has no `PrivacyInfo.xcprivacy`). | Art. 12/13 delivery; Play & App Store requirements | Verified absent. | Host the final policy at `https://cereveon.com/privacy`, link it from Settings + both store listings; add iOS privacy manifest. |
| A.8 | **Grafana Cloud shipping state must be pinned down.** Policy §5 says "only if enabled" — confirm whether the `monitoring` compose profile is live in prod and disclose accordingly. | Art. 13(1)(e) | Opt-in `alloy` sidecar in `docker-compose.prod.yml`. | Check the Hetzner box; if enabled, keep the row and sign Grafana's DPA; if not, keep the conditional wording. |
| A.9 | **Backup regime undocumented.** If Postgres backups/snapshots exist, erasure must cover or time-bound them. | Art. 17(1) | `pg_data` volume only; no backup config in repo. | Document backups (or their absence) and their retention; fold into §9. |
| A.10 | **Hetzner datacentre location not stated in repo** — "EU/Germany" hosting claim needs confirmation (Hetzner also operates in the US/SG). | §4/§8 accuracy | Only "Hetzner VPS" + CI secret `HETZNER_HOST`. | Confirm the region; then fix §4/§8 wording. |
| A.11 | **Lichess unlink keeps imported games.** `DELETE /lichess/link` detaches the handle but imported `game_events` remain as history. | Transparency | Noted in `llm/seca/auth/router.py:691`. | Either disclose ("imported games remain in your history until account deletion") in §3.2 — recommended — or delete on unlink. |
| A.12 | **Minor verifications.** (a) Android downloadable-fonts provider (`com.google.android.gms.fonts`) appears in font XMLs — confirm bundled fonts prevent runtime Google fetches, else add a §5 row. (b) Controller identity, DPO decision, child-age threshold, supervisory authority are open placeholders. | — | Fonts also bundled per repo. | Verify + fill placeholders. |

## Annex B — INTERNAL: claim → evidence map (REMOVE)

Every load-bearing claim above, pinned to source (all re-verified by
direct read on 2026-07-17, not just survey output).

| Policy claim | Evidence |
|---|---|
| Registration collects email + password only | `llm/seca/auth/router.py` `RegisterRequest`; `docs/API_CONTRACTS.md` §15 |
| PBKDF2-SHA256, 600k iterations, 16-byte salt | `llm/seca/auth/hashing.py:7-53` |
| JWT carries only `player_id`, `session_id`, `exp`; HS256; 24 h | `llm/seca/auth/tokens.py:19,35,38-44` |
| Tokens rotate per request, revocable, sessions pruned/capped | `llm/seca/auth/service.py:112-127,293-342`; `llm/server.py:759-824` |
| DeepSeek request = model + single user message + stream opts; headers = API key only; no user identifiers | `llm/seca/coach/explain_pipeline.py:178-197` |
| Prompt contains FEN, engine signal, chat message + ≤10-turn history, derived skill sketch, coach voice | `llm/seca/coach/chat_pipeline.py:571-752`; `llm/rag/prompts/mode_2/render.py:34-51` |
| Skill sketch is server-derived; client-supplied profile discarded; no email/ID in it | `llm/server.py:1662-1711,1901` |
| Profiling active in prod regardless of SAFE_MODE; RL learning paused | `llm/seca/events/router.py:605,630-647`; `llm/seca/skills/updater.py:40-184` |
| Chat stored verbatim + FEN, server-authoritative | `llm/seca/chat/models.py:48-108` |
| Feedback ≤2000 chars, never logged, never into LLM/coaching | `llm/seca/feedback/models.py`, `router.py:112-128` |
| Weekly digest = timestamp, app version, `left(player_id,8)`, message; via Gmail SMTP to operator | `.github/workflows/feedback-digest.yml:107-116,147-156` |
| Lichess OAuth token used once, revoked immediately, never stored (no token column) | `llm/seca/lichess/client.py:372-394`; `lichess/models.py:46-73` |
| Only Lichess-bound user datum is the username; imports pin `evals=false`; analysis is local | `llm/seca/lichess/client.py:397-538`; `analysis_service.py:15-33` |
| Stockfish runs locally (pooled subprocesses; apt-installed in image) | `llm/server.py:320-368`; `llm/Dockerfile.api` |
| Redis cache keyed on position hash, TTL-bounded, no persistence, no personal data | `llm/seca/engines/stockfish/pool.py:118-262`; `docker-compose.prod.yml:167-169` |
| Request logs include `client_ip`; chat text/emails/FENs not logged | `llm/server.py:744-754`; pipelines log metadata only |
| Android: single `INTERNET` permission; `allowBackup=false`; backup/transfer exclusion rules | `android/app/src/main/AndroidManifest.xml:3,7-8` |
| No analytics/crash/ads SDKs (Android deps; iOS has zero package deps) | `android/app/build.gradle.kts:146-169`; `ios/project.yml` |
| Token in EncryptedSharedPreferences (AES-256-GCM, Keystore master key) | `android/.../TokenStorage.kt:40-55` |
| Cert pinning: 3 SPKI pins on cereveon.com, both platforms | `android/.../network_security_config.xml`; `ios/.../AppConfig.swift`; `docs/THREAT_MODEL.md` §T2 |
| Clients talk only to cereveon.com (+ system-browser Lichess OAuth page) | `build.gradle.kts:49`; `LichessOAuth.kt:37`; `ios/.../AppConfig.swift:9` |
| Billing: client sends purchase token + product ID only; server verifies with Google; no card data | `android/.../BillingApiClient.kt:48-52`; `llm/seca/billing/router.py:200-275` |
| AI output validated pre-display incl. PII firewall; injection sanitisation; deterministic fallback | `docs/ARCHITECTURE.md` (validators, fallback); `docs/THREAT_MODEL.md` §T1 |
| Single-tier Hetzner hosting; Caddy TLS + HSTS; Postgres on-box | `docs/DEPLOYMENT.md` §0; `docker-compose.prod.yml`; `Caddyfile` |
| Account erasure: `DELETE /auth/me` purges every player-linked table (explicit ordered plan + discovery tripwire) | `llm/seca/auth/erasure.py`; `llm/tests/test_auth_account_deletion.py`; contract §41 |
| Player-graph FKs declare `ondelete="CASCADE"`; live Postgres retrofitted at startup | model files (21 FK edges); `llm/seca/auth/router.py::_ensure_fk_delete_cascade` |
| Data-export / portability endpoint still absent (open gap) | route survey 2026-07-17 |
