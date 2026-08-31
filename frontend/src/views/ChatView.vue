<script setup lang="ts">
import { Settings2 } from 'lucide-vue-next'
import { nextTick, ref } from 'vue'

import { useAuth } from '@/auth'
import ChatWorkspace from '@/components/ChatWorkspace.vue'
import ModelSettingsDialog from '@/components/ModelSettingsDialog.vue'

const showModelSettings = ref(false)
const modelSettingsTrigger = ref<HTMLButtonElement | null>(null)
const { isAdmin } = useAuth()

function openModelSettings() {
  showModelSettings.value = true
}

async function closeModelSettings() {
  showModelSettings.value = false
  await nextTick()
  modelSettingsTrigger.value?.focus()
}
</script>

<template>
  <div class="page-container chat-page">
    <section class="page-heading chat-page-heading" :inert="showModelSettings || undefined">
      <div>
        <p class="eyebrow">AI ASSISTANT</p>
        <h1>代码问答</h1>
        <p class="page-heading-description">账号级历史对话与长期记忆，结合代码、文档和Wiki生成可追溯回答。</p>
      </div>
      <button
        v-if="isAdmin"
        ref="modelSettingsTrigger"
        class="secondary-button"
        type="button"
        data-testid="open-model-settings"
        @click="openModelSettings"
      >
        <Settings2 :size="16" />模型配置
      </button>
    </section>

    <div :inert="showModelSettings || undefined" class="chat-workspace-host">
      <ChatWorkspace />
    </div>

    <ModelSettingsDialog
      v-if="showModelSettings"
      @close="closeModelSettings"
    />
  </div>
</template>
