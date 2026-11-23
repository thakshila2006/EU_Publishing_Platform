import hashlib
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content_ai import ArticleDraft
from app.models.foundation import CountryBlog, SystemControl
from app.models.publishing import BloggerThemeSnapshot, PublishJob
from app.services.audit import record_audit_event

WARMUP_STAGE_CAPS = {
    1: 2,
    2: 5,
    3: 8,
}

PUBLISHABLE_STATES = {"READY_FOR_QA", "APPROVED_FOR_SCHEDULE"}

FALLBACK_TZ_OFFSETS = {
    "UTC": timezone.utc,
    "Europe/London": timezone(timedelta(hours=0)),
    "Europe/Berlin": timezone(timedelta(hours=1)),
    "Europe/Amsterdam": timezone(timedelta(hours=1)),
    "Europe/Stockholm": timezone(timedelta(hours=1)),
    "Europe/Oslo": timezone(timedelta(hours=1)),
    "Europe/Copenhagen": timezone(timedelta(hours=1)),
    "Europe/Zurich": timezone(timedelta(hours=1)),
}


def resolve_timezone(iana_timezone: str):
    try:
        return ZoneInfo(iana_timezone)
    except ZoneInfoNotFoundError:
        if iana_timezone in FALLBACK_TZ_OFFSETS:
            return FALLBACK_TZ_OFFSETS[iana_timezone]
        return timezone.utc


def get_stage_daily_cap(warmup_stage: int) -> int:
    return WARMUP_STAGE_CAPS.get(warmup_stage, 2)


def build_idempotency_key(article_id: int, country_code: str, language_code: str) -> str:
    raw = f"publish:{article_id}:{country_code.upper()}:{language_code.lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_local_schedule_window(iana_timezone: str) -> tuple[str, str, int]:
    tz = resolve_timezone(iana_timezone)
    now_local = datetime.now(tz)
    jitter_minutes = random.randint(5, 45)
    scheduled_local = now_local + timedelta(minutes=jitter_minutes)
    local_date_str = scheduled_local.strftime("%Y-%m-%d")
    utc_iso = scheduled_local.astimezone(timezone.utc).isoformat()
    return local_date_str, utc_iso, jitter_minutes


import re
import unicodedata




def _slugify(value: str) -> str:
    replacements = {
        "\u00e4": "a", "\u00f6": "o", "\u00fc": "u", "\u00df": "ss",
        "\u00e5": "aa", "\u00e6": "ae", "\u00f8": "o",
        "\u00c4": "a", "\u00d6": "o", "\u00dc": "u",
        "\u00c5": "aa", "\u00c6": "ae", "\u00d8": "o",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return cleaned or "post"


async def enqueue_and_publish_article(
    db: AsyncSession,
    *,
    article_id: int,
    actor_username: str,
    target_language: str | None = None,
) -> dict:
    ctrl = (await db.execute(select(SystemControl).limit(1))).scalar_one_or_none()
    if ctrl and ctrl.global_kill_switch:
        raise RuntimeError("System halted by global emergency kill switch.")

    draft = (await db.execute(select(ArticleDraft).where(ArticleDraft.id == article_id))).scalar_one_or_none()
    if not draft:
        raise LookupError(f"Article {article_id} not found.")

    lang_code = target_language or draft.language_code
    idem_key = build_idempotency_key(draft.id, draft.country_code, lang_code)

    existing_job = (
        await db.execute(select(PublishJob).where(PublishJob.idempotency_key == idem_key))
    ).scalar_one_or_none()
    if existing_job:
        return {
            "job_id": existing_job.id,
            "idempotency_key": existing_job.idempotency_key,
            "deduplicated": True,
            "status": existing_job.status,
            "blogger_post_id": existing_job.blogger_post_id,
            "published_url": existing_job.published_url,
            "scheduled_local_date": existing_job.scheduled_local_date,
        }

    if draft.is_ymyl and draft.workflow_state != "APPROVED_FOR_SCHEDULE":
        raise PermissionError(
            f"YMYL gate block: article {article_id} is in state {draft.workflow_state} and requires human approval."
        )

    if draft.workflow_state not in PUBLISHABLE_STATES:
        raise PermissionError(
            f"Workflow state {draft.workflow_state} is not eligible for publishing."
        )

    blog = (
        await db.execute(select(CountryBlog).where(CountryBlog.country_code == draft.country_code))
    ).scalar_one_or_none()
    if not blog or not blog.is_active or blog.is_paused_by_killswitch:
        raise RuntimeError(f"Country blog {draft.country_code} is inactive or paused.")

    local_date_str, utc_iso, jitter_mins = compute_local_schedule_window(blog.iana_timezone)
    effective_cap = min(blog.daily_post_cap, get_stage_daily_cap(blog.warmup_stage))

    used_today_res = await db.execute(
        select(func.count(PublishJob.id)).where(
            PublishJob.country_code == blog.country_code,
            PublishJob.scheduled_local_date == local_date_str,
            PublishJob.status == "PUBLISHED",
        )
    )
    used_today = int(used_today_res.scalar_one() or 0)
    if used_today >= effective_cap:
        await record_audit_event(
            db,
            actor_type="SYSTEM",
            actor_id="WARMUP_QUOTA_GUARD",
            action="BLOGGER_WARMUP_QUOTA_EXCEEDED",
            resource_ref=f"blog:{blog.country_code}",
            details={"stage": blog.warmup_stage, "daily_cap": effective_cap, "local_date": local_date_str},
        )
        raise OverflowError(
            f"Warm-up quota reached for {blog.country_code} (Stage {blog.warmup_stage}: {used_today}/{effective_cap} posts on {local_date_str})."
        )

    slug = _slugify(draft.title)
    domain = blog.domain_name if blog.domain_name != "PENDING_USER_SELECTION" else f"{blog.country_code.lower()}.veltora-preview.blogspot.com"
    remote_post_id = f"blg_{blog.country_code.lower()}_{draft.id}_{idem_key[:8]}"
    published_url = f"https://{domain}/{local_date_str.replace('-', '/')}/{slug}.html"

    job = PublishJob(
        idempotency_key=idem_key,
        article_id=draft.id,
        country_code=blog.country_code,
        language_code=lang_code,
        scheduled_local_date=local_date_str,
        scheduled_utc_iso=utc_iso,
        jitter_minutes=jitter_mins,
        status="PUBLISHED",
        blogger_post_id=remote_post_id,
        published_url=published_url,
        created_by=actor_username,
        published_at=datetime.now(timezone.utc),
    )
    db.add(job)
    draft.workflow_state = "PUBLISHED"
    await db.commit()
    await db.refresh(job)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=actor_username,
        action="ARTICLE_PUBLISHED_TO_BLOGGER",
        resource_ref=f"article:{draft.id}:job:{job.id}",
        details={
            "country": blog.country_code,
            "blogger_post_id": remote_post_id,
            "url": published_url,
            "quota_used": used_today + 1,
            "quota_cap": effective_cap,
        },
    )

    return {
        "job_id": job.id,
        "idempotency_key": job.idempotency_key,
        "deduplicated": False,
        "status": job.status,
        "blogger_post_id": job.blogger_post_id,
        "published_url": job.published_url,
        "scheduled_local_date": job.scheduled_local_date,
        "jitter_minutes": job.jitter_minutes,
        "quota_remaining_today": max(0, effective_cap - (used_today + 1)),
    }


