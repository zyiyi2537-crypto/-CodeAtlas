from __future__ import annotations

import json

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from .member_lifecycle_lock import member_lifecycle_lock
from .models import AuditEvent, User

OWNER_ROLE = "owner"
WORKSPACE_ADMIN_ROLE = "workspace_admin"
MEMBER_ROLE = "member"
LEGACY_ADMIN_ROLE = "admin"

ADMIN_ROLES = frozenset({OWNER_ROLE, WORKSPACE_ADMIN_ROLE, LEGACY_ADMIN_ROLE})
ASSIGNABLE_ROLES = frozenset({OWNER_ROLE, WORKSPACE_ADMIN_ROLE, MEMBER_ROLE})
PRIVILEGED_ROLES = ADMIN_ROLES


def is_admin_role(role: str) -> bool:
    return role in ADMIN_ROLES


def is_owner_role(role: str) -> bool:
    return role == OWNER_ROLE


def can_assign_role(actor_role: str, requested_role: str) -> bool:
    if requested_role not in ASSIGNABLE_ROLES:
        return False
    if is_owner_role(actor_role):
        return True
    return is_admin_role(actor_role) and requested_role == MEMBER_ROLE


def can_manage_role(actor_role: str, target_role: str) -> bool:
    if is_owner_role(actor_role):
        return True
    return is_admin_role(actor_role) and target_role == MEMBER_ROLE


def configure_single_workspace_roles(engine: Engine, owner_email: str) -> dict[str, int]:
    """Atomically designate one owner and make every other account an admin."""
    normalized_email = owner_email.strip().lower()
    if not normalized_email:
        raise ValueError("Designated owner email is required")

    with member_lifecycle_lock(engine) as connection:
        with Session(connection) as session:
            users = session.exec(
                select(User)
                .order_by(col(User.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            designated_owner = next(
                (user for user in users if user.email.strip().lower() == normalized_email),
                None,
            )
            if designated_owner is None:
                raise ValueError("Designated owner does not exist")
            if not designated_owner.is_active:
                raise ValueError("Designated owner must be active")

            for user in users:
                user.role = (
                    OWNER_ROLE if user.id == designated_owner.id else WORKSPACE_ADMIN_ROLE
                )
                session.add(user)
            session.add(
                AuditEvent(
                    action="roles.configure_single_workspace",
                    target_type="user",
                    target_id=designated_owner.id,
                    detail_json=json.dumps(
                        {
                            "owner_email": normalized_email,
                            "owner_count": 1,
                            "workspace_admin_count": len(users) - 1,
                        },
                        sort_keys=True,
                    ),
                )
            )
            session.commit()

    return {
        OWNER_ROLE: 1,
        WORKSPACE_ADMIN_ROLE: len(users) - 1,
    }
