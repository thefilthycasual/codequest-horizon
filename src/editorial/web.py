"""Lightweight browser workspace for CodeQuest editorial review."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from html import escape
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .automation import (
    AutomationConfig,
    EditorialAutomationRunner,
    create_automation_runner,
)
from .discord import (
    DiscordApprovalBridge,
    DiscordConfig,
    modal_value,
    revision_modal,
    verify_discord_signature,
)
from .buffer import (
    BufferConfig,
    BufferDeliveryRejected,
    BufferDeliveryUncertain,
    BufferPublisher,
    build_buffer_payload,
    build_buffer_text,
    normalize_public_article_url,
    parse_scheduled_time,
)

from .drafting import (
    ArticleDraftGenerator,
    DraftGenerationError,
    create_ollama_cloud_draft_generator,
)
from .image_generation import (
    ImageGenerationConfig,
    ImageGenerationError,
    ImageGenerator,
    OpenAIImageGenerator,
    SUPPORTED_IMAGE_QUALITIES,
    SUPPORTED_IMAGE_SIZES,
    SUPPORTED_IMAGE_STYLES,
    build_featured_image_prompt,
)
from .integrations import IntegrationState, integration_inventory
from .models import (
    ArticleDraft,
    ArticleType,
    BrandProfile,
    BrandConnectionProfile,
    BrandRule,
    BrandRuleChannel,
    BufferDeliveryMode,
    BufferDeliveryStatus,
    DecisionOutcome,
    ConnectionProvider,
    DiscordApprovalRequest,
    DiscordApprovalStatus,
    DraftDecision,
    DraftParagraph,
    DraftSection,
    GeneratedImageAsset,
    Organization,
    PreferenceSignal,
    SocialPlatform,
    SocialPostDraft,
    SocialPostStatus,
    WordPressDeliveryStatus,
    WordPressPublishingSettings,
    VisualBrandProfile,
    VisualPreferenceFeedback,
)
from .preferences import build_preference_profile
from .quality import evaluate_draft, pending_required_facts
from .social import (
    PLATFORM_LIMITS,
    SocialCampaignGenerator,
    SocialGenerationError,
    create_ollama_cloud_social_generator,
    validate_social_post,
)
from .source_control import ConfigError, SourceControlService
from .secret_vault import SecretVaultError, WorkspaceSecretVault
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
    validate_image_upload,
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
minmax(310px,.75fr);gap:20px;align-items:start}.layout>*,.stack>*{min-width:0}.stack{display:grid;gap:20px}.panel{box-shadow:0 1px 2px rgba(17,24,39,.02)}
.source{padding:18px 0;border-top:1px solid var(--line)}.source:first-of-type{border-top:0}.source a{color:#c75b17;
overflow-wrap:anywhere}.facts li,.questions li{margin:7px 0}form{display:grid;gap:10px}form+form{margin-top:14px}
select,input,textarea,button{width:100%;font:inherit;border:1px solid #d9dde3;border-radius:12px;padding:11px 13px}
select,input,textarea{color:var(--ink);background:#fff}textarea{min-height:108px;resize:vertical}button{background:var(--ink);
color:#fff;font-weight:750;cursor:pointer;border-color:var(--ink);border-radius:999px}button:hover{filter:brightness(1.08)}
button:disabled{opacity:.45;cursor:not-allowed}.button-approve{background:var(--accent);border-color:var(--accent)}
.button-revise{background:#fff;color:var(--ink);border-color:#cfd4dc}.feedback{border-top:1px solid var(--line);padding:14px 0}
.rule{border-left:3px solid var(--success);padding-left:13px}.rule.avoid{border-color:var(--accent)}pre{max-width:100%;overflow-x:auto;white-space:pre-wrap;overflow-wrap:anywhere;
font:12px/1.6 ui-monospace,SFMono-Regular,monospace;color:var(--muted);background:#f8f9fa;border-radius:12px;padding:14px}
.panel.draft{padding:32px}.draft h2{font-size:30px;line-height:1.15}.draft .dek{font-size:17px}.quality-check{display:grid;
grid-template-columns:auto 1fr;gap:10px;padding:12px 0;border-top:1px solid var(--line)}.quality-check:first-of-type{border-top:0}
.quality-check p{margin:0}.quality-check small{display:block;color:var(--muted)}.decision{background:var(--accent-soft);
border:1px solid #ffd2b6;border-radius:14px;padding:14px}.empty{text-align:center;padding:76px 24px;background:#fff}
.fact-list{display:grid;gap:10px;margin:16px 0}.fact-option{display:grid;grid-template-columns:auto 1fr;gap:10px;
align-items:start;padding:13px;border:1px solid var(--line);border-radius:12px;background:#fafafa}.fact-option input{width:auto;margin-top:5px}
.evidence-links{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 18px}.evidence-links a{color:#c75b17;font-weight:700}
@media(max-width:820px){.layout{grid-template-columns:1fr}.nav{gap:12px}.horizon-chip{display:none}h1{font-size:35px}
.shell{padding-top:32px}}@media(max-width:560px){.top{padding:0 16px}.nav a{font-size:13px}.shell{padding-left:16px;
padding-right:16px}.panel,.card{padding:19px}.panel.draft{padding:22px}h1{font-size:31px}}
"""

_ADMIN_STYLE = """
:root{--sidebar:272px}.site-header{display:none}.sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar);
background:#fff;border-right:1px solid var(--line);padding:22px 16px;display:flex;flex-direction:column;z-index:20}
.workspace{display:flex;align-items:center;gap:12px;padding:10px 9px 22px;border-bottom:1px solid var(--line);margin-bottom:18px}
.workspace{text-decoration:none;color:inherit}.workspace:hover strong{color:var(--accent)}
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
.delivery-actions{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.delivery-actions .text-link{display:inline-block;margin-bottom:10px}.delivery-state{margin-top:18px;padding:14px;border-radius:12px;background:#f8f9fa}.payload-copy{font-size:16px;white-space:pre-wrap;overflow-wrap:anywhere}.payload-table{display:grid;gap:8px}.payload-row{display:flex;justify-content:space-between;gap:20px;min-width:0;border-top:1px solid var(--line);padding-top:8px}.payload-row code{min-width:0;overflow-wrap:anywhere;word-break:break-all;text-align:right}.panel .text-link{overflow-wrap:anywhere}
.run-list{display:grid;gap:10px}.run-row{display:grid;grid-template-columns:minmax(150px,.8fr) minmax(210px,1.4fr) auto;gap:18px;align-items:center;padding:15px 0;border-top:1px solid var(--line)}.run-row:first-child{border-top:0}.run-counts{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:12px}.badge.completed{color:var(--success);background:#eefaf5;border-color:#cdebdc}.badge.running{color:#1d4ed8;background:#eff6ff;border-color:#bfdbfe}.badge.partial{color:var(--warning);background:#fff7ed;border-color:#fed7aa}.badge.failed{color:var(--danger);background:#fef2f2;border-color:#fecaca}
.run-row{text-decoration:none;border-radius:10px;padding-left:10px;padding-right:10px}.run-row:hover,.run-row.active{background:#f7f7f8}.run-detail{margin:0 0 24px}.run-detail-head{display:flex;justify-content:space-between;gap:16px;align-items:start}.run-detail h2{margin-top:8px}.run-stories{display:grid;gap:8px;margin-top:16px}.run-story{padding:10px 12px;border:1px solid var(--line);border-radius:10px;text-decoration:none;font-weight:700}.run-story:hover{border-color:#f2c3a3}.queue-tools{display:grid;grid-template-columns:1fr;gap:12px;margin-bottom:14px}.queue-tools form{display:grid;grid-template-columns:1fr auto;gap:8px;width:min(100%,420px);justify-self:end}.queue-tools button{width:auto;padding-left:22px;padding-right:22px}.filter-tabs{display:flex;gap:7px;overflow:auto;padding-bottom:3px}.filter-tab{white-space:nowrap;text-decoration:none;padding:8px 12px;border:1px solid var(--line);border-radius:999px;background:#fff;color:var(--muted);font-weight:700}.filter-tab.active{background:var(--ink);border-color:var(--ink);color:#fff}.queue-list{display:grid}.queue-row{display:grid;grid-template-columns:minmax(0,1fr) minmax(160px,.25fr);gap:22px;padding:22px 4px;border-top:1px solid var(--line);align-items:center}.queue-row:first-child{border-top:0}.queue-row h2{margin:8px 0 6px}.queue-row h2 a{text-decoration:none}.queue-row h2 a:hover{color:#c75b17}.queue-row p{margin:0}.queue-summary{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.queue-side{text-align:right}.queue-side .meta{justify-content:flex-end;margin-top:0}.queue-side form{margin-top:10px}.queue-side .text-link{display:inline-block;margin-top:10px}.compact-button{padding:8px 14px;font-size:13px}
.brain-tabs{display:flex;gap:6px;padding:6px;background:#eceef1;border-radius:13px;margin-bottom:24px;overflow:auto}.brain-tab{white-space:nowrap;padding:9px 15px;border-radius:9px;text-decoration:none;color:#5f6671;font-weight:750}.brain-tab.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(17,24,39,.08)}.profile-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.profile-grid .span-2{grid-column:1/-1}.profile-grid textarea{min-height:86px}.brain-rule-list{display:grid;gap:14px}.brain-rule{border:1px solid var(--line);border-radius:14px;padding:17px;background:#fff}.brain-rule.disabled{opacity:.62}.brain-rule-head{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}.brain-rule form{display:grid;grid-template-columns:150px 150px 100px;gap:10px}.brain-rule form .rule-instruction{grid-column:1/-1}.brain-rule form .rule-enabled{display:flex;align-items:center;gap:8px}.brain-rule form .rule-enabled input{width:auto}.brain-rule form button{grid-column:1/-1}.memory-preview{max-height:430px;overflow:auto}.learning-signal{padding:17px 0;border-top:1px solid var(--line)}.learning-signal:first-of-type{border-top:0}.learning-signal p{margin:8px 0}.promote-form{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:10px}.promote-form button{width:auto}.coverage-list{display:grid;gap:9px}.coverage-row{display:flex;justify-content:space-between;gap:12px;padding-top:9px;border-top:1px solid var(--line)}
.radar-tools{display:grid;grid-template-columns:minmax(220px,1fr) 180px 150px 170px auto;gap:10px;margin-bottom:20px}.radar-tools button{width:auto;padding-left:22px;padding-right:22px}.radar-list{display:grid;gap:12px}.radar-row{display:grid;grid-template-columns:86px minmax(0,1fr) 190px;gap:20px;align-items:center}.score-box{display:grid;place-items:center;align-content:center;min-height:82px;border-radius:15px;background:var(--ink);color:#fff}.score-box strong{font-size:27px;line-height:1}.score-box small{font-size:10px;line-height:1.25;text-align:center;text-transform:uppercase;letter-spacing:.05em;opacity:.68}.score-box.pending{background:#f0f1f3;color:var(--muted)}.radar-copy h2{margin:6px 0}.radar-copy h2 a{text-decoration:none}.radar-copy h2 a:hover{color:var(--accent)}.tags{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.tag{padding:3px 8px;border-radius:999px;background:var(--accent-soft);color:#b85618;font-size:11px;font-weight:750}.radar-side{text-align:right}.radar-side .meta{justify-content:flex-end}.intelligence-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.signal-card{padding:17px;border:1px solid var(--line);border-radius:14px;background:#fafafa}.signal-card small,.signal-card strong{display:block}.signal-card strong{font-size:18px;margin-top:3px}.insight-copy{white-space:pre-wrap}.engagement-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.engagement-item{padding:10px 12px;border-radius:10px;background:#f7f7f8}.engagement-item strong,.engagement-item small{display:block}
.source-tabs{display:flex;gap:6px;padding:6px;background:#eceef1;border-radius:13px;margin-bottom:24px}.source-tab{padding:9px 15px;border-radius:9px;text-decoration:none;color:#5f6671;font-weight:750}.source-tab.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(17,24,39,.08)}.source-list{display:grid;gap:14px}.source-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:start}.source-card form{grid-column:1/-1;display:grid;grid-template-columns:1.1fr 1.8fr 1fr auto;gap:10px}.source-card form button{grid-column:1/-1}.toggle-field{display:flex;align-items:center;gap:8px;padding:10px}.toggle-field input{width:auto}.source-state{display:inline-block;width:10px;height:10px;border-radius:50%;background:#b8bdc5;margin:0 8px 1px 0}.source-state.on{background:var(--success);box-shadow:0 0 0 4px #e8f8f1}.topic-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.topic-form .span-2,.topic-form button{grid-column:1/-1}.topic-form textarea{min-height:92px}.group-card form{display:grid;grid-template-columns:1fr 1fr 110px;gap:10px}.group-card form .group-categories,.group-card form button{grid-column:1/-1}.config-note{padding:13px 16px;border:1px solid #cdebdc;border-radius:12px;background:#eefaf5;color:var(--success);margin-bottom:18px}
.config-error{padding:13px 16px;border:1px solid #fecaca;border-radius:12px;background:#fef2f2;color:var(--danger);margin-bottom:18px}
.category-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.category-option{display:grid;grid-template-columns:auto 1fr;gap:9px;align-items:start;padding:11px;border:1px solid var(--line);border-radius:11px;background:#fafafa}.category-option input{width:auto;margin-top:4px}.category-option small,.category-option strong{display:block}.publishing-list{display:grid}.publishing-row{display:grid;grid-template-columns:minmax(0,1fr) 220px auto;gap:18px;align-items:center;padding:17px 0;border-top:1px solid var(--line)}.publishing-row:first-child{border-top:0}.publishing-row h3{margin:4px 0}.taxonomy-list{display:flex;gap:7px;flex-wrap:wrap}.taxonomy-item{padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:#fff;font-size:12px}.connection-card{border-color:#cdebdc;background:#f8fffb}
.media-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px}.media-card{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}.media-card img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover;background:#eceef1}.media-card-copy{padding:12px}.media-card-copy strong,.media-card-copy small{display:block;overflow-wrap:anywhere}.media-choice{position:relative;padding:0;overflow:hidden}.media-choice input{position:absolute;top:10px;left:10px;width:18px;height:18px;z-index:2}.media-choice img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover}.media-choice span{display:block;padding:10px}.upload-panel input[type=file]{background:#fff}.publishing-tabs{display:flex;gap:6px;padding:6px;background:#eceef1;border-radius:13px;margin-bottom:24px}.publishing-tab{padding:9px 15px;border-radius:9px;text-decoration:none;color:#5f6671;font-weight:750}.publishing-tab.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(17,24,39,.08)}
.media-search{display:grid;grid-template-columns:1fr auto;gap:10px;margin-bottom:18px}.media-search button{width:auto}.media-grid+.filter-tabs{margin-top:20px}
.image-studio{border-color:#ffd2b6;background:linear-gradient(145deg,#fff 0%,#fff8f3 100%)}.image-candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.image-candidate{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}.image-candidate img{display:block;width:100%;aspect-ratio:3/2;object-fit:cover;background:#eceef1}.image-candidate-copy{padding:13px}.image-candidate-copy p{margin:6px 0}.image-controls{display:grid;grid-template-columns:1fr 1fr;gap:10px}.image-controls .span-2,.image-controls button{grid-column:1/-1}.cost-note{padding:10px 12px;border-radius:10px;background:#fff7ed;color:#9a5514;font-size:12px}
.visual-reference-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.visual-reference{border:1px solid var(--line);border-radius:13px;overflow:hidden;background:#fff}.visual-reference img{display:block;width:100%;aspect-ratio:16/10;object-fit:cover}.visual-reference div{padding:10px}.visual-feedback{margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}.visual-feedback form{display:grid;gap:8px}.visual-feedback .feedback-row{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.integration-tabs{display:flex;gap:6px;padding:6px;background:#eceef1;border-radius:13px;margin-bottom:24px}.integration-tab{padding:9px 15px;border-radius:9px;text-decoration:none;color:#5f6671;font-weight:750}.integration-tab.active{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(17,24,39,.08)}.integration-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.integration-card{display:flex;flex-direction:column;gap:12px}.integration-card h2,.integration-card p{margin:0}.integration-card-head{display:flex;justify-content:space-between;gap:14px;align-items:start}.integration-details{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.integration-detail{padding:10px;border-radius:10px;background:#f7f7f8;font-size:12px}.integration-card details{border-top:1px solid var(--line);padding-top:10px}.integration-card summary{cursor:pointer;font-weight:700;color:var(--muted)}.integration-card form{margin-top:auto}.badge.guarded{color:var(--warning);background:#fff7ed;border-color:#fed7aa}.badge.live{color:#b45309;background:#fffbeb;border-color:#fcd34d}.badge.off{color:#68707d;background:#f3f4f6}.badge.needs_setup{color:var(--danger);background:#fef2f2;border-color:#fecaca}.security-principles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.security-principle{padding:18px;border:1px solid var(--line);border-radius:14px;background:#fafafa}.security-principle h3{margin-top:0}
@media(max-width:980px){.stat-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:820px){.sidebar{position:static;width:auto;padding:12px}.workspace{padding-bottom:12px;margin-bottom:8px}.workspace small,.nav-label,.sidebar-foot{display:none}
.side-nav{display:flex;overflow:auto}.nav-item{white-space:nowrap}.content{margin-left:0}.shell{padding:30px 18px 70px}.story-tabs{border-radius:10px}.story-tab{padding:8px 12px}}
@media(max-width:700px){.queue-tools{grid-template-columns:1fr}.queue-row{grid-template-columns:1fr}.queue-side{text-align:left}.queue-side .meta{justify-content:flex-start}.queue-side form{max-width:230px}}
@media(max-width:700px){.profile-grid,.brain-rule form{grid-template-columns:1fr}.profile-grid .span-2,.brain-rule form .rule-instruction,.brain-rule form button{grid-column:1}.promote-form{grid-template-columns:1fr}.promote-form button{width:100%}}
@media(max-width:900px){.radar-tools{grid-template-columns:1fr 1fr}.radar-tools button{width:100%}.radar-row{grid-template-columns:70px minmax(0,1fr)}.radar-side{grid-column:2;text-align:left}.radar-side .meta{justify-content:flex-start}}
@media(max-width:620px){.radar-tools,.intelligence-grid{grid-template-columns:1fr}.radar-row{grid-template-columns:1fr}.score-box{min-height:64px}.radar-side{grid-column:1}}
@media(max-width:760px){.source-card form,.topic-form,.group-card form{grid-template-columns:1fr}.topic-form .span-2,.topic-form button,.group-card form .group-categories,.group-card form button,.source-card form button{grid-column:1}}
@media(max-width:760px){.publishing-row{grid-template-columns:1fr}.category-grid{grid-template-columns:1fr}}
@media(max-width:560px){.media-search{grid-template-columns:1fr}.media-search button{width:100%}}
@media(max-width:620px){.image-controls{grid-template-columns:1fr}.image-controls .span-2,.image-controls button{grid-column:1}}
@media(max-width:800px){.integration-grid,.security-principles{grid-template-columns:1fr}.integration-details{grid-template-columns:1fr}}
@media(max-width:560px){.run-row{grid-template-columns:1fr}.stat-grid{grid-template-columns:1fr 1fr}.queue-tools form{grid-template-columns:1fr}.queue-tools button{width:100%}}
@media(max-width:480px){.nav-item{font-size:13px;padding:9px}.nav-icon{display:none}}
"""

_ICONS = {
    "overview": "<svg viewBox='0 0 24 24'><rect x='3' y='3' width='7' height='7' rx='1'/><rect x='14' y='3' width='7' height='7' rx='1'/><rect x='3' y='14' width='7' height='7' rx='1'/><rect x='14' y='14' width='7' height='7' rx='1'/></svg>",
    "discovery": "<svg viewBox='0 0 24 24'><circle cx='11' cy='11' r='7'/><path d='m16 16 5 5M11 7v8M7 11h8'/></svg>",
    "sources": "<svg viewBox='0 0 24 24'><path d='M4 6h16M4 12h16M4 18h16'/><circle cx='8' cy='6' r='2'/><circle cx='16' cy='12' r='2'/><circle cx='10' cy='18' r='2'/></svg>",
    "editorial": "<svg viewBox='0 0 24 24'><path d='M4 5h16v14H4z'/><path d='M8 9h8M8 13h8M8 17h5'/></svg>",
    "drafts": "<svg viewBox='0 0 24 24'><path d='M6 3h9l4 4v14H6z'/><path d='M14 3v5h5M9 12h6M9 16h6'/></svg>",
    "publishing": "<svg viewBox='0 0 24 24'><path d='M5 4h14v16H5z'/><path d='M8 8h8M8 12h8M8 16h5'/><path d='m14 4 5 5'/></svg>",
    "integrations": "<svg viewBox='0 0 24 24'><path d='M8 12h8M12 8v8'/><path d='M7 4h10v4a4 4 0 0 1 0 8v4H7v-4a4 4 0 0 1 0-8z'/></svg>",
    "memory": "<svg viewBox='0 0 24 24'><path d='M12 3a4 4 0 0 0-4 4v1a4 4 0 0 0 0 8v1a4 4 0 0 0 4 4'/><path d='M12 3a4 4 0 0 1 4 4v1a4 4 0 0 1 0 8v1a4 4 0 0 1-4 4M12 3v18'/></svg>",
    "operations": "<svg viewBox='0 0 24 24'><path d='M4 7h10M4 17h16M18 7h2M4 12h3M11 12h9'/><circle cx='16' cy='7' r='2'/><circle cx='9' cy='12' r='2'/></svg>",
    "workspace_admin": "<svg viewBox='0 0 24 24'><path d='M4 20V6l8-3 8 3v14'/><path d='M8 9h2M14 9h2M8 13h2M14 13h2M9 20v-3h6v3'/></svg>",
}

