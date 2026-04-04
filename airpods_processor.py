from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Optional

import iphone_processor as base_proc
from iphone_processor import _iter_input_rows_from_string
from price_merge import merge_min_byn

AIRPODS_ICON = "\U0001f3a7"  # 🎧

_TRAILING_FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2,}$")
_TRAIL_SKU = re.compile(
    r"\s+(?:[A-Z]{1,4}\d{2,}[A-Z0-9]*)(?:/[A-Z0-9]+)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AirpodsKey:
    slug: str


def _strip_noise(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^[\U0001f3a7\u200d\s\ufe0f]+", "", s)
    s = re.sub(r"\s+", " ", s)
    s = _TRAILING_FLAGS.sub("", s)
    while True:
        m = _TRAIL_SKU.search(s)
        if not m:
            break
        s = s[: m.start()].rstrip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _has_airpods_context(orig: str, low: str) -> bool:
    if AIRPODS_ICON in orig or "\U0001f3a7" in orig:
        return True
    if re.search(r"\bair\s*pods?\b", low):
        return True
    if re.search(r"\bэйр\s*под", low) or re.search(r"\bейр\s*под", low):
        return True
    if re.search(r"\bap\s*[234]\b|\bap[234]\b", low):  # AP2 / ap 4
        return True
    if re.search(r"\bpods?\s+pro\b", low):
        return True
    if re.search(r"air\s*pods?.*\bmax\b|\bmax\b.*air\s*pods?", low):
        return True
    return False


def _parse_airpods_name(name_raw: str, airpods_map: dict[AirpodsKey, dict]) -> Optional[AirpodsKey]:
    """Сопоставление оптовой строки с позицией из базы (порядок правил: от частных к общим)."""
    orig = name_raw
    s = _strip_noise(name_raw)
    if not s:
        return None
    low = s.lower()

    if not _has_airpods_context(orig, low):
        return None

    def pick(slug: str) -> Optional[AirpodsKey]:
        k = AirpodsKey(slug)
        return k if k in airpods_map else None

    # Max (2024) — до Pro / цифр, чтобы не перепутать с «4» в годах
    if re.search(r"\bmax\b", low):
        if re.search(r"midnight|полночн", low):
            return pick("max_midnight")
        if re.search(r"starlight|star\s*light|зв[ёе]зд", low):
            return pick("max_starlight")
        if re.search(r"\bblue\b|син", low):
            return pick("max_blue")
        if re.search(r"purple|фиол", low):
            return pick("max_purple")
        if re.search(r"orange|оранж", low):
            return pick("max_orange")

    # Pro 3 / Pro 2 — до обычных «3»/«2»
    if re.search(r"\bpro\b", low):
        if re.search(r"\bpro\s*3\b|\bpro\s+3\b", low):
            return pick("app3")
        if re.search(r"\bpro\s*2\b|\bpro\s+2\b", low):
            return pick("app2")

    # Сокращения AP2 / AP3 / AP4 (цифра не на границе слова в «ap4»)
    if re.search(r"\bap4\b|\bap\s+4\b", low):
        if re.search(r"\banc\b|noise|шум|шумопод|активн|cancel", low):
            return pick("ap4_anc")
        if "pro" not in low and "max" not in low:
            return pick("ap4")

    # AirPods 4 с ANC (без префикса ap)
    if re.search(r"\b4\b", low) and re.search(
        r"\banc\b|noise|шум|шумопод|активн|cancel", low
    ):
        return pick("ap4_anc")

    # AirPods 4 без Pro/Max
    if re.search(r"\b4\b", low) and "pro" not in low and "max" not in low:
        return pick("ap4")

    if re.search(r"\bap3\b|\bap\s+3\b", low) and "pro" not in low and "max" not in low:
        return pick("ap3")

    if re.search(r"\b3\b", low) and "pro" not in low and "max" not in low:
        return pick("ap3")

    if re.search(r"\bap2\b|\bap\s+2\b", low) and "pro" not in low and "max" not in low:
        return pick("ap2")

    if re.search(r"\b2\b", low) and "pro" not in low and "max" not in low:
        return pick("ap2")

    return None


def _format_airpods_line_from_base(
    item: dict, *, price_byn: Optional[int], missing_price_text: str
) -> str:
    core = str(item["retail_core"])
    model = f"{AIRPODS_ICON}{core}"
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _csv_one_cell_row(value: str, delimiter: str) -> str:
    buf = StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow([value])
    return buf.getvalue().rstrip("\n")


def load_airpods_base(path: Path) -> tuple[list[AirpodsKey], dict[AirpodsKey, dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    order: list[AirpodsKey] = []
    base: dict[AirpodsKey, dict] = {}
    for item in data:
        key = AirpodsKey(slug=str(item["slug"]))
        order.append(key)
        base[key] = item
    return order, base


def collect_airpods_best_byn_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[AirpodsKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
) -> tuple[dict[AirpodsKey, int], set[AirpodsKey]]:
    best: dict[AirpodsKey, int] = {}
    has_price: set[AirpodsKey] = set()

    for name_raw, price_raw in _iter_input_rows_from_string(input_text, input_format=input_format):
        if price_raw is None:
            continue
        if "📱" in name_raw:
            continue
        if "⌚" in name_raw or "\u231a" in name_raw:
            continue
        low = name_raw.lower()
        if "ipad" in low or "\u25fe" in name_raw:
            continue

        price_usd = base_proc._try_parse_price_usd(price_raw)
        if price_usd is None:
            continue

        key = _parse_airpods_name(name_raw, base)
        if key is None:
            continue

        byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)
        has_price.add(key)
        prev = best.get(key)
        if prev is None or byn < prev:
            best[key] = byn

    return best, has_price


def format_airpods_to_csv(
    best: dict[AirpodsKey, int],
    has_price: set[AirpodsKey],
    *,
    base_order: list[AirpodsKey],
    base: dict[AirpodsKey, dict],
    missing_price_text: str,
    delimiter_out: str,
) -> str:
    pairs: list[tuple[AirpodsKey, str]] = []
    for key in base_order:
        item = base[key]
        if key in has_price:
            line = _format_airpods_line_from_base(
                item, price_byn=best[key], missing_price_text=missing_price_text
            )
        else:
            line = _format_airpods_line_from_base(
                item, price_byn=None, missing_price_text=missing_price_text
            )
        pairs.append((key, line))

    rows_out = [_csv_one_cell_row(line, delimiter_out) for _, line in pairs]
    return "\n".join(rows_out) + ("\n" if rows_out else "")


def process_airpods_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[AirpodsKey],
    base: dict[AirpodsKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    b, h = collect_airpods_best_byn_from_text(
        input_text,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd,
    )
    return format_airpods_to_csv(
        b,
        h,
        base_order=base_order,
        base=base,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )


def merge_airpods_from_texts(
    raw_a: str,
    raw_b: str,
    *,
    input_format: str,
    base_order: list[AirpodsKey],
    base: dict[AirpodsKey, dict],
    usd_to_byn: Decimal,
    markup_usd_a: Decimal,
    markup_usd_b: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    ba, _ = collect_airpods_best_byn_from_text(
        raw_a, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_a
    )
    bb, _ = collect_airpods_best_byn_from_text(
        raw_b, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_b
    )
    merged = merge_min_byn(ba, bb)
    return format_airpods_to_csv(
        merged,
        set(merged.keys()),
        base_order=base_order,
        base=base,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )
