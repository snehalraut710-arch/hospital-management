"""Public documentation pages.

Public, since this is a college project and whoever is marking it should be
able to read the architecture without an account. In a real deployment this
would sit behind @admin_required.
"""
from flask import Blueprint, abort, current_app, redirect, render_template, url_for

from hospital.services import docs as docs_service

bp = Blueprint("docs", __name__, url_prefix="/docs")


@bp.route("/")
def index():
    return page("index")


@bp.route("/<slug>")
def page(slug):
    # In debug the files are re-read on every request, so editing a Markdown
    # file shows up on refresh without restarting the server.
    rendered = docs_service.render(slug, reload=current_app.debug)
    if rendered is None:
        abort(404)

    pages = docs_service.available()
    slugs = [s for s, _ in pages]
    position = slugs.index(slug)

    return render_template(
        "docs/page.html",
        page=rendered,
        pages=pages,
        previous=pages[position - 1] if position > 0 else None,
        following=pages[position + 1] if position < len(pages) - 1 else None,
    )
