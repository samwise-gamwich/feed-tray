#!/usr/bin/env python3
"""Fetch RSS/Atom feeds from OPML; track new + today's entries; AI-summarize.

Files:
  ~/.local/state/feed-notify/state.json   seen entry ids per feed (internal)
  ~/.local/state/feed-notify/status.json  per feed: "items" (unread, for badge)
                                          and "today" (today's entries w/ read flag)
The feed-tray@local GNOME extension renders status.json.

Commands:
  (no args)               fetch feeds, update state + status
  --summarize URL TITLE   mark read, AI-summarize the article into a markdown
                          file under ~/Documents/FeedSummaries/<date>/, open it
  --clear URL             mark all of a feed's entries read
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

OPML = Path.home() / ".config/feeds/feeds.opml"
STATE = Path.home() / ".local/state/feed-notify/state.json"
STATUS = Path.home() / ".local/state/feed-notify/status.json"
LLM_CFG = Path.home() / ".config/feeds/llm.json"
SUMMARY_DIR = Path.home() / "Documents/FeedSummaries"
MODEL = "z-ai/glm-5.3-flash"
MAX_IDS = 1000            # cap remembered entry ids per feed
MAX_PENDING = 50          # cap unread titles per feed
MAX_TODAY = 15            # cap today's entries per feed
MAX_ARTICLE = 8000        # chars of article text sent to the model
UA = {"User-Agent": "Mozilla/5.0 (feed-tracker/1.0)"}


# ---------------------------------------------------------------- fetching

CAT_ICON = {"AI": "🤖", "SRE": "🛠️"}


def feeds_from_opml(path):
    """Yield (display_name, url); display name = '{icon} {category} - {title}'."""
    for outline in ET.parse(path).iter("outline"):
        if url := outline.get("xmlUrl"):
            title = outline.get("text") or url
            cat = outline.get("category") or "AI"
            icon = CAT_ICON.get(cat, "")
            yield f"{icon} {cat} - {title}", url


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def entry_id(entry):
    """Stable id for an RSS <item> or Atom <entry>."""
    for tag in ("guid", "id"):
        node = entry.find(f"{{*}}{tag}")
        if node is not None and (node.text or "").strip():
            return node.text.strip()
    for node in entry.iter("{*}link"):
        href = node.get("href") or node.text
        if href and href.strip():
            return href.strip()
    title = entry.findtext("{*}title") or ""
    return "h:" + hashlib.sha256(title.encode()).hexdigest()


def entry_title(entry):
    return (entry.findtext("{*}title") or "").strip() or "(untitled)"


def entry_link(entry):
    for node in entry.findall("{*}link"):  # findall supports the {*} wildcard
        href = node.get("href") or node.text
        if href and href.strip():
            return href.strip()
    return ""


def entry_date(entry):
    """Return entry publish date as an aware datetime, or None."""
    raw = None
    for tag in ("pubDate", "published", "updated", "date"):
        node = entry.find(f"{{*}}{tag}")
        if node is not None and (node.text or "").strip():
            raw = node.text.strip()
            break
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)  # RFC 822 (RSS)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def entries(feed_xml):
    root = ET.fromstring(feed_xml)
    return [(entry_id(e), entry_title(e), entry_link(e), entry_date(e))
            for e in root.iter() if e.tag.rsplit("}", 1)[-1] in ("item", "entry")]


def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


# ------------------------------------------------------- main update cycle

def run():
    state = load(STATE, {})
    status = load(STATUS, {})
    today = date.today()
    # sync display names (icon + category) even for feeds that fail to fetch
    for name, url in feeds_from_opml(OPML):
        if url in status:
            status[url]["name"] = name
    for name, url in feeds_from_opml(OPML):
        try:
            current = entries(fetch(url))
        except Exception as err:  # feed down / offline: skip, try next cycle
            print(f"{name}: {err}", file=sys.stderr)
            continue
        seen = state.setdefault(url, {"ids": []})
        pending = status.setdefault(url, {"name": name, "items": [],
                                          "today": []})
        pending["name"] = name
        pending.setdefault("today", [])
        fresh_ids = {eid for eid, *_ in current if eid not in seen["ids"]}
        fresh = [(eid, title, link) for eid, title, link, _ in current
                 if eid in fresh_ids and title not in
                 {it["title"] for it in pending["items"]}]

        if fresh and seen["ids"]:  # first sight of a feed seeds silently
            for _, title, link in fresh:
                pending["items"].insert(0, {"title": title, "link": link})
        seen["ids"] = ([eid for eid, *_ in fresh] + seen["ids"])[:MAX_IDS]
        pending["items"] = pending["items"][:MAX_PENDING]

        # today's entries, newest-first; unread = arrived in this or an
        # earlier cycle without being clicked (read flags persist via `old`)
        today_items = []
        for eid, title, link, dt in current:
            if dt is None or dt.astimezone().date() != today:
                continue
            today_items.append({"title": title, "link": link,
                                "read": eid not in fresh_ids})
        # keep read flags from the previous status where entries persist
        old = {it["title"]: it["read"] for it in pending["today"]}
        for it in today_items:
            if it["title"] in old and old[it["title"]]:
                it["read"] = True
        pending["today"] = today_items[:MAX_TODAY]

        # always keep the feed's most recent entry visible, even when it is
        # neither unread nor from today (so menus are never empty)
        pending.setdefault("latest", None)
        if current:
            eid, title, link = current[0][:3]
            unread_titles = {it["title"] for it in pending["items"]}
            old_latest = pending["latest"] or {}
            if title in unread_titles:
                read = False
            elif old_latest.get("title") == title:
                read = old_latest.get("read", True)
            else:
                read = True
            pending["latest"] = {"title": title,
                                 "link": link or old_latest.get("link", ""),
                                 "read": read}
        print(f"{name}: {len(fresh)} new, {len(today_items)} today",
              file=sys.stderr)
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))
    STATUS.write_text(json.dumps(status))


def mark_read(url, title):
    """Mark one entry read in the unread, today, and latest lists."""
    status = load(STATUS, {})
    feed = status.get(url)
    if feed:
        feed["items"] = [it for it in feed["items"] if it["title"] != title]
        for it in feed.get("today", []):
            if it["title"] == title:
                it["read"] = True
        if (feed.get("latest") or {}).get("title") == title:
            feed["latest"]["read"] = True
        STATUS.write_text(json.dumps(status))


# ------------------------------------------------------- AI summarization

class TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.title = ""
        self._in_title = False
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title = data.strip()
        elif not self._skip:
            text = " ".join(data.split())
            if len(text) > 40:  # keep real paragraphs, drop nav crumbs
                self.chunks.append(text)


def extract_article(html_bytes):
    p = TextExtractor()
    p.feed(html_bytes.decode("utf-8", "ignore"))
    text = "\n\n".join(p.chunks)[:MAX_ARTICLE]
    return p.title, text


def llm_config():
    """LLM gateway: env vars first, then ~/.config/feeds/llm.json."""
    url = os.environ.get("FEED_TRAY_LLM_URL")
    key = os.environ.get("FEED_TRAY_LLM_KEY")
    if not (url and key) and LLM_CFG.exists():
        cfg = json.loads(LLM_CFG.read_text())
        url, key = url or cfg.get("baseURL"), key or cfg.get("apiKey")
    if not (url and key):
        raise ValueError(
            "no LLM config: set FEED_TRAY_LLM_URL and FEED_TRAY_LLM_KEY "
            f"or create {LLM_CFG} with {{'baseURL': ..., 'apiKey': ...}}")
    return url.rstrip("/") + "/chat/completions", key


def summarize_md(url, feed_name, item_title):
    """Return the markdown summary text for an article (LLM or fallback)."""
    try:
        if not url:
            raise ValueError("no link in feed entry")
        page_title, text = extract_article(fetch(url))
    except Exception as err:
        page_title, text = "", ""
        note = f"> Could not fetch article: {err}\n"
    else:
        note = ""
    prompt = f"""Summarize this tech article for a DevOps/SRE/AI engineer.
