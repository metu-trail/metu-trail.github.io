#!/usr/bin/env python3
"""
METU-TRAIL website updater.

Fetches the latest publications (Google Scholar, falling back to DBLP) and
course details (METU catalog), shows you what changed, asks you to confirm
each change (or all of them), writes the YAML files under _data/ and finally
commits and pushes to GitHub.

Usage:
    python3 scripts/update.py                  # publications + courses
    python3 scripts/update.py --only pubs      # publications only
    python3 scripts/update.py --only courses   # courses only
    python3 scripts/update.py --dry-run        # show changes, write nothing
    python3 scripts/update.py --no-push        # commit but do not push

Nothing is changed without your confirmation.
"""

import argparse
import difflib
import html
import os
import random
import re
import subprocess
import sys
import time
import unicodedata

try:
    import requests
    import yaml
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("Missing dependencies. Run:  pip3 install -r scripts/requirements.txt")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "_data")

PIS_FILE = os.path.join(DATA, "pis.yml")
PUBS_FILE = os.path.join(DATA, "publications.yml")
EXCLUDED_FILE = os.path.join(DATA, "publications_excluded.yml")
THEMES_FILE = os.path.join(DATA, "themes.yml")
COURSES_FILE = os.path.join(DATA, "courses.yml")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
DBLP_HOSTS = ["https://dblp.org", "https://dblp.uni-trier.de", "https://dblp.dagstuhl.de"]
CATALOG = "https://catalog.metu.edu.tr/course.php?prog=571&course_code={code}"

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

class C:
    """ANSI colours (disabled when not writing to a terminal)."""
    on = sys.stdout.isatty()
    B = "\033[1m" if on else ""
    G = "\033[32m" if on else ""
    R = "\033[31m" if on else ""
    Y = "\033[33m" if on else ""
    D = "\033[2m" if on else ""
    X = "\033[0m" if on else ""


def fold(s):
    """Lower-case ASCII folding: 'Gökberk Cinbiş' -> 'gokberk cinbis'."""
    s = s.replace("ı", "i").replace("İ", "I")
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def title_key(title):
    return re.sub(r"[^a-z0-9]", "", fold(title))


def is_preprint(venue):
    v = fold(venue or "")
    return (not v) or "arxiv" in v or "preprint" in v or "openreview" in v or "biorxiv" in v


def load_yaml(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return default if data is None else data


class Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper, value):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


Dumper.add_representer(str, _str_representer)


def save_yaml(path, data, header):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header.rstrip() + "\n\n")
        yaml.dump(data, f, Dumper=Dumper, allow_unicode=True, sort_keys=False,
                  width=100, default_flow_style=False)


def ask(prompt, options):
    """Ask until the user types one of the option letters."""
    opts = "/".join(options)
    while True:
        try:
            ans = input("  %s [%s]: " % (prompt, opts)).strip().lower()
        except EOFError:
            ans = "q"
        if ans in options:
            return ans
        print("  Please type one of: %s" % opts)


def http_get(url, **kw):
    kw.setdefault("timeout", 30)
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", UA)
    return requests.get(url, headers=headers, **kw)


# --------------------------------------------------------------------------
# PI name matching
# --------------------------------------------------------------------------

def pi_matcher(pis):
    """Return a function mapping an author string to a PI key (or None)."""
    rules = []
    for pi in pis:
        surname = fold(pi["surname"])
        initials = set(fold(i) for i in pi.get("initials", []))
        aliases = set(fold(a) for a in pi.get("aliases", [])) | {fold(pi["name"])}
        rules.append((pi["key"], surname, initials, aliases))

    def match(author, allowed=None):
        a = fold(author).replace(".", " ").replace("-", " ")
        a = re.sub(r"\s+", " ", a).strip()
        for key, surname, initials, aliases in rules:
            if allowed is not None and key not in allowed:
                continue
            if a in aliases:
                return key
            # "S Kalkan", "RG Cinbis", "G Cinbis", "Sinan Kalkan"
            parts = a.split(" ")
            if len(parts) >= 2 and parts[-1] == surname and parts[0][:1] in initials:
                return key
        return None

    return match


