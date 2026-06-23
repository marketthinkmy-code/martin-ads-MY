# martin-ads-MY — Meta Ads Automation (adbot)

Hands-off Meta (Facebook/Instagram) advertising for **[BRAND]** (Martin / Malaysia), run in
the cloud and monitored from the Claude mobile app. <!-- TODO(martin): set [BRAND]/[PRODUCT]. -->
A 1:1 clone of the soocheng-ads adbot — code, guardrails, and cron schedules are unchanged;
only `config/config.yaml`, `config/audience.md`, and `prompts/` are project-specific.

**What it does**
1. **sync** — downloads creatives (videos / single images / multi-image carousels) from a
   Google Drive folder and uploads them to your Meta ad account.
2. **build** — creates the "Meta Entrepreneur" **1-1-10** structure (1 CBO campaign, 1 broad
   ad set targeting Malaysia 25+, 10 ads), writes each ad's caption + headline to a Google
   Doc, and auto-activates at a small CBO budget.
3. **monitor** — pauses any ad whose **CPL** exceeds your threshold.
4. **weekly_off / weekly_on** — pauses ALL ads every **Wed 15:00 GMT+8** and resumes exactly
   those every **Thu 00:00 GMT+8**.
5. **intel** — reads live creative signals, derives micro-segment angles/hooks, and appends
   new content ideas to a Google Doc.

## Architecture (why code + token, not just MCP)

A committed Python package (`adbot`) does all the work through the **official Meta Marketing
(Graph) API** + Google APIs. This is deliberate: the Meta MCP connector cannot upload
videos/images or build static carousels, and money-touching guardrails (CPL pause, weekly
OFF/ON) must be deterministic — not an LLM deciding each run. **Claude Code Routines** are the
cloud scheduler and the mobile-visible run log; each routine just runs one command
(`python -m adbot <cmd>`).

```
src/adbot/
  clients/        graph.py (Meta) · drive.py · gdoc.py · llm.py (Claude)
  drive_sync.py   media.py   creative_groups.py   captions.py
  build_1_1_10.py monitor_cpl.py   weekly_off.py   weekly_on.py   creative_intel.py
  docwriter.py    commands/   __main__.py
config/  config.yaml (settings) · audience.md (your precise-audience framework — you fill this)
prompts/ caption_system.md · intel_system.md
skills/  meta-ops · creative-intel
state/   json ledgers (cache + audit)
tests/   offline unit tests
```

## Setup

```bash
./setup.sh                 # venv + install + run offline tests
```
Then provide your inputs:
1. **config/config.yaml** — ad account id, page id, pixel id, landing URL + conversion
   domain, CBO daily budget (default 250 MYR), CPL threshold (default 40 MYR), Drive folder id.
2. **config/audience.md** — paste your precise-audience framework (remove every
   `TODO:` marker; captions refuse to run until you do).
3. **.env** — copy from `.env.example` and set the three secrets:
   - `META_SYSTEM_USER_TOKEN` (scopes: `ads_management, ads_read, business_management,
     pages_read_engagement`)
   - `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (base64 of the service-account key: `base64 -w0 key.json`;
     enable Drive + Docs APIs and **share the Drive folder + both Google Docs with its email**).
     Locally you may instead set `GOOGLE_SERVICE_ACCOUNT_JSON` to a file path or inline JSON.
   - `ANTHROPIC_API_KEY`
4. Confirm whether your region requires the `FINANCIAL_PRODUCTS_SERVICES` special ad category
   for trading ads (set it in `config.yaml`; note it can force targeting broad and override 25+).

Validate, then dry-run, then go live:
```bash
source .venv/bin/activate
python -m adbot doctor                       # all checks must pass
python -m adbot sync   --dry-run             # list + group assets, no upload
python -m adbot build  --dry-run             # print exact payloads, create nothing
python -m adbot sync                         # upload media to Meta
python -m adbot build                        # create + auto-activate the 1-1-10
```

## Commands

| Command | Purpose |
|---|---|
| `python -m adbot doctor` | Preflight: token, account, page, pixel, Drive, Docs, audience |
| `python -m adbot sync [--dry-run]` | Download Drive creatives, upload to Meta, group into 10 |
| `python -m adbot build [--dry-run]` | Create (+auto-activate) the 1-1-10 + write caption log |
| `python -m adbot monitor [--dry-run]` | Pause ads with CPL above threshold |
| `python -m adbot weekly_off [--dry-run]` | Pause ALL live ads (Wed kill switch) |
| `python -m adbot weekly_on [--dry-run]` | Resume exactly the ads weekly_off paused |
| `python -m adbot intel [--dry-run]` | Read creatives → new content ideas → Google Doc |

## Cloud routines (the "Cloud Cron")

Create three routines at **claude.ai/code/routines** (or `/schedule`): attach this repo, set
the three secrets as environment variables, and add `graph.facebook.com`, `www.googleapis.com`,
`api.anthropic.com` to the environment's network allowlist. Each run appears as a session in
the Claude mobile app, ending with a one-line `SUMMARY:`.

| Routine | Local (GMT+8) | UTC cron | Command |
|---|---|---|---|
| Weekly OFF | Wed 15:00 | `0 7 * * 3` | `python -m adbot weekly_off` |
| Weekly ON  | Thu 00:00 | `0 16 * * 3` | `python -m adbot weekly_on` |
| CPL monitor | hourly | `0 * * * *` | `python -m adbot monitor` |

> Thu 00:00 GMT+8 = Wed 16:00 UTC. The web form converts your local time to UTC automatically;
> if you set cron directly via `/schedule`, use the UTC values above. Verify after creating.

The weekly OFF/ON pair coordinates with the `ADBOT_WEEKLY_OFF` Meta ad label, so it works
across fresh cloud clones with no shared state file. Ads paused by the CPL monitor or by you
stay off (they aren't tagged).

## Budget & CPL (this project)

Set the CBO daily budget, per-ad-set minimum, and the CPL threshold / min-spend in
`config/config.yaml` (`meta.budget.*`, `kpi.*`). <!-- TODO(martin): document the chosen
numbers here once confirmed. --> The monitor pauses an ad once its CPL exceeds
`kpi.cpl_threshold_myr`, judged only after it has spent at least `kpi.cpl_min_spend_myr`.

## Safety & compliance

- Everything is created **PAUSED**; `build` activates the hierarchy only because
  `build.activate_after_build: true`. Set it to `false` to keep everything paused for review.
- `monitor` only ever pauses; it never re-activates.
- **Ad-policy compliance:** caption generation forbids guaranteed/expected results,
  "get-rich-quick", and unrealistic-results claims, and is education-framed. Disapproved ads
  would defeat the automation, so compliance is enforced in `prompts/caption_system.md`.
- **Honest limit:** this *biases* Meta's broad/Advantage+ delivery toward your audience via
  creative semantics (Andromeda) — it does not *force* delivery to a precise audience.

## Tests
```bash
source .venv/bin/activate && python -m pytest -q   # 27 offline tests, no network
```
