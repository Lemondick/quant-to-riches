import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { publishWebBuild } from './web-build.mjs'

const root = fileURLToPath(new URL('../', import.meta.url))
const [mode, ...extra] = process.argv.slice(2)
if (!['dev', 'build'].includes(mode)) {
  console.error('Usage: node scripts/run-web.mjs <dev|build>')
  process.exit(1)
}

function run(cli, args) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [path.join(root, 'node_modules', cli), ...args], {
      cwd: root,
      stdio: 'inherit',
      env: process.env,
    })
    child.on('error', (error) => {
      console.error(error.message)
      resolve(1)
    })
    child.on('exit', (code) => resolve(code ?? 1))
  })
}

try {
  if (mode === 'dev') {
    process.exitCode = await run('vite/bin/vite.js', extra)
  } else {
    const checked = await run('vue-tsc/bin/vue-tsc.js', ['-b'])
    if (checked !== 0) process.exitCode = checked
    else {
      const output = path.resolve(process.env.QUANT_WEB_OUTPUT_DIR || path.join(root, '../../data/runtime/web'))
      process.exitCode = await publishWebBuild(output, (stage) =>
        run('vite/bin/vite.js', ['build', '--outDir', stage, '--emptyOutDir', ...extra]),
      )
    }
  }
} catch (error) {
  console.error(`Vue frontend was not published: ${error.message}`)
  process.exitCode = 1
}
