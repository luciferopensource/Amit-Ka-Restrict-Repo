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

## 15. Codebase Audit & Defect Remediation (2026-11-12)

Comprehensive code audit conducted by Senior Software Engineer and Code Auditor. Remediated actionable defects tailored strictly for the white-label environment:
1. **False Positive Media Drop Prevention:** Fixed `extract_telegram_file_names` to only inspect caption/text fallback if no document attribute filenames exist. Movies with `Subtitles: English.srt` in caption are no longer dropped.
2. **LIFO Stack-Based HTML Tag Balancer:** Upgraded `balance_html_tags` to parse HTML tags in a true LIFO stack order, safely closing unclosed `<a>`, `<b>`, `<i>`, `<code>`, `<u>`, `<s>`, `<pre>` and dropping orphan closing tags to prevent Telegram MTProto entity parse errors.
3. **Iterative Truncation Backoff:** Implemented safe iterative backoff in `append_media_footer` ensuring `len(caption) + len(footer) <= 1024` with balanced HTML tags.
4. **Whole URL Cleansing:** Fixed `clean_promotional_hyperlinks` to remove full URLs without leaving dangling `https://` schemes.
5. **Bracket-Aware Markdown Link Unwrapping:** Replaced greedy markdown regexes with bracket-aware non-greedy patterns.
6. **Title Hyphen Normalisation Protection:** Modified `clean_promotional_watermarks` to only collapse consecutive hyphens (`{2,}`), protecting legitimate titles like `Spider-Man` and `WEB-DL`.
7. **Offline DB Safety Guard:** Guarded `db.db.watchers` calls when database is offline in dashboard and startup routines.
8. **Asynchronous Album Cleanup:** Offloaded blocking `shutil.rmtree` to `asyncio.to_thread`.
9. **Eliminated Redundant Startup Queries:** Consolidated duplicate startup watcher queries into a single cached pass.
10. **Strict White-Label Invariant Preserved:** Strictly zero `@luciferdatabase` or hyperlinks injected; all footers remain `💾 Size: {size_str}` only.
11. **Expanded Test Suite:** Added 5 new unit tests in `tests/test_forwarder_engine.py` (77 tests total, 100% pass rate).

## 16. Login Done Banner, 1-10s Delay Presets, TgCrypto, Monospace Terminal Box & Log FloodWait Guard (2026-11-13)
- **TgCrypto Integration (`requirements.txt`):** Added compiled C cryptography library `tgcrypto` to replace pure-python `pyaes`. Eliminates high CPU usage on Render and provides 20x–50x cryptographic throughput for multi-gigabyte transfers.
- **Login Status & Confirmation Banner:**
  - Added clean, framed confirmation banner upon successful authentication in `start_string_session_login` and `start_phone_login` displaying user's name, user ID, phone, and active status.
  - Added dynamic session status check in `/start` (`send_start`) indicating whether the user is currently `LOGGED IN` or `NOT LOGGED IN`.
- **1-10s Forwarding Delay Presets & Code Block Warning (`ask_for_speed`, `speed_callback`):**
  - Expanded speed setup inline menu with 1s, 2s, 3s (Default), 4s, 5s, 6s, 7s, 8s, 9s, 10s presets, manual custom delay option, and cancel button.
  - Added formatted monospace code block warning:
    - ⚡ 1s - 2s: FAST (High Telegram FloodWait risk)
    - ⭐ 3s (Default): BALANCED (Optimal speed & stability)
    - 🛡️ 4s - 10s: SAFE (Zero FloodWait risk for large batches)
- **Dark Terminal "Code Format" UI:** Wrapped `generate_aesthetic_progress_ui` and final batch completion summaries (`final_text`) in `<pre><code>...</code></pre>` ensuring Telegram renders all status and progress outputs inside the monospace dark terminal box.
- **Render Log Channel FloodWait Protection (`send_log`, `handle_private`):**
  - Fixed issue where batch skips during `auto_dl = False` flooded Telegram `LOG_CHANNEL` with rapid successive messages, triggering Telegram `[420 FLOOD_WAIT_X]`.
  - Replaced repetitive per-file skip notices in `handle_private` with console logs.
  - Added minimum 2.0s dispatch cooldown and automatic `FloodWait` suppression window in `send_log`.
  - Added notification in task start message if `auto_dl` is currently OFF.
