"""Shared Jinja environment + render helper that injects common context."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import REPO_ROOT

templates = Jinja2Templates(directory=str(REPO_ROOT / "app" / "templates"))


def render(
    request: Request,
    template: str,
    context: Mapping | None = None,
    *,
    status_code: int = 200,
    nav: str | None = None,
    flash: dict | None = None,
) -> HTMLResponse:
    ctx = dict(context or {})
    ctx.setdefault("nav", nav)
    ctx.setdefault("flash", flash)
    return templates.TemplateResponse(request, template, ctx, status_code=status_code)


def render_partial(template: str, context: Mapping | None = None) -> HTMLResponse:
    """Render a partial template (no base layout). Used for HTMX-style swaps."""
    tpl = templates.get_template(template)
    return HTMLResponse(tpl.render(**(context or {})))