_SOCIAL_LABELS = {
    SocialPlatform.LINKEDIN: "LinkedIn",
    SocialPlatform.X: "X",
    SocialPlatform.FACEBOOK: "Facebook",
}


def _nav_item(key: str, label: str, href: str, active: str) -> str:
    selected = " active" if key == active else ""
    return f"<a class='nav-item{selected}' href='{href}'><span class='nav-icon'>{_ICONS[key]}</span>{label}</a>"


def _page(
    title: str,
    body: str,
    active: str = "overview",
    brand_name: str = "CodeQuest",
    organization_name: str = "CodeQuest workspace",
) -> HTMLResponse:
    brand_words = [word for word in brand_name.split() if word]
    brand_mark = "".join(word[0] for word in brand_words[:2]).upper()
    if len(brand_words) == 1:
        brand_mark = "".join(character for character in brand_name if character.isupper())[:2]
    brand_mark = brand_mark or "BR"
    navigation = "".join(
        (
            _nav_item("overview", "Overview", "/", active),
            _nav_item("discovery", "Discovery Radar", "/discovery", active),
            _nav_item("sources", "Source Control", "/sources", active),
            _nav_item("editorial", "Editorial queue", "/editorial", active),
            _nav_item("drafts", "Draft library", "/drafts", active),
            _nav_item("publishing", "Publishing Hub", "/publishing", active),
            _nav_item("integrations", "Integrations", "/integrations", active),
            _nav_item("memory", "Brand Brain", "/preferences", active),
            _nav_item("operations", "Operations", "/operations", active),
            _nav_item("workspace_admin", "Workspace settings", "/workspace", active),
        )
    )
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{escape(title)} · CodeQuest</title><style>{_STYLE}{_ADMIN_STYLE}</style></head>"
        f"<body><aside class='sidebar'><a class='workspace' href='/workspace'><div class='workspace-mark'>{escape(brand_mark)}</div>"
        f"<div><strong>{escape(brand_name)}</strong><small>Switch brand workspace</small></div></a>"
        f"<p class='nav-label'>Workspace</p><nav class='side-nav'>{navigation}</nav>"
        f"<div class='sidebar-foot'><span class='status-dot'></span><div><strong>{escape(organization_name)}</strong>"
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


def _display_time(value) -> str:
    return value.strftime("%d %b %Y, %H:%M UTC")


def _run_duration(run) -> str:
    if run.finished_at is None:
        return "In progress"
    seconds = max(0, int((run.finished_at - run.started_at).total_seconds()))
    if seconds < 60:
        return f"{seconds} sec"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes} min {remainder} sec"


def _trigger_label(trigger: str) -> str:
    return {
        "manual-cli": "Manual CLI",
        "manual-workspace": "Manual workspace",
        "schedule": "Scheduled",
    }.get(trigger, trigger.replace("-", " ").replace("_", " ").title())


