import { defineConfig } from 'vite'
import type { Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

// vite.config.ts 不在 @types/node 覆盖范围，这里只声明用到的最小形状，避免引入新依赖。
declare const process: { env: Record<string, string | undefined> }

// batch5-C#5：手写精简版 service worker（不引入 vite-plugin-pwa，二选一已选定手写）。
// 构建时把 src/pwa/sw.template.js 的 __GUANLAN_PRECACHE_ASSETS__ 替换为本次构建
// 产出的 js/css 资源列表（hash 文件名即版本），以 sw.js 写入构建产物。
function guanlanPwaSw(template: string): Plugin {
  return {
    name: 'guanlan-pwa-sw',
    apply: 'build',
    generateBundle(_options, bundle) {
      const assets = ['/']
      for (const fileName of Object.keys(bundle)) {
        if (/\.([cm]?js|css)$/.test(fileName)) assets.push('/' + fileName)
      }
      const source = template.replace(/__GUANLAN_PRECACHE_ASSETS__/g, JSON.stringify(assets))
      this.emitFile({ type: 'asset', fileName: 'sw.js', source })
    },
  }
}

export default defineConfig(async () => {
  // @ts-ignore node 类型未安装；运行时存在 readFileSync。
  const { readFileSync } = await import('node:fs')
  const swTemplate: string = readFileSync(new URL('./src/pwa/sw.template.js', import.meta.url), 'utf8')
  // batch4-B#9：BUNDLE_ANALYZE=1 时输出 bundle 体积分析报告（stats.html + 明细），默认关闭不影响正常构建。
  // visualizer 改为懒加载：未安装 rollup-plugin-visualizer 时普通构建不受影响。
  const plugins: Plugin[] = [vue(), guanlanPwaSw(swTemplate)]
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
