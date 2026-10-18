"""Photo preview and confirmation are separate operations."""

import base64
import binascii
from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from quant_assistant.providers.ocr import OcrError
from quant_assistant.services.photo_import import PhotoImportError


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_base64: str = Field(min_length=1, max_length=8 * 1024 * 1024)
    as_of_date: date | None = None
    external_ocr_consent: bool = False
    holdings_only: bool = False


class PhotoRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=24)
    name: str = Field(default="", max_length=200)
    quantity: str = Field(max_length=40)
    average_cost: str = Field(max_length=40)
    market_value: str = Field(max_length=40)


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=32, max_length=32)
    positions: list[PhotoRow] = Field(min_length=1, max_length=100)
    cash: str = Field(max_length=40)
    confirmed: bool = False


class ManualBody(BaseModel):
    as_of_date: date


class MergeBody(BaseModel):
    preview_ids: list[str] = Field(min_length=1, max_length=6)


class ReviewBody(ConfirmBody):
    mode: str = "initial"
    external_flow: str = Field(default="0", max_length=40)


class CommitBody(BaseModel):
    review_id: str = Field(min_length=32, max_length=32)
    confirmed: bool = False


class HoldingsReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(min_length=32, max_length=32)
    positions: list[PhotoRow] = Field(min_length=1, max_length=100)


def photo_router(queries):
    router = APIRouter()

    def run(action):
        try:
            return action()
        except (ValueError, PhotoImportError) as exc:
            return JSONResponse(status_code=400, content={"message": str(exc)})

    @router.get("/portfolio/photo-holdings")
    def photo_holdings():
        return run(lambda: queries._photo_upload.photo_holdings())

    @router.post("/imports/fund-photo/holdings-review")
    def holdings_review(body: HoldingsReviewBody):
        return run(
            lambda: queries._photo_upload.review_holdings(
                body.preview_id, [row.model_dump() for row in body.positions]
            )
        )

    @router.post("/imports/fund-photo/holdings-commit")
    def holdings_commit(body: CommitBody):
        return run(lambda: queries._photo_upload.commit_holdings(body.review_id, body.confirmed))

    @router.post("/imports/fund-photo/manual")
    def manual(body: ManualBody):
        return run(lambda: queries._photo_upload.manual(body.as_of_date))

    @router.post("/imports/fund-photo/merge")
    def merge(body: MergeBody):
        return run(lambda: queries._photo_upload.merge(body.preview_ids))

    @router.post("/imports/fund-photo/review")
    def review(body: ReviewBody):
        return run(
            lambda: queries._photo_upload.review(
                body.preview_id,
                [row.model_dump() for row in body.positions],
                body.cash,
                body.mode,
                body.external_flow,
            )
        )

    @router.post("/imports/fund-photo/commit")
    def commit(body: CommitBody):
        return run(lambda: queries._photo_upload.commit_review(body.review_id, body.confirmed))

    @router.post("/imports/fund-photo/preview")
    def preview(body: PreviewBody):
        if body.external_ocr_consent is not True:
            return JSONResponse(
                status_code=400, content={"message": "必须同意将所选图片发送至已配置的OCR服务"}
            )
        try:
            image = base64.b64decode(body.image_base64, validate=True)
            options = {"holdings_only": True} if body.holdings_only else {}
            return queries._photo_upload.preview(image, body.as_of_date, **options)
        except (ValueError, binascii.Error) as exc:
            return JSONResponse(status_code=400, content={"message": str(exc)})
        except PhotoImportError as exc:
            return JSONResponse(
                status_code=422,
                content={"message": str(exc)[:400] + "；请补充清晰的持仓截图后重试。"},
            )
        except OcrError:
            return JSONResponse(
                status_code=422,
                content={
                    "message": "无法识别或核验基金持仓，请检查OCR配置、截图清晰度、"
                    "基金代码及历史净值。账户未改动。"
                },
            )

    @router.post("/imports/fund-photo/confirm")
    def confirm(body: ConfirmBody):
        try:
            return queries._photo_upload.confirm(
                body.preview_id,
                [row.model_dump() for row in body.positions],
                body.cash,
                body.confirmed,
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"message": str(exc)})

    return router
