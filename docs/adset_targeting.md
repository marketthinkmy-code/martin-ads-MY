# Ad-set targeting playbook — Martin MY (operator's manual setup)

On 2026-06-23 the operator manually refined the live `1-1-10` ad set
`120246560545590575` in Ads Manager. **Every future ad set should match this.**
The bot's `build` does NOT yet apply these automatically (see *Encoding status*),
so until that is wired in, treat this as a **post-build checklist**.

## The three settings

### 1. Customer lifecycle strategy = "Get conversions from all audiences"
Advantage+ Sales Campaign (ASC) ad-set control. "All audiences" = no new-vs-existing
budget split — Meta optimizes across everyone, and the **exclusions** below do the
"reach fresh prospects" filtering instead.

### 2. Advantage+ audience = ON
`targeting_automation.advantage_audience = 1`. With it ON, age / locale become
**suggestions**, not hard caps — Meta can expand beyond them. Min age now shows **25**
(a suggestion).

> ⚠️ This is exactly why the very first API build rejected
> `advantage_audience=1` together with a **hard** `age_min=35`
> (*"You can add a higher minimum age as a suggestion instead"*). With Advantage+
> audience ON you must express age as a **suggestion** (min 25), not a hard floor.
> We shipped build #1 with `advantage_audience=0` + hard 35–65; the operator then
> switched to **Advantage+ ON / min 25** in the UI. That is the desired end state.

### 3. Exclusions (`excluded_custom_audiences`) — drop existing leads + buyers
| Audience | ID | Type |
|---|---|---|
| 马丁 15days complete registration | `120224052749710575` | PLATFORM (website / pixel) |
| MARTIN MYSG PAID CUSTOMER - 13 APR | `120242141920810575` | CUSTOM (customer list) |

Purpose: don't pay to reach people who already registered or already bought.

## Encoding status (TODO to auto-apply in `build`)
`settings.Targeting.to_spec()` currently emits only
`geo_locations / age_min / age_max / targeting_automation.advantage_audience / locales`.
To auto-apply the above it must additionally:
- emit `excluded_custom_audiences` (the two IDs above),
- set `advantage_audience = 1` with `age_min = 25` (as a suggestion, not a hard floor),
- set the ASC customer-lifecycle control to "all audiences".

Pending operator go-ahead before changing build behaviour for future batches.

## Safety — the live ad set is not at risk
`build` reuses an existing ad set **by ID** and never rewrites its targeting on reuse;
the monitor cron only changes **budget / status**, never targeting. So nothing the bot
does will undo the operator's manual edits on `120246560545590575`.
