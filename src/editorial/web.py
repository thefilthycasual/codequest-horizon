"""Lightweight browser workspace for CodeQuest editorial review."""

from __future__ import annotations

from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .store import EDITORIAL_STATUSES, FEEDBACK_DIMENSIONS, EditorialStore


_STYLE = """
:root{color-scheme:dark;--bg:#090d12;--panel:#111821;--line:#273241;--text:#edf3fa;
--muted:#91a0b2;--accent:#62d6a7;--warn:#f1bf62}*{box-sizing:border-box}body{margin:0;
font:15px/1.55 ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--text)}
a{color:inherit}.shell{max-width:1120px;margin:auto;padding:32px 20px}.top{display:flex;
justify-content:space-between;align-items:center;margin-bottom:30px}.brand{font-size:20px;font-weight:750;
text-decoration:none}.eyebrow,.muted{color:var(--muted)}h1{font-size:34px;line-height:1.15;margin:4px 0 12px}
h2{font-size:19px;margin:0 0 15px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}
.card,.panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px}.card{text-decoration:none}
.card:hover{border-color:var(--accent)}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;
padding:3px 9px;color:var(--accent);font-size:12px;text-transform:uppercase;letter-spacing:.05em}.meta{display:flex;
gap:8px;flex-wrap:wrap;margin:12px 0}.layout{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,.8fr);gap:18px}
.stack{display:grid;gap:18px}.source{padding:14px 0;border-top:1px solid var(--line)}.source:first-of-type{border-top:0}
.source a{color:var(--accent);overflow-wrap:anywhere}.facts li,.questions li{margin:7px 0}form{display:grid;gap:10px}
select,textarea,button{width:100%;font:inherit;color:var(--text);background:#0b1118;border:1px solid var(--line);
border-radius:9px;padding:10px}textarea{min-height:105px;resize:vertical}button{background:var(--accent);color:#07130e;
font-weight:750;cursor:pointer}.feedback{border-top:1px solid var(--line);padding:12px 0}.empty{text-align:center;padding:70px 20px}
@media(max-width:780px){.layout{grid-template-columns:1fr}h1{font-size:28px}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · CodeQuest</title><style>{_STYLE}</style></head>"
        "<body><main class='shell'><header class='top'>"
        "<a class='brand' href='/'>CodeQuest Editorial</a>"
        "<span class='muted'>Horizon fork spike</span></header>"
        f"{body}</main></body></html>"
    )


def create_app(db_path: str | Path = "data/codequest-editorial.sqlite3") -> FastAPI:
    app = FastAPI(title="CodeQuest Editorial Workspace")
    store = EditorialStore(db_path)

    @app.get("/", response_class=HTMLResponse)
    def inbox() -> HTMLResponse:
        records = store.list_items()
        if not records:
            return _page(
                "Inbox",
                "<section class='empty panel'><p class='eyebrow'>EDITORIAL INBOX</p>"
                "<h1>No candidates yet</h1><p class='muted'>Import a Horizon content item "
                "with the codequest-workspace command.</p></section>",
            )
        cards = []
        for record in records:
            brief = record.packet.brief
            href = f"/items/{quote(brief.content_item_id, safe='')}"
            cards.append(
                f"<a class='card' href='{href}'><span class='badge'>{escape(record.status)}</span>"
                f"<h2>{escape(brief.working_title)}</h2>"
                f"<p class='muted'>{escape(brief.central_angle)}</p>"
                f"<div class='meta'><span>{escape(brief.article_type.value.replace('_', ' '))}</span>"
                f"<span>·</span><span>{len(record.packet.evidence.sources)} source(s)</span></div></a>"
            )
        return _page(
            "Inbox",
            "<p class='eyebrow'>EDITORIAL INBOX</p><h1>Candidate stories</h1>"
            "<p class='muted'>Review the angle and evidence before drafting.</p>"
            f"<section class='grid'>{''.join(cards)}</section>",
        )

    @app.get("/items/{content_item_id}", response_class=HTMLResponse)
    def detail(content_item_id: str) -> HTMLResponse:
        record = store.get_item(content_item_id)
        if not record:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        packet = record.packet
        brief = packet.brief
        feedback = store.list_feedback(content_item_id)
        sources = "".join(
            "<article class='source'>"
            f"<span class='badge'>{'primary' if source.is_primary else 'supporting'}</span>"
            f"<h3>{escape(source.title)}</h3>"
            f"<a href='{escape(str(source.url), quote=True)}' target='_blank' rel='noopener'>"
            f"{escape(str(source.url))}</a>"
            f"<p>{escape(source.excerpt) if source.excerpt else '<span class=muted>No excerpt captured.</span>'}</p>"
            "</article>"
            for source in packet.evidence.sources
        )
        facts = "".join(f"<li>{escape(fact)}</li>" for fact in brief.required_facts)
        questions = "".join(
            f"<li>{escape(question)}</li>" for question in packet.evidence.unresolved_questions
        ) or "<li>No open research gaps recorded.</li>"
        feedback_html = "".join(
            f"<article class='feedback'><span class='badge'>{escape(str(entry['dimension']))}</span>"
            f"<p>{escape(str(entry['note']))}</p><small class='muted'>{escape(str(entry['created_at']))}</small></article>"
            for entry in feedback
        ) or "<p class='muted'>No feedback yet.</p>"
        status_options = "".join(
            f"<option value='{status}'{' selected' if status == record.status else ''}>"
            f"{escape(status.replace('_', ' ').title())}</option>"
            for status in EDITORIAL_STATUSES
        )
        dimension_options = "".join(
            f"<option value='{dimension}'>{escape(dimension.replace('_', ' ').title())}</option>"
            for dimension in FEEDBACK_DIMENSIONS
        )
        encoded_id = quote(content_item_id, safe="")
        return _page(
            brief.working_title,
            f"<p class='eyebrow'>{escape(brief.article_type.value.replace('_', ' ').upper())}</p>"
            f"<h1>{escape(brief.working_title)}</h1><div class='meta'><span class='badge'>{escape(record.status)}</span>"
            f"<span>{len(packet.evidence.sources)} evidence source(s)</span></div>"
            "<div class='layout'><section class='stack'>"
            f"<article class='panel'><h2>Central angle</h2><p>{escape(brief.central_angle)}</p>"
            f"<h2>Audience value</h2><p>{escape(brief.audience_value)}</p></article>"
            f"<article class='panel'><h2>Required facts</h2><ul class='facts'>{facts}</ul>"
            f"<h2>Research gaps</h2><ul class='questions'>{questions}</ul></article>"
            f"<article class='panel'><h2>Evidence</h2>{sources}</article></section>"
            "<aside class='stack'><section class='panel'><h2>Review state</h2>"
            f"<form method='post' action='/items/{encoded_id}/status'><select name='status'>{status_options}</select>"
            "<button type='submit'>Update state</button></form></section>"
            "<section class='panel'><h2>Add feedback</h2>"
            f"<form method='post' action='/items/{encoded_id}/feedback'><select name='dimension'>{dimension_options}</select>"
            "<textarea name='note' required placeholder='What should the system learn or change?'></textarea>"
            "<button type='submit'>Save feedback</button></form></section>"
            f"<section class='panel'><h2>Feedback history</h2>{feedback_html}</section></aside></div>",
        )

    @app.post("/items/{content_item_id}/status")
    def update_status(content_item_id: str, status: str = Form()) -> RedirectResponse:
        try:
            store.set_status(content_item_id, status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    @app.post("/items/{content_item_id}/feedback")
    def add_feedback(
        content_item_id: str,
        dimension: str = Form(),
        note: str = Form(),
    ) -> RedirectResponse:
        try:
            store.add_feedback(content_item_id, dimension, note)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    return app
