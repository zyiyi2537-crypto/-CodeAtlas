from __future__ import annotations

import json

from sqlmodel import Session, col, select

from .authorization import AuthorizationScope
from .models import CompanyConvention


def serialize_convention(convention: CompanyConvention) -> dict:
    return {
        "id": convention.id,
        "space_id": convention.space_id,
        "title": convention.title,
        "category": convention.category,
        "language": convention.language,
        "framework": convention.framework,
        "task": convention.task,
        "rule": convention.rule,
        "prohibited_pattern": convention.prohibited_pattern,
        "examples": json.loads(convention.examples_json or "[]"),
        "citations": json.loads(convention.citations_json or "[]"),
        "status": convention.status,
        "updated_at": convention.updated_at,
    }


def _authorized_convention(
    convention: CompanyConvention,
    authorization_scope: AuthorizationScope,
) -> dict | None:
    serialized = serialize_convention(convention)
    citations = [
        citation
        for citation in serialized["citations"]
        if authorization_scope.permits_repository(
            str(citation.get("repository_id", ""))
        )
    ]
    if not citations:
        return None
    serialized["citations"] = citations
    return serialized


def find_company_conventions(
    session: Session,
    authorization_scope: AuthorizationScope,
    *,
    language: str = "",
    framework: str = "",
    task: str = "",
    include_unconfirmed: bool = False,
) -> list[dict]:
    if not authorization_scope.space_ids:
        return []
    statement = select(CompanyConvention).where(
        col(CompanyConvention.space_id).in_(authorization_scope.space_ids)
    )
    if not include_unconfirmed:
        statement = statement.where(CompanyConvention.status == "confirmed")
    conventions = list(
        session.exec(statement.order_by(col(CompanyConvention.updated_at).desc())).all()
    )
    normalized_language = language.strip().lower()
    normalized_framework = framework.strip().lower()
    task_terms = {term for term in task.strip().lower().split() if term}

    def applies(item: CompanyConvention) -> bool:
        item_language = item.language.strip().lower()
        item_framework = item.framework.strip().lower()
        if normalized_language and item_language not in {"", normalized_language}:
            return False
        if normalized_framework and item_framework not in {"", normalized_framework}:
            return False
        if not task_terms:
            return True
        searchable = " ".join(
            [item.title, item.category, item.task, item.rule]
        ).lower()
        return any(term in searchable for term in task_terms) or not item.task.strip()

    results = [
        authorized
        for item in conventions
        if applies(item)
        and (authorized := _authorized_convention(item, authorization_scope)) is not None
    ]
    return results[:50]
