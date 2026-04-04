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

WATCH_ICON = "⌚️"

# Порядок важен: более длинные серии раньше.
_SERIES_PATTERN = re.compile(
    r"^(Ultra 3 2025|Ultra 2 2024|SE 3 2025|SE 2|SE 2023|S11|S10)\s+",
    re.IGNORECASE,
)

_STRAP_SPLIT = re.compile(
    r"\s+(?:Nike Sport Loop|Milanese Loop|Sport Loop|Loop|SB|TL|AL|OB|M/L|S/M)\b",
    re.IGNORECASE,
)

# Артикулы вроде MEH94, MX4R3 (есть цифры); не трогаем слова цветов вроде Midnight.
_TRAILING_SKU = re.compile(
    r"\s+(?:[A-Za-z]{1,5}\d{2,}[A-Za-z0-9]*|\d{3,}[A-Za-z0-9]+)(?:/[A-Za-z0-9]+)?\s*$",
)

_TRAILING_FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2,}$")


@dataclass(frozen=True)
class WatchKey:
    series: str
    size: str
    color: str


def _series_canon(raw: str) -> str:
    k = raw.strip().lower()
    if k == "se 2023":
        return "SE 2"
    mapping = {
        "ultra 3 2025": "Ultra 3 2025",
        "ultra 2 2024": "Ultra 2 2024",
        "se 3 2025": "SE 3 2025",
        "se 2": "SE 2",
        "s11": "S11",
        "s10": "S10",
    }
    return mapping.get(k, raw.strip().title())


def _strip_leading_watch_emoji(s: str) -> str:
    s = s.strip()
    # ⌚ (U+231A) и необязательный VS15 (U+FE0F) — снимаем оба
    s = re.sub(r"^\u231a\ufe0f?\s*", "", s)
    s = re.sub(r"^⌚\s*", "", s)
    return s.strip()


def _normalize_watch_series_tokens(s: str) -> str:
    """SE2 / SE3 без пробела → как в базе; SE 3 без «2025» → SE 3 2025."""
    s = re.sub(r"\bSE2\b", "SE 2", s, flags=re.IGNORECASE)
    s = re.sub(r"\bSE3\b", "SE 3 2025", s, flags=re.IGNORECASE)
    s = re.sub(r"\bSE\s+3(?=\s+(?!2025\b))", "SE 3 2025", s, flags=re.IGNORECASE)
    return s


def _strip_trailing_flags_and_skus(s: str) -> str:
    s = _TRAILING_FLAGS.sub("", s).strip()
    while True:
        m = _TRAILING_SKU.search(s)
        if not m:
            break
        s = s[: m.start()].rstrip()
    return s


def _strip_strap_suffix(s: str) -> str:
    m = _STRAP_SPLIT.search(s)
    if m:
        s = s[: m.start()]
    return _strip_trailing_flags_and_skus(s.strip())


def _allowed_colors(watch_map: dict[WatchKey, dict], series: str, size: str) -> list[str]:
    out: list[str] = []
    for k in watch_map:
        if k.series == series and k.size == size:
            out.append(k.color)
    return out


def _map_color(series: str, size: str, color_fragment: str, watch_map: dict[WatchKey, dict]) -> Optional[str]:
    allowed = _allowed_colors(watch_map, series, size)
    if not allowed:
        return None

    stem = color_fragment.split("/")[0].strip()
    stem_l = stem.lower().replace("grey", "gray")

    if series.startswith("Ultra"):
        if "natural" in stem_l:
            c = "Natural Titanium"
            return c if c in allowed else None
        if "black" in stem_l:
            c = "Black Titanium"
            return c if c in allowed else None
        return None

    for canon in sorted(allowed, key=len, reverse=True):
        cl = canon.lower()
        if stem_l == cl or stem_l.startswith(cl + " ") or stem_l.startswith(cl + "/"):
            return canon
        if cl.startswith(stem_l) and len(stem_l) >= 3:
            return canon
        if stem_l.startswith(cl):
            return canon
    return None


def _parse_watch_name(name_raw: str, watch_map: dict[WatchKey, dict]) -> Optional[WatchKey]:
    s = _strip_leading_watch_emoji(name_raw)
    s = _normalize_watch_series_tokens(s)
    s = _strip_trailing_flags_and_skus(s)
    if not s:
        return None

    m = _SERIES_PATTERN.match(s)
    if not m:
        return None

    series = _series_canon(m.group(1))
    tail = s[m.end() :].strip()

    if series.startswith("Ultra"):
        size = ""
        color_raw = _strip_strap_suffix(tail)
    else:
        m2 = re.match(r"^(40|44|42|46)\s+(.+)$", tail, re.IGNORECASE)
        if not m2:
            return None
        size = m2.group(1)
        color_raw = _strip_strap_suffix(m2.group(2))

    color = _map_color(series, size, color_raw, watch_map)
    if color is None:
        return None

    key = WatchKey(series=series, size=size, color=color)
    return key if key in watch_map else None