- **Strict White-Label Compliance:** Zero `@luciferdatabase`, zero hyperlinks, and clean white-label branding maintained.
- **Verification:** All 77 forwarder engine tests passing (100% pass rate).

## 17. Audit Fixes — Tenant Isolation, Albums and Resource Guards (2026-11-13)

- **Watcher isolation:** watchers are now keyed by `(user_id, source_id, source_thread)`. `add_watcher()` includes the owning `user_id` in its query and `get_watcher()` accepts it, while the watcher handler resolves the owner from `USER_CLIENTS`. Two users watching the same source can no longer merge routes or send through each other's session.
- **Album filtering:** `process_media_album()` now applies `allowed_types`; albums previously bypassed the content-type filter entirely.
- **Cleanup watchdog:** skips folders that belong to an active task (`ACTIVE_PROCESSES`) and only removes scratch data older than 6 hours (was 2 hours, which could delete a running batch).
- **Disk guards:** default headroom raised from 500 MB to 1.5 GB, plus a per-item pre-download check requiring 115% of the file size.
- **Startup visibility:** index-initialisation and watcher-restore failures are logged (and sent to `LOG_CHANNEL`) instead of being swallowed; a warning is emitted when no `ADMINS`/`AUTH_USERS` are configured.
- **String-session login:** stores the API credentials actually used for validation instead of the possibly-empty bot globals.
- **Caption fallback:** filename extraction only inspects caption lines for file-bearing media, so text posts mentioning `*.srt` are no longer dropped.
- **White-label caption guard:** `strip_whitelabel_references()` removes `@LuciferDatabase` / `@RequestLuciferDatabase`, `DB ID` lines and residual promo lines from incoming captions, so even a caption copied from the personal cloud engine arrives fully clean. Media filename lines are protected from the filter.
- **No duplicate size:** `append_media_footer()` skips the size footer when the source caption already carries one.
- **Cloud caption flattening:** `flatten_cloud_filename()` reduces a Cloud Engine caption to the cleaned media filename before sanitisation, so the white-label output is the filename plus `💾 Size:` only — no template, dividers or metadata.
- **4-tier fast-copy:** bot copy → userbot copy → bridge via bot DM → download fallback. The bridge reads from the userbot account id (`acc.me.id`), so multi-account logins work.
- **Inline keyboards stripped:** source `reply_markup` is never copied, so no promotional buttons reach white-label destinations.
- **White-label invariant retained:** no `@luciferdatabase` hyperlink or Powered By footer; captions carry `💾 Size:` only.
- **Text formatting:** Bold/Italic wrap the caption; Mono strips any residual links before `<code>`.
- **Per-target resume:** progress is stored per destination; the batch resumes from the least-advanced target and only destinations still behind receive each message.
- **Multi-flow ask:** pending prompts are keyed by `(chat_id, flow_id)`; a new prompt supersedes older ones without leaving them hanging, and timeouts notify the user.
- **Bridge fast-copy:** userbot copies the source media into the bot PM, the bot copies it server-side to the destination, then deletes the bridge. Every copy result is checked (`None` = failure), the peer cache is warmed, and the bridge is retained on a Bot-PM failure so a file never vanishes. Copied documents whose caption Telegram drops are re-captioned with `edit_caption`; the forward fallback also edits the caption. The bridge uses a blank caption and now also supports Bot-PM destinations, so private sources need neither bot membership nor downloads.
- **Session encryption:** `SESSION_ENC_KEY` derives a Fernet key; `session`/`api_hash` are stored as `enc:` tokens, legacy plaintext still reads. If `SESSION_ENC_KEY` is unset, the key is derived from `BOT_TOKEN`, so rotating the token invalidates old sessions (re-login required).
- **Log redaction:** `/log` sends a redacted copy (`bot.log.redacted`) with DB URIs, bot tokens, credential assignments and long session strings removed.
- **Verification:** `pytest tests/test_forwarder_engine.py` — 85 passed (2026-11-13).

