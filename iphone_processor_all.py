from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Iterable, Iterator, Optional

import iphone_processor as base_proc
from price_merge import merge_min_byn


@dataclass(frozen=True)
class DeviceKey:
    # family: "iphone" (years 13-17), "air" (iPhone Air)
    family: str
    year: int  # for air: 0
    variant: str  # "", "Plus", "Pro", "Pro Max", "e"
    memory: str  # "128" | "256" | "512" | "1TB" | ...
    color: str  # canonical color
    sim_variant: str  # "1+1" | "eSim" | "dual"


IPHONE_ICON = "📱"


def _sim_display_label(sim_variant: str) -> str:
    """Подпись SIM в рознице: без флагов стран."""
    if sim_variant == "dual":
        return "Dual"
    if sim_variant == "eSim":
        return "eSim"
    return "1+1"


def _format_model_line(key: DeviceKey, *, show_13_16_sim_labels: bool = False) -> str:
    """Строка модели без слова iPhone: префикс 📱.

    iPhone 13–16: при выключенном доп. фильтре тип SIM не показываем (стандарт 1+1).
    При включённом — показываем 1+1 / eSim / Dual (без флагов стран).
    Air и iPhone 17 — тип SIM в названии как раньше.
    """
    if key.family == "air":
        core = f"Air {key.memory} {key.color} {key.sim_variant}"
        return f"{IPHONE_ICON}{core}"

    if key.family == "iphone" and key.year == 17:
        sim_tail = f" {_sim_display_label(key.sim_variant)}"
        if key.variant == "":
            core = f"{key.year} {key.memory} {key.color}{sim_tail}"
        elif key.variant == "e":
            core = f"{key.year}e {key.memory} {key.color}{sim_tail}"
        elif key.variant == "Plus":
            core = f"{key.year} Plus {key.memory} {key.color}{sim_tail}"
        elif key.variant == "Pro Max":
            core = f"{key.year} Pro Max {key.memory} {key.color}{sim_tail}"
        elif key.variant == "Pro":
            core = f"{key.year} Pro {key.memory} {key.color}{sim_tail}"
        else:
            core = f"{key.year} {key.variant} {key.memory} {key.color}{sim_tail}"
        return f"{IPHONE_ICON}{core}"

    # iPhone 13–16
    hide_sim = not show_13_16_sim_labels
    sim_tail = "" if hide_sim else f" {_sim_display_label(key.sim_variant)}"
    if key.variant == "":
        core = f"{key.year} {key.memory} {key.color}{sim_tail}"
    elif key.variant == "Plus":
        core = f"{key.year} Plus {key.memory} {key.color}{sim_tail}"
    elif key.variant == "Pro":
        core = f"{key.year} Pro {key.memory} {key.color}{sim_tail}"
    elif key.variant == "Pro Max":
        core = f"{key.year} Pro Max {key.memory} {key.color}{sim_tail}"
    elif key.variant == "e":
        core = f"{key.year}e {key.memory} {key.color}{sim_tail}"
    else:
        core = f"{key.year} {key.variant} {key.memory} {key.color}{sim_tail}"
    return f"{IPHONE_ICON}{core}"


def _format_retail_site_model_line(key: DeviceKey) -> str:
    """Строка для сайта: iPhone 17 или Air — без типа SIM (1+1 / eSim / Dual)."""
    if key.family == "air":
        return f"{IPHONE_ICON}Air {key.memory} {key.color}"
    if key.family == "iphone" and key.year == 17:
        if key.variant == "":
            core = f"{key.year} {key.memory} {key.color}"
        elif key.variant == "e":
            core = f"{key.year}e {key.memory} {key.color}"
        elif key.variant == "Plus":
            core = f"{key.year} Plus {key.memory} {key.color}"
        elif key.variant == "Pro Max":
            core = f"{key.year} Pro Max {key.memory} {key.color}"
        elif key.variant == "Pro":
            core = f"{key.year} Pro {key.memory} {key.color}"
        else:
            core = f"{key.year} {key.variant} {key.memory} {key.color}"
        return f"{IPHONE_ICON}{core}"
    raise ValueError("ожидается iPhone 17 или iPhone Air")


def _format_retail_site_price_line(key: DeviceKey, *, price_byn: int) -> str:
    """Одна строка списка для сайта: модель без SIM — цена BYN (как в Telegram-разметке)."""
    model = _format_retail_site_model_line(key)
    return f"{model} - **{price_byn} BYN**"


