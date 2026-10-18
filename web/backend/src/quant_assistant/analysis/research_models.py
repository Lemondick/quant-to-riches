"""Research-only contracts, versioning, and safe public failures."""

import hashlib
import json

UPSTREAM_COMMIT = "926de6e6308047e02c4d8110838fd2d2125dceb6"
ADAPTER_VERSION = "price-volume-review-v1"
PROMPT_VERSION = "bull-bear-review-v1"
ACTIVE = ("queued", "running", "cancel_requested")
ERRORS = {
    "research_disabled": "AI 复核尚未启用。",
    "worker_unavailable": "研究执行器未就绪。",
    "model_not_configured": "模型端点、名称或密钥文件未配置。",
    "preview_not_found": "未找到预览，请重新预览选股。",
    "preview_expired": "预览已过期，请重新预览选股。",
    "preview_changed": "行情或来源证据已变化，请重新预览选股。",
    "ineligible_evidence": "研究证据未通过校验，请查看预览中的缺失项。",
    "not_candidate": "该股票不属于本次预览候选。",
    "job_not_found": "未找到研究任务。",
    "job_active": "任务仍在执行，不能重复研究。",
    "quota_exceeded": "今日研究次数已用完，失败和重试也计入额度。",
    "model_budget_exceeded": "金额预算不足或调用费用上限未知，未调用模型。",
    "invalid_output": "模型输出未通过结构或引用校验。",
    "model_error": "模型服务调用失败。",
    "model_rate_limited": "模型服务限流，请稍后显式重试。",
    "timed_out": "研究超时，执行已终止。",
    "interrupted": "服务中断，本次研究未自动重跑。",
    "cancelled": "研究已取消。",
}


class ResearchError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(ERRORS[code])


def canonical(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
