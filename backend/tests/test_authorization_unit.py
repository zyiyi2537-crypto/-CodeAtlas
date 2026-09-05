from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from codeatlas.api import ConventionCitation, validate_convention_citations
from codeatlas.authorization import AuthorizationScope
from codeatlas.models import Repository


def test_resource_actions_are_evaluated_against_the_owning_space_role() -> None:
    scope = AuthorizationScope(
        actor_user_id="member-1",
        space_ids=("viewer-space", "editor-space"),
        repository_ids=("viewer-repo", "editor-repo"),
        collection_ids=("viewer-docs", "editor-docs"),
        actions=frozenset({"read", "search", "edit", "generate"}),
        space_roles=(("viewer-space", "viewer"), ("editor-space", "editor")),
        repository_spaces=(
            ("viewer-repo", "viewer-space"),
            ("editor-repo", "editor-space"),
        ),
        collection_spaces=(
            ("viewer-docs", "viewer-space"),
            ("editor-docs", "editor-space"),
        ),
    )

    assert scope.permits_repository("editor-repo", "edit")
    assert not scope.permits_repository("viewer-repo", "edit")
    assert scope.permits_collection("editor-docs", "generate")
    assert not scope.permits_collection("viewer-docs", "generate")


def test_resource_permissions_fail_closed_without_an_owning_space_mapping() -> None:
    scope = AuthorizationScope(
        actor_user_id="member-1",
        space_ids=("space-1",),
        repository_ids=("repo-without-map",),
        collection_ids=("collection-without-map",),
        actions=frozenset({"read", "search"}),
        space_roles=(("space-1", "viewer"),),
    )

    assert not scope.permits_repository("repo-without-map", "read")
    assert not scope.permits_collection("collection-without-map", "read")


def test_space_permissions_fail_closed_without_a_role_mapping() -> None:
    scope = AuthorizationScope(
        actor_user_id="member-1",
        space_ids=("space-without-role",),
        repository_ids=(),
        collection_ids=(),
        actions=frozenset({"read", "search"}),
    )

    assert not scope.permits_space("space-without-role", "read")


def test_convention_citation_requires_the_complete_source_range() -> None:
    repository = Repository(
        id="repo-1",
        name="reference",
        git_url="https://github.com/example/reference.git",
        space_id="space-1",
        last_commit="a" * 40,
        created_by="admin-1",
    )
    session = SimpleNamespace(
        get=lambda model, record_id: (
            repository if model is Repository and record_id == repository.id else None
        )
    )

    class Retriever:
        def get_file(self, *_args, **_kwargs):
            return {
                "content": "     1: first line",
                "start_line": 1,
                "end_line": 1,
            }

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(retriever=Retriever())))
    scope = AuthorizationScope(
        actor_user_id="admin-1",
        space_ids=("space-1",),
        repository_ids=(repository.id,),
        collection_ids=(),
        actions=frozenset({"read", "manage"}),
        space_roles=(("space-1", "manager"),),
        repository_spaces=((repository.id, "space-1"),),
    )
    citation = ConventionCitation(
        repository_id=repository.id,
        commit=repository.last_commit,
        path="src/example.py",
        start_line=1,
        end_line=10,
    )

    with pytest.raises(HTTPException) as error:
        validate_convention_citations(request, session, scope, "space-1", [citation])

    assert error.value.status_code == 422
