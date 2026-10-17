from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

from airpods_processor import (
    AirpodsKey,
    load_airpods_base,
    merge_airpods_from_texts,
    process_airpods_from_text,
)
from ipad_processor import (
    IpadKey,
    _format_ipad_line,
    load_ipad_base,
    merge_ipad_from_texts,
    process_ipad_from_text,
)
from macbook_processor import (
    MacbookKey,
    load_macbook_base,
    merge_macbook_from_texts,
    process_macbook_from_text,
)
from iphone_processor_all import (
    DeviceKey,
    load_all_iphone_base,
    merge_iphone_all_from_texts,
    process_iphone17_site_from_text,
    process_iphone_all_from_text,
)
from watch_processor import WatchKey, load_watch_base, merge_watch_from_texts, process_watch_from_text


app = FastAPI(title="Apple prices processor (iPhone + Watch + iPad + AirPods + MacBook)")

# Loaded once at startup; safe because it's immutable for requests.
BASE_ORDER: list[DeviceKey] = []
BASE: dict[DeviceKey, dict] = {}
WATCH_ORDER: list[WatchKey] = []
WATCH_BASE: dict[WatchKey, dict] = {}
IPAD_ORDER: list[IpadKey] = []
IPAD_BASE: dict[IpadKey, dict] = {}
AIRPODS_ORDER: list[AirpodsKey] = []
AIRPODS_BASE: dict[AirpodsKey, dict] = {}
MACBOOK_ORDER: list[MacbookKey] = []
MACBOOK_BASE: dict[MacbookKey, dict] = {}


@app.on_event("startup")
def _startup() -> None:
    global BASE_ORDER, BASE, WATCH_ORDER, WATCH_BASE, IPAD_ORDER, IPAD_BASE
    global AIRPODS_ORDER, AIRPODS_BASE, MACBOOK_ORDER, MACBOOK_BASE
    BASE_ORDER, BASE = load_all_iphone_base(
        base_13_16_path=Path(__file__).parent / "data" / "apple_iphone_13_16_base.json",
        base_air_path=Path(__file__).parent / "data" / "apple_iphone_air_base.json",
        base_17_path=Path(__file__).parent / "data" / "apple_iphone_17_base.json",
        base_18_path=Path(__file__).parent / "data" / "apple_iphone_18_base.json",
    )
    WATCH_ORDER, WATCH_BASE = load_watch_base(Path(__file__).parent / "data" / "apple_watch_base.json")
    IPAD_ORDER, IPAD_BASE = load_ipad_base(Path(__file__).parent / "data" / "apple_ipad_base.json")
    AIRPODS_ORDER, AIRPODS_BASE = load_airpods_base(
        Path(__file__).parent / "data" / "apple_airpods_base.json"
    )
    MACBOOK_ORDER, MACBOOK_BASE = load_macbook_base(
        Path(__file__).parent / "data" / "apple_macbook_base.json"
    )


class IphoneAllRequest(BaseModel):
    raw: str = Field(..., description="Сырой текст оптового прайса (по одной позиции на строку либо CSV).")
    input_format: Literal["auto", "text", "csv"] = "auto"
    usd_to_byn: Decimal
    markup_usd: Decimal = Field(..., description="Наценка в долларах USD (прибавляется к оптовой цене).")
    missing_price_text: str = "по запросу"
    delimiter_out: str = ";"
    include_cn_us_13_16: bool = False


class Iphone17SiteRequest(BaseModel):
    """Розничный прайс iPhone 17 и iPhone Air для сайта: BYN как во входе (без курса и наценки)."""

    raw: str = Field(
        ...,
        description="Розница по 17e / 17 / Pro / Pro Max и iPhone Air — цены в BYN (как из вкладки iPhone).",
    )
    input_format: Literal["auto", "text", "csv"] = "auto"
    delimiter_out: str = ";"


class _CategoryTabBase(BaseModel):
    """Один прайс или объединение двух (min BYN по ключу) с разными наценками."""

    raw: str = ""
    raw_b: str = ""
    merge: bool = False
    input_format: Literal["auto", "text", "csv"] = "auto"
    usd_to_byn: Decimal
    markup_usd: Decimal = Field(..., description="Наценка USD для первого прайса.")
    markup_usd_b: Decimal = Decimal("0")
    missing_price_text: str = "по запросу"
    delimiter_out: str = ";"

    @model_validator(mode="after")
    def _validate_merge_and_raw(self) -> _CategoryTabBase:
        if not self.raw.strip():
            raise ValueError("Вставьте оптовый прайс (raw).")
        if self.merge:
            if not self.raw_b.strip():
                raise ValueError("Для объединения укажите второй прайс (raw_b).")
            if self.markup_usd_b < 0:
                raise ValueError("Наценка для второго прайса (markup_usd_b) не может быть отрицательной.")
        return self


class IphoneTabRequest(_CategoryTabBase):
    include_cn_us_13_16: bool = False


