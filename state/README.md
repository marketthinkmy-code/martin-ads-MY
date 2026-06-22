# state/

Committed JSON ledgers used as a within-checkout cache and a human-readable audit trail.

| file | purpose |
|---|---|
| `media_cache.json` | Drive file id → uploaded `video_id` / `image_hash`, so `sync` never re-uploads |
| `entities.json` | campaign / ad set / ad ids created by the last `build` |
| `doc_index.json` | ids of the Google Docs created for the caption log + idea backlog |
| `pause_log.json` | append-only audit of every pause/resume (CPL, weekly_off, weekly_on) |

**Cross-run coordination does NOT depend on these files.** The cloud routines start from a
fresh clone, so the weekly OFF/ON cycle coordinates via a Meta **ad label**
(`ADBOT_WEEKLY_OFF`) instead — Wednesday tags+pauses what is live, Thursday resumes exactly
the tagged ads and clears the tag. `build`/`sync` reconcile from Meta by name/cache. These
files are the local record; the authoritative state lives in Meta itself.
