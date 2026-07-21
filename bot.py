from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path



from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from data import merge_products, only_digits
from google_sheets import read_google_sheet_csv
from pdf_generator import create_datamatrix_image, create_labels_pdf
from models import LabelItem, MarkCode, Product
from validator import LocalCodeValidator, extract_gtin, normalize_gtin


NOMENCLATURE_SHEET = "Отчёт с перечнем номенклатур"
GTIN_SHEET = "GTIN"
CODE_HEADERS = ("киз", "код", "код маркировки", "честный знак", "datamatrix", "data matrix", "mark_code")
CODE_START_RE = re.compile(r"(?=01\d{14})")
CACHE_TTL_SECONDS = 600
PRODUCTS_CACHE: dict[tuple[str, str, str], tuple[float, list]] = {}
APP_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = APP_DIR / "output"
DB_PATH = OUTPUT_DIR / "labelbot.db"
PRODUCTS_CACHE_FILE = OUTPUT_DIR / "products_cache.json"
BACKUP_DIR = OUTPUT_DIR / "backup"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📈 Статистика", "🔎 Проверить таблицу"],
        ["❓ Команды", "↻ Сброс"],
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text(
        "Бот готов. Данные товара беру из Google Sheets.\n\n"
        "Отправьте файл .txt или .csv с КИЗами — бот сам проверит GTIN, дубли и товары.\n"
        "Если получится больше 3 PDF по артикулам, бот сам отправит ZIP.\n"
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

    pending_codes = context.user_data.setdefault("pending_codes", [])
    pending_codes.extend(codes)
    total_codes = len(pending_codes)

    if context.user_data.get("awaiting_batch_number"):
        await update.message.reply_text(
            f"<b>Добавил файл: {len(codes)} КИЗ. Всего в общей партии: {total_codes} КИЗ.</b>",
            reply_markup=MAIN_KEYBOARD,
            parse_mode="HTML",
        )
        return

    context.user_data["awaiting_batch_number"] = True
    await update.message.reply_text(
        f"<b>Получил КИЗов: {total_codes}. Если файлов несколько, отправьте все файлы сейчас. Потом напишите номер партии, например: 0295</b>",
        reply_markup=MAIN_KEYBOARD,
        parse_mode="HTML",
    )


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
    if context.user_data.pop("awaiting_batch_number", False):
        codes = context.user_data.pop("pending_codes", [])
        batch_number = text.strip()
        if not codes:
            await update.message.reply_text("КИЗы не найдены. Отправьте файл ещё раз.", reply_markup=MAIN_KEYBOARD)
            return
        await make_pdf(update, context, codes, batch_number=batch_number)
        return

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

    if text == "📈 Статистика":
        await send_stats(update)
        return True

    if text == "🔎 Проверить таблицу":
        await check_sheet(update, context)
        return True

    if text == "❓ Команды":
        await update.message.reply_text(
            "Команды:\n"
            "/start - показать меню\n"
            "/sheet ссылка - сменить Google Sheets\n"
            "/wb 705719577 - PDF штрихкодов без ЧЗ по Артикул WB\n"
            "/art ДвоСпортЧерный-01 - PDF штрихкодов без ЧЗ по Артикул продавца\n"
            "/stat - статистика за сегодня\n"
            "/stat 2026-06-01 2026-06-25 - статистика за период\n\n"
            "Для PDF с ЧЗ просто отправьте .csv/.txt файл с КИЗами. "
            "Бот сам решит: один PDF, несколько PDF или ZIP.",
            reply_markup=MAIN_KEYBOARD,
        )
        return True

    if text == "↻ Сброс":
        context.user_data.clear()
        PRODUCTS_CACHE.clear()
        await update.message.reply_text("Сбросил временные данные и кэш Google Sheets.", reply_markup=MAIN_KEYBOARD)
        await start(update, context)
        return True

    return False

async def make_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE, codes: list[str], batch_number: str = "") -> None:
    sheet_url = context.user_data.get("sheet_url") or os.environ.get("GOOGLE_SHEET_URL")
    if not sheet_url:
        await update.message.reply_text("Сначала отправьте ссылку: /sheet https://docs.google.com/spreadsheets/d/...")
        return
    if not codes:
        await update.message.reply_text("Не нашел КИЗы в сообщении или файле.")
        return

    temp_dir = Path(tempfile.mkdtemp(prefix="labelbot_"))

    try:
        await update.message.reply_text(f"Получил КИЗов: {len(codes)}. Проверяю GTIN и товары...", reply_markup=MAIN_KEYBOARD)
        products = get_products(sheet_url)
        analysis = build_items_with_report(products, codes)
        report_text = build_analysis_message(analysis)
        await update.message.reply_text(report_text, reply_markup=MAIN_KEYBOARD)

        control_report_path = temp_dir / "control_report.csv"
        write_rows_csv(control_report_path, analysis["control_rows"])
        await update.message.reply_document(document=control_report_path.open("rb"), filename=control_report_path.name)

        if analysis["duplicate_used"]:
            report_path = temp_dir / "used_kiz_duplicates.csv"
            write_rows_csv(report_path, analysis["duplicate_used"])
            await update.message.reply_document(document=report_path.open("rb"), filename=report_path.name)

        if analysis["not_found"] or analysis["invalid_gtin"]:
            report_path = temp_dir / "not_found.csv"
            write_rows_csv(report_path, analysis["not_found"] + analysis["invalid_gtin"])
            await update.message.reply_document(document=report_path.open("rb"), filename=report_path.name)
            if not analysis["items"]:
                await update.message.reply_text("Нет этикеток для печати: все КИЗы с ошибками.")
                return

        items = analysis["items"]
        readability = check_datamatrix_readability(items[0].mark_code.raw) if items else ""
        outputs = create_auto_outputs(items, temp_dir, batch_number=batch_number)
        out_name = ", ".join(name for _, name in outputs)
        remember_printed_codes(items, out_name, update.effective_user)
    except Exception as exc:
        await update.message.reply_text(f"Не удалось создать PDF: {human_error(exc)}")
        return

    for output_path, file_name in outputs:
        await update.message.reply_document(document=output_path.open("rb"), filename=file_name)
    if readability:
        await update.message.reply_text(readability, reply_markup=MAIN_KEYBOARD)

