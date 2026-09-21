"""Pre-render the documentation's Mermaid diagrams to static SVG.

Why pre-render instead of shipping Mermaid to the browser:

* Mermaid 11's `dist/mermaid.min.js` is an esbuild IIFE that never assigns
  `window.mermaid`, so the usual `<script>` + `mermaid.initialize()` pattern
  simply does not work with it.
* The build that does work is ESM, and it lazy-loads a separate chunk per
  diagram type -- 16 MB across 103 files to vendor.
* Loading it from a CDN would break the project's offline guarantee.

`htmlLabels` has to be off. With it on, Mermaid puts label text
inside `<foreignObject>` as HTML -- and browsers do not render foreignObject
when an SVG is loaded through an `<img>` tag, so every diagram would appear as
shapes with invisible text. Turning it off emits native `<text>` elements.

Rendering ahead of time gives static SVG: a few hundred KB in total, no
JavaScript at runtime, and diagrams that work with no network at all.

Run this only when a diagram changes:

    python tools/render_diagrams.py

Requires mermaid-cli (`npm install -g @mermaid-js/mermaid-cli`), which is a
development tool -- it is not needed to run the application.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
OUT = ROOT / "hospital" / "static" / "diagrams"

MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)

CONFIG = """{
  "theme": "base",
  "htmlLabels": false,
  "themeVariables": {
    "primaryColor": "#d3f5ec",
    "primaryBorderColor": "#12a186",
    "primaryTextColor": "#0f1e26",
    "lineColor": "#5b7480",
    "secondaryColor": "#eaf1fa",
    "tertiaryColor": "#f5f8f9",
    "fontFamily": "Segoe UI, Roboto, -apple-system, Helvetica, Arial, sans-serif",
    "fontSize": "14px"
  },
  "flowchart": { "curve": "basis", "htmlLabels": false },
  "sequence":  { "actorFontFamily": "inherit", "noteFontFamily": "inherit" }
}"""


def diagram_id(source: str) -> str:
    """Content-addressed name, so an unchanged diagram is never re-rendered
    and a changed one always gets a fresh file."""
    return hashlib.sha1(source.strip().encode("utf-8")).hexdigest()[:16]


def find_mmdc() -> str:
    found = shutil.which("mmdc")
    if found:
        return found
    local = ROOT / "node_modules" / ".bin" / "mmdc"
    if local.exists():
        return str(local)
    sys.exit(
        "mermaid-cli not found. Install it with:\n"
        "    npm install -g @mermaid-js/mermaid-cli"
    )


def main() -> int:
    mmdc = find_mmdc()
    OUT.mkdir(parents=True, exist_ok=True)

    blocks: dict[str, str] = {}
    for path in sorted(DOCS.glob("*.md")):
        for source in MERMAID_BLOCK.findall(path.read_text(encoding="utf-8")):
            blocks[diagram_id(source)] = source

    print(f"{len(blocks)} unique diagrams across {len(list(DOCS.glob('*.md')))} documents")

    rendered = skipped = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        config = Path(tmp) / "config.json"
        config.write_text(CONFIG, encoding="utf-8")

        for index, (key, source) in enumerate(blocks.items(), start=1):
            target = OUT / f"{key}.svg"
            if target.exists():
                skipped += 1
                continue

            src = Path(tmp) / f"{key}.mmd"
            src.write_text(source, encoding="utf-8")

            result = subprocess.run(
                [mmdc, "-i", str(src), "-o", str(target),
                 "-c", str(config), "-b", "transparent", "--quiet"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and target.exists():
                rendered += 1
                print(f"  [{index}/{len(blocks)}] {key}.svg")
            else:
                failed += 1
                print(f"  [{index}/{len(blocks)}] FAILED {key}")
                print("      " + (result.stderr or result.stdout).strip()[:200])

    # Remove SVGs whose diagram no longer exists in any document.
    orphans = [p for p in OUT.glob("*.svg") if p.stem not in blocks]
    for path in orphans:
        path.unlink()

    print(f"\nrendered {rendered}, unchanged {skipped}, failed {failed}, "
          f"removed {len(orphans)} orphan(s)")
    total = sum(p.stat().st_size for p in OUT.glob("*.svg"))
    print(f"total SVG payload: {total / 1024:.0f} KB")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
