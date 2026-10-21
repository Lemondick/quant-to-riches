import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { visualizer } from 'rollup-plugin-visualizer'

// vite.config.ts 不在 @types/node 覆盖范围，这里只声明用到的最小形状，避免引入新依赖。
declare const process: { env: Record<string, string | undefined> }

export default defineConfig({
  plugins: [
    vue(),
    // batch4-B#9：BUNDLE_ANALYZE=1 时输出 bundle 体积分析报告（stats.html + 明细），默认关闭不影响正常构建。
    ...(process.env.BUNDLE_ANALYZE
      ? [visualizer({ filename: 'docs/bundle-stats.html', template: 'treemap', gzipSize: true, brotliSize: true }),
         visualizer({ filename: 'docs/bundle-stats.json', template: 'raw-data', gzipSize: true, brotliSize: true })]
      : []),
  ],
  server: { host: '127.0.0.1', port: 57450, strictPort: true },
  preview: { host: '127.0.0.1', port: 57450, strictPort: true },
  build: { target: 'es2022' },
})
