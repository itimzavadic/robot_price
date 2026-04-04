from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from io import StringIO
from pathlib import Path
from collections import defaultdict
from typing import Optional

import iphone_processor as base_proc
from iphone_processor import _iter_input_rows_from_string
from price_merge import merge_min_byn

MACBOOK_ICON = "\U0001f4bb"  # 💻
DASH_LINE = "------------------------"

_TRAILING_FLAGS = re.compile(r"[\U0001F1E6-\U0001F1FF]{2,}$")
_TRAIL_SKU = re.compile(
    r"\s+(?:[A-Z]{1,4}\d{2,}[A-Z0-9]*)(?:/[A-Z0-9]+)?\s*$",
    re.IGNORECASE,
)
# MW0W3, MRYM3, MC6T4 — одна цифра в середине/конце
_TRAIL_APPLE_CONFIG = re.compile(r"\s+([A-Z][A-Z0-9]{3,})\s*$", re.IGNORECASE)
_RE_APPLE_SKU = re.compile(r"^[A-Z]{1,4}\d[A-Z0-9]{2,}$", re.IGNORECASE)

_RE_SEC_AIR_PRO = re.compile(
    r"^(?:MacBook\s+)?(Air|Pro)\s+(\d{2})[\"”″]?\s+"
    r"(?:(\d{4})\s+)?"
    r"(M\d+|A\d+)(\s*Pro)?\s+"
    r"(\d+)\s*/\s*(\d+|1[tT][bB])\s*:\s*$",
    re.IGNORECASE,
)
_RE_SEC_NEO = re.compile(
    r"^(?:MacBook\s+)?Neo\s+(?:(\d{4})\s+)?"
    r"(M\d+|A\d+)(\s*Pro)?\s+"
    r"(\d+)\s*/\s*(\d+|1[tT][bB])\s*:\s*$",
    re.IGNORECASE,
)