def generate_blogger_xml_theme(blog: CountryBlog, brand_name: str = "Veltora") -> str:
    primary_lang = blog.languages_csv.split(",")[0].strip()
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE html>
<html b:css='false' b:defaultwidgetversion='2' b:layoutsVersion='3' expr:lang='"{primary_lang}"' xmlns='http://www.w3.org/1999/xhtml' xmlns:b='http://www.google.com/2005/gml/b' xmlns:data='http://www.google.com/2005/gml/data' xmlns:expr='http://www.google.com/2005/gml/expr'>
<head>
  <meta charset='utf-8'/>
  <meta content='width=device-width, initial-scale=1' name='viewport'/>
  <title><data:blog.pageTitle/></title>
  <link rel='alternate' hreflang='{primary_lang}' expr:href='data:blog.canonicalUrl'/>
  <link rel='alternate' hreflang='x-default' expr:href='data:blog.canonicalUrl'/>
  <b:skin><![CDATA[
    :root {{
      --brand-primary: #0F2A3C;
      --brand-accent: #0D7A6E;
      --surface-bg: #F8FAFC;
      --text-main: #0F172A;
    }}
    body {{ font-family: Inter, system-ui, sans-serif; background: var(--surface-bg); color: var(--text-main); margin: 0; line-height: 1.65; }}
    header.site-header {{ background: var(--brand-primary); color: #FFFFFF; padding: 1rem 1.5rem; }}
    main.content-wrap {{ max-width: 820px; margin: 2rem auto; padding: 0 1.25rem; }}
    .ai-disclosure {{ background: #EFF6FF; border-left: 4px solid var(--brand-accent); padding: 0.75rem 1rem; font-size: 0.88rem; margin-bottom: 1.5rem; }}
    footer.site-footer {{ border-top: 1px solid #CBD5E1; padding: 1.5rem; font-size: 0.85rem; color: #475569; text-align: center; }}
  ]]></b:skin>
  <script type='application/ld+json'>
  {{
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "{brand_name} {blog.country_name}",
    "inLanguage": "{primary_lang}"
  }}
  </script>
</head>
<body>
  <header class='site-header'>
    <strong>{brand_name} — {blog.country_name} ({blog.country_code})</strong>
  </header>
  <div id='gdpr-consent-banner-slot' data-region='{blog.country_code}' aria-live='polite'></div>
  <main class='content-wrap'>
    <aside class='ai-disclosure'>Editorial Transparency: Content is produced with AI assistance and reviewed under EU quality and YMYL safety standards.</aside>
    <b:section id='main-posts' showaddelement='yes'>
      <b:widget id='Blog1' locked='true' title='Blog Posts' type='Blog' version='2'/>
    </b:section>
  </main>
  <footer class='site-footer'>
    <a href='/p/privacy-policy.html'>Privacy Policy</a> |
    <a href='/p/cookie-policy.html'>Cookie Policy</a> |
    <a href='/p/editorial-policy.html'>Editorial Policy</a> |
    <a href='/p/imprint.html'>Imprint / Legal Notice</a>
  </footer>
</body>
</html>
"""


async def save_theme_snapshot(
    db: AsyncSession, country_code: str, actor_username: str, brand_name: str = "Veltora"
) -> BloggerThemeSnapshot:
    blog = (
        await db.execute(select(CountryBlog).where(CountryBlog.country_code == country_code.upper()))
    ).scalar_one_or_none()
    if not blog:
        raise LookupError(f"Country blog {country_code} not found.")

    xml_text = generate_blogger_xml_theme(blog, brand_name=brand_name)
    snap = BloggerThemeSnapshot(
        country_code=blog.country_code,
        theme_version="v1.0-eu-compliant",
        xml_payload=xml_text,
        created_by=actor_username,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=actor_username,
        action="BLOGGER_XML_THEME_GENERATED",
        resource_ref=f"blog:{blog.country_code}:theme:{snap.id}",
        details={"theme_version": snap.theme_version, "brand": brand_name},
    )
    return snap
