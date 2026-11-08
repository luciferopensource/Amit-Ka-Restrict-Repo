# Project Memory — Telegram Restrict Bot (Customised Private Deployment)

**Parent Brand:** Lucifer Open Source
**Repository Directory:** `amit_restrict_bot`
**Git:** Branch `main`, HEAD `c7f7839` at time of verification
**Language & Conventions:** British English (`behaviour`, `customise`, `initialise`), asynchronous Python, Pyrofork MTProto
**Last Verified:** 2026-11-06 (direct source inspection)

---

## 1. Project Identity

- **Directory:** `amit_restrict_bot` — a Git repository in its own right, independent of `../Restrict-main/`.
- **Product name in source/README:** *Telegram Restrict Bot*; deployed as a customised private instance.
- **Derivation:** same upstream engine family as `../Restrict-main/`, but this copy carries its own configuration surface, database schema and handler set. It shares no manifest, database, session or deployment with that project.

## 2. Purpose and Scope

Saves and forwards media that ordinary forwarding cannot reach — protected/restricted content inside private Telegram channels and groups — into operator-controlled destinations, with per-user presentation controls.

In scope:
- Link-driven extraction (`/dl <t.me link>` or reply-to-link).
- Watcher-based channel synchronisation: a saved source → destination pairing that keeps copying new posts.
- Saved source/destination channel registry, including forum topics and join-by-invite flows.
- Per-user caption replacement rules (watermark/promo stripping and rebranding) and per-user custom thumbnails.
- Multi-account MTProto login (string session or phone + OTP + 2FA) with per-user client instances.
- Operational surfaces: `/status`, `/tasks`, `/log`, `/stats`, `/botstats`, `/pixel`, `/broadcast`, `/cancel`, `/toggledl`.

Out of scope: worker-bot pools, channel provisioning, inline search indexing, web UI.

## 3. Verified Technology Stack

| Layer | Verified detail |
|---|---|
| Runtime | Python 3.10 (`Dockerfile`: `python:3.10-slim`; `nixpacks.toml`: `python310`) |
| MTProto | `pyrofork==2.3.45`, `pyromod`, `pyaes` |
| Database | MongoDB through `motor` (async), `dnspython` for SRV resolution |
| Web/health | `aiohttp` health endpoint on `$PORT`, default `8080` |
| Diagnostics | `psutil` (RAM/CPU reporting in `/botstats`, `/status`) |
| Event loop | `uvloop` on Linux |
| Packaging | `Dockerfile`, `nixpacks.toml`, `requirements.txt` |

`requirements.txt` pins `pyrofork`; the remaining entries are unpinned. There is no `aiogram` dependency — bot interface and forwarding engine live in the same module.

## 4. Entry Points

| Path | Role |
|---|---|
| `restrict_bot.py` | The only executable entry point. 8,069 lines: configuration, database layer, client lifecycle, handlers, transfer engine, sanitiser. |
| `tests/test_forwarder_engine.py` | Pytest suite (12 test functions) covering blocklist, safe whitelist, metadata inspection and caption/filename sanitisation. |
| `Dockerfile` / `nixpacks.toml` | Container builds, both starting `restrict_bot.py`. |

## 5. Architecture and Module Responsibilities

The project is deliberately monolithic: one module, ordered sections, no package split.

- **Configuration section** — reads `API_ID`, `API_HASH`, `BOT_TOKEN`, `DB_URI`, `DB_NAME`, `STRING_SESSION`, `LOG_CHANNEL`, `STATUS_UPDATE_INTERVAL`, `LOGIN_SYSTEM`, `ERROR_MESSAGE`, `WAITING_TIME`, and the authorisation registers `OWNER_ID`, `ADMINS`, `SUDOS`, `AUTH_USERS` (comma/semicolon/space separated).
- **`Database` class** — Motor client with a 5-second server-selection timeout; indexes on `users.id` (unique), `watchers(source_id, source_thread)`, `sync_progress(user_id, source_id, dest_id)` and `saved_channels(user_id, channel_id, channel_type, thread_id)` (unique).
- **Client lifecycle** — a module-level `USER_CLIENTS` registry holds one user client per authorised Telegram account; `/login`, `/logout`, `/accounts`, `/switchaccount`, `/kickuser` manage them. `LOGIN_SYSTEM` can disable self-service login.
- **Transfer engine** — `/dl` resolves the link, calls `check_link_restriction`, then downloads and re-uploads with bounded concurrency. Global counters: `PENDING_TASKS`, `ACTIVE_TASKS`, `ACTIVE_PROCESSES`, `TASK_QUEUE`, `CANCEL_FLAGS`, `MAX_CONCURRENT_TASKS_PER_USER`.
- **Sanitiser** — `BLOCK_PART_REGEX`, `WATERMARK_CLEAN_REGEX` and safe-whitelist logic strip source branding, promo links and file-name tags; the test suite asserts that no `@luciferdatabase` branding or hyperlinks survive.
- **Presentation** — per-user `custom_thumb` field on the `users` document; caption templates from the `replacements` collection.
- **`assets/`** — static branding material; do not regenerate silently.

## 6. Execution Flows

**Startup:** environment read → authorisation registers built → `Database.init_indexes()` → bot client started → optional aiohttp health server bound to `$PORT` → handlers registered → task queue consumer begins.

