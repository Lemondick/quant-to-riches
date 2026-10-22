#!/usr/bin/env node
/**
 * 生成后端路由快照 web/frontend/src/api/route-snapshot.json，供「高级工具 / API 控制台」页面使用。
 *
 * 数据源优先级：
 *  1. web/backend-dotnet/artifacts/openapi.json（A 的 E13 产物；构建期导出的 OpenAPI 契约）
 *  2. 回退：扫描 web/backend-dotnet/src/QuantAssistant.Api/Controllers/*.cs 的
 *     [Route]/[Http*] 特性与 record 请求体，生成等价快照（来源标注为 controller-scan）
 *
 * 额外标注每条路由是否在前端源码中有调用（启发式：去掉 /api/v1 前缀后的静态路径
 * 前缀是否出现在 web/frontend/src 的字符串字面量中），用于在控制台中标识"无入口路由"。
 *
 * 用法：node scripts/generate-route-snapshot.mjs
 */
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const artifactsPath = join(root, '..', 'backend-dotnet', 'artifacts', 'openapi.json')
const controllersDir = join(root, '..', 'backend-dotnet', 'src', 'QuantAssistant.Api', 'Controllers')
const srcDir = join(root, 'src')
const outPath = join(srcDir, 'api', 'route-snapshot.json')

function readFrontendSources() {
  const chunks = []
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name)
      const stat = statSync(path)
      if (stat.isDirectory()) {
        walk(path)
        continue
      }
      if (/\.(ts|vue|js|mjs)$/.test(name)) chunks.push(readFileSync(path, 'utf8'))
    }
  }
  walk(srcDir)
  return chunks.join('\n')
}

function markFrontendUsage(routes, frontendSource) {
  for (const route of routes) {
    const staticPrefix = route.path
      .replace(/^\/api\/v1/, '')
      .split('{')[0]
      .replace(/\/$/, '')
    route.hasFrontendEntry =
      staticPrefix.length > 1 &&
      (frontendSource.includes(`'${staticPrefix}`) ||
        frontendSource.includes(`"${staticPrefix}`) ||
        frontendSource.includes(`\`${staticPrefix}`))
  }
  return routes
}

function schemaFields(schema, components) {
  if (!schema) return []
  let target = schema
  if (schema.$ref) {
    const name = schema.$ref.split('/').pop()
    target = components?.[name] ?? {}
  }
  const props = target.properties ?? {}
  const required = new Set(target.required ?? [])
  return Object.entries(props).map(([name, def]) => ({
    name,
    type: def.$ref ? def.$ref.split('/').pop() : (def.type ?? 'string') + (def.format ? `:${def.format}` : ''),
    required: required.has(name),
  }))
}

function fromOpenApi(doc) {
  const components = doc.components?.schemas ?? {}
  const routes = []
  for (const [path, item] of Object.entries(doc.paths ?? {})) {
    for (const [method, op] of Object.entries(item)) {
      if (!['get', 'post', 'put', 'delete', 'patch'].includes(method)) continue
      const params = (op.parameters ?? []).map((p) => ({
        name: p.name,
        kind: p.in === 'path' ? 'path' : 'query',
        type: p.schema?.type ?? 'string',
        required: !!p.required,
      }))
      const bodySchema = op.requestBody?.content?.['application/json']?.schema
      const bodyFields = schemaFields(bodySchema, components)
      if (bodySchema)
        params.push({ name: 'body', kind: 'body', type: 'object', required: !!op.requestBody?.required, bodyFields })
      routes.push({
        method: method.toUpperCase(),
        path,
        controller: (op.tags ?? ['misc'])[0],
        action: op.operationId ?? '',
        summary: op.summary ?? '',
        params,
        hasFrontendEntry: false,
      })
    }
  }
  return { source: 'openapi.json', routes }
}

function csTypeName(t) {
  return t.replace('?', '')
}

