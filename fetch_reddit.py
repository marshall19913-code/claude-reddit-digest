#!/usr/bin/env python3
"""
Daily Reddit Digest
-------------------
Fetches the top N posts of the day from a list of subreddits (using
Reddit's own API, not scraping) and renders them into one static HTML page.

Meant to be run once a day by the accompanying GitHub Actions workflow,
but you can also run it locally:

    export REDDIT_CLIENT_ID=xxxx
    export REDDIT_CLIENT_SECRET=xxxx
    python fetch_reddit.py

See README.md for how to get REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET.
"""

import os
import sys
import html
import base64
import json
from datetime import datetime, timezone
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# CONFIG — this is the only section you should need to touch
# ---------------------------------------------------------------------------
SUBREDDITS = [
    "technology",
    "dataisbeautiful",
    "askscience",
    "explainlikeimfive",
    "todayilearned",
]

POSTS_PER_SUBREDDIT = 10
TIME_WINDOW = "day"          # one of: day, week, month, year, all
OUTPUT_PATH = "docs/index.html"
PAGE_TITLE = "The Daily Sift"

# Reddit requires a descriptive, unique user agent — put your username in it.
USER_AGENT = "daily-reddit-digest/1.0 (by u/your_username)"
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
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except (HTTPError, URLError) as e:
        print(f"  ! failed to fetch r/{subreddit}: {e}", file=sys.stderr)
        return []

    posts = []
    for child in payload.get("data", {}).get("children", []):
        p = child.get("data", {})
        permalink = f"https://reddit.com{p.get('permalink', '')}"
        posts.append({
            "title": p.get("title", "(untitled)"),
            "discussion_url": permalink,
            "external_url": p.get("url_overridden_by_dest") or permalink,
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "author": p.get("author", "unknown"),
            "flair": p.get("link_flair_text"),
        })
    return posts


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def render_section(subreddit: str, posts: list) -> str:
    if not posts:
        items = '<li class="empty">No posts fetched for this subreddit today.</li>'
    else:
        rows = []
        for i, p in enumerate(posts, start=1):
            flair_html = f'<span class="flair">{esc(p["flair"])}</span>' if p["flair"] else ""
            rows.append(f"""
        <li class="post">
          <span class="rank">{i:02d}</span>
          <div class="post-body">
            <a class="title" href="{esc(p['external_url'])}" target="_blank" rel="noopener">{esc(p['title'])}</a>{flair_html}
            <div class="meta">{p['score']:,} points &middot; {p['num_comments']:,} comments &middot; u/{esc(p['author'])} &middot; <a href="{esc(p['discussion_url'])}" target="_blank" rel="noopener">view thread</a></div>
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
    <p>Top posts of the day, across __SUBREDDIT_COUNT__ subreddits &middot; generated __GENERATED_AT__</p>
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


def main():
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Missing REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET environment variables.\n"
            "See README.md for how to create these.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Authenticating with Reddit...")
    token = get_access_token(client_id, client_secret)

    data = {}
    for sub in SUBREDDITS:
        print(f"Fetching r/{sub} ...")
        data[sub] = fetch_top_posts(sub, token, POSTS_PER_SUBREDDIT, TIME_WINDOW)

    generated_at = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")
    output_html = render_html(data, generated_at)

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output_html)
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
