from __future__ import annotations

from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image
from pylibdmtx.pylibdmtx import encode as encode_datamatrix
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from models import LabelItem



LABEL_W = 58 * mm
LABEL_H = 40 * mm

# Coordinates from the user's table. X is from the left edge, Y is from the top edge.
TITLE_X = 18 * mm
TITLE_Y = -0.2 * mm
TITLE_W = 22 * mm
TITLE_H = 4.1 * mm
BARCODE_X = -9 * mm
BARCODE_Y = 4.4 * mm
BARCODE_W = 70.0 * mm
BARCODE_H = 12.3 * mm
BARCODE_NUMBER_X = 10 * mm
BARCODE_NUMBER_Y = 16.5 * mm
BARCODE_NUMBER_W = 20 * mm
BARCODE_NUMBER_H = 2.5 * mm
TOP_NUMBER_X = 50.0 * mm
TOP_NUMBER_Y = 5.8 * mm
TOP_NUMBER_W = 4 * mm
TOP_NUMBER_H = 5 * mm
PRODUCT_X = 2 * mm
PRODUCT_Y = 19.4 * mm
PRODUCT_W = 22 * mm
PRODUCT_H = 5 * mm

DM_W = 20.0 * mm
DM_H = 20.0 * mm

ARTICLE_LABEL_X = 2 * mm
ARTICLE_VALUE_X = 9 * mm
WB_LABEL_X = 2 * mm
WB_VALUE_X = 13 * mm
SIZE_LABEL_X = 2 * mm
SIZE_VALUE_X = 13 * mm
COLOR_LABEL_X = 2 * mm
COLOR_VALUE_X = 13 * mm
SUPPLIER_LABEL_X = 2 * mm
SUPPLIER_VALUE_X = 13 * mm
DM_X = 30.6 * mm
VERTICAL_X = 54.4 * mm
EAC_X = 20.1 * mm

ARTICLE_Y = 22 * mm
WB_Y = 25 * mm
SIZE_Y = 28 * mm
COLOR_Y = 31 * mm
SUPPLIER_Y = 33 * mm
DM_Y = 18.5 * mm
VERTICAL_Y = 18.6 * mm
EAC_Y = 33.7 * mm

ARTICLE_VALUE_W = 17 * mm
WB_VALUE_W = 15 * mm
SIZE_VALUE_W = 12 * mm
COLOR_VALUE_W = 16 * mm
SUPPLIER_VALUE_W = 15 * mm

VERTICAL_W = 3 * mm
EAC_W = 6 * mm


VERTICAL_H = 19.0 * mm
EAC_H = 4 * mm


FONT_REGULAR = "Arial"
FONT_BOLD = "Arial-Bold"


def create_labels_pdf(
    items: list[LabelItem],
    output_path: str | Path,
    include_mark_code: bool = True,
    info_page: dict[str, str] | None = None,
) -> Path:
    _register_fonts()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    canvas = Canvas(str(output_path), pagesize=landscape((LABEL_H, LABEL_W)))
    for item in items:
        _draw_label(canvas, item, include_mark_code=include_mark_code)
        canvas.showPage()
    if info_page:
        _draw_info_page(canvas, info_page)
        canvas.showPage()
    canvas.save()
    return output_path



def _draw_info_page(canvas: Canvas, info: dict[str, str]) -> None:
    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.25)
    canvas.rect(0.8 * mm, 0.8 * mm, LABEL_W - 1.6 * mm, LABEL_H - 1.6 * mm)

    canvas.setFont(FONT_BOLD, 10.8)
    canvas.drawCentredString(LABEL_W / 2, from_top(0.7 * mm, 4 * mm), "ИНФО ПАРТИИ")

    rows = [
        ("Дата", info.get("date", "")),
        ("Партия", info.get("batch", "")),
        ("КИЗ", info.get("count", "")),
        ("Бренд", info.get("brand", "")),
        ("Предмет", info.get("subject", "")),
        ("Артикул", info.get("seller_article", "")),
        ("WB арт", info.get("wb_article", "")),
        ("Размер", info.get("size", "")),
        ("Поставщик", info.get("supplier", "")),
    ]

    y = from_top(6.4 * mm, 2.2 * mm)
    for label, value in rows:
        is_main = label in ("Дата", "Партия")
        label_size = 6.0 if is_main else 5.0
        value_size = 7.0 if is_main else 5.5
        canvas.setFont(FONT_BOLD, label_size)
        canvas.drawString(2 * mm, y, f"{label}:")
        canvas.setFont(FONT_BOLD, value_size)
        canvas.drawString(18 * mm, y, _fit_text(value, 37 * mm, FONT_BOLD, value_size))
        y -= 3.75 * mm if is_main else 3.05 * mm

    canvas.setFont(FONT_BOLD, 3.5)
    canvas.drawRightString(LABEL_W - 2 * mm, 2.0 * mm, "Последняя страница не для товара")