async def stat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        start_date, end_date = parse_stat_period(context.args)
    except ValueError:
        await update.message.reply_text("Дата должна быть так: /stat 2026-06-01 2026-06-25", reply_markup=MAIN_KEYBOARD)
        return
    await send_stats(update, start_date=start_date, end_date=end_date)


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

    try:
        nomenclature_rows = read_google_sheet_csv(sheet_url, nomenclature_sheet)
        gtin_rows = read_google_sheet_csv(sheet_url, gtin_sheet)
        products = merge_products(nomenclature_rows, gtin_rows)
        save_products_local(products)
    except Exception:
        products = load_products_local()
        if not products:
            raise
    PRODUCTS_CACHE[cache_key] = (now, products)
    return products



def _gtin_keys(value: str | None) -> set[str]:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    keys: set[str] = set()
    if digits:
        keys.add(normalize_gtin(digits))
    if len(digits) == 13:
        keys.add("0" + digits)
    if len(digits) == 14 and digits.startswith("0"):
        keys.add(digits[1:])
    return {key for key in keys if key}

def _product_report_fields(product: Product | None) -> dict[str, str]:
    if product is None:
        return {
            "Р‘СЂРµРЅРґ": "",
            "РџСЂРµРґРјРµС‚": "",
            "РђСЂС‚РёРєСѓР» РїСЂРѕРґР°РІС†Р°": "",
            "РђСЂС‚РёРєСѓР» WB": "",
            "Р Р°Р·РјРµСЂ": "",
            "Р¦РІРµС‚": "",
            "РџРѕСЃС‚Р°РІС‰РёРє": "",
            "Р‘Р°СЂРєРѕРґ": "",
            "GTIN С‚РѕРІР°СЂР°": "",
        }
    return {
        "Р‘СЂРµРЅРґ": product.brand,
        "РџСЂРµРґРјРµС‚": product.subject,
        "РђСЂС‚РёРєСѓР» РїСЂРѕРґР°РІС†Р°": product.seller_article,
        "РђСЂС‚РёРєСѓР» WB": product.wb_article,
        "Р Р°Р·РјРµСЂ": product.size,
        "Р¦РІРµС‚": product.color,
        "РџРѕСЃС‚Р°РІС‰РёРє": product.supplier,
        "Р‘Р°СЂРєРѕРґ": product.barcode,
        "GTIN С‚РѕРІР°СЂР°": product.gtin,
    }


