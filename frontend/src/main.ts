import { VueQueryPlugin } from '@tanstack/vue-query'
import { createApp } from 'vue'

import App from '@/App.vue'
import { router } from '@/router'
import '@fontsource/ibm-plex-mono/400.css'
import '@fontsource/ibm-plex-mono/500.css'
import '@fontsource/noto-serif-sc/600.css'
import '@fontsource/noto-serif-sc/700.css'
import '@/style.css'

createApp(App).use(router).use(VueQueryPlugin).mount('#app')
