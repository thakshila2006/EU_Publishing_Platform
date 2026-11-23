import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceEvaluation
from app.models.content_ai import ArticleDraft, YmylReview
from app.models.foundation import CountryBlog
from app.services.audit import record_audit_event

EU_COUNTRY_CODES = {"UK", "DE", "NL", "SE", "NO", "DK", "CH"}


async def evaluate_article_compliance(
    db: AsyncSession,
    *,
    article_id: int,
    actor_username: str,
    promote_if_passed: bool = True,
) -> dict:
    draft = (await db.execute(select(ArticleDraft).where(ArticleDraft.id == article_id))).scalar_one_or_none()
    if not draft:
        raise LookupError(f"Article {article_id} not found.")

    blog = (
        await db.execute(select(CountryBlog).where(CountryBlog.country_code == draft.country_code))
    ).scalar_one_or_none()

    findings: list[dict] = []
    seo_score = 100

    title_len = len(draft.title.strip())
    body_words = len(draft.body_markdown.strip().split())

    if title_len < 12 or title_len > 90:
        seo_score -= 15
        findings.append({"code": "SEO_TITLE_LENGTH", "severity": "WARN", "message": f"Title length {title_len} outside 12-90 chars."})

    if body_words < 15:
        seo_score -= 25
        findings.append({"code": "QA_BODY_DEPTH", "severity": "BLOCK", "message": f"Body word count ({body_words}) is below minimum threshold."})

    try:
        citations = json.loads(draft.citations_json or "[]")
    except json.JSONDecodeError:
        citations = []

    if draft.is_ymyl and len(citations) == 0:
        seo_score -= 20
        findings.append({"code": "YMYL_CITATIONS_MISSING", "severity": "WARN", "message": "YMYL article lacks structured source citations."})

    seo_score = max(0, min(100, seo_score))
    qa_passed = body_words >= 15 and seo_score >= 60

    # EU GDPR & DSA transparency check
    has_disclosure = bool(draft.disclosure_text and draft.disclosure_text.strip())
    country_supported = draft.country_code.upper() in EU_COUNTRY_CODES and (blog is not None)
    gdpr_dsa_compliant = has_disclosure and country_supported

    if not has_disclosure:
        findings.append({"code": "DSA_AI_DISCLOSURE_MISSING", "severity": "BLOCK", "message": "AI/editorial transparency disclosure is required."})
    if not country_supported:
        findings.append({"code": "EU_COUNTRY_SCOPE", "severity": "BLOCK", "message": f"Country {draft.country_code} is outside configured target set."})

    # YMYL human approval check
    ymyl_gate_satisfied = True
    if draft.is_ymyl:
        rev = (await db.execute(select(YmylReview).where(YmylReview.article_id == draft.id))).scalar_one_or_none()
        if not rev or rev.decision != "APPROVED":
            ymyl_gate_satisfied = False
            findings.append({
                "code": "YMYL_HUMAN_APPROVAL_REQUIRED",
                "severity": "BLOCK",
                "message": "YMYL article requires APPROVED human expert review before scheduling/publishing.",
            })

    passed_all = qa_passed and gdpr_dsa_compliant and ymyl_gate_satisfied
    overall_status = "PASSED" if passed_all else "BLOCKED"

    if promote_if_passed and passed_all and draft.workflow_state in {"DRAFT", "READY_FOR_QA", "APPROVED_FOR_SCHEDULE"}:
        if draft.workflow_state != "PUBLISHED":
            draft.workflow_state = "APPROVED_FOR_SCHEDULE"

    evaluation = ComplianceEvaluation(
        article_id=draft.id,
        country_code=draft.country_code,
        language_code=draft.language_code,
        seo_score=seo_score,
        qa_passed=qa_passed,
        gdpr_dsa_compliant=gdpr_dsa_compliant,
        ymyl_gate_satisfied=ymyl_gate_satisfied,
        overall_status=overall_status,
        findings_json=json.dumps(findings, ensure_ascii=False),
        evaluated_by=actor_username,
    )
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)

    await record_audit_event(
        db,
        actor_type="HUMAN",
        actor_id=actor_username,
        action="COMPLIANCE_EVALUATION_RECORDED",
        resource_ref=f"article:{draft.id}:eval:{evaluation.id}",
        details={
            "status": overall_status,
            "seo_score": seo_score,
            "gdpr_dsa_compliant": gdpr_dsa_compliant,
            "ymyl_gate_satisfied": ymyl_gate_satisfied,
        },
    )

    return {
        "evaluation_id": evaluation.id,
        "article_id": draft.id,
        "country_code": draft.country_code,
        "language_code": draft.language_code,
        "seo_score": seo_score,
        "qa_passed": qa_passed,
        "gdpr_dsa_compliant": gdpr_dsa_compliant,
        "ymyl_gate_satisfied": ymyl_gate_satisfied,
        "overall_status": overall_status,
        "workflow_state": draft.workflow_state,
        "findings": findings,
    }