def _retail_site_sort_key(key: DeviceKey) -> tuple:
    """Сначала линейка 17 (17e → 17 → Pro → Pro Max), затем Air; внутри — память → цвет."""
    if key.family == "air":
        return (1, 0, _memory_rank(key.memory), key.color.lower())
    if key.family == "iphone" and key.year == 17:
        return (0, _variant_rank_device(key), _memory_rank(key.memory), key.color.lower())
    raise ValueError("ожидается iPhone 17 или iPhone Air")


def _format_telegram_line(
    key: DeviceKey,
    *,
    price_byn: Optional[int],
    missing_price_text: str,
    show_13_16_sim_labels: bool = False,
) -> str:
    """Одна строка для Telegram: модель - **цена BYN** (жирный через Markdown)."""
    model = _format_model_line(key, show_13_16_sim_labels=show_13_16_sim_labels)
    if price_byn is not None:
        return f"{model} - **{price_byn} BYN**"
    return f"{model} - **{missing_price_text}**"


def _iphone_13_16_base_key(ik) -> DeviceKey:
    return DeviceKey(
        family="iphone",
        year=ik.year,
        variant=ik.variant,
        memory=ik.memory,
        color=ik.color,
        sim_variant="1+1",
    )


def _update_best_price(
    best_numeric: dict[DeviceKey, int],
    has_numeric: set[DeviceKey],
    key: DeviceKey,
    price_byn: int,
) -> None:
    has_numeric.add(key)
    prev = best_numeric.get(key)
    if prev is None or price_byn < prev:
        best_numeric[key] = price_byn


# 13–15: без линейки «e».
_VARIANT_ORDER_13_15 = ("", "Plus", "Pro", "Pro Max")
# 16: сначала 16e, затем обычный 16, потом Plus / Pro / Pro Max.
_VARIANT_ORDER_16 = ("e", "", "Plus", "Pro", "Pro Max")
# 17: сначала 17e, затем обычный 17, потом Pro / Pro Max.
_VARIANT_ORDER_17 = ("e", "", "Plus", "Pro", "Pro Max")
_MEMORY_ORDER = ("128", "256", "512", "1TB", "2TB")
_SIM_ORDER = ("1+1", "eSim", "dual")


def _variant_rank_device(key: DeviceKey) -> int:
    if key.family != "iphone":
        return 0
    if key.year == 17:
        order = _VARIANT_ORDER_17
    elif key.year == 16:
        order = _VARIANT_ORDER_16
    else:
        order = _VARIANT_ORDER_13_15
    try:
        return order.index(key.variant)
    except ValueError:
        return 999


def _memory_rank(m: str) -> int:
    try:
        return _MEMORY_ORDER.index(m)
    except ValueError:
        return 999


def _sim_rank(s: str) -> int:
    try:
        return _SIM_ORDER.index(s)
    except ValueError:
        return 999


def _retail_sort_key(key: DeviceKey) -> tuple:
    """Сортировка розницы. Air в конце.

    iPhone 17: поколение → вариант → память → цвет → SIM (1+1 / eSim / Dual рядом по цвету).
    Остальные iPhone: поколение → вариант → память → SIM → цвет.
    """
    if key.family == "air":
        return (2, 0, 0, _memory_rank(key.memory), _sim_rank(key.sim_variant), key.color.lower())
    if key.family == "iphone" and key.year == 17:
        return (
            0,
            key.year,
            _variant_rank_device(key),
            _memory_rank(key.memory),
            key.color.lower(),
            _sim_rank(key.sim_variant),
        )
    return (
        0,
        key.year,
        _variant_rank_device(key),
        _memory_rank(key.memory),
        _sim_rank(key.sim_variant),
        key.color.lower(),
    )


def _model_group_for_separator(key: DeviceKey) -> tuple:
    """Группа «модели» для линии-разделителя из тире (год + вариант; Air отдельно)."""
    if key.family == "air":
        return ("air",)
    return ("iphone", key.year, key.variant)


def _inject_retail_separators(sorted_pairs: list[tuple[DeviceKey, str]]) -> list[str]:
    """Между группами моделей — строка тире; между разными объёмами памяти внутри группы — пустая строка."""
    out: list[str] = []
    prev_group: Optional[tuple] = None
    prev_memory: Optional[str] = None
    dash_line = "------------------------"
    for key, line in sorted_pairs:
        g = _model_group_for_separator(key)
        if prev_group is not None and g != prev_group:
            out.append(dash_line)
            prev_memory = None
        elif prev_group == g and prev_memory is not None and key.memory != prev_memory:
            out.append("")
        out.append(line)
        prev_group = g
        prev_memory = key.memory
    return out