def normalise_authors(authors, pis, match, allowed):
    """Replace PI author variants by the PI's canonical display name."""
    names = {p["key"]: p["name"] for p in pis}
    out = []
    for a in authors:
        a = a.strip()
        if not a:
            continue
        k = match(a, allowed)
        out.append(names[k] if k else a)
    return out


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------

class Blocked(Exception):
    pass


def scholar_profile(scholar_id, verbose=True):
    """All publications on a Google Scholar profile (list page only)."""
    pubs, start = [], 0
    while True:
        url = ("https://scholar.google.com/citations?user=%s&hl=en&cstart=%d"
               "&pagesize=100&sortby=pubdate" % (scholar_id, start))
        r = http_get(url)
        text = r.text
        if r.status_code != 200 or "gsc_a_tr" not in text and start == 0 or \
                re.search(r"captcha|unusual traffic", text, re.I):
            raise Blocked("Google Scholar returned HTTP %d / blocked page" % r.status_code)
        soup = BeautifulSoup(text, "html.parser")
        rows = soup.select("tr.gsc_a_tr")
        for row in rows:
            a = row.select_one("a.gsc_a_at")
            if a is None:
                continue
            grays = row.select("div.gs_gray")
            authors = grays[0].get_text() if grays else ""
            venue = ""
            if len(grays) > 1:
                g = grays[1]
                for sp in g.select("span.gs_oph"):
                    sp.decompose()
                venue = g.get_text().strip()
            year = row.select_one("td.gsc_a_y").get_text().strip()
            pubs.append({
                "title": a.get_text().strip(),
                "authors": [x.strip() for x in authors.split(",") if x.strip()],
                "authors_truncated": authors.strip().endswith("..."),
                "venue": clean_venue(venue),
                "year": int(year) if year.isdigit() else None,
                "url": scholar_link(html.unescape(a["href"])),
            })
        if verbose:
            print("    fetched %d entries" % len(pubs))
        if len(rows) < 100:
            break
        start += 100
        time.sleep(random.uniform(3, 7))
    for p in pubs:
        p["authors"] = [x for x in p["authors"] if x != "..."]
    return pubs


def scholar_link(href):
    """Short, stable link to a paper's Scholar page."""
    m = re.search(r"citation_for_view=([\w-]+):([\w-]+)", href)
    if not m:
        return "https://scholar.google.com" + href
    return ("https://scholar.google.com/citations?view_op=view_citation&hl=en&user=%s"
            "&citation_for_view=%s:%s" % (m.group(1), m.group(1), m.group(2)))


def clean_venue(v):
    v = re.sub(r"\s+", " ", v).strip().rstrip(",")
    v = re.sub(r"\s*…$", "", v)
    # drop trailing page / volume numbers: "CVPR, 1234-1243" -> "CVPR"
    v = re.sub(r"(,\s*|\s+)(\d+\s*(\(\d+\))?\s*,\s*)?\d+\s*-\s*\d+$", "", v)
    v = re.sub(r",\s*\d+(\s*\(\d+\))?$", "", v)
    # drop trailing volume / issue: "Neurocomputing 650", "... 36 (5)" (keeps "ECCV 2020")
    v = re.sub(r"\s+\d{1,3}(\s*\(\d+\))?(,\s*[A-Za-z]*\d+[\w-]*)?$", "", v)
    return v.strip().rstrip(",").strip()


def scholar_full_authors(url):
    """Full author list from a Scholar citation page (used when truncated)."""
    r = http_get(url)
    if r.status_code != 200 or re.search(r"captcha|unusual traffic", r.text, re.I):
        raise Blocked("blocked")
    soup = BeautifulSoup(r.text, "html.parser")
    for field in soup.select("div.gs_scl"):
        name = field.select_one(".gsc_oci_field")
        if name and name.get_text().strip().lower() in ("authors", "inventors"):
            val = field.select_one(".gsc_oci_value").get_text()
            return [x.strip() for x in val.split(",") if x.strip()]
    return None


def dblp_get(path):
    last = None
    for host in DBLP_HOSTS:
        try:
            r = http_get(host + path, timeout=30)
            if r.status_code == 200:
                return r
            last = "HTTP %d" % r.status_code
        except requests.RequestException as e:
            last = str(e)
    raise RuntimeError("DBLP unreachable (%s)" % last)


