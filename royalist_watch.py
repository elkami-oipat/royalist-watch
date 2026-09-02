#!/usr/bin/env python3
"""royalist_watch.py - watch muusikoiden.net Tori for a keyword."""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")
STATE_FILE = os.environ.get("STATE_FILE", "state/seen.json")

KEYWORDS = [k.strip() for k in
            os.environ.get("KEYWORDS", "royalist").split(",") if k.strip()]

LIST_URLS = [
    "https://muusikoiden.net/tori/?category=55",
    "https://muusikoiden.net/tori/?category=40",
]

INTERVAL = 300
USER_AGENT = "royalist-watch/1.1 (personal saved-search alert)"
MAX_NEW_ADS_PER_CYCLE = 40

AD_RE = re.compile(r"/tori/ilmoitus/(\d+)")
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
TITLE_RE = re.compile(r"(?is)<title>(.*?)</title>")


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fi,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return raw.decode("iso-8859-15", errors="replace")


def visible_text(html):
    return TAG_RE.sub(" ", SCRIPT_RE.sub(" ", html))


def ad_ids(html):
    return list(dict.fromkeys(AD_RE.findall(SCRIPT_RE.sub(" ", html))))


def matched_keywords(text):
    low = text.lower()
    return [k for k in KEYWORDS if k.lower() in low]


def ad_title(html):
    m = TITLE_RE.search(html)
    if not m:
        return "Uusi ilmoitus"
    t = " ".join(TAG_RE.sub(" ", m.group(1)).split())
    return t.split("\u00b7")[0].strip() or "Uusi ilmoitus"


def notify(title, message, click=None, priority=5, tags=("moneybag",)):
    payload = {
        "topic": NTFY_TOPIC,
        "title": title,
        "message": message,
        "priority": priority,
        "tags": list(tags),
    }
    if click:
        payload["click"] = click
    req = urllib.request.Request(
        NTFY_SERVER,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_seen():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        return {"seen": [], "hits": []}
    state.setdefault("seen", [])
    state.setdefault("hits", [])
    return state


def save_seen(state):
    state["seen"] = state["seen"][-4000:]
    state["hits"] = state["hits"][-500:]
    parent = os.path.dirname(STATE_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE_FILE)


def log(*args):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *args, flush=True)


def poll(state, seed_only=False):
    seen = set(state["seen"])
    hits = set(state["hits"])
    found = []
    new_ids = []

    for url in LIST_URLS:
        try:
            html = fetch(url)
        except (urllib.error.URLError, OSError) as exc:
            log("fetch failed:", url, exc)
            continue
        for ad in ad_ids(html):
            if ad not in seen and ad not in new_ids:
                new_ids.append(ad)
        time.sleep(1)

    if seed_only:
        state["seen"].extend(new_ids)
        log(f"seeded {len(new_ids)} existing ads, no alerts sent")
        return []

    if len(new_ids) > MAX_NEW_ADS_PER_CYCLE:
        log(f"{len(new_ids)} new ads at once - checking newest "
            f"{MAX_NEW_ADS_PER_CYCLE} only")
        new_ids = new_ids[:MAX_NEW_ADS_PER_CYCLE]

    for ad in new_ids:
        ad_url = f"https://muusikoiden.net/tori/ilmoitus/{ad}"
        try:
            page = fetch(ad_url)
        except (urllib.error.URLError, OSError) as exc:
            log("fetch failed:", ad_url, exc)
            continue
        state["seen"].append(ad)
        hit = matched_keywords(visible_text(page))
        if hit and ad not in hits:
            state["hits"].append(ad)
            found.append((ad, ad_url, ad_title(page), hit))
            log("MATCH", ad, ad_title(page))
        time.sleep(1)

    log(f"checked {len(new_ids)} new ads, {len(found)} match(es)")
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    if not NTFY_TOPIC:
        sys.exit("NTFY_TOPIC is not set.")

    if args.test:
        notify("Royalist watch", "Test alert - notifications are working.",
               click="https://muusikoiden.net/tori/?category=55")
        log("test notification sent")
        return

    state = load_seen()
    first_run = not state["seen"]

    while True:
        try:
            found = poll(state, seed_only=first_run)
            first_run = False
            for ad, url, title, hit in found:
                notify(
                    title=f"Royalist! {title}",
                    message=f"Match: {', '.join(hit)}\n{url}",
                    click=url,
                )
            save_seen(state)
        except Exception as exc:
            log("error:", repr(exc))
        if args.once:
            return
        time.sleep(INTERVAL + random.randint(0, 30))


if __name__ == "__main__":
    main()
