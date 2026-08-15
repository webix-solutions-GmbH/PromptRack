<script setup lang="ts">
// Redeeming an invite link: the only way an account is created after the
// first one, since sign-up closes forever once the `users` table is
// non-empty. Public, and modelled on SetupView — the invitee supplies exactly
// what the bootstrap account's owner did (email, name, password). What they
// cannot supply is a role: the invite already decided that, and the page only
// shows it.
//
// Three states, because the backend draws the same three: a valid link (410
// vs 404 is worth distinguishing — "no longer usable" and "not real" are
// different things for the person holding it to read), a spent or expired
// one, and one that matches nothing.
//
// The route is public but the router guard sends a signed-in visitor home:
// redeeming an invite while signed in as someone else is not a coherent
// action.
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import Card from 'primevue/card'
import InputText from 'primevue/inputtext'
import Message from 'primevue/message'
import Password from 'primevue/password'
import Tag from 'primevue/tag'
import { ApiError } from '../api/client'
import { invitesApi, type InviteOfferView } from '../api/invites'
import { ROLE_LABELS } from '../lib/roles'
import { formatDateTime } from '../lib/format'
import { useAuthStore } from '../stores/auth'
import BrandMark from '../components/BrandMark.vue'

const props = defineProps<{ token: string }>()

const auth = useAuthStore()
const router = useRouter()

/** `loading` until the offer resolves, then exactly one of the three the
 * backend can answer with. */
type State = 'loading' | 'valid' | 'gone' | 'unknown'

const state = ref<State>('loading')
const offer = ref<InviteOfferView | null>(null)
const loadError = ref<string | null>(null)

onMounted(async () => {
  try {
    offer.value = await invitesApi.read(props.token)
    state.value = 'valid'
  } catch (err) {
    if (err instanceof ApiError) {
      loadError.value = err.message
      state.value = err.status === 410 ? 'gone' : 'unknown'
    } else {
      loadError.value = 'Could not check this invite link.'
      state.value = 'unknown'
    }
  }
})

// --- the form -------------------------------------------------------------

/** `app.auth.passwords.MIN_PASSWORD_LENGTH`. Stated here so the form says so
 * before the request rather than after a 422. */
const MIN_PASSWORD_LENGTH = 12

const name = ref('')
const email = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

const passwordsMatch = computed(
  () => confirmPassword.value.length === 0 || confirmPassword.value === password.value,
)

async function submit() {
  error.value = null
  if (password.value !== confirmPassword.value) {
    error.value = 'Passwords do not match.'
    return
  }
  submitting.value = true
  try {
    // Called through `invitesApi` rather than through the auth store: this
    // runs before there is a user. The response is the same `MeResponse`
    // `/auth/me` answers with and the session cookie is already set, so the
    // store can be filled directly instead of re-reading it.
    const me = await invitesApi.accept(props.token, {
      email: email.value,
      password: password.value,
      name: name.value,
    })
    auth.applyMe(me)
    await router.replace({ name: 'home' })
  } catch (err) {
    if (err instanceof ApiError && err.status === 410) {
      // Someone else got here first, or it expired while the form was open.
      loadError.value = err.message
      state.value = 'gone'
      return
    }
    error.value = err instanceof ApiError ? err.message : 'Could not create the account.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <Card class="auth-card">
      <template #title>
        <span class="auth-brand"><BrandMark :size="32" />PromptRack</span>
      </template>
      <template #subtitle>
        <template v-if="state === 'valid'">You've been invited</template>
        <template v-else-if="state === 'loading'">Checking your invite…</template>
        <template v-else>Invite link</template>
      </template>
      <template #content>
        <p v-if="state === 'loading'" class="hint">One moment.</p>

        <div v-else-if="state === 'gone' || state === 'unknown'" class="invite-dead">
          <Message severity="warn" :closable="false">
            {{
              loadError ??
              (state === 'gone'
                ? 'That invite link is no longer usable.'
                : 'That invite link is not valid.')
            }}
          </Message>
          <p class="hint">
            {{
              state === 'gone'
                ? 'Invite links work once, and expire if nobody uses them.'
                : 'Check that you copied the whole link, or ask an administrator for a new one.'
            }}
          </p>
          <Button label="Go to sign in" text class="w-full" @click="router.replace('/login')" />
        </div>

        <form v-else class="auth-form" @submit.prevent="submit">
          <p class="invite-offer">
            This invite creates an account with the role
            <Tag :value="ROLE_LABELS[offer!.role]" severity="info" />
            and is usable until {{ formatDateTime(offer!.expires_at) }}.
          </p>
          <div class="field">
            <label for="invite-name">Name</label>
            <InputText id="invite-name" v-model="name" autocomplete="name" required autofocus />
          </div>
          <div class="field">
            <label for="invite-email">Email</label>
            <InputText
              id="invite-email"
              v-model="email"
              type="email"
              autocomplete="email"
              required
            />
          </div>
          <div class="field">
            <label for="invite-password">Password</label>
            <Password
              id="invite-password"
              v-model="password"
              toggle-mask
              autocomplete="new-password"
              input-class="w-full"
              required
            />
            <small class="hint">At least {{ MIN_PASSWORD_LENGTH }} characters.</small>
          </div>
          <div class="field">
            <label for="invite-confirm-password">Confirm password</label>
            <Password
              id="invite-confirm-password"
              v-model="confirmPassword"
              :feedback="false"
              toggle-mask
              autocomplete="new-password"
              input-class="w-full"
              :invalid="!passwordsMatch"
              required
            />
            <small v-if="!passwordsMatch" class="field-error">Passwords do not match.</small>
          </div>
          <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>
          <Button type="submit" label="Create account" :loading="submitting" class="w-full" />
        </form>
      </template>
    </Card>
  </div>
</template>

<style scoped>
/* Same frame as LoginView/SetupView — this is the third screen that renders
 * outside the app shell, and it should read as one of them. */
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 1.5rem;
}

.auth-card {
  width: 100%;
  max-width: 22rem;
}

/* Same lockup in LoginView and SetupView — these three screens
 * duplicate their frame rather than share a layout, and this follows suit. */
.auth-brand {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  letter-spacing: -0.01em;
}

.auth-form,
.invite-dead {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.invite-offer {
  margin: 0;
  font-size: 0.875rem;
  color: var(--p-text-muted-color);
}

.field-error {
  color: var(--p-red-500, #ef4444);
}

.w-full {
  width: 100%;
}
</style>
