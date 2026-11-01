# nof21.github.io

Personal academic website of Dr Natanael Bolson, built with [Hugo](https://gohugo.io) and the
[Hugo Blox](https://hugoblox.com) kit, deployed to GitHub Pages on every push to `main`.

## How the site updates itself

`.github/workflows/update-publications.yml` runs every Monday (and on demand from the Actions tab):

1. **Citation metrics.** Publication count, citations and h-index (from OpenAlex) are written to
   `data/metrics.yaml`, and per-paper citation counts to `data/citations.yaml`. These are committed
   straight to `main` and the site is redeployed.
2. **New publications.** Works linked to ORCID `0000-0002-2375-2657` are checked against
   `content/publication/`. Each new work gets a publication page and BibTeX file (metadata from
   Crossref), and a news post announcing it. If a free, legal copy exists
   (open-access version or accepted manuscript, via OpenAlex), a PDF link is added, and existing
   papers get one too as soon as a free copy appears. These arrive as a pull request named
   "Publication updates" for you to review and merge.

Settings (types to include, news rules, DOIs to ignore) are in `data/publications_config.yaml`.

### One-off setup on GitHub

- Settings > Actions > General > Workflow permissions: select **Read and write permissions** and tick
  **Allow GitHub Actions to create and approve pull requests**.
- Optional: Settings > Secrets and variables > Actions > Variables:
  - `OPENALEX_MAILTO`: your email address, for faster OpenAlex access.
  - `AUTO_PUBLISH` = `true`: publish new papers directly instead of opening a pull request.

### When a pull request arrives

- Check titles, authors and abstracts.
- Add a `featured.jpg` (e.g. the graphical abstract) to the publication folder for a thumbnail.
- Set `featured: true` in a paper's `index.md` to list it under "Selected publications".
- Edit the wording of the news post in `content/news/<slug>/index.md` if you like.
- To reject an item for good, add its DOI to `ignore_dois` in `data/publications_config.yaml`.

## Editing content by hand

| What | Where |
| --- | --- |
| Name, role, bio, links, education, experience, awards | `data/authors/me.yaml` |
| Homepage statement, research themes and their key papers | `content/_index.md` |
| News posts | `content/news/<slug>/index.md` |
| Publications | `content/publication/<slug>/index.md` + `cite.bib` |
| CV PDF | `static/uploads/resume.pdf` |
| Navigation | `config/_default/menus.yaml` |

To add a news item (a talk, award or grant), copy any folder in `content/news/`, then change the
title, date, summary and text.

## Design

The custom homepage blocks are in `layouts/_partials/hbx/blocks/` (`profile-hero`,
`research-themes`, `news-list`, `selected-publications`). The publication and news list pages are in
`layouts/publication/list.html` and `layouts/news/list.html`, and the shared publication entry is in
`layouts/_partials/nb/pub-item.html`. Colours, fonts and spacing are set in `assets/css/custom.css`.
The three colours in the hero flow diagram match the three research themes.

Fonts: Source Serif 4 and IBM Plex Sans (SIL Open Font License, see `static/fonts/`), self-hosted.

## Run locally

```bash
pnpm install
hugo server
```

To test the publication sync without changing anything:

```bash
pip install pyyaml
python scripts/update_publications.py --dry-run
```
