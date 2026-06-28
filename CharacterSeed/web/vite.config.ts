import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vite 配置：Vue 3 + TS + 开发代理到 FastAPI (8000)
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    proxy: {
      // 开发时把 /api 代理到 FastAPI 后端
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        // SSE 关键：自接管响应，让 proxyRes.pipe(res) 真正做到逐 chunk 透传
        selfHandleResponse: true,
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, req, res) => {
            // 透传 SSE 必需的响应头
            res.setHeader('cache-control', 'no-cache')
            res.setHeader('x-accel-buffering', 'no')
            res.setHeader('access-control-allow-origin', req.headers.origin || '*')
            // 直接 pipe 后端响应流到浏览器，不缓冲
            proxyRes.pipe(res)
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