**Link extraction:** user sends `/dl <link>` (private or group) → authorisation gate → `check_link_restriction` classifies the link → download via the requesting user's client → sanitiser rewrites caption/filename → upload to the chosen destination → progress message edited in place → per-user task counters released in a `finally` block.

**Watcher sync:** `/watch` stores a source/destination pairing with allowed media types, delay and text format → sync state keyed `(user_id, source_id, dest_id)` in `sync_progress` → repeated passes resume from the stored offset rather than re-copying.

## 7. Database and API Boundaries

- **Collections (verified in source):** `users`, `settings`, `replacements`, `stats`, `watchers`, `sync_progress`, `saved_channels`.
- `users` holds the per-account record, including the session string and the `custom_thumb` file ID. There is **no** separate `custom_thumbnails` or `forward_history` collection — those names in earlier memory were inaccurate.
- **Database name:** `DB_NAME`, defaulting to `restrict_bot`.
- **External integration:** Telegram MTProto only. No webhooks and no public HTTP API; the aiohttp server exists solely as a hosting health probe.
- **No disk staging:** media streams from Telegram to Telegram; `assets/` holds static branding only.

## 8. External Integrations

| Integration | Direction | Notes |
|---|---|---|
| Telegram MTProto (Pyrofork) | bidirectional | Bot client for the UI; one user client per logged-in account for media work |
| MongoDB (Motor) | read/write | Authoritative store for users, sessions, watchers and sync offsets |
| Hosting health probe | inbound | `aiohttp` 200 response on `$PORT` for Koyeb/Render/Sevalla uptime checks |

## 9. Architectural Invariants

- Keep the single-file structure and section ordering; do not split into a package without explicit approval.
- Every command path is gated by `is_authorized()`; never bypass it to make a flow work.
- Keep state per user: caption replacements and thumbnails must never be global.
- Preserve watcher semantics — link parsing, stored offsets, resumable sync.
- Preserve sanitiser behaviour; the test suite encodes it as a requirement, not an option.
- Respect FloodWait with non-blocking sleeps; the login flow emulates Telegram Desktop headers deliberately.

## 10. Security Requirements

- Never print, log or commit `STRING_SESSION`, `BOT_TOKEN`, `API_HASH`, `DB_URI` or `.env` contents.
- Session strings live in the `users` collection; they are credentials and must not be copied to any other project.
- No destructive MongoDB operations against a live deployment; no re-seeding of `users` or `saved_channels`.
- Authorisation must fail closed when `OWNER_ID`/`ADMINS` are unset.

## 11. Existing Development Decisions

- **Monolith retained.** One module keeps deployment to a small container simple, at the cost of navigability.
- **Multi-account over a single shared session.** Earlier memory described one `SAVE_RESTRICTED_MEDIA_SESSION`; the current source creates per-user clients at runtime, with a legacy `STRING_SESSION` bootstrap.
- **Sanitise, do not rebrand blindly.** A blocklist plus an explicit safe whitelist with regex cleaning, rather than blanket replacement.
- **Database optional.** `Database` degrades to `None` when no URI is supplied, so the bot can start without MongoDB.

## 12. Documentation References

- [`README.md`](./README.md) — feature and deployment overview.
- [`GEMINI.md`](./GEMINI.md) — brand, spelling and async-safety rules.
- [`AGENTS.md`](./AGENTS.md) — scoped agent instructions.
- [`../AGENTS.md`](../AGENTS.md) — workspace governance.
- [`../LUCIFER_WORKSPACE_MEMORY/projects/amit-restrict-bot/00_PROJECT_MASTER_PROFILE.md`](../LUCIFER_WORKSPACE_MEMORY/projects/amit-restrict-bot/00_PROJECT_MASTER_PROFILE.md) — central profile.

## 13. Corrections and Open Items

- Environment variable names in earlier memory (`SAVE_RESTRICTED_MEDIA_SESSION`, `MONGO_URI`) and in `AGENTS.md` are **not** present in the current source, which uses `STRING_SESSION`, `DB_URI` and `DB_NAME`. The agent instructions were left untouched in this phase and still carry the legacy names.
- The `/batch <start-link> <end-link>` handler described in earlier memory no longer exists; range work is now expressed through `/dl` on link ranges and through watchers.
- `tests/test_forwarder_engine.py` was **not executed** during this verification. Its existence and scope were confirmed by reading it; no pass/fail claim is made.

## 14. Caption Sanitisation & White-Label Compliance (2026-11-08)

- **Foreign Promotional Footers Stripping:** Added `strip_existing_media_footers()` in `restrict_bot.py`. Drops incoming `Size: ...`, `Join ...`, and promotional footer lines from other channels/bots (e.g. `[\[ ᴍᴏᴠɪᴇꜱ ʙᴏᴛᴢ ᴜᴘᴅᴀᴛᴇ \]](...)`).
- **Markdown Title Unwrapping:** Updated `clean_promotional_hyperlinks()` to unwrap full-line markdown titles with nested brackets, preserving filenames cleanly.
- **Strict White-Label Compliance:** Appends only `💾 Size: {size_str}` without any personal channels, `@luciferdatabase` branding, or hyperlinks.
- **Test Suite Verification:** Ran `pytest tests/test_forwarder_engine.py`; 72/72 tests passed (100% pass rate).
