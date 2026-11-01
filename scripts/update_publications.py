#!/usr/bin/env python3
"""Weekly publication sync for the website.

What it does
------------
1. Finds every work linked to your ORCID iD (OpenAlex + the ORCID public API).
2. Compares DOIs and titles against the pages in content/publication/.
3. For each new work: writes content/publication/<slug>/index.md and cite.bib
   (metadata from Crossref), appends the BibTeX to publications.bib and, if the
   paper is recent, writes a short news post in content/news/<slug>/.
4. Refreshes citation metrics (data/metrics.yaml) and per-paper citation counts
   (data/citations.yaml) from OpenAlex.

Settings live in data/publications_config.yaml.

Usage
-----
    python scripts/update_publications.py            # update files in place
    python scripts/update_publications.py --dry-run  # report only, write nothing

Only PyYAML is required (pip install pyyaml). Set OPENALEX_MAILTO to an email
address to use OpenAlex's faster "polite pool" (optional).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PUB_DIR = ROOT / "content" / "publication"
NEWS_DIR = ROOT / "content" / "news"
DATA_DIR = ROOT / "data"
BIB_FILE = ROOT / "publications.bib"
CONFIG_FILE = DATA_DIR / "publications_config.yaml"

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
ORCID_API = "https://pub.orcid.org/v3.0"

STOPWORDS = set(
    """a an the of and or for in on to with via by from at as into towards toward
    under over its their our is are be using based new case study analysis
    assessment evaluation approach framework review""".split()
)

# Crossref type -> Hugo Blox (CSL) publication type
CSL_TYPES = {
    "journal-article": "article-journal",
    "proceedings-article": "paper-conference",
    "book-chapter": "chapter",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "report": "report",
    "posted-content": "manuscript",
    "dissertation": "thesis",
    "dataset": "dataset",
}

# Optional offline mode for testing: a JSON file mapping URL -> response.
FIXTURES = None
if os.environ.get("PUBSYNC_FIXTURES"):
    FIXTURES = json.loads(Path(os.environ["PUBSYNC_FIXTURES"]).read_text())


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def _user_agent() -> str:
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    ua = "academic-website-publication-sync/1.0 (+https://github.com)"
    return f"{ua} mailto:{mailto}" if mailto else ua


def http_get(url: str, accept: str = "application/json", retries: int = 3) -> str | None:
    """GET a URL and return the body as text, or None on 404."""
    if FIXTURES is not None:
        return FIXTURES.get(url)
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": _user_agent()})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            if err.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            raise
    return None


def http_json(url: str) -> dict | None:
    body = http_get(url)
    return json.loads(body) if body else None


def openalex_url(path: str, **params) -> str:
    mailto = os.environ.get("OPENALEX_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    query = urllib.parse.urlencode(params, safe=":|,*")
    return f"{OPENALEX}{path}?{query}" if query else f"{OPENALEX}{path}"


# --------------------------------------------------------------------------- #
# Normalisation helpers
# --------------------------------------------------------------------------- #
def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", doi)
    return doi.removeprefix("doi:").strip()


def norm_title(title: str | None) -> str:
    if not title:
        return ""
    title = unicodedata.normalize("NFKD", html.unescape(title))
    return re.sub(r"[^a-z0-9]", "", title.lower())


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean_text(text: str | None) -> str:
    """Strip JATS/HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"^\s*(abstract|summary)\s*[:.]?\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def abstract_from_inverted_index(index: dict | None) -> str:
    if not index:
        return ""
    positions = [(pos, word) for word, poss in index.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions))


def open_access_links(oa: dict | None) -> tuple[str, str]:
    """(pdf_url, landing_url) of the best free full-text copy OpenAlex knows about."""
    if not oa or not (oa.get("open_access") or {}).get("is_oa"):
        return "", ""
    loc = oa.get("best_oa_location") or {}
    return (loc.get("pdf_url") or "").strip(), (loc.get("landing_page_url") or "").strip()


def date_from_parts(obj: dict | None) -> dt.date | None:
    try:
        parts = obj["date-parts"][0]
    except (TypeError, KeyError, IndexError):
        return None
    if not parts or parts[0] is None:
        return None
    year, month, day = (list(parts) + [1, 1])[:3]
    try:
        return dt.date(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Reading the site
# --------------------------------------------------------------------------- #
def read_front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\s*\n(.*?)\n---\s*(\n|$)", text, flags=re.S)
    if not match:
        return {}
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}


