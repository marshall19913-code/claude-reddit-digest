#!/usr/bin/env python3
"""
Daily Reddit Digest
-------------------
Fetches the top N posts of the day from a list of subreddits and renders
them into one static HTML page.

Two modes, chosen automatically:
  - API mode: if REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set, uses
    Reddit's official API (includes scores and comment counts).
  - RSS mode: otherwise, uses Reddit's public RSS feeds. No credentials
    needed, but feeds carry no scores or comment counts, and Reddit may
    block requests from cloud servers (see README).

Meant to be run once a day by the accompanying GitHub Actions workflow,
but you can also run it locally:

    python fetch_reddit.py                      # RSS mode
    export REDDIT_CLIENT_ID=xxxx                # optional: API mode
    export REDDIT_CLIENT_SECRET=xxxx
    python fetch_reddit.py

See README.md for details.
"""

import os
import sys
import re
import html
import base64
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# CONFIG — this is the only section you should need to touch
# ---------------------------------------------------------------------------
SUBREDDITS = [
    "LocalLLaMA",
    "AI_Agents",
    "vibecoding",
    "mlops",
    "MachineLearning",
    "LanguageTechnology",
    "PromptEngineering",
    "AIToolsandTips",
    "artificial",
    "singularity",
    "ArtificialIntelligence",
    "LLMDevs",
    "LangChain",
    "AutoGPT",
    "n8n",
    "ClaudeAI",
    "aipromptprogramming",
    "FluxAI",
    "aivideo",
    "AiVideos",
    "ControlProblem",
    "accelerate",
    "agi",
    "robotics",
    "robotlearning",
    "RoboticsEngineering",
    "deeplearning",
    "neuralnetworks",
    "SideProject",
    "StableDiffusion",
    "ChatGPT",
    "OpenAI",
    "worldnews",
    "TrendForecast",
    "BuyItForLife",
    "SkincareAddiction",
    "Beauty",
    "Hardware",
    "SelfHosted",
    "Homelab",
    "SupplyChain",
    "SysAdmin",
    "RenewableEnergy",
    "EnergyStorage",
    "3Dprinting",
    "Biohackers",
    "GenZ",
    "GenAlpha",
    "streetwear",
    "ThrowingFits",
    "BeautyGuruChatter",
    "Tiktokfashion",
    "gamedev",
    "popheads",
    "StanTwitter",
    "technology",
]

POSTS_PER_SUBREDDIT = 5      # max NEW posts shown per subreddit
FETCH_LIMIT = 35             # how many of the week's top posts to look through
TIME_WINDOW = "week"         # one of: day, week, month, year, all
SEEN_PATH = "seen_posts.json"  # remembers what you've already been shown
SEEN_KEEP_DAYS = 14          # forget posts after this long (must exceed the window)
OUTPUT_PATH = "docs/index.html"
PAGE_TITLE = "The Daily Sift"

# Reddit requires a descriptive, unique user agent — put your username in it.
USER_AGENT = "daily-reddit-digest/1.0 (by u/Unique-Highlight-241)"
# ---------------------------------------------------------------------------

REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_API_BASE = "https://oauth.reddit.com"


