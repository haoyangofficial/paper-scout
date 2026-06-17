#!/usr/bin/env python3
"""Collect recent papers, open PDFs, and likely implementation repositories."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path


USER_AGENT = "CodexResearchPaperScout/1.0 (literature survey helper)"
S2_FIELDS = ",".join(
    [
        "title",
        "year",
        "venue",
        "publicationVenue",
        "publicationTypes",
        "authors",
        "abstract",
        "citationCount",
        "isOpenAccess",
        "openAccessPdf",
        "url",
        "externalIds",
    ]
)


def slugify(text: str, max_len: int = 90) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "item"


def request_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30):
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def download_file(url: str, dest: Path, timeout: int = 60) -> tuple[bool, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            content = response.read()
        if not content:
            return False, "empty response"
        dest.write_bytes(content)
        return True, f"downloaded {len(content)} bytes"
    except Exception as exc:  # noqa: BLE001 - report network/download failures to metadata
        return False, str(exc)


def search_semantic_scholar(query: str, start_year: int, end_year: int, limit: int):
    encoded = urllib.parse.urlencode(
        {
            "query": query,
            "limit": min(max(limit * 3, 20), 100),
            "fields": S2_FIELDS,
            "year": f"{start_year}-{end_year}",
        }
    )
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?{encoded}"
    data = request_json(url)
    return data.get("data", [])


def is_preprint(paper: dict) -> bool:
    venue = (paper.get("venue") or "").lower()
    pub_types = [str(t).lower() for t in paper.get("publicationTypes") or []]
    preprint_markers = ("arxiv", "biorxiv", "medrxiv", "ssrn", "preprint")
    if any(marker in venue for marker in preprint_markers):
        return True
    return any("preprint" in t for t in pub_types)


def is_published(paper: dict) -> bool:
    if is_preprint(paper):
        return False
    venue = paper.get("venue") or ""
    pub_venue = paper.get("publicationVenue") or {}
    pub_types = [str(t).lower() for t in paper.get("publicationTypes") or []]
    if any(t in {"journalarticle", "conference"} for t in pub_types):
        return True
    return bool(venue.strip() or pub_venue.get("name"))


def paper_score(paper: dict) -> float:
    score = 0.0
    if is_published(paper):
        score += 40
    elif is_preprint(paper):
        score += 10
    if paper.get("isOpenAccess") or paper.get("openAccessPdf"):
        score += 20
    if paper.get("abstract"):
        score += 5
    score += min(float(paper.get("citationCount") or 0), 100.0) / 5.0
    score += float(paper.get("year") or 0) / 1000.0
    return score


def arxiv_pdf_url(paper: dict) -> str | None:
    external = paper.get("externalIds") or {}
    arxiv_id = external.get("ArXiv") or external.get("arXiv")
    if not arxiv_id:
        return None
    return f"https://arxiv.org/pdf/{arxiv_id}.pdf"


def pdf_url_for(paper: dict) -> str | None:
    open_pdf = paper.get("openAccessPdf") or {}
    if open_pdf.get("url"):
        return open_pdf["url"]
    return arxiv_pdf_url(paper)


def github_api_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def search_github_repo(title: str, token: str | None) -> dict | None:
    query = urllib.parse.urlencode({"q": f'"{title}" in:name,description,readme', "per_page": 3})
    url = f"https://api.github.com/search/repositories?{query}"
    try:
        data = request_json(url, headers=github_api_headers(token))
    except Exception:
        return None
    items = data.get("items") or []
    if not items:
        return None
    item = items[0]
    return {
        "source": "github_search",
        "name": item.get("full_name"),
        "url": item.get("html_url"),
        "default_branch": item.get("default_branch") or "main",
        "stars": item.get("stargazers_count"),
        "description": item.get("description"),
        "official": False,
    }


def search_paperswithcode(title: str) -> dict | None:
    query = urllib.parse.urlencode({"q": title})
    url = f"https://paperswithcode.com/api/v1/papers/?{query}"
    try:
        data = request_json(url)
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    paper = results[0]
    repos = paper.get("repositories") or []
    if not repos:
        return {
            "source": "paperswithcode",
            "paper_url": paper.get("url_abs") or paper.get("url"),
            "official": False,
        }
    repo = repos[0]
    return {
        "source": "paperswithcode",
        "name": repo.get("owner") + "/" + repo.get("name") if repo.get("owner") and repo.get("name") else repo.get("name"),
        "url": repo.get("url"),
        "stars": repo.get("stars"),
        "framework": repo.get("framework"),
        "official": bool(repo.get("is_official")),
    }


def github_zip_url(repo_url: str, branch: str = "main") -> str | None:
    match = re.match(r"https://github\.com/([^/]+)/([^/#?]+)", repo_url.rstrip("/"))
    if not match:
        return None
    owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"


def maybe_download_repo(repo: dict | None, dest_dir: Path, paper_slug: str) -> tuple[str | None, str | None]:
    if not repo or not repo.get("url"):
        return None, None
    branch = repo.get("default_branch") or "main"
    zip_url = github_zip_url(repo["url"], branch)
    if not zip_url and branch != "master":
        zip_url = github_zip_url(repo["url"], "master")
    if not zip_url:
        return None, "repository is not a GitHub URL or has no zip endpoint"
    dest = dest_dir / f"{paper_slug}-repo.zip"
    ok, message = download_file(zip_url, dest)
    if ok and zipfile.is_zipfile(dest):
        return str(dest), message
    if dest.exists():
        dest.unlink()
    return None, message


def authors_text(paper: dict, limit: int = 8) -> str:
    authors = [a.get("name", "") for a in paper.get("authors") or [] if a.get("name")]
    if len(authors) > limit:
        return ", ".join(authors[:limit]) + " et al."
    return ", ".join(authors)


def render_markdown(area: str, papers: list[dict], start_year: int, end_year: int) -> str:
    today = dt.date.today().isoformat()
    lines = [
        f"# Literature Review: {area}",
        "",
        f"Search date: {today}",
        f"Window: {start_year}-{end_year}",
        "Selection criteria: recent papers, published venues preferred, open PDFs preferred, implementation code preferred.",
        "",
        "## Executive Summary",
        "",
        "Draft this section after reading the paper summaries and PDFs.",
        "",
        "## Paper Summaries",
        "",
    ]
    for index, item in enumerate(papers, 1):
        paper = item["paper"]
        repo = item.get("repo") or {}
        lines.extend(
            [
                f"### {index}. {paper.get('title', 'Untitled')}",
                "",
                f"- Year: {paper.get('year') or 'unknown'}",
                f"- Authors: {authors_text(paper) or 'unknown'}",
                f"- Venue/status: {paper.get('venue') or 'unknown'} ({'published' if is_published(paper) else 'preprint/unclear'})",
                f"- Semantic Scholar: {paper.get('url') or 'not available'}",
                f"- PDF: {item.get('pdf_path') or item.get('pdf_url') or 'not downloaded'}",
                f"- Code: {repo.get('url') or 'not found'}",
                f"- Repo zip: {item.get('repo_zip') or 'not downloaded'}",
                f"- Code provenance: {repo.get('source') or 'none'}; official={repo.get('official') if repo else 'unknown'}",
                f"- Citations: {paper.get('citationCount') or 0}",
                "",
                "**Abstract:**",
                "",
                (paper.get("abstract") or "No abstract returned by source API.").strip(),
                "",
                "**Summary:** Replace this with a concise reading-based summary.",
                "",
                "**Strengths:**",
                "",
                "**Limitations:**",
                "",
                "**How it connects to the area:**",
                "",
            ]
        )
    lines.extend(
        [
            "## Synthesis",
            "",
            "Compare methods, assumptions, datasets, evaluation designs, and implementation maturity.",
            "",
            "## Research Ideas",
            "",
            "Ground each idea in one or more surveyed papers, and include why it is feasible or interesting now.",
            "",
            "## Downloaded Artifacts",
            "",
        ]
    )
    for item in papers:
        title = item["paper"].get("title", "Untitled")
        lines.append(f"- {title}")
        lines.append(f"  - PDF: {item.get('pdf_path') or 'not downloaded'}")
        lines.append(f"  - Repo zip: {item.get('repo_zip') or 'not downloaded'}")
    lines.extend(
        [
            "",
            "## Notes and Limitations",
            "",
            "Record API limits, missing PDFs, uncertain code provenance, and any reason preprints were included.",
            "",
        ]
    )
    return "\n".join(lines)


def collect(args: argparse.Namespace) -> int:
    end_year = args.end_year or dt.date.today().year
    start_year = args.start_year or end_year - 1
    output = Path(args.output)
    papers_dir = output / "papers"
    repos_dir = output / "repos"
    output.mkdir(parents=True, exist_ok=True)
    papers_dir.mkdir(exist_ok=True)
    repos_dir.mkdir(exist_ok=True)

    candidates = search_semantic_scholar(args.area, start_year, end_year, args.max_papers)
    ranked = sorted(candidates, key=paper_score, reverse=True)
    selected: list[dict] = []

    for paper in ranked:
        if len(selected) >= args.max_papers:
            break
        title = paper.get("title") or "Untitled"
        paper_slug = slugify(title)
        item = {"paper": paper, "score": paper_score(paper)}

        pdf_url = pdf_url_for(paper)
        item["pdf_url"] = pdf_url
        if pdf_url:
            pdf_path = papers_dir / f"{paper_slug}.pdf"
            ok, message = download_file(pdf_url, pdf_path)
            item["pdf_download_status"] = message
            if ok:
                item["pdf_path"] = str(pdf_path)

        repo = search_paperswithcode(title)
        if not repo or not repo.get("url"):
            time.sleep(args.pause)
            repo = search_github_repo(title, args.github_token or os.environ.get("GITHUB_TOKEN"))
        item["repo"] = repo
        repo_zip, repo_message = maybe_download_repo(repo, repos_dir, paper_slug)
        item["repo_zip"] = repo_zip
        item["repo_download_status"] = repo_message

        selected.append(item)
        time.sleep(args.pause)

    (output / "papers.json").write_text(json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "literature_review.md").write_text(
        render_markdown(args.area, selected, start_year, end_year),
        encoding="utf-8",
    )
    print(f"Wrote {output / 'papers.json'}")
    print(f"Wrote {output / 'literature_review.md'}")
    print(f"Downloaded PDFs: {sum(1 for p in selected if p.get('pdf_path'))}/{len(selected)}")
    print(f"Downloaded repos: {sum(1 for p in selected if p.get('repo_zip'))}/{len(selected)}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("area", help="Research area or query")
    parser.add_argument("--output", required=True, help="Output folder")
    parser.add_argument("--max-papers", type=int, default=12, help="Maximum ranked papers to keep")
    parser.add_argument("--start-year", type=int, help="Override start publication year")
    parser.add_argument("--end-year", type=int, help="Override end publication year")
    parser.add_argument("--github-token", help="Optional GitHub token for higher search limits")
    parser.add_argument("--pause", type=float, default=1.0, help="Pause between API calls")
    return parser.parse_args(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(collect(parse_args(sys.argv[1:])))
    except urllib.error.HTTPError as exc:
        print(f"HTTP error: {exc.code} {exc.reason}", file=sys.stderr)
        raise SystemExit(2)
    except urllib.error.URLError as exc:
        print(f"Network error: {exc.reason}", file=sys.stderr)
        raise SystemExit(2)