def dblp_profile(pi):
    """All publications of a PI from DBLP (fallback source)."""
    pid = pi.get("dblp_pid")
    if not pid:
        r = dblp_get("/search/author/api?q=%s&format=json&h=10"
                     % requests.utils.quote(fold(pi["name"])))
        hits = r.json()["result"]["hits"].get("hit", [])
        if not hits:
            raise RuntimeError("no DBLP author found for %s" % pi["name"])
        pid = hits[0]["info"]["url"].split("/pid/")[-1]
        print("    %sDBLP id guessed as %s; set dblp_pid in _data/pis.yml to be sure%s"
              % (C.Y, pid, C.X))
    r = dblp_get("/pid/%s.xml" % pid)
    soup = BeautifulSoup(r.content, "html.parser")
    pubs = []
    for rec in soup.select("r > *"):
        title = rec.find("title")
        if title is None:
            continue
        venue = rec.find("journal") or rec.find("booktitle")
        ee = rec.find("ee")
        year = rec.find("year")
        pubs.append({
            "title": title.get_text().strip().rstrip("."),
            "authors": [re.sub(r"\s+\d{4}$", "", a.get_text()) for a in rec.find_all("author")],
            "authors_truncated": False,
            "venue": venue.get_text().strip() if venue else "",
            "year": int(year.get_text()) if year else None,
            "url": ee.get_text().strip() if ee else "https://dblp.org/rec/" + rec.get("key", ""),
        })
    return pubs


def fetch_all(pis):
    """{pi_key: [pub, ...]} from Scholar, DBLP when Scholar refuses."""
    result = {}
    for i, pi in enumerate(pis):
        print("  %s%s%s" % (C.B, pi["name"], C.X))
        try:
            result[pi["key"]] = scholar_profile(pi["scholar_id"])
            src = "scholar"
        except (Blocked, requests.RequestException) as e:
            print("    %sGoogle Scholar failed (%s); falling back to DBLP%s" % (C.Y, e, C.X))
            try:
                result[pi["key"]] = dblp_profile(pi)
                src = "dblp"
            except Exception as e2:  # noqa
                print("    %sDBLP failed too (%s); skipping this PI%s" % (C.R, e2, C.X))
                continue
        for p in result[pi["key"]]:
            p["source"] = src
        if i < len(pis) - 1:
            time.sleep(random.uniform(4, 8))
    return result


def merge(per_pi, pis, match):
    """Merge per-PI lists into one list of unique papers (keyed by title)."""
    merged = {}
    for key, pubs in per_pi.items():
        for p in pubs:
            if not p.get("title"):
                continue
            k = title_key(p["title"])
            p = dict(p, pis=[key])
            if k not in merged:
                merged[k] = p
                continue
            q = merged[k]
            q["pis"] = sorted(set(q["pis"]) | {key})
            # prefer the published (non-preprint) version, then the newer one
            if is_preprint(q["venue"]) and not is_preprint(p["venue"]):
                p["pis"] = q["pis"]
                if len(q["authors"]) > len(p["authors"]):
                    p["authors"] = q["authors"]
                merged[k] = p
            elif len(p["authors"]) > len(q["authors"]) and not p["authors_truncated"]:
                q["authors"] = p["authors"]
                q["authors_truncated"] = False
    out = []
    for k, p in merged.items():
        p["id"] = k[:60]
        p["authors"] = normalise_authors(p["authors"], pis, match, set(p["pis"]))
        out.append(p)
    return out


def find_existing(key, index):
    """Exact title match, else a near-identical title (typo / punctuation)."""
    if key in index:
        return index[key]
    close = difflib.get_close_matches(key, list(index.keys()), n=1, cutoff=0.95)
    return index[close[0]] if close else None


# --------------------------------------------------------------------------
# Themes
# --------------------------------------------------------------------------

def suggest_themes(title, venue, themes):
    text = " " + fold(title + " " + (venue or "")) + " "
    found = []
    for th in themes:
        for kw in th.get("keywords", []):
            if re.search(r"(?<![a-z])" + re.escape(fold(kw)), text):
                found.append(th["key"])
                break
    return found


# --------------------------------------------------------------------------
# Publications update
# --------------------------------------------------------------------------

def fmt_pub(p):
    return "%s%s%s\n      %s\n      %s (%s)" % (
        C.B, p["title"], C.X, ", ".join(p["authors"]), p.get("venue") or "-", p.get("year") or "?")


