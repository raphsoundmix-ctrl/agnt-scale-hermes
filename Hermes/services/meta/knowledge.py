"""Meta Ads domain model + primer.

Two jobs:
  1. Validate/inform payload building (enums, sensible defaults per objective).
  2. Seed the GLOBAL platform-knowledge memory so agents "understand" Meta Business,
     Ads Manager, pixels, objectives, and the rest — kept current by the changelog watcher.

Enums reflect ODAX (Outcome-Driven Ad Experiences). The watcher reconciles these with
the live API version; treat them as the current best-known baseline, not gospel.
"""
from __future__ import annotations

# ── Campaign objectives (ODAX OUTCOME_*) ───────────────────────────────────────
OBJECTIVES: dict[str, str] = {
    "OUTCOME_AWARENESS": "Reach / brand awareness / video views — top of funnel.",
    "OUTCOME_TRAFFIC": "Send people to a destination (site, app, Messenger, WhatsApp).",
    "OUTCOME_ENGAGEMENT": "Messages, video views, post engagement, conversions on engagement.",
    "OUTCOME_LEADS": "Lead forms (instant forms), conversion leads, calls, messages.",
    "OUTCOME_APP_PROMOTION": "App installs and in-app actions.",
    "OUTCOME_SALES": "Conversions / catalog sales — bottom of funnel, needs pixel/dataset.",
}

# ── Optimization goals (adset.optimization_goal) ────────────────────────────────
OPTIMIZATION_GOALS: dict[str, str] = {
    "OFFSITE_CONVERSIONS": "Optimize for pixel/CAPI conversion events (sales/leads).",
    "LINK_CLICKS": "Optimize for link clicks (traffic).",
    "LANDING_PAGE_VIEWS": "Optimize for LPV (traffic that actually loads the page).",
    "LEAD_GENERATION": "Optimize for instant-form leads.",
    "IMPRESSIONS": "Maximize impressions.",
    "REACH": "Maximize unique reach.",
    "THRUPLAY": "Optimize for ~15s video plays.",
    "QUALITY_LEAD": "Optimize toward higher-quality leads (conversion leads).",
    "VALUE": "Optimize for purchase value (ROAS / value optimization).",
}

BILLING_EVENTS = ["IMPRESSIONS", "LINK_CLICKS", "THRUPLAY"]

BID_STRATEGIES: dict[str, str] = {
    "LOWEST_COST_WITHOUT_CAP": "Highest volume for the budget (default / auto bid).",
    "LOWEST_COST_WITH_BID_CAP": "Cap the bid per result (bid_amount required).",
    "COST_CAP": "Keep avg cost-per-result at/under a target (bid_amount required).",
    "LOWEST_COST_WITH_MIN_ROAS": "Value optimization with a minimum ROAS floor.",
}

CALL_TO_ACTIONS = [
    "LEARN_MORE", "SHOP_NOW", "SIGN_UP", "SUBSCRIBE", "GET_OFFER", "BOOK_TRAVEL",
    "DOWNLOAD", "CONTACT_US", "GET_QUOTE", "APPLY_NOW", "ORDER_NOW", "MESSAGE_PAGE",
]

# Special ad categories — REQUIRED on campaign create (empty list if none apply).
SPECIAL_AD_CATEGORIES = ["NONE", "HOUSING", "EMPLOYMENT", "CREDIT", "ISSUES_ELECTIONS_POLITICS"]

# Standard pixel conversion events (custom_event_type on promoted_object).
PIXEL_EVENTS = [
    "PURCHASE", "LEAD", "COMPLETE_REGISTRATION", "ADD_TO_CART", "INITIATE_CHECKOUT",
    "ADD_PAYMENT_INFO", "SUBSCRIBE", "START_TRIAL", "CONTACT", "SUBMIT_APPLICATION", "VIEW_CONTENT",
]

