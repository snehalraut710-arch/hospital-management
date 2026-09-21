"""Rendering the project documentation inside the application.

The documentation lives as Markdown files in `docs/`, which stay the canonical
source - they are readable on disk and on GitHub. This module renders them for
the web without duplicating them.

Two things need care:

* **Mermaid blocks.** Python-Markdown would turn ```mermaid into a plain code
  block. They are lifted out before conversion and replaced with an `<img>`
  pointing at a pre-rendered SVG. Diagrams are rendered ahead of time by
  `tools/render_diagrams.py` and named by a hash of their source, so the page
  needs no JavaScript and works with no network. See that script for why
  shipping Mermaid to the browser was rejected.
* **Path safety.** A URL slug must never be able to escape the docs directory.
  Slugs are matched against a whitelist built by scanning the folder, so a
  crafted path like `../../config` cannot resolve to anything.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import markdown

#: docs/ sits beside the hospital package, at the project root.
DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"

#: Rendered pages are cached per process. The files do not change at runtime in
#: production; `reload` clears the cache for development.
_cache: dict[str, "Page"] = {}

#: Pre-rendered diagrams, produced by tools/render_diagrams.py
DIAGRAM_DIR = Path(__file__).resolve().parent.parent / "static" / "diagrams"

_MERMAID = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_MD_LINK = re.compile(r'href="([^"]+\.md)(#[^"]*)?"')
_H1 = re.compile(r"^#\s+(.+)$", re.M)


@dataclass
class Page:
    slug: str
    title: str
    html: str
    toc: str
    diagrams: int


def diagram_id(source: str) -> str:
    """Content-addressed filename. Must match tools/render_diagrams.py."""
    return hashlib.sha1(source.strip().encode("utf-8")).hexdigest()[:16]


def _slug(path: Path) -> str:
    return "index" if path.stem.upper() == "README" else path.stem


def available() -> list[tuple[str, str]]:
    """(slug, title) for every documentation page, in filename order.

    This list is also the whitelist: a slug not in it is a 404, which is what
    makes path traversal impossible.
    """
    pages = []
    for path in sorted(DOCS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = _H1.search(text)
        title = match.group(1).strip() if match else path.stem
        # Strip the leading number: "1. Project Overview" -> "Project Overview"
        title = re.sub(r"^\d+\.\s*", "", title)
        pages.append((_slug(path), title))
    # index first, then numbered pages in order
    pages.sort(key=lambda p: (p[0] != "index", p[0]))
    return pages


def _path_for(slug: str) -> Path | None:
    for candidate, _ in available():
        if candidate == slug:
            name = "README.md" if slug == "index" else f"{slug}.md"
            path = DOCS_DIR / name
            # Extra check: confirm the resolved path is still inside docs/
            if path.resolve().parent == DOCS_DIR.resolve() and path.exists():
                return path
    return None


def _rewrite_links(html: str) -> str:
    """Turn `04-database.md#anchor` into `/docs/04-database#anchor`.

    The Markdown files link to each other by filename so they work on disk and
    on GitHub. In the app those must become routes.
    """
    def replace(match: re.Match) -> str:
        target, anchor = match.group(1), match.group(2) or ""
        stem = Path(target).stem
        slug = "index" if stem.upper() == "README" else stem
        return f'href="/docs/{slug}{anchor}"'

    return _MD_LINK.sub(replace, html)


def render(slug: str, reload: bool = False) -> Page | None:
    """Render one documentation page, or None if the slug is unknown."""
    if not reload and slug in _cache:
        return _cache[slug]

    path = _path_for(slug)
    if path is None:
        return None

    text = path.read_text(encoding="utf-8")

    # Lift Mermaid blocks out before Markdown touches them.
    diagrams: list[str] = []

    def stash(match: re.Match) -> str:
        diagrams.append(match.group(1))
        return f"\n\nMERMAIDBLOCK{len(diagrams) - 1}ENDBLOCK\n\n"

    text = _MERMAID.sub(stash, text)

    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "attr_list", "sane_lists"],
        extension_configs={"toc": {"permalink": False, "toc_depth": "2-3"}},
    )
    html = converter.convert(text)

    # Put the diagrams back as pre-rendered SVG images.
    for index, source in enumerate(diagrams):
        name = diagram_id(source)
        svg = DIAGRAM_DIR / f"{name}.svg"
        if svg.exists():
            replacement = (
                f'<figure class="diagram">'
                f'<img src="/static/diagrams/{name}.svg" alt="Diagram" loading="lazy">'
                f"</figure>"
            )
        else:
            # The source is still shown rather than silently dropped, so a
            # missing render is visible instead of leaving a hole in the page.
            escaped = (source.replace("&", "&amp;")
                             .replace("<", "&lt;").replace(">", "&gt;"))
            replacement = (
                '<figure class="diagram missing">'
                '<p class="small muted">Diagram not rendered - run '
                '<code>python tools/render_diagrams.py</code></p>'
                f"<pre><code>{escaped}</code></pre></figure>"
            )
        html = html.replace(f"<p>MERMAIDBLOCK{index}ENDBLOCK</p>", replacement)

    html = _rewrite_links(html)

    match = _H1.search(path.read_text(encoding="utf-8"))
    title = match.group(1).strip() if match else slug
    title = re.sub(r"^\d+\.\s*", "", title)

    page = Page(
        slug=slug, title=title, html=html,
        toc=getattr(converter, "toc", ""), diagrams=len(diagrams),
    )
    _cache[slug] = page
    return page


def clear_cache() -> None:
    _cache.clear()