def _draw_label(canvas: Canvas, item: LabelItem, include_mark_code: bool = True) -> None:
    product = item.product
    mark = item.mark_code

    canvas.setStrokeColor(colors.black)
    canvas.setLineWidth(0.25)
    canvas.rect(0.8 * mm, 0.8 * mm, LABEL_W - 1.6 * mm, LABEL_H - 1.6 * mm)

    _draw_centered_text(canvas, product.brand or "Timur Kids", TITLE_X + TITLE_W / 2, from_top(TITLE_Y, TITLE_H) - 0.2 * mm, 8.2, bold=True)

    if product.barcode:
        barcode = createBarcodeDrawing(
            "Code128",
            value=product.barcode,
            barHeight=BARCODE_H,
            humanReadable=False,
            width=BARCODE_W,
            height=BARCODE_H,
        )
        renderPDF.draw(barcode, canvas, BARCODE_X, from_top(BARCODE_Y, BARCODE_H))
        _draw_centered_text(canvas, product.barcode, BARCODE_NUMBER_X + BARCODE_NUMBER_W / 2, from_top(BARCODE_NUMBER_Y, BARCODE_NUMBER_H) + 0.1 * mm, 4.4)

    canvas.setFont(FONT_BOLD, 5.2)
    subject_lines = _wrap_text(product.subject, PRODUCT_W, FONT_BOLD, 5.2, max_lines=2)
    y = from_top(PRODUCT_Y, 1.7 * mm)
    for line in subject_lines:
        canvas.drawString(PRODUCT_X, y, line)
        y -= 2.2 * mm

    _draw_label_value(canvas, "Артикул:", product.seller_article, ARTICLE_LABEL_X, ARTICLE_VALUE_X, ARTICLE_Y, ARTICLE_VALUE_W)
    _draw_label_value(canvas, "Артикул WB:", product.wb_article, WB_LABEL_X, WB_VALUE_X, WB_Y, WB_VALUE_W)
    _draw_label_value(canvas, "Размер:", product.size, SIZE_LABEL_X, SIZE_VALUE_X, SIZE_Y, SIZE_VALUE_W)
    _draw_label_value(canvas, "Цвет:", product.color, COLOR_LABEL_X, COLOR_VALUE_X, COLOR_Y, COLOR_VALUE_W)
    _draw_label_value(canvas, "Поставщик:", product.supplier, SUPPLIER_LABEL_X, SUPPLIER_VALUE_X, SUPPLIER_Y, SUPPLIER_VALUE_W, label_size=3.8, value_size=4.5)

    canvas.setFont(FONT_BOLD, 8.2)
    canvas.drawCentredString(TOP_NUMBER_X + TOP_NUMBER_W / 2, from_top(TOP_NUMBER_Y, TOP_NUMBER_H) + 0.4 * mm, str(item.index))

    if include_mark_code and mark.raw:
        _draw_mark_code(canvas, mark.raw)
    else:
        _draw_eac(canvas, EAC_X, from_top(EAC_Y, EAC_H), 5.8)


def _draw_mark_code(canvas: Canvas, code: str) -> None:
    dm_y = from_top(DM_Y, DM_H)
    quiet = 1.0 * mm
    canvas.saveState()
    canvas.setFillColor(colors.white)
    canvas.setStrokeColor(colors.white)
    canvas.rect(DM_X - quiet, dm_y - quiet, DM_W + quiet * 2, DM_H + quiet * 2, fill=1, stroke=0)
    canvas.restoreState()

    dm_image = create_datamatrix_image(code)
    canvas.drawImage(ImageReader(dm_image), DM_X, dm_y, width=DM_W, height=DM_H, mask="auto")

    _draw_vertical_mark_text(canvas, code)

    _draw_eac(canvas, EAC_X, from_top(EAC_Y, EAC_H), 5.8)


