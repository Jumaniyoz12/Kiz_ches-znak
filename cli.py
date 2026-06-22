from __future__ import annotations

import argparse

from data import build_label_items, merge_products, read_codes, read_csv_rows, read_products
from google_sheets import read_google_sheet_csv
from pdf_generator import create_labels_pdf
from validator import LocalCodeValidator


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 58x40 mm Honest Sign labels PDF.")
    parser.add_argument("--products", help="Old mode: CSV file with ready products")
    parser.add_argument("--nomenclature", help="CSV export of sheet 'Отчёт с перечнем номенклатур'")
    parser.add_argument("--gtin", help="CSV export of sheet 'GTIN'")
    parser.add_argument("--google-sheet-url", help="Google Sheets URL or spreadsheet id")
    parser.add_argument("--nomenclature-sheet", default="Отчёт с перечнем номенклатур")
    parser.add_argument("--gtin-sheet", default="GTIN")
    parser.add_argument("--codes", required=True, help="TXT file with Honest Sign codes, one per line")
    parser.add_argument("--out", required=True, help="Output PDF path")
    args = parser.parse_args()

    if args.google_sheet_url:
        nomenclature_rows = read_google_sheet_csv(args.google_sheet_url, args.nomenclature_sheet)
        gtin_rows = read_google_sheet_csv(args.google_sheet_url, args.gtin_sheet)
        products = merge_products(nomenclature_rows, gtin_rows)
    elif args.nomenclature and args.gtin:
        products = merge_products(read_csv_rows(args.nomenclature), read_csv_rows(args.gtin))
    elif args.products:
        products = read_products(args.products)
    else:
        raise SystemExit("Укажите --google-sheet-url или пару --nomenclature/--gtin")

    codes = read_codes(args.codes)
    items = build_label_items(products, codes, LocalCodeValidator())
    output = create_labels_pdf(items, args.out)
    print(f"PDF готов: {output}")


if __name__ == "__main__":
    main()

