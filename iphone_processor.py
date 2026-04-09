#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
from io import StringIO
import json
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Optional, Iterator


@dataclass(frozen=True)
class IPhoneKey:
    year: int
    variant: str  # "", "Plus", "Pro", "Pro Max", "e"
    memory: str  # "128" | "256" | "512" | "1TB"
    color: str  # canonical color from base


def _normalize_text(s: str) -> str:
    # Unify Cyrillic/Latin variants that may appear in source.
    # For iPhone 16e model token the difference between "Е" and "е" matters.
    s = s.replace("Е", "е")
    # iPhone 16E (Latin E) -> 16e
    s = re.sub(r"\b16E\b", "16e", s)
    s = re.sub(r"\b17E\b", "17e", s)
    return s.strip()


def wholesale_line_skips_iphone_13_16_parsing(name_raw: str) -> bool:
    """Планшеты / AirPods / MacBook в опте не разбирать как iPhone 13–16."""
    lowered = _normalize_text(name_raw).lower()
    if re.search(r"\bipad\b", lowered):
        return True
    if re.search(r"\bair\s+(11|13)\s+m\d+", lowered):
        return True
    if re.search(r"\bpro\s+(11|12|13)\s+m\d+\b", lowered):
        return True
    if re.search(r"\bair\s*pods?\b", lowered):
        return True
    if "\U0001f3a7" in name_raw:
        return True
    if re.search(r"\bmacbook\b", lowered):
        return True
    return False


def wholesale_line_skips_all_iphone_row_processing(name_raw: str) -> bool:
    """Строка относится к другой категории — не гонять через парсеры iPhone (17 / Air / 13–16)."""
    lowered = _normalize_text(name_raw).lower()
    if re.search(r"\bair\s*pods?\b", lowered):
        return True
    if "\U0001f3a7" in name_raw:
        return True
    if re.search(r"\bmacbook\b", lowered):
        return True
    return False


def _is_blocked_country_flags(s: str) -> bool:
    lowered = s.lower()

    # Block strings that contain China/USA flags in any common form.
    if "🇨🇳" in s or "🇺🇸" in s:
        return True

    china = [
        r"\bcn\b",
        r"china",
        r"китай",
        r"\bcn\s*-\s*",
    ]
    usa = [
        r"\bus(a)?\b",
        r"\busa\b",
        r"сша",
        r"united\s+states",
    ]

    for pat in china + usa:
        if re.search(pat, lowered, flags=re.IGNORECASE):
            return True
    return False