class WatchTabRequest(_CategoryTabBase):
    pass


class IpadTabRequest(_CategoryTabBase):
    pass


class AirpodsTabRequest(_CategoryTabBase):
    pass


class MacbookTabRequest(_CategoryTabBase):
    pass


@app.get("/health")
def health() -> dict[str, str]:
    """Проверка живости; `ipad_retail_label_sample` — пример строки розницы iPad (без года в тексте)."""
    out: dict[str, str] = {"status": "ok"}
    for k in IPAD_ORDER:
        if k.kind == "base11":
            out["ipad_retail_label_sample"] = _format_ipad_line(
                k, price_byn=None, missing_price_text="…"
            )
            break
    return out


@app.get("/")
def index_page() -> FileResponse:
    """Минимальный фронтенд: вставка оптового прайса и запуск обработки."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/process/iphone-17-site", response_class=Response)
def process_iphone_17_site(req: Iphone17SiteRequest) -> Response:
    csv_text = process_iphone17_site_from_text(
        req.raw,
        input_format=req.input_format,
        base=BASE,
        delimiter_out=req.delimiter_out,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="iphone_17_site.csv"'},
    )


@app.post("/process/iphone-all")
def process_iphone_all(req: IphoneAllRequest) -> JSONResponse:
    csv_text, rejected = process_iphone_all_from_text(
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
    return JSONResponse(content={"csv": csv_text, "rejected": rejected})


def _iphone_tab_result(req: IphoneTabRequest) -> tuple[str, list[str]]:
    if req.merge:
        return merge_iphone_all_from_texts(
            req.raw,
            req.raw_b,
            input_format=req.input_format,
            base_order=BASE_ORDER,
            base=BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd_a=req.markup_usd,
            markup_usd_b=req.markup_usd_b,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
            include_cn_us_13_16=req.include_cn_us_13_16,
        )
    return process_iphone_all_from_text(
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


@app.post("/process/iphone")
def process_iphone_tab(req: IphoneTabRequest) -> JSONResponse:
    csv_text, rejected = _iphone_tab_result(req)
    return JSONResponse(content={"csv": csv_text, "rejected": rejected})


@app.post("/process/watch", response_class=Response)
def process_watch_tab(req: WatchTabRequest) -> Response:
    if req.merge:
        csv_text = merge_watch_from_texts(
            req.raw,
            req.raw_b,
            input_format=req.input_format,
            base_order=WATCH_ORDER,
            base=WATCH_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd_a=req.markup_usd,
            markup_usd_b=req.markup_usd_b,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    else:
        csv_text = process_watch_from_text(
            req.raw,
            input_format=req.input_format,
            base_order=WATCH_ORDER,
            base=WATCH_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd=req.markup_usd,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    name = "watch_merged.csv" if req.merge else "watch_retail.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/process/ipad", response_class=Response)
def process_ipad_tab(req: IpadTabRequest) -> Response:
    if req.merge:
        csv_text = merge_ipad_from_texts(
            req.raw,
            req.raw_b,
            input_format=req.input_format,
            base_order=IPAD_ORDER,
            base=IPAD_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd_a=req.markup_usd,
            markup_usd_b=req.markup_usd_b,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    else:
        csv_text = process_ipad_from_text(
            req.raw,
            input_format=req.input_format,
            base_order=IPAD_ORDER,
            base=IPAD_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd=req.markup_usd,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    name = "ipad_merged.csv" if req.merge else "ipad_retail.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/process/airpods", response_class=Response)
def process_airpods_tab(req: AirpodsTabRequest) -> Response:
    if req.merge:
        csv_text = merge_airpods_from_texts(
            req.raw,
            req.raw_b,
            input_format=req.input_format,
            base_order=AIRPODS_ORDER,
            base=AIRPODS_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd_a=req.markup_usd,
            markup_usd_b=req.markup_usd_b,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    else:
        csv_text = process_airpods_from_text(
            req.raw,
            input_format=req.input_format,
            base_order=AIRPODS_ORDER,
            base=AIRPODS_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd=req.markup_usd,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    name = "airpods_merged.csv" if req.merge else "airpods_retail.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/process/macbook", response_class=Response)
def process_macbook_tab(req: MacbookTabRequest) -> Response:
    if req.merge:
        csv_text = merge_macbook_from_texts(
            req.raw,
            req.raw_b,
            input_format=req.input_format,
            base_order=MACBOOK_ORDER,
            base=MACBOOK_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd_a=req.markup_usd,
            markup_usd_b=req.markup_usd_b,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    else:
        csv_text = process_macbook_from_text(
            req.raw,
            input_format=req.input_format,
            base_order=MACBOOK_ORDER,
            base=MACBOOK_BASE,
            usd_to_byn=req.usd_to_byn,
            markup_usd=req.markup_usd,
            missing_price_text=req.missing_price_text,
            delimiter_out=req.delimiter_out,
        )
    name = "macbook_merged.csv" if req.merge else "macbook_retail.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
