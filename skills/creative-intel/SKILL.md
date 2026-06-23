---
name: creative-intel
description: >
  Read live STOCK BLOOM ad creatives and their performance signals, derive micro-segment
  angles and hooks, and append new video/single-image content ideas to the Google Doc backlog.
  Use when asked to generate more content ideas from what is currently running.
---

# Creative intelligence

Turns live creative signals into the next batch of content ideas, kept precise to Soo Cheng
老师's audience framework even under broad/Advantage+ (Andromeda) delivery.

## Deterministic path (preferred for routines)
```bash
source .venv/bin/activate 2>/dev/null || ./setup.sh
python -m adbot intel            # add --dry-run to preview ideas without writing the Doc
```
This reads each managed ad's spend/leads/CPL, asks the model (using `prompts/intel_system.md`
+ `config/audience.md`) for 6-10 new ideas, and appends the non-duplicate ones to the
idea-backlog Google Doc with a date stamp and a generation-ready prompt per idea.

## Ad-hoc path (interactive session with the Meta connector)
When exploring by hand, the Meta MCP can read creatives directly:
- `ads_get_creatives` / `ads_get_ad_videos` / `ads_get_ad_images` — what's running and the copy.
- `ads_get_ad_entities` — per-ad CTR / CPL / spend over a window.
Cluster by angle and CPL, identify which `encoded_audience_signals` (from the caption log)
actually convert, then propose new angles. Append results with `adbot intel` so they land in
the same Doc in the same format.

## Rules
- Stay compliant: no guaranteed/expected profit, no get-rich-quick, no unrealistic claims.
- Never overwrite existing ideas — the backlog is append-only and de-duplicated by title.