def meaningful_changes(cur, f):
    """Source changes worth proposing for a paper already on the site.

    Hand edits (title casing, shortened venue names, fixed author names) are
    respected: only real news is proposed, e.g. a preprint that got published.
    """
    diff = {}
    upgraded = is_preprint(cur.get("venue")) and f.get("venue") and not is_preprint(f["venue"])
    if title_key(f["title"]) != title_key(cur["title"]):
        diff["title"] = (cur["title"], f["title"])
    if upgraded:
        diff["venue"] = (cur.get("venue", ""), f["venue"])
    if f.get("year") and (not cur.get("year") or (upgraded and f["year"] != cur["year"])):
        diff["year"] = (cur.get("year"), f["year"])
    if not f.get("authors_truncated") and len(f["authors"]) > len(cur.get("authors") or []):
        diff["authors"] = (cur.get("authors"), f["authors"])
    if not cur.get("url") and f.get("url"):
        diff["url"] = ("", f["url"])
    return diff


def update_publications(args, pis, themes):
    match = pi_matcher(pis)
    pubs = load_yaml(PUBS_FILE, [])
    excluded = load_yaml(EXCLUDED_FILE, [])
    theme_keys = [t["key"] for t in themes]

    print("\n%s== Fetching publications ==%s" % (C.B, C.X))
    fetched = merge(fetch_all(pis), pis, match)
    if not fetched:
        print("  Nothing fetched; publications left unchanged.")
        return False
    print("  %d unique papers across all PIs." % len(fetched))

    pub_index = {title_key(p["title"]): p for p in pubs}
    excl_index = {title_key(e["title"]): e for e in excluded}

    new, changed = [], []
    for f in fetched:
        k = title_key(f["title"])
        cur = find_existing(k, pub_index)
        if cur is not None:
            if cur.get("locked"):
                continue
            diff = meaningful_changes(cur, f)
            if diff:
                changed.append((cur, f, diff))
            continue
        if find_existing(k, excl_index) is not None:
            continue
        new.append(f)

    new.sort(key=lambda p: -(p.get("year") or 0))
    dirty = False

    # ---- metadata changes of papers already on the site
    if changed:
        print("\n%s%d paper(s) on the site have updated metadata:%s" % (C.B, len(changed), C.X))
        accept_all = False
        for i, (cur, f, diff) in enumerate(changed, 1):
            print("\n  [%d/%d] %s%s%s" % (i, len(changed), C.B, cur["title"], C.X))
            for fld, (old, newv) in diff.items():
                if isinstance(old, list):
                    old, newv = ", ".join(old), ", ".join(newv)
                print("      %s: %s%s%s -> %s%s%s" % (fld, C.R, old, C.X, C.G, newv, C.X))
            if not accept_all:
                a = ask("Apply? (y)es (n)o (a)ll remaining (s)kip remaining (q)uit",
                        ["y", "n", "a", "s", "q"])
                if a == "q":
                    sys.exit("Aborted; nothing written.")
                if a == "s":
                    break
                if a == "n":
                    continue
                if a == "a":
                    accept_all = True
            for fld, (old, newv) in diff.items():
                cur[fld] = newv
            dirty = True
    else:
        print("\n  No metadata changes for papers already on the site.")

    # ---- new papers
    if new:
        print("\n%s%d new paper(s) found on the PIs' profiles.%s" % (C.B, len(new), C.X))
        print("  Only papers on trustworthy & responsible AI go on the site. For each paper")
        print("  answer (y) include, (n) exclude - remembered, never asked again,")
        print("  (l) ask me later, (t) include and type the themes yourself,")
        print("  (a) apply the suggestion to all remaining, (s) skip all remaining, (q) quit.")
        print("  Themes: %s" % ", ".join(theme_keys))
        auto = False
        for i, f in enumerate(new, 1):
            sug = suggest_themes(f["title"], f.get("venue"), themes)
            print("\n  [%d/%d] %s" % (i, len(new), fmt_pub(f)))
            print("      suggestion: %s" % (
                ("%sinclude%s, themes: %s" % (C.G, C.X, ", ".join(sug))) if sug
                else "%sexclude%s (no trustworthy-AI keywords)" % (C.D, C.X)))
            if auto:
                a = "y" if sug else "n"
            else:
                a = ask("Include?", ["y", "n", "l", "t", "a", "s", "q"])
                if a == "q":
                    sys.exit("Aborted; nothing written.")
                if a == "s":
                    break
                if a == "a":
                    auto = True
                    a = "y" if sug else "n"
            if a == "l":
                continue
            if a == "n":
                excluded.append({"title": f["title"], "year": f.get("year")})
                dirty = True
                continue
            if a == "t" or (a == "y" and not sug):
                while True:
                    raw = input("      themes (comma separated): ").strip()
                    sug = [x.strip() for x in raw.split(",") if x.strip()]
                    bad = [x for x in sug if x not in theme_keys]
                    if not bad:
                        break
                    print("      unknown theme(s): %s" % ", ".join(bad))
            if f.get("authors_truncated") and f.get("source") == "scholar":
                try:
                    time.sleep(random.uniform(2, 4))
                    full = scholar_full_authors(f["url"])
                    if full:
                        f["authors"] = normalise_authors(full, pis, match, set(f["pis"]))
                except Exception:
                    print("      %s(could not fetch the full author list; kept the short one)%s"
                          % (C.Y, C.X))
            pubs.append({
                "title": f["title"],
                "authors": f["authors"],
                "venue": f.get("venue", ""),
                "year": f.get("year"),
                "url": f.get("url", ""),
                "themes": sug,
            })
            dirty = True
    else:
        print("  No new papers.")

    if dirty and not args.dry_run:
        pubs.sort(key=lambda p: (-(p.get("year") or 0), p["title"].lower()))
        excluded.sort(key=lambda p: (-(p.get("year") or 0), p["title"].lower()))
        save_yaml(PUBS_FILE, pubs, PUBS_HEADER)
        save_yaml(EXCLUDED_FILE, excluded, EXCLUDED_HEADER)
        print("\n  %sWrote _data/publications.yml and _data/publications_excluded.yml%s" % (C.G, C.X))
    return dirty


