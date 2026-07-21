"""Lightweight browser workspace for CodeQuest editorial review."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .discord import (
    DiscordApprovalBridge,
    DiscordConfig,
    modal_value,
    revision_modal,
    verify_discord_signature,
)

from .drafting import (
    ArticleDraftGenerator,
    DraftGenerationError,
    create_ollama_cloud_draft_generator,
)
from .models import (
    ArticleType,
    DecisionOutcome,
    DiscordApprovalRequest,
    DiscordApprovalStatus,
    DraftDecision,
)
from .preferences import build_preference_profile
from .quality import evaluate_draft
from .store import (
    EDITORIAL_STATUSES,
    FEEDBACK_DIMENSIONS,
    FEEDBACK_SCOPES,
    FEEDBACK_SIGNALS,
    EditorialStore,
)
from .wordpress import (
    WordPressConfig,
    WordPressPublisher,
    build_wordpress_payload,
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

_ADMIN_STYLE = """
:root{--sidebar:272px}.site-header{display:none}.sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar);
background:#fff;border-right:1px solid var(--line);padding:22px 16px;display:flex;flex-direction:column;z-index:20}
.workspace{display:flex;align-items:center;gap:12px;padding:10px 9px 22px;border-bottom:1px solid var(--line);margin-bottom:18px}
.workspace-mark{width:42px;height:42px;display:grid;place-items:center;border-radius:13px;background:var(--ink);color:#fff;
font-weight:850;letter-spacing:-.04em}.workspace strong,.workspace small{display:block}.workspace strong{font-size:16px}.workspace small{color:var(--muted);font-size:12px}
.nav-label{margin:12px 11px 7px;color:#9a9fa8;font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}
.side-nav{display:grid;gap:4px}.nav-item{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:10px;
text-decoration:none;color:#464b55;font-weight:650}.nav-item:hover{background:#f6f6f7}.nav-item.active{background:#f1f2f3;color:#111}
.nav-icon{width:19px;height:19px;display:grid;place-items:center;color:#747a84}.nav-icon svg{width:18px;height:18px;
fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}.nav-item.active .nav-icon{color:var(--accent)}
.sidebar-foot{margin-top:auto;border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;align-items:center;gap:10px}
.status-dot{width:9px;height:9px;border-radius:50%;background:#2db879;box-shadow:0 0 0 4px #e8f8f1}.sidebar-foot strong,.sidebar-foot small{display:block}
.sidebar-foot small{font-size:11px;color:var(--muted)}.content{margin-left:var(--sidebar);min-height:100vh}.shell{max-width:1320px;padding:42px 38px 80px}
.stat-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:14px;margin-bottom:28px}.stat-card{background:#fff;border:1px solid var(--line);
border-radius:14px;padding:20px}.stat-card small{color:var(--muted)}.stat-value{display:block;font-size:34px;font-weight:820;line-height:1.1;letter-spacing:-.04em;margin:7px 0}
.section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:26px 0 14px}.text-link{color:#c75b17;font-weight:700;text-decoration:none}
.discord-panel{border-color:#d9dcff;background:#fafaff}.discord-panel .discord-badge{background:#5865f2;color:#fff;border-color:#5865f2}
.crumb{display:flex;gap:8px;color:var(--muted);font-size:12px;margin-bottom:18px}.crumb a{text-decoration:none}.crumb a:hover{color:var(--accent)}
@media(max-width:980px){.stat-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:820px){.sidebar{position:static;width:auto;padding:12px}.workspace{padding-bottom:12px;margin-bottom:8px}.workspace small,.nav-label,.sidebar-foot{display:none}
.side-nav{display:flex;overflow:auto}.nav-item{white-space:nowrap}.content{margin-left:0}.shell{padding:30px 18px 70px}}
@media(max-width:480px){.stat-grid{grid-template-columns:1fr 1fr}.nav-item{font-size:13px;padding:9px}.nav-icon{display:none}}
"""

_ICONS = {
    "overview": "<svg viewBox='0 0 24 24'><rect x='3' y='3' width='7' height='7' rx='1'/><rect x='14' y='3' width='7' height='7' rx='1'/><rect x='3' y='14' width='7' height='7' rx='1'/><rect x='14' y='14' width='7' height='7' rx='1'/></svg>",
    "editorial": "<svg viewBox='0 0 24 24'><path d='M4 5h16v14H4z'/><path d='M8 9h8M8 13h8M8 17h5'/></svg>",
    "drafts": "<svg viewBox='0 0 24 24'><path d='M6 3h9l4 4v14H6z'/><path d='M14 3v5h5M9 12h6M9 16h6'/></svg>",
    "memory": "<svg viewBox='0 0 24 24'><path d='M12 3a4 4 0 0 0-4 4v1a4 4 0 0 0 0 8v1a4 4 0 0 0 4 4'/><path d='M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1 0 8v1a4 4 0 0 1-4 4M12 3v18'/></svg>",
}


def _nav_item(key: str, label: str, href: str, active: str) -> str:
    selected = " active" if key == active else ""
    return f"<a class='nav-item{selected}' href='{href}'><span class='nav-icon'>{_ICONS[key]}</span>{label}</a>"


def _page(title: str, body: str, active: str = "overview") -> HTMLResponse:
    navigation = "".join(
        (
            _nav_item("overview", "Overview", "/", active),
            _nav_item("editorial", "Editorial queue", "/editorial", active),
            _nav_item("drafts", "Draft library", "/drafts", active),
            _nav_item("memory", "Editorial memory", "/preferences", active),
        )
    )
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · CodeQuest</title><style>{_STYLE}{_ADMIN_STYLE}</style></head>"
        "<body><aside class='sidebar'><div class='workspace'><div class='workspace-mark'>CQ</div>"
        "<div><strong>CodeQuest</strong><small>Editorial studio</small></div></div>"
        f"<p class='nav-label'>Workspace</p><nav class='side-nav'>{navigation}</nav>"
        "<div class='sidebar-foot'><span class='status-dot'></span><div><strong>Local workspace</strong>"
        "<small>Horizon discovery connected</small></div></div></aside>"
        f"<main class='content'><div class='shell'>{body}</div></main></body></html>"
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
    discord_bridge_factory: Callable[[], DiscordApprovalBridge] | None = None,
    wordpress_publisher_factory: Callable[[], WordPressPublisher] | None = None,
) -> FastAPI:
    app = FastAPI(title="CodeQuest Editorial Workspace")
    store = EditorialStore(db_path)
    writer_factory = draft_generator_factory or create_ollama_cloud_draft_generator
    bridge_factory = discord_bridge_factory or (
        lambda: DiscordApprovalBridge(DiscordConfig.from_env())
    )
    publisher_factory = wordpress_publisher_factory or (
        lambda: WordPressPublisher(WordPressConfig.from_env())
    )

    def approved_draft(content_item_id: str):
        record = store.get_item(content_item_id)
        draft = store.get_latest_draft(content_item_id)
        decision = store.get_latest_decision(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if (
            draft is None
            or decision is None
            or record.status != "approved"
            or decision.outcome != DecisionOutcome.APPROVED
            or decision.draft_id != draft.draft_id
        ):
            raise HTTPException(
                status_code=409,
                detail="The exact latest draft must be approved in the workspace first.",
            )
        return record, draft

    @app.get("/", response_class=HTMLResponse)
    def overview() -> HTMLResponse:
        stats = store.dashboard_stats()
        recent = store.list_items()[:4]
        cards = "".join(
            f"<a class='card' href='/items/{quote(record.packet.brief.content_item_id, safe='')}'>"
            f"<span class='badge {escape(record.status)}'>{escape(record.status.replace('_', ' '))}</span>"
            f"<h2>{escape(record.packet.brief.working_title)}</h2>"
            f"<p class='muted'>{escape(record.packet.brief.central_angle)}</p></a>"
            for record in recent
        ) or "<article class='panel empty'><p class='muted'>No stories have entered the workspace yet.</p></article>"
        return _page(
            "Overview",
            "<header class='page-head'><p class='eyebrow'>WORKSPACE OVERVIEW</p>"
            "<h1>Your editorial operation, <span class='accent'>at a glance.</span></h1>"
            "<p class='muted'>Draft, learn, and approve from one focused workspace.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Stories</small><strong class='stat-value'>{stats['items']}</strong><span class='muted'>in the pipeline</span></article>"
            f"<article class='stat-card'><small>Needs attention</small><strong class='stat-value'>{stats['attention']}</strong><span class='muted'>selected or revising</span></article>"
            f"<article class='stat-card'><small>Draft versions</small><strong class='stat-value'>{stats['drafts']}</strong><span class='muted'>saved locally</span></article>"
            f"<article class='stat-card'><small>Learned preferences</small><strong class='stat-value'>{stats['reusable_feedback']}</strong><span class='muted'>reusable rules</span></article>"
            "</section><div class='section-head'><h2>Recent stories</h2><a class='text-link' href='/editorial'>View editorial queue →</a></div>"
            f"<section class='grid'>{cards}</section>",
            active="overview",
        )

    @app.get("/editorial", response_class=HTMLResponse)
    def inbox() -> HTMLResponse:
        records = store.list_items()
        if not records:
            return _page(
                "Editorial queue",
                "<section class='empty panel'><p class='eyebrow'>EDITORIAL INBOX</p>"
                "<h1>No candidates <span class='accent'>yet</span></h1><p class='muted'>Import a Horizon content item "
                "with the codequest-workspace command.</p></section>",
                active="editorial",
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
            "Editorial queue",
            "<header class='page-head'><p class='eyebrow'>EDITORIAL INBOX</p>"
            "<h1>Find the signal. Shape the <span class='accent'>story.</span></h1>"
            "<p class='muted'>Review the angle, evidence, and editorial memory before drafting.</p></header>"
            f"<section class='grid'>{''.join(cards)}</section>",
            active="editorial",
        )

    @app.get("/drafts", response_class=HTMLResponse)
    def drafts() -> HTMLResponse:
        entries = []
        for record in store.list_items():
            for draft in store.list_drafts(record.packet.brief.content_item_id):
                entries.append((draft, record.status))
        entries.sort(key=lambda item: item[0].created_at, reverse=True)
        cards = "".join(
            f"<a class='card' href='/items/{quote(draft.content_item_id, safe='')}'>"
            f"<span class='badge {escape(status)}'>{escape(status.replace('_', ' '))}</span>"
            f"<h2>{escape(draft.title)}</h2><p class='muted'>{escape(draft.dek)}</p>"
            f"<small class='muted'>{escape(draft.created_at.isoformat())} · {len(draft.sections)} sections</small></a>"
            for draft, status in entries
        ) or "<article class='panel empty'><h2>No drafts yet</h2><p class='muted'>Select a story in the editorial queue to generate the first review draft.</p></article>"
        return _page(
            "Draft library",
            "<header class='page-head'><p class='eyebrow'>DRAFT LIBRARY</p>"
            "<h1>Every version, easy to <span class='accent'>find.</span></h1>"
            "<p class='muted'>Review all generated article versions without mixing them into discovery.</p></header>"
            f"<section class='grid'>{cards}</section>",
            active="drafts",
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
            active="memory",
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
        discord_request = store.get_latest_discord_request(content_item_id)
        wordpress_delivery = (
            store.get_wordpress_delivery(latest_draft.draft_id) if latest_draft else None
        )
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
        if record.status == "approved":
            review_controls = (
                f"<div class='decision'><span class='badge {escape(record.status)}'>"
                f"{escape(record.status.replace('_', ' '))}</span>"
                "<p>This draft was approved in the CodeQuest workspace. Publishing remains a separate step.</p></div>"
            )
        elif record.status == "ready_for_approval":
            review_controls = (
                "<div class='decision'><span class='badge ready_for_approval'>Ready for review</span>"
                "<p>The workspace editor can now make the final decision.</p></div>"
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
            if record.status != "approved" and not (
                decision_for_latest
                and decision_for_latest.outcome == DecisionOutcome.NEEDS_REVISION
            ):
                approval_actions = (
                    f"<form method='post' action='/items/{encoded_id}/decision'>"
                    "<textarea name='notes' placeholder='Approval note or required revisions'></textarea>"
                    f"<button class='button-approve' name='outcome' value='approved' type='submit'"
                    f"{' disabled' if not quality_report.can_approve else ''}>Approve in workspace</button>"
                    "<button class='button-revise' name='outcome' value='needs_revision' type='submit'>"
                    "Request revision</button></form>"
                )
            if discord_request and discord_request.status == DiscordApprovalStatus.PENDING:
                discord_controls = (
                    "<p><strong>Shared with Discord for optional feedback</strong></p>"
                    f"<small class='muted'>Request {escape(discord_request.request_id)}</small>"
                )
            elif discord_request and discord_request.status == DiscordApprovalStatus.DELIVERY_FAILED:
                discord_controls = (
                    "<p class='muted'>The last delivery failed. Check the Discord settings and retry.</p>"
                    f"<form method='post' action='/items/{encoded_id}/discord'><button type='submit'>Retry Discord delivery</button></form>"
                )
            elif discord_request and discord_request.status in {
                DiscordApprovalStatus.ENDORSED,
                DiscordApprovalStatus.REVISION_SUGGESTED,
            }:
                discord_controls = (
                    f"<p><strong>Discord response: {escape(discord_request.status.value.replace('_', ' '))}</strong></p>"
                    + (
                        f"<p>{escape(discord_request.revision_notes)}</p>"
                        if discord_request.revision_notes
                        else ""
                    )
                    + "<small class='muted'>Advisory only—the workspace decision is authoritative.</small>"
                )
            elif record.status in {"selected", "ready_for_approval", "approved"}:
                discord_controls = (
                    "<p class='muted'>Optionally share this version for a quick team signal. Discord cannot approve it.</p>"
                    f"<form method='post' action='/items/{encoded_id}/discord'><button type='submit'>Share with Discord</button></form>"
                )
            else:
                discord_controls = (
                    "<p class='muted'>Generate the requested revision before sharing another version with Discord.</p>"
                )
            approval_panel = (
                "<section class='panel'><h2>Quality gate</h2>"
                f"{_quality_html(quality_report)}</section>"
                "<section class='panel'><h2>Editorial decision</h2>"
                f"{_decision_html(decision_for_latest)}{approval_actions}</section>"
                "<section class='panel discord-panel'><span class='badge discord-badge'>Discord · optional</span>"
                f"{discord_controls}</section>"
            )
        else:
            approval_panel = (
                "<section class='panel'><h2>Quality gate</h2>"
                "<p class='muted'>Generate a draft to run deterministic checks.</p></section>"
            )
        publishing_panel = ""
        if latest_draft and record.status == "approved":
            if wordpress_delivery and wordpress_delivery.status.value == "draft_created":
                publishing_controls = (
                    "<p><strong>WordPress draft created</strong></p>"
                    f"<p class='muted'>Post #{wordpress_delivery.post_id} · delivered in {wordpress_delivery.attempts} attempt(s)</p>"
                    f"<a class='text-link' href='{escape(wordpress_delivery.editor_url or '', quote=True)}' target='_blank' rel='noopener'>Open in WordPress editor →</a>"
                )
            elif wordpress_delivery and wordpress_delivery.status.value == "failed":
                publishing_controls = (
                    "<p><strong>Delivery failed</strong></p>"
                    f"<p class='muted'>{escape(wordpress_delivery.error_message)}</p>"
                    f"<form method='post' action='/items/{encoded_id}/wordpress'><button type='submit'>Retry WordPress draft</button></form>"
                )
            else:
                publishing_controls = (
                    "<p class='muted'>Preview the exact approved payload, then create a WordPress draft. This cannot publish the post.</p>"
                    f"<a class='text-link' href='/items/{encoded_id}/wordpress/preview'>Preview WordPress payload →</a>"
                    f"<form method='post' action='/items/{encoded_id}/wordpress'><button type='submit'>Create WordPress draft</button></form>"
                )
            publishing_panel = (
                "<section class='panel'><span class='badge approved'>WordPress · draft only</span>"
                f"<h2>Delivery</h2>{publishing_controls}</section>"
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
            f"{review_controls}</section>{approval_panel}{publishing_panel}"
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
            active="editorial",
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

    @app.post("/items/{content_item_id}/discord")
    async def send_to_discord(content_item_id: str) -> RedirectResponse:
        record = store.get_item(content_item_id)
        draft = store.get_latest_draft(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if draft is None or record.status not in {"selected", "ready_for_approval", "approved"}:
            raise HTTPException(
                status_code=409,
                detail="Only a reviewed draft can be shared with Discord.",
            )
        if not evaluate_draft(record.packet, draft).can_approve:
            raise HTTPException(status_code=409, detail="Resolve blocking quality issues first.")
        request = DiscordApprovalRequest(
            content_item_id=content_item_id,
            draft_id=draft.draft_id,
        )
        try:
            store.create_discord_request(request)
            channel_id, message_id = await bridge_factory().send_request(
                request,
                draft,
                evaluate_draft(record.packet, draft),
            )
            store.update_discord_delivery(request.request_id, channel_id, message_id)
        except (KeyError, ValueError) as exc:
            if store.get_discord_request(request.request_id):
                store.mark_discord_delivery_failed(request.request_id)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            store.mark_discord_delivery_failed(request.request_id)
            raise HTTPException(
                status_code=502,
                detail="Discord delivery failed; the draft remains unapproved.",
            ) from exc
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    @app.get("/items/{content_item_id}/wordpress/preview", response_class=HTMLResponse)
    def preview_wordpress(content_item_id: str) -> HTMLResponse:
        _record, draft = approved_draft(content_item_id)
        payload = build_wordpress_payload(draft)
        encoded_id = quote(content_item_id, safe="")
        return _page(
            f"WordPress preview · {draft.title}",
            "<div class='crumb'><a href='/drafts'>Draft library</a><span>›</span>"
            f"<a href='/items/{encoded_id}'>Article review</a><span>›</span><span>WordPress preview</span></div>"
            "<header class='page-head'><p class='eyebrow'>WORDPRESS PAYLOAD PREVIEW</p>"
            f"<h1>{escape(draft.title)}</h1>"
            "<p class='muted'>This is the exact article body that will be sent with status <strong>draft</strong>.</p></header>"
            "<div class='layout'><article class='panel draft'>"
            f"<h2>{escape(str(payload['title']))}</h2>{payload['content']}</article>"
            "<aside class='stack'><section class='panel'><span class='badge approved'>Draft only</span>"
            "<h2>Ready to deliver?</h2><p class='muted'>WordPress will create an unpublished draft. Publishing remains manual.</p>"
            f"<form method='post' action='/items/{encoded_id}/wordpress'><button type='submit'>Create WordPress draft</button></form>"
            f"<a class='text-link' href='/items/{encoded_id}'>Return to article review</a></section></aside></div>",
            active="drafts",
        )

    @app.post("/items/{content_item_id}/wordpress")
    async def create_wordpress_draft(content_item_id: str) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        try:
            delivery = store.begin_wordpress_delivery(content_item_id, draft.draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if delivery.status.value == "draft_created":
            return RedirectResponse(
                f"/items/{quote(content_item_id, safe='')}", status_code=303
            )
        try:
            result = await publisher_factory().create_draft(draft)
        except ValueError as exc:
            store.fail_wordpress_delivery(delivery.delivery_id, str(exc))
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            store.fail_wordpress_delivery(
                delivery.delivery_id,
                "WordPress delivery failed. Check the connection and credentials, then retry.",
            )
            raise HTTPException(
                status_code=502,
                detail="WordPress delivery failed; no successful draft was recorded.",
            ) from exc
        store.complete_wordpress_delivery(
            delivery.delivery_id,
            result.post_id,
            result.post_url,
            result.editor_url,
        )
        return RedirectResponse(f"/items/{quote(content_item_id, safe='')}", status_code=303)

    @app.post("/discord/interactions")
    async def discord_interactions(incoming: Request) -> JSONResponse:
        body = await incoming.body()
        try:
            config = DiscordConfig.from_env()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not verify_discord_signature(
            config.public_key,
            incoming.headers.get("x-signature-ed25519", ""),
            incoming.headers.get("x-signature-timestamp", ""),
            body,
        ):
            raise HTTPException(status_code=401, detail="Invalid Discord signature")
        payload = await incoming.json()
        interaction_type = payload.get("type")
        if interaction_type == 1:
            return JSONResponse({"type": 1})
        actor = payload.get("member", {}).get("user") or payload.get("user", {})
        actor_id = str(actor.get("id", "unknown"))
        actor_name = str(actor.get("global_name") or actor.get("username") or "Discord editor")
        custom_id = str(payload.get("data", {}).get("custom_id", ""))
        if interaction_type == 3 and custom_id.startswith("cq:revise:"):
            return JSONResponse(revision_modal(custom_id.removeprefix("cq:revise:")))
        bridge = bridge_factory()
        if interaction_type == 3 and custom_id.startswith("cq:approve:"):
            request_id = custom_id.removeprefix("cq:approve:")
            try:
                resolved = store.resolve_discord_request(
                    request_id,
                    DiscordApprovalStatus.ENDORSED,
                    actor_id,
                    actor_name,
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Approval request not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            return JSONResponse(
                {
                    "type": 7,
                    "data": {
                        "content": f"👍 Looks good to {actor_name}. Advisory only—final approval remains in CodeQuest.",
                        "embeds": payload.get("message", {}).get("embeds", []),
                        "components": [],
                        "allowed_mentions": {"parse": []},
                    },
                }
            )
        if interaction_type == 5 and custom_id.startswith("cq:revision_modal:"):
            request_id = custom_id.removeprefix("cq:revision_modal:")
            notes = modal_value(payload, "revision_notes")
            try:
                resolved = store.resolve_discord_request(
                    request_id,
                    DiscordApprovalStatus.REVISION_SUGGESTED,
                    actor_id,
                    actor_name,
                    notes,
                )
                await bridge.close_request_message(
                    resolved,
                    f"📝 Changes suggested by {actor_name}: {notes}",
                )
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Approval request not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Exception:
                pass
            return JSONResponse(
                {
                    "type": 4,
                    "data": {
                        "content": "Suggestions were returned to the CodeQuest workspace for the editor to consider.",
                        "flags": 64,
                        "allowed_mentions": {"parse": []},
                    },
                }
            )
        raise HTTPException(status_code=400, detail="Unsupported Discord interaction")

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
