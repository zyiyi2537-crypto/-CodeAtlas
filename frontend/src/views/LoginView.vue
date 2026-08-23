<script setup lang="ts">
import { KeyRound, LogIn } from 'lucide-vue-next'
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '@/api'
import { login } from '@/auth'

const route = useRoute()
const router = useRouter()
const email = ref('')
const password = ref('')
const pending = ref(false)
const error = ref('')

async function submit() {
  pending.value = true
  error.value = ''
  try {
    await login(email.value, password.value)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/overview'
    await router.push(redirect)
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <section class="auth-panel">
      <div class="auth-icon"><KeyRound :size="22" /></div>
      <div>
        <p class="eyebrow">SECURE CONSOLE</p>
        <h1>控制台登录</h1>
      </div>
      <form class="stack-form" @submit.prevent="submit">
        <label>
          <span>邮箱</span>
          <input v-model="email" type="email" autocomplete="username" required />
        </label>
        <label>
          <span>密码</span>
          <input
            v-model="password"
            type="password"
            autocomplete="current-password"
            minlength="12"
            required
          />
        </label>
        <div v-if="error" class="error-banner">{{ error }}</div>
        <button class="command-button full-width" type="submit" :disabled="pending">
          <LogIn :size="17" />
          {{ pending ? '验证中' : '登录' }}
        </button>
      </form>
    </section>
  </div>
</template>