PUBS_HEADER = """\
# Trustworthy & responsible AI publications shown on the site.
# Maintained by scripts/update.py, but safe to edit by hand on GitHub:
#   title / authors / venue / year / url : shown on the Publications page
#   themes : any of fairness, privacy, explainability, robustness, alignment
#            (shown as tags and used on the Research page)
#   locked: true  -> the update script will never modify this entry
# PI names in `authors` must be written exactly as in _data/pis.yml to be highlighted."""

EXCLUDED_HEADER = """\
# Papers you decided NOT to show on the site. The update script will not ask
# about these again. Delete an entry to be asked about it next time."""


# --------------------------------------------------------------------------
# Courses update
# --------------------------------------------------------------------------

def tr_title(s):
    """'Prof.Dr. SİNAN KALKAN' -> 'Prof. Dr. Sinan Kalkan' (Turkish-aware casing)."""
    s = s.replace("Prof.Dr.", "Prof. Dr. ").replace("Assoc.Prof.", "Assoc. Prof. ")
    s = s.replace("Asst.Prof.", "Asst. Prof. ")
    out = []
    for w in s.split():
        if w.isupper() and len(w) > 1:
            w = w[0] + w[1:].replace("İ", "i").replace("I", "ı").lower()
        out.append(w)
    return " ".join(out)


def fetch_course(code):
    r = http_get(CATALOG.format(code=code))
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")   # page is ISO-8859-9
    text = re.sub(r"[ \t]+", " ", soup.get_text("\n"))
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    def after(label):
        for i, l in enumerate(lines):
            if l.rstrip(":").lower() == label.lower() and i + 1 < len(lines):
                nxt = lines[i + 1]
                return "" if nxt.endswith(":") else nxt
        return ""

    content = ""
    if "Course Content" in lines:
        i = lines.index("Course Content")
        body = []
        for l in lines[i + 1:]:
            if l.startswith("Design:") or l.startswith("CC-IG"):
                break
            body.append(l)
        content = " ".join(body)
        if content.lower().startswith("sorry no data"):
            content = ""
    title = ""
    for tag in soup.find_all(["h2", "h3", "h4"]):
        t = tag.get_text().strip()
        if re.match(r"^[A-Z]+\s?\d{3,4}", t):
            title = t
            break
    return {
        "credits": after("METU Credit (Theoretical-Laboratory hours/week)"),
        "ects": after("ECTS Credit"),
        "level": after("Level of Study"),
        "coordinator": tr_title(after("Course Coordinator")),
        "catalog_description": content,
        "_title": title,
    }