def _control_row(index: int, code: str, gtin: str, status: str, reason: str, product: Product | None = None) -> dict[str, str]:
    row = {
        "РЎС‚Р°С‚СѓСЃ": status,
        "РџСЂРёС‡РёРЅР°": reason,
        "РќРѕРјРµСЂ": str(index),
        "GTIN РљРР—": gtin or "",
        "РљРР—": code,
    }
    row.update(_product_report_fields(product))
    return row


def build_items_with_report(products: list[Product], codes: list[str]) -> dict:
    validator = LocalCodeValidator()
    product_by_gtin: dict[str, Product] = {}
    for product in products:
        for key in _gtin_keys(product.gtin):
            product_by_gtin[key] = product
    used_codes = load_used_codes()
    items: list[LabelItem] = []
    invalid_gtin: list[dict[str, str]] = []
    not_found: list[dict[str, str]] = []
    duplicate_used: list[dict[str, str]] = []
    control_rows: list[dict[str, str]] = []
    cleaned_codes = [clean_marking_code(code) for code in codes]
    seen_in_file = Counter(cleaned_codes)

    for index, code in enumerate(cleaned_codes, start=1):
        gtin14 = extract_gtin(code)
        gtin = normalize_gtin(gtin14)
        structure_error = validate_kiz_structure(code)
        if structure_error:
            row = _control_row(index, code, gtin or "", "ERROR", structure_error)
            invalid_gtin.append(row)
            control_rows.append(row)
            continue

        if not gtin or len(gtin) != 13 or not gtin.startswith("470"):
            reason = "GTIN РґРѕР»Р¶РµРЅ Р±С‹С‚СЊ 13 С†РёС„СЂ Рё РЅР°С‡РёРЅР°С‚СЊСЃСЏ СЃ 470"
            row = _control_row(index, code, gtin or "", "ERROR", reason)
            invalid_gtin.append(row)
            control_rows.append(row)
            continue

        product = None
        for key in _gtin_keys(gtin14):
            product = product_by_gtin.get(key)
            if product is not None:
                break
        if product is None:
            reason = "GTIN РЅРµ РЅР°Р№РґРµРЅ РІ Google Sheets/Р»РѕРєР°Р»СЊРЅРѕР№ Р±Р°Р·Рµ"
            row = _control_row(index, code, gtin, "NOT_FOUND", reason)
            not_found.append(row)
            control_rows.append(row)
            continue

        status = "OK"
        reason = ""
        if code in used_codes:
            old = used_codes[code]
            status = "DUPLICATE_USED"
            reason = f"РЈР¶Рµ РїРµС‡Р°С‚Р°Р»СЃСЏ {old.get('printed_at', '')} С„Р°Р№Р» {old.get('file_name', '')}"
            duplicate_used.append(_control_row(index, code, gtin, status, reason, product))

        mark_code = validator.check_code(code, expected_gtin=product.gtin)
        items.append(LabelItem(product=product, mark_code=mark_code, index=len(items) + 1))
        control_rows.append(_control_row(index, code, gtin, status, reason, product))

    duplicates_in_file = sum(count - 1 for count in seen_in_file.values() if count > 1)
    return {
        "items": items,
        "invalid_gtin": invalid_gtin,
        "not_found": not_found,
        "duplicate_used": duplicate_used,
        "control_rows": control_rows,
        "duplicates_in_file": duplicates_in_file,
        "total": len(codes),
    }


def build_analysis_message(analysis: dict) -> str:
    return (
        "Проверка готова:\n"
        f"Всего КИЗ: {analysis['total']}\n"
        f"Будет напечатано: {len(analysis['items'])}\n"
        f"Ошибки GTIN: {len(analysis['invalid_gtin'])}\n"
        f"Не найден товар: {len(analysis['not_found'])}\n"
        f"Уже печатались: {len(analysis['duplicate_used'])}\n"
        f"Дубли внутри файла: {analysis['duplicates_in_file']}"
    )


def write_rows_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    headers = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)



def group_items_by_product(items: list[LabelItem]) -> list[list[LabelItem]]:
    groups: dict[tuple[str, str], list[LabelItem]] = defaultdict(list)
    for item in items:
        groups[(item.product.seller_article, item.product.size)].append(item)
    result: list[list[LabelItem]] = []
    for grouped_items in groups.values():
        result.append([
            LabelItem(product=item.product, mark_code=item.mark_code, index=index)
            for index, item in enumerate(grouped_items, start=1)
        ])
    return result


