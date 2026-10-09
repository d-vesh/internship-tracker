"""
Daily LinkedIn internship scraper.

Pulls new Software Engineering and Game Design internship postings from
LinkedIn's public (logged-out) job search, keeps a running list in jobs.json,
and rewrites README.md with a table per category: company, role, location,
date posted, and apply link.

No LinkedIn login is used, so your account is never at risk.
"""

import json
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
DATA_FILE = ROOT / "jobs.json"
README = ROOT / "README.md"

SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    "?keywords={kw}&location={loc}&f_E=1&f_TPR={window}&start={start}"
)
POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)


def get(url, tries=3):
    for attempt in range(tries):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:  # rate limited: back off
                time.sleep(10 * (attempt + 1))
                continue
            return None
        except requests.RequestException:
            time.sleep(3)
    return None


def text(el):
    return el.get_text(strip=True) if el else ""


def search(keyword):
    """Yield job cards for one keyword across a few result pages."""
    window = f"r{CONFIG['lookback_hours'] * 3600}"
    for start in range(0, CONFIG["max_results_per_keyword"], 25):
        html = get(SEARCH_URL.format(
            kw=quote(keyword), loc=quote(CONFIG["location"]),
            window=window, start=start))
        if not html:
            break
        cards = BeautifulSoup(html, "html.parser").select("div.base-card")
        if not cards:
            break
        for card in cards:
            urn = card.get("data-entity-urn", "")
            job_id = urn.rsplit(":", 1)[-1]
            if not job_id.isdigit():
                continue
            link = card.select_one("a.base-card__full-link")
            posted = card.select_one("time")
            yield {
                "id": job_id,
                "title": text(card.select_one("h3.base-search-card__title")),
                "company": text(card.select_one("h4.base-search-card__subtitle")),
                "location": text(card.select_one("span.job-search-card__location")),
                "posted": (posted.get("datetime") if posted else "") or date.today().isoformat(),
                "linkedin_url": (link["href"].split("?")[0] if link else
                                 f"https://www.linkedin.com/jobs/view/{job_id}"),
            }
        time.sleep(random.uniform(1.5, 3.5))


def external_apply_url(job_id):
    """Return the company's own application URL if LinkedIn exposes one."""
    html = get(POSTING_URL.format(id=job_id), tries=1)
    if not html:
        return None
    code = BeautifulSoup(html, "html.parser").select_one("code#applyUrl")
    if not code:
        return None
    raw = re.sub(r"^<!--|-->$", "", code.decode_contents().strip()).strip().strip('"')
    qs = parse_qs(urlparse(raw).query)
    return unquote(qs["url"][0]) if "url" in qs else (raw or None)


def matches(job, category):
    title = job["title"].lower()
    if not any(w in title for w in ("intern", "co-op", "coop")):
        return False
    if any(w in title for w in CONFIG["exclude_title_words"]):
        return False
    return any(w in title for w in category["title_must_include_one_of"])


def main():
    jobs = json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}
    found_new = 0

    for cat_name, category in CONFIG["categories"].items():
        for kw in category["keywords"]:
            for job in search(kw):
                if job["id"] in jobs or not matches(job, category):
                    continue
                job["category"] = cat_name
                job["apply_url"] = external_apply_url(job["id"]) or job["linkedin_url"]
                job["first_seen"] = date.today().isoformat()
                jobs[job["id"]] = job
                found_new += 1
                time.sleep(random.uniform(1, 2.5))

    # drop postings older than the retention window
    cutoff = (date.today() - timedelta(days=CONFIG["keep_days"])).isoformat()
    jobs = {k: v for k, v in jobs.items() if v["posted"] >= cutoff}

    DATA_FILE.write_text(json.dumps(jobs, indent=2, sort_keys=True))
    write_readme(jobs)
    print(f"{found_new} new postings, {len(jobs)} total tracked")


def md(s):
    return s.replace("|", "\\|")


def write_readme(jobs):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Internship Tracker",
        "",
        "New **Software Engineering** and **Game Design** internships from LinkedIn, "
        "refreshed every morning by a GitHub Action.",
        "",
        f"_Last updated: {now} · {len(jobs)} open postings from the last "
        f"{CONFIG['keep_days']} days · 🆕 = found in the latest run_",
        "",
    ]
    today = date.today().isoformat()
    for cat_name in CONFIG["categories"]:
        rows = sorted((j for j in jobs.values() if j["category"] == cat_name),
                      key=lambda j: (j["posted"], j["first_seen"]), reverse=True)
        out += [f"## {cat_name} ({len(rows)})", ""]
        if not rows:
            out += ["_Nothing new right now. Check back tomorrow._", ""]
            continue
        out += ["| Company | Role | Location | Posted | Apply |",
                "|---|---|---|---|---|"]
        for j in rows:
            new = " 🆕" if j["first_seen"] == today else ""
            out.append(
                f"| **{md(j['company'])}**{new} | {md(j['title'])} | {md(j['location'])} "
                f"| {j['posted']} | [Apply]({j['apply_url']}) |")
        out.append("")
    out += [
        "---",
        "Edit `config.json` to change keywords, location, or how long postings stay listed. "
        "Run it manually from the **Actions** tab → *Update internships* → *Run workflow*.",
    ]
    README.write_text("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
