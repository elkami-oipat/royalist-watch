#!/usr/bin/env python3
"""royalist_watch.py - watch Tori + guitar shops for a keyword."""

import argparse
import hashlib
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

# mode "tori" = follow ad links; mode "page" = scan the page text
SOURCES = [
    {"mode": "tori", "name": "Tori pedaalit",
     "url": "https://muusikoiden.net/tori/?category=55"},
    {"mode": "tori", "name": "Tori vahvistimet",
     "url": "https://muusikoiden.net/tori/?category=40"},
    {"mode": "page", "name": "Kaksi Kitaraa",
     "url": "https://www.kaksikitaraa.com/tuoteryhma/efektit-ja-pedaalit"},
    {"mode": "page", "name": "Kitarapaja Helsinki",
     "url": "https://www.kitarapaja.com/just-in"},
    {"mode": "page", "name": "Kitarapaja Oulu",
     "url": "https://www.kitarapaja.com/just-in_2"},
    {"mode": "page", "name": "Tonefest",
     "url": "https://www.tonefestguitargallery.com/collections/just-in"},
]

INTERVAL = 300
USER_AGENT = "royalist-watch/2.0 (personal saved-search alert)"
MAX_NEW_ADS_PER_CYCLE = 40

AD_RE = re.compile(r"/tori/ilmoitus/(\d+)")
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
TITLE_RE = re.compile(r"(?is)<title>(.*?)</title>")
NOISE_RE = re.compile(r"[^a-z]+")


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "fi,en;q=0.8"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else "utf-8"
    try:
        return raw.decode(enc, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def visible_text(html):
    return " ".join(TAG_RE.sub(" ", SCRIPT_RE.sub(" ", html)).split())


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
    payload = {"topic": NTFY_TOPIC, "title": title, "message": message,
               "priority": priority, "tags": list(tags)}
    if click:
        payload["click"] = click
    req = urllib.request.Request(
        NTFY_SERVER, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    state.setdefault("seen", [])
    state.setdefault("hits", [])
    state.setdefault("snips", [])
    return state


def save_state(state):
    state["seen"] = state["seen"][-4000:]
    state["hits"] = state["hits"][-500:]
    state["snips"] = state["snips"][-500:]
    parent = os.path.dirname(STATE_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.replace(tmp, STATE_FILE)


def log(*args):
    print(time.strftime("[%Y-%m-%d %H:%M:%S]"), *args, flush=True)


def check_tori(src, state, seed_only):
    seen = set(state["seen"])
    hits = set(state["hits"])
    found, new_ids = [], []
    try:
        html = fetch(src["url"])
    except (urllib.error.URLError, OSError) as exc:
        log("fetch failed:", src["url"], exc)
        return []
    for ad in ad_ids(html):
        if ad not in seen:
            new_ids.append(ad)

    if seed_only:
        state["seen"].extend(new_ids)
        log(f"{src['name']}: seeded {len(new_ids)} ads")
        return []

    if len(new_ids) > MAX_NEW_ADS_PER_CYCLE:
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
            found.append((f"{ad_title(page)}", ad_url, hit))
            log("MATCH", src["name"], ad)
        time.sleep(1)

    log(f"{src['name']}: checked {len(new_ids)} new ads")
    return found


def check_page(src, state):
    snips = set(state["snips"])
    found = []
    try:
        text = visible_text(fetch(src["url"]))
    except (urllib.error.URLError, OSError) as exc:
        log("fetch failed:", src["url"], exc)
        return []

    low = text.lower()
    n = 0
    for kw in KEYWORDS:
        for m in re.finditer(re.escape(kw.lower()), low):
            n += 1
            snippet = text[max(0, m.start() - 70):m.start() + 90].strip()
            fingerprint = NOISE_RE.sub("", snippet.lower())
            key = hashlib.sha1(
                (src["url"] + fingerprint).encode("utf-8")).hexdigest()[:16]
            if key not in snips:
                snips.add(key)
                state["snips"].append(key)
                found.append((f"{src['name']}: {snippet}", src["url"], [kw]))
                log("MATCH", src["name"], snippet[:60])

    log(f"{src['name']}: {len(text)} chars, {n} keyword hit(s)")
    return found


def selftest():
    for src in SOURCES:
        try:
            text = visible_text(fetch(src["url"]))
            verdict = "ok" if len(text) > 2000 else "SUSPICIOUS - very little text"
            log(f"{src['name']}: {len(text)} chars - {verdict}")
        except Exception as exc:
            log(f"{src['name']}: FAILED - {exc!r}")
        time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return

    if not NTFY_TOPIC:
        sys.exit("NTFY_TOPIC is not set.")

    if args.test:
        notify("Royalist watch", "Test alert - notifications are working.",
               click="https://muusikoiden.net/tori/?category=55")
        log("test notification sent")
        return

    state = load_state()
    first_run = not state["seen"]

    while True:
        try:
            found = []
            for src in SOURCES:
                if src["mode"] == "tori":
                    found += check_tori(src, state, first_run)
                else:
                    found += check_page(src, state)
                time.sleep(1)
            first_run = False
            for title, url, hit in found:
                notify(title=f"Royalist! {title[:80]}",
                       message=f"{title}\n{url}", click=url)
            save_state(state)
        except Exception as exc:
            log("error:", repr(exc))
        if args.once:
            return
        time.sleep(INTERVAL + random.randint(0, 30))


if __name__ == "__main__":
    main()
