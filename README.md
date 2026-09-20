# Feed Tray — RSS → GNOME top bar with AI summaries

A personal feed service: fetches your DevOps/SRE/AI RSS feeds on a schedule,
shows them as one button in the GNOME top bar, and AI-summarizes any article
into a markdown file opened with `glow` on click. No dependencies beyond
Debian's stock Python 3, GNOME Shell, and an LLM gateway you already have.

## What it does

1. A systemd **user timer** runs the tracker every 15 minutes.
2. The tracker fetches every feed listed in the OPML, compares entry ids
   against what it has already seen, and writes two state files.
3. A GNOME Shell extension (**feed-tray@local**) renders one panel button:
   - red badge = total unread count
   - menu = one scrollable submenu per feed (`{icon} {AI|SRE} - {title}`)
   - `*` marks unread entries; opened any time, the menu triggers a fresh
     fetch so the list is always current
   - clicking an entry marks it read, AI-summarizes the article to a
     `~/Documents/FeedSummaries/<date>/<slug>.md`, and opens it with `glow`

## Architecture / data flow

```
~/.config/feeds/feeds.opml          your feeds (source of truth)
        │
        ▼  systemd user timer (15 min) + on every menu open
~/.local/bin/feed-tracker.py
        │
        ├── ~/.local/state/feed-notify/state.json   seen entry ids (internal)
        ├── ~/.local/state/feed-notify/status.json  what the panel shows
        │
        ▼
~/.local/share/gnome-shell/extensions/feed-tray@local/   (top-bar UI)
        │  click entry
        ▼
feed-tracker.py --summarize  →  fetch article → extract text
        →  LLM (OpenAI-compatible gateway via env vars or llm.json)
        →  ~/Documents/FeedSummaries/<date>/<slug>.md  →  glow
```

## Files

| File | Purpose |
|---|---|
| `~/.config/feeds/feeds.opml` | feed list + categories (edit this to change feeds) |
| `~/.local/bin/feed-tracker.py` | fetcher / state / AI summarizer |
| `~/.local/share/gnome-shell/extensions/feed-tray@local/` | panel button extension |
| `~/.config/systemd/user/feed-notify.{service,timer}` | 15-min schedule |
| `~/.local/state/feed-notify/state.json` | seen entry ids per feed (auto-managed) |
| `~/.local/state/feed-notify/status.json` | unread + today lists per feed (auto-managed) |
| `~/Documents/FeedSummaries/<date>/` | generated markdown summaries |

## Configurable parameters

### Feeds (`feeds.opml`)
Add/remove `<outline type="rss" category="AI|SRE" text="Title" xmlUrl="..."/>`.
The `category` attribute drives the label: `AI → 🤖`, `SRE → 🛠️` (see
`CAT_ICON` in the tracker). The panel picks up changes within 60 s; new feeds
seed silently on first fetch (no fake unread flood).

### Tracker (`~/.local/bin/feed-tracker.py`, top of file)
| Constant | Default | Meaning |
|---|---|---|
| `MODEL` | `z-ai/glm-5.3-flash` | model used for summaries (any id your gateway serves) |
| `SUMMARY_DIR` | `~/Documents/FeedSummaries` | where markdown files are written |
| `MAX_PENDING` | `50` | unread entries remembered per feed (badge/menu source) |
| `MAX_TODAY` | `15` | "today's entries" kept per feed |
| `MAX_IDS` | `1000` | seen-entry memory per feed (dedupe window) |
| `MAX_ARTICLE` | `8000` | chars of article text sent to the model |
| `CAT_ICON` | `{"AI": "🤖", "SRE": "🛠️"}` | icon per category in menu labels |
| `UA` | `Mozilla/5.0 (feed-tracker/1.0)` | HTTP user agent for fetching |

### LLM endpoint
Resolved in this order:
1. Environment: `FEED_TRAY_LLM_URL` + `FEED_TRAY_LLM_KEY`
2. `~/.config/feeds/llm.json` — `{"baseURL": "...", "apiKey": "..."}`,
   created by `install.sh` (prompts, or reads the same env vars)

Any OpenAI-compatible `/chat/completions` gateway works; `MODEL` picks the
model id it serves.

### Panel extension (`extension.js` / `stylesheet.css`)
| Constant | Default | Meaning |
|---|---|---|
| `REFRESH_SECS` | `60` | poll interval for status.json (badge/menu refresh) |
| stylesheet `.feed-tray-scroll` | `max-height: 320px` | scroll height per submenu |

### Schedule (`feed-notify.timer`)
`OnCalendar=*:0/15` — every 15 minutes, `Persistent=true` catches up after
suspend. Edit and then `systemctl --user daemon-reload && systemctl --user
restart feed-notify.timer`.

## CLI reference

```bash
~/.local/bin/feed-tracker.py                          # fetch + update state
~/.local/bin/feed-tracker.py --summarize FEED_URL "Entry title"   # mark read, summarize, open
~/.local/bin/feed-tracker.py --clear FEED_URL         # mark all of a feed read
```

`FEED_URL` is the feed's `xmlUrl` (also the top-level key in `status.json`).

## Maintenance

- Reload the extension after editing its files:
  `killall -3 gnome-shell` (X11, in-place restart) then
  `gnome-extensions info feed-tray@local` should show State: ACTIVE.
- Logs: `journalctl --user -u feed-notify.service` (fetch errors),
  `journalctl -b /usr/bin/gnome-shell | grep -i feed` (extension errors).
- Status: `systemctl --user status feed-notify.timer`,
  `gnome-extensions info feed-tray@local`.
- If a feed's submenu is missing, it likely failed its last fetches (e.g.
  hnrss 502s) — it reappears automatically on the first successful fetch.

## Known limits

- Each feed always shows at least its most recent entry (`latest`), even when
  it is neither unread nor published today — so no submenu is ever empty.
- A failed article fetch still produces a file: title-only LLM summary plus
  the error note; an LLM failure produces a file with the error and link.
- Summaries need the article's `<link>` in the feed — entries without one are
  summarized from the title only.
