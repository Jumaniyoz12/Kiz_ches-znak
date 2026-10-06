from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import pymupdf
import zxingcpp

from bot import build_items_with_report, clean_marking_code, create_auto_outputs
from data import build_label_items
from models import LabelItem, MarkCode, Product
from pdf_generator import create_datamatrix_image, create_labels_pdf, format_creation_date, prepare_marking_code_for_datamatrix
from pdf_verifier import sampled_page_indexes, verify_pdf_datamatrix
from validator import LocalCodeValidator


CODE = "010470041145982921SAMPLE001<GS>91ABCD<GS>92TESTCRYPTO1234567890"


def product(gtin: str = "4700411459829") -> Product:
    return Product(
        brand="Timur Kids",
        subject="Футболка",
        seller_article="TK-SAMPLE-01",
        wb_article="100000001",
        size="116",
        color="Белый",
        supplier="Тестовый поставщик",
        barcode="2200000000001",
        gtin=gtin,
        composition="Хлопок 100%",
    )


class CoreTests(unittest.TestCase):
    def test_creation_date_format(self) -> None:
        self.assertEqual(format_creation_date(date(2026, 10, 5)), "05.10.2026")

    def test_pdf_verifier_checks_all_pages_by_default(self) -> None:
        self.assertEqual(sampled_page_indexes(10), list(range(10)))
        self.assertEqual(sampled_page_indexes(10, max_pages=5), [0, 2, 4, 7, 9])

    def test_unknown_gtin_is_not_assigned_to_next_product(self) -> None:
        with self.assertRaisesRegex(ValueError, "товар с GTIN"):
            build_label_items(
                [product()],
                ["010999999999999921SAMPLE001<GS>91ABCD<GS>92TESTCRYPTO1234567890"],
                LocalCodeValidator(),
            )

    def test_gs1_datamatrix_round_trip(self) -> None:
        image = create_datamatrix_image(CODE)
        result = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.DataMatrix)
        self.assertIsNotNone(result)
        self.assertEqual(result.symbology_identifier, "]d2")
        self.assertEqual(result.content_type, zxingcpp.ContentType.GS1)
        self.assertEqual(result.bytes, prepare_marking_code_for_datamatrix(CODE))

    def test_duplicate_in_same_file_is_not_printed_twice(self) -> None:
        with patch("bot.load_used_codes", return_value={}):
            analysis = build_items_with_report([product()], [CODE, CODE])
        self.assertEqual(len(analysis["items"]), 1)
        self.assertEqual(analysis["duplicates_in_file"], 1)
        self.assertEqual(analysis["control_rows"][1]["Статус"], "DUPLICATE_IN_FILE")

    def test_previously_used_code_is_not_printed(self) -> None:
        previous = {prepare_marking_code_for_datamatrix(CODE).decode("ascii"): {"printed_at": "2026-01-01", "file_name": "old.pdf"}}
        with patch("bot.load_used_codes", return_value=previous):
            analysis = build_items_with_report([product()], [CODE])
        self.assertEqual(analysis["items"], [])
        self.assertEqual(len(analysis["duplicate_used"]), 1)

    def test_double_quotes_inside_crypto_are_preserved(self) -> None:
        raw = '010470041145982921SAMPLE001<GS>91ABCD<GS>92TEST""CRYPTO'
        cleaned = clean_marking_code(raw)
        self.assertIn('TEST""CRYPTO', cleaned)

    def test_pdf_rendered_datamatrix_round_trip(self) -> None:
        item = LabelItem(
            product=product(),
            mark_code=MarkCode(raw=CODE, gtin="04700411459829", is_valid=True, message=""),
            index=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "labels.pdf"
            create_labels_pdf([item], output)
            checked = verify_pdf_datamatrix(output, [CODE], max_pages=5)
            with pymupdf.open(output) as document:
                image = document[0].get_pixmap(dpi=300, alpha=False).pil_image()
            barcodes = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.Code128)
        self.assertEqual(checked, [0])
        self.assertIn(product().barcode, [barcode.text for barcode in barcodes])

    def test_compact_labels_keep_long_article_visible(self) -> None:
        long_article = "TK-LONG-ARTICLE-123456789"
        item = LabelItem(
            product=replace(product(), seller_article=long_article),
            mark_code=MarkCode(raw=CODE, gtin="04700411459829", is_valid=True, message=""),
            index=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "compact-label.pdf"
            create_labels_pdf([item], output, creation_date=date(2026, 10, 5))
            with pymupdf.open(output) as document:
                text = document[0].get_text()
        self.assertIn("Арт:", text)
        self.assertIn("Арт WB:", text)
        self.assertIn(long_article, text)

    def test_more_than_three_products_create_verified_zip(self) -> None:
        items: list[LabelItem] = []
        for index in range(4):
            gtin = str(4700411459829 + index)
            code = f"010{gtin}21SAMPLE{index:03d}<GS>91ABCD<GS>92ZIPTEST{index:010d}"
            item_product = Product(
                brand="Timur Kids",
                subject="Футболка",
                seller_article=f"TK-ZIP-{index}",
                wb_article=str(100000001 + index),
                size=str(116 + index),
                color="Белый",
                supplier="Тестовый поставщик",
                barcode=str(2200000000001 + index),
                gtin=gtin,
                composition="Хлопок 100%",
            )
            items.append(
                LabelItem(
                    product=item_product,
                    mark_code=MarkCode(raw=code, gtin="0" + gtin, is_valid=True, message=""),
                    index=index + 1,
                )
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = create_auto_outputs(items, Path(temp_dir), batch_number="QA")
            self.assertEqual(len(outputs), 1)
            zip_path, zip_name = outputs[0]
            self.assertTrue(zip_name.endswith(".zip"))
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(len(archive.namelist()), 4)


if __name__ == "__main__":
    unittest.main()
