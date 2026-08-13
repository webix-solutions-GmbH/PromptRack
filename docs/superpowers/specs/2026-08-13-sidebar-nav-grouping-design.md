# Sidebar navigation grouping

## Problem

`src/components/sidebar-nav.tsx` renders ten links as one flat list. Nothing tells
the reader that Prompts, System Prompts, Toolsets and Machines are things you
configure *before* a run, while Runs and Results are what you look at *after* one,
and that the last three are account plumbing. Page-level headings are already fine
(`/`, `/runs`, `/prompts`, `/machines` all carry an `h1`), so this is a navigation
problem only.

## Design

Group the links into four sections that follow the order of work:

```
  Dashboard          (no heading)

  SETUP
    Prompts
    System Prompts
    Toolsets
    Machines

  EVALUATE
    Runs
    Results

  SETTINGS
    Workspaces
    API tokens
    Users            (admin only)
```

`NAV_ITEMS` becomes `NAV_SECTIONS`: `{ label: string | null, items: NavItem[] }[]`.
The first section carries `label: null` and renders Dashboard with no heading.

`SETTINGS`, not `ADMIN`: Workspaces and API tokens are member-accessible, so
`ADMIN` would be wrong for everyone but the administrator.

Machines sits under `SETUP` rather than in a section of its own — a heading over a
single item is noise, and a machine is configuration like the rest of that group.

### Rendering

Each section is a `div` with `flex flex-col gap-1`. A labelled section gets
`mt-5` and a heading styled `px-3 pb-1 text-[11px] font-medium uppercase
tracking-wider text-zinc-400 dark:text-zinc-500`. Link markup, classes and the
active-state rule (`href === '/' ? pathname === '/' : pathname.startsWith(href)`)
are unchanged.

### Role filtering

The `adminOnly` filter now runs per section, and a section whose items all filter
away renders nothing — heading included. Only `Users` is `adminOnly` today, so no
section can actually empty out; the guard exists so that adding a second
admin-only item later cannot leave a heading with nothing under it.

## Out of scope

Page headings, the workspace switcher, the user menu, sidebar width, and any new
call-to-action button. The change touches `src/components/sidebar-nav.tsx` and
nothing else.

## Verification

`npx tsc --noEmit`, `npm run lint`, and a look at the running app as an admin and
as a member.
