// Dark/light/system display preference. Deliberately localStorage and NOT a
// column on the user row (unlike `users.active_customer_id`, which lives on
// the server precisely because it must be unforgeable and survive a session
// refresh): this is a per-*device* preference, not a per-user one — the same
// signed-in person can reasonably want dark on a home machine and light on a
// work laptop, and neither should overwrite the other's choice at login.
//
// STORAGE_KEY must stay byte-identical to the key the inline script in
// frontend/index.html reads — that script sets the `dark` class before this
// store (or even the Vue bundle) has loaded, to avoid a flash of the wrong
// theme; see that file for why it can't just wait for `init()` below.
import { defineStore } from 'pinia'

export type ThemeMode = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const STORAGE_KEY = 'promptrack-theme'

const SYSTEM_DARK_QUERY = '(prefers-color-scheme: dark)'

// Both accessors swallow storage failures for the same reason the inline
// script in index.html does: `localStorage` throws outright in some
// private-browsing modes and when site data is disabled, and losing the
// theme preference must never be the thing that stops the app booting.
// Falling back to `system` is the honest degradation — it is also the
// default, so a user who never chose still gets what they expect.
function readStoredMode(): ThemeMode {
  let stored: string | null = null
  try {
    stored = localStorage.getItem(STORAGE_KEY)
  } catch {
    return 'system'
  }
  return stored === 'light' || stored === 'dark' || stored === 'system' ? stored : 'system'
}

export const useThemeStore = defineStore('theme', {
  state: () => ({
    mode: readStoredMode() as ThemeMode,
    // The OS preference, tracked live so `resolved` can follow it while in
    // `system` mode without polling — read once here and kept current by the
    // `change` listener `init()` registers.
    systemPrefersDark: window.matchMedia(SYSTEM_DARK_QUERY).matches,
    mediaQuery: null as MediaQueryList | null,
    onSystemChange: null as ((event: MediaQueryListEvent) => void) | null,
  }),
  getters: {
    /** What the app should actually render: `mode` when explicit, the OS
     * preference when `mode` is `system`. The theme toggle icon and every
     * `dark` class decision reads this, never `mode` directly, so `system`
     * is never visually ambiguous. */
    resolved(state): ResolvedTheme {
      return state.mode === 'system' ? (state.systemPrefersDark ? 'dark' : 'light') : state.mode
    },
  },
  actions: {
    /** Called once from AppLayout's setup, which is mounted for the whole
     * app's lifetime (signed in or not — the login screen has no toggle but
     * still gets the class the persisted choice implies). Registers the
     * `prefers-color-scheme` listener so flipping the OS theme repaints the
     * app immediately in `system` mode, rather than only reflecting "system
     * as of page load". Idempotent so a hot-reloaded root component can't
     * stack a second listener. */
    init() {
      if (this.mediaQuery) return
      this.applyClass()
      this.mediaQuery = window.matchMedia(SYSTEM_DARK_QUERY)
      this.onSystemChange = (event) => {
        this.systemPrefersDark = event.matches
        if (this.mode === 'system') this.applyClass()
      }
      this.mediaQuery.addEventListener('change', this.onSystemChange)
    },
    setMode(mode: ThemeMode) {
      this.mode = mode
      try {
        localStorage.setItem(STORAGE_KEY, mode)
      } catch {
        // Storage unavailable or full: the choice still applies for this
        // page's lifetime, it just will not survive a reload.
      }
      this.applyClass()
    },
    /** Mirrors `style.css`'s `:root:has(.dark), .dark` selector and the
     * `darkModeSelector: '.dark'` PrimeVue is configured with in main.ts —
     * this is the one place that adds or removes the class both key off. */
    applyClass() {
      document.documentElement.classList.toggle('dark', this.resolved === 'dark')
    },
  },
})