def _csv_one_cell_row(value: str, delimiter: str) -> str:
    """Одна строка CSV с одной ячейкой (без лишних кавычек для пустой строки)."""
    buf = StringIO()
    csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow([value])
    return buf.getvalue().rstrip("\n")


def _parse_text_rows(input_text: str) -> Iterator[tuple[str, Optional[str]]]:
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


def _parse_csv_rows(input_text: str, *, delimiter: str = ";") -> Iterator[tuple[str, Optional[str]]]:
    reader = csv.reader(StringIO(input_text), delimiter=delimiter)
    for idx, row in enumerate(reader, start=1):
        if not row or len(row) < 2:
            continue
        name = row[0].strip()
        price_raw = row[1].strip()
        if idx == 1:
            if base_proc._try_parse_price_usd(price_raw) is None:  # skip header
                continue
        yield name, price_raw


def _extract_iphone13_16_key(name_raw: str) -> Optional[DeviceKey]:
    key13 = base_proc._extract_year_variant_memory_color(name_raw)
    if key13 is None:
        return None
    if key13.year not in (13, 14, 15, 16):
        return None
    return DeviceKey(
        family="iphone",
        year=key13.year,
        variant=key13.variant,
        memory=key13.memory,
        color=key13.color,
        sim_variant="1+1",
    )


def _extract_air_key(name_raw: str) -> Optional[DeviceKey]:
    s = base_proc._normalize_text(name_raw)
    if not re.search(r"\bair\b", s, flags=re.IGNORECASE):
        return None

    lowered = s.lower()
    # iPad Air в опте: «Air 11/13 … M…», не iPhone Air
    if re.search(r"\bipad\b", lowered):
        return None
    if re.search(r"\bair\s+(11|13)\s+m\d+", lowered):
        return None
    memory = None
    if re.search(r"\b2\s*tb\b|\b2tb\b", lowered):
        memory = "2TB"
    elif re.search(r"\b1\s*tb\b|\b1tb\b", lowered):
        memory = "1TB"
    elif re.search(r"\b512\b", lowered):
        memory = "512"
    elif re.search(r"\b256\b", lowered):
        memory = "256"
    if memory is None:
        return None

    canonical_colors = {"black", "white", "blue", "gold"}
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", s)
    color_raw = None
    for t in tokens:
        tl = t.lower()
        if tl in canonical_colors:
            color_raw = tl
            break
    if color_raw is None:
        return None

    color_title = color_raw[:1].upper() + color_raw[1:]
    return DeviceKey(family="air", year=0, variant="", memory=memory, color=color_title, sim_variant="eSim")


def _extract_iphone17_key(name_raw: str) -> Optional[DeviceKey]:
    s = base_proc._normalize_text(name_raw)
    lowered = s.lower()

    is_17e = bool(re.search(r"\b17[еe]\b", lowered) or re.search(r"\b17\s+e\b", lowered))
    is_17 = bool(re.search(r"\b17\b", lowered))
    if not is_17e and not is_17:
        return None

    # Опт часто пишет «17 Air …» вместо iPhone Air — это не линейка 17.
    if re.search(r"\b17\s+air\b", lowered):
        return None

    # Variant (17e раньше Pro, чтобы не перепутать с «17 Pro»)
    variant = ""
    if is_17e:
        variant = "e"
    elif re.search(r"\bpro\s*max\b", lowered):
        variant = "Pro Max"
    elif re.search(r"\bpro\b", lowered):
        variant = "Pro"
    elif re.search(r"\bplus\b", lowered):
        variant = "Plus"

    # Memory
    memory = None
    if re.search(r"\b2\s*tb\b|\b2tb\b", lowered):
        memory = "2TB"
    elif re.search(r"\b1\s*tb\b|\b1tb\b", lowered):
        memory = "1TB"
    elif re.search(r"\b512\b", lowered):
        memory = "512"
    elif re.search(r"\b256\b", lowered):
        memory = "256"
    if memory is None:
        return None

    # Color
    canonical_colors = {"black", "blue", "lavender", "sage", "white", "orange", "silver", "pink"}
    tokens = re.findall(r"[a-zA-Zа-яА-Я0-9]+", s)
    color_raw = None
    for t in tokens:
        tl = t.lower()
        if tl in canonical_colors:
            color_raw = tl
            break
    if color_raw is None:
        return None

    color_title = color_raw[:1].upper() + color_raw[1:]

    # SIM variant
    sim_variant = None
    if re.search(r"\b2\s*sim\b|\b2sim\b", lowered) or "2sim" in lowered:
        sim_variant = "dual"
    elif re.search(r"\b1\s*\+\s*1\b|1\+1", lowered):
        sim_variant = "1+1"
    elif re.search(r"sim\s*\+\s*e", lowered) or re.search(r"sim\s*\+\s*e-?sim", lowered) or "sim+esim" in lowered.replace(" ", ""):
        sim_variant = "1+1"
    elif re.search(r"e-?sim", lowered) or "esim" in lowered:
        sim_variant = "eSim"
    elif re.search(r"\bdual\b", lowered):
        sim_variant = "dual"
    elif re.search(r"\bsim\b", lowered):
        sim_variant = "dual"

    if sim_variant is None:
        return None

    return DeviceKey(family="iphone", year=17, variant=variant, memory=memory, color=color_title, sim_variant=sim_variant)