def _parse_text_rows(input_text: str) -> Iterator[tuple[str, Optional[str]]]:
    for line in input_text.splitlines():
        s = line.strip()
        if not s or re.match(r"^[-—⌘─\s]+$", s):
            continue
        if "-" not in s:
            continue
        left, right = s.rsplit("-", 1)
        name = left.strip()
        price_raw = right.strip()
        if not name:
            continue
        yield name, price_raw


def _parse_csv_rows(input_text: str, *, delimiter: str = ";") -> Iterator[tuple[str, Optional[str]]]:
    reader = csv.reader(StringIO(input_text), delimiter=delimiter)
    for idx, row in enumerate(reader, start=1):
        if not row or len(row) < 2:
            continue
        name = row[0].strip()
        price_raw = row[1].strip()
        if idx == 1 and base_proc._try_parse_price_usd(price_raw) is None:
            continue
        yield name, price_raw


def load_watch_base(path: Path) -> tuple[list[WatchKey], dict[WatchKey, dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    order: list[WatchKey] = []
    base: dict[WatchKey, dict] = {}
    for item in data:
        key = WatchKey(
            series=str(item["series"]),
            size=str(item.get("size", "")),
            color=str(item["color"]),
        )
        order.append(key)
        base[key] = item
    return order, base


def _format_watch_line(key: WatchKey, *, price_byn: Optional[int], missing_price_text: str) -> str:
    if key.size:
        core = f"{key.series} {key.size} {key.color}"
    else:
        core = f"{key.series} {key.color}"
    model = f"{WATCH_ICON}{core}"
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _csv_one_cell_row(value: str, delimiter: str) -> str:
    buf = StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow([value])
    return buf.getvalue().rstrip("\n")


def _inject_watch_separators(pairs: list[tuple[WatchKey, str]]) -> list[str]:
    out: list[str] = []
    dash = "------------------------"
    prev_series: Optional[str] = None
    prev_size: Optional[str] = None
    for key, line in pairs:
        if prev_series is not None and key.series != prev_series:
            out.append(dash)
            prev_size = None
        elif prev_series == key.series and prev_size is not None and key.size != prev_size:
            out.append("")
        out.append(line)
        prev_series = key.series
        prev_size = key.size
    return out


def collect_watch_best_byn_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[WatchKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
) -> tuple[dict[WatchKey, int], set[WatchKey]]:
    best: dict[WatchKey, int] = {}
    has_price: set[WatchKey] = set()

    for name_raw, price_raw in _iter_input_rows_from_string(input_text, input_format=input_format):
        if price_raw is None:
            continue
        if "📱" in name_raw or "iphone" in name_raw.lower():
            continue
        wl = name_raw.lower()
        if re.search(r"\bair\s*pods?\b", wl) or "\U0001f3a7" in name_raw:
            continue
        if re.search(r"\bmacbook\b", wl):
            continue

        price_usd = base_proc._try_parse_price_usd(price_raw)
        if price_usd is None:
            continue

        key = _parse_watch_name(name_raw, base)
        if key is None:
            continue

        byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)
        has_price.add(key)
        prev = best.get(key)
        if prev is None or byn < prev:
            best[key] = byn

    return best, has_price


def format_watch_to_csv(
    best: dict[WatchKey, int],
    has_price: set[WatchKey],
    *,
    base_order: list[WatchKey],
    missing_price_text: str,
    delimiter_out: str,
) -> str:
    pairs: list[tuple[WatchKey, str]] = []
    for key in base_order:
        if key in has_price:
            line = _format_watch_line(key, price_byn=best[key], missing_price_text=missing_price_text)
        else:
            line = _format_watch_line(key, price_byn=None, missing_price_text=missing_price_text)
        pairs.append((key, line))

    lines = _inject_watch_separators(pairs)
    rows_out: list[str] = []
    for L in lines:
        if L == "":
            rows_out.append("")
        else:
            rows_out.append(_csv_one_cell_row(L, delimiter_out))
    return "\n".join(rows_out) + ("\n" if rows_out else "")


def process_watch_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[WatchKey],
    base: dict[WatchKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    b, h = collect_watch_best_byn_from_text(
        input_text,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd,
    )
    return format_watch_to_csv(
        b, h, base_order=base_order, missing_price_text=missing_price_text, delimiter_out=delimiter_out
    )


def merge_watch_from_texts(
    raw_a: str,
    raw_b: str,
    *,
    input_format: str,
    base_order: list[WatchKey],
    base: dict[WatchKey, dict],
    usd_to_byn: Decimal,
    markup_usd_a: Decimal,
    markup_usd_b: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    ba, _ = collect_watch_best_byn_from_text(
        raw_a, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_a
    )
    bb, _ = collect_watch_best_byn_from_text(
        raw_b, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_b
    )
    merged = merge_min_byn(ba, bb)
    return format_watch_to_csv(
        merged,
        set(merged.keys()),
        base_order=base_order,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )
