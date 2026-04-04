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

IPAD_ICON = "\u25fe\ufe0f"

_TRAILING_FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2,}$")
_TRAIL_SKU = re.compile(
    r"\s+(?:[A-Z]{1,4}\d{2,}[A-Z0-9]*)(?:/[A-Z0-9]+)?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IpadKey:
    kind: str  # base11 | air | pro | mini
    inch: str  # 11, 13, "" для mini
    chip: str  # M2, M3, M4, M5, "" для base/mini
    memory: str
    conn: str  # Wi-Fi | LTE
    color: str


def _strip_noise(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^[\u25fe\u25aa\u25fc\u2b1b\u231a\ufe0f\s]+", "", s, flags=re.I)
    s = re.sub(r"^📺\s*", "", s)
    s = re.sub(r"^◾\s*", "", s)
    s = re.sub(r"^▪\s*", "", s)
    s = re.sub(r"[\u201c\u201d\"]", " ", s)
    s = re.sub(r"\s*тонкая\s*", " ", s, flags=re.IGNORECASE)
    s = _TRAILING_FLAGS.sub("", s)
    while True:
        m = _TRAIL_SKU.search(s)
        if not m:
            break
        s = s[: m.start()].rstrip()
    # «128Gb» / «256GB» слитно с цифрами — для парсера то же, что «128 » / «256 »
    s = re.sub(r"(\d+)\s*gb\b", r"\1 ", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d+)\s*tb\b", r"\1 ", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _conn_from_token(tok: Optional[str]) -> Optional[str]:
    if tok is None:
        return None
    t = tok.lower().replace("+", " ").replace("-", " ")
    if "wifi" in t or "wi fi" in t:
        if any(x in t for x in ("lte", "5g", "cell", "cellular")):
            return "LTE"
        return "Wi-Fi"
    if any(x in t for x in ("lte", "5g", "cell", "cellular", "5г")):
        return "LTE"
    return None


def _color_canon(raw: str, *, pro: bool = False) -> Optional[str]:
    sl = raw.lower().replace("grey", "gray")
    if pro:
        if sl == "black":
            return "Black"
        if sl == "silver":
            return "Silver"
        return None
    if sl == "starlight":
        return "Starlight"
    if sl in ("gray", "blue", "purple", "yellow", "silver", "pink"):
        return sl.title()
    return None


_re_base = re.compile(
    r"ipad\s+11\s+(\d+)\s+(?:(wifi|wi\s*fi|wi-?fi|lte|5g|cellular)\s+)?(blue|yellow|silver|pink)\b",
    re.IGNORECASE,
)
_re_mini = re.compile(
    r"ipad\s+mini\s+7\s+(\d+)\s+(?:(wifi|wi\s*fi|wi-?fi|lte|5g|cellular)\s+)?(gray|grey|blue|purple|starlight)\b",
    re.IGNORECASE,
)
_CONN = r"(?:wifi|wi\s*fi|wi-?fi|lte|5g|cellular)"
_re_air_ipad_cm = re.compile(
    rf"ipad\s+air\s+(\d{{2}})\s*(?:inch\s*)?(?:2024\s+)?(m\d+)\s+((?:{_CONN})\s+)?(\d+)\s+(gray|grey|blue|purple|starlight)\b",
    re.IGNORECASE,
)
_re_air_ipad_mc = re.compile(
    rf"ipad\s+air\s+(\d{{2}})\s*(?:inch\s*)?(?:2024\s+)?(m\d+)\s+(\d+)\s+((?:{_CONN})\s+)?(gray|grey|blue|purple|starlight)\b",
    re.IGNORECASE,
)
_re_air_plain_cm = re.compile(
    rf"^air\s+(\d{{2}})\s+(m\d+)\s+((?:{_CONN})\s+)?(\d+)\s+(gray|grey|blue|purple|starlight)\b",
    re.IGNORECASE,
)
_re_air_plain_mc = re.compile(
    rf"^air\s+(\d{{2}})\s+(m\d+)\s+(\d+)\s+((?:{_CONN})\s+)?(gray|grey|blue|purple|starlight)\b",
    re.IGNORECASE,
)
_re_pro_ipad_cm = re.compile(
    rf"ipad\s+pro\s+(\d{{2}})\s*(?:inch\s*)?(m[45])\s+((?:{_CONN})\s+)?(\d+)\s+(black|silver)\b",
    re.IGNORECASE,
)
_re_pro_ipad_mc = re.compile(
    rf"ipad\s+pro\s+(\d{{2}})\s*(?:inch\s*)?(m[45])\s+(\d+)\s+((?:{_CONN})\s+)?(black|silver)\b",
    re.IGNORECASE,
)
_re_pro_plain_cm = re.compile(
    rf"^pro\s+(\d{{2}})\s+(m[45])\s+((?:{_CONN})\s+)?(\d+)\s+(black|silver)\b",
    re.IGNORECASE,
)
_re_pro_plain_mc = re.compile(
    rf"^pro\s+(\d{{2}})\s+(m[45])\s+(\d+)\s+((?:{_CONN})\s+)?(black|silver)\b",
    re.IGNORECASE,
)


def _parse_ipad_name(name_raw: str, ipad_map: dict[IpadKey, dict]) -> Optional[IpadKey]:
    s = _strip_noise(name_raw)
    if not s:
        return None
    low = s.lower()

    m = _re_mini.search(low)
    if m:
        mem, ctok, col = m.group(1), m.group(2), m.group(3)
        conn = _conn_from_token(ctok) or "Wi-Fi"
        c = _color_canon(col, pro=False)
        if c is None:
            return None
        key = IpadKey("mini", "", "", mem, conn, c)
        return key if key in ipad_map else None

    m = _re_base.search(low)
    if m:
        mem, ctok, col = m.group(1), m.group(2), m.group(3)
        conn = _conn_from_token(ctok) or "Wi-Fi"
        c = _color_canon(col, pro=False)
        if c is None:
            return None
        key = IpadKey("base11", "11", "", mem, conn, c)
        return key if key in ipad_map else None

    for rx in (_re_air_ipad_cm, _re_air_ipad_mc, _re_air_plain_cm, _re_air_plain_mc):
        m = rx.search(low)
        if m:
            inch, chip = m.group(1), m.group(2).upper()
            if rx in (_re_air_ipad_cm, _re_air_plain_cm):
                ctok, mem, col = m.group(3), m.group(4), m.group(5)
            else:
                mem, ctok, col = m.group(3), m.group(4), m.group(5)
            ctok = (ctok or "").strip() or None
            conn = _conn_from_token(ctok) or "Wi-Fi"
            c = _color_canon(col, pro=False)
            if c is None:
                return None
            key = IpadKey("air", inch, chip, mem, conn, c)
            return key if key in ipad_map else None

    for rx in (_re_pro_ipad_cm, _re_pro_ipad_mc, _re_pro_plain_cm, _re_pro_plain_mc):
        m = rx.search(low)
        if m:
            inch, chip = m.group(1), m.group(2).upper()
            if rx in (_re_pro_ipad_cm, _re_pro_plain_cm):
                ctok, mem, col = m.group(3), m.group(4), m.group(5)
            else:
                mem, ctok, col = m.group(3), m.group(4), m.group(5)
            ctok = (ctok or "").strip() or None
            conn = _conn_from_token(ctok) or "Wi-Fi"
            c = _color_canon(col, pro=True)
            if c is None:
                return None
            key = IpadKey("pro", inch, chip, mem, conn, c)
            return key if key in ipad_map else None

    return None


def _format_ipad_line(key: IpadKey, *, price_byn: Optional[int], missing_price_text: str) -> str:
    if key.kind == "base11":
        core = f"iPad {key.inch} {key.memory} {key.conn} {key.color}"
    elif key.kind == "air":
        core = f"iPad Air {key.inch} {key.chip} {key.memory} {key.conn} {key.color}"
    elif key.kind == "pro":
        core = f"iPad Pro {key.inch} {key.chip} {key.memory} {key.conn} {key.color}"
    else:
        core = f"iPad mini 7 {key.memory} {key.conn} {key.color}"
    model = f"{IPAD_ICON}{core}"
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _csv_one_cell_row(value: str, delimiter: str) -> str:
    buf = StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow([value])
    return buf.getvalue().rstrip("\n")


def _ipad_group(k: IpadKey) -> tuple:
    if k.kind == "base11":
        return ("base11",)
    if k.kind == "mini":
        return ("mini",)
    if k.kind == "air":
        return ("air", k.inch, k.chip)
    return ("pro", k.inch, k.chip)


def _inject_ipad_separators(pairs: list[tuple[IpadKey, str]]) -> list[str]:
    out: list[str] = []
    dash = "------------------------"
    prev_g: Optional[tuple] = None
    prev_mem: Optional[str] = None
    for key, line in pairs:
        g = _ipad_group(key)
        if prev_g is not None and g != prev_g:
            out.append(dash)
            prev_mem = None
        elif prev_g == g and prev_mem is not None and key.memory != prev_mem:
            out.append("")
        out.append(line)
        prev_g = g
        prev_mem = key.memory
    return out


def load_ipad_base(path: Path) -> tuple[list[IpadKey], dict[IpadKey, dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    order: list[IpadKey] = []
    base: dict[IpadKey, dict] = {}
    for item in data:
        key = IpadKey(
            kind=str(item["kind"]),
            inch=str(item.get("inch", "")),
            chip=str(item.get("chip", "")),
            memory=str(item["memory"]),
            conn=str(item["conn"]),
            color=str(item["color"]),
        )
        order.append(key)
        base[key] = item
    return order, base


def collect_ipad_best_byn_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[IpadKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
) -> tuple[dict[IpadKey, int], set[IpadKey]]:
    best: dict[IpadKey, int] = {}
    has_price: set[IpadKey] = set()

    for name_raw, price_raw in _iter_input_rows_from_string(input_text, input_format=input_format):
        if price_raw is None:
            continue
        if "📱" in name_raw:
            continue
        if "⌚" in name_raw or "\u231a" in name_raw:
            continue
        if "\U0001f3a7" in name_raw or re.search(r"\bair\s*pods?\b", name_raw, flags=re.IGNORECASE):
            continue

        price_usd = base_proc._try_parse_price_usd(price_raw)
        if price_usd is None:
            continue

        key = _parse_ipad_name(name_raw, base)
        if key is None:
            continue

        byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)
        has_price.add(key)
        prev = best.get(key)
        if prev is None or byn < prev:
            best[key] = byn

    return best, has_price


def format_ipad_to_csv(
    best: dict[IpadKey, int],
    has_price: set[IpadKey],
    *,
    base_order: list[IpadKey],
    missing_price_text: str,
    delimiter_out: str,
) -> str:
    pairs: list[tuple[IpadKey, str]] = []
    for key in base_order:
        if key in has_price:
            line = _format_ipad_line(key, price_byn=best[key], missing_price_text=missing_price_text)
        else:
            line = _format_ipad_line(key, price_byn=None, missing_price_text=missing_price_text)
        pairs.append((key, line))

    lines = _inject_ipad_separators(pairs)
    rows_out: list[str] = []
    for L in lines:
        if L == "":
            rows_out.append("")
        else:
            rows_out.append(_csv_one_cell_row(L, delimiter_out))
    return "\n".join(rows_out) + ("\n" if rows_out else "")


def process_ipad_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[IpadKey],
    base: dict[IpadKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    b, h = collect_ipad_best_byn_from_text(
        input_text,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd,
    )
    return format_ipad_to_csv(
        b, h, base_order=base_order, missing_price_text=missing_price_text, delimiter_out=delimiter_out
    )


def merge_ipad_from_texts(
    raw_a: str,
    raw_b: str,
    *,
    input_format: str,
    base_order: list[IpadKey],
    base: dict[IpadKey, dict],
    usd_to_byn: Decimal,
    markup_usd_a: Decimal,
    markup_usd_b: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    ba, _ = collect_ipad_best_byn_from_text(
        raw_a, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_a
    )
    bb, _ = collect_ipad_best_byn_from_text(
        raw_b, input_format=input_format, base=base, usd_to_byn=usd_to_byn, markup_usd=markup_usd_b
    )
    merged = merge_min_byn(ba, bb)
    return format_ipad_to_csv(
        merged,
        set(merged.keys()),
        base_order=base_order,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )
