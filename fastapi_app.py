from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from ipad_processor import IpadKey, load_ipad_base
from iphone_processor_all import DeviceKey, load_all_iphone_base, process_iphone_all_from_text
from mixed_processor import process_mixed_retail_from_text
from watch_processor import WatchKey, load_watch_base


app = FastAPI(title="Apple prices processor (iPhone + Watch + iPad)")

# Loaded once at startup; safe because it's immutable for requests.
BASE_ORDER: list[DeviceKey] = []
BASE: dict[DeviceKey, dict] = {}
WATCH_ORDER: list[WatchKey] = []
WATCH_BASE: dict[WatchKey, dict] = {}
IPAD_ORDER: list[IpadKey] = []
IPAD_BASE: dict[IpadKey, dict] = {}


@app.on_event("startup")
def _startup() -> None:
    global BASE_ORDER, BASE, WATCH_ORDER, WATCH_BASE, IPAD_ORDER, IPAD_BASE
    BASE_ORDER, BASE = load_all_iphone_base(
        base_13_16_path=Path(__file__).parent / "data" / "apple_iphone_13_16_base.json",
        base_air_path=Path(__file__).parent / "data" / "apple_iphone_air_base.json",
        base_17_path=Path(__file__).parent / "data" / "apple_iphone_17_base.json",
    )
    WATCH_ORDER, WATCH_BASE = load_watch_base(Path(__file__).parent / "data" / "apple_watch_base.json")
    IPAD_ORDER, IPAD_BASE = load_ipad_base(Path(__file__).parent / "data" / "apple_ipad_base.json")


class IphoneAllRequest(BaseModel):
    raw: str = Field(..., description="Сырой текст оптового прайса (по одной позиции на строку либо CSV).")
    input_format: Literal["auto", "text", "csv"] = "auto"
    usd_to_byn: Decimal
    markup_usd: Decimal = Field(..., description="Наценка в долларах USD (прибавляется к оптовой цене).")
    missing_price_text: str = "по запросу"
    delimiter_out: str = ";"
    include_cn_us_13_16: bool = False


class MixedRetailRequest(BaseModel):
    """Смешанный опт: iPhone, Watch, iPad — своя наценка USD на категорию."""
    raw: str
    input_format: Literal["auto", "text", "csv"] = "auto"
    usd_to_byn: Decimal
    markup_usd_iphone: Decimal
    markup_usd_watch: Decimal
    markup_usd_ipad: Decimal
    missing_price_text: str = "по запросу"
    delimiter_out: str = ";"
    include_cn_us_13_16: bool = False


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def index_page() -> FileResponse:
    """Минимальный фронтенд: вставка оптового прайса и запуск обработки."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/process/iphone-all", response_class=Response)
def process_iphone_all(req: IphoneAllRequest) -> Response:
    csv_text = process_iphone_all_from_text(
        req.raw,
        input_format=req.input_format,
        base_order=BASE_ORDER,
        base=BASE,
        usd_to_byn=req.usd_to_byn,
        markup_usd=req.markup_usd,
        missing_price_text=req.missing_price_text,
        delimiter_out=req.delimiter_out,
        include_cn_us_13_16=req.include_cn_us_13_16,
    )

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="iphone_all.csv"'},
    )


@app.post("/process/mixed", response_class=Response)
def process_mixed(req: MixedRetailRequest) -> Response:
    csv_text = process_mixed_retail_from_text(
        req.raw,
        input_format=req.input_format,
        base_iphone_order=BASE_ORDER,
        base_iphone=BASE,
        watch_order=WATCH_ORDER,
        watch_map=WATCH_BASE,
        ipad_order=IPAD_ORDER,
        ipad_map=IPAD_BASE,
        usd_to_byn=req.usd_to_byn,
        markup_usd_iphone=req.markup_usd_iphone,
        markup_usd_watch=req.markup_usd_watch,
        markup_usd_ipad=req.markup_usd_ipad,
        missing_price_text=req.missing_price_text,
        delimiter_out=req.delimiter_out,
        include_cn_us_13_16=req.include_cn_us_13_16,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="retail_mixed.csv"'},
    )