def extract_device_key(name_raw: str) -> Optional[DeviceKey]:
    # См. wholesale_line_skips_iphone_13_16_parsing — та же логика, что и в process_iphone_all.
    if base_proc.wholesale_line_skips_iphone_13_16_parsing(name_raw):
        return None
    # Priority matters: "Air" also contains "iPhone" sometimes, etc.
    key = _extract_iphone17_key(name_raw)
    if key is not None:
        return key
    key = _extract_air_key(name_raw)
    if key is not None:
        return key
    key = _extract_iphone13_16_key(name_raw)
    if key is not None:
        return key
    return None


def load_all_iphone_base(
    *,
    base_13_16_path: Path,
    base_air_path: Path,
    base_17_path: Path,
) -> tuple[list[DeviceKey], dict[DeviceKey, dict]]:
    base_order: list[DeviceKey] = []
    base_map: dict[DeviceKey, dict] = {}

    # iPhone 13-16: existing json has {year, variant, memory, color}, no sim_variant.
    data13 = json.loads(base_13_16_path.read_text(encoding="utf-8"))
    for item in data13:
        key = DeviceKey(
            family="iphone",
            year=int(item["year"]),
            variant=str(item["variant"]),
            memory=str(item["memory"]),
            color=str(item["color"]),
            sim_variant="1+1",
        )
        base_order.append(key)
        base_map[key] = item

    data_air = json.loads(base_air_path.read_text(encoding="utf-8"))
    for item in data_air:
        key = DeviceKey(
            family=str(item["family"]),
            year=int(item["year"]),
            variant=str(item.get("variant", "")),
            memory=str(item["memory"]),
            color=str(item["color"]),
            sim_variant=str(item["sim_variant"]),
        )
        base_order.append(key)
        base_map[key] = item

    data17 = json.loads(base_17_path.read_text(encoding="utf-8"))
    for item in data17:
        key = DeviceKey(
            family=str(item["family"]),
            year=int(item["year"]),
            variant=str(item.get("variant", "")),
            memory=str(item["memory"]),
            color=str(item["color"]),
            sim_variant=str(item["sim_variant"]),
        )
        base_order.append(key)
        base_map[key] = item

    return base_order, base_map


