import { access } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { preview } from 'vite'

const root = fileURLToPath(new URL('../', import.meta.url))
const output = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'), 'build')
try {
  await access(path.join(output, 'index.html'))
  const server = await preview({ root, build: { outDir: output } })
  server.printUrls()
  console.log('Supplied Vue UI preview. Business data is local demo data; no API fixtures are injected.')
} catch (error) {
  console.error(`Cannot preview the Vue frontend: ${error.message}. Run npm run build first.`)
  process.exitCode = 1
}