def update_courses(args):
    courses = load_yaml(COURSES_FILE, [])
    print("\n%s== Fetching course details from the METU catalog ==%s" % (C.B, C.X))
    dirty, accept_all = False, False
    for c in courses:
        try:
            f = fetch_course(c["catalog_code"])
        except Exception as e:
            print("  %s: %sfailed (%s)%s" % (c["code"], C.R, e, C.X))
            continue
        f.pop("_title")
        diff = {k: (c.get(k, ""), v) for k, v in f.items() if v and v != c.get(k, "")}
        if not diff:
            print("  %s: no changes" % c["code"])
            continue
        print("\n  %s%s %s%s" % (C.B, c["code"], c["title"], C.X))
        for k, (old, newv) in diff.items():
            print("      %s: %s%s%s -> %s%s%s" % (k, C.R, old or "(empty)", C.X, C.G, newv, C.X))
        if "catalog_description" in diff:
            print("      %snote: the page shows `summary`; review it after accepting.%s" % (C.Y, C.X))
        if not accept_all:
            a = ask("Apply? (y)es (n)o (a)ll remaining (q)uit", ["y", "n", "a", "q"])
            if a == "q":
                sys.exit("Aborted; nothing written.")
            if a == "n":
                continue
            if a == "a":
                accept_all = True
        for k, (old, newv) in diff.items():
            c[k] = newv
        dirty = True
    if dirty and not args.dry_run:
        save_yaml(COURSES_FILE, courses, COURSES_HEADER)
        print("\n  %sWrote _data/courses.yml%s" % (C.G, C.X))
    return dirty


COURSES_HEADER = """\
# Courses shown on the Courses page. Edit freely on GitHub.
#   summary              : the text shown on the page (hand-written)
#   catalog_description  : copied from the METU catalog by scripts/update.py
#   credits/ects/level/coordinator : refreshed from the catalog by the script"""


# --------------------------------------------------------------------------
# Git
# --------------------------------------------------------------------------

def git(*cmd, check=True):
    return subprocess.run(["git"] + list(cmd), cwd=ROOT, check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          universal_newlines=True)


def commit_and_push(args, what):
    status = git("status", "--porcelain", "_data").stdout.strip()
    if not status:
        print("\nNo file changes to commit.")
        return
    print("\n%s== Changed files ==%s\n%s" % (C.B, C.X, status))
    if ask("Commit these changes?", ["y", "n"]) != "y":
        print("Left the changes uncommitted.")
        return
    git("add", "_data")
    msg = "Update %s (scripts/update.py)" % " and ".join(what)
    print(git("commit", "-m", msg).stdout.strip())
    if args.no_push:
        return
    if ask("Push to GitHub now? The site updates a minute later.", ["y", "n"]) != "y":
        print("Committed locally; run `git push` when ready.")
        return
    r = git("push", "origin", "HEAD", check=False)
    print(r.stdout.strip())
    if r.returncode != 0:
        print("%sPush failed; fix the problem above and run `git push`.%s" % (C.R, C.X))


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["pubs", "courses"])
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--no-push", action="store_true", help="commit but do not push")
    args = ap.parse_args()

    pis = load_yaml(PIS_FILE, [])
    themes = load_yaml(THEMES_FILE, [])

    if not args.dry_run and git("rev-parse", "--is-inside-work-tree", check=False).returncode == 0:
        if git("status", "--porcelain", "_data").stdout.strip():
            print("%sNote: _data/ already has uncommitted changes; they will be included "
                  "in the commit.%s" % (C.Y, C.X))

    what = []
    if args.only in (None, "pubs") and update_publications(args, pis, themes):
        what.append("publications")
    if args.only in (None, "courses") and update_courses(args):
        what.append("courses")

    if args.dry_run:
        print("\nDry run: nothing was written.")
    elif what:
        commit_and_push(args, what)
    else:
        print("\nEverything is up to date.")


if __name__ == "__main__":
    main()
