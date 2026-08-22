<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

defineProps<{ title: string; description?: string }>()
</script>

<template>
  <div class="empty-state">
    <Inbox :size="24" aria-hidden="true" />
    <p>{{ title }}</p>
    <p v-if="description" class="empty-state-description">{{ description }}</p>
    <slot />
  </div>
</template>
