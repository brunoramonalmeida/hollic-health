import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [
      react(),
      VitePWA({
        registerType: 'autoUpdate',
        includeAssets: [
          'apple-touch-icon.png',
          'apple-touch-icon-precomposed.png',
          'assets/icons/icon.png',
          'assets/icons/icon-192.png',
          'assets/icons/icon-512.png',
          'assets/icons/icon-maskable-512.png',
          'assets/icons/apple-touch-icon.png',
          'assets/images/logo.svg',
          'assets/images/logo_splash.svg',
        ],
        manifest: {
          name: 'Santé Saúde',
          short_name: 'Santé',
          description:
            'Assine nosso plano e cuide da sua saúde e da de seus familiares.',
          theme_color: '#E08D2E',
          background_color: '#FFFFFF',
          display: 'standalone',
          display_override: ['standalone', 'fullscreen', 'minimal-ui'],
          orientation: 'portrait',
          start_url: './',
          scope: './',
          lang: 'pt-BR',
          icons: [
            {
              src: '/assets/icons/icon-192.png',
              sizes: '192x192',
              type: 'image/png',
              purpose: 'any',
            },
            {
              src: '/assets/icons/icon-512.png',
              sizes: '512x512',
              type: 'image/png',
              purpose: 'any',
            },
            {
              src: '/assets/icons/icon-maskable-512.png',
              sizes: '512x512',
              type: 'image/png',
              purpose: 'maskable',
            },
            {
              src: '/apple-touch-icon.png',
              sizes: '180x180',
              type: 'image/png',
              purpose: 'any',
            },
          ],
        },
        workbox: {
          globPatterns: ['**/*.{js,css,html,ico,png,svg,jpg,ttf,woff2}'],
          navigateFallbackDenylist: [/^\/api/],
        },
        devOptions: {
          enabled: false,
        },
      }),
    ],
    server: {
      host: true,
      port: 5174,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          secure: false,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              console.error('[vite proxy /api]', err.message)
            })
          },
        },
      },
    },
    preview: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          secure: false,
        },
      },
    },
  }
})