def existing_publications() -> tuple[set[str], set[str], set[str]]:
    """Return (dois, normalised titles, slugs) already on the site."""
    dois, titles, slugs = set(), set(), set()
    for index in PUB_DIR.glob("*/index.md"):
        slugs.add(index.parent.name)
        fm = read_front_matter(index)
        titles.add(norm_title(fm.get("title")))
        doi = fm.get("doi") or ((fm.get("hugoblox") or {}).get("ids") or {}).get("doi")
        if doi:
            dois.add(norm_doi(str(doi)))
        bib = index.parent / "cite.bib"
        if bib.exists():
            for found in re.findall(r"doi\s*=\s*[{\"]([^}\"]+)", bib.read_text(encoding="utf-8"), flags=re.I):
                dois.add(norm_doi(found))
    dois.discard("")
    titles.discard("")
    return dois, titles, slugs


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def fetch_openalex_works(orcid: str) -> list[dict]:
    works, cursor = [], "*"
    while cursor:
        url = openalex_url("/works", filter=f"author.orcid:{orcid}", per_page=200, cursor=cursor)
        data = http_json(url) or {}
        works.extend(data.get("results", []))
        cursor = (data.get("meta") or {}).get("next_cursor")
        if not data.get("results"):
            break
    return works


def fetch_openalex_by_dois(dois: list[str]) -> list[dict]:
    works = []
    for i in range(0, len(dois), 50):
        chunk = "|".join(f"https://doi.org/{d}" for d in dois[i : i + 50])
        data = http_json(openalex_url("/works", filter=f"doi:{chunk}", per_page=50)) or {}
        works.extend(data.get("results", []))
    return works


def fetch_openalex_author(orcid: str) -> dict | None:
    return http_json(openalex_url(f"/authors/orcid:{orcid}"))


def fetch_orcid_dois(orcid: str) -> set[str]:
    data = http_json(f"{ORCID_API}/{orcid}/works") or {}
    dois = set()
    for group in data.get("group", []):
        for ext in (group.get("external-ids") or {}).get("external-id", []):
            if ext.get("external-id-type") == "doi":
                dois.add(norm_doi(ext.get("external-id-value")))
    dois.discard("")
    return dois


def fetch_crossref(doi: str) -> dict | None:
    data = http_json(f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}")
    return (data or {}).get("message")


def fetch_crossref_bibtex(doi: str) -> str | None:
    url = f"{CROSSREF}/works/{urllib.parse.quote(doi, safe='/')}/transform/application/x-bibtex"
    return http_get(url, accept="application/x-bibtex")


# --------------------------------------------------------------------------- #
# Building pages
# --------------------------------------------------------------------------- #
def authors_from(cr: dict | None, oa: dict | None) -> list[dict]:
    """List of {'given', 'family', 'name'} dicts, Crossref first, OpenAlex fallback."""
    people = []
    for a in (cr or {}).get("author", []) or []:
        given, family = (a.get("given") or "").strip(), (a.get("family") or "").strip()
        name = " ".join(p for p in (given, family) if p) or a.get("name", "")
        if name:
            people.append({"given": given, "family": family or name, "name": name})
    if not people:
        for a in (oa or {}).get("authorships", []) or []:
            name = ((a.get("author") or {}).get("display_name") or "").strip()
            if name:
                people.append({"given": " ".join(name.split()[:-1]), "family": name.split()[-1], "name": name})
    return people


def make_slug(people: list[dict], year: int, title: str, taken: set[str]) -> str:
    family = slugify(people[0]["family"].split()[-1]) if people else "paper"
    words = [w for w in slugify(title).split("-") if w not in STOPWORDS and len(w) > 3]
    keyword = "-".join(words[:2]) or "paper"
    base = f"{family}-{year}-{keyword}"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    taken.add(slug)
    return slug


def bibtex_key(people: list[dict], year: int, title: str) -> str:
    family = re.sub(r"[^A-Za-z]", "", unicodedata.normalize("NFKD", people[0]["family"]).encode("ascii", "ignore").decode()) if people else "Anon"
    words = [w for w in slugify(title).split("-") if w not in STOPWORDS and len(w) > 3]
    return f"{family}{year}{(words[0] if words else 'paper').capitalize()}"


def build_bibtex(key: str, rec: dict) -> str:
    entry_type = {"article-journal": "article", "chapter": "incollection", "paper-conference": "inproceedings", "book": "book", "report": "techreport"}.get(rec["csl_type"], "misc")
    container_field = {"article": "journal", "incollection": "booktitle", "inproceedings": "booktitle"}.get(entry_type)
    fields = [
        ("title", rec["title"]),
        ("author", " and ".join(f"{p['family']}, {p['given']}".strip(", ") for p in rec["authors"])),
        (container_field, rec["venue"]) if container_field else None,
        ("year", str(rec["date"].year)),
        ("volume", rec.get("volume")),
        ("number", rec.get("issue")),
        ("pages", rec.get("pages")),
        ("publisher", rec.get("publisher")),
        ("doi", rec["doi"]),
    ]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in (f for f in fields if f) if v)
    return f"@{entry_type}{{{key},\n{body}\n}}\n"


