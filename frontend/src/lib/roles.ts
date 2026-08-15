// The role vocabulary, in one place — the client-side copy of
// `backend/app/auth/policy.py`'s `ROLES`, `ROLE_LABELS` and
// `ROLE_DESCRIPTIONS`, whose wording is reproduced verbatim so the role
// picker on `/users` says exactly what the guards enforce rather than a
// paraphrase of it.
//
// Same shape as `lib/rating.ts`: a wire vocabulary plus the small lookup the
// components render it through. `stores/auth.ts` re-exports `Role` from here
// rather than restating it, so a role added to the column has one list to
// appear in on this side of the wire, not two that can disagree.

export type Role = 'admin' | 'member' | 'viewer'

/** Every role, most privileged first — the order `app.models.auth.UserRole`'s
 * `Literal` has, which is what `policy.py`'s `ROLES` is derived from. */
export const ROLES: readonly Role[] = ['admin', 'member', 'viewer']

export const ROLE_LABELS: Record<Role, string> = {
  admin: 'Admin',
  member: 'Member',
  viewer: 'Viewer',
}

export const ROLE_DESCRIPTIONS: Record<Role, string> = {
  admin: 'Everything a member can do, plus user management, endpoints and toolset credentials.',
  member: 'Prompts, versions, test cases, runs and ratings.',
  viewer: 'Read-only.',
}

export interface RoleOption {
  value: Role
  label: string
  description: string
}

/** Ready to hand to a PrimeVue `Select` (`option-label`/`option-value`), with
 * the description available to an `#option` template. Mutably typed because
 * that component's `options` prop is `any[]`, which a `readonly` array is not
 * assignable to. */
export const ROLE_OPTIONS: RoleOption[] = ROLES.map((role) => ({
  value: role,
  label: ROLE_LABELS[role],
  description: ROLE_DESCRIPTIONS[role],
}))