def _draw_vertical_mark_text(canvas: Canvas, code: str) -> None:
    font_size = 2.0
    line_gap = 1.05 * mm
    lines = _split_text_to_width(str(code or ""), VERTICAL_H, FONT_REGULAR, font_size)
    canvas.saveState()
    canvas.translate(VERTICAL_X, from_top(VERTICAL_Y, VERTICAL_H))
    canvas.rotate(90)
    canvas.setFont(FONT_REGULAR, font_size)
    for index, line in enumerate(lines):
        canvas.drawString(0, -index * line_gap, line)
    canvas.restoreState()


def _split_text_to_width(text: str, max_width: float, font: str, size: float) -> list[str]:
    text = str(text or "")
    if not text:
        return [""]
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and stringWidth(candidate, font, size) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines

def create_datamatrix_image(code: str) -> Image.Image:
    payload = prepare_marking_code_for_datamatrix(code)
    encoded = encode_datamatrix(payload, scheme="Ascii", size="SquareAuto")
    image = Image.frombytes("RGB", (encoded.width, encoded.height), encoded.pixels)
    return image


def prepare_marking_code_for_datamatrix(code: str) -> bytes:
    # Keep ASCII 29 group separators if they exist in the source KIZ.
    # libdmtx expects bytes; utf-8 keeps digits and common Honest Sign symbols stable.
    normalized = str(code or "").replace("\\x1d", "\x1d").replace("<GS>", "\x1d")
    return normalized.encode("utf-8")

def _draw_eac(canvas: Canvas, x: float, y: float, size: float) -> None:
    canvas.setFont(FONT_BOLD, size)
    canvas.drawString(x, y, "EAC")


def _draw_label_value(
    canvas: Canvas,
    label: str,
    value: str,
    label_x: float,
    value_x: float,
    y_from_top: float,
    value_width: float,
    label_size: float = 4.2,
    value_size: float = 4.8,
) -> None:
    y = from_top(y_from_top, 1.7 * mm)
    canvas.setFont(FONT_REGULAR, label_size)
    canvas.drawString(label_x, y, label)
    canvas.setFont(FONT_BOLD, value_size)
    canvas.drawString(value_x, y, _fit_text(value, value_width, FONT_BOLD, value_size))


def from_top(y: float, height: float = 0) -> float:
    return LABEL_H - y - height


def _draw_centered_text(canvas: Canvas, text: str, x: float, y: float, size: float, bold: bool = False) -> None:
    font = FONT_BOLD if bold else FONT_REGULAR
    text = _fit_text(text, LABEL_W - 4 * mm, font, size)
    canvas.setFont(font, size)
    canvas.drawCentredString(x, y, text)


def _wrap_text(text: str, max_width: float, font: str, size: float, max_lines: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return [""]

    lines: list[str] = []
    current = ""
    used_words = 0
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, font, size) > max_width:
            lines.append(current)
            current = word
            if len(lines) == max_lines - 1:
                used_words += 1
                break
        else:
            current = candidate
            used_words += 1
    if current and len(lines) < max_lines:
        lines.append(current)
    if used_words < len(words) and lines:
        lines[-1] = _fit_text(lines[-1] + " " + " ".join(words[used_words:]), max_width, font, size)
    return lines[:max_lines]


def _fit_text(text: str, max_width: float, font: str, size: float) -> str:
    text = str(text or "")
    if stringWidth(text, font, size) <= max_width:
        return text
    ellipsis = "..."
    result = text
    while result and stringWidth(result + ellipsis, font, size) > max_width:
        result = result[:-1]
    return result + ellipsis if result else ""


def _register_fonts() -> None:
    if FONT_REGULAR in pdfmetrics.getRegisteredFontNames():
        return
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(regular)))
        pdfmetrics.registerFont(TTFont(FONT_BOLD, str(bold)))
        return
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, "DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, "DejaVuSans-Bold.ttf"))