def collect_iphone_all_best_byn_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[DeviceKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    include_cn_us_13_16: bool = False,
) -> tuple[dict[DeviceKey, int], set[DeviceKey]]:
    best_numeric: dict[DeviceKey, int] = {}
    has_numeric: set[DeviceKey] = set()

    if input_format == "csv":
        rows = _parse_csv_rows(input_text, delimiter=";")
    else:
        rows = _parse_text_rows(input_text)

    for name_raw, price_raw in rows:
        if price_raw is None:
            continue

        if base_proc.wholesale_line_skips_all_iphone_row_processing(name_raw):
            continue

        price_usd = base_proc._try_parse_price_usd(price_raw)
        if price_usd is None:
            continue

        price_byn = base_proc.compute_final_price_byn(price_usd, markup_usd, usd_to_byn)

        k17 = _extract_iphone17_key(name_raw)
        if k17 is not None:
            if k17 not in base:
                continue
            _update_best_price(best_numeric, has_numeric, k17, price_byn)
            continue

        ka = _extract_air_key(name_raw)
        if ka is not None:
            if ka not in base:
                continue
            _update_best_price(best_numeric, has_numeric, ka, price_byn)
            continue

        if base_proc.wholesale_line_skips_iphone_13_16_parsing(name_raw):
            continue

        ik = base_proc._extract_year_variant_memory_color(name_raw)
        if ik is None or ik.year not in (13, 14, 15, 16):
            continue

        core_key = _iphone_13_16_base_key(ik)
        if core_key not in base:
            continue

        blocked = base_proc._is_blocked_country_flags(name_raw)
        low = name_raw.lower()
        has_dual = bool(re.search(r"\bdual\b", low))
        has_esim = bool(re.search(r"e\s*-?\s*sim|\besim\b", low))

        if include_cn_us_13_16:
            if not blocked:
                k11 = DeviceKey(
                    family="iphone",
                    year=ik.year,
                    variant=ik.variant,
                    memory=ik.memory,
                    color=ik.color,
                    sim_variant="1+1",
                )
                _update_best_price(best_numeric, has_numeric, k11, price_byn)
            else:
                if has_dual:
                    kd = DeviceKey(
                        family="iphone",
                        year=ik.year,
                        variant=ik.variant,
                        memory=ik.memory,
                        color=ik.color,
                        sim_variant="dual",
                    )
                    _update_best_price(best_numeric, has_numeric, kd, price_byn)
                elif has_esim:
                    ke = DeviceKey(
                        family="iphone",
                        year=ik.year,
                        variant=ik.variant,
                        memory=ik.memory,
                        color=ik.color,
                        sim_variant="eSim",
                    )
                    _update_best_price(best_numeric, has_numeric, ke, price_byn)
        else:
            if blocked:
                continue
            _update_best_price(best_numeric, has_numeric, core_key, price_byn)

    return best_numeric, has_numeric


def format_iphone_all_to_csv(
    best_numeric: dict[DeviceKey, int],
    has_numeric: set[DeviceKey],
    *,
    base_order: list[DeviceKey],
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
    include_cn_us_13_16: bool = False,
) -> str:
    pairs: list[tuple[DeviceKey, str]] = []
    for key in base_order:
        if key.family == "iphone" and 13 <= key.year <= 16 and include_cn_us_13_16:
            k11 = DeviceKey(
                family="iphone",
                year=key.year,
                variant=key.variant,
                memory=key.memory,
                color=key.color,
                sim_variant="1+1",
            )
            if k11 in has_numeric:
                pairs.append(
                    (
                        k11,
                        _format_telegram_line(
                            k11,
                            price_byn=best_numeric[k11],
                            missing_price_text=missing_price_text,
                            show_13_16_sim_labels=True,
                        ),
                    )
                )
            else:
                pairs.append(
                    (
                        k11,
                        _format_telegram_line(
                            k11,
                            price_byn=None,
                            missing_price_text=missing_price_text,
                            show_13_16_sim_labels=True,
                        ),
                    )
                )

            ke = DeviceKey(
                family="iphone",
                year=key.year,
                variant=key.variant,
                memory=key.memory,
                color=key.color,
                sim_variant="eSim",
            )
            if ke in has_numeric:
                pairs.append(
                    (
                        ke,
                        _format_telegram_line(
                            ke,
                            price_byn=best_numeric[ke],
                            missing_price_text=missing_price_text,
                            show_13_16_sim_labels=True,
                        ),
                    )
                )

            kd = DeviceKey(
                family="iphone",
                year=key.year,
                variant=key.variant,
                memory=key.memory,
                color=key.color,
                sim_variant="dual",
            )
            if kd in has_numeric:
                pairs.append(
                    (
                        kd,
                        _format_telegram_line(
                            kd,
                            price_byn=best_numeric[kd],
                            missing_price_text=missing_price_text,
                            show_13_16_sim_labels=True,
                        ),
                    )
                )
            continue

        if key in has_numeric:
            line = _format_telegram_line(
                key,
                price_byn=best_numeric[key],
                missing_price_text=missing_price_text,
                show_13_16_sim_labels=False,
            )
        else:
            line = _format_telegram_line(
                key,
                price_byn=None,
                missing_price_text=missing_price_text,
                show_13_16_sim_labels=False,
            )
        pairs.append((key, line))

    pairs.sort(key=lambda p: _retail_sort_key(p[0]))
    lines = _inject_retail_separators(pairs)
    rows: list[str] = []
    for L in lines:
        if L == "":
            rows.append("")
        else:
            rows.append(_csv_one_cell_row(L, delimiter_out))
    return "\n".join(rows) + ("\n" if rows else "")


