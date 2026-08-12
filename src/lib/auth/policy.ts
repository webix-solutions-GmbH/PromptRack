/**
 * What each role may do — the only place role semantics are written down.
 *
 * Pure on purpose: every guard, every hidden button and the MCP read-only gate
 * ask these two predicates, so the answer cannot drift between call sites.
 */

export const ROLES = ['admin', 'member', 'viewer'] as const;
export type Role = (typeof ROLES)[number];

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer',
};

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: 'Everything a member can do, plus user management, machines and toolset credentials.',
  member: 'Prompts, system prompts, tools, runs and ratings.',
  viewer: 'Read-only.',
};

/** Unknown/legacy values degrade to the least privileged role, never to admin. */
export function parseRole(value: unknown): Role {
  return typeof value === 'string' && (ROLES as readonly string[]).includes(value)
    ? (value as Role)
    : 'viewer';
}

/** May change content: prompts, system prompts, tools, runs, ratings. */
export function canWrite(role: Role): boolean {
  return role === 'admin' || role === 'member';
}

/** May change infrastructure and users: machines, toolsets, roles, other users' tokens. */
export function canAdminister(role: Role): boolean {
  return role === 'admin';
}