def create_auto_outputs(items: list[LabelItem], temp_dir: Path, batch_number: str = "") -> list[tuple[Path, str]]:
    groups = group_items_by_product(items)
    if len(groups) > 3:
        return [create_zip_from_groups(groups, temp_dir, batch_number=batch_number)]

    outputs: list[tuple[Path, str]] = []
    for grouped_items in groups:
        pdf_name = build_pdf_filename(grouped_items)
        pdf_path = temp_dir / pdf_name
        create_labels_pdf(grouped_items, pdf_path, info_page=build_info_page(grouped_items, batch_number=batch_number))
        outputs.append((pdf_path, pdf_name))
    return outputs


def create_zip_from_groups(groups: list[list[LabelItem]], temp_dir: Path, batch_number: str = "") -> tuple[Path, str]:
    total = sum(len(group) for group in groups)
    today = datetime.now().strftime("%Y-%m-%d")
    zip_name = safe_filename(f"labels_{total}шт_{today}") + ".zip"
    zip_path = temp_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for grouped_items in groups:
            pdf_name = build_pdf_filename(grouped_items)
            pdf_path = temp_dir / pdf_name
            create_labels_pdf(grouped_items, pdf_path, info_page=build_info_page(grouped_items, batch_number=batch_number))
            archive.write(pdf_path, arcname=pdf_name)
    return zip_path, zip_name


def build_info_page(items: list[LabelItem], batch_number: str = "") -> dict[str, str]:
    first = items[0].product if items else None
    if first is None:
        return {}
    today = datetime.now().strftime("%Y-%m-%d")
    batch = str(batch_number or "").strip() or safe_filename(f"{first.seller_article}_{first.size}_{today}_{len(items)}шт")
    return {
        "date": today,
        "batch": batch,
        "count": str(len(items)),
        "brand": first.brand,
        "subject": first.subject,
        "seller_article": first.seller_article,
        "wb_article": first.wb_article,
        "size": first.size,
        "supplier": first.supplier,
    }

def create_zip_by_products(items: list[LabelItem], temp_dir: Path) -> tuple[Path, str]:
    today = datetime.now().strftime("%Y-%m-%d")
    groups: dict[tuple[str, str], list[LabelItem]] = defaultdict(list)
    for item in items:
        groups[(item.product.seller_article, item.product.size)].append(item)

    zip_name = safe_filename(f"labels_{len(items)}шт_{today}") + ".zip"
    zip_path = temp_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for grouped_items in groups.values():
            for idx, item in enumerate(grouped_items, start=1):
                grouped_items[idx - 1] = LabelItem(product=item.product, mark_code=item.mark_code, index=idx)
            pdf_name = build_pdf_filename(grouped_items)
            pdf_path = temp_dir / pdf_name
            create_labels_pdf(grouped_items, pdf_path, info_page=build_info_page(grouped_items))
            archive.write(pdf_path, arcname=pdf_name)
    return zip_path, zip_name


def check_datamatrix_readability(code: str) -> str:
    try:
        from pylibdmtx.pylibdmtx import decode as decode_datamatrix
        image = create_datamatrix_image(code)
        decoded = decode_datamatrix(image)
        if decoded:
            return "✅ DataMatrix проверен: код создаётся и читается программно."
        return "⚠️ DataMatrix не прочитался программно. Проверь размер/белое поле перед печатью."
    except Exception as exc:
        return f"⚠️ Проверка DataMatrix недоступна: {exc}"


def init_db() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS printed_codes (code TEXT PRIMARY KEY, gtin TEXT, article TEXT, size TEXT, file_name TEXT, user_name TEXT, printed_at TEXT)"
        )
    backup_db_once_daily()


def backup_db_once_daily() -> None:
    if not DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    backup_path = BACKUP_DIR / f"labelbot_{today}.db"
    shutil.copy2(DB_PATH, backup_path)


def load_used_codes() -> dict[str, dict[str, str]]:
    init_db()
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT code, gtin, article, size, file_name, user_name, printed_at FROM printed_codes").fetchall()
    return {
        row[0]: {"gtin": row[1], "article": row[2], "size": row[3], "file_name": row[4], "user_name": row[5], "printed_at": row[6]}
        for row in rows
    }


