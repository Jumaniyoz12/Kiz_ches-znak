from __future__ import annotations

import csv
from pathlib import Path

from models import LabelItem, Product
from validator import LocalCodeValidator, extract_gtin, normalize_gtin


FIELD_ALIASES = {
    "brand": ("Бренд", "brand"),
    "subject": ("Предмет", "Название товара", "Товар", "name"),
    "seller_article": ("Артикул продавца", "Артикул", "АРТИКУЛ", "article"),
    "wb_article": ("Артикул WB", "WB", "Номенклатура"),
    "size": ("Размер", "size"),
    "color": ("Цвет", "color"),
    "supplier": ("Поставщик", "Название поставщика", "supplier"),
    "barcode": ("Баркод", "Штрихкод", "barcode", "bar_code"),
    "gtin": ("GTIN", "ГТИН", "gtin"),
    "composition": ("Состав", "composition"),
}


def read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [{key: (value or "").strip() for key, value in row.items()} for row in reader if any(row.values())]


def read_products(path: str | Path) -> list[Product]:
    rows = read_csv_rows(path)
    return [_row_to_product(row) for row in rows]


def read_codes(path: str | Path) -> list[str]:
    path = Path(path)
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def merge_products(nomenclature_rows: list[dict[str, str]], gtin_rows: list[dict[str, str]]) -> list[Product]:
    gtin_by_barcode: dict[str, str] = {}
    color_by_barcode: dict[str, str] = {}
    for row in gtin_rows:
        barcode = only_digits(_get(row, "barcode"))
        gtin = normalize_gtin(_get(row, "gtin"))
        gtin_article = _get(row, "seller_article")
        if barcode and gtin:
            gtin_by_barcode[barcode] = gtin
        if barcode:
            color = _extract_color(gtin_article)
            if color:
                color_by_barcode[barcode] = color

    products: list[Product] = []
    for row in nomenclature_rows:
        barcode = only_digits(_get(row, "barcode"))
        if not barcode:
            continue
        product = _row_to_product(row)
        products.append(
            Product(
                brand=product.brand,
                subject=product.subject,
                seller_article=product.seller_article,
                wb_article=product.wb_article,
                size=product.size,
                color=product.color or color_by_barcode.get(barcode, ""),
                supplier=product.supplier or "ОсОО ТКЛ",
                barcode=barcode,
                gtin=gtin_by_barcode.get(barcode, ""),
                composition=product.composition,
            )
        )
    return products


def build_label_items(products: list[Product], codes: list[str], validator: LocalCodeValidator) -> list[LabelItem]:
    product_by_gtin = {normalize_gtin(product.gtin): product for product in products if product.gtin}
    items: list[LabelItem] = []
    unused_products = iter(products)

    for index, code in enumerate(codes, start=1):
        code_gtin = normalize_gtin(extract_gtin(code))
        product = product_by_gtin.get(code_gtin) if code_gtin else None
        if product is None:
            product = next(unused_products, None)
        if product is None:
            raise ValueError(f"Для кода #{index} не найден товар")
        mark_code = validator.check_code(code, expected_gtin=product.gtin)
        items.append(LabelItem(product=product, mark_code=mark_code, index=index))

    return items


def _row_to_product(row: dict[str, str]) -> Product:
    return Product(
        brand=_get(row, "brand"),
        subject=_get(row, "subject"),
        seller_article=_get(row, "seller_article"),
        wb_article=only_digits(_get(row, "wb_article")),
        size=_get(row, "size"),
        color=_extract_color(_get(row, "seller_article")) or _get(row, "color"),
        supplier=_get(row, "supplier"),
        barcode=only_digits(_get(row, "barcode")),
        gtin=normalize_gtin(_get(row, "gtin")),
        composition=_get(row, "composition"),
    )


def _get(row: dict[str, str], field: str) -> str:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            return str(row[alias]).strip()
    return ""


def _extract_color(article: str) -> str:
    marker = "ц:"
    if marker not in article:
        return ""
    color = article.split(marker, 1)[1]
    for separator in (";", ", р:", ",р:", " р:"):
        color = color.split(separator, 1)[0]
    return color.strip()


def only_digits(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())