# Sensible defaults per objective → optimization_goal + billing + whether a pixel is needed.
OBJECTIVE_DEFAULTS: dict[str, dict] = {
    "OUTCOME_AWARENESS": {"optimization_goal": "REACH", "billing_event": "IMPRESSIONS", "needs_pixel": False},
    "OUTCOME_TRAFFIC": {"optimization_goal": "LANDING_PAGE_VIEWS", "billing_event": "IMPRESSIONS", "needs_pixel": False},
    "OUTCOME_ENGAGEMENT": {"optimization_goal": "THRUPLAY", "billing_event": "IMPRESSIONS", "needs_pixel": False},
    "OUTCOME_LEADS": {"optimization_goal": "LEAD_GENERATION", "billing_event": "IMPRESSIONS", "needs_pixel": False},
    "OUTCOME_APP_PROMOTION": {"optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS", "needs_pixel": False},
    "OUTCOME_SALES": {"optimization_goal": "OFFSITE_CONVERSIONS", "billing_event": "IMPRESSIONS", "needs_pixel": True},
}

# ── Primer text → seeds platform-knowledge memory (kind='primer', scope='long') ─
PLATFORM_PRIMER = """\
META BUSINESS & ADS MANAGER — OPERATOR PRIMER (AGNT SCALE)

Meta Business Suite / Business Manager (business.facebook.com): the top-level container
that owns assets — ad accounts, Pages, Instagram accounts, pixels/datasets, catalogs,
and people/partner permissions. A System User (in Business Settings) issues long-lived
server-to-server tokens for automation.

Ads Manager (adsmanager.facebook.com): where campaigns are built and managed. Object
hierarchy: Ad Account (act_<id>) → Campaign (objective + budget if CBO) → Ad Set
(budget if ABO, audience, placements, optimization, schedule, promoted_object) → Ad
(creative). Insights at any level via /insights.

Campaign objective (ODAX OUTCOME_*) sets what Meta optimizes the whole campaign toward.
Pick from business truth, not vanity: SALES needs a pixel + conversion event; LEADS can
use instant forms; TRAFFIC ≠ conversions (LPV is the honest traffic optimization goal).

Budgeting: CBO (Advantage Campaign Budget) sets budget at the campaign and lets Meta
distribute across ad sets; ABO sets budget per ad set. daily_budget / lifetime_budget are
in the account's minor currency unit (e.g. cents).

Bid strategy: LOWEST_COST (auto, max volume) by default; COST_CAP / BID_CAP / MIN_ROAS for
control. Learning phase: ~50 optimization events in ~7 days to exit; avoid frequent edits
that reset it.

Targeting (ad set): geo_locations, age_min/age_max, genders, detailed targeting
(interests/behaviors/demographics via flexible_spec), custom/lookalike audiences,
publisher_platforms + positions (placements). Advantage+ Audience expands beyond the set.
Advantage+ Shopping (ASC) is a largely automated sales campaign type.

Pixel & Conversions API (CAPI): the pixel (browser) + CAPI (server, POST /<pixel_id>/events
with hashed user_data) feed conversion signal back to Meta — better signal = better
optimization, especially post-iOS14. Event Match Quality (EMQ) measures signal strength.
promoted_object on a conversion ad set = {pixel_id, custom_event_type}.

Attribution: default 7-day click / 1-day view. Never confuse top-of-funnel attention
(reach, CTR, ThruPlay) with business results (CPA, ROAS, purchases). Wilson LCB on rate
metrics guards against low-volume false winners.

Automation discipline (non-bot, account-safe): use the official Marketing API (the
sanctioned automation channel), respect Business-Use-Case rate limits, make gradual
changes (e.g. +20% scale steps), keep ad policy clean, and require human approval before
any spend-changing action. The API is not "bot detection" territory — it is how Meta
intends advertisers to automate.
"""


def validate_objective(objective: str) -> None:
    if objective not in OBJECTIVES:
        raise ValueError(f"unknown objective '{objective}'. Valid: {sorted(OBJECTIVES)}")


def validate_optimization_goal(goal: str) -> None:
    if goal not in OPTIMIZATION_GOALS:
        raise ValueError(f"unknown optimization_goal '{goal}'. Valid: {sorted(OPTIMIZATION_GOALS)}")


def defaults_for(objective: str) -> dict:
    validate_objective(objective)
    return dict(OBJECTIVE_DEFAULTS[objective])