def remember_printed_codes(items: list[LabelItem], file_name: str, user) -> None:
    init_db()
    user_name = getattr(user, "full_name", None) or getattr(user, "username", None) or "unknown"
    printed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(DB_PATH) as conn:
        for item in items:
            conn.execute(
                "INSERT OR IGNORE INTO printed_codes (code, gtin, article, size, file_name, user_name, printed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (item.mark_code.raw, item.mark_code.gtin or "", item.product.seller_article, item.product.size, file_name, user_name, printed_at),
            )
    backup_db_once_daily()


async def send_stats(update: Update, start_date: str | None = None, end_date: str | None = None) -> None:
    init_db()
    if not start_date:
        start_date = datetime.now().strftime("%Y-%m-%d")
    if not end_date:
        end_date = start_date
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)

    with sqlite3.connect(DB_PATH) as conn:
        params = (start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S"))
        total_period = conn.execute("SELECT COUNT(*) FROM printed_codes WHERE printed_at >= ? AND printed_at < ?", params).fetchone()[0]
        total_all = conn.execute("SELECT COUNT(*) FROM printed_codes").fetchone()[0]
        by_article = conn.execute(
            "SELECT article, size, COUNT(*) FROM printed_codes WHERE printed_at >= ? AND printed_at < ? GROUP BY article, size ORDER BY COUNT(*) DESC LIMIT 10",
            params,
        ).fetchall()
        by_user = conn.execute(
            "SELECT user_name, COUNT(*), MIN(printed_at), MAX(printed_at) FROM printed_codes WHERE printed_at >= ? AND printed_at < ? GROUP BY user_name ORDER BY COUNT(*) DESC LIMIT 10",
            params,
        ).fetchall()
    title = f"Статистика за {start_date}" if start_date == end_date else f"Статистика {start_date} - {end_date}"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [title + ":", f"Время отчёта: {generated_at}", f"Этикеток за период: {total_period}", f"Всего в базе: {total_all}"]
    if by_user:
        lines.append("\nКто печатал:")
        lines.extend(f"{name}: {count} шт ({format_time_range(first_at, last_at)})" for name, count, first_at, last_at in by_user)
    if by_article:
        lines.append("\nПо артикулам:")
        lines.extend(f"{article} / {size}: {count} шт" for article, size, count in by_article)
    await update.message.reply_text("\n".join(lines), reply_markup=MAIN_KEYBOARD)



def format_time_range(first_at: str, last_at: str) -> str:
    first_time = str(first_at or "")[11:16]
    last_time = str(last_at or "")[11:16]
    if first_time and last_time and first_time != last_time:
        return f"{first_time}-{last_time}"
    return first_time or last_time or "--:--"

def parse_stat_period(args: list[str]) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    if len(args) == 1:
        validate_date(args[0])
        return args[0], args[0]
    validate_date(args[0])
    validate_date(args[1])
    return args[0], args[1]


def validate_date(value: str) -> None:
    datetime.strptime(value, "%Y-%m-%d")

async def check_sheet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    sheet_url = context.user_data.get("sheet_url") or os.environ.get("GOOGLE_SHEET_URL")
    if not sheet_url:
        await update.message.reply_text("Сначала отправьте ссылку: /sheet https://docs.google.com/spreadsheets/d/...")
        return
    try:
        products = get_products(sheet_url)
        barcode_counts = Counter(product.barcode for product in products if product.barcode)
        gtin_counts = Counter(normalize_gtin(product.gtin) for product in products if normalize_gtin(product.gtin))
        barcode_dupes = [code for code, count in barcode_counts.items() if count > 1]
        gtin_dupes = [code for code, count in gtin_counts.items() if count > 1]
        msg = (
            f"Проверка таблицы:\nТоваров: {len(products)}\n"
            f"Дубли баркодов: {len(barcode_dupes)}\nДубли GTIN: {len(gtin_dupes)}\n"
            f"Локальная база обновлена: {PRODUCTS_CACHE_FILE.name}"
        )
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
    except Exception as exc:
        await update.message.reply_text(f"Не смог проверить таблицу: {human_error(exc)}", reply_markup=MAIN_KEYBOARD)


def save_products_local(products: list[Product]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCTS_CACHE_FILE.write_text(json.dumps([asdict(product) for product in products], ensure_ascii=False), encoding="utf-8")


def load_products_local() -> list[Product]:
    if not PRODUCTS_CACHE_FILE.exists():
        return []
    rows = json.loads(PRODUCTS_CACHE_FILE.read_text(encoding="utf-8"))
    return [Product(**row) for row in rows]


def human_error(exc: Exception) -> str:
    text = str(exc)
    if "handshake operation timed out" in text or "timed out" in text:
        return "Google таблица долго не отвечает. Бот попробует локальную базу, если она уже была сохранена."
    return text

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
    if path.suffix.lower() == ".csv":
        return read_codes_from_csv(text)
    return read_codes_line_by_line(text)


def read_codes_line_by_line(text: str) -> list[str]:
    # Important: one physical line is one KIZ. Do not split by regex inside crypto text.
    codes: list[str] = []
    for line in text.split("\n"):
        code = clean_marking_code(line.rstrip("\r"))
        if not code:
            continue
        if code.strip().lower() in CODE_HEADERS:
            continue
        codes.append(code)
    return codes


def _best_code_from_csv_row(row: list[str]) -> str:
    # CSV wraps KIZ values with quotes and escapes internal quotes as "".
    # After csv.reader parses the row, the real cell text is restored.
    for cell in row:
        code = clean_marking_code(cell)
        if code and code.strip().lower() not in CODE_HEADERS and code.startswith("01"):
            return code
    for cell in row:
        code = clean_marking_code(cell)
        if code and code.strip().lower() not in CODE_HEADERS:
            return code
    return ""


def extract_marking_codes(text: str) -> list[str]:
    # Used only for plain Telegram messages, not for uploaded CSV/TXT files.
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
    value = str(value or "")
    value = value.replace("\ufeff", "")

    gs = "\x1d"
    for marker in ("\\x1d", "<GS>", "[GS]", "{GS}"):
        value = value.replace(marker, gs)
    value = value.replace(chr(0x241D), gs)
    value = value.replace(chr(0x100000), gs)

    # CSV escaped quote -> real quote.
    value = value.replace('""', '"')

    # Some exporters/scanners turn GS into a plain space before AI 91/92.
    value = re.sub(r"\s+91", gs + "91", value)
    value = re.sub(r"\s+92", gs + "92", value)

    # Real KIZ must not contain ordinary spaces/tabs/newlines.
    value = re.sub(r"[ \t\r\n]+", "", value).strip()

    # Remove only wrapping quotes around the whole KIZ, not quotes inside serial.
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    return value.strip()


def read_codes_from_csv(text: str) -> list[str]:
    codes: list[str] = []
    reader = csv.reader(text.split("\n"))
    for row in reader:
        code = _best_code_from_csv_row(row)
        if code:
            codes.append(code)
    return codes


def validate_kiz_structure(code: str) -> str:
    code = clean_marking_code(code)
    if not code:
        return "EMPTY_KIZ"
    if not code.startswith("01"):
        return "MUST_START_WITH_01"
    if len(code) < 31:
        return "KIZ_TOO_SHORT"

    gtin14 = code[2:16]
    if len(gtin14) != 14 or not gtin14.isdigit():
        return "GTIN_AFTER_01_MUST_BE_14_DIGITS"
    if code[16:18] != "21":
        return "AI_21_MISSING_AFTER_GTIN"

    gs = "\x1d"
    if gs + "91" not in code:
        return "GS_91_MISSING"
    if gs + "92" not in code:
        return "GS_92_MISSING"

    pos_91 = code.find(gs + "91")
    pos_92 = code.find(gs + "92")
    if pos_92 <= pos_91:
        return "AI_92_MUST_BE_AFTER_AI_91"
    if not code[18:pos_91]:
        return "SERIAL_AFTER_21_IS_EMPTY"
    if not code[pos_91 + 3:pos_92]:
        return "CRYPTO_91_IS_EMPTY"
    if not code[pos_92 + 3:]:
        return "CRYPTO_92_IS_EMPTY"
    return ""


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not     token:
        raise RuntimeError("Укажите TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("sheet", set_sheet))
    app.add_handler(CommandHandler("wb", handle_wb))
    app.add_handler(CommandHandler("art", handle_art))
    app.add_handler(CommandHandler("stat", stat_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_codes))
    app.run_polling()


if __name__ == "__main__":
    main()









