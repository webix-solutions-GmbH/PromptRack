<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Password from 'primevue/password'
import Message from 'primevue/message'
import Card from 'primevue/card'
import { ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'
import BrandMark from '../components/BrandMark.vue'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

async function submit() {
  error.value = null
  submitting.value = true
  try {
    await auth.login(email.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    await router.replace(redirect)
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : 'Sign in failed.'
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
      <template #subtitle>Sign in to continue</template>
      <template #content>
        <form class="auth-form" @submit.prevent="submit">
          <div class="field">
            <label for="login-email">Email</label>
            <InputText id="login-email" v-model="email" type="email" autocomplete="email" required autofocus />
          </div>
          <div class="field">
            <label for="login-password">Password</label>
            <Password
              id="login-password"
              v-model="password"
              :feedback="false"
              toggle-mask
              autocomplete="current-password"
              input-class="w-full"
              required
            />
          </div>
          <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>
          <Button type="submit" label="Sign in" :loading="submitting" class="w-full" />
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

/* Same lockup in SetupView and InviteAcceptView — these three screens
 * duplicate their frame rather than share a layout, and this follows suit. */
.auth-brand {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  letter-spacing: -0.01em;
}

.auth-form {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.w-full {
  width: 100%;
}
</style>
