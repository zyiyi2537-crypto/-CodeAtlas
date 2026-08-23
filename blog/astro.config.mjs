import sitemap from '@astrojs/sitemap'
import { defineConfig } from 'astro/config'

export default defineConfig({
  site: process.env.SITE_URL || 'http://codeatlas.example.com:8080',
  integrations: [sitemap()],
  markdown: {
    shikiConfig: { theme: 'github-dark' },
  },
  server: { host: '127.0.0.1', port: 4321 },
})
