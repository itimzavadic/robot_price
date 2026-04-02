from __future__ import annotations

from decimal import Decimal

from iphone_processor import _iter_input_rows_from_string
from iphone_processor_all import DeviceKey, extract_device_key, process_iphone_all_from_text
from ipad_processor import IpadKey, _parse_ipad_name, process_ipad_from_text
from watch_processor import WatchKey, _parse_watch_name, process_watch_from_text

SECTION_SEP = "\n\n------------------------\n\n"


def _input_has_iphone_rows(input_text: str, input_format: str) -> bool:
    for name_raw, _pr in _iter_input_rows_from_string(input_text, input_format=input_format):
        if "📱" in name_raw or extract_device_key(name_raw) is not None:
            return True
    return False


def _input_has_watch_rows(input_text: str, input_format: str, watch_map: dict) -> bool:
    for name_raw, _pr in _iter_input_rows_from_string(input_text, input_format=input_format):
        if _parse_watch_name(name_raw, watch_map) is not None:
            return True
    return False


def _input_has_ipad_rows(input_text: str, input_format: str, ipad_map: dict) -> bool:
    for name_raw, _pr in _iter_input_rows_from_string(input_text, input_format=input_format):
        if _parse_ipad_name(name_raw, ipad_map) is not None:
            return True
    return False


def process_mixed_retail_from_text(
    input_text: str,
    *,
    input_format: str,
    base_iphone_order: list[DeviceKey],
    base_iphone: dict[DeviceKey, dict],
    watch_order: list[WatchKey],
    watch_map: dict[WatchKey, dict],
    ipad_order: list[IpadKey],
    ipad_map: dict[IpadKey, dict],
    usd_to_byn: Decimal,
    markup_usd_iphone: Decimal,
    markup_usd_watch: Decimal,
    markup_usd_ipad: Decimal,
    missing_price_text: str = "по запросу",
    delimiter_out: str = ";",
    include_cn_us_13_16: bool = False,
) -> str:
    """Смешанный опт: блоки iPhone / Watch / iPad по фактическим строкам; наценки раздельно."""
    has_iphone = _input_has_iphone_rows(input_text, input_format)
    has_watch = _input_has_watch_rows(input_text, input_format, watch_map)
    has_ipad = _input_has_ipad_rows(input_text, input_format, ipad_map)

    def _iphone() -> str:
        return process_iphone_all_from_text(
            input_text,
            input_format=input_format,
            base_order=base_iphone_order,
            base=base_iphone,
            usd_to_byn=usd_to_byn,
            markup_usd=markup_usd_iphone,
            missing_price_text=missing_price_text,
            delimiter_out=delimiter_out,
            include_cn_us_13_16=include_cn_us_13_16,
        )

    def _watch() -> str:
        return process_watch_from_text(
            input_text,
            input_format=input_format,
            base_order=watch_order,
            base=watch_map,
            usd_to_byn=usd_to_byn,
            markup_usd=markup_usd_watch,
            missing_price_text=missing_price_text,
            delimiter_out=delimiter_out,
        )

    def _ipad() -> str:
        return process_ipad_from_text(
            input_text,
            input_format=input_format,
            base_order=ipad_order,
            base=ipad_map,
            usd_to_byn=usd_to_byn,
            markup_usd=markup_usd_ipad,
            missing_price_text=missing_price_text,
            delimiter_out=delimiter_out,
        )

    blocks: list[str] = []
    if has_iphone:
        blocks.append(_iphone())
    if has_watch:
        blocks.append(_watch())
    if has_ipad:
        blocks.append(_ipad())

    if not blocks:
        blocks = [_iphone(), _watch(), _ipad()]

    trimmed = [b.strip("\n") for b in blocks]
    return SECTION_SEP.join(trimmed) + "\n"
