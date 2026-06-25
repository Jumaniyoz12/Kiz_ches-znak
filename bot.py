from __future__ import annotations

import csv
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path



from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from data import build_label_items, merge_products, only_digits
from google_sheets import read_google_sheet_csv
from pdf_generator import create_labels_pdf
from models import LabelItem, MarkCode
from validator import LocalCodeValidator


NOMENCLATURE_SHEET = "Отчёт с перечнем номенклатур"
GTIN_SHEET = "GTIN"
CODE_HEADERS = ("киз", "код", "код маркировки", "честный знак", "datamatrix", "data matrix", "mark_code")
CODE_START_RE = re.compile(r"(?=01\d{14})")
CACHE_TTL_SECONDS = 600
PRODUCTS_CACHE: dict[tuple[str, str, str], tuple[float, list]] = {}
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Проанализировать"],
        ["💳 Баланс - Оплата"],
        ["❓ Команды", "↻ Сброс"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text(
        "Бот готов. Данные товара беру из Google Sheets.\n\n"
        "Для больших партий нажмите 📊 Проанализировать и отправьте файл .txt или .csv с КИЗами.\n"
        "Для штрихкодов без ЧЗ используйте: /wb 705719577 или /art АртикулПродавца.",
        reply_markup=MAIN_KEYBOARD,
    )


async def set_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("После /sheet вставьте ссылку на Google Sheets.")
        return
    context.user_data["sheet_url"] = context.args[0]
    PRODUCTS_CACHE.clear()
    await update.message.reply_text("Google-файл сохранен. Теперь отправьте файл .txt/.csv с КИЗами или коды сообщением.", reply_markup=MAIN_KEYBOARD)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    document = update.message.document
    file_name = document.file_name or "codes.txt"
    suffix = Path(file_name).suffix.lower()
    if suffix not in (".txt", ".csv"):
        await update.message.reply_text("Пришлите КИЗы файлом .txt или .csv.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="labelbot_"))
    codes_path = temp_dir / file_name
    telegram_file = await document.get_file()
    await telegram_file.download_to_drive(codes_path)

    try:
        codes = read_codes_from_file(codes_path)
    except Exception as exc:
        await update.message.reply_text(f"Не смог прочитать файл с КИЗами: {exc}")
        return

    await make_pdf(update, context, codes)


async def handle_wb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        context.user_data["awaiting_barcode_mode"] = "wb"
        await update.message.reply_text("Отправьте Артикул WB, например: 705719577", reply_markup=MAIN_KEYBOARD)
        return
    await make_barcode_pdf(update, context, query, mode="wb")


async def handle_art(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip()
    if not query:
        context.user_data["awaiting_barcode_mode"] = "art"
        await update.message.reply_text("Отправьте Артикул продавца, например: ДвоСпортЧерный-01", reply_markup=MAIN_KEYBOARD)
        return
    await make_barcode_pdf(update, context, query, mode="art")


async def handle_codes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await handle_menu_button(update, context):
        return

    text = update.message.text.strip()
    awaiting_mode = context.user_data.pop("awaiting_barcode_mode", None)
    if awaiting_mode:
        await make_barcode_pdf(update, context, text, mode=awaiting_mode)
        return

    command = parse_barcode_request(text)
    if command:
        mode, query = command
        if not query:
            context.user_data["awaiting_barcode_mode"] = mode
            prompt = "Отправьте Артикул WB." if mode == "wb" else "Отправьте Артикул продавца."
            await update.message.reply_text(prompt, reply_markup=MAIN_KEYBOARD)
            return
        await make_barcode_pdf(update, context, query, mode=mode)
        return

    codes = extract_marking_codes(text)
    if codes:
        await make_pdf(update, context, codes)
        return

    if looks_like_wb_article(text):
        await make_barcode_pdf(update, context, text, mode="wb")
        return

    await make_barcode_pdf(update, context, text, mode="art")

async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = update.message.text.strip()

    if text == "📊 Проанализировать":
        await update.message.reply_text(
            "Отправьте файл .csv или .txt с КИЗами. Один КИЗ = одна этикетка.",
            reply_markup=MAIN_KEYBOARD,
        )
        return True

    if text == "💳 Баланс - Оплата":
        await update.message.reply_text(
            "Раздел оплаты пока не подключен. Сейчас бот работает без оплаты.",
            reply_markup=MAIN_KEYBOARD,
        )
        return True

    if text == "❓ Команды":
        await update.message.reply_text(
            "Команды:\n"
            "/start - показать меню\n"
            "/sheet ссылка - сменить Google Sheets\n"
            "/wb 705719577 - PDF штрихкодов без ЧЗ по Артикул WB\n"
            "/art ДвоСпортЧерный-01 - PDF штрихкодов без ЧЗ по Артикул продавца\n\n"
            "Для PDF с ЧЗ просто отправьте .csv/.txt файл с КИЗами.",
            reply_markup=MAIN_KEYBOARD,
        )
        return True

    if text == "↻ Сброс":
        context.user_data.clear()
        PRODUCTS_CACHE.clear()
        await update.message.reply_text("Сбросил временные данные.", reply_markup=MAIN_KEYBOARD)
        await start(update, context)
        return True

    return False

async def make_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE, codes: list[str]) -> None:
    sheet_url = context.user_data.get("sheet_url") or os.environ.get("GOOGLE_SHEET_URL")
    if not sheet_url:
        await update.message.reply_text("Сначала отправьте ссылку: /sheet https://docs.google.com/spreadsheets/d/...")
        return
    if not codes:
        await update.message.reply_text("Не нашел КИЗы в сообщении или файле.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="labelbot_"))

    try:
        await update.message.reply_text(f"Получил КИЗов: {len(codes)}. Готовлю PDF...", reply_markup=MAIN_KEYBOARD)
        products = get_products(sheet_url)
        items = build_label_items(products, codes, LocalCodeValidator())
        pdf_name = build_pdf_filename(items)
        output_path = temp_dir / pdf_name
        create_labels_pdf(items, output_path)
    except Exception as exc:
        await update.message.reply_text(f"Не удалось создать PDF: {exc}")
        return

    await update.message.reply_document(document=output_path.open("rb"), filename=pdf_name)

async def make_barcode_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str, mode: str) -> None:
    sheet_url = context.user_data.get("sheet_url") or os.environ.get("GOOGLE_SHEET_URL")
    if not sheet_url:
        await update.message.reply_text("Сначала отправьте ссылку: /sheet https://docs.google.com/spreadsheets/d/...")
        return
    if not query:
        await update.message.reply_text("Напишите артикул. Пример: /wb 705719577 или /art ДвоСпортЧерный-01")
        return

    try:
        products = get_products(sheet_url)
        found = find_products(products, query, mode)
        if not found:
            await update.message.reply_text("Не нашел товар по этому артикулу.")
            return

        items = [LabelItem(product=product, mark_code=MarkCode(raw="", gtin=product.gtin, is_valid=True, message=""), index=i)
                 for i, product in enumerate(found, start=1)]
        temp_dir = Path(tempfile.mkdtemp(prefix="labelbot_"))
        pdf_name = build_barcode_filename(found)
        output_path = temp_dir / pdf_name
        create_labels_pdf(items, output_path, include_mark_code=False)
    except Exception as exc:
        await update.message.reply_text(f"Не удалось создать PDF: {exc}")
        return

    await update.message.reply_document(document=output_path.open("rb"), filename=pdf_name)


def parse_barcode_request(text: str):
    stripped = text.strip()
    lower = stripped.lower()
    for prefix, mode in (("/wb", "wb"), ("вб", "wb"), ("wb", "wb"), ("/art", "art"), ("арт", "art"), ("art", "art")):
        if lower.startswith(prefix + " "):
            return mode, stripped[len(prefix):].strip()
    return None

def looks_like_wb_article(text: str) -> bool:
    digits = only_digits(text)
    return digits == text.strip() and len(digits) >= 6

def find_products(products, query: str, mode: str):
    if mode == "wb":
        digits = only_digits(query)
        return [product for product in products if product.wb_article == digits]

    needle = normalize_article(query)
    return [product for product in products if needle in normalize_article(product.seller_article)]


def normalize_article(value: str) -> str:
    return str(value or "").replace(" ", "").lower()


def build_barcode_filename(products) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    first = products[0]
    if all(product.seller_article == first.seller_article for product in products):
        base = f"{first.seller_article}_barcodes_{len(products)}шт_{today}"
    else:
        base = f"barcodes_{len(products)}шт_{today}"
    return safe_filename(base) + ".pdf"

def get_products(sheet_url: str):
    nomenclature_sheet = os.environ.get("NOMENCLATURE_SHEET", NOMENCLATURE_SHEET)
    gtin_sheet = os.environ.get("GTIN_SHEET", GTIN_SHEET)
    cache_key = (sheet_url, nomenclature_sheet, gtin_sheet)
    now = time.time()

    cached = PRODUCTS_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    nomenclature_rows = read_google_sheet_csv(sheet_url, nomenclature_sheet)
    gtin_rows = read_google_sheet_csv(sheet_url, gtin_sheet)
    products = merge_products(nomenclature_rows, gtin_rows)
    PRODUCTS_CACHE[cache_key] = (now, products)
    return products


def build_pdf_filename(items) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    if not items:
        return f"labels_{today}.pdf"

    first = items[0].product
    if all(item.product.seller_article == first.seller_article and item.product.size == first.size for item in items):
        base = f"{first.seller_article}_{first.size}_{today}"
    else:
        base = f"{first.seller_article}_{first.size}_mix_{len(items)}шт_{today}"
    return safe_filename(base) + ".pdf"


def safe_filename(value: str) -> str:
    value = re.sub(r'[\\/:*?"<>|]+', "_", str(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:120] or "labels"


def read_codes_from_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    extracted = extract_marking_codes(text)
    if extracted:
        return extracted
    if path.suffix.lower() == ".txt":
        return [line.strip() for line in text.splitlines() if line.strip()]
    return read_codes_from_csv(text)


def extract_marking_codes(text: str) -> list[str]:
    starts = [match.start() for match in CODE_START_RE.finditer(text)]
    if not starts:
        return []

    codes: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        code = clean_marking_code(text[start:end])
        if code:
            codes.append(code)
    return codes


def clean_marking_code(value: str) -> str:
    value = value.replace("\ufeff", "")
    value = value.replace("\r", "").replace("\n", "").replace("\t", "")
    return value.strip().strip("\"',; ")


def read_codes_from_csv(text: str) -> list[str]:
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return []

    header = [cell.strip().lower() for cell in rows[0]]
    code_col = next((i for i, name in enumerate(header) if any(key in name for key in CODE_HEADERS)), None)

    if code_col is not None:
        data_rows = rows[1:]
        codes = [row[code_col].strip() for row in data_rows if code_col < len(row) and row[code_col].strip()]
    else:
        codes = [row[0].strip() for row in rows if row and row[0].strip()]

    return [code for code in codes if code.strip().lower() not in CODE_HEADERS]


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not     token:
        raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sheet", set_sheet))
    app.add_handler(CommandHandler("wb", handle_wb))
    app.add_handler(CommandHandler("art", handle_art))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_codes))
    app.run_polling()


if __name__ == "__main__":
    main()




