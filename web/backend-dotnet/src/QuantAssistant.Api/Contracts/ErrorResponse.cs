namespace QuantAssistant.Api.Contracts;

/// <summary>
/// 统一错误响应契约，与旧站点 ErrorResponse 一致：
/// {"error_code": "...", "message": "...", "request_id": "..."}
/// 字段名经全局 snake_case 命名策略输出为 error_code/message/request_id。
/// </summary>
public sealed record ErrorResponse(string ErrorCode, string Message, string RequestId);
