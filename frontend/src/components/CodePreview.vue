<script setup lang="ts">
import { useQuery } from '@tanstack/vue-query'
import { Copy, ExternalLink, X } from 'lucide-vue-next'
import { computed } from 'vue'

import { api } from '@/api'
import { shortCommit } from '@/format'
import type { FilePreview, SearchResult } from '@/types'

const props = defineProps<{ result: SearchResult }>()
const emit = defineEmits<{ close: [] }>()

const queryKey = computed(() => [
  'file',
  props.result.repo,
  props.result.path,
  props.result.start_line,
])

const preview = useQuery({
  queryKey,
  queryFn: async () => {
    const start = Math.max(1, props.result.start_line - 20)
    const end = Math.min(start + 199, props.result.end_line + 40)
    const { data } = await api.get<FilePreview>(
      `/repositories/${props.result.repo}/file`,
      { params: { path: props.result.path, start_line: start, end_line: end } },
    )
    return data
  },
})

async function copyCode() {
  if (preview.data.value?.content) await navigator.clipboard.writeText(preview.data.value.content)
}
</script>

<template>
  <div class="preview-backdrop" role="presentation" @click.self="emit('close')">
    <section class="code-preview" role="dialog" aria-modal="true" aria-label="文件预览">
      <header class="preview-header">
        <div>
          <strong>{{ result.path }}</strong>
          <span>{{ shortCommit(result.commit) }} · L{{ result.start_line }}–{{ result.end_line }}</span>
        </div>
        <div class="preview-actions">
          <button
            class="icon-button tooltip"
            type="button"
            data-tooltip="复制代码"
            aria-label="复制代码"
            @click="copyCode"
          >
            <Copy :size="17" />
          </button>
          <a
            class="icon-button tooltip"
            :href="`https://github.com/search?q=${encodeURIComponent(result.path)}`"
            target="_blank"
            rel="noreferrer"
            data-tooltip="在上游查找"
            aria-label="在上游查找"
          >
            <ExternalLink :size="17" />
          </a>
          <button
            class="icon-button tooltip"
            type="button"
            data-tooltip="关闭"
            aria-label="关闭"
            @click="emit('close')"
          >
            <X :size="18" />
          </button>
        </div>
      </header>
      <div v-if="preview.isPending.value" class="loading-block">正在读取文件…</div>
      <pre v-else-if="preview.data.value" class="source-code"><code>{{ preview.data.value.content }}</code></pre>
      <div v-else class="error-banner">文件读取失败</div>
    </section>
  </div>
</template>
