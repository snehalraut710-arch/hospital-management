"""The in-app documentation pages."""
import re
from pathlib import Path

import pytest

from hospital.services import docs as docs_service


@pytest.fixture(autouse=True)
def clear_docs_cache():
    docs_service.clear_cache()
    yield
    docs_service.clear_cache()


class TestPages:
    def test_every_document_renders(self, client):
        for slug, _ in docs_service.available():
            response = client.get(f"/docs/{slug}")
            assert response.status_code == 200, slug

    def test_the_index_is_the_readme(self, client):
        assert client.get("/docs/").status_code == 200
        assert b"MediQueue" in client.get("/docs/").data

    def test_documentation_is_public(self, client):
        """No account needed - an examiner should be able to read it."""
        response = client.get("/docs/05-ai-engine")
        assert response.status_code == 200
        assert b"Sign in" not in response.data[:400]

    def test_unknown_page_is_a_404(self, client):
        assert client.get("/docs/no-such-page").status_code == 404

    def test_pages_are_listed_in_order(self):
        slugs = [s for s, _ in docs_service.available()]
        assert slugs[0] == "index"
        assert slugs[1:] == sorted(slugs[1:])

    def test_titles_drop_the_leading_number(self):
        titles = dict(docs_service.available())
        assert titles["01-overview"] == "Project Overview"


class TestPathTraversal:
    """A URL slug must never escape the docs directory."""

    @pytest.mark.parametrize("attempt", [
        "../config",
        "../../hospital/models",
        "....//config",
        "01-overview.md",
        "index.md",
        "%2e%2e%2fconfig",
    ])
    def test_traversal_attempts_are_refused(self, client, attempt):
        assert client.get(f"/docs/{attempt}").status_code == 404

    def test_render_refuses_an_unknown_slug(self):
        assert docs_service.render("../config") is None
        assert docs_service.render("wsgi") is None


class TestMarkdownRendering:
    def test_markdown_becomes_html(self, client):
        html = client.get("/docs/03-architecture").data.decode()
        assert "<h1" in html and "<h2" in html
        assert "<table>" in html
        assert "<pre><code" in html

    def test_no_raw_markdown_leaks_through(self, client):
        html = client.get("/docs/04-database").data.decode()
        assert "```" not in html
        assert "MERMAIDBLOCK" not in html

    def test_cross_document_links_become_routes(self, client):
        """Docs link to each other as `04-database.md` so they work on disk;
        in the app those must be rewritten to /docs/ URLs."""
        html = client.get("/docs/03-architecture").data.decode()
        assert not re.search(r'href="[^"]+\.md"', html)
        assert "/docs/04-database" in html

    def test_anchors_survive_link_rewriting(self, client):
        html = client.get("/docs/03-architecture").data.decode()
        assert "/docs/04-database#2-preventing-double-booking" in html

    def test_a_table_of_contents_is_generated(self, client):
        html = client.get("/docs/05-ai-engine").data.decode()
        assert 'class="docs-toc"' in html

    def test_navigation_marks_the_current_page(self, client):
        html = client.get("/docs/07-queue-management").data.decode()
        assert 'class="active"' in html

    def test_pages_are_cached_after_first_render(self):
        first = docs_service.render("01-overview")
        second = docs_service.render("01-overview")
        assert first is second


class TestDiagrams:
    def test_diagrams_render_as_svg_images(self, client):
        html = client.get("/docs/06-appointment-flow").data.decode()
        assert html.count("/static/diagrams/") == 6
        assert "<pre class=\"mermaid\">" not in html

    def test_no_diagram_is_missing(self, client):
        """A missing render shows a warning block rather than a hole; if this
        fires, run `python tools/render_diagrams.py`."""
        for slug, _ in docs_service.available():
            html = client.get(f"/docs/{slug}").data.decode()
            assert "diagram missing" not in html, f"{slug} has an unrendered diagram"

    def test_every_mermaid_block_has_a_rendered_svg(self):
        """Guards against editing a diagram and forgetting to re-render it."""
        missing = []
        for path in sorted(docs_service.DOCS_DIR.glob("*.md")):
            for source in docs_service._MERMAID.findall(path.read_text(encoding="utf-8")):
                name = docs_service.diagram_id(source)
                if not (docs_service.DIAGRAM_DIR / f"{name}.svg").exists():
                    missing.append(f"{path.name}:{name}")
        assert not missing, (
            "Unrendered diagrams - run `python tools/render_diagrams.py`: "
            + ", ".join(missing)
        )

    def test_svgs_use_native_text_not_foreign_objects(self):
        """Browsers do not render <foreignObject> inside an SVG loaded via
        <img>, so labels would be invisible."""
        for svg in docs_service.DIAGRAM_DIR.glob("*.svg"):
            content = svg.read_text(encoding="utf-8")
            assert "<foreignObject" not in content, svg.name
            assert "<text" in content or "<tspan" in content, svg.name

    def test_referenced_svgs_are_served(self, client):
        html = client.get("/docs/03-architecture").data.decode()
        for ref in set(re.findall(r"/static/diagrams/[a-f0-9]+\.svg", html)):
            assert client.get(ref).status_code == 200, ref

    def test_docs_pages_load_no_external_resources(self, client):
        """The offline guarantee: nothing may be fetched from the internet."""
        html = client.get("/docs/03-architecture").data.decode()
        assert "http://" not in html.replace("http://www.w3.org", "")
        assert "cdn." not in html
