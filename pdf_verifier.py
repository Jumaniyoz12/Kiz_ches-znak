from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image
import zxingcpp

from pdf_generator import prepare_marking_code_for_datamatrix


def sampled_page_indexes(total_pages: int, max_pages: int | None = None) -> list[int]:
    if total_pages <= 0:
        return []
    if max_pages is None or max_pages <= 0 or max_pages >= total_pages:
        return list(range(total_pages))
    count = min(total_pages, max_pages)
    if count == 1:
        return [0]
    return sorted({round(index * (total_pages - 1) / (count - 1)) for index in range(count)})


def verify_pdf_datamatrix(
    pdf_path: str | Path,
    expected_codes: list[str],
    *,
    max_pages: int | None = None,
    dpi: int = 300,
) -> list[int]:
    """Render representative PDF pages and verify GS1 DataMatrix payloads.

    Every label page is checked by default. ``max_pages`` can be set to a
    positive limit for a faster representative sample. A mismatch or an
    unreadable symbol raises ValueError and prevents delivery.
    """
    path = Path(pdf_path)
    expected_payloads = [prepare_marking_code_for_datamatrix(code) for code in expected_codes]
    if not expected_payloads:
        return []

    document = pymupdf.open(path)
    try:
        if document.page_count < len(expected_payloads):
            raise ValueError(
                f"PDF содержит {document.page_count} стр., ожидалось минимум {len(expected_payloads)}"
            )

        checked = sampled_page_indexes(len(expected_payloads), max_pages=max_pages)
        scale = dpi / 72
        matrix = pymupdf.Matrix(scale, scale)

        for page_index in checked:
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
            result = zxingcpp.read_barcode(
                image,
                formats=zxingcpp.BarcodeFormat.DataMatrix,
                try_rotate=True,
                try_downscale=True,
            )
            page_number = page_index + 1
            if result is None:
                raise ValueError(f"DataMatrix не читается после рендера PDF, страница {page_number}")
            if result.symbology_identifier != "]d2" or result.content_type != zxingcpp.ContentType.GS1:
                raise ValueError(f"DataMatrix не распознан как GS1 (]d2), страница {page_number}")
            if result.bytes != expected_payloads[page_index]:
                raise ValueError(f"DataMatrix изменился после рендера PDF, страница {page_number}")

        return checked
    finally:
        document.close()
