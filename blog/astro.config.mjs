import sitemap from '@astrojs/sitemap'
import { defineConfig } from 'astro/config'

export default defineConfig({
  site: process.env.SITE_URL || 'https://codeatlas.example.com',
  integrations: [sitemap()],
  markdown: {
    shikiConfig: { theme: 'github-dark' },
  },
  server: { host: '127.0.0.1', port: 4321 },
})