def _clean_name_remove_country_flags(s: str) -> str:
    """
    Удаляем из названия явные токены стран (используется для строк без маппинга).
    """
    cleaned = _normalize_text(s)
    # Remove common country tokens while preserving the main device name.
    cleaned = re.sub(r"\bcn\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\busa?\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bchina\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"китай", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"сша", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_year_variant_memory_color(raw_name: str) -> Optional[IPhoneKey]:
    """
    Примитивный парсер названия устройства для iPhone 13-16 (блок A):
    ожидаем, что в строке встречаются:
      - год (13|14|15|16) или токен вида "16e"/"16е"
      - память (128|256|512|1TB)
      - цвет (Midnight/Starlight/Pink/...)
      - вариант (Plus/Pro/Pro Max) либо отсутствует.

    Возвращаем ключ для поиска в базе.
    """
    s = _normalize_text(raw_name)
    lowered = s.lower()

    # Variant: Pro Max must be checked before "Pro".
    variant = ""
    if re.search(r"\bpro\s*max\b", lowered) or "promax" in lowered.replace(" ", ""):
        variant = "Pro Max"
    elif re.search(r"\bpro\b", lowered):
        variant = "Pro"
    elif re.search(r"\bplus\b", lowered):
        variant = "Plus"
    else:
        # iPhone 16e: represented as "16e"/"16е" token.
        if re.search(r"\b16[еe]\b", lowered) or re.search(r"\b16[еe]\b", lowered.replace(" ", "")):
            variant = "e"

    year_match = re.search(r"\b(13|14|15|16)\b", lowered)
    if not year_match:
        # Special case: "16e" may not match "\b16\b" depending on tokenization.
        year_match = re.search(r"\b16[еe]\b", lowered)
        if not year_match:
            return None

    year = int(year_match.group(1)) if year_match.lastindex else None
    if year is None:
        # The "16e" special match gives full token not group(1); fall back to 16.
        year = 16

    # Memory
    mem = None
    mem_1tb = re.search(r"\b1\s*tb\b", lowered)
    if mem_1tb:
        mem = "1TB"
    else:
        mem_match = re.search(r"\b(128|256|512)\b", lowered)
        if mem_match:
            mem = mem_match.group(1)
        else:
            # «128Gb» / «256gb» без пробела перед gb
            mem_glued = re.search(r"(?<!\d)(128|256|512)\s*gb\b", lowered, flags=re.IGNORECASE)
            if mem_glued:
                mem = mem_glued.group(1)
    if mem is None:
        return None

    # Color candidates: take first known color token found in string.
    canonical_colors = {
        "midnight",
        "starlight",
        "pink",
        "blue",
        "green",
        "purple",
        "yellow",
        "black",
        "white",
        "natural",
        "desert",
        "ultramarine",
        "teal",
        "gold",
        "orange",
        "silver",
    }
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", s)
    tokens_lower = [t.lower() for t in tokens]

    color_raw = None
    for t in tokens_lower:
        if t in canonical_colors:
            color_raw = t
            break
    if color_raw is None:
        return None

    # Canonicalize color depending on year (conversion rules from PROJECT).
    # - iPhone 13/14: Black -> Midnight, White -> Starlight
    # - iPhone 15: Midnight -> Black
    color_canon = color_raw
    if year in (13, 14):
        if color_raw == "black":
            color_canon = "midnight"
        elif color_raw == "white":
            color_canon = "starlight"
    elif year == 15:
        if color_raw == "midnight":
            color_canon = "black"

    # Title-case the canonical color for base matching.
    # All canonical colors in base are in Title Case.
    color_canon_title = color_canon[:1].upper() + color_canon[1:]

    # If variant detected "e" ensure year=16 and variant token is consistent.
    if variant == "e":
        year = 16

    return IPhoneKey(year=year, variant=variant, memory=mem, color=color_canon_title)


IPHONE_ICON = "📱"


def _format_model_line_13_16(key: IPhoneKey, _sim_variant: str) -> str:
    # В рознице для 13–16 тип SIM не выводим (стандарт 1+1); _sim_variant — для совместимости вызова.
    if key.variant == "":
        core = f"{key.year} {key.memory} {key.color}"
    elif key.variant == "Plus":
        core = f"{key.year} Plus {key.memory} {key.color}"
    elif key.variant == "Pro":
        core = f"{key.year} Pro {key.memory} {key.color}"
    elif key.variant == "Pro Max":
        core = f"{key.year} Pro Max {key.memory} {key.color}"
    elif key.variant == "e":
        core = f"{key.year}e {key.memory} {key.color}"
    else:
        core = f"{key.year} {key.variant} {key.memory} {key.color}"
    return f"{IPHONE_ICON}{core}"


def _format_telegram_line_13_16(
    key: IPhoneKey,
    sim_variant: str,
    *,
    price_byn: Optional[int],
    missing_price_text: str,
) -> str:
    model = _format_model_line_13_16(key, sim_variant)
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _parse_price_usd_single(value: str) -> Decimal:
    """Одна числовая цена USD (фрагмент без слэша между двумя оптами)."""
    v = value.strip()
    if not v:
        raise ValueError("empty")
    vl = v.lower()
    if "нет" in vl or "нету" in vl or "n/a" in vl:
        raise ValueError("missing")

    v = v.replace("$", "").replace("USD", "").strip()
    v = re.sub(r"[^0-9.,]", "", v)
    if not v:
        raise ValueError("no digits")
    if "," in v and "." in v:
        v = v.replace(",", "")
    v = v.replace(",", ".")
    return Decimal(v)


def _parse_price_usd(value: str) -> Decimal:
    """
    Парсит USD-цену из строки.
    Две и более цены через «/» (например 680/685 за 🇮🇳/🇪🇺) — берётся минимум.
    Если цена не числовая (например, "нету"), возвращает исключение.
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty")

    if "/" in raw:
        parts = [p.strip() for p in raw.split("/") if p.strip()]
        parsed: list[Decimal] = []
        for p in parts:
            try:
                parsed.append(_parse_price_usd_single(p))
            except ValueError:
                continue
        if len(parsed) >= 2:
            return min(parsed)
        if len(parsed) == 1:
            return parsed[0]

    return _parse_price_usd_single(raw)


def _try_parse_price_usd(value: str) -> Optional[Decimal]:
    try:
        return _parse_price_usd(value)
    except Exception:
        return None


def _try_parse_price_byn(value: str) -> Optional[int]:
    """Розничная цена в BYN из ячейки прайса (цифры, пробелы-разделители, суффикс BYN, ** из Telegram)."""
    s = value.strip()
    if not s:
        return None
    s = re.sub(r"\*+", "", s)
    low = s.lower()
    for word in ("byn", "брн", "руб", "brn"):
        low = low.replace(word, "")
    digits = re.sub(r"\D", "", low)
    if not digits:
        return None
    n = int(digits)
    if n <= 0:
        return None
    return n


def round_to_tens(byn: Decimal) -> int:
    """
    Округление до числа, которое оканчивается на 0.
    Пример: 2031 -> 2030, 2035 -> 2040 (ROUND_HALF_UP на границе).
    """
    ten = Decimal(10)
    q = byn / ten
    rounded_q = q.quantize(Decimal("0"), rounding=ROUND_HALF_UP)
    return int(rounded_q * ten)


def compute_final_price_byn(price_usd: Decimal, markup_usd: Decimal, usd_to_byn: Decimal) -> int:
    """Итог: (опт USD + наценка USD) * курс → BYN, округление до десятков."""
    price_with_markup_usd = price_usd + markup_usd
    byn = price_with_markup_usd * usd_to_byn
    return round_to_tens(byn)


def _iter_input_rows_csv(input_path: Path) -> Iterator[tuple[str, Optional[str]]]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)

        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t"])
        reader = csv.reader(f, dialect=dialect)

        for row_idx, row in enumerate(reader, start=1):
            if not row:
                continue
            if len(row) < 2:
                continue
            name = row[0].strip()
            price_raw = row[1].strip()

            if row_idx == 1:
                # Header: if price doesn't parse into USD number.
                if _try_parse_price_usd(price_raw) is None:
                    continue
            yield name, price_raw


def _iter_input_rows_text(input_path: Path) -> Iterator[tuple[str, Optional[str]]]:
    """
    Парсинг текста вида:
      <device name> - <price>
      <device name> - нету
    Также пропускаем служебные строки (разделители, блоки и т.д.).
    """
    with input_path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if "-" not in s:
                continue

            # Split by the last dash-like delimiter to be resilient to "e-sim" / "dual-usb-c".
            left, right = s.rsplit("-", 1)
            name = left.strip()
            price_raw = right.strip()

            # Skip lines that look like headings/bullets.
            if not re.search(r"\b(13|14|15|16)\b|16[еe]\b", name, flags=re.IGNORECASE):
                # Not iPhone 13-16 related.
                # Still yield if our mapping can find a key later.
                pass

            # Normalize common "missing" forms: keep raw, parse will return None.
            if not name:
                continue

            yield name, price_raw


def _iter_input_rows(input_path: Path, *, input_format: str) -> Iterator[tuple[str, Optional[str]]]:
    if input_format == "csv":
        yield from _iter_input_rows_csv(input_path)
        return
    if input_format == "text":
        yield from _iter_input_rows_text(input_path)
        return

    # auto: look for CSV delimiters in first non-empty line
    with input_path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if ";" in s or "," in s or "\t" in s:
                # assume CSV
                yield from _iter_input_rows_csv(input_path)
            else:
                yield from _iter_input_rows_text(input_path)
            return


def _iter_input_rows_text_from_string(input_text: str) -> Iterator[tuple[str, Optional[str]]]:
    for line in input_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if "-" not in s:
            continue

        left, right = s.rsplit("-", 1)
        name = left.strip()
        price_raw = right.strip()

        if not name:
            continue
        yield name, price_raw


def _iter_input_rows_csv_from_string(input_text: str) -> Iterator[tuple[str, Optional[str]]]:
    # Heuristic delimiter: prefer ';' if present.
    delimiter = ";"
    if "\t" in input_text:
        delimiter = "\t"
    elif "," in input_text and ";" not in input_text:
        delimiter = ","

    reader = csv.reader(StringIO(input_text), delimiter=delimiter)
    for row_idx, row in enumerate(reader, start=1):
        if not row:
            continue
        if len(row) < 2:
            continue
        name = row[0].strip()
        price_raw = row[1].strip()

        if row_idx == 1:
            # header detection
            if _try_parse_price_usd(price_raw) is None:
                continue
        yield name, price_raw


def _iter_input_rows_from_string(input_text: str, *, input_format: str) -> Iterator[tuple[str, Optional[str]]]:
    if input_format == "text":
        yield from _iter_input_rows_text_from_string(input_text)
        return
    if input_format == "csv":
        yield from _iter_input_rows_csv_from_string(input_text)
        return

    # auto detect
    stripped = input_text.strip()
    if not stripped:
        return
    first_line = stripped.splitlines()[0]
    if ";" in first_line or "," in first_line or "\t" in first_line:
        yield from _iter_input_rows_csv_from_string(input_text)
    else:
        yield from _iter_input_rows_text_from_string(input_text)


def process_iphone_13_16_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[IPhoneKey],
    base: dict[IPhoneKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
) -> str:
    rows = process_iphone_13_16_block(
        _iter_input_rows_from_string(input_text, input_format=input_format),
        base,
        base_order,
        sim_variant="1+1",
        markup_usd=markup_usd,
        usd_to_byn=usd_to_byn,
        missing_price_text=missing_price_text,
    )

    buf = StringIO()
    writer = csv.writer(buf, delimiter=delimiter_out)
    for line in rows:
        writer.writerow([line])
    return buf.getvalue()


def load_base(base_path: Path) -> tuple[list[IPhoneKey], dict[IPhoneKey, dict]]:
    data = json.loads(base_path.read_text(encoding="utf-8"))
    base_order: list[IPhoneKey] = []
    base: dict[IPhoneKey, dict] = {}
    for item in data:
        key = IPhoneKey(
            year=int(item["year"]),
            variant=str(item["variant"]),
            memory=str(item["memory"]),
            color=str(item["color"]),
        )
        base[key] = item
        base_order.append(key)
    return base_order, base


def process_iphone_13_16_block(
    input_rows: Iterable[tuple[str, Optional[str]]],
    base: dict[IPhoneKey, dict],
    base_order: list[IPhoneKey],
    *,
    sim_variant: str,
    markup_usd: Decimal,
    usd_to_byn: Decimal,
    missing_price_text: str,
) -> list[str]:
    """
    Возвращает строки для CSV/Telegram: 📱модель - **цена BYN**
    """
    best_numeric: dict[IPhoneKey, int] = {}
    has_numeric: set[IPhoneKey] = set()

    for name_raw, price_usd_raw in input_rows:
        if _is_blocked_country_flags(name_raw):
            continue
        if wholesale_line_skips_iphone_13_16_parsing(name_raw):
            continue

        key = _extract_year_variant_memory_color(name_raw)
        if key is None:
            continue

        if key not in base:
            # Device not present in our canonical iPhone 13-16 base.
            continue

        if price_usd_raw is None:
            continue
        price_usd = _try_parse_price_usd(price_usd_raw)
        if price_usd is None:
            continue

        price_byn = compute_final_price_byn(price_usd, markup_usd, usd_to_byn)

        has_numeric.add(key)
        prev = best_numeric.get(key)
        if prev is None or price_byn < prev:
            best_numeric[key] = price_byn

    output: list[str] = []
    for key in base_order:
        if key in has_numeric:
            output.append(
                _format_telegram_line_13_16(
                    key,
                    sim_variant,
                    price_byn=best_numeric[key],
                    missing_price_text=missing_price_text,
                )
            )
        else:
            output.append(
                _format_telegram_line_13_16(
                    key,
                    sim_variant,
                    price_byn=None,
                    missing_price_text=missing_price_text,
                )
            )
    return output


def write_output_csv(output_path: Path, lines: list[str], *, delimiter: str = ";") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        for line in lines:
            writer.writerow([line])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="iPhone 13-16 simple price processor -> Telegram CSV")
    parser.add_argument("--input", required=True, help="Input opтовый прайс CSV")
    parser.add_argument("--output", required=True, help="Output CSV for Telegram")
    parser.add_argument("--input-format", default="auto", choices=["auto", "csv", "text"], help="Input format")
    parser.add_argument("--usd-to-byn", required=True, help="Курс USD->BYN")
    parser.add_argument("--markup-usd", required=True, help="Наценка в долларах USD (прибавляется к оптовой цене)")
    parser.add_argument("--missing-price", default="по запросу", help="Текст для отсутствующих позиций")
    parser.add_argument("--delimiter-out", default=";", help="Делимитер для output CSV")
    parser.add_argument("--base", default=str(Path(__file__).parent / "data" / "apple_iphone_13_16_base.json"))
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    output_path = Path(args.output)
    usd_to_byn = _parse_price_usd(args.usd_to_byn)
    markup_usd = _parse_price_usd(args.markup_usd)

    base_order, base = load_base(Path(args.base))
    rows = process_iphone_13_16_block(
        _iter_input_rows(input_path, input_format=args.input_format),
        base,
        base_order,
        sim_variant="1+1",
        markup_usd=markup_usd,
        usd_to_byn=usd_to_byn,
        missing_price_text=args.missing_price,
    )
    write_output_csv(output_path, rows, delimiter=args.delimiter_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