def rekey_bibtex(bib: str, key: str) -> str:
    """Give Crossref's BibTeX a readable key and one field per line."""
    bib = re.sub(r"^(\s*@\w+\s*\{)\s*[^,]+,\s*", lambda m: f"{m.group(1)}{key},\n  ", bib.strip(), count=1)
    bib = re.sub(r"\}\s*,\s*(\w+)\s*=\s*", lambda m: f"}},\n  {m.group(1)} = ", bib)
    bib = re.sub(r"^(\s+)(\w+)=\{", lambda m: f"{m.group(1)}{m.group(2)} = {{", bib, flags=re.M)
    bib = re.sub(r"\}\s*\}\s*$", "}\n}", bib)
    return bib + "\n"


def yaml_block(data: dict) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100000, default_flow_style=False)


def first_sentences(text: str, n: int = 2, limit: int = 420) -> str:
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    out = " ".join(sentences[:n]).strip()
    return out if len(out) <= limit else out[: limit].rsplit(" ", 1)[0] + "…"


def join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def record_for(doi: str, oa: dict | None, cfg: dict) -> dict | None:
    """Assemble one publication record from Crossref (preferred) and OpenAlex."""
    cr = fetch_crossref(doi)
    title = clean_text(((cr or {}).get("title") or [None])[0] or (oa or {}).get("title") or "")
    if not title:
        return None
    people = authors_from(cr, oa)
    issued = date_from_parts((cr or {}).get("issued")) or date_from_parts((cr or {}).get("published"))
    online = date_from_parts((cr or {}).get("published-online")) or date_from_parts((cr or {}).get("created"))
    if not issued and oa and oa.get("publication_date"):
        issued = dt.date.fromisoformat(oa["publication_date"])
    issued = issued or online or dt.date.today()
    online = online or issued
    # Hugo hides future-dated pages, so use the online date when the issue date is still ahead.
    if issued > dt.date.today():
        issued = min(online, dt.date.today())
    venue = ((cr or {}).get("container-title") or [None])[0]
    if not venue and oa:
        venue = (((oa.get("primary_location") or {}).get("source")) or {}).get("display_name")
    abstract = clean_text((cr or {}).get("abstract")) or clean_text(abstract_from_inverted_index((oa or {}).get("abstract_inverted_index")))
    cr_type = (cr or {}).get("type") or {"article": "journal-article", "review": "journal-article", "book-chapter": "book-chapter", "book": "book", "preprint": "posted-content"}.get((oa or {}).get("type"), "journal-article")
    return {
        "doi": doi,
        "title": title,
        "authors": people,
        "date": issued,
        "online_date": online,
        "venue": clean_text(venue or ""),
        "abstract": abstract,
        "csl_type": CSL_TYPES.get(cr_type, "article-journal"),
        "volume": (cr or {}).get("volume"),
        "issue": (cr or {}).get("issue"),
        "pages": (cr or {}).get("page") or (cr or {}).get("article-number"),
        "publisher": (cr or {}).get("publisher"),
        "url": (((cr or {}).get("resource") or {}).get("primary") or {}).get("URL"),
        "cited_by": (oa or {}).get("cited_by_count", 0),
        "oa_pdf": open_access_links(oa)[0],
        "oa_landing": open_access_links(oa)[1],
    }


def write_publication(rec: dict, slug: str, dry_run: bool) -> str:
    folder = PUB_DIR / slug
    fm = {
        "title": rec["title"],
        "authors": [p["name"] for p in rec["authors"]],
        "date": rec["date"].isoformat(),
        "publishDate": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "publication_types": [rec["csl_type"]],
        "publication": f"*{rec['venue']}*" if rec["venue"] else "",
        "hugoblox": {"ids": {"doi": rec["doi"]}},
        "abstract": rec["abstract"],
        "featured": False,
        "image": {"caption": "", "focal_point": "center"},
    }
    if rec.get("url") and "doi.org" not in rec["url"]:
        fm["links"] = [{"name": "Publisher", "url": rec["url"]}]
    if rec.get("oa_pdf"):
        fm["url_pdf"] = rec["oa_pdf"]
    elif rec.get("oa_landing") and norm_doi(rec["oa_landing"]) != rec["doi"]:
        fm.setdefault("links", []).append({"name": "Free full text", "url": rec["oa_landing"]})
    page = f"---\n{yaml_block(fm)}---\n"
    key = bibtex_key(rec["authors"], rec["date"].year, rec["title"])
    bib = fetch_crossref_bibtex(rec["doi"])
    bib = rekey_bibtex(bib, key) if bib and bib.strip().startswith("@") else build_bibtex(key, rec)
    if not dry_run:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.md").write_text(page, encoding="utf-8")
        (folder / "cite.bib").write_text(bib, encoding="utf-8")
        with BIB_FILE.open("a", encoding="utf-8") as fh:
            fh.write("\n" + bib)
    return bib


