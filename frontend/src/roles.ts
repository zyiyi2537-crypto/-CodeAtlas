export type UserRole = 'owner' | 'workspace_admin' | 'member' | 'admin'

const ADMIN_ROLES = new Set<UserRole>(['owner', 'workspace_admin', 'admin'])

export function isAdminRole(role: UserRole | undefined): boolean {
  return role !== undefined && ADMIN_ROLES.has(role)
}

export function isOwnerRole(role: UserRole | undefined): boolean {
  return role === 'owner'
}

export function canAssignRole(actorRole: UserRole, requestedRole: UserRole): boolean {
  if (actorRole === 'owner') return requestedRole !== 'admin'
  return isAdminRole(actorRole) && requestedRole === 'member'
}

export function canManageRole(actorRole: UserRole, targetRole: UserRole): boolean {
  if (actorRole === 'owner') return true
  return isAdminRole(actorRole) && targetRole === 'member'
}

export function roleLabel(role: UserRole): string {
  return {
    owner: '所有者',
    workspace_admin: '工作区管理员',
    member: '成员',
    admin: '旧版管理员',
  }[role]
}