Write GitHub-flavored markdown, no preamble, in this exact shape:

## TL;DR
(2-3 sentences)

## Key points
- (3-6 bullets, concrete facts/numbers)

## Why it matters
(1-2 sentences of practical relevance)

TITLE: {item_title}
URL: {url or "(unavailable)"}
CONTENT:
{text or "(article text unavailable - summarize from the title only)"}"""
    try:
        endpoint, key = llm_config()
        body = json.dumps({"model": MODEL, "max_tokens": 1500,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(endpoint, data=body, method="POST",
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"})
        reply = json.loads(urllib.request.urlopen(req, timeout=120).read())
        content = reply["choices"][0]["message"]["content"] or ""
        if not content.strip():
            raise ValueError("empty model response")
    except Exception as err:
        content = f"## Summary failed\n\n`{err}`\n"
    slug = re.sub(r"[^\w-]+", "-", item_title)[:60].strip("-") or "entry"
    return (f"# {item_title}\n\n"
            f"*{feed_name} — {date.today().isoformat()} — <{url}>*\n\n"
            f"{note}{content}\n"), slug


def do_summarize(feed_url, title):
    status = load(STATUS, {})
    feed = status.get(feed_url) or {}
    feed_name = feed.get("name", "feed")
    all_items = feed.get("items", []) + feed.get("today", [])
    link = next((it["link"] for it in all_items if it["title"] == title), "")
    mark_read(feed_url, title)
    md, slug = summarize_md(link, feed_name, title)
    out_dir = SUMMARY_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{slug}.md"
    out.write_text(md)
    # open with glow in a terminal when available, else the default app
    if shutil.which("glow") and shutil.which("x-terminal-emulator"):
        subprocess.run(["x-terminal-emulator", "-e", "glow", "-p", str(out)],
                       check=False)
    else:
        subprocess.run(["xdg-open", str(out)], check=False)
    print(out)


# ------------------------------------------------------------------ main

def do_clear(url):
    status = load(STATUS, {})
    feed = status.get(url)
    if feed:
        feed["items"] = []
        for it in feed.get("today", []):
            it["read"] = True
        if feed.get("latest"):
            feed["latest"]["read"] = True
        STATUS.write_text(json.dumps(status))


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--summarize"]:
        do_summarize(argv[1], argv[2])
        return 0
    if argv[:1] == ["--clear"]:
        do_clear(argv[1])
        return 0
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