def get_access_token(client_id: str, client_secret: str) -> str:
    """App-only (client_credentials) OAuth. Read-only, no user login needed."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = b"grant_type=client_credentials"
    req = urlrequest.Request(REDDIT_TOKEN_URL, data=data, method="POST")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urlrequest.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode())
    if "access_token" not in payload:
        raise RuntimeError(f"Reddit auth failed: {payload}")
    return payload["access_token"]


def fetch_top_posts(subreddit: str, token: str, limit: int, window: str) -> list:
    url = f"{REDDIT_API_BASE}/r/{subreddit}/top?limit={limit}&t={window}&raw_json=1"
    req = urlrequest.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", USER_AGENT)
    payload = None
    for attempt in range(3):
        try:
            with urlrequest.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode())
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 2:
                print(f"  ! rate limited on r/{subreddit}, waiting before retry...", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
                continue
            print(f"  ! failed to fetch r/{subreddit}: {e}", file=sys.stderr)
            return None
        except URLError as e:
            print(f"  ! failed to fetch r/{subreddit}: {e}", file=sys.stderr)
            return None

    posts = []
    for child in payload.get("data", {}).get("children", []):
        p = child.get("data", {})
        permalink = f"https://reddit.com{p.get('permalink', '')}"
        posts.append({
            "id": p.get("name") or permalink,
            "title": p.get("title", "(untitled)"),
            "discussion_url": permalink,
            "external_url": p.get("url_overridden_by_dest") or permalink,
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "author": p.get("author", "unknown"),
            "flair": p.get("link_flair_text"),
        })
    return posts


ATOM = "{http://www.w3.org/2005/Atom}"


def fetch_top_posts_rss(subreddit: str, limit: int, window: str) -> list:
    """Credential-free mode: reads Reddit's public Atom feed for the subreddit.
    Feeds carry no score / comment count / flair, so those are left as None."""
    url = f"https://www.reddit.com/r/{subreddit}/top/.rss?t={window}&limit={limit}"
    req = urlrequest.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    body = None
    for attempt in range(3):
        try:
            with urlrequest.urlopen(req, timeout=20) as resp:
                body = resp.read()
            break
        except HTTPError as e:
            if e.code == 429 and attempt < 2:
                print(f"  ! rate limited on r/{subreddit}, waiting before retry...", file=sys.stderr)
                time.sleep(20 * (attempt + 1))
                continue
            hint = " (Reddit is blocking this machine; see README)" if e.code in (403, 429) else ""
            print(f"  ! failed to fetch r/{subreddit}: {e}{hint}", file=sys.stderr)
            return None
        except URLError as e:
            print(f"  ! failed to fetch r/{subreddit}: {e}", file=sys.stderr)
            return None

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f"  ! r/{subreddit} returned something that isn't a feed: {e}", file=sys.stderr)
        return None

    posts = []
    for entry in root.findall(f"{ATOM}entry")[:limit]:
        link_el = entry.find(f"{ATOM}link")
        discussion = link_el.get("href", "") if link_el is not None else ""
        content = entry.findtext(f"{ATOM}content") or ""
        m = re.search(r'<a href="([^"]+)">\[link\]</a>', content)
        author = (entry.findtext(f"{ATOM}author/{ATOM}name") or "unknown").strip()
        posts.append({
            "id": (entry.findtext(f"{ATOM}id") or discussion).strip(),
            "title": (entry.findtext(f"{ATOM}title") or "(untitled)").strip(),
            "discussion_url": discussion,
            "external_url": html.unescape(m.group(1)) if m else discussion,
            "score": None,
            "num_comments": None,
            "author": author[3:] if author.startswith("/u/") else author,
            "flair": None,
        })
    return posts

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def render_section(subreddit: str, posts: list) -> str:
    if posts is None:
        items = '<li class="empty">Couldn\'t load this subreddit on the last run.</li>'
    elif not posts:
        items = '<li class="empty">No new posts since the last run.</li>'
    else:
        rows = []
        for i, p in enumerate(posts, start=1):
            flair_html = f'<span class="flair">{esc(p["flair"])}</span>' if p["flair"] else ""
            meta_parts = []
            if p["score"] is not None:
                meta_parts.append(f"{p['score']:,} points")
            if p["num_comments"] is not None:
                meta_parts.append(f"{p['num_comments']:,} comments")
            meta_parts.append(f"u/{esc(p['author'])}")
            meta_parts.append(f'<a href="{esc(p["discussion_url"])}" target="_blank" rel="noopener">view thread</a>')
            meta_html = " &middot; ".join(meta_parts)
            rows.append(f"""
        <li class="post">
          <span class="rank">{i:02d}</span>
          <div class="post-body">
            <a class="title" href="{esc(p['external_url'])}" target="_blank" rel="noopener">{esc(p['title'])}</a>{flair_html}
            <div class="meta">{meta_html}</div>
          </div>
        </li>""")
        items = "".join(rows)

    return f"""
    <section class="sub-section">
      <h2><a href="https://reddit.com/r/{esc(subreddit)}" target="_blank" rel="noopener">r/{esc(subreddit)}</a></h2>
      <ol class="post-list">{items}
      </ol>
    </section>"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__PAGE_TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,600;1,9..144,500&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --paper: #EEEAE1;
    --ink: #23262B;
    --muted: #746F5E;
    --accent: #2F6F62;
    --line: #D9D3C4;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--paper);
    color: var(--ink);
    font-family: 'Inter', -apple-system, sans-serif;
    max-width: 680px;
    margin: 0 auto;
    padding: 3rem 1.5rem 5rem;
    line-height: 1.55;
  }
  a { color: inherit; }
  a:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  header.masthead {
    margin-bottom: 3rem;
    border-bottom: 2px solid var(--ink);
    padding-bottom: 1.25rem;
  }
  header.masthead h1 {
    font-family: 'Fraunces', serif;
    font-size: 2.4rem;
    font-weight: 600;
    margin: 0 0 0.4rem;
    letter-spacing: -0.01em;
  }
  header.masthead p {
    margin: 0;
    color: var(--muted);
    font-size: 0.95rem;
  }

  section.sub-section { margin-bottom: 2.75rem; }
  section.sub-section h2 {
    font-family: 'Fraunces', serif;
    font-size: 1.4rem;
    font-weight: 600;
    margin: 0 0 1rem;
  }
  section.sub-section h2 a {
    text-decoration: none;
    border-bottom: 1px solid var(--accent);
  }
  section.sub-section h2 a:hover { color: var(--accent); }

  ol.post-list { list-style: none; margin: 0; padding: 0; }
  li.post {
    display: flex;
    gap: 1rem;
    padding: 0.9rem 0;
    border-top: 1px solid var(--line);
  }
  li.post:first-child { border-top: none; }
  li.empty { color: var(--muted); font-style: italic; padding: 0.5rem 0; }

  span.rank {
    font-family: 'Fraunces', serif;
    font-style: italic;
    font-weight: 500;
    color: var(--accent);
    font-size: 1.05rem;
    min-width: 1.6rem;
    flex-shrink: 0;
  }
  .post-body { flex: 1; min-width: 0; }
  a.title {
    text-decoration: none;
    font-weight: 500;
    font-size: 1rem;
  }
  a.title:hover { color: var(--accent); }
  .flair {
    margin-left: 0.5rem;
    font-size: 0.72rem;
    color: var(--accent);
    border: 1px solid var(--accent);
    border-radius: 3px;
    padding: 0.05rem 0.4rem;
    white-space: nowrap;
  }
  .meta {
    margin-top: 0.3rem;
    font-size: 0.82rem;
    color: var(--muted);
  }
  .meta a { color: var(--muted); text-decoration: underline; }
  .meta a:hover { color: var(--accent); }

  footer {
    margin-top: 3rem;
    padding-top: 1.25rem;
    border-top: 1px solid var(--line);
    color: var(--muted);
    font-size: 0.8rem;
  }

  @media (max-width: 480px) {
    body { padding: 2rem 1.1rem 3rem; }
    header.masthead h1 { font-size: 2rem; }
    li.post { gap: 0.7rem; }
  }
