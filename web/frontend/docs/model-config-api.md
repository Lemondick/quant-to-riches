# 模型配置接口

设置入口：`/settings?section=models`。本次只增加前端配置和后端适配层，不实现模型推理、报告生成或 Jev 服务。

在 `.env.local` 中配置 `VITE_API_BASE_URL=/api`，重启开发服务或重新构建后启用后端请求。后端部署在其他域时可填写完整服务根地址，并由后端配置跨域及身份验证。前端环境变量会公开到构建产物，请勿填写 Key。

未设置服务根地址时，读取和保存操作使用本浏览器的配置，默认值按钮填入参考图中的示例模型；模型列表读取按钮不可用。接入后端后，这些操作请求下列接口，失败时显示错误，不会假装已保存到服务端。

## 配置数据

```json
{
  "baseUrl": "https://your-provider.example/v1",
  "models": {
    "news": "gemini-3.1-pro-low",
    "strategy": "gpt-6.1-sol",
    "risk": "gpt-6-astra",
    "jev": "jev-latest"
  },
  "connections": { "compatible": false, "jev": false }
}
```

模型 ID 为用户参考配置中的示例，不保证任何服务实际提供这些模型。四个模型字段均为必填字符串，允许用户直接填写 ID；列表中不存在的 ID 会显示权限核对提示。

## 路由约定

所有路径均相对于 `VITE_API_BASE_URL`。

| 方法 | 路径 | 请求与响应 |
| --- | --- | --- |
| GET | `/model-config` | 返回上面的配置对象；`connections` 表示后端是否保存过对应连接，不代表本次验证通过 |
| PUT | `/model-config` | 请求为 `{baseUrl, models}`；保存角色配置，成功返回 204 或 JSON |
| GET | `/model-config/defaults` | 返回同结构的服务端默认配置，前端只填充其中的 `models`，等待用户保存 |
| POST | `/model-config/connection` | 请求 `{baseUrl, apiKey?}`；后端读取兼容服务模型并保存连接，成功返回 `{"models":["model-a","model-b"]}` |
| POST | `/model-config/jev-connection` | 请求 `{apiKey?}`；Jev 地址与协议由后端管理，后端读取模型并保存连接，响应同上 |

后端已保存连接时，留空 Key 可沿用现有凭据。读取模型列表成功后，前端清除该组暂存 Key。列表不能包含空模型 ID；空列表会提示检查服务权限。

后端应使用正常 HTTP 状态码报告失败，例如 401、403、404、429、5xx。前端请求超时为 15 秒；离开模型设置时取消未完成的请求。请求只发往配置的后端，不在浏览器中直连兼容模型或 Jev 服务。

## 凭据与本地保存

- 非敏感配置只保存 `baseUrl` 和四个模型 ID，位于 localStorage 的 `guanlan.model-config.v1`，可进入工作台本地备份。
- 未提交的 Key 暂存于当前标签页 sessionStorage 的 `guanlan.model-keys.v1`，不进入 localStorage、偏好导出或本地备份；关闭标签页后清除。浏览器限制 sessionStorage 时只在内存中保留。
- 连接成功后 Key 应由后端管理，配置和默认值接口不得返回明文 Key。前端不展示服务端原始错误内容，避免意外回显凭据。
- 后端应按当前登录用户隔离连接和配置；读取列表只校验模型可见性，不触发报告生成或付费推理。
