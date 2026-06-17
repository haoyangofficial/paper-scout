---
name: paper-scout
description: Find, rank, download, and synthesize recent research papers for a user-specified research area. Use when Codex is asked to survey papers, literature, journal or conference publications, recent work from the last two years, open-access PDFs, implementation code, GitHub repositories, Papers with Code links, or to produce a Markdown literature review with paper summaries, conclusions, and future research ideas.
---

# Paper Scout

## Overview

Use this skill to build a recent, code-aware literature survey for a specific research area. Prefer papers from the last two years, prioritize published journal or conference papers over preprints, prefer open-access PDFs, and favor papers with implementation code.

## Workflow

1. Clarify the research area only when the user request is too broad to search meaningfully. Otherwise proceed with the given topic.
2. Create a task folder, usually `work/paper-scout/<slug>/`, unless the user specified a destination.
3. Run `scripts/collect_papers.py` to search, rank, download PDFs, find implementation repositories, download repo zip files, and create a starter Markdown report:

```bash
python3 /Users/hauyeung/.codex/skills/paper-scout/scripts/collect_papers.py \
  "RESEARCH AREA" \
  --output work/paper-scout/AREA-SLUG \
  --max-papers 12
```

4. Inspect the generated files:
   - `papers/*.pdf`: downloaded open-access papers
   - `repos/*.zip`: downloaded implementation repositories when found
   - `papers.json`: ranked metadata and download status
   - `literature_review.md`: starter report
5. Read abstracts first. For the strongest papers, extract full-paper details from PDFs when available; do not invent methods, metrics, or conclusions from metadata alone.
6. Rewrite `literature_review.md` into the final user-facing Markdown:
   - Start with scope, search date, year window, ranking criteria, and limitations.
   - Summarize each paper with citation, publication status, problem, method, data/benchmarks, main results, strengths, limitations, and implementation/code availability.
   - Conclude with cross-paper themes, strongest directions, gaps, and practical takeaways.
   - Add research ideas for further development that are grounded in the surveyed papers.
   - Include a local artifact inventory with downloaded PDF and repo paths.

## Ranking Rules

Prefer:

- Papers from the last two years by publication year.
- Published journal articles and peer-reviewed conference papers.
- Preprints only when they are highly relevant, influential, very recent, or have unusually strong implementation support.
- Open-access PDFs over metadata-only records.
- Papers with official implementation code, then author-associated repositories, then credible community implementations.

Use `references/source-quality.md` when ranking is ambiguous or when the result set mixes preprints, workshops, journals, and conference papers.

## Output Contract

The final Markdown should be useful even without opening the downloaded files. Use this shape:

```markdown
# Literature Review: <Research Area>

Search date: <date>
Window: <date/year range>
Selection criteria: recent, published preferred, open PDF preferred, implementation code preferred

## Executive Summary

## Paper Summaries

### 1. <Title>
Citation/status:
PDF:
Code:
Summary:
Strengths:
Limitations:
How it connects to the area:

## Synthesis

## Research Ideas

## Downloaded Artifacts

## Notes and Limitations
```

## Practical Notes

- Use web access when the task requires live paper search or downloads.
- If API or network access fails, explain the limitation and fall back to official pages the agent can access.
- If too few published papers exist in the last two years, include relevant preprints and label them clearly.
- If a repository zip fails to download, keep the repository URL and record the failure in the Markdown.
- Never imply that a repository is official unless the source establishes it or the repository belongs to an author/lab associated with the paper.
