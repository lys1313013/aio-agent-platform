import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  optimizeDeps: {
    include: ['antd', '@ant-design/icons'],
  },
  server: {
    port: 5273,
    proxy: {
      // SSE streaming endpoint — needs special handling
      '/api/chat/stream': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        // Prevent proxy from buffering SSE responses
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes) => {
            proxyRes.headers['cache-control'] = 'no-cache';
            proxyRes.headers['x-accel-buffering'] = 'no';
            // Remove any compression that would break streaming
            delete proxyRes.headers['content-encoding'];
            delete proxyRes.headers['content-length'];
          });
        },
      },
      // All other API endpoints
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
