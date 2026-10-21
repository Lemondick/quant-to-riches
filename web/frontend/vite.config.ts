import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// vite.config.ts 不在 @types/node 覆盖范围，这里只声明用到的最小形状，避免引入新依赖。
declare const process: { env: Record<string, string | undefined> }

export default defineConfig(async () => {
  // batch4-B#9：BUNDLE_ANALYZE=1 时输出 bundle 体积分析报告（stats.html + 明细），默认关闭不影响正常构建。
  // visualizer 改为懒加载：未安装 rollup-plugin-visualizer 时普通构建不受影响。
  const plugins = [vue()]
  if (process.env.BUNDLE_ANALYZE) {
    // 可选依赖：未安装 rollup-plugin-visualizer 时跳过分析插件，不影响构建。
    // @ts-ignore 未安装时的类型解析失败；运行时走下面的 try/catch。
    const mod = await import('rollup-plugin-visualizer').catch(() => null)
    const visualizer = (mod as any)?.visualizer
    if (visualizer) {
      plugins.push(
        visualizer({ filename: 'docs/bundle-stats.html', template: 'treemap', gzipSize: true, brotliSize: true }),
        visualizer({ filename: 'docs/bundle-stats.json', template: 'raw-data', gzipSize: true, brotliSize: true }),
      )
    }
  }
  return {
    plugins,
    server: { host: '127.0.0.1', port: 57450, strictPort: true },
    preview: { host: '127.0.0.1', port: 57450, strictPort: true },
    build: { target: 'es2022' },
  }
})
