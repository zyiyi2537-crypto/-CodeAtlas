from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, col, select

from .models import (
    DocumentCollection,
    KnowledgeSpace,
    Repository,
    RepositoryAccess,
    SpaceGrant,
    User,
)
from .roles import is_admin_role

SPACE_ROLE_ACTIONS = {
    "viewer": frozenset({"read", "search"}),
    "editor": frozenset({"read", "search", "generate", "edit"}),
    "manager": frozenset({"read", "search", "generate", "edit", "manage"}),
}


@dataclass(frozen=True)
class AuthorizationScope:
    """Resolved storage-level boundaries for one browser or token identity."""

    actor_user_id: str | None
    space_ids: tuple[str, ...]
    repository_ids: tuple[str, ...]
    collection_ids: tuple[str, ...]
    actions: frozenset[str]
    space_roles: tuple[tuple[str, str], ...] = ()
    repository_spaces: tuple[tuple[str, str], ...] = ()
    collection_spaces: tuple[tuple[str, str], ...] = ()

    def permits_space(self, space_id: str, action: str = "read") -> bool:
        if action not in self.actions or space_id not in self.space_ids:
            return False
        roles = dict(self.space_roles)
        return action in SPACE_ROLE_ACTIONS.get(roles.get(space_id, ""), frozenset())

    def permits_repository(self, repository_id: str, action: str = "read") -> bool:
        if action not in self.actions or repository_id not in self.repository_ids:
            return False
        space_id = dict(self.repository_spaces).get(repository_id)
        return space_id is not None and self.permits_space(space_id, action)

    def permits_collection(self, collection_id: str, action: str = "read") -> bool:
        if action not in self.actions or collection_id not in self.collection_ids:
            return False
        space_id = dict(self.collection_spaces).get(collection_id)
        return space_id is not None and self.permits_space(space_id, action)


def _space_roles(session: Session, user: User) -> dict[str, str]:
    spaces = session.exec(select(KnowledgeSpace)).all()
    if is_admin_role(user.role):
        return {space.id: "manager" for space in spaces}

    roles = {
        space.id: "viewer"
        for space in spaces
        if space.visibility == "workspace"
    }
    grants = session.exec(select(SpaceGrant).where(SpaceGrant.user_id == user.id)).all()
    for grant in grants:
        roles[grant.space_id] = grant.role
    return roles


def resolve_authorization_scope(
    session: Session,
    user: User | None,
    *,
    allow_anonymous_repositories: bool = False,
) -> AuthorizationScope:
    if user is None:
        repositories: list[Repository] = []
        if allow_anonymous_repositories:
            repositories = list(
                session.exec(
                    select(Repository)
                    .join(
                        KnowledgeSpace,
                        col(KnowledgeSpace.id) == col(Repository.space_id),
                    )
                    .where(
                        Repository.visibility == "public",
                        KnowledgeSpace.visibility == "workspace",
                    )
                ).all()
            )
        repository_ids = tuple(sorted(repository.id for repository in repositories))
        space_ids = tuple(sorted({repository.space_id for repository in repositories}))
        return AuthorizationScope(
            actor_user_id=None,
            space_ids=space_ids,
            repository_ids=repository_ids,
            collection_ids=(),
            actions=frozenset({"read", "search"}) if repository_ids else frozenset(),
            space_roles=tuple((space_id, "viewer") for space_id in space_ids),
            repository_spaces=tuple(
                sorted(
                    (repository.id, repository.space_id)
                    for repository in repositories
                )
            ),
        )

    if not user.is_active:
        return AuthorizationScope(None, (), (), (), frozenset())

    roles = _space_roles(session, user)
    space_ids = tuple(sorted(roles))
    if not space_ids:
        return AuthorizationScope(user.id, (), (), (), frozenset())

    repository_statement = select(Repository).where(
        col(Repository.space_id).in_(space_ids)
    )
    if not is_admin_role(user.role):
        granted_ids = set(
            session.exec(
                select(RepositoryAccess.repository_id).where(
                    RepositoryAccess.user_id == user.id
                )
            ).all()
        )
        if granted_ids:
            repository_statement = repository_statement.where(
                (col(Repository.visibility) == "public")
                | (col(Repository.id).in_(granted_ids))
            )
        else:
            repository_statement = repository_statement.where(
                Repository.visibility == "public"
            )
    repositories = list(session.exec(repository_statement).all())
    repository_ids = tuple(sorted(repo.id for repo in repositories))
    collections = list(
        session.exec(
            select(DocumentCollection).where(
                col(DocumentCollection.space_id).in_(space_ids)
            )
        ).all()
    )
    collection_ids = tuple(sorted(collection.id for collection in collections))
    actions = {"read", "search"}
    for role in roles.values():
        actions.update(SPACE_ROLE_ACTIONS.get(role, frozenset()))
    return AuthorizationScope(
        actor_user_id=user.id,
        space_ids=space_ids,
        repository_ids=repository_ids,
        collection_ids=collection_ids,
        actions=frozenset(actions),
        space_roles=tuple(sorted(roles.items())),
        repository_spaces=tuple(
            sorted((repository.id, repository.space_id) for repository in repositories)
        ),
        collection_spaces=tuple(
            sorted((collection.id, collection.space_id) for collection in collections)
        ),
    )


def resolve_token_scope(
    session: Session,
    user: User,
    repository_ids: tuple[str, ...],
    space_ids: tuple[str, ...],
) -> AuthorizationScope:
    base = resolve_authorization_scope(session, user)
    allowed_spaces = set(base.space_ids)
    if space_ids:
        allowed_spaces.intersection_update(space_ids)

    allowed_repositories = set(base.repository_ids)
    if repository_ids:
        allowed_repositories.intersection_update(repository_ids)
    else:
        public_ids = set(
            session.exec(
                select(Repository.id).where(Repository.visibility == "public")
            ).all()
        )
        allowed_repositories.intersection_update(public_ids)
    repositories = session.exec(
        select(Repository.id, Repository.space_id).where(
            col(Repository.id).in_(allowed_repositories)
        )
    ).all() if allowed_repositories else []
    allowed_repositories = {
        repository_id
        for repository_id, space_id in repositories
        if space_id in allowed_spaces
    }

    collections = session.exec(
        select(DocumentCollection.id, DocumentCollection.space_id).where(
            col(DocumentCollection.id).in_(base.collection_ids)
        )
    ).all() if base.collection_ids else []
    allowed_collections = {
        collection_id
        for collection_id, space_id in collections
        if space_id in allowed_spaces
    }
    roles = tuple(
        (space_id, role)
        for space_id, role in base.space_roles
        if space_id in allowed_spaces
    )
    return AuthorizationScope(
        actor_user_id=user.id,
        space_ids=tuple(sorted(allowed_spaces)),
        repository_ids=tuple(sorted(allowed_repositories)),
        collection_ids=tuple(sorted(allowed_collections)),
        actions=base.actions,
        space_roles=roles,
        repository_spaces=tuple(
            (repository_id, space_id)
            for repository_id, space_id in base.repository_spaces
            if repository_id in allowed_repositories
        ),
        collection_spaces=tuple(
            (collection_id, space_id)
            for collection_id, space_id in base.collection_spaces
            if collection_id in allowed_collections
        ),
    )