def write_news(rec: dict, slug: str, cfg: dict, dry_run: bool) -> None:
    owners = [o.lower() for o in cfg.get("owner_names", [])]
    coauthors = [p["name"] for p in rec["authors"] if not any(o in p["name"].lower() for o in owners)]
    venue = rec["venue"] or "a peer-reviewed venue"
    lead = f"Our paper “{rec['title']}” has been published in *{venue}*"
    lead += f", with {join_names(coauthors)}." if coauthors else "."
    fm = {
        "title": f"New paper in {venue}",
        "date": rec["online_date"].isoformat(),
        "summary": rec["title"],
        "publication_slug": slug,
        "tags": ["publication"],
    }
    body = lead + "\n\n"
    if rec["abstract"]:
        body += first_sentences(rec["abstract"], 2) + "\n\n"
    free = rec.get("oa_pdf") or (rec.get("oa_landing") if norm_doi(rec.get("oa_landing")) != rec["doi"] else "")
    links = [f"[Read the paper](https://doi.org/{rec['doi']})"]
    if free:
        links.append(f"[Free full text]({free})")
    links.append(f"[Details and citation](/publication/{slug}/)")
    body += " · ".join(links) + "\n"
    if not dry_run:
        folder = NEWS_DIR / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.md").write_text(f"---\n{yaml_block(fm)}---\n\n{body}", encoding="utf-8")


