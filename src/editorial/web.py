"""Lightweight browser workspace for CodeQuest editorial review."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .drafting import (
    ArticleDraftGenerator,
    DraftGenerationError,
    create_ollama_cloud_draft_generator,
)
from .models import ArticleType, DecisionOutcome, DraftDecision
from .preferences import build_preference_profile
from .quality import evaluate_draft
from .store import (
    EDITORIAL_STATUSES,
    FEEDBACK_DIMENSIONS,
    FEEDBACK_SCOPES,
    FEEDBACK_SIGNALS,
    EditorialStore,
)


_STYLE = """
:root{color-scheme:light;--ink:#111827;--muted:#68707d;--soft:#f5f6f8;--panel:#fff;
--line:#e5e7eb;--accent:#f47a2a;--accent-soft:#fff1e8;--success:#18865b;--warning:#b66a13;
--danger:#b93a35;--shadow:0 14px 36px rgba(17,24,39,.07)}*{box-sizing:border-box}body{margin:0;
font:15px/1.6 Arial,Helvetica,sans-serif;background:var(--soft);color:var(--ink)}a{color:inherit}
.site-header{position:sticky;top:0;z-index:20;background:rgba(255,255,255,.94);backdrop-filter:blur(12px);
border-bottom:1px solid rgba(229,231,235,.8)}.top{max-width:1240px;margin:auto;min-height:72px;padding:0 24px;
display:flex;justify-content:space-between;align-items:center}.brand{position:relative;font-size:22px;font-weight:800;
text-decoration:none;letter-spacing:-.03em}.brand:after{content:"";position:absolute;left:2px;right:2px;bottom:-9px;
height:3px;background:var(--accent)}.nav{display:flex;gap:24px;align-items:center}.nav a{text-decoration:none;color:#303744}
.nav a:hover{color:var(--accent)}.horizon-chip{padding:8px 13px;border-radius:999px;background:var(--accent);
color:#fff;font-size:12px;font-weight:700;box-shadow:0 8px 18px rgba(244,122,42,.22)}.shell{max-width:1240px;
margin:auto;padding:48px 24px 80px}.page-head{max-width:850px;margin-bottom:34px}.eyebrow{color:var(--accent);
font-size:12px;font-weight:800;letter-spacing:.12em;text-transform:uppercase}.muted{color:var(--muted)}h1{font-size:46px;
line-height:1.08;letter-spacing:-.045em;margin:6px 0 14px}h1 .accent{color:var(--accent)}h2{font-size:20px;
letter-spacing:-.02em;margin:0 0 14px}h3{font-size:17px;margin:22px 0 8px}.grid{display:grid;
grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}.card,.panel{background:var(--panel);
border:1px solid var(--line);border-radius:18px;padding:24px}.card{position:relative;text-decoration:none;overflow:hidden;
transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.card:before{content:"";position:absolute;
left:0;right:0;top:0;height:3px;background:var(--accent);opacity:0;transition:opacity .18s ease}.card:hover{transform:translateY(-3px);
border-color:#f2c3a3;box-shadow:var(--shadow)}.card:hover:before{opacity:1}.badge{display:inline-flex;align-items:center;
border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:#4b5563;background:#fff;font-size:11px;
font-weight:800;text-transform:uppercase;letter-spacing:.06em}.badge.candidate{background:#f3f4f6}.badge.selected,
.badge.prefer,.badge.pass{color:var(--success);background:#eefaf5;border-color:#cdebdc}.badge.approved{color:#fff;
background:var(--success);border-color:var(--success)}.badge.ready_for_approval{color:#c75b17;background:var(--accent-soft);
border-color:#ffd2b6}.badge.needs_revision,.badge.avoid,.badge.warning{color:var(--warning);
background:#fff7ed;border-color:#fed7aa}.badge.block{color:var(--danger);background:#fef2f2;border-color:#fecaca}.meta{display:flex;
align-items:center;gap:8px;flex-wrap:wrap;margin:12px 0}.layout{display:grid;grid-template-columns:minmax(0,1.55fr)
minmax(310px,.75fr);gap:20px;align-items:start}.stack{display:grid;gap:20px}.panel{box-shadow:0 1px 2px rgba(17,24,39,.02)}
.source{padding:18px 0;border-top:1px solid var(--line)}.source:first-of-type{border-top:0}.source a{color:#c75b17;
overflow-wrap:anywhere}.facts li,.questions li{margin:7px 0}form{display:grid;gap:10px}form+form{margin-top:14px}
select,textarea,button{width:100%;font:inherit;border:1px solid #d9dde3;border-radius:12px;padding:11px 13px}
select,textarea{color:var(--ink);background:#fff}textarea{min-height:108px;resize:vertical}button{background:var(--ink);
color:#fff;font-weight:750;cursor:pointer;border-color:var(--ink);border-radius:999px}button:hover{filter:brightness(1.08)}
button:disabled{opacity:.45;cursor:not-allowed}.button-approve{background:var(--accent);border-color:var(--accent)}
.button-revise{background:#fff;color:var(--ink);border-color:#cfd4dc}.feedback{border-top:1px solid var(--line);padding:14px 0}
.rule{border-left:3px solid var(--success);padding-left:13px}.rule.avoid{border-color:var(--accent)}pre{white-space:pre-wrap;
font:12px/1.6 ui-monospace,SFMono-Regular,monospace;color:var(--muted);background:#f8f9fa;border-radius:12px;padding:14px}
.draft{padding:32px}.draft h2{font-size:30px;line-height:1.15}.draft .dek{font-size:17px}.quality-check{display:grid;
grid-template-columns:auto 1fr;gap:10px;padding:12px 0;border-top:1px solid var(--line)}.quality-check:first-of-type{border-top:0}
.quality-check p{margin:0}.quality-check small{display:block;color:var(--muted)}.decision{background:var(--accent-soft);
border:1px solid #ffd2b6;border-radius:14px;padding:14px}.empty{text-align:center;padding:76px 24px;background:#fff}
@media(max-width:820px){.layout{grid-template-columns:1fr}.nav{gap:12px}.horizon-chip{display:none}h1{font-size:35px}
.shell{padding-top:32px}}@media(max-width:560px){.top{padding:0 16px}.nav a{font-size:13px}.shell{padding-left:16px;
padding-right:16px}.panel,.card{padding:19px}.draft{padding:22px}h1{font-size:31px}}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · CodeQuest</title><style>{_STYLE}</style></head>"
        "<body><header class='site-header'><div class='top'>"
        "<a class='brand' href='/'>CodeQuest</a>"
        "<nav class='nav'><a href='/'>Editorial</a><a href='/preferences'>Memory</a>"
        "<span class='horizon-chip'>Horizon powered</span></nav></div></header>"
        f"<main class='shell'>{body}</main></body></html>"
    )


def _rules_html(profile) -> str:
    if not profile.rules:
        return "<p class='muted'>No applicable preferences yet.</p>"
    return "".join(
        f"<article class='feedback rule {escape(rule.signal.value)}'>"
        f"<span class='badge {escape(rule.signal.value)}'>{escape(rule.signal.value)}</span> "
        f"<span class='badge'>{escape(rule.dimension.replace('_', ' '))}</span> "
        f"<span class='muted'>{escape(rule.scope.value.replace('_', ' '))}</span>"
        f"<p>{escape(rule.instruction)}</p>"
        + (
            f"<small class='muted'>Repeated {rule.evidence_count} times</small>"
            if rule.evidence_count > 1
            else ""
        )
        + "</article>"
        for rule in profile.rules
    )


def _draft_html(draft) -> str:
    if draft is None:
        return (
            "<article class='panel'><h2>Article draft</h2>"
            "<p class='muted'>No draft generated yet.</p></article>"
        )
    sections = []
    for section in draft.sections:
        paragraphs = []
        for paragraph in section.paragraphs:
            citations = " ".join(
                f"<a class='badge' href='{escape(str(draft.source_map[source_id]), quote=True)}' "
                f"target='_blank' rel='noopener'>{escape(source_id)}</a>"
                for source_id in paragraph.source_ids
            )
            paragraphs.append(f"<p>{escape(paragraph.text)} {citations}</p>")
        sections.append(f"<section><h3>{escape(section.heading)}</h3>{''.join(paragraphs)}</section>")
    return (
        "<article class='panel draft'><p class='eyebrow'>UNPUBLISHED REVIEW DRAFT</p>"
        f"<h2>{escape(draft.title)}</h2><p class='muted dek'>{escape(draft.dek)}</p>"
        f"{''.join(sections)}<small class='muted'>Generated with {escape(draft.generator_model)} · "
        f"{escape(draft.prompt_version)} · {escape(draft.created_at.isoformat())}</small></article>"
    )


def _quality_html(report) -> str:
    return "".join(
        f"<div class='quality-check'><span class='badge {escape(check.status.value)}'>"
        f"{escape(check.status.value)}</span><p><strong>{escape(check.label)}</strong>"
        f"<small>{escape(check.detail)}</small></p></div>"
        for check in report.checks
    )


def _decision_html(decision) -> str:
    if decision is None:
        return "<p class='muted'>No decision has been recorded for this story.</p>"
    return (
        f"<div class='decision'><span class='badge {escape(decision.outcome.value)}'>"
        f"{escape(decision.outcome.value.replace('_', ' '))}</span>"
        + (f"<p>{escape(decision.notes)}</p>" if decision.notes else "")
        + f"<small class='muted'>Decision for {escape(decision.draft_id)} · "
        f"{escape(decision.created_at.isoformat())}</small></div>"
    )


def create_app(
    db_path: str | Path = "data/codequest-editorial.sqlite3",
    draft_generator_factory: Callable[[], ArticleDraftGenerator] | None = None,
) -> FastAPI:
    app = FastAPI(title="CodeQuest Editorial Workspace")
    store = EditorialStore(db_path)
    writer_factory = draft_generator_factory or create_ollama_cloud_draft_generator

    @app.get("/", response_class=HTMLResponse)
    def inbox() -> HTMLResponse:
        records = store.list_items()
        if not records:
            return _page(
                "Inbox",
                "<section class='empty panel'><p class='eyebrow'>EDITORIAL INBOX</p>"
                "<h1>No candidates <span class='accent'>yet</span></h1><p class='muted'>Import a Horizon content item "
                "with the codequest-workspace command.</p></section>",
            )
        cards = []
        for record in records:
            brief = record.packet.brief
            href = f"/items/{quote(brief.content_item_id, safe='')}"
            cards.append(
                f"<a class='card' href='{href}'><span class='badge {escape(record.status)}'>{escape(record.status)}</span>"
                f"<h2>{escape(brief.working_title)}</h2>"
                f"<p class='muted'>{escape(brief.central_angle)}</p>"
                f"<div class='meta'><span>{escape(brief.article_type.value.replace('_', ' '))}</span>"
                f"<span>·</span><span>{len(record.packet.evidence.sources)} source(s)</span></div></a>"
            )
        return _page(
            "Inbox",
            "<header class='page-head'><p class='eyebrow'>EDITORIAL INBOX</p>"
            "<h1>Find the signal. Shape the <span class='accent'>story.</span></h1>"
            "<p class='muted'>Review the angle, evidence, and editorial memory before drafting.</p></header>"
            f"<section class='grid'>{''.join(cards)}</section>",
        )

    @app.get("/preferences", response_class=HTMLResponse)
    def preferences() -> HTMLResponse:
        sections = []
        global_profile = build_preference_profile(store)
        sections.append(
            "<article class='panel'><p class='eyebrow'>ALL ARTICLES</p>"
            f"<h2>Global preferences</h2>{_rules_html(global_profile)}</article>"
        )
        for article_type in ArticleType:
            profile = build_preference_profile(store, article_type=article_type)
            specific_rules = [rule for rule in profile.rules if rule.scope.value == "article_type"]
            if not specific_rules:
                continue
            profile.rules = specific_rules
            sections.append(
                f"<article class='panel'><p class='eyebrow'>{escape(article_type.value.replace('_', ' ').upper())}</p>"
                f"<h2>Article-type preferences</h2>{_rules_html(profile)}</article>"
            )
        return _page(
            "Preferences",
            "<header class='page-head'><p class='eyebrow'>EDITORIAL MEMORY</p>"
            "<h1>Your taste, made <span class='accent'>repeatable.</span></h1>"
            "<p class='muted'>Only explicit, reusable feedback appears here. Story-only notes stay with their story.</p></header>"
            f"<section class='stack'>{''.join(sections)}</section>",
        )

    @app.get("/items/{content_item_id}", response_class=HTMLResponse)
    def detail(content_item_id: str) -> HTMLResponse:
        record = store.get_item(content_item_id)
        if not record:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        packet = record.packet
        brief = packet.brief
        feedback = store.list_feedback(content_item_id)
        latest_draft = store.get_latest_draft(content_item_id)
        quality_report = evaluate_draft(packet, latest_draft) if latest_draft else None
        latest_decision = store.get_latest_decision(content_item_id)
        decision_for_latest = (
            latest_decision
            if latest_draft and latest_decision and latest_decision.draft_id == latest_draft.draft_id
            else None
        )
        preference_profile = build_preference_profile(
            store,
            article_type=brief.article_type,
            content_item_id=content_item_id,
        )
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
            f"<article class='feedback'><span class='badge'>{escape(str(entry['signal']))}</span> "
            f"<span class='badge'>{escape(str(entry['dimension']))}</span> "
            f"<span class='muted'>{escape(str(entry['scope']).replace('_', ' '))}</span>"
            f"<p>{escape(str(entry['note']))}</p><small class='muted'>{escape(str(entry['created_at']))}</small></article>"
            for entry in feedback
        ) or "<p class='muted'>No feedback yet.</p>"
        status_options = "".join(
            f"<option value='{status}'{' selected' if status == record.status else ''}>"
            f"{escape(status.replace('_', ' ').title())}</option>"
            for status in EDITORIAL_STATUSES
            if status != "approved"
        )
        dimension_options = "".join(
            f"<option value='{dimension}'>{escape(dimension.replace('_', ' ').title())}</option>"
            for dimension in FEEDBACK_DIMENSIONS
        )
        signal_options = "".join(
            f"<option value='{signal}'>{escape(signal.title())}</option>"
            for signal in FEEDBACK_SIGNALS
        )
        scope_labels = {
            "story": "This story only",
            "article_type": "This article type",
            "global": "All articles",
        }
        scope_options = "".join(
            f"<option value='{scope}'>{escape(scope_labels[scope])}</option>"
            for scope in FEEDBACK_SCOPES
        )
        encoded_id = quote(content_item_id, safe="")
        if record.status in {"approved", "ready_for_approval"}:
            review_controls = (
                f"<div class='decision'><span class='badge {escape(record.status)}'>"
                f"{escape(record.status.replace('_', ' '))}</span>"
                "<p>This story is locked to its persisted draft decision. Final approval remains in Discord.</p></div>"
            )
        else:
            review_controls = (
                f"<form method='post' action='/items/{encoded_id}/status'>"
                f"<select name='status'>{status_options}</select>"
                "<button type='submit'>Update state</button></form>"
                + (
                    f"<form method='post' action='/items/{encoded_id}/draft'>"
                    f"<button type='submit'>{'Generate revision' if record.status == 'needs_revision' else 'Generate review draft'}</button>"
                    "<small class='muted'>Uses Ollama Cloud and remains unpublished.</small></form>"
                    if record.status in {"selected", "needs_revision"}
                    else "<p class='muted'>Select this story before generating a draft.</p>"
                )
            )
        if latest_draft and quality_report:
            approval_actions = ""
            if record.status not in {"approved", "ready_for_approval"}:
                approval_actions = (
                    f"<form method='post' action='/items/{encoded_id}/decision'>"
                    "<textarea name='notes' placeholder='Approval note or required revisions'></textarea>"
                    f"<button class='button-approve' name='outcome' value='ready_for_approval' type='submit'"
                    f"{' disabled' if not quality_report.can_approve else ''}>Queue for Discord approval</button>"
                    "<button class='button-revise' name='outcome' value='needs_revision' type='submit'>"
                    "Request revision</button></form>"
                )
            approval_panel = (
                "<section class='panel'><h2>Quality gate</h2>"
                f"{_quality_html(quality_report)}</section>"
                "<section class='panel'><h2>Editorial decision</h2>"
                f"{_decision_html(decision_for_latest)}{approval_actions}</section>"
            )
        else:
            approval_panel = (
                "<section class='panel'><h2>Quality gate</h2>"
                "<p class='muted'>Generate a draft to run deterministic checks.</p></section>"
            )
        return _page(
            brief.working_title,
            f"<header class='page-head'><p class='eyebrow'>{escape(brief.article_type.value.replace('_', ' ').upper())}</p>"
            f"<h1>{escape(brief.working_title)}</h1><div class='meta'>"
            f"<span class='badge {escape(record.status)}'>{escape(record.status)}</span>"
            f"<span>{len(packet.evidence.sources)} evidence source(s)</span>"
            f"<span>·</span><span>{len(store.list_drafts(content_item_id))} draft version(s)</span></div></header>"
            "<div class='layout'><section class='stack'>"
            f"<article class='panel'><h2>Central angle</h2><p>{escape(brief.central_angle)}</p>"
            f"<h2>Audience value</h2><p>{escape(brief.audience_value)}</p></article>"
            f"<article class='panel'><h2>Required facts</h2><ul class='facts'>{facts}</ul>"
            f"<h2>Research gaps</h2><ul class='questions'>{questions}</ul></article>"
            f"<article class='panel'><h2>Evidence</h2>{sources}</article>"
            f"{_draft_html(latest_draft)}</section>"
            "<aside class='stack'><section class='panel'><h2>Review state</h2>"
            f"{review_controls}</section>{approval_panel}"
            "<section class='panel'><h2>Add feedback</h2>"
            f"<form method='post' action='/items/{encoded_id}/feedback'>"
            f"<select name='signal'>{signal_options}</select>"
            f"<select name='dimension'>{dimension_options}</select>"
            f"<select name='scope'>{scope_options}</select>"
            "<textarea name='note' required placeholder='What should the system learn or change?'></textarea>"
            "<button type='submit'>Save feedback</button></form></section>"
            f"<section class='panel'><h2>Effective writing profile</h2>{_rules_html(preference_profile)}"
            f"<pre>{escape(preference_profile.writer_instructions())}</pre></section>"
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

    @app.post("/items/{content_item_id}/draft")
    async def generate_draft(content_item_id: str) -> RedirectResponse:
        record = store.get_item(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if record.status not in {"selected", "needs_revision"}:
            raise HTTPException(
                status_code=409,
                detail="Draft generation requires a Selected or Needs Revision story.",
            )
        profile = build_preference_profile(
            store,
            record.packet.brief.article_type,
            content_item_id,
        )
        revision_notes = store.latest_revision_notes(content_item_id)
        try:
            draft = await writer_factory().generate(record.packet, profile, revision_notes)
        except DraftGenerationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Ollama Cloud generation failed; no draft was saved.",
            ) from exc
        store.save_draft(draft)
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    @app.post("/items/{content_item_id}/decision")
    def decide_draft(
        content_item_id: str,
        outcome: str = Form(),
        notes: str = Form(""),
    ) -> RedirectResponse:
        record = store.get_item(content_item_id)
        draft = store.get_latest_draft(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if draft is None:
            raise HTTPException(status_code=409, detail="No draft exists for this story.")
        try:
            decision = DraftDecision(
                content_item_id=content_item_id,
                draft_id=draft.draft_id,
                outcome=DecisionOutcome(outcome),
                notes=notes.strip(),
                quality_report=evaluate_draft(record.packet, draft),
            )
            store.record_decision(decision)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    @app.post("/items/{content_item_id}/feedback")
    def add_feedback(
        content_item_id: str,
        dimension: str = Form(),
        signal: str = Form("prefer"),
        scope: str = Form("story"),
        note: str = Form(),
    ) -> RedirectResponse:
        try:
            store.add_feedback(content_item_id, dimension, note, signal=signal, scope=scope)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    return app
