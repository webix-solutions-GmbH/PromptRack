<script setup lang="ts">
// First-run sign-up: reachable only while the `users` table is empty (the
// router guard sends every unauthenticated visitor here instead of /login
// while `auth.setupRequired` is true, and back to /login once an account
// exists). The account created here becomes the administrator.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Message from 'primevue/message'
import Card from 'primevue/card'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()

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
    await auth.signUp(name.value, email.value, password.value)
    await router.replace('/')
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Could not create the account.'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <Card class="auth-card">
      <template #title>PromptRack</template>
      <template #subtitle>Create the administrator account</template>
      <template #content>
        <form class="auth-form" @submit.prevent="submit">
          <div class="field">
            <label for="setup-name">Name</label>
            <InputText id="setup-name" v-model="name" autocomplete="name" required autofocus />
          </div>
          <div class="field">
            <label for="setup-email">Email</label>
            <InputText id="setup-email" v-model="email" type="email" autocomplete="email" required />
          </div>
          <div class="field">
            <label for="setup-password">Password</label>
            <Password
              id="setup-password"
              v-model="password"
              toggle-mask
              input-class="w-full"
              required
            />
          </div>
          <div class="field">
            <label for="setup-confirm-password">Confirm password</label>
            <Password
              id="setup-confirm-password"
              v-model="confirmPassword"
              :feedback="false"
              toggle-mask
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

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.field label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--p-text-muted-color);
}

.field-error {
  color: var(--p-red-500, #ef4444);
}

.w-full {
  width: 100%;
}
</style>
