import { describe, expect, it } from 'vitest'

import {
  canAssignRole,
  canManageRole,
  isAdminRole,
  isOwnerRole,
  roleLabel,
} from '@/roles'

describe('workspace roles', () => {
  it('recognizes owner and workspace administrators as administrative roles', () => {
    expect(isAdminRole('owner')).toBe(true)
    expect(isAdminRole('workspace_admin')).toBe(true)
    expect(isAdminRole('admin')).toBe(true)
    expect(isAdminRole('member')).toBe(false)
    expect(isOwnerRole('owner')).toBe(true)
    expect(isOwnerRole('workspace_admin')).toBe(false)
  })

  it('enforces the role hierarchy in member controls', () => {
    expect(canManageRole('owner', 'workspace_admin')).toBe(true)
    expect(canManageRole('workspace_admin', 'member')).toBe(true)
    expect(canManageRole('workspace_admin', 'workspace_admin')).toBe(false)
    expect(canManageRole('workspace_admin', 'owner')).toBe(false)
    expect(canAssignRole('workspace_admin', 'member')).toBe(true)
    expect(canAssignRole('workspace_admin', 'workspace_admin')).toBe(false)
    expect(canAssignRole('owner', 'workspace_admin')).toBe(true)
  })

  it('renders human-readable Chinese role labels', () => {
    expect(roleLabel('owner')).toBe('所有者')
    expect(roleLabel('workspace_admin')).toBe('工作区管理员')
    expect(roleLabel('member')).toBe('成员')
    expect(roleLabel('admin')).toBe('旧版管理员')
  })
})
