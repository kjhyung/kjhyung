#!/usr/bin/env python3
"""Regenerate the publications table and the current-work block in README.md.

Sources (public, no keys required beyond a GitHub token for rate limits):
  * arXiv API  -- papers by author name, newest first
  * GitHub API -- repositories you pushed to most recently

Usage:
    GH_USER=your-username ARXIV_AUTHOR="Your Name" python automation/update_readme.py
    GH_USER=... ARXIV_AUTHOR=... python automation/update_readme.py --dry-run

Only the text between the BEGIN/END marker pairs is replaced; the rest of
README.md is left byte-identical:

    <!-- BEGIN:publications --> ... <!-- END:publications -->
    <!-- BEGIN:works -->        ... <!-- END:works -->
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

PUB_BEGIN = "<!-- BEGIN:publications -->"
PUB_END = "<!-- END:publications -->"
WORKS_BEGIN = "<!-- BEGIN:works -->"
WORKS_END = "<!-- END:works -->"
MAX_ROWS = 8
UA = {"User-Agent": "readme-theme-automation/1.0"}


def get(url, headers=None):
    request = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def github_headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def escape(text):
    return " ".join(text.split()).replace("|", "\\|")


def clean_repos(repos, user):
    """Drop forks, archives, empty pushes and the profile repo itself."""
    return [
        repo
        for repo in repos
        if not repo.get("fork")
        and not repo.get("archived")
        and repo.get("pushed_at")
        and repo.get("name", "").lower() != user.lower()
    ]


def fetch_repos(user):
    """Your most recently pushed repositories."""
    if not user:
        return []
    url = f"https://api.github.com/users/{user}/repos?sort=pushed&per_page=30"
    try:
        repos = json.loads(get(url, github_headers()))
    except (urllib.error.URLError, ValueError) as err:
        print(f"github: skipped ({err})", file=sys.stderr)
        return []
    if not isinstance(repos, list):
        print("github: skipped (unexpected response)", file=sys.stderr)
        return []
    return clean_repos(repos, user)


def arxiv_rows(author, limit=MAX_ROWS):
    """Rows from the arXiv API for a given author name."""
    if not author:
        return []
    query = urllib.parse.urlencode(
        {
            "search_query": f'au:"{author}"',
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": str(limit),
        }
    )
    try:
        feed = ET.fromstring(get(f"http://export.arxiv.org/api/query?{query}"))
    except (urllib.error.URLError, ET.ParseError) as err:
        print(f"arxiv: skipped ({err})", file=sys.stderr)
        return []

    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows = []
    for entry in feed.findall("a:entry", ns):
        title = escape(entry.findtext("a:title", default="", namespaces=ns))
        link = entry.findtext("a:id", default="", namespaces=ns)
        published = entry.findtext("a:published", default="", namespaces=ns)
        if title and link:
            rows.append((published, published[:4], title, "arXiv preprint", f"[paper]({link})"))
    return rows


def github_rows(repos, limit=5):
    """Rows from the repositories you pushed to most recently."""
    rows = []
    for repo in repos:
        pushed = repo["pushed_at"]
        language = repo.get("language") or "code"
        rows.append(
            (
                pushed,
                pushed[:4],
                escape(repo.get("name", "")),
                f"code · {language}",
                f"[code]({repo.get('html_url', '')})",
            )
        )
        if len(rows) >= limit:
            break
    return rows


def render_publications(rows):
    """Render rows as the theme's four-column table."""
    lines = ["| year | work | venue | links |", "|-----:|------|-------|-------|"]
    for _, year, work, venue, links in rows:
        lines.append(f"| {year} | {work} | {venue} | {links or '—'} |")
    return "\n".join(lines)


def human_age(iso):
    """'today' / '3 days ago' / '2 months ago' from an API timestamp."""
    try:
        pushed = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return ""
    days = (datetime.now(timezone.utc) - pushed).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    if days < 365:
        return f"{days // 30} months ago"
    return f"{days // 365} years ago"


def render_works(repos):
    """Current-work panel: newest repo as 'working on', three more as 'recently active'."""
    if not repos:
        return None

    def link(repo):
        return f"[{escape(repo.get('name', ''))}]({repo.get('html_url', '')})"

    head, rest = repos[0], repos[1:4]
    first = f"> **Working on** · {link(head)}"
    description = escape(head.get("description") or "")
    if description:
        first += f" — {description}"
    meta = [x for x in (head.get("language") or "", human_age(head["pushed_at"])) if x]
    if meta:
        first += " · " + " · ".join(meta)

    lines = [first]
    if rest:
        lines += [">", "> **Recently active** · " + " · ".join(link(r) for r in rest)]
    lines += [
        ">",
        f"> <sub>auto-updated {datetime.now(timezone.utc):%d %b %Y} · "
        "automation/update_readme.py</sub>",
    ]
    return "\n".join(lines)


def replace_block(text, begin, end, body):
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    return pattern.sub(lambda _: f"{begin}\n{body}\n{end}", text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the generated blocks instead of writing README.md",
    )
    parser.add_argument(
        "--readme",
        default=str(Path(__file__).resolve().parent.parent / "README.md"),
        help="path to the README to update",
    )
    args = parser.parse_args()

    user = os.environ.get("GH_USER", "").strip()
    author = os.environ.get("ARXIV_AUTHOR", "").strip()
    if not user and not author:
        print("set GH_USER and/or ARXIV_AUTHOR", file=sys.stderr)
        return 2

    repos = fetch_repos(user)
    rows = arxiv_rows(author) + github_rows(repos)
    rows.sort(key=lambda row: row[0], reverse=True)
    publications = render_publications(rows[:MAX_ROWS]) if rows else ""
    works = render_works(repos)

    if args.dry_run:
        print(publications or "(no publication rows)")
        print()
        print(works or "(no repository rows)")
        return 0

    if not publications and not works:
        print("no rows fetched — leaving README.md untouched", file=sys.stderr)
        return 0

    path = Path(args.readme)
    text = path.read_text(encoding="utf-8")
    missing = [
        marker
        for marker in (PUB_BEGIN, PUB_END, WORKS_BEGIN, WORKS_END)
        if marker not in text
    ]
    if missing:
        print(f"{path} is missing markers: {', '.join(missing)}", file=sys.stderr)
        return 1

    updated = text
    if publications:
        updated = replace_block(updated, PUB_BEGIN, PUB_END, publications)
    if works:
        updated = replace_block(updated, WORKS_BEGIN, WORKS_END, works)

    if updated == text:
        print("README already current")
        return 0

    path.write_text(updated, encoding="utf-8", newline="\n")
    print(f"updated {path} ({len(rows[:MAX_ROWS])} publication rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
