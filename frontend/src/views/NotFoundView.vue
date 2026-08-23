<script setup lang="ts">
import { ArrowLeft } from 'lucide-vue-next'
</script>

<template>
  <div class="not-found">
    <strong>404</strong>
    <h1>页面不存在</h1>
    <RouterLink class="command-button" to="/"><ArrowLeft :size="17" />返回搜索</RouterLink>
  </div>
</template>