def write_yaml_if_changed(path: Path, data: dict, header: str, ignore_keys: tuple = ()) -> bool:
    old = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if old is not None:
        strip = lambda d: {k: v for k, v in d.items() if k not in ignore_keys} if isinstance(d, dict) else d
        if strip(old) == strip(data):
            return False
    path.write_text(header + yaml_block(data), encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing files")
    parser.add_argument("--report", type=Path, help="write a Markdown summary (used as the pull-request body)")
    args = parser.parse_args()

    cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    orcid = cfg["orcid"]
    include_types = set(cfg.get("include_types", ["article", "review", "book-chapter", "book"]))
    min_year = int(cfg.get("min_year", 1900))
    news_window = int(cfg.get("news_window_days", 120))
    ignore = {norm_doi(d) for d in cfg.get("ignore_dois", []) or []}
    today = dt.date.today()

    site_dois, site_titles, slugs = existing_publications()
    print(f"Site has {len(site_dois)} publications with DOIs.")

    oa_works = fetch_openalex_works(orcid)
    by_doi = {norm_doi(w.get("doi")): w for w in oa_works if w.get("doi")}
    print(f"OpenAlex: {len(oa_works)} works for ORCID {orcid}.")

    try:
        orcid_dois = fetch_orcid_dois(orcid)
        print(f"ORCID: {len(orcid_dois)} DOIs.")
    except Exception as exc:  # ORCID being down should not stop the run
        print(f"::warning::ORCID lookup failed: {exc}")
        orcid_dois = set()

    # Pick up DOIs listed on ORCID (or on the site) that OpenAlex did not attach to the ORCID.
    missing = sorted((orcid_dois | site_dois) - set(by_doi))
    if missing:
        for w in fetch_openalex_by_dois(missing):
            by_doi[norm_doi(w.get("doi"))] = w

    # ---- New publications -------------------------------------------------
    candidates = []
    for doi in sorted(set(by_doi) | orcid_dois):
        if not doi or doi in site_dois or doi in ignore:
            continue
        oa = by_doi.get(doi)
        if oa:
            if oa.get("is_retracted") or oa.get("is_paratext"):
                continue
            if oa.get("type") not in include_types:
                continue
            if (oa.get("publication_year") or 0) < min_year:
                continue
            if norm_title(oa.get("title")) in site_titles:
                continue
        candidates.append((doi, oa))

    added, seen_titles = [], set(site_titles)
    for doi, oa in candidates:
        rec = record_for(doi, oa, cfg)
        if not rec or rec["date"].year < min_year:
            continue
        key = norm_title(rec["title"])
        if key in seen_titles:  # another version of a paper already on the site
            continue
        if not oa and rec["csl_type"] in ("dataset", "manuscript"):
            continue
        seen_titles.add(key)
        slug = make_slug(rec["authors"], rec["date"].year, rec["title"], slugs)
        write_publication(rec, slug, args.dry_run)
        recent = (today - rec["online_date"]).days <= news_window
        if recent:
            write_news(rec, slug, cfg, args.dry_run)
        added.append((rec, slug, recent))
        print(f"+ {slug}: {rec['title']} ({rec['venue']}, {rec['date']}){' [news]' if recent else ''}")

    # ---- Free full text for papers already on the site ------------------------
    oa_added = []
    for index in sorted(PUB_DIR.glob("*/index.md")):
        fm = read_front_matter(index)
        if fm.get("url_pdf"):
            continue
        doi = norm_doi(str(fm.get("doi") or ((fm.get("hugoblox") or {}).get("ids") or {}).get("doi") or ""))
        pdf, _ = open_access_links(by_doi.get(doi))
        if not doi or not pdf or doi in {r["doi"] for r, _, _ in added}:
            continue
        if not args.dry_run:
            text = index.read_text(encoding="utf-8")
            text = re.sub(r"\n---\s*(\n|$)", f"\nurl_pdf: '{pdf}'\n---\\1", text, count=1)
            index.write_text(text, encoding="utf-8")
        oa_added.append((fm.get("title", index.parent.name), pdf))
        print(f"~ free PDF found for {index.parent.name}")

    # ---- Metrics ------------------------------------------------------------
    metrics_changed = False
    author = fetch_openalex_author(orcid)
    site_and_new = site_dois | {r["doi"] for r, _, _ in added}
    citations = {d: int(by_doi[d].get("cited_by_count") or 0) for d in sorted(site_and_new) if d in by_doi}
    if author and not args.dry_run:
        stats = author.get("summary_stats") or {}
        metrics = {
            "publications": len(list(PUB_DIR.glob("*/index.md"))),
            "citations": int(author.get("cited_by_count") or 0),
            "h_index": int(stats.get("h_index") or 0),
            "i10_index": int(stats.get("i10_index") or 0),
            "source": "OpenAlex",
            "source_url": author.get("id", "https://openalex.org"),
            "updated": today.isoformat(),
        }
        header = "# Written by scripts/update_publications.py. Do not edit by hand.\n"
        metrics_changed |= write_yaml_if_changed(DATA_DIR / "metrics.yaml", metrics, header, ignore_keys=("updated",))
        metrics_changed |= write_yaml_if_changed(DATA_DIR / "citations.yaml", {"updated": today.isoformat(), "counts": citations}, header, ignore_keys=("updated",))
        print(f"Metrics: {metrics['citations']} citations, h-index {metrics['h_index']}{' (changed)' if metrics_changed else ' (unchanged)'}.")

    # ---- Report -------------------------------------------------------------
    lines = []
    if added or oa_added:
        if added:
            lines.append(f"The weekly sync found **{len(added)} publication(s)** linked to ORCID {orcid} that are not on the site yet.\n")
        for rec, slug, recent in added:
            free = f" Free full text: [link]({rec['oa_pdf'] or rec['oa_landing']})." if (rec['oa_pdf'] or rec['oa_landing']) else ""
            lines.append(f"- **{rec['title']}** ({rec['venue']}, {rec['date'].year}). DOI: [{rec['doi']}](https://doi.org/{rec['doi']}).{free} Page: `content/publication/{slug}/`{'. News post added.' if recent else '.'}")
        if oa_added:
            lines.append(f"\nFree full-text PDFs found for **{len(oa_added)} existing paper(s)** (added as a PDF link):\n")
            for title, pdf in oa_added:
                lines.append(f"- {title}: [PDF]({pdf})")
        lines += [
            "",
            "Before merging:",
            "- Check titles, authors and abstracts.",
            "- Optionally add a `featured.jpg` (graphical abstract) to each new publication folder, and set `featured: true` to show it under Selected publications.",
            "- Edit or delete the news posts in `content/news/` if you want different wording.",
            "- To reject an item permanently, add its DOI to `ignore_dois` in `data/publications_config.yaml` and close this PR.",
        ]
    else:
        lines.append("No new publications found.")
    report = "\n".join(lines) + "\n"
    print("\n" + report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"new_count={len(added)}\nchanges={len(added) + len(oa_added)}\nmetrics_changed={str(metrics_changed).lower()}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