</style>
</head>
<body>
  <header class="masthead">
    <h1>__PAGE_TITLE__</h1>
    <p>New posts gaining traction, across __SUBREDDIT_COUNT__ subreddits &middot; generated __GENERATED_AT__</p>
  </header>

  <main>__SECTIONS__
  </main>

  <footer>
    Built with a small Python script and a daily GitHub Actions run &mdash; no manual updates.
  </footer>
</body>
</html>
"""


def render_html(data: dict, generated_at: str) -> str:
    sections_html = "".join(render_section(sub, posts) for sub, posts in data.items())
    return (
        HTML_TEMPLATE
        .replace("__PAGE_TITLE__", esc(PAGE_TITLE))
        .replace("__SUBREDDIT_COUNT__", str(len(data)))
        .replace("__GENERATED_AT__", esc(generated_at))
        .replace("__SECTIONS__", sections_html)
    )


def load_seen() -> dict:
    try:
        with open(SEEN_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    use_api = bool(client_id and client_secret)

    token = None
    if use_api:
        print("Using Reddit API (credentials found). Authenticating...")
        token = get_access_token(client_id, client_secret)
    else:
        print("No API credentials found; using public RSS feeds.")

    data = {}
    for sub in SUBREDDITS:
        print(f"Fetching r/{sub} ...")
        if use_api:
            data[sub] = fetch_top_posts(sub, token, FETCH_LIMIT, TIME_WINDOW)
        else:
            data[sub] = fetch_top_posts_rss(sub, FETCH_LIMIT, TIME_WINDOW)
        result = data[sub]
        print(f"  -> {'FAILED' if result is None else str(len(result)) + ' posts'}")
        time.sleep(2)  # stay well under Reddit's rate limit

    if not any(data.values()):
        print("No posts were fetched from any subreddit; leaving the existing page untouched.", file=sys.stderr)
        sys.exit(1)

    # Keep only posts not shown on an earlier day. Posts first shown *today*
    # still count as new, so re-running the workflow the same day is harmless.
    today = datetime.now(timezone.utc).date()
    today_s = today.isoformat()
    seen = load_seen()
    for sub in list(data):
        if data[sub] is None:
            continue  # failed fetch: don't mark anything as seen
        fetched = data[sub]  # this run's ranked top-35, most likely first
        fresh = [p for p in fetched if p["id"] not in seen or seen[p["id"]] == today_s]
        shown = fresh[:POSTS_PER_SUBREDDIT]
        # Mark only what's actually shown. Anything ranked lower stays
        # eligible, so a post that climbs into the top 5 later still gets
        # its turn -- that's what lets late bloomers surface once they
        # prove themselves, at the cost of them not looking "brand new."
        for p in shown:
            seen.setdefault(p["id"], today_s)
        data[sub] = shown
        print(f"r/{sub}: {len(shown)} new")

    generated_at = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    output_html = render_html(data, generated_at)

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output_html)
    print(f"Wrote {OUTPUT_PATH}")

    cutoff = (today - timedelta(days=SEEN_KEEP_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=0, sort_keys=True)
    print(f"Saved {len(seen)} remembered posts to {SEEN_PATH}")


if __name__ == "__main__":
    main()
