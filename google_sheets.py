from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request


def read_google_sheet_csv(spreadsheet_url: str, sheet_name_or_gid: str) -> list[dict[str, str]]:
    csv_url = build_csv_export_url(spreadsheet_url, sheet_name_or_gid)
    request = urllib.request.Request(csv_url, headers={"User-Agent": "labelbot/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{key: (value or "").strip() for key, value in row.items()} for row in reader if any(row.values())]




def build_csv_export_url(spreadsheet_url: str, sheet_name_or_gid: str) -> str:
    sheet_id = extract_spreadsheet_id(spreadsheet_url)
    value = str(sheet_name_or_gid or "").strip()
    if value.isdigit():
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={value}"
    encoded_sheet = urllib.parse.quote(value)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv&sheet={encoded_sheet}"


def extract_spreadsheet_id(spreadsheet_url: str) -> str:
    parts = urllib.parse.urlparse(spreadsheet_url)
    path_parts = [part for part in parts.path.split("/") if part]
    if "d" in path_parts:
        index = path_parts.index("d")
        if index + 1 < len(path_parts):
            return path_parts[index + 1]
    if spreadsheet_url and "/" not in spreadsheet_url:
        return spreadsheet_url
    raise ValueError("Не удалось понять ссылку Google Sheets")