def create_app(
    db_path: str | Path = "data/codequest-editorial.sqlite3",
    draft_generator_factory: Callable[[], ArticleDraftGenerator] | None = None,
    discord_bridge_factory: Callable[[], DiscordApprovalBridge] | None = None,
    wordpress_publisher_factory: Callable[[], WordPressPublisher] | None = None,
    social_generator_factory: Callable[[], SocialCampaignGenerator] | None = None,
    buffer_config_factory: Callable[[], BufferConfig] | None = None,
    buffer_publisher_factory: Callable[[], BufferPublisher] | None = None,
    automation_runner_factory: Callable[[], EditorialAutomationRunner] | None = None,
    source_config_path: str | Path | None = None,
    image_generator_factory: Callable[[], ImageGenerator] | None = None,
    image_config_factory: Callable[[], ImageGenerationConfig] | None = None,
    generated_image_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="CodeQuest Editorial Workspace")
    store = EditorialStore(db_path)
    try:
        credential_vault = WorkspaceSecretVault.from_env()
        credential_vault_error = ""
    except SecretVaultError as exc:
        credential_vault = WorkspaceSecretVault(None)
        credential_vault_error = str(exc)

    def render_page(title: str, body: str, active: str = "overview") -> HTMLResponse:
        brand = store.get_brand_profile()
        organization = store.get_organization(brand.organization_id)
        return _page(
            title,
            body,
            active=active,
            brand_name=brand.name,
            organization_name=organization.name,
        )
    bridge_factory = discord_bridge_factory or (
        lambda: DiscordApprovalBridge(DiscordConfig.from_env())
    )
    def brand_connection_values(
        provider: ConnectionProvider,
    ) -> tuple[dict[str, str | bool], dict[str, str]] | None:
        profile = store.get_brand_connection(provider)
        if profile is None:
            return None
        return profile.settings, credential_vault.decrypt(profile.encrypted_secrets)

    def active_wordpress_config() -> WordPressConfig:
        values = brand_connection_values(ConnectionProvider.WORDPRESS)
        if values is None:
            return WordPressConfig.from_env()
        settings, secrets = values
        return WordPressConfig(
            base_url=str(settings.get("base_url", "")).rstrip("/"),
            username=str(settings.get("username", "")),
            application_password=secrets.get("application_password", ""),
            dry_run=bool(settings.get("dry_run", True)),
        )

    def active_writer_factory() -> ArticleDraftGenerator:
        values = brand_connection_values(ConnectionProvider.OLLAMA)
        if values is None:
            return create_ollama_cloud_draft_generator()
        settings, secrets = values
        return create_ollama_cloud_draft_generator(
            base_url=str(settings.get("base_url", "")),
            model=str(settings.get("writer_model", "")),
            api_key=secrets.get("api_key", ""),
        )

    def active_social_writer_factory() -> SocialCampaignGenerator:
        values = brand_connection_values(ConnectionProvider.OLLAMA)
        if values is None:
            return create_ollama_cloud_social_generator()
        settings, secrets = values
        return create_ollama_cloud_social_generator(
            base_url=str(settings.get("base_url", "")),
            model=str(settings.get("social_model") or settings.get("writer_model", "")),
            api_key=secrets.get("api_key", ""),
        )

    writer_factory = draft_generator_factory or active_writer_factory

    publisher_factory = wordpress_publisher_factory or (
        lambda: WordPressPublisher(active_wordpress_config())
    )
    social_writer_factory = social_generator_factory or active_social_writer_factory
    def active_buffer_config() -> BufferConfig:
        values = brand_connection_values(ConnectionProvider.BUFFER)
        if values is None:
            return BufferConfig.from_env()
        settings, secrets = values
        return BufferConfig(
            access_token=secrets.get("access_token", ""),
            channel_ids={
                SocialPlatform.LINKEDIN: str(settings.get("linkedin_channel_id", "")),
                SocialPlatform.X: str(settings.get("x_channel_id", "")),
                SocialPlatform.FACEBOOK: str(settings.get("facebook_channel_id", "")),
            },
            dry_run=bool(settings.get("dry_run", True)),
            schedule_timezone=str(settings.get("schedule_timezone", "Africa/Johannesburg")),
        )

    buffer_settings_factory = buffer_config_factory or active_buffer_config
    buffer_sender_factory = buffer_publisher_factory or (
        lambda: BufferPublisher(buffer_settings_factory())
    )
    automation_settings = AutomationConfig.from_env()
    discovery_config_path = Path(
        source_config_path or automation_settings.discovery_config_path
    )
    def active_source_path() -> Path:
        brand_id = store.get_active_brand_id()
        if brand_id == "brand_codequest":
            return discovery_config_path
        return Path(db_path).parent / "brand-sources" / brand_id / "config.json"

    class ActiveSourceControl:
        @property
        def ready(self) -> bool:
            return active_source_path().is_file()

        def __getattr__(self, name: str):
            return getattr(SourceControlService(active_source_path()), name)

    source_control = ActiveSourceControl()

    def automation_factory() -> EditorialAutomationRunner:
        if automation_runner_factory is not None:
            return automation_runner_factory()
        active_settings = replace(
            automation_settings,
            discovery_config_path=active_source_path(),
        )
        return create_automation_runner(db_path, active_settings)
    def active_image_config() -> ImageGenerationConfig:
        values = brand_connection_values(ConnectionProvider.IMAGES)
        if values is None:
            return ImageGenerationConfig.from_env()
        settings, secrets = values
        return ImageGenerationConfig(
            provider=str(settings.get("provider", "openai")),
            model=str(settings.get("model", "gpt-image-2")),
            api_key=secrets.get("api_key", ""),
            base_url=str(settings.get("base_url", "https://api.openai.com/v1")),
            enabled=bool(settings.get("enabled", False)),
        )

    image_settings_factory = image_config_factory or active_image_config
    image_writer_factory = image_generator_factory or (
        lambda: OpenAIImageGenerator(image_settings_factory())
    )
    image_output_dir = Path(generated_image_dir or Path(db_path).parent / "generated-images")

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

    def approved_social_post(content_item_id: str, post_id: str):
        _record, draft = approved_draft(content_item_id)
        campaign = store.get_social_campaign(draft.draft_id)
        if campaign is None:
            raise HTTPException(status_code=409, detail="Generate a social campaign first.")
        posts = store.list_latest_social_posts(campaign.campaign_id)
        post = next((item for item in posts if item.post_id == post_id), None)
        if post is None:
            raise HTTPException(
                status_code=409,
                detail="Only the latest social version can be delivered to Buffer.",
            )
        if post.status != SocialPostStatus.APPROVED:
            raise HTTPException(
                status_code=409,
                detail="Approve this exact social version before Buffer delivery.",
            )
        return draft, campaign, post

    def buffer_preview_data(
        content_item_id: str,
        post_id: str,
        mode: str,
        due_at: str,
    ):
        _draft, campaign, post = approved_social_post(content_item_id, post_id)
        try:
            selected_mode = BufferDeliveryMode(mode)
            config = buffer_settings_factory()
            scheduled_for = (
                parse_scheduled_time(due_at, config.schedule_timezone)
                if selected_mode == BufferDeliveryMode.CUSTOM_SCHEDULED
                else None
            )
            payload = build_buffer_payload(
                post,
                campaign,
                config,
                selected_mode,
                scheduled_for,
            )
        except (BufferDeliveryRejected, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return campaign, post, config, selected_mode, scheduled_for, payload

    @app.get("/workspace", response_class=HTMLResponse)
    def workspace_settings(notice: str = "") -> HTMLResponse:
        active_brand = store.get_brand_profile()
        organizations = store.list_organizations()
        brands = store.list_brands()
        organization_by_id = {
            organization.organization_id: organization for organization in organizations
        }
        brand_cards = "".join(
            "<article class='panel integration-card'><div class='integration-card-head'><div>"
            f"<p class='eyebrow'>{escape(organization_by_id[brand.organization_id].name.upper())}</p>"
            f"<h2>{escape(brand.name)}</h2></div>"
            + (
                "<span class='badge approved'>Active brand</span>"
                if brand.brand_id == active_brand.brand_id
                else "<span class='badge'>Available</span>"
            )
            + "</div>"
            f"<p class='muted'>{escape(brand.description or 'Brand context has not been completed yet.')}</p>"
            f"<div class='integration-details'><div class='integration-detail'><strong>{len(store.list_items(brand.brand_id))}</strong><br>stories</div>"
            f"<div class='integration-detail'><strong>{len(store.list_brand_rules(brand.brand_id))}</strong><br>rules</div>"
            f"<div class='integration-detail'><strong>{len(store.list_visual_feedback(brand.brand_id))}</strong><br>visual signals</div></div>"
            + (
                "<a class='text-link' href='/preferences'>Manage this brand’s identity →</a>"
                if brand.brand_id == active_brand.brand_id
                else f"<form method='post' action='/workspace/switch'><input type='hidden' name='brand_id' value='{escape(brand.brand_id, quote=True)}'><button type='submit'>Switch to this brand</button></form>"
            )
            + "</article>"
            for brand in brands
        )
        organization_options = "".join(
            f"<option value='{escape(organization.organization_id, quote=True)}'>{escape(organization.name)}</option>"
            for organization in organizations
        )
        notice_html = (
            f"<div class='notice'><strong>{escape(notice)}</strong></div>" if notice else ""
        )
        return render_page(
            "Workspace settings",
            "<header class='page-head'><p class='eyebrow'>WORKSPACE SETTINGS</p>"
            "<h1>Manage organisations and <span class='accent'>brands.</span></h1>"
            "<p class='muted'>Each brand has its own stories, writing rules, visual identity, and learning history. Switching changes the entire editorial workspace.</p></header>"
            f"{notice_html}<section class='integration-grid'>{brand_cards}</section>"
            "<div class='layout' style='margin-top:20px'><section class='panel'><p class='eyebrow'>NEW BRAND</p>"
            "<h2>Add a brand workspace</h2><p class='muted'>Use a separate brand when content should learn a different voice, audience, or visual identity.</p>"
            "<form method='post' action='/workspace/brands'>"
            f"<label><span class='field-label'>Organisation</span><select name='organization_id'>{organization_options}</select></label>"
            "<label><span class='field-label'>Brand name</span><input name='name' required maxlength='100' placeholder='For example: Acme Developer Platform'></label>"
            "<label><span class='field-label'>Short description</span><textarea name='description' maxlength='600' placeholder='What this brand does and who it serves'></textarea></label>"
            "<button type='submit'>Create and open brand</button></form></section>"
            "<aside class='panel'><p class='eyebrow'>NEW ORGANISATION</p><h2>Add an organisation</h2>"
            "<p class='muted'>An organisation is the future billing and access boundary. It can contain one or more brands.</p>"
            "<form method='post' action='/workspace/organizations'>"
            "<label><span class='field-label'>Organisation name</span><input name='name' required maxlength='100' placeholder='For example: Acme Group'></label>"
            "<button class='button-revise' type='submit'>Add organisation</button></form>"
            "<div class='cost-note'>This phase isolates content and learning. User invitations, roles, billing, and per-organisation secret vaults come later.</div></aside></div>",
            active="workspace_admin",
        )

    @app.post("/workspace/organizations")
    def create_organization(name: str = Form()) -> RedirectResponse:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="Enter an organisation name.")
        try:
            organization = store.add_organization(Organization(name=cleaned_name))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/workspace?notice={quote(organization.name)}%20organisation%20added",
            status_code=303,
        )

    @app.post("/workspace/brands")
    def create_brand(
        organization_id: str = Form(),
        name: str = Form(),
        description: str = Form(""),
    ) -> RedirectResponse:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="Enter a brand name.")
        try:
            brand = store.add_brand(
                BrandProfile(
                    organization_id=organization_id,
                    name=cleaned_name,
                    description=description.strip(),
                )
            )
            store.set_active_brand(brand.brand_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Choose a valid organisation.") from exc
        return RedirectResponse(
            f"/workspace?notice={quote(brand.name)}%20created%20and%20opened",
            status_code=303,
        )

    @app.post("/workspace/switch")
    def switch_brand(brand_id: str = Form()) -> RedirectResponse:
        try:
            brand = store.set_active_brand(brand_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Brand workspace not found.") from exc
        return RedirectResponse(
            f"/workspace?notice=Now%20working%20in%20{quote(brand.name)}",
            status_code=303,
        )

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
        return render_page(
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

    @app.get("/discovery", response_class=HTMLResponse)
    def discovery_radar(
        q: str = "", source: str = "all", min_score: float = 0, sort: str = "score"
    ) -> HTMLResponse:
        if sort not in {"score", "newest"}:
            raise HTTPException(status_code=404, detail="Discovery sort not found")
        min_score = min(10, max(0, min_score))
        all_records = store.list_items()
        source_values = sorted(
            {
                record.packet.discovery.source_type.value
                for record in all_records
                if record.packet.discovery is not None
            }
        )
        if source != "all" and source not in source_values:
            raise HTTPException(status_code=404, detail="Discovery source not found")
        search = q.strip().casefold()
        records = []
        for record in all_records:
            insight = record.packet.discovery
            score = insight.ai_score if insight and insight.ai_score is not None else None
            searchable = " ".join(
                [
                    record.packet.brief.working_title,
                    record.packet.brief.central_angle,
                    *(insight.ai_tags if insight else []),
                    insight.ai_summary if insight else "",
                    insight.ai_reason if insight else "",
                ]
            ).casefold()
            if search and search not in searchable:
                continue
            if source != "all" and (not insight or insight.source_type.value != source):
                continue
            if min_score and (score is None or score < min_score):
                continue
            records.append(record)
        records.sort(
            key=lambda record: (
                record.packet.discovery.ai_score
                if record.packet.discovery and record.packet.discovery.ai_score is not None
                else -1
            )
            if sort == "score"
            else (
                record.packet.discovery.published_at.timestamp()
                if record.packet.discovery
                else 0
            ),
            reverse=True,
        )
        scored_count = sum(
            record.packet.discovery is not None
            and record.packet.discovery.ai_score is not None
            for record in all_records
        )
        high_signal = sum(
            record.packet.discovery is not None
            and record.packet.discovery.ai_score is not None
            and record.packet.discovery.ai_score >= 8
            for record in all_records
        )
        source_options = "".join(
            f"<option value='{escape(value, quote=True)}'{' selected' if value == source else ''}>"
            f"{escape(value.replace('_', ' ').title())}</option>"
            for value in source_values
        )
        rows = []
        for record in records:
            packet = record.packet
            insight = packet.discovery
            encoded_id = quote(packet.brief.content_item_id, safe="")
            if insight and insight.ai_score is not None:
                score_box = f"<div class='score-box'><strong>{insight.ai_score:.1f}</strong><small>Horizon score</small></div>"
            else:
                score_box = "<div class='score-box pending'><strong>—</strong><small>Awaiting refresh</small></div>"
            tags = "".join(
                f"<span class='tag'>{escape(tag)}</span>" for tag in (insight.ai_tags if insight else [])[:6]
            )
            source_label = insight.source_type.value if insight else "legacy"
            published = _display_time(insight.published_at) if insight else "Awaiting Horizon refresh"
            reason = (
                insight.ai_reason or insight.ai_summary
                if insight
                else "This legacy story remains usable. Its intelligence will be backfilled when Horizon discovers it again."
            )
            action = (
                f"<form method='post' action='/items/{encoded_id}/status'><input type='hidden' name='status' value='selected'>"
                "<input type='hidden' name='return_to' value='discovery'><button class='compact-button' type='submit'>Select story</button></form>"
                if record.status == "candidate"
                else f"<a class='text-link' href='/items/{encoded_id}?tab=intelligence'>Open intelligence →</a>"
            )
            rows.append(
                f"<article class='panel radar-row'>{score_box}<div class='radar-copy'>"
                f"<span class='badge {escape(record.status)}'>{escape(record.status.replace('_', ' '))}</span> "
                f"<span class='badge'>{escape(source_label)}</span><h2><a href='/items/{encoded_id}?tab=intelligence'>{escape(packet.brief.working_title)}</a></h2>"
                f"<p class='muted'>{escape(reason)}</p><div class='tags'>{tags}</div></div>"
                f"<div class='radar-side'><small class='muted'>{escape(published)}</small><div class='meta'><span>{len(packet.evidence.sources)} sources</span></div>{action}</div></article>"
            )
        radar_rows = "".join(rows) or (
            "<section class='panel empty'><h2>No matching signals</h2>"
            "<p class='muted'>Adjust the radar filters or run Horizon discovery from Operations.</p></section>"
        )
        return render_page(
            "Discovery Radar",
            "<header class='page-head'><p class='eyebrow'>DISCOVERY RADAR</p>"
            "<h1>Find the stories worth <span class='accent'>pursuing.</span></h1>"
            "<p class='muted'>Horizon scoring and enrichment, preserved for editorial judgment.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Tracked stories</small><strong class='stat-value'>{len(all_records)}</strong><span class='muted'>in editorial storage</span></article>"
            f"<article class='stat-card'><small>Scored</small><strong class='stat-value'>{scored_count}</strong><span class='muted'>with Horizon intelligence</span></article>"
            f"<article class='stat-card'><small>High signal</small><strong class='stat-value'>{high_signal}</strong><span class='muted'>score 8 or above</span></article>"
            f"<article class='stat-card'><small>Source types</small><strong class='stat-value'>{len(source_values)}</strong><span class='muted'>represented in the radar</span></article></section>"
            "<form class='radar-tools' method='get' action='/discovery'>"
            f"<input name='q' value='{escape(q, quote=True)}' placeholder='Search signals, summaries or tags'>"
            f"<select name='source'><option value='all'>All sources</option>{source_options}</select>"
            f"<input type='number' name='min_score' min='0' max='10' step='0.5' value='{min_score:g}' aria-label='Minimum score'>"
            f"<select name='sort'><option value='score'{' selected' if sort == 'score' else ''}>Highest score</option><option value='newest'{' selected' if sort == 'newest' else ''}>Newest first</option></select>"
            "<button type='submit'>Apply</button></form>"
            f"<section class='radar-list'>{radar_rows}</section>",
            active="discovery",
        )

    @app.get("/sources", response_class=HTMLResponse)
    def source_control_page(tab: str = "sources", notice: str = "") -> HTMLResponse:
        if tab not in {"sources", "topics"}:
            raise HTTPException(status_code=404, detail="Source control tab not found")
        if not source_control.ready:
            active_brand = store.get_brand_profile()
            template_ready = discovery_config_path.is_file()
            setup_action = (
                "<form method='post' action='/sources/initialize'>"
                "<button type='submit'>Copy the current source policy</button></form>"
                if active_brand.brand_id != "brand_codequest" and template_ready
                else "<a class='text-link' href='/operations'>Open Operations →</a>"
            )
            return render_page(
                "Source Control",
                "<section class='panel empty'><p class='eyebrow'>SOURCE CONTROL</p>"
                "<h1>Discovery configuration <span class='accent'>needed.</span></h1>"
                f"<p class='muted'>{escape(active_brand.name)} has no source policy yet. Copying creates an independent starting point; future edits affect only this brand.</p>"
                f"{setup_action}</section>",
                active="sources",
            )
        try:
            config = source_control.load()
            raw_config = source_control.load_raw()
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        records = store.list_items()
        observed: dict[str, int] = {}
        for record in records:
            insight = record.packet.discovery
            if insight:
                observed[insight.source_type.value] = observed.get(insight.source_type.value, 0) + 1
        enabled_rss = sum(source.enabled for source in config.sources.rss)
        active_types = {
            "rss": bool(enabled_rss),
            "hackernews": config.sources.hackernews.enabled,
            "reddit": config.sources.reddit.enabled,
            "telegram": config.sources.telegram.enabled,
            "ossinsight": config.sources.ossinsight.enabled,
            "twitter": bool(config.sources.twitter and config.sources.twitter.enabled),
            "openbb": bool(config.sources.openbb and config.sources.openbb.enabled),
            "gdelt": bool(config.sources.gdelt and config.sources.gdelt.enabled),
            "google_news": bool(config.sources.google_news and config.sources.google_news.enabled),
            "github": any(source.enabled for source in config.sources.github),
        }
        active_type_count = sum(active_types.values())
        categories = sorted(
            {
                category
                for category in [
                    config.sources.hackernews.category,
                    *(source.category for source in config.sources.rss),
                ]
                if category
            }
        )
        notice_html = (
            f"<div class='config-note'>{escape(notice)}</div>" if notice else ""
        )
        tabs = (
            "<nav class='source-tabs' aria-label='Source controls'>"
            f"<a class='source-tab{' active' if tab == 'sources' else ''}' href='/sources?tab=sources'>Sources</a>"
            f"<a class='source-tab{' active' if tab == 'topics' else ''}' href='/sources?tab=topics'>Topics & limits</a></nav>"
        )
        rss_cards = []
        raw_rss = raw_config.get("sources", {}).get("rss", [])
        for index, source in enumerate(config.sources.rss):
            checked = " checked" if source.enabled else ""
            raw_url = (
                str(raw_rss[index].get("url", source.url))
                if index < len(raw_rss)
                else str(source.url)
            )
            rss_cards.append(
                "<article class='panel source-card'><div>"
                f"<h2><span class='source-state{' on' if source.enabled else ''}'></span>{escape(source.name)}</h2>"
                f"<p class='muted'>{escape(raw_url)}</p>"
                f"<span class='badge'>RSS · {escape(source.category or 'uncategorised')}</span></div>"
                f"<span class='badge {'pass' if source.enabled else ''}'>{'Enabled' if source.enabled else 'Paused'}</span>"
                f"<form method='post' action='/sources/rss/{index}'>"
                f"<input name='name' value='{escape(source.name, quote=True)}' required aria-label='Feed name'>"
                f"<input name='url' value='{escape(raw_url, quote=True)}' required aria-label='Feed URL'>"
                f"<input name='category' value='{escape(source.category or '', quote=True)}' placeholder='Category' aria-label='Category'>"
                f"<label class='toggle-field'><input type='checkbox' name='enabled' value='true'{checked}> Enabled</label>"
                "<button type='submit'>Save feed</button></form></article>"
            )
        rss_html = "".join(rss_cards) or (
            "<section class='panel'><h2>No RSS feeds configured</h2><p class='muted'>Add the first trusted publication below.</p></section>"
        )
        hn = config.sources.hackernews
        hn_checked = " checked" if hn.enabled else ""
        sources_body = (
            "<div class='layout'><section class='stack'><div class='section-head'><h2>RSS publications</h2>"
            f"<span class='muted'>{enabled_rss}/{len(config.sources.rss)} enabled · {observed.get('rss', 0)} observed stories</span></div><div class='source-list'>{rss_html}</div></section>"
            "<aside class='stack'><section class='panel'><p class='eyebrow'>ADD SOURCE</p><h2>New RSS feed</h2>"
            "<form method='post' action='/sources/rss'><input name='name' required placeholder='Publication name'>"
            "<input name='url' type='url' required placeholder='https://example.com/feed.xml'>"
            "<input name='category' placeholder='Category, e.g. developer-tools'>"
            "<label class='toggle-field'><input type='checkbox' name='enabled' value='true' checked> Enabled</label>"
            "<button type='submit'>Add RSS feed</button></form></section>"
            "<section class='panel'><p class='eyebrow'>COMMUNITY SIGNAL</p><h2>Hacker News</h2>"
            f"<p class='muted'>{observed.get('hackernews', 0)} enriched stories currently in the workspace.</p>"
            "<form method='post' action='/sources/hackernews'>"
            f"<input type='number' min='1' max='500' name='fetch_top_stories' value='{hn.fetch_top_stories}' aria-label='Stories to fetch'>"
            f"<input type='number' min='0' name='min_score' value='{hn.min_score}' aria-label='Minimum HN score'>"
            f"<input name='category' value='{escape(hn.category or '', quote=True)}' placeholder='Category'>"
            f"<label class='toggle-field'><input type='checkbox' name='enabled' value='true'{hn_checked}> Enabled</label>"
            "<button type='submit'>Save Hacker News</button></form></section></aside></div>"
        )
        filtering = config.filtering
        include_value = ", ".join(filtering.include_keywords)
        exclude_value = ", ".join(filtering.exclude_keywords)
        group_cards = "".join(
            "<article class='panel group-card'>"
            f"<span class='badge'>{escape(key)}</span><h2>{escape(group.name or key)}</h2>"
            f"<p class='muted'>Together, these {len(group.categories)} categor{'y' if len(group.categories) == 1 else 'ies'} can contribute at most {group.limit} stories per run.</p>"
            "<form method='post' action='/sources/groups'>"
            f"<input type='hidden' name='key' value='{escape(key, quote=True)}'>"
            f"<label><span class='field-label'>Name editors see</span><input name='name' value='{escape(group.name or '', quote=True)}' placeholder='Display name'></label>"
            f"<label><span class='field-label'>Combined story maximum</span><input type='number' min='1' name='limit' value='{group.limit}' aria-label='Group limit'></label>"
            "<span></span>"
            "<label class='group-categories'><span class='field-label'>Source categories sharing this maximum</span>"
            f"<textarea class='group-categories' name='categories' required>{escape(', '.join(group.categories))}</textarea>"
            "</label><button type='submit'>Save topic-mix limit</button></form></article>"
            for key, group in filtering.category_groups.items()
        ) or "<section class='panel'><h2>No topic-mix limits yet</h2><p class='muted'>Every qualifying story currently competes for the same overall story limit. Add a group if one subject starts crowding out the others.</p></section>"
        topics_body = (
            "<div class='layout'><section class='stack'><section class='panel'><p class='eyebrow'>WHAT SHOULD ENTER YOUR QUEUE?</p>"
            "<h2>Choose which stories Horizon keeps</h2><p class='muted'>After Horizon collects stories, these rules remove unwanted topics, require any must-have topics, reject weak stories, and finally limit how many reach your queue.</p>"
            "<form class='topic-form' method='post' action='/sources/filtering'>"
            f"<label><span class='field-label'>How strong must a story be?</span><small class='muted'>Horizon scores every story from 0–10. At {filtering.ai_score_threshold:g}, lower-scoring stories are left out.</small><input type='number' min='0' max='10' step='0.1' name='score_threshold' value='{filtering.ai_score_threshold}'></label>"
            f"<label><span class='field-label'>How far back should Horizon look?</span><small class='muted'>A {filtering.time_window_hours}-hour window finds stories published during roughly the last {max(1, round(filtering.time_window_hours / 24))} day(s).</small><input type='number' min='1' max='720' name='time_window_hours' value='{filtering.time_window_hours}'></label>"
            f"<label><span class='field-label'>How many stories may one run keep?</span><small class='muted'>This is the final overall cap after all other rules. Leave empty for no overall cap.</small><input type='number' min='1' name='max_items' value='{filtering.max_items or ''}' placeholder='No overall cap'></label>"
            f"<label><span class='field-label'>How many ungrouped stories may appear?</span><small class='muted'>Applies to stories whose source category is not in a topic-mix group below.</small><input type='number' min='1' name='default_group_limit' value='{filtering.default_group_limit or ''}' placeholder='No separate cap'></label>"
            f"<label class='span-2'><span class='field-label'>Only keep stories about these topics (optional)</span><small class='muted'>When filled in, a story must match at least one phrase. Separate phrases with commas or new lines. Leave empty to allow every topic.</small><textarea name='include_keywords' placeholder='AI agents, developer tools, Python'>{escape(include_value)}</textarea></label>"
            f"<label class='span-2'><span class='field-label'>Never keep stories about these topics</span><small class='muted'>Exclusions always win—even when the story also matches an allowed topic.</small><textarea name='exclude_keywords' placeholder='crypto price, celebrity gossip'>{escape(exclude_value)}</textarea></label>"
            "<button type='submit'>Save discovery policy</button></form></section>"
            f"<div class='section-head'><h2>Topic-mix limits</h2><span class='muted'>{len(filtering.category_groups)} group(s)</span></div>"
            "<p class='muted'>Prevent one broad subject from filling the entire queue. Each group combines several source categories and sets their shared maximum.</p>"
            f"{group_cards}</section>"
            "<aside class='stack'><section class='panel'><p class='eyebrow'>CURRENT EFFECT</p><h2>How the rules run</h2>"
            f"<p><strong>1. Exclude:</strong> {len(filtering.exclude_keywords)} blocked phrase(s).</p>"
            f"<p><strong>2. Include:</strong> {'Match at least one of ' + str(len(filtering.include_keywords)) + ' phrase(s).' if filtering.include_keywords else 'No required topics; all subjects may continue.'}</p>"
            f"<p><strong>3. Quality:</strong> Keep scores of {filtering.ai_score_threshold:g} or higher.</p>"
            f"<p><strong>4. Mix and volume:</strong> Apply {len(filtering.category_groups)} topic-group limit(s), then keep at most {filtering.max_items if filtering.max_items else 'an unlimited number of'} stories.</p></section>"
            "<section class='panel'><p class='eyebrow'>ADD A TOPIC-MIX LIMIT</p><h2>Stop one subject dominating the queue</h2>"
            "<p class='muted'>Example: combine <strong>AI</strong>, <strong>machine-learning</strong>, and <strong>LLMs</strong> with a limit of 5. Horizon may then keep at most five stories across those categories combined—not five from each.</p>"
            "<form method='post' action='/sources/groups'><input name='key' required placeholder='developer-core'>"
            "<small class='muted'>Internal label, for example developer-core</small>"
            "<input name='name' placeholder='Developer core topics'><small class='muted'>Name shown to editors</small>"
            "<input type='number' min='1' name='limit' value='5'><small class='muted'>Maximum stories from all categories in this group</small>"
            "<textarea name='categories' required placeholder='ai, developer-tools, developer-news'></textarea>"
            "<small class='muted'>Use the same category names assigned to sources on the Sources tab.</small>"
            "<button type='submit'>Add topic-mix limit</button></form></section></aside></div>"
        )
        return render_page(
            "Source Control",
            "<header class='page-head'><p class='eyebrow'>SOURCE CONTROL</p>"
            "<h1>Shape what Horizon <span class='accent'>notices.</span></h1>"
            "<p class='muted'>Manage the real discovery inputs and limits used by the automation pipeline.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Active source types</small><strong class='stat-value'>{active_type_count}</strong><span class='muted'>of {len(active_types)} supported types</span></article>"
            f"<article class='stat-card'><small>RSS feeds</small><strong class='stat-value'>{enabled_rss}</strong><span class='muted'>{len(config.sources.rss)} configured</span></article>"
            f"<article class='stat-card'><small>Topic categories</small><strong class='stat-value'>{len(categories)}</strong><span class='muted'>assigned to primary sources</span></article>"
            f"<article class='stat-card'><small>Radar coverage</small><strong class='stat-value'>{sum(observed.values())}</strong><span class='muted'>stories with preserved intelligence</span></article></section>"
            f"{notice_html}{tabs}{sources_body if tab == 'sources' else topics_body}",
            active="sources",
        )

    @app.post("/sources/initialize")
    def initialize_brand_sources() -> RedirectResponse:
        brand = store.get_brand_profile()
        target = active_source_path()
        if brand.brand_id == "brand_codequest":
            raise HTTPException(
                status_code=409,
                detail="The original CodeQuest source policy must be configured directly.",
            )
        if target.is_file():
            return RedirectResponse("/sources", status_code=303)
        if not discovery_config_path.is_file():
            raise HTTPException(
                status_code=409,
                detail="Configure the original workspace source policy before copying it.",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(discovery_config_path, target)
        return RedirectResponse(
            f"/sources?notice={quote(brand.name)}%20now%20has%20an%20independent%20source%20policy",
            status_code=303,
        )

    def _optional_positive_int(value: str, label: str) -> int | None:
        if not value.strip():
            return None
        try:
            parsed = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{label} must be a whole number.") from exc
        if parsed < 1:
            raise HTTPException(status_code=400, detail=f"{label} must be at least 1.")
        return parsed

    @app.post("/sources/filtering")
    def update_source_filtering(
        score_threshold: float = Form(),
        time_window_hours: int = Form(),
        max_items: str = Form(""),
        default_group_limit: str = Form(""),
        include_keywords: str = Form(""),
        exclude_keywords: str = Form(""),
    ) -> RedirectResponse:
        try:
            source_control.update_filtering(
                score_threshold=score_threshold,
                time_window_hours=time_window_hours,
                max_items=_optional_positive_int(max_items, "Maximum stories"),
                include_keywords=include_keywords,
                exclude_keywords=exclude_keywords,
                default_group_limit=_optional_positive_int(default_group_limit, "Default group limit"),
            )
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/sources?tab=topics&notice=Discovery%20policy%20saved", status_code=303)

    @app.post("/sources/hackernews")
    def update_hackernews_source(
        enabled: bool = Form(False),
        fetch_top_stories: int = Form(),
        min_score: int = Form(),
        category: str = Form(""),
    ) -> RedirectResponse:
        try:
            source_control.update_hackernews(
                enabled=enabled,
                fetch_top_stories=fetch_top_stories,
                min_score=min_score,
                category=category,
            )
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/sources?notice=Hacker%20News%20settings%20saved", status_code=303)

    @app.post("/sources/rss")
    def add_rss_source(
        name: str = Form(), url: str = Form(), category: str = Form(""), enabled: bool = Form(False)
    ) -> RedirectResponse:
        try:
            source_control.add_rss(name=name, url=url, category=category, enabled=enabled)
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/sources?notice=RSS%20feed%20added", status_code=303)

    @app.post("/sources/rss/{index}")
    def update_rss_source(
        index: int,
        name: str = Form(),
        url: str = Form(),
        category: str = Form(""),
        enabled: bool = Form(False),
    ) -> RedirectResponse:
        try:
            source_control.update_rss(
                index, name=name, url=url, category=category, enabled=enabled
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/sources?notice=RSS%20feed%20saved", status_code=303)

    @app.post("/sources/groups")
    def update_source_group(
        key: str = Form(), name: str = Form(""), categories: str = Form(), limit: int = Form()
    ) -> RedirectResponse:
        try:
            source_control.update_category_group(
                key=key, name=name, categories=categories, limit=limit
            )
        except (ConfigError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/sources?tab=topics&notice=Category%20group%20saved", status_code=303)

    @app.get("/operations", response_class=HTMLResponse)
    def operations(run: str = "") -> HTMLResponse:
        runs = store.list_automation_runs()
        latest = runs[0] if runs else None
        selected_run = store.get_automation_run(run) if run else latest
        if run and selected_run is None:
            raise HTTPException(status_code=404, detail="Automation run not found")
        available = automation_runner_factory is not None or source_control.ready
        rows = []
        for history_run in runs:
            error = (
                f"<p class='muted'>{escape(history_run.error_message)}</p>"
                if history_run.error_message
                else ""
            )
            active_class = (
                " active"
                if selected_run and selected_run.run_id == history_run.run_id
                else ""
            )
            rows.append(
                f"<a class='run-row{active_class}' href='/operations?run={quote(history_run.run_id, safe='')}'><div>"
                f"<span class='badge {escape(history_run.status.value)}'>{escape(history_run.status.value)}</span>"
                f"<p><strong>{escape(history_run.stage.replace('_', ' '))}</strong></p></div>"
                "<div>"
                f"<div class='run-counts'><span>{history_run.discovered_count} discovered</span>"
                f"<span>{history_run.imported_count} imported</span><span>{history_run.selected_count} selected</span>"
                f"<span>{history_run.drafted_count} drafted</span><span>{history_run.skipped_count} already known</span></div>"
                f"{error}</div><small class='muted'>{escape(_display_time(history_run.started_at))}</small></a>"
            )
        run_history = "".join(rows) or (
            "<div class='empty'><h2>No runs yet</h2>"
            "<p class='muted'>Run discovery when you are ready to populate the editorial queue.</p></div>"
        )
        schedule_value = "On" if automation_settings.schedule_enabled else "Off"
        draft_value = "On" if automation_settings.auto_generate else "Off"
        readiness = "Ready" if available else "Setup needed"
        latest_value = latest.status.value.replace("_", " ") if latest else "Never run"
        disabled = "" if available else " disabled"
        setup_note = (
            "The discovery configuration is ready."
            if available
            else "Create data/config.json from the CodeQuest example before running discovery."
        )
        if selected_run:
            trigger_label = _trigger_label(selected_run.trigger)
            story_link_parts = []
            for item_id in selected_run.imported_item_ids:
                item_record = store.get_item(item_id)
                label = (
                    item_record.packet.brief.working_title if item_record else item_id
                )
                story_link_parts.append(
                    f"<a class='run-story' href='/items/{quote(item_id, safe='')}'>"
                    f"{escape(label)}</a>"
                )
            story_links = "".join(story_link_parts)
            if not story_links and selected_run.imported_count:
                story_links = (
                    "<p class='muted'>This earlier run recorded its totals before item-level links were available.</p>"
                )
            run_detail = (
                "<section class='panel run-detail'><div class='run-detail-head'><div>"
                f"<span class='badge {escape(selected_run.status.value)}'>{escape(selected_run.status.value)}</span>"
                f"<h2>{escape(trigger_label)} run</h2>"
                f"<p class='muted'>Started {_display_time(selected_run.started_at)} · {_run_duration(selected_run)}</p>"
                "</div><a class='text-link' href='/editorial'>Open editorial queue →</a></div>"
                "<div class='run-counts'>"
                f"<span><strong>{selected_run.discovered_count}</strong> discovered</span>"
                f"<span><strong>{selected_run.imported_count}</strong> imported</span>"
                f"<span><strong>{selected_run.selected_count}</strong> selected</span>"
                f"<span><strong>{selected_run.drafted_count}</strong> drafted</span>"
                f"<span><strong>{selected_run.skipped_count}</strong> already known</span></div>"
                + (
                    f"<p class='muted'>{escape(selected_run.error_message)}</p>"
                    if selected_run.error_message
                    else ""
                )
                + (f"<div class='run-stories'>{story_links}</div>" if story_links else "")
                + "</section>"
            )
        else:
            run_detail = ""
        return render_page(
            "Operations",
            "<header class='page-head'><p class='eyebrow'>OPERATIONS</p>"
            "<h1>Automation you can <span class='accent'>see and stop.</span></h1>"
            "<p class='muted'>Discover and prepare work automatically without bypassing editorial approval.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Discovery</small><strong class='stat-value'>{escape(readiness)}</strong><span class='muted'>{automation_settings.max_candidates} candidates maximum</span></article>"
            f"<article class='stat-card'><small>Schedule</small><strong class='stat-value'>{escape(schedule_value)}</strong><span class='muted'>Every {automation_settings.interval_minutes} minutes when enabled</span></article>"
            f"<article class='stat-card'><small>Automatic drafting</small><strong class='stat-value'>{escape(draft_value)}</strong><span class='muted'>Human approval is always required</span></article>"
            f"<article class='stat-card'><small>Last result</small><strong class='stat-value'>{escape(latest_value.title())}</strong><span class='muted'>{len(runs)} recorded run(s)</span></article>"
            "</section><section class='layout'><article class='panel'><p class='eyebrow'>RUN CONTROL</p>"
            "<h2>Discover new candidates</h2>"
            f"<p class='muted'>{escape(setup_note)}</p>"
            "<p>This run may import and select stories. It cannot approve articles, create WordPress drafts, or send social posts.</p>"
            "<a class='text-link' href='/sources'>Review discovery sources and topic limits →</a>"
            f"<form method='post' action='/operations/run'><button class='button-approve' type='submit'{disabled}>Run discovery now</button></form>"
            "</article><article class='panel'><p class='eyebrow'>CURRENT LIMITS</p>"
            f"<h2>{automation_settings.lookback_hours}-hour lookback</h2>"
            f"<p class='muted'>Imports at most {automation_settings.max_candidates} candidates and automatically selects at most {automation_settings.auto_select_count}.</p>"
            "<p class='muted'>Scheduling and model-backed drafting remain opt-in environment settings.</p>"
            f"</article></section>{run_detail}<div class='section-head'><h2>Run history</h2>"
            "<span class='muted'>Select a run to inspect it</span></div>"
            f"<section class='panel run-list'>{run_history}</section>",
            active="operations",
        )

    @app.post("/operations/run")
    async def run_automation() -> RedirectResponse:
        if automation_runner_factory is None and not source_control.ready:
            raise HTTPException(
                status_code=409,
                detail="Discovery configuration is not ready.",
            )
        try:
            run = await automation_factory().run_once(trigger="manual-workspace")
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(
            f"/operations?run={quote(run.run_id, safe='')}", status_code=303
        )

    @app.get("/integrations", response_class=HTMLResponse)
    def integrations_center(
        tab: str = "overview", notice: str = "", error: str = ""
    ) -> HTMLResponse:
        if tab not in {"overview", "setup", "security"}:
            raise HTTPException(status_code=404, detail="Integration tab not found")
        brand = store.get_brand_profile()
        connections_by_provider = {
            profile.provider: profile for profile in store.list_brand_connections()
        }
        integrations = integration_inventory(
            discovery_config_path=active_source_path()
        )
        overridden_integrations = []
        for integration in integrations:
            try:
                provider = ConnectionProvider(integration.key)
            except ValueError:
                overridden_integrations.append(integration)
                continue
            profile = connections_by_provider.get(provider)
            if profile is None:
                overridden_integrations.append(integration)
                continue
            if provider in {ConnectionProvider.WORDPRESS, ConnectionProvider.BUFFER}:
                state = (
                    IntegrationState.GUARDED
                    if bool(profile.settings.get("dry_run", True))
                    else IntegrationState.LIVE
                )
                label = "Safe mode" if state == IntegrationState.GUARDED else "Live writes enabled"
            elif provider == ConnectionProvider.IMAGES and not bool(
                profile.settings.get("enabled", False)
            ):
                state, label = IntegrationState.OFF, "Optional · off"
            else:
                state, label = IntegrationState.READY, "Ready"
            overridden_integrations.append(
                replace(
                    integration,
                    state=state,
                    state_label=label,
                    summary=f"Using encrypted settings owned by {brand.name}.",
                )
            )
        integrations = overridden_integrations
        ready_count = sum(
            item.state in {IntegrationState.READY, IntegrationState.GUARDED}
            for item in integrations
        )
        live_count = sum(item.state == IntegrationState.LIVE for item in integrations)
        setup_count = sum(
            item.state == IntegrationState.NEEDS_SETUP for item in integrations
        )
        optional_off_count = sum(
            item.state == IntegrationState.OFF for item in integrations
        )
        state_badges = {
            IntegrationState.READY: "selected",
            IntegrationState.GUARDED: "guarded",
            IntegrationState.LIVE: "live",
            IntegrationState.OFF: "off",
            IntegrationState.NEEDS_SETUP: "needs_setup",
        }
        cards = []
        for integration in integrations:
            details = "".join(
                f"<div class='integration-detail'>{escape(detail)}</div>"
                for detail in integration.details
            )
            config_names = "".join(
                f"<code>{escape(name)}</code>"
                for name in integration.configuration_names
            )
            test_disabled = (
                " disabled"
                if integration.state
                in {IntegrationState.OFF, IntegrationState.NEEDS_SETUP}
                else ""
            )
            optional_note = " · optional" if integration.optional else ""
            brand_override = (
                "<span class='badge selected'>Brand-specific</span>"
                if integration.key in {provider.value for provider in connections_by_provider}
                else "<span class='badge'>Environment fallback</span>"
            )
            cards.append(
                "<article class='panel integration-card'>"
                "<div class='integration-card-head'><div>"
                f"<p class='eyebrow'>{escape(integration.key.upper())}{optional_note}</p>"
                f"<h2>{escape(integration.name)}</h2></div>"
                f"<span class='badge {state_badges[integration.state]}'>{escape(integration.state_label)}</span></div>"
                f"<p>{escape(integration.purpose)}</p>{brand_override}"
                f"<p class='muted'>{escape(integration.summary)}</p>"
                f"<div class='integration-details'>{details}</div>"
                "<details><summary>Administrator configuration names</summary>"
                f"<div class='tags'>{config_names}</div>"
                "<p class='muted'>Values are read from protected runtime secrets and are never shown here.</p></details>"
                f"<form method='post' action='/integrations/{integration.key}/test'>"
                f"<button class='button-revise' type='submit'{test_disabled}>{escape(integration.test_label)}</button></form>"
                "</article>"
            )
        tabs = (
            "<nav class='integration-tabs' aria-label='Integration Center sections'>"
            f"<a class='integration-tab{' active' if tab == 'overview' else ''}' href='/integrations?tab=overview'>Connections</a>"
            f"<a class='integration-tab{' active' if tab == 'setup' else ''}' href='/integrations?tab=setup'>Brand setup</a>"
            f"<a class='integration-tab{' active' if tab == 'security' else ''}' href='/integrations?tab=security'>Secrets & tenancy</a></nav>"
        )
        feedback = (
            f"<div class='config-note'>{escape(notice)}</div>" if notice else ""
        ) + (f"<div class='config-error'>{escape(error)}</div>" if error else "")
        overview_body = (
            "<div class='section-head'><div><h2>Organisation connections</h2>"
            "<p class='muted'>Configuration status only—credential values never enter the page.</p></div>"
            "<span class='muted'>Tests are read-only or configuration-only</span></div>"
            f"<section class='integration-grid'>{''.join(cards)}</section>"
        )
        security_body = (
            "<div class='layout'><section class='panel'><p class='eyebrow'>SECRET BOUNDARY</p>"
            "<h2>Credentials do not belong in editorial data</h2>"
            "<p>Brand credentials are encrypted before they enter SQLite. The encryption key remains in the protected runtime environment and is never stored with the database.</p>"
            "<div class='security-principles'>"
            "<article class='security-principle'><h3>Tenant isolation</h3><p class='muted'>Each brand has separate provider settings and encrypted credential payloads.</p></article>"
            "<article class='security-principle'><h3>Masked by design</h3><p class='muted'>The dashboard reports only whether a field exists. It does not return values, partial values, or fingerprints.</p></article>"
            "<article class='security-principle'><h3>Explicit writes</h3><p class='muted'>Connection tests do not post content. WordPress, Buffer, Discord, and paid generation retain separate action boundaries.</p></article>"
            "<article class='security-principle'><h3>Rotation ready</h3><p class='muted'>A future vault adapter can replace a credential without changing stored articles or integration records.</p></article>"
            "</div></section><aside class='stack'><section class='panel'><p class='eyebrow'>EXISTING SOLUTIONS</p>"
            "<h2>Use a vault when this becomes SaaS</h2>"
            "<p class='muted'>Recommended starting point: Infisical for a SaaS-friendly operator experience. OpenBao is a strong infrastructure-led alternative. Neither is required for local development.</p>"
            "<p><a class='text-link' href='https://github.com/Infisical/infisical' target='_blank' rel='noopener'>Review Infisical →</a></p>"
            "<p><a class='text-link' href='https://github.com/openbao/openbao' target='_blank' rel='noopener'>Review OpenBao →</a></p>"
            "</section><section class='panel'><h2>Current storage rule</h2>"
            f"<p class='muted'>{'Encrypted brand credential storage is available.' if credential_vault.ready else 'The credential vault is locked. Set WORKSPACE_SECRET_KEY before saving brand credentials.'}</p>"
            "</section></aside></div>"
        )
        def connection_settings(provider: ConnectionProvider) -> dict[str, str | bool]:
            profile = connections_by_provider.get(provider)
            return profile.settings if profile else {}

        wordpress_settings = connection_settings(ConnectionProvider.WORDPRESS)
        ollama_settings = connection_settings(ConnectionProvider.OLLAMA)
        buffer_settings = connection_settings(ConnectionProvider.BUFFER)
        image_settings = connection_settings(ConnectionProvider.IMAGES)
        secret_hint = "Leave blank to keep the saved credential" if credential_vault.ready else "Vault locked by administrator"
        disabled = "" if credential_vault.ready else " disabled"
        setup_body = (
            "<div class='section-head'><div><h2>Brand-owned connections</h2>"
            f"<p class='muted'>Settings saved here apply only to {escape(brand.name)}. Blank secret fields never erase a saved credential.</p></div>"
            f"<span class='badge {'selected' if credential_vault.ready else 'warning'}'>{'Vault ready' if credential_vault.ready else 'Vault locked'}</span></div>"
            + (f"<div class='config-error'>{escape(credential_vault_error)}</div>" if credential_vault_error else "")
            + "<section class='integration-grid'>"
            "<article class='panel'><p class='eyebrow'>WORDPRESS</p><h2>Website drafts and media</h2>"
            "<form method='post' action='/integrations/wordpress/configure'>"
            f"<input name='base_url' type='url' required value='{escape(str(wordpress_settings.get('base_url', '')), quote=True)}' placeholder='https://example.com'>"
            f"<input name='username' required value='{escape(str(wordpress_settings.get('username', '')), quote=True)}' placeholder='WordPress username'>"
            f"<input name='secret' type='password' placeholder='{escape(secret_hint, quote=True)}'{disabled}>"
            f"<label class='toggle-field'><input type='checkbox' name='dry_run' value='true'{' checked' if wordpress_settings.get('dry_run', True) else ''}> Preview only</label>"
            f"<button type='submit'{disabled}>Save WordPress connection</button></form></article>"
            "<article class='panel'><p class='eyebrow'>OLLAMA CLOUD</p><h2>Article and social writing</h2>"
            "<form method='post' action='/integrations/ollama/configure'>"
            f"<input name='base_url' type='url' required value='{escape(str(ollama_settings.get('base_url', '')), quote=True)}' placeholder='https://ollama.com'>"
            f"<input name='writer_model' required value='{escape(str(ollama_settings.get('writer_model', '')), quote=True)}' placeholder='Article model'>"
            f"<input name='social_model' value='{escape(str(ollama_settings.get('social_model', '')), quote=True)}' placeholder='Social model (optional)'>"
            f"<input name='secret' type='password' placeholder='{escape(secret_hint, quote=True)}'{disabled}>"
            f"<button type='submit'{disabled}>Save Ollama connection</button></form></article>"
            "<article class='panel'><p class='eyebrow'>BUFFER</p><h2>Social publishing channels</h2>"
            "<form method='post' action='/integrations/buffer/configure'>"
            f"<input name='linkedin_channel_id' required value='{escape(str(buffer_settings.get('linkedin_channel_id', '')), quote=True)}' placeholder='LinkedIn channel ID'>"
            f"<input name='x_channel_id' required value='{escape(str(buffer_settings.get('x_channel_id', '')), quote=True)}' placeholder='X channel ID'>"
            f"<input name='facebook_channel_id' required value='{escape(str(buffer_settings.get('facebook_channel_id', '')), quote=True)}' placeholder='Facebook channel ID'>"
            f"<input name='schedule_timezone' required value='{escape(str(buffer_settings.get('schedule_timezone', 'Africa/Johannesburg')), quote=True)}' placeholder='Africa/Johannesburg'>"
            f"<input name='secret' type='password' placeholder='{escape(secret_hint, quote=True)}'{disabled}>"
            f"<label class='toggle-field'><input type='checkbox' name='dry_run' value='true'{' checked' if buffer_settings.get('dry_run', True) else ''}> Preview only</label>"
            f"<button type='submit'{disabled}>Save Buffer connection</button></form></article>"
            "<article class='panel'><p class='eyebrow'>AI IMAGES</p><h2>Featured-image generation</h2>"
            "<form method='post' action='/integrations/images/configure'>"
            f"<input name='base_url' type='url' required value='{escape(str(image_settings.get('base_url', 'https://api.openai.com/v1')), quote=True)}' placeholder='https://api.openai.com/v1'>"
            f"<input name='image_model' required value='{escape(str(image_settings.get('model', 'gpt-image-2')), quote=True)}' placeholder='gpt-image-2'>"
            f"<input name='secret' type='password' placeholder='{escape(secret_hint, quote=True)}'{disabled}>"
            f"<label class='toggle-field'><input type='checkbox' name='enabled' value='true'{' checked' if image_settings.get('enabled', False) else ''}> Allow explicit paid generation</label>"
            f"<button type='submit'{disabled}>Save image connection</button></form></article></section>"
        )
        return render_page(
            "Integrations",
            "<header class='page-head'><p class='eyebrow'>INTEGRATIONS</p>"
            "<h1>Every connection, with its <span class='accent'>safety state.</span></h1>"
            "<p class='muted'>See what is ready, what can write externally, and what an administrator still needs to configure.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Ready or guarded</small><strong class='stat-value'>{ready_count}</strong><span class='muted'>safe to use</span></article>"
            f"<article class='stat-card'><small>Live writes</small><strong class='stat-value'>{live_count}</strong><span class='muted'>explicit actions enabled</span></article>"
            f"<article class='stat-card'><small>Needs setup</small><strong class='stat-value'>{setup_count}</strong><span class='muted'>administrator attention</span></article>"
            f"<article class='stat-card'><small>Optional services off</small><strong class='stat-value'>{optional_off_count}</strong><span class='muted'>no workflow blocker</span></article></section>"
            f"{feedback}{tabs}{overview_body if tab == 'overview' else setup_body if tab == 'setup' else security_body}",
            active="integrations",
        )

    @app.post("/integrations/{provider}/configure")
    def configure_brand_integration(
        provider: str,
        base_url: str = Form(""),
        username: str = Form(""),
        writer_model: str = Form(""),
        social_model: str = Form(""),
        linkedin_channel_id: str = Form(""),
        x_channel_id: str = Form(""),
        facebook_channel_id: str = Form(""),
        schedule_timezone: str = Form("Africa/Johannesburg"),
        image_model: str = Form("gpt-image-2"),
        dry_run: str = Form(""),
        enabled: str = Form(""),
        secret: str = Form(""),
    ) -> RedirectResponse:
        try:
            selected_provider = ConnectionProvider(provider)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Connection type not found.") from exc
        if not credential_vault.ready:
            raise HTTPException(
                status_code=409,
                detail="The credential vault must be configured before saving connections.",
            )
        if selected_provider not in {
            ConnectionProvider.WORDPRESS,
            ConnectionProvider.OLLAMA,
            ConnectionProvider.BUFFER,
            ConnectionProvider.IMAGES,
        }:
            raise HTTPException(status_code=404, detail="Connection type not found.")

        existing = store.get_brand_connection(selected_provider)
        existing_ciphertext = existing.encrypted_secrets if existing else ""
        secret_name = {
            ConnectionProvider.WORDPRESS: "application_password",
            ConnectionProvider.OLLAMA: "api_key",
            ConnectionProvider.BUFFER: "access_token",
            ConnectionProvider.IMAGES: "api_key",
        }[selected_provider]
        if not secret.strip() and not existing_ciphertext:
            raise HTTPException(
                status_code=400,
                detail="Enter the credential the first time this connection is saved.",
            )

        cleaned_base_url = base_url.strip().rstrip("/")
        if selected_provider in {
            ConnectionProvider.WORDPRESS,
            ConnectionProvider.OLLAMA,
            ConnectionProvider.IMAGES,
        }:
            parsed = urlsplit(cleaned_base_url)
            local_wordpress = (
                selected_provider == ConnectionProvider.WORDPRESS
                and parsed.hostname in {"localhost", "127.0.0.1"}
            )
            if not parsed.hostname or (parsed.scheme != "https" and not local_wordpress):
                raise HTTPException(
                    status_code=400,
                    detail="Use a secure HTTPS provider address.",
                )

        if selected_provider == ConnectionProvider.WORDPRESS:
            if not username.strip():
                raise HTTPException(status_code=400, detail="Enter the WordPress username.")
            settings: dict[str, str | bool] = {
                "base_url": cleaned_base_url,
                "username": username.strip(),
                "dry_run": dry_run == "true",
            }
        elif selected_provider == ConnectionProvider.OLLAMA:
            if not writer_model.strip():
                raise HTTPException(status_code=400, detail="Enter the article model.")
            settings = {
                "base_url": cleaned_base_url,
                "writer_model": writer_model.strip(),
                "social_model": social_model.strip(),
            }
        elif selected_provider == ConnectionProvider.BUFFER:
            channel_values = {
                "linkedin_channel_id": linkedin_channel_id.strip(),
                "x_channel_id": x_channel_id.strip(),
                "facebook_channel_id": facebook_channel_id.strip(),
            }
            if not all(channel_values.values()):
                raise HTTPException(
                    status_code=400,
                    detail="Enter the LinkedIn, X, and Facebook channel IDs.",
                )
            try:
                ZoneInfo(schedule_timezone.strip())
            except ZoneInfoNotFoundError as exc:
                raise HTTPException(
                    status_code=400,
                    detail="Enter a valid timezone such as Africa/Johannesburg.",
                ) from exc
            settings = {
                **channel_values,
                "schedule_timezone": schedule_timezone.strip(),
                "dry_run": dry_run == "true",
            }
        else:
            if not image_model.strip():
                raise HTTPException(status_code=400, detail="Enter the image model.")
            settings = {
                "provider": "openai",
                "base_url": cleaned_base_url,
                "model": image_model.strip(),
                "enabled": enabled == "true",
            }
        try:
            encrypted_secrets, secret_names = credential_vault.merge(
                existing_ciphertext,
                {secret_name: secret},
            )
            store.save_brand_connection(
                BrandConnectionProfile(
                    brand_id=store.get_active_brand_id(),
                    provider=selected_provider,
                    settings=settings,
                    encrypted_secrets=encrypted_secrets,
                    configured_secret_names=secret_names,
                )
            )
        except SecretVaultError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return RedirectResponse(
            f"/integrations?tab=setup&notice={quote(selected_provider.value.title())}%20connection%20saved",
            status_code=303,
        )

    @app.post("/integrations/{integration_key}/test")
    async def test_integration(integration_key: str) -> RedirectResponse:
        allowed = {"horizon", "ollama", "wordpress", "images", "buffer", "discord"}
        if integration_key not in allowed:
            raise HTTPException(status_code=404, detail="Integration not found")
        try:
            if integration_key == "horizon":
                source_control.load()
                message = "Horizon source configuration is valid."
            elif integration_key == "ollama":
                writer_factory()
                message = "Ollama Cloud writing configuration is valid."
            elif integration_key == "wordpress":
                categories = await publisher_factory().list_categories()
                message = f"WordPress responded successfully with {len(categories)} categories."
            elif integration_key == "images":
                image_writer_factory()
                message = "Image provider configuration is valid. No image was generated."
            elif integration_key == "buffer":
                buffer_settings_factory()
                message = "Buffer configuration is complete. No post was created."
            else:
                bot_name = await bridge_factory().test_connection()
                message = f"Discord bot {bot_name} responded successfully. No message was sent."
        except Exception as exc:
            safe_errors = (ValueError, ConfigError, DraftGenerationError, ImageGenerationError)
            detail = str(exc) if isinstance(exc, safe_errors) else (
                f"{integration_key.title()} did not respond successfully. Check its credentials and endpoint."
            )
            return RedirectResponse(
                f"/integrations?error={quote(detail, safe='')}", status_code=303
            )
        return RedirectResponse(
            f"/integrations?notice={quote(message, safe='')}", status_code=303
        )

    @app.get("/editorial", response_class=HTMLResponse)
    def inbox(status: str = "all", q: str = "") -> HTMLResponse:
        all_records = store.list_items()
        if not all_records:
            return render_page(
                "Editorial queue",
                "<section class='empty panel'><p class='eyebrow'>EDITORIAL INBOX</p>"
                "<h1>No candidates <span class='accent'>yet</span></h1><p class='muted'>Import a Horizon content item "
                "with the codequest-workspace command.</p></section>",
                active="editorial",
            )
        if status != "all" and status not in EDITORIAL_STATUSES:
            raise HTTPException(status_code=404, detail="Editorial filter not found")
        search = q.strip()
        records = [
            record
            for record in all_records
            if (status == "all" or record.status == status)
            and (
                not search
                or search.casefold()
                in " ".join(
                    [
                        record.packet.brief.working_title,
                        record.packet.brief.central_angle,
                        record.packet.brief.article_type.value,
                        record.status,
                    ]
                ).casefold()
            )
        ]
        counts = {
            candidate_status: sum(
                record.status == candidate_status for record in all_records
            )
            for candidate_status in EDITORIAL_STATUSES
        }
        filter_labels = {
            "all": "All",
            "candidate": "Candidates",
            "selected": "Selected",
            "needs_revision": "Revisions",
            "ready_for_approval": "Ready",
            "approved": "Approved",
            "archived": "Archived",
        }
        filter_tabs = "".join(
            f"<a class='filter-tab{' active' if key == status else ''}' "
            f"href='/editorial?status={key}{f'&q={quote(search)}' if search else ''}'>"
            f"{label} {len(all_records) if key == 'all' else counts[key]}</a>"
            for key, label in filter_labels.items()
        )
        rows = []
        next_labels = {
            "selected": ("Open workspace", "overview"),
            "needs_revision": ("Review revision", "review"),
            "ready_for_approval": ("Make decision", "review"),
            "approved": ("Open delivery", "delivery"),
            "archived": ("View story", "overview"),
        }
        for record in records:
            brief = record.packet.brief
            href = f"/items/{quote(brief.content_item_id, safe='')}"
            draft_count = len(store.list_drafts(brief.content_item_id))
            if record.status == "candidate":
                action = (
                    f"<form method='post' action='{href}/status'>"
                    "<input type='hidden' name='status' value='selected'>"
                    "<input type='hidden' name='return_to' value='editorial'>"
                    "<button class='compact-button' type='submit'>Select story</button></form>"
                )
            else:
                action_label, action_tab = next_labels[record.status]
                action = (
                    f"<a class='text-link' href='{href}?tab={action_tab}'>{action_label} →</a>"
                )
            rows.append(
                "<article class='queue-row'><div>"
                f"<span class='badge {escape(record.status)}'>{escape(record.status.replace('_', ' '))}</span>"
                f"<h2><a href='{href}'>{escape(brief.working_title)}</a></h2>"
                f"<p class='muted queue-summary'>{escape(brief.central_angle)}</p></div>"
                "<aside class='queue-side'><div class='meta'>"
                f"<span>{escape(brief.article_type.value.replace('_', ' '))}</span>"
                f"<span>·</span><span>{len(record.packet.evidence.sources)} sources</span>"
                f"<span>·</span><span>{draft_count} drafts</span></div>"
                f"<small class='muted'>Updated {escape(record.updated_at[:10])}</small>{action}</aside></article>"
            )
        queue_content = "".join(rows) or (
            "<div class='empty'><h2>No matching stories</h2>"
            "<p class='muted'>Try another status or clear the search.</p></div>"
        )
        return render_page(
            "Editorial queue",
            "<header class='page-head'><p class='eyebrow'>EDITORIAL INBOX</p>"
            "<h1>Find the signal. Shape the <span class='accent'>story.</span></h1>"
            "<p class='muted'>Prioritize candidates, move work forward, and find any story quickly.</p></header>"
            "<section class='queue-tools'><nav class='filter-tabs' aria-label='Editorial status filters'>"
            f"{filter_tabs}</nav><form method='get' action='/editorial'>"
            f"<input type='hidden' name='status' value='{escape(status, quote=True)}'>"
            f"<input type='search' name='q' value='{escape(search, quote=True)}' placeholder='Search stories'>"
            "<button type='submit'>Search</button></form></section>"
            f"<section class='panel queue-list'>{queue_content}</section>",
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
        return render_page(
            "Draft library",
            "<header class='page-head'><p class='eyebrow'>DRAFT LIBRARY</p>"
            "<h1>Every version, easy to <span class='accent'>find.</span></h1>"
            "<p class='muted'>Review all generated article versions without mixing them into discovery.</p></header>"
            f"<section class='grid'>{cards}</section>",
            active="drafts",
        )

    @app.get("/publishing", response_class=HTMLResponse)
    def publishing_hub(
        tab: str = "articles", notice: str = "", q: str = "", page: int = 1
    ) -> HTMLResponse:
        if tab not in {"articles", "categories", "media"}:
            raise HTTPException(status_code=404, detail="Publishing tab not found")
        categories = store.list_wordpress_categories()
        category_map = {category.category_id: category for category in categories}
        synced_at = store.wordpress_categories_synced_at()
        media = store.list_wordpress_media()
        media_map = {item.media_id: item for item in media}
        media_synced_at = store.wordpress_media_synced_at()
        if page < 1:
            raise HTTPException(status_code=404, detail="Media page not found")
        media_search = q.strip()
        filtered_media = [
            item
            for item in media
            if not media_search
            or media_search.casefold()
            in " ".join([item.title, item.filename, item.alt_text]).casefold()
        ]
        media_page_size = 24
        media_page_count = max(
            1, (len(filtered_media) + media_page_size - 1) // media_page_size
        )
        if page > media_page_count:
            raise HTTPException(status_code=404, detail="Media page not found")
        visible_media = filtered_media[
            (page - 1) * media_page_size : page * media_page_size
        ]
        try:
            wordpress_config = WordPressConfig.from_env()
            connection_ready = True
            site_label = wordpress_config.base_url
            connection_note = (
                "Connected for previews; draft delivery is disabled by dry-run mode."
                if wordpress_config.dry_run
                else "Connected for category sync and draft delivery."
            )
        except ValueError as exc:
            connection_ready = False
            site_label = "WordPress setup needed"
            connection_note = str(exc)
        rows = []
        ready_count = 0
        assigned_count = 0
        for record in store.list_items():
            content_item_id = record.packet.brief.content_item_id
            draft = store.get_latest_draft(content_item_id)
            if draft is None:
                continue
            settings = store.get_wordpress_publishing_settings(content_item_id)
            delivery = store.get_wordpress_delivery(draft.draft_id)
            selected_names = [
                category_map[category_id].name
                for category_id in settings.category_ids
                if category_id in category_map
            ]
            if selected_names:
                assigned_count += 1
            featured_image = (
                media_map.get(settings.featured_media_id)
                if settings.featured_media_id is not None
                else None
            )
            if delivery and delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
                state = "WordPress draft created"
                badge = "approved"
            elif record.status == "approved":
                state = "Ready for WordPress"
                badge = "ready_for_approval"
                ready_count += 1
            else:
                state = "Article review in progress"
                badge = record.status
            placement_text = (
                ", ".join(selected_names) if selected_names else "No categories assigned"
            )
            if featured_image:
                placement_text += f" · Image: {featured_image.title}"
            encoded_id = quote(content_item_id, safe="")
            rows.append(
                "<article class='publishing-row'><div>"
                f"<span class='badge {escape(badge)}'>{escape(state)}</span>"
                f"<h3>{escape(draft.title)}</h3><small class='muted'>Latest article version</small></div>"
                f"<div><small class='muted'>Website placement</small><p>{escape(placement_text)}</p></div>"
                f"<a class='text-link' href='/items/{encoded_id}?tab=delivery&channel=wordpress'>Configure →</a></article>"
            )
        publishing_rows = "".join(rows) or (
            "<div class='empty'><h2>No article drafts yet</h2>"
            "<p class='muted'>Drafted articles will appear here when they are ready for publishing setup.</p></div>"
        )
        taxonomy = "".join(
            f"<span class='taxonomy-item'>{escape(category.name)} <small class='muted'>({category.post_count})</small></span>"
            for category in categories
        ) or "<p class='muted'>Sync WordPress to load the categories editors can assign to articles.</p>"
        notice_html = (
            f"<div class='config-note'>{escape(notice)}</div>" if notice else ""
        )
        sync_button = (
            "<form method='post' action='/publishing/wordpress/categories/sync'>"
            "<button type='submit'>Sync WordPress categories</button></form>"
            if connection_ready
            else "<p class='muted'>Add the WordPress URL, username, and application password to enable syncing.</p>"
        )
        media_cards = "".join(
            "<article class='media-card'>"
            f"<img src='{escape(item.thumbnail_url or item.source_url, quote=True)}' alt='{escape(item.alt_text or item.title, quote=True)}' loading='lazy'>"
            f"<div class='media-card-copy'><strong>{escape(item.title)}</strong>"
            f"<small class='muted'>{escape(item.filename or item.mime_type)}</small>"
            f"<small class='muted'>{f'{item.width} × {item.height}' if item.width and item.height else 'Dimensions unavailable'}</small></div></article>"
            for item in visible_media
        ) or (
            "<section class='panel empty'><h2>No matching images</h2>"
            "<p class='muted'>Try another search or sync the WordPress media library.</p></section>"
        )
        publishing_tabs = "<nav class='publishing-tabs' aria-label='Publishing Hub sections'>" + "".join(
            f"<a class='publishing-tab{' active' if key == tab else ''}' href='/publishing?tab={key}'>{label}</a>"
            for key, label in (
                ("articles", "Articles"),
                ("categories", f"Categories ({len(categories)})"),
                ("media", f"Media library ({len(media)})"),
            )
        ) + "</nav>"
        articles_body = (
            "<div class='layout'><section class='panel'><div class='section-head'><h2>Article publishing setup</h2>"
            "<span class='muted'>Latest versions only</span></div>"
            f"<div class='publishing-list'>{publishing_rows}</div></section>"
            "<aside class='stack'><section class='panel connection-card'><p class='eyebrow'>WORDPRESS CONNECTION</p>"
            f"<h2>{escape(site_label)}</h2><p class='muted'>{escape(connection_note)}</p>"
            "<p class='muted'>Categories and featured images are configured before draft delivery.</p></section></aside></div>"
        )
        categories_body = (
            "<div class='layout'><section class='panel'><div class='section-head'><h2>Website categories</h2>"
            f"<span class='muted'>{'Synced ' + synced_at[:10] if synced_at else 'Not synced'}</span></div>"
            f"<div class='taxonomy-list'>{taxonomy}</div></section>"
            "<aside class='stack'><section class='panel connection-card'><p class='eyebrow'>REFRESH FROM WORDPRESS</p>"
            "<h2>Keep category choices current</h2><p class='muted'>Sync after categories are added, renamed, or reorganised in WordPress.</p>"
            f"{sync_button}</section></aside></div>"
        )
        upload_disabled = " disabled" if not connection_ready or wordpress_config.dry_run else ""
        upload_note = (
            "Media upload is disabled while WordPress dry-run mode is on. Existing images can still be synced and selected."
            if connection_ready and wordpress_config.dry_run
            else "JPEG, PNG, WebP, or GIF · maximum 10 MB. Uploading adds the image to WordPress but does not publish an article."
        )
        media_sync = (
            "<form method='post' action='/publishing/wordpress/media/sync'><button type='submit'>Sync media library</button></form>"
            if connection_ready
            else "<p class='muted'>Configure WordPress before syncing media.</p>"
        )
        media_pagination = "".join(
            f"<a class='filter-tab{' active' if number == page else ''}' href='/publishing?tab=media&page={number}{'&q=' + quote(media_search) if media_search else ''}'>{number}</a>"
            for number in range(1, media_page_count + 1)
        )
        media_body = (
            "<div class='section-head'><div><h2>WordPress images</h2>"
            f"<p class='muted'>{len(filtered_media)} matching · {'synced ' + media_synced_at[:10] if media_synced_at else 'not synced yet'}</p></div>{media_sync}</div>"
            "<form class='media-search' method='get' action='/publishing'><input type='hidden' name='tab' value='media'>"
            f"<input type='search' name='q' value='{escape(media_search, quote=True)}' placeholder='Search image title, filename, or alt text'>"
            "<button type='submit'>Search media</button></form>"
            "<div class='layout'><section>"
            f"<div class='media-grid'>{media_cards}</div><nav class='filter-tabs' aria-label='Media pages'>{media_pagination}</nav></section>"
            "<aside class='stack'><section class='panel upload-panel'><p class='eyebrow'>UPLOAD IMAGE</p>"
            "<h2>Add to WordPress media</h2>"
            f"<p class='muted'>{escape(upload_note)}</p>"
            "<form method='post' action='/publishing/wordpress/media/upload' enctype='multipart/form-data'>"
            "<input type='file' name='image' accept='image/jpeg,image/png,image/webp,image/gif' required>"
            "<input name='title' required placeholder='Image title'>"
            "<textarea name='alt_text' required placeholder='Describe the image for accessibility'></textarea>"
            f"<button type='submit'{upload_disabled}>Upload to WordPress</button></form></section></aside></div>"
        )
        return render_page(
            "Publishing Hub",
            "<header class='page-head'><p class='eyebrow'>PUBLISHING HUB</p>"
            "<h1>Prepare every article for its <span class='accent'>destination.</span></h1>"
            "<p class='muted'>Choose website metadata before creating a WordPress draft. Publishing remains a separate manual decision.</p></header>"
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Article drafts</small><strong class='stat-value'>{len(rows)}</strong><span class='muted'>available for setup</span></article>"
            f"<article class='stat-card'><small>Ready to deliver</small><strong class='stat-value'>{ready_count}</strong><span class='muted'>approved article(s)</span></article>"
            f"<article class='stat-card'><small>Category assignments</small><strong class='stat-value'>{assigned_count}</strong><span class='muted'>configured article(s)</span></article>"
            f"<article class='stat-card'><small>WordPress assets</small><strong class='stat-value'>{len(categories) + len(media)}</strong><span class='muted'>{len(categories)} categories · {len(media)} images</span></article></section>"
            f"{notice_html}{publishing_tabs}"
            f"{articles_body if tab == 'articles' else categories_body if tab == 'categories' else media_body}",
            active="publishing",
        )

    @app.post("/publishing/wordpress/categories/sync")
    async def sync_wordpress_categories() -> RedirectResponse:
        try:
            categories = await publisher_factory().list_categories()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="WordPress categories could not be loaded. Check the connection and credentials.",
            ) from exc
        store.replace_wordpress_categories(categories)
        return RedirectResponse(
            f"/publishing?tab=categories&notice={len(categories)}%20WordPress%20categories%20synced",
            status_code=303,
        )

    @app.post("/publishing/wordpress/media/sync")
    async def sync_wordpress_media() -> RedirectResponse:
        try:
            media = await publisher_factory().list_media()
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="The WordPress media library could not be loaded. Check the connection and credentials.",
            ) from exc
        store.replace_wordpress_media(media)
        return RedirectResponse(
            f"/publishing?tab=media&notice={len(media)}%20WordPress%20images%20synced",
            status_code=303,
        )

    @app.post("/publishing/wordpress/media/upload")
    async def upload_wordpress_media(
        image: UploadFile = File(),
        title: str = Form(),
        alt_text: str = Form(),
    ) -> RedirectResponse:
        allowed_types = {"image/jpeg", "image/png", "image/webp", "image/gif"}
        content_type = (image.content_type or "").lower()
        if content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail="Choose a JPEG, PNG, WebP, or GIF image.",
            )
        clean_title = title.strip()
        clean_alt_text = alt_text.strip()
        if not clean_title or not clean_alt_text:
            raise HTTPException(
                status_code=400,
                detail="An image title and accessibility description are required.",
            )
        filename = "".join(
            character
            for character in Path(image.filename or "uploaded-image").name
            if character.isalnum() or character in {".", "-", "_"}
        ) or "uploaded-image"
        content = await image.read(10 * 1024 * 1024 + 1)
        await image.close()
        if not content or len(content) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="The image must be larger than 0 bytes and no more than 10 MB.",
            )
        try:
            validate_image_upload(content_type, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            item = await publisher_factory().upload_media(
                filename=filename,
                content_type=content_type,
                content=content,
                title=clean_title,
                alt_text=clean_alt_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="The image could not be uploaded to WordPress.",
            ) from exc
        store.upsert_wordpress_media(item)
        return RedirectResponse(
            "/publishing?tab=media&notice=Image%20uploaded%20to%20WordPress",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/images/generate")
    async def generate_featured_image(
        content_item_id: str,
        creative_direction: str = Form(""),
        style: str = Form("editorial illustration"),
        size: str = Form("1536x1024"),
        quality: str = Form("medium"),
    ) -> RedirectResponse:
        record = store.get_item(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        draft = store.get_latest_draft(content_item_id)
        if draft is None:
            raise HTTPException(status_code=409, detail="Generate an article draft first.")
        if len(creative_direction.strip()) > 1_200:
            raise HTTPException(
                status_code=400,
                detail="Creative direction must be 1,200 characters or fewer.",
            )
        if size not in SUPPORTED_IMAGE_SIZES or quality not in SUPPORTED_IMAGE_QUALITIES:
            raise HTTPException(status_code=400, detail="Choose a supported size and quality.")
        try:
            visual_profile = store.get_visual_brand_profile(record.brand_id)
            visual_feedback = store.list_visual_feedback(record.brand_id)[:12]
            prompt = build_featured_image_prompt(
                draft,
                creative_direction=creative_direction,
                style=style,
                visual_profile=visual_profile,
                visual_feedback=visual_feedback,
            )
            generator = image_writer_factory()
            result = await generator.generate(
                prompt=prompt,
                size=size,
                quality=quality,
            )
            validate_image_upload(result.mime_type, result.image_bytes)
        except ImageGenerationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="The image provider could not generate a candidate. No image was saved.",
            ) from exc
        asset = GeneratedImageAsset(
            content_item_id=content_item_id,
            draft_id=draft.draft_id,
            provider=generator.provider_name,
            model=generator.model_name,
            prompt=result.prompt,
            creative_direction=creative_direction.strip(),
            style=style,
            size=size,
            quality=quality,
            mime_type=result.mime_type,
            filename="pending.png",
            file_path="pending.png",
            alt_text=f"Editorial illustration for {draft.title}",
            visual_profile_snapshot=visual_profile,
            visual_feedback_snapshot=visual_feedback,
        )
        filename = f"{asset.asset_id}.png"
        asset.filename = filename
        asset.file_path = filename
        image_output_dir.mkdir(parents=True, exist_ok=True)
        (image_output_dir / filename).write_bytes(result.image_bytes)
        try:
            store.save_generated_image(asset)
        except Exception:
            (image_output_dir / filename).unlink(missing_ok=True)
            raise
        encoded_id = quote(content_item_id, safe="")
        return RedirectResponse(
            f"/items/{encoded_id}?tab=delivery&channel=images&notice=Image%20candidate%20generated",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/images/{asset_id}/feedback")
    def add_generated_image_feedback(
        content_item_id: str,
        asset_id: str,
        signal: str = Form(),
        dimension: str = Form(),
        note: str = Form(),
    ) -> RedirectResponse:
        record = store.get_item(content_item_id)
        asset = store.get_generated_image(asset_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        if asset is None or asset.content_item_id != content_item_id:
            raise HTTPException(status_code=404, detail="Generated image not found")
        if dimension not in {"style", "composition", "colour", "subject"}:
            raise HTTPException(status_code=400, detail="Choose a supported visual feedback area.")
        try:
            store.add_visual_feedback(
                VisualPreferenceFeedback(
                    brand_id=record.brand_id,
                    asset_id=asset_id,
                    signal=PreferenceSignal(signal),
                    dimension=dimension,
                    note=note.strip(),
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=images&notice=Visual%20feedback%20saved",
            status_code=303,
        )

    @app.get("/generated-images/{asset_id}")
    def generated_image_file(asset_id: str) -> FileResponse:
        asset = store.get_generated_image(asset_id)
        if asset is None or store.get_item(asset.content_item_id) is None:
            raise HTTPException(status_code=404, detail="Generated image not found")
        safe_name = Path(asset.file_path).name
        file_path = (image_output_dir / safe_name).resolve()
        if file_path.parent != image_output_dir.resolve() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Generated image file not found")
        return FileResponse(file_path, media_type=asset.mime_type)

    @app.post("/items/{content_item_id}/images/{asset_id}/wordpress")
    async def upload_generated_image_to_wordpress(
        content_item_id: str,
        asset_id: str,
        alt_text: str = Form(),
    ) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        asset = store.get_generated_image(asset_id)
        if asset is None or asset.content_item_id != content_item_id:
            raise HTTPException(status_code=404, detail="Generated image not found")
        if asset.draft_id != draft.draft_id:
            raise HTTPException(
                status_code=409,
                detail="Generate a new image for the latest approved article version.",
            )
        delivery = store.get_wordpress_delivery(draft.draft_id)
        if delivery and delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
            raise HTTPException(
                status_code=409,
                detail="This WordPress draft already exists. Change its image in WordPress.",
            )
        cleaned_alt_text = alt_text.strip()
        if not cleaned_alt_text:
            raise HTTPException(
                status_code=400,
                detail="An accessibility description is required.",
            )
        file_path = (image_output_dir / Path(asset.file_path).name).resolve()
        if file_path.parent != image_output_dir.resolve() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="Generated image file not found")
        content = file_path.read_bytes()
        try:
            validate_image_upload(asset.mime_type, content)
            media_item = await publisher_factory().upload_media(
                filename=asset.filename,
                content_type=asset.mime_type,
                content=content,
                title=f"{draft.title} featured image",
                alt_text=cleaned_alt_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="The generated image could not be uploaded to WordPress.",
            ) from exc
        store.upsert_wordpress_media(media_item)
        settings = store.get_wordpress_publishing_settings(content_item_id)
        settings.featured_media_id = media_item.media_id
        store.save_wordpress_publishing_settings(settings)
        asset.alt_text = cleaned_alt_text
        asset.wordpress_media_id = media_item.media_id
        store.save_generated_image(asset)
        encoded_id = quote(content_item_id, safe="")
        return RedirectResponse(
            f"/items/{encoded_id}?tab=delivery&channel=images&notice=Generated%20image%20selected",
            status_code=303,
        )

    @app.post("/items/{content_item_id}/wordpress/settings")
    def save_wordpress_settings(
        content_item_id: str,
        category_id: list[int] = Form(default=[]),
        featured_media_id: int = Form(0),
    ) -> RedirectResponse:
        draft = store.get_latest_draft(content_item_id)
        if draft is None:
            raise HTTPException(status_code=409, detail="Generate an article draft first.")
        delivery = store.get_wordpress_delivery(draft.draft_id)
        if delivery and delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
            raise HTTPException(
                status_code=409,
                detail="This version already exists in WordPress and its categories are locked here.",
            )
        try:
            store.save_wordpress_publishing_settings(
                WordPressPublishingSettings(
                    content_item_id=content_item_id,
                    category_ids=category_id,
                    featured_media_id=featured_media_id or None,
                )
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=wordpress",
            status_code=303,
        )

    @app.get("/preferences", response_class=HTMLResponse)
    def preferences(
        tab: str = "overview",
        preview_type: str = "news_report",
        media_q: str = "",
    ) -> HTMLResponse:
        allowed_tabs = {"overview", "visual", "article", "social", "learning"}
        if tab not in allowed_tabs:
            raise HTTPException(status_code=404, detail="Brand Brain tab not found")
        try:
            selected_preview_type = ArticleType(preview_type)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Article type not found") from exc

        brand = store.get_brand_profile()
        visual_profile = store.get_visual_brand_profile(brand.brand_id)
        wordpress_media = store.list_wordpress_media()
        all_rules = store.list_brand_rules()
        active_rules = [rule for rule in all_rules if rule.enabled]
        feedback_signals = store.list_feedback_signals()
        profile_fields = [
            brand.description,
            brand.audience,
            brand.positioning,
            brand.voice_summary,
            brand.default_cta,
            brand.website_url,
            brand.prohibited_terms,
        ]
        completeness = round(
            100 * sum(bool(value) for value in profile_fields) / len(profile_fields)
        )
        article_coverage = len(
            {rule.article_type for rule in active_rules if rule.article_type is not None}
        )
        social_coverage = len(
            {
                rule.channel
                for rule in active_rules
                if rule.channel != BrandRuleChannel.ARTICLE
            }
        )
        stats = (
            "<section class='stat-grid'>"
            f"<article class='stat-card'><small>Brand profile</small><strong class='stat-value'>{completeness}%</strong><span class='muted'>context completed</span></article>"
            f"<article class='stat-card'><small>Active rules</small><strong class='stat-value'>{len(active_rules)}</strong><span class='muted'>approved constraints</span></article>"
            f"<article class='stat-card'><small>Learning inbox</small><strong class='stat-value'>{len(feedback_signals)}</strong><span class='muted'>traceable signals</span></article>"
            f"<article class='stat-card'><small>Coverage</small><strong class='stat-value'>{article_coverage + social_coverage}</strong><span class='muted'>article types and social channels</span></article>"
            "</section>"
        )
        tabs = "".join(
            f"<a class='brain-tab{' active' if key == tab else ''}' href='/preferences?tab={key}'>"
            f"{label}</a>"
            for key, label in (
                ("overview", "Overview"),
                ("visual", "Visual identity"),
                ("article", "Article voice"),
                ("social", "Social voice"),
                ("learning", "Learning inbox"),
            )
        )

        def article_type_options(selected: ArticleType | None) -> str:
            options = "<option value=''>All article types</option>"
            return options + "".join(
                f"<option value='{article_type.value}'"
                f"{' selected' if selected == article_type else ''}>"
                f"{escape(article_type.value.replace('_', ' ').title())}</option>"
                for article_type in ArticleType
            )

        def signal_options(selected: PreferenceSignal) -> str:
            return "".join(
                f"<option value='{signal.value}'"
                f"{' selected' if selected == signal else ''}>{signal.value.title()}</option>"
                for signal in PreferenceSignal
            )

        def dimension_options(selected: str) -> str:
            return "".join(
                f"<option value='{dimension}'"
                f"{' selected' if selected == dimension else ''}>"
                f"{escape(dimension.replace('_', ' ').title())}</option>"
                for dimension in FEEDBACK_DIMENSIONS
            )

        def rule_editor(rule: BrandRule) -> str:
            type_field = (
                f"<select name='article_type'>{article_type_options(rule.article_type)}</select>"
                if rule.channel == BrandRuleChannel.ARTICLE
                else "<input type='hidden' name='article_type' value=''>"
            )
            return (
                f"<article class='brain-rule{' disabled' if not rule.enabled else ''}'>"
                "<div class='brain-rule-head'><div>"
                f"<span class='badge {escape(rule.signal.value)}'>{escape(rule.signal.value)}</span> "
                f"<span class='badge'>{escape(rule.channel.value)}</span></div>"
                f"<small class='muted'>Priority {rule.priority} · {escape(rule.source.replace('_', ' '))}</small></div>"
                f"<form method='post' action='/preferences/rules/{quote(rule.rule_id, safe='')}'>"
                f"<select name='signal'>{signal_options(rule.signal)}</select>"
                f"<select name='dimension'>{dimension_options(rule.dimension)}</select>"
                f"<input type='number' name='priority' min='0' max='100' value='{rule.priority}'>"
                f"{type_field}"
                f"<textarea class='rule-instruction' name='instruction' required>{escape(rule.instruction)}</textarea>"
                "<label class='rule-enabled'>"
                f"<input type='checkbox' name='enabled' value='true'{' checked' if rule.enabled else ''}> Active</label>"
                f"<input type='hidden' name='return_tab' value='{tab}'>"
                "<button class='button-revise' type='submit'>Save rule</button></form></article>"
            )

        preview_profile = build_preference_profile(store, selected_preview_type)
        preview_options = "".join(
            f"<option value='{article_type.value}'"
            f"{' selected' if article_type == selected_preview_type else ''}>"
            f"{escape(article_type.value.replace('_', ' ').title())}</option>"
            for article_type in ArticleType
        )
        overview_body = (
            "<div class='layout'><section class='stack'>"
            "<article class='panel'><p class='eyebrow'>APPROVED BRAND CONTEXT</p>"
            f"<h2>{escape(brand.name)} profile</h2>"
            "<form class='profile-grid' method='post' action='/preferences/profile'>"
            f"<label><span class='field-label'>Brand name</span><input name='name' required value='{escape(brand.name, quote=True)}'></label>"
            f"<label><span class='field-label'>Website</span><input name='website_url' value='{escape(brand.website_url, quote=True)}' placeholder='https://example.com'></label>"
            f"<label class='span-2'><span class='field-label'>Brand description</span><textarea name='description' placeholder='What the organisation does and why it exists'>{escape(brand.description)}</textarea></label>"
            f"<label><span class='field-label'>Primary audience</span><textarea name='audience'>{escape(brand.audience)}</textarea></label>"
            f"<label><span class='field-label'>Positioning</span><textarea name='positioning'>{escape(brand.positioning)}</textarea></label>"
            f"<label class='span-2'><span class='field-label'>Voice summary</span><textarea name='voice_summary' placeholder='Practical, evidence-led, direct...'>{escape(brand.voice_summary)}</textarea></label>"
            f"<label><span class='field-label'>Default call to action</span><textarea name='default_cta'>{escape(brand.default_cta)}</textarea></label>"
            f"<label><span class='field-label'>Prohibited terms</span><textarea name='prohibited_terms' placeholder='One per line'>{escape(chr(10).join(brand.prohibited_terms))}</textarea></label>"
            f"<label><span class='field-label'>Default language</span><input name='default_language' value='{escape(brand.default_language, quote=True)}'></label>"
            "<button class='button-approve span-2' type='submit'>Save brand profile</button></form></article>"
            "</section><aside class='stack'><article class='panel'>"
            "<p class='eyebrow'>GENERATION PREVIEW</p><h2>What the writer receives</h2>"
            "<form method='get' action='/preferences'><input type='hidden' name='tab' value='overview'>"
            f"<select name='preview_type'>{preview_options}</select>"
            "<button class='button-revise' type='submit'>Preview article type</button></form>"
            f"<pre class='memory-preview'>{escape(preview_profile.writer_instructions())}</pre>"
            "</article><article class='panel'><h2>Controlled learning</h2>"
            "<p class='muted'>Story feedback remains a signal until you promote it. Approved rules can be edited or paused at any time.</p>"
            "<a class='text-link' href='/preferences?tab=learning'>Review learning inbox →</a>"
            "</article></aside></div>"
        )

        article_rules = [
            rule for rule in all_rules if rule.channel == BrandRuleChannel.ARTICLE
        ]
        article_rules_html = "".join(rule_editor(rule) for rule in article_rules) or (
            "<div class='empty'><h2>No approved article rules</h2>"
            "<p class='muted'>Add the first rule or promote feedback from the learning inbox.</p></div>"
        )
        article_body = (
            "<div class='layout'><section class='brain-rule-list'>"
            f"{article_rules_html}</section><aside class='panel'><p class='eyebrow'>NEW ARTICLE RULE</p>"
            "<h2>Add an approved constraint</h2><form method='post' action='/preferences/rules'>"
            "<input type='hidden' name='channel' value='article'>"
            "<select name='signal'><option value='prefer'>Prefer</option><option value='avoid'>Avoid</option></select>"
            f"<select name='dimension'>{dimension_options('general')}</select>"
            f"<select name='article_type'>{article_type_options(None)}</select>"
            "<input type='number' name='priority' min='0' max='100' value='50'>"
            "<textarea name='instruction' required placeholder='Write one clear, testable instruction'></textarea>"
            "<input type='hidden' name='return_tab' value='article'>"
            "<button type='submit'>Add article rule</button></form></aside></div>"
        )

        reference_by_id = {item.media_id: item for item in wordpress_media}
        reference_cards = "".join(
            "<article class='visual-reference'>"
            f"<img src='{escape(reference_by_id[media_id].thumbnail_url or reference_by_id[media_id].source_url, quote=True)}' alt='{escape(reference_by_id[media_id].alt_text or reference_by_id[media_id].title, quote=True)}'>"
            f"<div><strong>{escape(reference_by_id[media_id].title)}</strong>"
            f"<form method='post' action='/preferences/visual/references/{media_id}/remove'>"
            "<button class='button-revise' type='submit'>Remove reference</button></form></div></article>"
            for media_id in visual_profile.reference_media_ids
            if media_id in reference_by_id
        ) or "<p class='muted'>No reference images selected yet.</p>"
        media_query = media_q.strip().lower()
        available_media = [
            item for item in wordpress_media
            if item.media_id not in visual_profile.reference_media_ids
            and (
                not media_query
                or media_query in item.title.lower()
                or media_query in item.filename.lower()
                or media_query in item.alt_text.lower()
            )
        ]
        available_media = available_media[:40]
        reference_options = "".join(
            f"<option value='{item.media_id}'>{escape(item.title)} · media #{item.media_id}</option>"
            for item in available_media
        )
        visual_body = (
            "<div class='layout'><section class='stack'><article class='panel'>"
            "<p class='eyebrow'>APPROVED VISUAL DIRECTION</p><h2>How the brand should look</h2>"
            "<p class='muted'>These are instructions, not automatic decisions. Every future image candidate records the version used.</p>"
            "<form class='profile-grid' method='post' action='/preferences/visual'>"
            f"<label class='span-2'><span class='field-label'>Overall visual direction</span><textarea name='visual_summary' placeholder='Describe the recognisable visual feel of the brand'>{escape(visual_profile.visual_summary)}</textarea></label>"
            f"<label><span class='field-label'>Primary colour</span><input name='primary_color' value='{escape(visual_profile.primary_color, quote=True)}' placeholder='#111827'></label>"
            f"<label><span class='field-label'>Accent colour</span><input name='accent_color' value='{escape(visual_profile.accent_color, quote=True)}' placeholder='#F47A2A'></label>"
            f"<label><span class='field-label'>Background colour</span><input name='background_color' value='{escape(visual_profile.background_color, quote=True)}' placeholder='#FFFFFF'></label>"
            f"<label><span class='field-label'>People and faces</span><select name='people_policy'>" + "".join(
                f"<option{' selected' if option == visual_profile.people_policy else ''}>{escape(option)}</option>"
                for option in ("Only when people meaningfully support the story", "Prefer people and human moments", "Avoid people and faces")
            ) + "</select></label>"
            f"<label><span class='field-label'>Preferred styles</span><textarea name='preferred_styles' placeholder='One per line'>{escape(chr(10).join(visual_profile.preferred_styles))}</textarea></label>"
            f"<label><span class='field-label'>Styles to avoid</span><textarea name='avoided_styles' placeholder='One per line'>{escape(chr(10).join(visual_profile.avoided_styles))}</textarea></label>"
            f"<label class='span-2'><span class='field-label'>Composition rules</span><textarea name='composition_rules' placeholder='One clear rule per line'>{escape(chr(10).join(visual_profile.composition_rules))}</textarea></label>"
            f"<label class='span-2'><span class='field-label'>Text inside images</span><select name='text_policy'>" + "".join(
                f"<option{' selected' if option == visual_profile.text_policy else ''}>{escape(option)}</option>"
                for option in ("No readable text in generated images", "Allow short editorial labels when essential", "Allow headline text")
            ) + "</select></label>"
            "<button class='button-approve span-2' type='submit'>Save visual identity</button></form></article>"
            "<article class='panel'><h2>Reference image library</h2>"
            "<p class='muted'>Choose strong examples from the synced WordPress media library. They document the look you want; the app does not copy or publish them automatically.</p>"
            f"<div class='visual-reference-grid'>{reference_cards}</div>"
            "<form class='media-search' method='get' action='/preferences'>"
            "<input type='hidden' name='tab' value='visual'>"
            f"<input type='search' name='media_q' value='{escape(media_q, quote=True)}' placeholder='Search the media library by title or filename'>"
            "<button class='button-revise' type='submit'>Find images</button></form>"
            + (
                "<form method='post' action='/preferences/visual/references'><label><span class='field-label'>Add a reference</span>"
                f"<select name='media_id' required><option value=''>Choose an image…</option>{reference_options}</select></label>"
                "<button type='submit'>Add reference image</button></form>"
                if reference_options else "<p class='muted'>No matching images. Try a different search or sync the WordPress media library.</p>"
            )
            + "</article></section><aside class='stack'><article class='panel'><p class='eyebrow'>PROMPT PREVIEW</p>"
            "<h2>What the image generator receives</h2>"
            f"<pre class='memory-preview'>{escape(visual_profile.prompt_context())}</pre>"
            "</article><article class='panel'><h2>How visual learning works</h2>"
            "<p class='muted'>On each generated candidate, record what should be repeated or avoided. Recent reviewed feedback then guides the next prompt and remains visible in its snapshot.</p>"
            "</article></aside></div>"
        )

        social_rules = [
            rule for rule in all_rules if rule.channel != BrandRuleChannel.ARTICLE
        ]
        social_rules_html = "".join(rule_editor(rule) for rule in social_rules) or (
            "<div class='empty'><h2>No approved social rules</h2>"
            "<p class='muted'>Create platform-specific voice constraints for future campaigns.</p></div>"
        )
        social_channel_options = "".join(
            f"<option value='{channel.value}'>{escape(channel.value.title())}</option>"
            for channel in BrandRuleChannel
            if channel != BrandRuleChannel.ARTICLE
        )
        social_body = (
            "<div class='layout'><section class='brain-rule-list'>"
            f"{social_rules_html}</section><aside class='panel'><p class='eyebrow'>NEW SOCIAL RULE</p>"
            "<h2>Add a platform constraint</h2><form method='post' action='/preferences/rules'>"
            f"<select name='channel'>{social_channel_options}</select>"
            "<select name='signal'><option value='prefer'>Prefer</option><option value='avoid'>Avoid</option></select>"
            f"<select name='dimension'>{dimension_options('tone')}</select>"
            "<input type='hidden' name='article_type' value=''>"
            "<input type='number' name='priority' min='0' max='100' value='50'>"
            "<textarea name='instruction' required placeholder='For example: Open LinkedIn posts with a concrete developer consequence.'></textarea>"
            "<input type='hidden' name='return_tab' value='social'>"
            "<button type='submit'>Add social rule</button></form></aside></div>"
        )

        approved_sources = {rule.source for rule in all_rules}
        learning_html = "".join(
            "<article class='learning-signal'>"
            f"<span class='badge {escape(str(signal['signal']))}'>{escape(str(signal['signal']))}</span> "
            f"<span class='badge'>{escape(str(signal['dimension']).replace('_', ' '))}</span> "
            f"<span class='muted'>{escape(str(signal['scope']).replace('_', ' '))}</span>"
            f"<p>{escape(str(signal['note']))}</p>"
            f"<small class='muted'>From <a href='/items/{quote(str(signal['content_item_id']), safe='')}?tab=learning'>"
            f"{escape(str(signal['story_title']))}</a> · {escape(str(signal['created_at'])[:10])}</small>"
            + (
                "<p><span class='badge pass'>Approved rule</span></p>"
                if f"feedback:{signal['feedback_id']}" in approved_sources
                else (
                    f"<form class='promote-form' method='post' action='/preferences/feedback/{signal['feedback_id']}/promote'>"
                    "<select name='rule_scope'><option value='brand'>Brand-wide</option>"
                    "<option value='article_type'>This article type</option></select>"
                    "<button type='submit'>Promote to rule</button></form>"
                )
            )
            + "</article>"
            for signal in feedback_signals
        ) or (
            "<div class='empty'><h2>No learning signals yet</h2>"
            "<p class='muted'>Feedback from story Learning tabs will appear here for review.</p></div>"
        )
        learning_body = (
            "<div class='layout'><section class='panel'><p class='eyebrow'>HUMAN-REVIEWED LEARNING</p>"
            f"<h2>{len(feedback_signals)} feedback signal(s)</h2>{learning_html}</section>"
            "<aside class='panel'><h2>How this improves generation</h2>"
            "<p class='muted'>Promoted signals become approved rules. Repeated editing patterns can later create suggestions here, but never change the brand automatically.</p>"
            "</aside></div>"
        )
        bodies = {
            "overview": overview_body,
            "visual": visual_body,
            "article": article_body,
            "social": social_body,
            "learning": learning_body,
        }
        return render_page(
            "Brand Brain",
            "<header class='page-head'><p class='eyebrow'>BRAND BRAIN</p>"
            "<h1>Teach the system how your <span class='accent'>brand thinks.</span></h1>"
            "<p class='muted'>Approved brand context and granular rules shape future articles and social campaigns. Learning remains transparent and reversible.</p></header>"
            f"{stats}<nav class='brain-tabs' aria-label='Brand Brain sections'>{tabs}</nav>"
            f"{bodies[tab]}",
            active="memory",
        )

    @app.post("/preferences/profile")
    def update_brand_profile(
        name: str = Form(),
        website_url: str = Form(""),
        description: str = Form(""),
        audience: str = Form(""),
        positioning: str = Form(""),
        voice_summary: str = Form(""),
        default_cta: str = Form(""),
        prohibited_terms: str = Form(""),
        default_language: str = Form("en"),
    ) -> RedirectResponse:
        current = store.get_brand_profile()
        terms = [
            term.strip()
            for term in prohibited_terms.replace(",", "\n").splitlines()
            if term.strip()
        ]
        cleaned_language = default_language.strip() or "en"
        if not cleaned_language.replace("-", "").replace("_", "").isalnum():
            raise HTTPException(status_code=400, detail="Use a valid language code.")
        try:
            profile = BrandProfile(
                brand_id=current.brand_id,
                organization_id=current.organization_id,
                name=name.strip(),
                website_url=website_url.strip(),
                description=description.strip(),
                audience=audience.strip(),
                positioning=positioning.strip(),
                voice_summary=voice_summary.strip(),
                default_cta=default_cta.strip(),
                prohibited_terms=list(dict.fromkeys(terms)),
                default_language=cleaned_language,
                created_at=current.created_at,
                updated_at=current.updated_at,
            )
            store.save_brand_profile(profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/preferences?tab=overview", status_code=303)

    @app.post("/preferences/rules")
    def create_brand_rule(
        channel: str = Form(),
        signal: str = Form(),
        dimension: str = Form(),
        instruction: str = Form(),
        article_type: str = Form(""),
        priority: int = Form(50),
        return_tab: str = Form("article"),
    ) -> RedirectResponse:
        if return_tab not in {"article", "social"}:
            raise HTTPException(status_code=400, detail="Invalid Brand Brain destination.")
        if dimension not in FEEDBACK_DIMENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported rule dimension.")
        try:
            parsed_channel = BrandRuleChannel(channel)
            parsed_type = ArticleType(article_type) if article_type else None
            if parsed_channel != BrandRuleChannel.ARTICLE:
                parsed_type = None
            rule = BrandRule(
                brand_id=store.get_active_brand_id(),
                channel=parsed_channel,
                signal=PreferenceSignal(signal),
                dimension=dimension,
                instruction=instruction.strip(),
                article_type=parsed_type,
                priority=priority,
            )
            store.add_brand_rule(rule)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/preferences?tab={return_tab}", status_code=303)

    @app.post("/preferences/visual")
    def update_visual_brand_profile(
        visual_summary: str = Form(""),
        primary_color: str = Form(""),
        accent_color: str = Form(""),
        background_color: str = Form(""),
        preferred_styles: str = Form(""),
        avoided_styles: str = Form(""),
        composition_rules: str = Form(""),
        people_policy: str = Form(),
        text_policy: str = Form(),
    ) -> RedirectResponse:
        current = store.get_visual_brand_profile()
        colours = [primary_color.strip(), accent_color.strip(), background_color.strip()]
        if any(
            colour and (len(colour) != 7 or not colour.startswith("#") or
                        any(character not in "0123456789abcdefABCDEF" for character in colour[1:]))
            for colour in colours
        ):
            raise HTTPException(status_code=400, detail="Use six-digit colours such as #F47A2A.")

        def lines(value: str) -> list[str]:
            return list(dict.fromkeys(line.strip() for line in value.splitlines() if line.strip()))

        try:
            store.save_visual_brand_profile(
                VisualBrandProfile(
                    brand_id=current.brand_id,
                    visual_summary=visual_summary.strip(),
                    primary_color=colours[0].upper(),
                    accent_color=colours[1].upper(),
                    background_color=colours[2].upper(),
                    preferred_styles=lines(preferred_styles),
                    avoided_styles=lines(avoided_styles),
                    composition_rules=lines(composition_rules),
                    people_policy=people_policy.strip(),
                    text_policy=text_policy.strip(),
                    reference_media_ids=current.reference_media_ids,
                    updated_at=current.updated_at,
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/preferences?tab=visual&notice=Visual%20identity%20saved", status_code=303)

    @app.post("/preferences/visual/references")
    def add_visual_reference(media_id: int = Form()) -> RedirectResponse:
        profile = store.get_visual_brand_profile()
        profile.reference_media_ids.append(media_id)
        try:
            store.save_visual_brand_profile(profile)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/preferences?tab=visual&notice=Reference%20image%20added", status_code=303)

    @app.post("/preferences/visual/references/{media_id}/remove")
    def remove_visual_reference(media_id: int) -> RedirectResponse:
        profile = store.get_visual_brand_profile()
        profile.reference_media_ids = [item for item in profile.reference_media_ids if item != media_id]
        store.save_visual_brand_profile(profile)
        return RedirectResponse("/preferences?tab=visual&notice=Reference%20image%20removed", status_code=303)

    @app.post("/preferences/rules/{rule_id}")
    def update_brand_rule(
        rule_id: str,
        signal: str = Form(),
        dimension: str = Form(),
        instruction: str = Form(),
        article_type: str = Form(""),
        priority: int = Form(50),
        enabled: str = Form(""),
        return_tab: str = Form("article"),
    ) -> RedirectResponse:
        rule = store.get_brand_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Brand rule not found")
        if return_tab not in {"article", "social"}:
            raise HTTPException(status_code=400, detail="Invalid Brand Brain destination.")
        if dimension not in FEEDBACK_DIMENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported rule dimension.")
        try:
            rule.signal = PreferenceSignal(signal)
            rule.dimension = dimension
            rule.instruction = instruction.strip()
            rule.article_type = (
                ArticleType(article_type)
                if article_type and rule.channel == BrandRuleChannel.ARTICLE
                else None
            )
            rule.priority = priority
            rule.enabled = enabled == "true"
            store.save_brand_rule(rule)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(f"/preferences?tab={return_tab}", status_code=303)

    @app.post("/preferences/feedback/{feedback_id}/promote")
    def promote_feedback(feedback_id: int, rule_scope: str = Form()) -> RedirectResponse:
        signal = store.get_feedback_signal(feedback_id)
        if signal is None:
            raise HTTPException(status_code=404, detail="Feedback signal not found")
        if rule_scope not in {"brand", "article_type"}:
            raise HTTPException(status_code=400, detail="Unsupported rule scope.")
        if f"feedback:{feedback_id}" not in {
            rule.source for rule in store.list_brand_rules()
        }:
            try:
                store.add_brand_rule(
                    BrandRule(
                        brand_id=store.get_active_brand_id(),
                        channel=BrandRuleChannel.ARTICLE,
                        signal=PreferenceSignal(str(signal["signal"])),
                        dimension=str(signal["dimension"]),
                        instruction=str(signal["note"]),
                        article_type=(
                            ArticleType(str(signal["article_type"]))
                            if rule_scope == "article_type"
                            else None
                        ),
                        source=f"feedback:{feedback_id}",
                    )
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse("/preferences?tab=learning", status_code=303)

    @app.get("/items/{content_item_id}", response_class=HTMLResponse)
    def detail(
        content_item_id: str,
        tab: str = "overview",
        channel: str = "wordpress",
        notice: str = "",
    ) -> HTMLResponse:
        allowed_tabs = {"overview", "intelligence", "editor", "review", "delivery", "learning"}
        if tab not in allowed_tabs:
            raise HTTPException(status_code=404, detail="Story tab not found")
        if channel not in {"wordpress", "images", "social"}:
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
        wordpress_categories = store.list_wordpress_categories()
        wordpress_media = store.list_wordpress_media()
        wordpress_settings = store.get_wordpress_publishing_settings(content_item_id)
        generated_images = store.list_generated_images(content_item_id)
        image_config = image_settings_factory()
        social_campaign = (
            store.get_social_campaign(latest_draft.draft_id) if latest_draft else None
        )
        social_posts = (
            store.list_latest_social_posts(social_campaign.campaign_id)
            if social_campaign
            else []
        )
        confirmed_facts = (
            store.list_confirmed_required_facts(latest_draft.draft_id)
            if latest_draft
            else set()
        )
        pending_facts = (
            pending_required_facts(packet, latest_draft, confirmed_facts)
            if latest_draft
            else []
        )
        quality_report = (
            evaluate_draft(packet, latest_draft, confirmed_facts)
            if latest_draft
            else None
        )
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
        if latest_draft:
            evidence_links = "".join(
                f"<a href='{escape(str(source.url), quote=True)}' target='_blank' rel='noopener'>"
                f"S{index}: {escape(source.title)}</a>"
                for index, source in enumerate(packet.evidence.sources, start=1)
            )
            pending_fact_options = "".join(
                "<label class='fact-option'>"
                f"<input type='checkbox' name='fact_index' value='{index}'>"
                f"<span>{escape(fact)}</span></label>"
                for index, fact in enumerate(brief.required_facts)
                if fact in pending_facts
            )
            if pending_fact_options:
                fact_review_body = (
                    "<p>Check each statement against the saved sources, then confirm only the facts you verified.</p>"
                    f"<div class='evidence-links'>{evidence_links}</div>"
                    f"<form method='post' action='/items/{encoded_id}/facts/confirm'>"
                    f"<input type='hidden' name='draft_id' value='{escape(latest_draft.draft_id, quote=True)}'>"
                    f"<div class='fact-list'>{pending_fact_options}</div>"
                    "<button type='submit'>Confirm selected facts</button></form>"
                )
            else:
                fact_review_body = (
                    "<p><span class='badge pass'>Complete</span></p>"
                    "<p>Every required fact is represented in this draft or has been explicitly checked.</p>"
                )
            fact_confirmation_panel = (
                "<section class='panel'><h2>Required fact review</h2>"
                f"{fact_review_body}"
                "<small class='muted'>Fact confirmation applies only to this draft version and does not approve the article.</small>"
                "</section>"
            )
        else:
            fact_confirmation_panel = ""
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
        current_generated_images = [
            asset
            for asset in generated_images
            if latest_draft and asset.draft_id == latest_draft.draft_id
        ]
        try:
            wordpress_upload_ready = (
                wordpress_publisher_factory is not None
                or not WordPressConfig.from_env().dry_run
            )
        except ValueError:
            wordpress_upload_ready = False
        generated_image_cards = []
        for asset in current_generated_images:
            asset_feedback = store.list_visual_feedback(record.brand_id, asset.asset_id)
            if asset.wordpress_media_id:
                candidate_action = (
                    "<span class='badge approved'>Selected in WordPress</span>"
                    f"<p class='muted'>Media #{asset.wordpress_media_id}</p>"
                )
            elif record.status != "approved":
                candidate_action = (
                    "<p class='muted'>Approve this article version before uploading its image to WordPress.</p>"
                )
            elif wordpress_delivery and wordpress_delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
                candidate_action = (
                    "<p class='muted'>The WordPress draft already exists. Add or change its image in WordPress.</p>"
                )
            elif not wordpress_upload_ready:
                candidate_action = (
                    "<p class='muted'>WordPress upload is in dry-run mode or not configured.</p>"
                )
            else:
                candidate_action = (
                    f"<form method='post' action='/items/{encoded_id}/images/{escape(asset.asset_id, quote=True)}/wordpress'>"
                    f"<input name='alt_text' required value='{escape(asset.alt_text, quote=True)}' placeholder='Accessibility description'>"
                    "<button class='button-approve' type='submit'>Upload and use as featured image</button></form>"
                )
            generated_image_cards.append(
                "<article class='image-candidate'>"
                f"<img src='/generated-images/{escape(asset.asset_id, quote=True)}' alt='{escape(asset.alt_text, quote=True)}'>"
                "<div class='image-candidate-copy'>"
                f"<span class='badge'>{escape(asset.provider)} · {escape(asset.model)}</span>"
                f"<p><strong>{escape(asset.style.title())}</strong></p>"
                f"<small class='muted'>{escape(asset.size)} · {escape(asset.quality)} quality · generated {asset.created_at.strftime('%Y-%m-%d %H:%M UTC')}</small>"
                f"{candidate_action}<div class='visual-feedback'><strong>Teach future images</strong>"
                f"<p class='muted'>{len(asset_feedback)} saved signal(s). Say exactly what should be repeated or avoided.</p>"
                f"<form method='post' action='/items/{encoded_id}/images/{escape(asset.asset_id, quote=True)}/feedback'>"
                "<div class='feedback-row'><select name='signal'><option value='prefer'>Use more like this</option><option value='avoid'>Avoid this</option></select>"
                "<select name='dimension'><option value='style'>Visual style</option><option value='composition'>Layout and composition</option><option value='colour'>Colour</option><option value='subject'>Subject matter</option></select></div>"
                "<textarea name='note' required maxlength='600' placeholder='For example: Keep the simple central metaphor and generous empty space.'></textarea>"
                "<button class='button-revise' type='submit'>Save visual feedback</button></form></div></div></article>"
            )
        style_options = "".join(
            f"<option value='{escape(style, quote=True)}'{' selected' if style == 'editorial illustration' else ''}>{escape(style.title())}</option>"
            for style in sorted(SUPPORTED_IMAGE_STYLES)
        )
        size_options = "".join(
            f"<option value='{size}'{' selected' if size == '1536x1024' else ''}>{size} · {'landscape' if size == '1536x1024' else 'square' if size == '1024x1024' else 'portrait'}</option>"
            for size in sorted(SUPPORTED_IMAGE_SIZES, reverse=True)
        )
        quality_options = "".join(
            f"<option value='{quality}'{' selected' if quality == 'medium' else ''}>{quality.title()} quality</option>"
            for quality in ("low", "medium", "high")
        )
        image_generation_ready = image_generator_factory is not None or image_config.ready
        image_studio_panel = (
            "<section class='panel image-studio'><p class='eyebrow'>AI IMAGE STUDIO</p>"
            "<h2>Create a featured-image candidate</h2>"
            "<p class='muted'>The article, approved visual identity, and recent reviewed image feedback shape the prompt. Generated images stay local until you explicitly upload one.</p>"
            f"<p><span class='badge {'selected' if image_generation_ready else 'warning'}'>{escape(image_config.readiness_note)}</span></p>"
            f"<form class='image-controls' method='post' action='/items/{encoded_id}/images/generate'>"
            "<textarea class='span-2' name='creative_direction' maxlength='1200' placeholder='Optional creative direction—for example: Show several developer tools converging into one clear workflow, with warm orange accents.'></textarea>"
            f"<select name='style'>{style_options}</select><select name='size'>{size_options}</select>"
            f"<select name='quality'>{quality_options}</select>"
            "<div class='cost-note'>Generation uses provider credits. Nothing runs automatically, and each click requests one image.</div>"
            f"<button type='submit'{' disabled' if not image_generation_ready else ''}>Generate one candidate</button></form>"
            + (
                f"<h3>Current article candidates</h3><div class='image-candidates'>{''.join(generated_image_cards)}</div>"
                if generated_image_cards
                else "<p class='muted'>No image candidates have been generated for this article version yet.</p>"
            )
            + "</section>"
            if latest_draft
            else ""
        )
        if latest_draft and wordpress_delivery and wordpress_delivery.status == WordPressDeliveryStatus.DRAFT_CREATED:
            category_names = [
                category.name
                for category in wordpress_categories
                if category.category_id in wordpress_settings.category_ids
            ]
            featured_image = next(
                (
                    item
                    for item in wordpress_media
                    if item.media_id == wordpress_settings.featured_media_id
                ),
                None,
            )
            wordpress_category_panel = (
                "<section class='panel'><p class='eyebrow'>WORDPRESS SETUP</p><h2>Saved with this draft</h2>"
                f"<p>{escape(', '.join(category_names) if category_names else 'WordPress default category')}</p>"
                f"<p><strong>Featured image:</strong> {escape(featured_image.title if featured_image else 'None selected')}</p>"
                "<small class='muted'>This article version already exists in WordPress. Change its website metadata in WordPress if needed.</small></section>"
            )
        elif latest_draft and wordpress_categories:
            category_options = "".join(
                "<label class='category-option'>"
                f"<input type='checkbox' name='category_id' value='{category.category_id}'"
                f"{' checked' if category.category_id in wordpress_settings.category_ids else ''}>"
                f"<span><strong>{escape(category.name)}</strong><small class='muted'>/{escape(category.slug)} · {category.post_count} post(s)</small></span></label>"
                for category in wordpress_categories
            )
            selectable_media = [
                item
                for item in wordpress_media
                if item.mime_type
                in {"image/jpeg", "image/png", "image/webp", "image/gif"}
            ]
            current_featured_image = next(
                (
                    item
                    for item in selectable_media
                    if item.media_id == wordpress_settings.featured_media_id
                ),
                None,
            )
            media_options = "<option value='0'>No featured image — use the website default</option>" + "".join(
                f"<option value='{item.media_id}'"
                f"{' selected' if item.media_id == wordpress_settings.featured_media_id else ''}>"
                f"{escape(item.title)} — {escape(item.filename or item.mime_type)}</option>"
                for item in selectable_media
            )
            current_featured_preview = (
                f"<img src='{escape(current_featured_image.thumbnail_url or current_featured_image.source_url, quote=True)}' "
                f"alt='{escape(current_featured_image.alt_text or current_featured_image.title, quote=True)}' style='width:min(100%,420px);border-radius:12px;margin-bottom:10px'>"
                if current_featured_image
                else ""
            )
            media_intro = (
                current_featured_preview
                + f"<select name='featured_media_id'>{media_options}</select>"
                + "<p><a class='text-link' href='/publishing?tab=media'>Browse or upload images in the Publishing Hub →</a></p>"
                if selectable_media
                else "<p class='muted'>Sync the media library before choosing a featured image. <a class='text-link' href='/publishing?tab=media'>Open Media library →</a></p>"
            )
            wordpress_category_panel = (
                "<section class='panel'><p class='eyebrow'>WORDPRESS SETUP</p>"
                "<h2>Where should this article appear?</h2>"
                "<p class='muted'>Choose one or more categories from your website. If none are selected, WordPress will use its default category.</p>"
                f"<form method='post' action='/items/{encoded_id}/wordpress/settings'>"
                f"<div class='category-grid'>{category_options}</div>"
                "<h3>Featured image</h3><p class='muted'>Choose the image WordPress should use on listings, previews, and social link cards.</p>"
                f"{media_intro}<button type='submit'>Save WordPress setup</button></form></section>"
            )
        elif latest_draft:
            wordpress_category_panel = (
                "<section class='panel'><p class='eyebrow'>WORDPRESS CATEGORIES</p>"
                "<h2>Load categories from your website</h2>"
                "<p class='muted'>Sync WordPress once, then return here to choose where this article should appear.</p>"
                "<a class='text-link' href='/publishing'>Open Publishing Hub →</a></section>"
            )
        else:
            wordpress_category_panel = ""
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
            publishing_panel = wordpress_category_panel + (
                "<section class='panel'><span class='badge approved'>WordPress · draft only</span>"
                f"<h2>Delivery</h2>{publishing_controls}</section>"
            )
        else:
            publishing_panel = wordpress_category_panel + (
                "<section class='panel'><span class='badge warning'>WordPress · locked</span>"
                "<h2>Draft delivery</h2><p class='muted'>Approve the latest article version in the Review tab before creating a WordPress draft.</p></section>"
            )
        if social_campaign:
            try:
                buffer_config = buffer_settings_factory()
                buffer_config_error = ""
            except BufferDeliveryRejected as exc:
                buffer_config = None
                buffer_config_error = str(exc)
            wordpress_ready = bool(
                wordpress_delivery
                and wordpress_delivery.status == WordPressDeliveryStatus.DRAFT_CREATED
            )
            public_url_value = (
                str(social_campaign.public_article_url)
                if social_campaign.public_article_url
                else ""
            )
            if social_campaign.public_article_url:
                public_url_intro = (
                    "<span class='badge approved'>Public URL confirmed</span>"
                    f"<p><a class='text-link' href='{escape(public_url_value, quote=True)}' target='_blank' rel='noopener'>{escape(public_url_value)}</a></p>"
                )
            elif wordpress_ready:
                public_url_intro = (
                    "<span class='badge warning'>Awaiting publication</span>"
                    "<p class='muted'>Publish the WordPress draft manually, then confirm its public URL here. Buffer remains locked until then.</p>"
                )
            else:
                public_url_intro = (
                    "<span class='badge warning'>Website delivery required</span>"
                    "<p class='muted'>Create the WordPress draft first. After it is manually published, return here to confirm its public URL.</p>"
                )
            public_url_form = (
                f"<form method='post' action='/items/{encoded_id}/social/public-url'>"
                f"<input type='url' name='public_url' required value='{escape(public_url_value, quote=True)}' placeholder='https://www.mycodequest.net/article-slug/'>"
                f"<button type='submit'>{'Update' if public_url_value else 'Confirm'} public article URL</button></form>"
                if wordpress_ready
                else ""
            )
            public_url_panel = (
                "<section class='panel'><h2>Published article URL</h2>"
                f"{public_url_intro}{public_url_form}</section>"
            )
            social_cards = []
            for post in sorted(social_posts, key=lambda item: item.platform.value):
                source_value = ", ".join(post.source_ids)
                limit = PLATFORM_LIMITS[post.platform]
                buffer_delivery = store.get_buffer_delivery(post.post_id)
                delivery_locks_review = bool(
                    buffer_delivery
                    and buffer_delivery.status
                    in {
                        BufferDeliveryStatus.PENDING,
                        BufferDeliveryStatus.UNCERTAIN,
                        BufferDeliveryStatus.DELIVERED,
                    }
                )
                locked_attribute = " disabled" if delivery_locks_review else ""
                if buffer_delivery and buffer_delivery.status == BufferDeliveryStatus.DELIVERED:
                    buffer_controls = (
                        "<div class='delivery-state'><span class='badge approved'>Buffer delivered</span>"
                        f"<p><strong>{escape(buffer_delivery.buffer_status or 'accepted')}</strong></p>"
                        f"<small class='muted'>Buffer post {escape(buffer_delivery.buffer_post_id or '')} · {buffer_delivery.attempts} attempt(s)</small></div>"
                    )
                elif buffer_delivery and buffer_delivery.status == BufferDeliveryStatus.UNCERTAIN:
                    buffer_controls = (
                        "<div class='delivery-state'><span class='badge block'>Outcome uncertain</span>"
                        f"<p>{escape(buffer_delivery.error_message)}</p>"
                        "<small class='muted'>Automatic retry is blocked to prevent a duplicate. Check this channel in Buffer.</small></div>"
                    )
                elif buffer_delivery and buffer_delivery.status == BufferDeliveryStatus.PENDING:
                    buffer_controls = (
                        "<div class='delivery-state'><span class='badge warning'>Delivery pending</span>"
                        "<p class='muted'>This request may already be in progress. Retry is blocked.</p></div>"
                    )
                elif post.status == SocialPostStatus.APPROVED and social_campaign.public_article_url and buffer_config:
                    retry_query = ""
                    retry_label = "Preview send now"
                    if buffer_delivery and buffer_delivery.status == BufferDeliveryStatus.FAILED:
                        retry_label = "Review failed delivery"
                        retry_query = (
                            f"&due_at={quote(buffer_delivery.scheduled_for.isoformat(), safe='')}"
                            if buffer_delivery.scheduled_for
                            else ""
                        )
                        retry_mode = buffer_delivery.mode.value
                    else:
                        retry_mode = BufferDeliveryMode.SHARE_NOW.value
                    buffer_controls = (
                        "<div class='delivery-actions'>"
                        + (
                            "<div class='delivery-state'><span class='badge block'>Last attempt failed</span>"
                            f"<p>{escape(buffer_delivery.error_message)}</p></div>"
                            if buffer_delivery
                            else ""
                        )
                        + f"<a class='text-link' href='/items/{encoded_id}/buffer/{escape(post.post_id, quote=True)}/preview?mode={retry_mode}{retry_query}'>{retry_label} →</a>"
                        + (
                            f"<form method='get' action='/items/{encoded_id}/buffer/{escape(post.post_id, quote=True)}/preview'>"
                            f"<input type='hidden' name='mode' value='{BufferDeliveryMode.CUSTOM_SCHEDULED.value}'>"
                            f"<label class='field-label'>Schedule in {escape(buffer_config.schedule_timezone)}</label>"
                            "<input type='datetime-local' name='due_at' required>"
                            "<button class='button-revise' type='submit'>Preview scheduled delivery</button></form>"
                            if not buffer_delivery
                            else ""
                        )
                        + "</div>"
                    )
                elif post.status != SocialPostStatus.APPROVED:
                    buffer_controls = (
                        "<div class='delivery-state'><p class='muted'>Approve this exact copy before preparing Buffer delivery.</p></div>"
                    )
                elif buffer_config_error:
                    buffer_controls = (
                        f"<div class='delivery-state'><span class='badge block'>Buffer setup needed</span><p>{escape(buffer_config_error)}</p></div>"
                    )
                else:
                    buffer_controls = (
                        "<div class='delivery-state'><p class='muted'>Confirm the published article URL to unlock Buffer previews.</p></div>"
                    )
                social_cards.append(
                    "<article class='panel social-card'>"
                    f"<div class='social-head'><h2>{escape(_SOCIAL_LABELS[post.platform])}</h2>"
                    f"<span class='badge {escape(post.status.value)}'>{escape(post.status.value.replace('_', ' '))}</span></div>"
                    f"<div class='social-meta'><span>Version {post.version}</span>"
                    f"<span>{len(post.body)} / {limit} characters</span></div>"
                    f"<form method='post' action='/items/{encoded_id}/social/{post.platform.value}/edit'>"
                    f"<input type='hidden' name='base_post_id' value='{escape(post.post_id, quote=True)}'>"
                    f"<textarea name='body' required maxlength='{limit}'{locked_attribute}>{escape(post.body)}</textarea>"
                    "<label class='field-label'>Supporting evidence IDs</label>"
                    f"<input name='source_ids' required value='{escape(source_value, quote=True)}'{locked_attribute}>"
                    f"<input name='edit_note' required placeholder='What changed in this version?'{locked_attribute}>"
                    f"<button class='button-revise' type='submit'{locked_attribute}>Save new version</button></form>"
                    f"<form class='inline-actions' method='post' action='/items/{encoded_id}/social/{escape(post.post_id, quote=True)}/decision'>"
                    f"<button class='button-approve' name='status' value='approved' type='submit'{locked_attribute}>Approve copy</button>"
                    f"<button class='button-revise' name='status' value='needs_revision' type='submit'{locked_attribute}>Needs revision</button></form>"
                    f"{buffer_controls}"
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
            if buffer_config is None:
                buffer_badge_class = "block"
                buffer_badge_label = "Buffer · setup needed"
            elif buffer_config.dry_run:
                buffer_badge_class = "warning"
                buffer_badge_label = "Buffer · preview only"
            else:
                buffer_badge_class = "selected"
                buffer_badge_label = "Buffer · guarded delivery"
            social_panel = (
                f"{public_url_panel}<section class='social-list'>"
                f"{''.join(social_cards)}</section>"
                f"<section class='panel buffer-lock'><span class='badge {buffer_badge_class}'>{buffer_badge_label}</span>"
                f"<h2>{approved_count} of 3 posts approved</h2>"
                "<p class='muted'>Each platform requires its own payload preview and explicit confirmation. Ambiguous network outcomes cannot be retried automatically.</p></section>"
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
            f"<a class='subtab{' active' if channel == 'images' else ''}' href='/items/{encoded_id}?tab=delivery&channel=images'>Images ({len(current_generated_images)})</a>"
            f"<a class='subtab{' active' if channel == 'social' else ''}' href='/items/{encoded_id}?tab=delivery&channel=social'>Social campaign</a></nav>"
        )
        delivery_body = (
            f"<div class='layout'><section class='stack'>{publishing_panel}</section>"
            f"<aside class='stack'>{discord_panel}</aside></div>"
            if channel == "wordpress"
            else image_studio_panel
            if channel == "images"
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
        insight = packet.discovery
        if insight is None:
            intelligence_body = (
                "<section class='panel empty'><p class='eyebrow'>LEGACY STORY</p>"
                "<h2>Intelligence is awaiting a Horizon refresh</h2>"
                "<p class='muted'>The assignment and evidence remain available. When Horizon sees this story again, its score, rationale, tags, enrichment and engagement will be added without changing the editorial state.</p>"
                "<a class='text-link' href='/operations'>Open Operations →</a></section>"
            )
        else:
            score_value = f"{insight.ai_score:.1f} / 10" if insight.ai_score is not None else "Not scored"
            insight_tags = "".join(
                f"<span class='tag'>{escape(tag)}</span>" for tag in insight.ai_tags
            ) or "<span class='muted'>No AI tags recorded.</span>"
            engagement = "".join(
                "<div class='engagement-item'>"
                f"<strong>{escape(str(value))}</strong><small class='muted'>{escape(key.replace('_', ' ').title())}</small></div>"
                for key, value in insight.engagement.items()
            ) or "<p class='muted'>This source did not provide public engagement metrics.</p>"
            discussion_link = (
                f"<p><a class='text-link' href='{escape(insight.discussion_url, quote=True)}' target='_blank' rel='noopener'>Open source discussion →</a></p>"
                if insight.discussion_url.startswith(("https://", "http://"))
                else ""
            )
            enrichment_sections = "".join(
                f"<section class='panel'><h2>{label}</h2><p class='insight-copy'>{escape(value)}</p></section>"
                for label, value in (
                    ("Detailed summary", insight.detailed_summary),
                    ("Background", insight.background),
                    ("Community discussion", insight.community_discussion),
                )
                if value
            ) or "<section class='panel'><h2>Enrichment</h2><p class='muted'>No extended enrichment was returned for this story.</p></section>"
            intelligence_body = (
                "<div class='layout'><section class='stack'>"
                "<section class='panel'><p class='eyebrow'>HORIZON ASSESSMENT</p>"
                f"<div class='intelligence-grid'><div class='signal-card'><small class='muted'>Editorial score</small><strong>{escape(score_value)}</strong></div>"
                f"<div class='signal-card'><small class='muted'>Category</small><strong>{escape(insight.category or 'Uncategorised')}</strong></div>"
                f"<div class='signal-card'><small class='muted'>Source</small><strong>{escape(insight.source_type.value.replace('_', ' ').title())}</strong></div>"
                f"<div class='signal-card'><small class='muted'>Author</small><strong>{escape(insight.author or 'Not supplied')}</strong></div></div>"
                f"<h3>Why Horizon surfaced it</h3><p>{escape(insight.ai_reason or 'No score rationale was returned.')}</p>"
                f"<h3>Discovery summary</h3><p>{escape(insight.ai_summary or 'No discovery summary was returned.')}</p>"
                f"<div class='tags'>{insight_tags}</div></section>{enrichment_sections}</section>"
                "<aside class='stack'><section class='panel'><h2>Timeline</h2>"
                f"<p><small class='muted'>Published</small><br><strong>{escape(_display_time(insight.published_at))}</strong></p>"
                f"<p><small class='muted'>Captured</small><br><strong>{escape(_display_time(insight.fetched_at))}</strong></p>"
                f"{discussion_link}</section><section class='panel'><h2>Engagement signals</h2><div class='engagement-list'>{engagement}</div></section>"
                f"<section class='panel'><h2>Evidence coverage</h2><p><strong>{len(packet.evidence.sources)}</strong> saved source(s)</p>"
                f"<p class='muted'>{len(packet.evidence.supporting_sources)} supporting source(s) beyond the primary item.</p></section></aside></div>"
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
            "intelligence": intelligence_body,
            "editor": (
                "<div class='layout'><section class='stack'>"
                f"{_draft_html(latest_draft, edit_href)}</section><aside class='stack'>{versions_panel}</aside></div>"
            ),
            "review": (
                f"<div class='layout'><section class='stack'>{quality_panel}{fact_confirmation_panel}</section>"
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
            (
                "intelligence",
                "Intelligence",
                f"{packet.discovery.ai_score:.1f}" if packet.discovery and packet.discovery.ai_score is not None else "",
            ),
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
            "intelligence": "Horizon score, rationale, enrichment, engagement, and source context.",
            "editor": "Read or edit the latest article while preserving every version.",
            "review": "Quality checks, workflow state, and the authoritative editorial decision.",
            "delivery": "Prepare website and social delivery without mixing their review steps.",
            "learning": "Story feedback and the writing preferences learned from it.",
        }
        return render_page(
            brief.working_title,
            "<div class='crumb'><a href='/editorial'>Editorial queue</a><span>›</span>"
            f"<span>{escape(brief.working_title)}</span></div>"
            f"<header class='page-head'><p class='eyebrow'>{escape(brief.article_type.value.replace('_', ' ').upper())}</p>"
            f"<h1>{escape(brief.working_title)}</h1><div class='meta'>"
            f"<span class='badge {escape(record.status)}'>{escape(record.status)}</span>"
            f"<span>{len(packet.evidence.sources)} evidence source(s)</span>"
            f"<span>·</span><span>{len(draft_versions)} draft version(s)</span></div></header>"
            + (
                f"<div class='config-note'>{escape(notice)}</div>"
                if notice
                else ""
            )
            +
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
        return render_page(
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
            brand_profile_snapshot=base.brand_profile_snapshot,
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
    def update_status(
        content_item_id: str,
        status: str = Form(),
        return_to: str = Form(""),
    ) -> RedirectResponse:
        try:
            store.set_status(content_item_id, status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Editorial item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if return_to == "editorial":
            destination = "/editorial?status=selected"
        elif return_to == "discovery":
            destination = "/discovery"
        else:
            destination = f"/items/{quote(content_item_id, safe='')}?tab=review"
        return RedirectResponse(destination, status_code=303)

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
                quality_report=evaluate_draft(
                    record.packet,
                    draft,
                    store.list_confirmed_required_facts(draft.draft_id),
                ),
            )
            store.record_decision(decision)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=review", status_code=303
        )

    @app.post("/items/{content_item_id}/facts/confirm")
    def confirm_facts(
        content_item_id: str,
        draft_id: str = Form(),
        fact_index: list[int] = Form(default=[]),
    ) -> RedirectResponse:
        record = store.get_item(content_item_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Editorial item not found")
        facts = record.packet.brief.required_facts
        if not fact_index or any(index < 0 or index >= len(facts) for index in fact_index):
            raise HTTPException(status_code=400, detail="Select valid required facts to confirm.")
        try:
            store.confirm_required_facts(
                content_item_id,
                draft_id,
                [facts[index] for index in dict.fromkeys(fact_index)],
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        confirmed_facts = store.list_confirmed_required_facts(draft.draft_id)
        quality_report = evaluate_draft(record.packet, draft, confirmed_facts)
        if not quality_report.can_approve:
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
                quality_report,
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
        try:
            store.set_social_post_status(post_id, review_status)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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

    @app.post("/items/{content_item_id}/social/public-url")
    def confirm_public_article_url(
        content_item_id: str, public_url: str = Form()
    ) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        campaign = store.get_social_campaign(draft.draft_id)
        wordpress_delivery = store.get_wordpress_delivery(draft.draft_id)
        if campaign is None:
            raise HTTPException(status_code=409, detail="Generate a social campaign first.")
        if (
            wordpress_delivery is None
            or wordpress_delivery.status != WordPressDeliveryStatus.DRAFT_CREATED
        ):
            raise HTTPException(
                status_code=409,
                detail="Create the WordPress draft before confirming its public URL.",
            )
        try:
            wordpress_profile = store.get_brand_connection(
                ConnectionProvider.WORDPRESS
            )
            wordpress_base_url = (
                str(wordpress_profile.settings.get("base_url", ""))
                if wordpress_profile
                else os.getenv("WORDPRESS_BASE_URL", "")
            )
            normalized = normalize_public_article_url(
                public_url,
                wordpress_base_url,
            )
            store.set_social_campaign_public_url(campaign.campaign_id, normalized)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

    @app.get(
        "/items/{content_item_id}/buffer/{post_id}/preview",
        response_class=HTMLResponse,
    )
    def preview_buffer_delivery(
        content_item_id: str,
        post_id: str,
        mode: str,
        due_at: str = "",
    ) -> HTMLResponse:
        campaign, post, config, selected_mode, scheduled_for, payload = buffer_preview_data(
            content_item_id,
            post_id,
            mode,
            due_at,
        )
        existing = store.get_buffer_delivery(post.post_id)
        if existing and existing.status in {
            BufferDeliveryStatus.PENDING,
            BufferDeliveryStatus.UNCERTAIN,
        }:
            raise HTTPException(
                status_code=409,
                detail="This delivery may already exist in Buffer. Check Buffer before retrying.",
            )
        encoded_id = quote(content_item_id, safe="")
        mode_label = (
            "Send now"
            if selected_mode == BufferDeliveryMode.SHARE_NOW
            else "Schedule"
        )
        schedule_label = (
            scheduled_for.isoformat() if scheduled_for else "Immediately after confirmation"
        )
        input_preview = json.dumps(payload["variables"]["input"], indent=2)
        due_value = scheduled_for.isoformat() if scheduled_for else ""
        confirmation = (
            "<p class='muted'>Delivery is disabled because <code>BUFFER_DRY_RUN</code> is true.</p>"
            "<button type='button' disabled>Buffer delivery disabled</button>"
            if config.dry_run
            else (
                f"<form method='post' action='/items/{encoded_id}/buffer/{escape(post.post_id, quote=True)}'>"
                f"<input type='hidden' name='mode' value='{selected_mode.value}'>"
                f"<input type='hidden' name='due_at' value='{escape(due_value, quote=True)}'>"
                f"<button class='button-approve' type='submit'>{mode_label} in Buffer</button></form>"
            )
        )
        return render_page(
            f"Buffer preview · {_SOCIAL_LABELS[post.platform]}",
            "<div class='crumb'><a href='/editorial'>Editorial queue</a><span>›</span>"
            f"<a href='/items/{encoded_id}?tab=delivery&channel=social'>Social campaign</a><span>›</span><span>Buffer preview</span></div>"
            "<header class='page-head'><p class='eyebrow'>BUFFER PAYLOAD PREVIEW</p>"
            f"<h1>{escape(_SOCIAL_LABELS[post.platform])}: {escape(mode_label)}</h1>"
            "<p class='muted'>Review the final linked copy and destination before the one explicit delivery action.</p></header>"
            "<div class='layout'><section class='stack'><article class='panel'>"
            "<h2>Final social copy</h2>"
            f"<p class='payload-copy'>{escape(build_buffer_text(post, campaign))}</p></article>"
            "<article class='panel'><h2>Mutation input</h2>"
            f"<pre>{escape(input_preview)}</pre></article></section>"
            "<aside class='stack'><section class='panel'><h2>Delivery summary</h2>"
            "<div class='payload-table'>"
            f"<div class='payload-row'><span>Platform</span><strong>{escape(_SOCIAL_LABELS[post.platform])}</strong></div>"
            f"<div class='payload-row'><span>Mode</span><strong>{escape(mode_label)}</strong></div>"
            f"<div class='payload-row'><span>When</span><code>{escape(schedule_label)}</code></div>"
            f"<div class='payload-row'><span>Social version</span><code>{escape(post.post_id)}</code></div>"
            f"<div class='payload-row'><span>Article version</span><code>{escape(campaign.article_draft_id)}</code></div>"
            "</div></section><section class='panel'><span class='badge warning'>Final confirmation</span>"
            "<h2>Ready?</h2><p class='muted'>This action creates a real Buffer post when dry-run is disabled.</p>"
            f"{confirmation}</section></aside></div>",
            active="editorial",
        )

    @app.post("/items/{content_item_id}/buffer/{post_id}")
    async def deliver_to_buffer(
        content_item_id: str,
        post_id: str,
        mode: str = Form(),
        due_at: str = Form(""),
    ) -> RedirectResponse:
        campaign, post, config, selected_mode, scheduled_for, payload = buffer_preview_data(
            content_item_id,
            post_id,
            mode,
            due_at,
        )
        if config.dry_run:
            raise HTTPException(
                status_code=409,
                detail="Buffer delivery is disabled while BUFFER_DRY_RUN is true.",
            )
        final_text = build_buffer_text(post, campaign)
        try:
            delivery = store.begin_buffer_delivery(
                post,
                config.channel_ids[post.platform],
                selected_mode,
                final_text,
                scheduled_for,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if delivery.status == BufferDeliveryStatus.DELIVERED:
            return RedirectResponse(
                f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
                status_code=303,
            )
        try:
            result = await buffer_sender_factory().create_post(payload)
        except BufferDeliveryRejected as exc:
            store.fail_buffer_delivery(delivery.delivery_id, str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except BufferDeliveryUncertain as exc:
            store.mark_buffer_delivery_uncertain(delivery.delivery_id, str(exc))
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            message = "Buffer delivery ended without a conclusive response. Check Buffer before retrying."
            store.mark_buffer_delivery_uncertain(delivery.delivery_id, message)
            raise HTTPException(status_code=502, detail=message) from exc
        store.complete_buffer_delivery(
            delivery.delivery_id,
            result.post_id,
            result.status,
            result.due_at,
        )
        return RedirectResponse(
            f"/items/{quote(content_item_id, safe='')}?tab=delivery&channel=social",
            status_code=303,
        )

    @app.get("/items/{content_item_id}/wordpress/preview", response_class=HTMLResponse)
    def preview_wordpress(content_item_id: str) -> HTMLResponse:
        _record, draft = approved_draft(content_item_id)
        settings = store.get_wordpress_publishing_settings(content_item_id)
        categories = {
            category.category_id: category
            for category in store.list_wordpress_categories()
        }
        media = {item.media_id: item for item in store.list_wordpress_media()}
        featured_image = (
            media.get(settings.featured_media_id)
            if settings.featured_media_id is not None
            else None
        )
        payload = build_wordpress_payload(
            draft,
            settings.category_ids,
            settings.featured_media_id,
        )
        selected_category_names = [
            categories[category_id].name
            for category_id in settings.category_ids
            if category_id in categories
        ]
        featured_preview = (
            f"<img src='{escape(featured_image.thumbnail_url or featured_image.source_url, quote=True)}' "
            f"alt='{escape(featured_image.alt_text or featured_image.title, quote=True)}' "
            "style='width:100%;border-radius:12px'>"
            f"<p>{escape(featured_image.title)}</p>"
            if featured_image
            else "<p class='muted'>No featured image selected.</p>"
        )
        encoded_id = quote(content_item_id, safe="")
        return render_page(
            f"WordPress preview · {draft.title}",
            "<div class='crumb'><a href='/drafts'>Draft library</a><span>›</span>"
            f"<a href='/items/{encoded_id}'>Article review</a><span>›</span><span>WordPress preview</span></div>"
            "<header class='page-head'><p class='eyebrow'>WORDPRESS PAYLOAD PREVIEW</p>"
            f"<h1>{escape(draft.title)}</h1>"
            "<p class='muted'>This is the exact article body that will be sent with status <strong>draft</strong>.</p></header>"
            "<div class='layout'><article class='panel draft'>"
            f"<h2>{escape(str(payload['title']))}</h2>{payload['content']}</article>"
            "<aside class='stack'><section class='panel'><span class='badge approved'>Draft only</span>"
            f"<h2>Website categories</h2><p>{escape(', '.join(selected_category_names) if selected_category_names else 'WordPress default category')}</p>"
            f"<h2>Featured image</h2>{featured_preview}"
            "<h2>Ready to deliver?</h2><p class='muted'>WordPress will create an unpublished draft. Publishing remains manual.</p>"
            f"<form method='post' action='/items/{encoded_id}/wordpress'><button type='submit'>Create WordPress draft</button></form>"
            f"<a class='text-link' href='/items/{encoded_id}?tab=delivery'>Return to delivery</a></section></aside></div>",
            active="drafts",
        )

    @app.post("/items/{content_item_id}/wordpress")
    async def create_wordpress_draft(content_item_id: str) -> RedirectResponse:
        _record, draft = approved_draft(content_item_id)
        settings = store.get_wordpress_publishing_settings(content_item_id)
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
            result = await publisher_factory().create_draft(
                draft,
                settings.category_ids,
                settings.featured_media_id,
            )
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
