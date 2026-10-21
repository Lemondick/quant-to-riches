/**
 * batch4 流 B（#6）：冒烟测试用的最小 mock 后端。
 * - GET /api/v1/auth/session -> 401（未登录），驱动各 Live 页渲染登录表单；
 * - POST /api/v1/auth/login -> 401（账号或密码无效）；
 * - 其余 /api/v1/* -> 503 { error_code: 'mock_unavailable' }，
 *   页面应显示“数据暂不可用”类提示而非白屏/异常。
 * 用法：node tests/e2e/mock-api.mjs [port，默认 57451]
 */
import http from 'node:http'

const port = Number(process.argv[2] ?? 57451)
const json = (res, status, body) => {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': '*',
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  })
  res.end(payload)
}

const server = http.createServer((req, res) => {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    })
    return res.end()
  }
  const url = new URL(req.url ?? '/', 'http://localhost')
  if (url.pathname === '/api/v1/auth/session' && req.method === 'GET')
    return json(res, 401, { error_code: 'unauthorized', message: '请先登录后端账户。' })
  if (url.pathname === '/api/v1/auth/login' && req.method === 'POST')
    return json(res, 401, { error_code: 'invalid_credentials', message: '账号或密码无效。' })
  if (url.pathname.startsWith('/api/v1/'))
    return json(res, 401, { error_code: 'unauthorized', message: '请先登录后端账户。' })
  return json(res, 404, { error_code: 'not_found' })
})

server.listen(port, '127.0.0.1', () => console.log(`mock api listening on http://127.0.0.1:${port}`))
