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
    ArticleDraft,
    ArticleType,
    DecisionOutcome,
    DiscordApprovalRequest,
    DiscordApprovalStatus,
    DraftDecision,
    DraftParagraph,
    DraftSection,
    PreferenceSignal,
    SocialPlatform,
    SocialPostDraft,
    SocialPostStatus,
)
from .preferences import build_preference_profile
from .quality import evaluate_draft
from .social import (
    PLATFORM_LIMITS,
    SocialCampaignGenerator,
    SocialGenerationError,
    create_ollama_cloud_social_generator,
    validate_social_post,
)
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
select,input,textarea,button{width:100%;font:inherit;border:1px solid #d9dde3;border-radius:12px;padding:11px 13px}
select,input,textarea{color:var(--ink);background:#fff}textarea{min-height:108px;resize:vertical}button{background:var(--ink);
color:#fff;font-weight:750;cursor:pointer;border-color:var(--ink);border-radius:999px}button:hover{filter:brightness(1.08)}
button:disabled{opacity:.45;cursor:not-allowed}.button-approve{background:var(--accent);border-color:var(--accent)}
.button-revise{background:#fff;color:var(--ink);border-color:#cfd4dc}.feedback{border-top:1px solid var(--line);padding:14px 0}
.rule{border-left:3px solid var(--success);padding-left:13px}.rule.avoid{border-color:var(--accent)}pre{white-space:pre-wrap;
font:12px/1.6 ui-monospace,SFMono-Regular,monospace;color:var(--muted);background:#f8f9fa;border-radius:12px;padding:14px}
.panel.draft{padding:32px}.draft h2{font-size:30px;line-height:1.15}.draft .dek{font-size:17px}.quality-check{display:grid;
grid-template-columns:auto 1fr;gap:10px;padding:12px 0;border-top:1px solid var(--line)}.quality-check:first-of-type{border-top:0}
.quality-check p{margin:0}.quality-check small{display:block;color:var(--muted)}.decision{background:var(--accent-soft);
border:1px solid #ffd2b6;border-radius:14px;padding:14px}.empty{text-align:center;padding:76px 24px;background:#fff}
@media(max-width:820px){.layout{grid-template-columns:1fr}.nav{gap:12px}.horizon-chip{display:none}h1{font-size:35px}
.shell{padding-top:32px}}@media(max-width:560px){.top{padding:0 16px}.nav a{font-size:13px}.shell{padding-left:16px;
padding-right:16px}.panel,.card{padding:19px}.panel.draft{padding:22px}h1{font-size:31px}}
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
.draft-toolbar{display:flex;justify-content:flex-end;margin-bottom:10px}.draft-toolbar a{display:inline-flex;width:auto;padding:8px 14px;border:1px solid var(--line);
border-radius:999px;text-decoration:none;font-weight:750;background:#fff}.editor-section{display:grid;gap:12px}.editor-paragraph{padding:16px;border:1px solid var(--line);
border-radius:12px;background:#fafafa}.field-label{display:block;font-size:12px;font-weight:800;color:#565d68;margin-bottom:5px}.version{padding:11px 0;border-top:1px solid var(--line)}
.version:first-of-type{border-top:0}.version code{font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.story-tabs{display:flex;gap:6px;padding:6px;background:#eceef1;border-radius:13px;margin:0 0 24px;overflow:auto}.story-tab{display:flex;align-items:center;
gap:7px;white-space:nowrap;padding:9px 15px;border-radius:9px;text-decoration:none;color:#5f6671;font-weight:750}.story-tab:hover{background:rgba(255,255,255,.65)}
.story-tab.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(17,24,39,.08)}.story-tab .count{font-size:10px;padding:1px 6px;border-radius:999px;background:#e8e9ec;color:#737985}
.story-tab.active .count{background:var(--accent-soft);color:#c75b17}.tab-intro{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:18px}.tab-intro p{margin:0}
.subtabs{display:flex;gap:18px;border-bottom:1px solid var(--line);margin:-2px 0 20px}.subtab{padding:9px 2px 11px;text-decoration:none;color:var(--muted);font-weight:750;border-bottom:2px solid transparent}
.subtab.active{color:var(--ink);border-bottom-color:var(--accent)}.social-list{display:grid;gap:16px}.social-card textarea{min-height:142px}.social-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:10px}
.social-head .badge.draft{padding:4px 9px;background:#f3f4f6}.social-meta{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:12px;margin:8px 0 14px}.inline-actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}.buffer-lock{border-style:dashed}
@media(max-width:980px){.stat-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:820px){.sidebar{position:static;width:auto;padding:12px}.workspace{padding-bottom:12px;margin-bottom:8px}.workspace small,.nav-label,.sidebar-foot{display:none}
.side-nav{display:flex;overflow:auto}.nav-item{white-space:nowrap}.content{margin-left:0}.shell{padding:30px 18px 70px}.story-tabs{border-radius:10px}.story-tab{padding:8px 12px}}
@media(max-width:480px){.stat-grid{grid-template-columns:1fr 1fr}.nav-item{font-size:13px;padding:9px}.nav-icon{display:none}}
"""

_ICONS = {
    "overview": "<svg viewBox='0 0 24 24'><rect x='3' y='3' width='7' height='7' rx='1'/><rect x='14' y='3' width='7' height='7' rx='1'/><rect x='3' y='14' width='7' height='7' rx='1'/><rect x='14' y='14' width='7' height='7' rx='1'/></svg>",
    "editorial": "<svg viewBox='0 0 24 24'><path d='M4 5h16v14H4z'/><path d='M8 9h8M8 13h8M8 17h5'/></svg>",
    "drafts": "<svg viewBox='0 0 24 24'><path d='M6 3h9l4 4v14H6z'/><path d='M14 3v5h5M9 12h6M9 16h6'/></svg>",
    "memory": "<svg viewBox='0 0 24 24'><path d='M12 3a4 4 0 0 0-4 4v1a4 4 0 0 0 0 8v1a4 4 0 0 0 4 4'/><path d='M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1 0 8v1a4 4 0 0 1-4 4M12 3v18'/></svg>",
}

_SOCIAL_LABELS = {
    SocialPlatform.LINKEDIN: "LinkedIn",
    SocialPlatform.X: "X",
    SocialPlatform.FACEBOOK: "Facebook",
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


def _draft_html(draft, edit_href: str | None = None) -> str:
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
    toolbar = (
        f"<div class='draft-toolbar'><a href='{escape(edit_href, quote=True)}'>Edit this version</a></div>"
        if edit_href
        else ""
    )
    lineage = (
        f" · edited from {escape(draft.parent_draft_id)}"
        if draft.parent_draft_id
        else ""
    )
    return (
        f"<article class='panel draft'>{toolbar}<p class='eyebrow'>UNPUBLISHED REVIEW DRAFT</p>"
        f"<h2>{escape(draft.title)}</h2><p class='muted dek'>{escape(draft.dek)}</p>"
        f"{''.join(sections)}<small class='muted'>Generated with {escape(draft.generator_model)} · "
        f"{escape(draft.prompt_version)} · {escape(draft.created_at.isoformat())}{lineage}</small></article>"
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
    social_generator_factory: Callable[[], SocialCampaignGenerator] | None = None,
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
    social_writer_factory = social_generator_factory or create_ollama_cloud_social_generator

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
    def detail(
        content_item_id: str, tab: str = "overview", channel: str = "wordpress"
    ) -> HTMLResponse:
        allowed_tabs = {"overview", "editor", "review", "delivery", "learning"}
        if tab not in allowed_tabs:
            raise HTTPException(status_code=404, detail="Story tab not found")
        if channel not in {"wordpress", "social"}:
            raise HTTPException(status_code=404, detail="Delivery channel not found")
        record = store.get_item(content_item_id)
        if not record:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        packet = record.packet
        brief = packet.brief
        feedback = store.list_feedback(content_item_id)
        latest_draft = store.get_latest_draft(content_item_id)
        draft_versions = store.list_drafts(content_item_id)
        discord_request = store.get_latest_discord_request(content_item_id)
        wordpress_delivery = (
            store.get_wordpress_delivery(latest_draft.draft_id) if latest_draft else None
        )
        social_campaign = (
            store.get_social_campaign(latest_draft.draft_id) if latest_draft else None
        )
        social_posts = (
            store.list_latest_social_posts(social_campaign.campaign_id)
            if social_campaign
            else []
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
        can_edit_draft = bool(
            latest_draft
            and not (
                wordpress_delivery
                and wordpress_delivery.status.value == "draft_created"
            )
        )
        edit_href = f"/items/{encoded_id}/edit" if can_edit_draft else None
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
            if latest_draft and latest_draft.parent_draft_id and decision_for_latest is None:
                review_controls += (
                    "<p class='muted'>This edited version requires a fresh editorial decision.</p>"
                )
        approval_actions = ""
        if latest_draft and quality_report and record.status != "approved" and not (
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
        quality_panel = (
            "<section class='panel'><h2>Quality gate</h2>"
            f"{_quality_html(quality_report)}</section>"
            if quality_report
            else "<section class='panel'><h2>Quality gate</h2><p class='muted'>Generate a draft to run deterministic checks.</p></section>"
        )
        decision_panel = (
            "<section class='panel'><h2>Editorial decision</h2>"
            f"{_decision_html(decision_for_latest)}{approval_actions}</section>"
            if latest_draft
            else "<section class='panel'><h2>Editorial decision</h2><p class='muted'>A decision becomes available after drafting.</p></section>"
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
        elif latest_draft and record.status in {"selected", "ready_for_approval", "approved"}:
            discord_controls = (
                "<p class='muted'>Optionally share this version for a quick team signal. Discord cannot approve it.</p>"
                f"<form method='post' action='/items/{encoded_id}/discord'><button type='submit'>Share with Discord</button></form>"
            )
        else:
            discord_controls = (
                "<p class='muted'>A reviewed draft is required before it can be shared with Discord.</p>"
            )
        discord_panel = (
            "<section class='panel discord-panel'><span class='badge discord-badge'>Discord · optional</span>"
            f"<h2>Team feedback</h2>{discord_controls}</section>"
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
        else:
            publishing_panel = (
                "<section class='panel'><span class='badge warning'>WordPress · locked</span>"
                "<h2>Draft delivery</h2><p class='muted'>Approve the latest article version in the Review tab before creating a WordPress draft.</p></section>"
            )
        if social_campaign:
            social_cards = []
            for post in sorted(social_posts, key=lambda item: item.platform.value):
                source_value = ", ".join(post.source_ids)
                limit = PLATFORM_LIMITS[post.platform]
                social_cards.append(
                    "<article class='panel social-card'>"
                    f"<div class='social-head'><h2>{escape(_SOCIAL_LABELS[post.platform])}</h2>"
                    f"<span class='badge {escape(post.status.value)}'>{escape(post.status.value.replace('_', ' '))}</span></div>"
                    f"<div class='social-meta'><span>Version {post.version}</span>"
                    f"<span>{len(post.body)} / {limit} characters</span></div>"
                    f"<form method='post' action='/items/{encoded_id}/social/{post.platform.value}/edit'>"
                    f"<input type='hidden' name='base_post_id' value='{escape(post.post_id, quote=True)}'>"
                    f"<textarea name='body' required maxlength='{limit}'>{escape(post.body)}</textarea>"
                    "<label class='field-label'>Supporting evidence IDs</label>"
                    f"<input name='source_ids' required value='{escape(source_value, quote=True)}'>"
                    "<input name='edit_note' required placeholder='What changed in this version?'>"
                    "<button class='button-revise' type='submit'>Save new version</button></form>"
                    f"<form class='inline-actions' method='post' action='/items/{encoded_id}/social/{escape(post.post_id, quote=True)}/decision'>"
                    "<button class='button-approve' name='status' value='approved' type='submit'>Approve copy</button>"
                    "<button class='button-revise' name='status' value='needs_revision' type='submit'>Needs revision</button></form>"
                    "</article>"
                )
            approved_count = sum(
                post.status == SocialPostStatus.APPROVED for post in social_posts
            )
            social_preferences = store.list_social_preferences()
            learned_rules = "".join(
                f"<div class='version'><strong>{escape(_SOCIAL_LABELS[SocialPlatform(platform)])}</strong> · "
                f"{escape(instruction)}</div>"
                for platform, instructions in social_preferences.items()
                for instruction in instructions
            ) or "<p class='muted'>No social preferences recorded yet.</p>"
            platform_options = "".join(
                f"<option value='{platform.value}'>{escape(_SOCIAL_LABELS[platform])}</option>"
                for platform in SocialPlatform
            )
            social_panel = (
                "<section class='social-list'>"
                f"{''.join(social_cards)}</section>"
                "<section class='panel buffer-lock'><span class='badge warning'>Buffer · disabled</span>"
                f"<h2>{approved_count} of 3 posts approved</h2>"
                "<p class='muted'>Scheduling stays locked until a later delivery increment adds a public article URL, payload preview, and an explicit send action.</p></section>"
                "<div class='layout'><section class='panel'><h2>Social memory</h2>"
                f"{learned_rules}</section><aside class='panel'><h2>Teach future campaigns</h2>"
                f"<form method='post' action='/items/{encoded_id}/social/feedback'>"
                f"<select name='platform'>{platform_options}</select>"
                "<select name='signal'><option value='prefer'>Prefer</option><option value='avoid'>Avoid</option></select>"
                "<textarea name='note' required placeholder='For example: Keep LinkedIn openings practical and specific.'></textarea>"
                "<button type='submit'>Save social preference</button></form></aside></div>"
            )
        elif latest_draft and record.status == "approved":
            social_panel = (
                "<section class='panel'><span class='badge selected'>Social campaign</span>"
                "<h2>Create platform-specific drafts</h2>"
                "<p class='muted'>Generate distinct LinkedIn, X, and Facebook copy from this exact approved article. Nothing will be sent to Buffer.</p>"
                f"<form method='post' action='/items/{encoded_id}/social'><button type='submit'>Generate social campaign</button></form></section>"
            )
        else:
            social_panel = (
                "<section class='panel'><span class='badge warning'>Social · locked</span>"
                "<h2>Campaign drafting</h2><p class='muted'>Approve the latest article version before creating social copy.</p></section>"
            )
        delivery_tabs = (
            "<nav class='subtabs' aria-label='Delivery channels'>"
            f"<a class='subtab{' active' if channel == 'wordpress' else ''}' href='/items/{encoded_id}?tab=delivery&channel=wordpress'>WordPress</a>"
            f"<a class='subtab{' active' if channel == 'social' else ''}' href='/items/{encoded_id}?tab=delivery&channel=social'>Social campaign</a></nav>"
        )
        delivery_body = (
            f"<div class='layout'><section class='stack'>{publishing_panel}</section>"
            f"<aside class='stack'>{discord_panel}</aside></div>"
            if channel == "wordpress"
            else social_panel
        )
        versions_html = "".join(
            "<div class='version'>"
            + (
                "<span class='badge selected'>Latest</span> "
                if draft.draft_id == latest_draft.draft_id
                else ""
            )
            + f"<strong>{escape(draft.title)}</strong><br><code>{escape(draft.draft_id)}</code>"
            + (
                f"<br><small class='muted'>Edited from {escape(draft.parent_draft_id)}</small>"
                if draft.parent_draft_id
                else "<br><small class='muted'>Generated version</small>"
            )
            + "</div>"
            for draft in draft_versions
        ) or "<p class='muted'>No versions yet.</p>"
        versions_panel = (
            f"<section class='panel'><h2>Version history</h2>{versions_html}</section>"
            if latest_draft
            else ""
        )
        add_feedback_panel = (
            "<section class='panel'><h2>Add feedback</h2>"
            f"<form method='post' action='/items/{encoded_id}/feedback'>"
            f"<select name='signal'>{signal_options}</select>"
            f"<select name='dimension'>{dimension_options}</select>"
            f"<select name='scope'>{scope_options}</select>"
            "<textarea name='note' required placeholder='What should the system learn or change?'></textarea>"
            "<button type='submit'>Save feedback</button></form></section>"
        )
        profile_panel = (
            f"<section class='panel'><h2>Effective writing profile</h2>{_rules_html(preference_profile)}"
            f"<pre>{escape(preference_profile.writer_instructions())}</pre></section>"
        )
        feedback_history_panel = (
            f"<section class='panel'><h2>Feedback history</h2>{feedback_html}</section>"
        )
        tab_bodies = {
            "overview": (
                "<div class='layout'><section class='stack'>"
                f"<article class='panel'><h2>Central angle</h2><p>{escape(brief.central_angle)}</p>"
                f"<h2>Audience value</h2><p>{escape(brief.audience_value)}</p></article>"
                f"<article class='panel'><h2>Required facts</h2><ul class='facts'>{facts}</ul>"
                f"<h2>Research gaps</h2><ul class='questions'>{questions}</ul></article></section>"
                f"<aside class='stack'><article class='panel'><h2>Evidence</h2>{sources}</article></aside></div>"
            ),
            "editor": (
                "<div class='layout'><section class='stack'>"
                f"{_draft_html(latest_draft, edit_href)}</section><aside class='stack'>{versions_panel}</aside></div>"
            ),
            "review": (
                f"<div class='layout'><section class='stack'>{quality_panel}</section>"
                "<aside class='stack'><section class='panel'><h2>Review state</h2>"
                f"{review_controls}</section>{decision_panel}</aside></div>"
            ),
            "delivery": (
                f"{delivery_tabs}{delivery_body}"
            ),
            "learning": (
                f"<div class='layout'><section class='stack'>{profile_panel}{feedback_history_panel}</section>"
                f"<aside class='stack'>{add_feedback_panel}</aside></div>"
            ),
        }
        tab_labels = (
            ("overview", "Overview", ""),
            ("editor", "Editor", str(len(draft_versions)) if draft_versions else ""),
            ("review", "Review", ""),
            (
                "delivery",
                "Delivery",
                str((1 if wordpress_delivery else 0) + (1 if social_campaign else 0))
                if wordpress_delivery or social_campaign
                else "",
            ),
            ("learning", "Learning", str(len(feedback)) if feedback else ""),
        )
        tabs_html = "".join(
            f"<a class='story-tab{' active' if key == tab else ''}' href='/items/{encoded_id}?tab={key}'>"
            f"{label}{f'<span class=count>{count}</span>' if count else ''}</a>"
            for key, label, count in tab_labels
        )
        descriptions = {
            "overview": "Assignment, required facts, research gaps, and saved evidence.",
            "editor": "Read or edit the latest article while preserving every version.",
            "review": "Quality checks, workflow state, and the authoritative editorial decision.",
            "delivery": "Prepare website and social delivery without mixing their review steps.",
            "learning": "Story feedback and the writing preferences learned from it.",
        }
        return _page(
            brief.working_title,
            "<div class='crumb'><a href='/editorial'>Editorial queue</a><span>›</span>"
            f"<span>{escape(brief.working_title)}</span></div>"
            f"<header class='page-head'><p class='eyebrow'>{escape(brief.article_type.value.replace('_', ' ').upper())}</p>"
            f"<h1>{escape(brief.working_title)}</h1><div class='meta'>"
            f"<span class='badge {escape(record.status)}'>{escape(record.status)}</span>"
            f"<span>{len(packet.evidence.sources)} evidence source(s)</span>"
            f"<span>·</span><span>{len(draft_versions)} draft version(s)</span></div></header>"
            f"<nav class='story-tabs' aria-label='Story workspace'>{tabs_html}</nav>"
            f"<div class='tab-intro'><div><p class='eyebrow'>{tab.upper()}</p>"
            f"<p class='muted'>{escape(descriptions[tab])}</p></div></div>"
            f"{tab_bodies[tab]}",
            active="editorial",
        )

    @app.get("/items/{content_item_id}/edit", response_class=HTMLResponse)
    def edit_draft(content_item_id: str) -> HTMLResponse:
        record = store.get_item(content_item_id)
        draft = store.get_latest_draft(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if draft is None:
            raise HTTPException(status_code=409, detail="Generate a draft before editing it.")
        delivery = store.get_wordpress_delivery(draft.draft_id)
        if delivery and delivery.status.value == "draft_created":
            raise HTTPException(
                status_code=409,
                detail="This version already has a WordPress draft and can no longer be edited here.",
            )
        encoded_id = quote(content_item_id, safe="")
        section_fields = []
        for section_index, section in enumerate(draft.sections):
            paragraphs = []
            for paragraph in section.paragraphs:
                paragraphs.append(
                    "<div class='editor-paragraph'>"
                    f"<input type='hidden' name='paragraph_section' value='{section_index}'>"
                    "<label class='field-label'>Paragraph</label>"
                    f"<textarea name='paragraph_text' required>{escape(paragraph.text)}</textarea>"
                    "<label class='field-label'>Evidence IDs</label>"
                    f"<input name='paragraph_sources' required value='{escape(', '.join(paragraph.source_ids), quote=True)}'>"
                    "</div>"
                )
            section_fields.append(
                "<section class='panel editor-section'>"
                f"<p class='eyebrow'>SECTION {section_index + 1}</p>"
                "<label class='field-label'>Section heading</label>"
                f"<input name='section_heading' required value='{escape(section.heading, quote=True)}'>"
                f"{''.join(paragraphs)}</section>"
            )
        source_reference = "".join(
            f"<div class='version'><strong>{escape(source_id)}</strong>"
            f"<br><a class='text-link' href='{escape(str(source_url), quote=True)}' target='_blank' rel='noopener'>{escape(str(source_url))}</a></div>"
            for source_id, source_url in draft.source_map.items()
        )
        return _page(
            f"Edit · {draft.title}",
            "<div class='crumb'><a href='/drafts'>Draft library</a><span>›</span>"
            f"<a href='/items/{encoded_id}'>Article review</a><span>›</span><span>Edit draft</span></div>"
            "<header class='page-head'><p class='eyebrow'>NEW VERSION</p>"
            "<h1>Edit the article, preserve the <span class='accent'>history.</span></h1>"
            "<p class='muted'>Saving creates a new draft version and requires a fresh editorial approval.</p></header>"
            "<div class='layout'><form class='stack' method='post' "
            f"action='/items/{encoded_id}/edit'>"
            f"<input type='hidden' name='base_draft_id' value='{escape(draft.draft_id, quote=True)}'>"
            "<section class='panel editor-section'><label class='field-label'>Headline</label>"
            f"<input name='title' required value='{escape(draft.title, quote=True)}'>"
            "<label class='field-label'>Summary</label>"
            f"<textarea name='dek' required>{escape(draft.dek)}</textarea></section>"
            f"{''.join(section_fields)}"
            "<section class='panel'><label class='field-label'>Edit note</label>"
            "<textarea name='edit_note' required placeholder='Briefly record what changed and why.'></textarea>"
            "<button class='button-approve' type='submit'>Save as new version</button></section></form>"
            "<aside class='stack'><section class='panel'><h2>What happens next</h2>"
            "<p class='muted'>The previous version remains available. Any previous approval is invalidated for this new version, and quality checks run again automatically.</p></section>"
            f"<section class='panel'><h2>Evidence reference</h2>{source_reference}</section></aside></div>",
            active="drafts",
        )

    @app.post("/items/{content_item_id}/edit")
    def save_edited_draft(
        content_item_id: str,
        base_draft_id: str = Form(),
        title: str = Form(),
        dek: str = Form(),
        section_heading: list[str] = Form(),
        paragraph_section: list[int] = Form(),
        paragraph_text: list[str] = Form(),
        paragraph_sources: list[str] = Form(),
        edit_note: str = Form(),
    ) -> RedirectResponse:
        record = store.get_item(content_item_id)
        base = store.get_latest_draft(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if base is None:
            raise HTTPException(status_code=409, detail="Generate a draft before editing it.")
        if base.draft_id != base_draft_id:
            raise HTTPException(
                status_code=409,
                detail="The draft changed while it was being edited. Reload and try again.",
            )
        cleaned_title = title.strip()
        cleaned_dek = dek.strip()
        cleaned_note = edit_note.strip()
        if not cleaned_title or not cleaned_dek or not cleaned_note:
            raise HTTPException(
                status_code=400,
                detail="Headline, summary, and edit note are required.",
            )
        if not section_heading or not (
            len(paragraph_section) == len(paragraph_text) == len(paragraph_sources)
        ):
            raise HTTPException(status_code=400, detail="Invalid article section data.")
        grouped: list[list[DraftParagraph]] = [[] for _ in section_heading]
        known_sources = set(base.source_map)
        for section_index, text, source_text in zip(
            paragraph_section, paragraph_text, paragraph_sources
        ):
            if section_index < 0 or section_index >= len(section_heading):
                raise HTTPException(status_code=400, detail="Invalid paragraph section.")
            cleaned_text = text.strip()
            source_ids = list(
                dict.fromkeys(source_text.replace(",", " ").split())
            )
            if not cleaned_text or not source_ids:
                raise HTTPException(
                    status_code=400,
                    detail="Every paragraph needs text and at least one evidence ID.",
                )
            unknown = [source_id for source_id in source_ids if source_id not in known_sources]
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail="Unknown evidence IDs: " + ", ".join(unknown),
                )
            grouped[section_index].append(
                DraftParagraph(text=cleaned_text, source_ids=source_ids)
            )
        sections = []
        for heading, paragraphs in zip(section_heading, grouped):
            cleaned_heading = heading.strip()
            if not cleaned_heading or not paragraphs:
                raise HTTPException(
                    status_code=400,
                    detail="Every section needs a heading and at least one paragraph.",
                )
            sections.append(DraftSection(heading=cleaned_heading, paragraphs=paragraphs))
        edited = ArticleDraft(
            content_item_id=content_item_id,
            title=cleaned_title,
            dek=cleaned_dek,
            sections=sections,
            source_map=base.source_map,
            preference_rules=base.preference_rules,
            revision_notes=base.revision_notes,
            parent_draft_id=base.draft_id,
            edit_note=cleaned_note,
            generator_model=base.generator_model,
            prompt_version=base.prompt_version,
        )
        try:
            store.save_edited_draft(edited, base.draft_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=editor", status_code=303
        )

    @app.post("/items/{content_item_id}/status")
    def update_status(content_item_id: str, status: str = Form()) -> RedirectResponse:
        try:
            store.set_status(content_item_id, status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=review", status_code=303
        )

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
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=editor", status_code=303
        )

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
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=review", status_code=303
        )

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
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=wordpress",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/social")
    async def generate_social_campaign(content_item_id: str) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        if store.get_social_campaign(draft.draft_id):
            raise HTTPException(
                status_code=409,
                detail="A social campaign already exists for this article version.",
            )
        try:
            campaign, posts = await social_writer_factory().generate(
                content_item_id, draft, store.list_social_preferences()
            )
            store.create_social_campaign(campaign, posts)
        except SocialGenerationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="Ollama Cloud social generation failed; no campaign was saved.",
            ) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/social/{platform}/edit")
    def edit_social_post(
        content_item_id: str,
        platform: str,
        base_post_id: str = Form(),
        body: str = Form(),
        source_ids: str = Form(),
        edit_note: str = Form(),
    ) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        try:
            selected_platform = SocialPlatform(platform)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Social platform not found") from exc
        campaign = store.get_social_campaign(draft.draft_id)
        if campaign is None:
            raise HTTPException(status_code=409, detail="Generate a social campaign first.")
        versions = store.list_social_post_versions(campaign.campaign_id, selected_platform)
        latest = versions[0] if versions else None
        if latest is None or latest.post_id != base_post_id:
            raise HTTPException(
                status_code=409,
                detail="The social draft changed while it was being edited. Reload and try again.",
            )
        cleaned_note = edit_note.strip()
        cleaned_body = body.strip()
        cleaned_sources = list(dict.fromkeys(source_ids.replace(",", " ").split()))
        if not cleaned_note:
            raise HTTPException(status_code=400, detail="An edit note is required.")
        if not cleaned_body or not cleaned_sources:
            raise HTTPException(
                status_code=400,
                detail="Social copy and at least one supporting evidence ID are required.",
            )
        edited = SocialPostDraft(
            campaign_id=campaign.campaign_id,
            content_item_id=content_item_id,
            article_draft_id=draft.draft_id,
            platform=selected_platform,
            body=cleaned_body,
            source_ids=cleaned_sources,
            version=latest.version + 1,
            parent_post_id=latest.post_id,
            edit_note=cleaned_note,
            generator_model=latest.generator_model,
            prompt_version=latest.prompt_version,
        )
        try:
            validate_social_post(edited, draft)
            store.save_edited_social_post(edited, latest.post_id)
        except SocialGenerationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/social/{post_id}/decision")
    def decide_social_post(
        content_item_id: str, post_id: str, status: str = Form()
    ) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        campaign = store.get_social_campaign(draft.draft_id)
        if campaign is None:
            raise HTTPException(status_code=409, detail="Generate a social campaign first.")
        latest_posts = store.list_latest_social_posts(campaign.campaign_id)
        post = next((item for item in latest_posts if item.post_id == post_id), None)
        if post is None:
            raise HTTPException(
                status_code=409,
                detail="Only the latest social draft can receive a review decision.",
            )
        try:
            review_status = SocialPostStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unsupported social decision.") from exc
        if review_status not in {
            SocialPostStatus.APPROVED,
            SocialPostStatus.NEEDS_REVISION,
        }:
            raise HTTPException(status_code=400, detail="Unsupported social decision.")
        store.set_social_post_status(post_id, review_status)
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/social/feedback")
    def add_social_feedback(
        content_item_id: str,
        platform: str = Form(),
        signal: str = Form(),
        note: str = Form(),
    ) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        campaign = store.get_social_campaign(draft.draft_id)
        if campaign is None:
            raise HTTPException(status_code=409, detail="Generate a social campaign first.")
        try:
            store.add_social_feedback(
                content_item_id,
                campaign.campaign_id,
                SocialPlatform(platform),
                PreferenceSignal(signal),
                note,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

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
            f"<a class='text-link' href='/items/{encoded_id}?tab=delivery'>Return to delivery</a></section></aside></div>",
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
                f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=wordpress",
                status_code=303,
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
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=wordpress",
            status_code=303,
        )

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
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=learning", status_code=303
        )

    return app