def process_iphone_all_from_text(
    input_text: str,
    *,
    input_format: str,
    base_order: list[DeviceKey],
    base: dict[DeviceKey, dict],
    usd_to_byn: Decimal,
    markup_usd: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
    include_cn_us_13_16: bool = False,
) -> str:
    best, has_n = collect_iphone_all_best_byn_from_text(
        input_text,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd,
        include_cn_us_13_16=include_cn_us_13_16,
    )
    return format_iphone_all_to_csv(
        best,
        has_n,
        base_order=base_order,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
        include_cn_us_13_16=include_cn_us_13_16,
    )


def merge_iphone_all_from_texts(
    raw_a: str,
    raw_b: str,
    *,
    input_format: str,
    base_order: list[DeviceKey],
    base: dict[DeviceKey, dict],
    usd_to_byn: Decimal,
    markup_usd_a: Decimal,
    markup_usd_b: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
    include_cn_us_13_16: bool = False,
) -> str:
    ba, _ = collect_iphone_all_best_byn_from_text(
        raw_a,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd_a,
        include_cn_us_13_16=include_cn_us_13_16,
    )
    bb, _ = collect_iphone_all_best_byn_from_text(
        raw_b,
        input_format=input_format,
        base=base,
        usd_to_byn=usd_to_byn,
        markup_usd=markup_usd_b,
        include_cn_us_13_16=include_cn_us_13_16,
    )
    merged = merge_min_byn(ba, bb)
    has_m = set(merged.keys())
    return format_iphone_all_to_csv(
        merged,
        has_m,
        base_order=base_order,
        missing_price_text=missing_price_text,
        delimiter_out=delimiter_out,
        include_cn_us_13_16=include_cn_us_13_16,
    )


def collect_retail_site_min_by_group(
    input_text: str,
    *,
    input_format: str,
    base: dict[DeviceKey, dict],
) -> dict[tuple, tuple[int, DeviceKey]]:
    """Розничный прайс (BYN): iPhone 17 и iPhone Air. Группа — минимальная BYN среди вариантов SIM.

    Ключ группы: (\"17\", variant, memory, color) или (\"air\", memory, color).
    """
    best: dict[tuple, tuple[int, DeviceKey]] = {}

    rows = base_proc._iter_input_rows_from_string(input_text, input_format=input_format)

    for name_raw, price_raw in rows:
        if price_raw is None:
            continue
        if base_proc.wholesale_line_skips_all_iphone_row_processing(name_raw):
            continue
        price_byn = base_proc._try_parse_price_byn(price_raw)
        if price_byn is None:
            continue

        k = _extract_iphone17_key(name_raw)
        if k is not None and k in base:
            g: tuple = ("17", k.variant, k.memory, k.color)
        else:
            ka = _extract_air_key(name_raw)
            if ka is None or ka not in base:
                continue
            k = ka
            g = ("air", k.memory, k.color)

        prev = best.get(g)
        if prev is None or price_byn < prev[0]:
            best[g] = (price_byn, k)

    return best


def process_iphone17_site_from_text(
    input_text: str,
    *,
    input_format: str,
    base: dict[DeviceKey, dict],
    delimiter_out: str = ";",
) -> str:
    """Список для сайта: розничные BYN без пересчёта; min на память+цвет внутри линейки; iPhone 17 + Air; без SIM в названии."""
    groups = collect_retail_site_min_by_group(
        input_text,
        input_format=input_format,
        base=base,
    )
    if not groups:
        return ""

    winners: list[tuple[DeviceKey, int]] = []
    for _g, (price_byn, key) in groups.items():
        winners.append((key, price_byn))
    winners.sort(key=lambda t: _retail_site_sort_key(t[0]))

    pairs: list[tuple[DeviceKey, str]] = [
        (key, _format_retail_site_price_line(key, price_byn=price_byn)) for key, price_byn in winners
    ]
    lines = _inject_retail_separators(pairs)
    rows: list[str] = []
    for L in lines:
        if L == "":
            rows.append("")
        else:
            rows.append(_csv_one_cell_row(L, delimiter_out))
    return "\n".join(rows) + ("\n" if rows else "")

