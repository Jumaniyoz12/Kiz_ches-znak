from pdf_generator import create_datamatrix_image
import zxingcpp


TEST_CODE = "010470041145982921TEST001<GS>91ABCD<GS>92TESTCRYPTO123456"

image = create_datamatrix_image(TEST_CODE)
result = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.DataMatrix)
if result is None or result.symbology_identifier != "]d2":
    raise RuntimeError("GS1 DataMatrix self-check failed")

print("GS1 DataMatrix ok")
