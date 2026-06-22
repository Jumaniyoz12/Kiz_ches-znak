from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    brand: str
    subject: str
    seller_article: str
    wb_article: str
    size: str
    color: str
    supplier: str
    barcode: str
    gtin: str = ""
    composition: str = ""


@dataclass(frozen=True)
class MarkCode:
    raw: str
    gtin: str | None
    is_valid: bool
    message: str


@dataclass(frozen=True)
class LabelItem:
    product: Product
    mark_code: MarkCode
    index: int