_RE_FULL_NEO = re.compile(
    r"^MacBook\s+Neo\s+(\d{4})\s+(M\d+|A\d+)(\s*Pro)?\s+"
    r"(\d+)\s*/\s*(\d+|1[tT][bB])\s+(.+)$",
    re.IGNORECASE,
)
_RE_FULL_AIR_PRO = re.compile(
    r"^MacBook\s+(Air|Pro)\s+(\d{2})[\"”″]?\s+(\d{4})\s+"
    r"(M\d+|A\d+)(\s*Pro)?\s+"
    r"(\d+)\s*/\s*(\d+|1[tT][bB])\s+(.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MacbookKey:
    family: str
    inch: str
    year: int
    chip: str
    spec: str
    color: str


@dataclass
class _SectionCtx:
    family: str
    inch: str
    year: Optional[int]
    chip: str
    spec: str


def _normalize_storage_tok(tok: str) -> str:
    t = tok.strip().lower().replace("тб", "tb")
    if t.endswith("tb"):
        return t[:-2] + "TB"
    return tok.strip()


def _build_spec(ram: str, storage_tok: str) -> str:
    return f"{ram.strip()}/{_normalize_storage_tok(storage_tok)}"


def _normalize_parse_line(s: str) -> str:
    """Кириллическая «М» (U+041C) вместо латинской M в чипе M3/M4 и т.д."""
    return s.replace("\u041c", "M").replace("\u0421", "C")


def _spec_ram_storage_sort(spec: str) -> tuple[int, int]:
    ram_s, st_s = spec.split("/", 1)
    ram = int(ram_s.strip())
    u = st_s.strip().upper()
    if u.endswith("TB"):
        n = int(re.sub(r"\D", "", u.replace("TB", "")) or "1")
        return (ram, n * 1024)
    return (ram, int(re.sub(r"\D", "", u) or "0"))


def _chip_sort_tuple(chip: str) -> tuple[int, int, int]:
    chip = chip.strip()
    m = re.match(r"^M(\d+)(\s+Pro)?$", chip, re.IGNORECASE)
    if m:
        n = int(m.group(1))
        pro = 1 if m.group(2) and m.group(2).strip().lower() == "pro" else 0
        return (0, n, pro)
    m = re.match(r"^A(\d+)", chip, re.IGNORECASE)
    if m:
        return (1, int(m.group(1)), 0)
    return (9, 0, 0)


def _block_sort_tuple(k: MacbookKey) -> tuple:
    """Порядок блоков: линейка → диагональ → год → чип → конфигурация ОЗУ/диск."""
    fam = {"Neo": 0, "Air": 1, "Pro": 2}.get(k.family, 9)
    inch = int(k.inch) if k.inch else 0
    sp = _spec_ram_storage_sort(k.spec)
    return (fam, inch, k.year, _chip_sort_tuple(k.chip), sp[0], sp[1])


def _chip_from_groups(base: str, pro_suffix: Optional[str]) -> str:
    c = base.strip()
    if pro_suffix and pro_suffix.strip().lower() == "pro":
        return f"{c} Pro"
    return c


def _strip_macbook_noise(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^[\U0001f4bb\u200d\s\ufe0f]+", "", s)
    s = _TRAILING_FLAGS.sub("", s)
    while True:
        m = _TRAIL_SKU.search(s)
        if not m:
            break
        s = s[: m.start()].rstrip()
    while True:
        m = _TRAIL_APPLE_CONFIG.search(s)
        if not m or not any(c.isdigit() for c in m.group(1)):
            break
        s = s[: m.start()].rstrip()
    return re.sub(r"\s+", " ", s).strip()


def _parse_section_header(line: str) -> Optional[_SectionCtx]:
    s = _normalize_parse_line(line.strip())
    m = _RE_SEC_AIR_PRO.match(s)
    if m:
        fam, inch, y, chip_b, pro_s, ra, st = m.groups()
        year = int(y) if y else None
        chip = _chip_from_groups(chip_b, pro_s)
        spec = _build_spec(ra, st)
        return _SectionCtx(fam, inch, year, chip, spec)
    m = _RE_SEC_NEO.match(s)
    if m:
        y, chip_b, pro_s, ra, st = m.groups()
        year = int(y) if y else None
        chip = _chip_from_groups(chip_b, pro_s)
        spec = _build_spec(ra, st)
        return _SectionCtx("Neo", "", year, chip, spec)
    return None


def _parse_detail_color_price(line: str) -> Optional[tuple[str, Decimal]]:
    if "-" not in line:
        return None
    left, right = line.rsplit("-", 1)
    price_usd = base_proc._try_parse_price_usd(right.strip())
    if price_usd is None:
        return None
    left = left.strip()
    parts = left.split()
    if not parts:
        return None
    if _RE_APPLE_SKU.match(parts[0]):
        color = " ".join(parts[1:]).strip()
    else:
        color = left
    if not color:
        return None
    return color, price_usd


def _candidates_for_section(all_keys: list[MacbookKey], sec: _SectionCtx) -> list[MacbookKey]:
    out: list[MacbookKey] = []
    for k in all_keys:
        if k.family != sec.family:
            continue
        if sec.family == "Neo":
            if sec.year is not None and k.year != sec.year:
                continue
        else:
            if k.inch != sec.inch:
                continue
            if sec.year is not None and k.year != sec.year:
                continue
        if k.chip != sec.chip:
            continue
        if k.spec != sec.spec:
            continue
        out.append(k)
    return out


def _match_color(raw: str, allowed: set[str]) -> Optional[str]:
    t = raw.strip().lower().replace("grey", "gray")
    for a in allowed:
        if a.lower() == t:
            return a
    if "sky" in t and "blue" in t:
        for a in allowed:
            if "sky" in a.lower():
                return a
    if t in ("space gray", "space grey"):
        for a in allowed:
            if a.lower() == "space gray":
                return a
    if t == "gray":
        grays = [a for a in allowed if a.lower() in ("gray", "grey")]
        if len(grays) == 1:
            return grays[0]
        for a in allowed:
            if a.lower() == "gray":
                return a
    return None


def _key_from_parsed(
    family: str,
    inch: str,
    year: int,
    chip: str,
    spec: str,
    color_raw: str,
    all_keys: list[MacbookKey],
) -> Optional[MacbookKey]:
    cands = [
        k
        for k in all_keys
        if k.family == family
        and k.inch == inch
        and k.year == year
        and k.chip == chip
        and k.spec == spec
    ]
    if not cands:
        return None
    allowed = {k.color for k in cands}
    col = _match_color(color_raw, allowed)
    if not col:
        return None
    for k in cands:
        if k.color == col:
            return k
    return None


def _parse_full_macbook_key(clean: str, all_keys: list[MacbookKey]) -> Optional[MacbookKey]:
    s = _normalize_parse_line(clean.strip())
    m = _RE_FULL_NEO.match(s)
    if m:
        year_s, chip_b, pro_s, ra, st, color_raw = m.groups()
        chip = _chip_from_groups(chip_b, pro_s)
        spec = _build_spec(ra, st)
        return _key_from_parsed("Neo", "", int(year_s), chip, spec, color_raw.strip(), all_keys)
    m = _RE_FULL_AIR_PRO.match(s)
    if m:
        fam, inch, year_s, chip_b, pro_s, ra, st, color_raw = m.groups()
        chip = _chip_from_groups(chip_b, pro_s)
        spec = _build_spec(ra, st)
        return _key_from_parsed(
            fam, inch, int(year_s), chip, spec, color_raw.strip(), all_keys
        )
    return None


def _line_smells_macbook(name_raw: str) -> bool:
    if MACBOOK_ICON in name_raw:
        return True
    return "macbook" in name_raw.lower()


def _leading_sku_detail_line(name_raw: str) -> bool:
    parts = name_raw.split()
    if not parts:
        return False
    return bool(_RE_APPLE_SKU.match(parts[0]))


def _is_separator_line(s: str) -> bool:
    t = s.strip()
    if not t:
        return False
    dashish = frozenset("—-\u2013\u2014_\u2500 \t")
    return all(ch in dashish for ch in t)


def _collect_multiline_sections(
    text: str,
    *,
    all_keys: list[MacbookKey],
    markup_usd: Decimal,
    usd_to_byn: Decimal,
    best: dict[MacbookKey, int],
    has_price: set[MacbookKey],
) -> None:
    pending: Optional[_SectionCtx] = None
    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s:
            continue
        if _is_separator_line(s):
            continue

        sec = _parse_section_header(s)
        if sec:
            pending = sec
            continue

        if pending:
            det = _parse_detail_color_price(s)
            if det:
                color_raw, price_usd = det
                cands = _candidates_for_section(all_keys, pending)
                allowed = {k.color for k in cands}
                col = _match_color(color_raw, allowed)
                if col:
                    for k in cands:
                        if k.color != col:
                            continue
                        byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)
                        has_price.add(k)
                        prev = best.get(k)
                        if prev is None or byn < prev:
                            best[k] = byn
                        break
                continue


def _format_macbook_line(item: dict, *, price_byn: Optional[int], missing_price_text: str) -> str:
    # Дюймы в CSV: U+2033 вместо ", иначе writer удваивает кавычки в ячейке.
    core = str(item["retail_core"]).replace('"', "\u2033")
    model = f"{MACBOOK_ICON}{core}"
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _csv_one_cell_row(value: str, delimiter: str) -> str:
    buf = StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow([value])
    return buf.getvalue().rstrip("\n")


def wholesale_text_looks_like_macbook(text: str) -> bool:
    """Опт с MacBook: слово MacBook, иконка 💻 или заголовки вида «Air 13" M4 16/256:»."""
    if "macbook" in text.lower():
        return True
    if MACBOOK_ICON in text:
        return True
    for line in text.splitlines():
        if _parse_section_header(line.strip()):
            return True
    return False


def load_macbook_base(path: Path) -> tuple[list[MacbookKey], dict[MacbookKey, dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    order: list[MacbookKey] = []
    base: dict[MacbookKey, dict] = {}
    for item in data:
        key = MacbookKey(
            family=str(item["family"]),
            inch=str(item.get("inch") or ""),
            year=int(item["year"]),
            chip=str(item["chip"]),
            spec=str(item["spec"]),
            color=str(item["color"]),
        )
        order.append(key)
        base[key] = item
    return order, base


def collect_macbook_best_byn_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[MacbookKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
) -> tuple[dict[MacbookKey, int], set[MacbookKey]]:
    best: dict[MacbookKey, int] = {}
    has_price: set[MacbookKey] = set()
    all_keys = list(base.keys())

    _collect_multiline_sections(
        input_text,
        all_keys=all_keys,
        markup_usd=markup_usd,
        usd_to_byn=usd_to_byn,
        best=best,
        has_price=has_price,
    )

    for name_raw, price_raw in _iter_input_rows_from_string(input_text, input_format=input_format):
        if price_raw is None:
            continue
        if not _line_smells_macbook(name_raw):
            continue
        if _leading_sku_detail_line(name_raw):
            continue
        if "📱" in name_raw or "⌚" in name_raw or "\u231a" in name_raw:
            continue
        low = name_raw.lower()
        if "ipad" in low or "\u25fe" in name_raw or "\U0001f3a7" in name_raw:
            continue

        price_usd = base_proc._try_parse_price_usd(price_raw)
        if price_usd is None:
            continue

        clean = _strip_macbook_noise(name_raw)
        key = _parse_full_macbook_key(clean, all_keys)
        if key is None:
            continue

        byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)
        has_price.add(key)
        prev = best.get(key)
        if prev is None or byn < prev:
            best[key] = byn

    return best, has_price


def format_macbook_to_csv(
    best: dict[MacbookKey, int],
    has_price: set[MacbookKey],
    *,
    base_order: list[MacbookKey],
    base: dict[MacbookKey, dict],
    missing_price_text: str,
    delimiter_out: str,
) -> str:
    blocks: dict[tuple, list[MacbookKey]] = defaultdict(list)
    for key in base_order:
        blocks[_block_sort_tuple(key)].append(key)

    sorted_block_keys = sorted(blocks.keys())
    rows_out: list[str] = []
    for bi, bkey in enumerate(sorted_block_keys):
        if bi > 0:
            rows_out.append(_csv_one_cell_row(DASH_LINE, delimiter_out))
        keys_in = blocks[bkey]
        keys_in.sort(
            key=lambda k: (
                k not in has_price,
                best[k] if k in has_price else 0,
                k.color.lower(),
            )
        )
        for key in keys_in:
            item = base[key]
            if key in has_price:
                line = _format_macbook_line(
                    item, price_byn=best[key], missing_price_text=missing_price_text
                )
            else:
                line = _format_macbook_line(item, price_byn=None, missing_price_text=missing_price_text)
            rows_out.append(_csv_one_cell_row(line, delimiter_out))
    return "\n".join(rows_out) + ("\n" if rows_out else "")


def process_macbook_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[MacbookKey],
    base: dict[MacbookKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    b, h = collect_macbook_best_byn_from_text(
        input_text,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd,
    )
    return format_macbook_to_csv(
        b,
        h,
        base_order=base_order,
        base=base,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )


def merge_macbook_from_texts(
    raw_a: str,
    raw_b: str,
    *,
    input_format: str,
    base_order: list[MacbookKey],
    base: dict[MacbookKey, dict],
    usd_to_byn: Decimal,
    markup_usd_a: Decimal,
    markup_usd_b: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    ba, _ = collect_macbook_best_byn_from_text(
        raw_a,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd_a,
    )
    bb, _ = collect_macbook_best_byn_from_text(
        raw_b,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd_b,
    )
    merged = merge_min_byn(ba, bb)
    return format_macbook_to_csv(
        merged,
        set(merged.keys()),
        base_order=base_order,
        base=base,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
    )
