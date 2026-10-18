import { access, lstat, mkdir, mkdtemp, readFile, realpath, rename, rm } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import path from 'node:path'

export async function publishWebBuild(dist, build) {
  const root = path.resolve(dist)
  await mkdir(root, { recursive: true })
  if ((await realpath(root)) !== root) throw new Error('Web build directory must not be a symlink')
  const output = path.join(root, 'build')
  try {
    if ((await lstat(output)).isSymbolicLink()) throw new Error('Web output must not be a symlink')
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  const stage = await mkdtemp(path.join(root, '.build-stage-'))
  const backup = path.join(root, `.build-backup-${randomUUID()}`)
  const cleanup = async (target) => {
    if (path.dirname(target) !== root || !/^\.build-(stage|backup)-/.test(path.basename(target))) {
      throw new Error('Refusing to remove an unmanaged web build directory')
    }
    await rm(target, { recursive: true, force: true })
  }
  let backedUp = false
  try {
    const code = await build(stage)
    if (code !== 0) return code
    const html = await readFile(path.join(stage, 'index.html'), 'utf8')
    const references = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)]
    if (!references.some((item) => /\.js(?:\?|$)/.test(item[1]))) throw new Error('Web build has no entry script')
    for (const [, reference] of references) {
      if (/^(https?:|data:|#|\/\/)/i.test(reference)) continue
      const target = path.resolve(stage, reference.split(/[?#]/)[0].replace(/^\//, ''))
      if (!target.startsWith(stage + path.sep)) throw new Error('Web asset is outside the staging directory')
      await access(target)
    }
    try {
      await rename(output, backup)
      backedUp = true
    } catch (error) {
      if (error.code !== 'ENOENT') throw error
    }
    try {
      await rename(stage, output)
    } catch (error) {
      if (backedUp) await rename(backup, output)
      backedUp = false
      throw error
    }
    if (backedUp) {
      await cleanup(backup)
      backedUp = false
    }
    return 0
  } finally {
    await cleanup(stage)
    // A backup is deliberately kept if publication or rollback could not finish.
  }
}
