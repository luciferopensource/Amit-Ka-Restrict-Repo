# AGENTS.md — Telegram Restrict Bot (Customised Deployment)

**Scope:** instructions for `amit_restrict_bot` only. Workspace rules live in [`../AGENTS.md`](../AGENTS.md) — read that file whenever it is not already in your context.

## 1. Identity and purpose
A customised deployment of the Restrict engine that saves and forwards restricted media out of private Telegram channels and groups. It adds per-user caption templates, per-user custom thumbnails and batch link-range synchronisation (`/batch <start-link> <end-link>`) to a sovereign single-file bot.

## 2. Independent project boundary
One of the 18 independent projects in the Lucifer Open Source workspace. It shares no manifest, database, session or deployment with `../Restrict-main/` even though both derive from the same upstream engine — this copy carries its own configuration and schema. Never copy a session string, database URI or thumbnail record between the two.

## 3. Verified technology stack
- Python 3.10+; `requirements.txt` pins `pyrofork==2.3.45`, `pyromod`, `motor`, `aiohttp`, `psutil`, `dnspython`, `uvloop`.
- MongoDB via Motor (async); uvloop event loop.
- Docker (`Dockerfile`) and Nixpacks (`nixpacks.toml`) container builds; no aiogram — the bot interface and forwarding engine live in one module.

## 4. Entry points
- `restrict_bot.py` — the only executable entry point (~8,000 lines: client init, handlers, forwarding engine, caption/thumbnail logic).
- `tests/test_forwarder_engine.py` — pytest suite for the forwarding engine.

## 5. Module responsibilities
- `restrict_bot.py` — monolithic application: startup, user authorisation, `/batch` range parsing, media download/upload, caption rendering.
- `assets/` — static brand assets (thumbnails, formatting material). Do not regenerate silently.
- `tests/` — engine tests; keep them runnable without live credentials by mocking the client.

## 6. Data and integration boundaries
- MongoDB collections described in `PROJECT_MEMORY.md`: `users`, `settings`, `custom_thumbnails`, `forward_history`.
- One MTProto user session string (env-configured) performs all media work; there is no worker pool.
- Telegram is the only external integration; no webhooks, no HTTP API.

## 7. Architectural invariants
- Keep the single-file structure and its section ordering; extend it rather than splitting it into a package without explicit approval.
- Authorisation checks must gate every forwarding command — never bypass them to make a test pass.
- Preserve batch-range semantics: inclusive ranges, link parsing, and per-range state.
- Keep the caption/thumbnail templating per user; never hard-code one user's template globally.

## 8. Mandatory documentation reading
Read in order: [`../AGENTS.md`](../AGENTS.md), `PROJECT_MEMORY.md`, `GEMINI.md`, `README.md`, then the workspace profile `../LUCIFER_WORKSPACE_MEMORY/projects/amit-restrict-bot/00_PROJECT_MASTER_PROFILE.md`. Treat those memory files as read-only input for a code task.

## 9. Coding and modification rules
- Read the relevant region of `restrict_bot.py` before editing; it is one large module, so never patch by guessing offsets.
- Preserve existing formatting, handler names and user-facing strings unless the task asks for UI changes.
- No new dependency without justification: the runtime is a constrained container.

## 10. Testing and verification
- Run `pytest tests/` for engine-affecting changes; report the real outcome, including failures and skips.
- Never describe a test suite as passing without executing it in this session.
- FloodWait, quota and session-expiry behaviour cannot be verified offline — state that as untested.

## 11. Security restrictions
- Never print, log or commit `SAVE_RESTRICTED_MEDIA_SESSION`, the Mongo URI, API hashes or `.env` contents.
- Never copy this project's session material into another project.
- No destructive MongoDB operations; no re-seeding of live collections.

## 12. Git and backup rules
- Repository root is this directory (branch `main`). Check `git status` first; pre-existing modifications are user-owned.
- Commit or push only when explicitly authorised; never force-push, rewrite history or change remotes.
- `.gitignore` ignores `AGENTS.md`; a root-level `!AGENTS.md` exception added in Phase 6 keeps this file versionable. Do not widen that exception to other Markdown or to `.agents/`.

## 13. Agent handoff
Before switching agents mid-task, record in `PROJECT_MEMORY.md`: branch and commit, modified/untracked paths, work done, work remaining, tests actually run.

## 14. Memory and status upkeep
Update `PROJECT_MEMORY.md` only for durable architecture or decisions, and a project status document for progress. Never record conversation history as project knowledge; never claim unexecuted verification.
