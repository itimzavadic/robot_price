from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from airpods_processor import (
    AirpodsKey,
    load_airpods_base,
    merge_airpods_from_texts,
    process_airpods_from_text,
)
from ipad_processor import IpadKey, load_ipad_base, merge_ipad_from_texts, process_ipad_from_text
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
from mixed_processor import process_mixed_retail_from_text
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


class MixedRetailRequest(BaseModel):
    """Смешанный опт: iPhone, Watch, iPad, AirPods — своя наценка USD на категорию."""
    raw: str
    input_format: Literal["auto", "text", "csv"] = "auto"
    usd_to_byn: Decimal
    markup_usd_iphone: Decimal
    markup_usd_watch: Decimal
    markup_usd_ipad: Decimal
    markup_usd_airpods: Decimal
    markup_usd_macbook: Decimal = Field(
        default=Decimal("110"), description="Наценка MacBook, USD (для смешанного прайса)."
    )
    missing_price_text: str = "по запросу"
    delimiter_out: str = ";"
    include_cn_us_13_16: bool = False


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
    return {"status": "ok"}


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


def _iphone_tab_csv(req: IphoneTabRequest) -> str:
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


@app.post("/process/iphone", response_class=Response)
def process_iphone_tab(req: IphoneTabRequest) -> Response:
    csv_text = _iphone_tab_csv(req)
    name = "iphone_merged.csv" if req.merge else "iphone_retail.csv"
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


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
        airpods_order=AIRPODS_ORDER,
        airpods_map=AIRPODS_BASE,
        macbook_order=MACBOOK_ORDER,
        macbook_map=MACBOOK_BASE,
        usd_to_byn=req.usd_to_byn,
        markup_usd_iphone=req.markup_usd_iphone,
        markup_usd_watch=req.markup_usd_watch,
        markup_usd_ipad=req.markup_usd_ipad,
        markup_usd_airpods=req.markup_usd_airpods,
        markup_usd_macbook=req.markup_usd_macbook,
        missing_price_text=req.missing_price_text,
        delimiter_out=req.delimiter_out,
        include_cn_us_13_16=req.include_cn_us_13_16,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="retail_mixed.csv"'},
    )

