<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { Copy, X } from 'lucide-vue-next'
import { computed } from 'vue'

import { api, errorMessage } from '@/api'
import { shortCommit } from '@/format'
import type { FilePreview, SearchResult } from '@/types'

const props = defineProps<{ result: SearchResult }>()
const emit = defineEmits<{ close: [] }>()

const queryKey = computed(() => [
  'file',
  props.result.repo,
  props.result.commit,
  props.result.path,
  props.result.start_line,
  props.result.end_line,
])

const preview = useQuery({
  queryKey,
  queryFn: async ({ signal }) => {
    const result = props.result
    const start = Math.max(1, result.start_line - 20)
    const end = Math.min(start + 199, result.end_line + 40)
    const { data } = await api.get<FilePreview>(
      `/repositories/${result.repo}/file`,
      {
        params: { path: result.path, start_line: start, end_line: end, commit: result.commit || undefined },
        signal,
      },
    )
    if (result.commit && data.commit !== result.commit) {
      throw new Error('引用版本已更新，无法显示原版本源码，请重新检索。')
    }
    return data
  },
})

async function copyCode() {
  if (!preview.error.value && preview.data.value?.content) {
    await navigator.clipboard.writeText(preview.data.value.content)
  }
}

function closePreview() {
  emit('close')
}
</script>

<template>
  <div class="preview-backdrop" role="presentation" @click.self="closePreview">
    <section v-modal-dialog="closePreview" class="code-preview" role="dialog" aria-modal="true" aria-label="文件预览">
      <header class="preview-header">
        <div>
          <strong>{{ result.path }}</strong>
          <span>{{ shortCommit(result.commit || preview.data.value?.commit || '') }} · L{{ result.start_line }}–{{ result.end_line }}</span>
        </div>
        <div class="preview-actions">
          <button
            class="icon-button tooltip"
            type="button"
            data-tooltip="复制代码"
            aria-label="复制代码"
            :disabled="!preview.data.value || !!preview.error.value"
            @click="copyCode"
          >
            <Copy :size="17" />
          </button>
          <button
            class="icon-button tooltip"
            type="button"
            data-tooltip="关闭"
            aria-label="关闭"
            @click="closePreview"
          >
            <X :size="18" />
          </button>
        </div>
      </header>
      <div v-if="preview.isPending.value" class="loading-block">正在读取文件…</div>
      <div v-else-if="preview.error.value" class="error-banner" role="alert">{{ errorMessage(preview.error.value) }}</div>
      <pre v-else-if="preview.data.value" class="source-code"><code>{{ preview.data.value.content }}</code></pre>
    </section>
  </div>
</template>