function fromControllerScan() {
  const routes = []
  for (const file of readdirSync(controllersDir).filter((f) => f.endsWith('.cs'))) {
    const src = readFileSync(join(controllersDir, file), 'utf8')
    const base = (src.match(/Route\("([^"]+)"\)/)?.[1] ?? '').replace(/\/$/, '')
    const records = {}
    for (const rm of src.matchAll(/public sealed record (\w+)\(([^)]*)\)/g)) {
      const fields = []
      for (const part of rm[2].split(',')) {
        const pm = part.trim().match(/(\S+)\s+(\w+)\s*(=.*)?$/)
        if (pm) fields.push({ name: pm[2], type: csTypeName(pm[1]), required: !pm[1].includes('?') && !pm[3] })
      }
      records[rm[1]] = fields
    }
    const methodRe =
      /\[(HttpGet|HttpPost|HttpPut|HttpDelete|HttpPatch)(?:\("([^"]*)"\))?[^\]]*\]\s*\n?\s*public [^\n]*?\b(\w+)\s*\(([^)]*)\)/g
    for (const mm of src.matchAll(methodRe)) {
      const verb = mm[1].replace('Http', '').toUpperCase()
      const sub = mm[2] ?? ''
      let path = (base + '/' + sub).replace(/\/+/g, '/')
      path = '/' + path.replace(/^\/+/, '').replace(/^~\//, '').replace(/\/+$/, '')
      if (path === '') path = '/'
      const params = []
      for (const part of mm[4].split(',')) {
        const p = part.trim()
        if (!p) continue
        const attr = p.match(/^\[(From\w+)(?:\(Name\s*=\s*"([^"]+)"\))?\]\s*(\S+)\s+(\w+)(?:\s*=.*)?$/)
        const plain = p.match(/^(\S+)\s+(\w+)(?:\s*=.*)?$/)
        if (attr) {
          const [, from, alias, type, name] = attr
          const param = {
            name: alias ?? name,
            kind: from === 'FromBody' ? 'body' : from === 'FromRoute' ? 'path' : 'query',
            type: csTypeName(type),
            required: !type.includes('?'),
          }
          if (from === 'FromBody' && records[csTypeName(type)]) param.bodyFields = records[csTypeName(type)]
          // 路径模板里的占位符视为 path 参数
          if (path.includes(`{${param.name}}`) || path.toLowerCase().includes(`{${param.name.toLowerCase()}}`))
            param.kind = 'path'
          params.push(param)
        } else if (plain) {
          const [, type, name] = plain
          if (/^(CancellationToken|ClaimsPrincipal)$/.test(type)) continue
          params.push({
            name,
            kind: path.includes(`{${name}}`) ? 'path' : 'query',
            type: csTypeName(type),
            required: !type.includes('?'),
          })
        }
      }
      routes.push({
        method: verb,
        path,
        controller: file.replace('Controller.cs', ''),
        action: mm[3],
        summary: '',
        params,
        hasFrontendEntry: false,
      })
    }
  }
  routes.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method))
  return { source: 'controller-scan', routes }
}

const frontendSource = readFrontendSources()
let snapshot
if (existsSync(artifactsPath)) {
  const doc = JSON.parse(readFileSync(artifactsPath, 'utf8'))
  snapshot = fromOpenApi(doc)
  console.log(`数据源: ${artifactsPath}（OpenAPI ${doc.openapi ?? 'unknown'}）`)
} else {
  snapshot = fromControllerScan()
  console.log(`数据源: 控制器扫描（${artifactsPath} 不存在，已回退）`)
}
snapshot.routes = markFrontendUsage(snapshot.routes, frontendSource)
snapshot.generated_at = new Date().toISOString()
snapshot.route_count = snapshot.routes.length
snapshot.unexposed_count = snapshot.routes.filter((r) => !r.hasFrontendEntry).length
writeFileSync(outPath, JSON.stringify(snapshot, null, 2) + '\n')
console.log(`路由 ${snapshot.route_count} 条，其中无前端入口约 ${snapshot.unexposed_count} 条 → ${outPath}`)
