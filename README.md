# Telegram-бот для этикеток 58x40 мм


Проект делает PDF с термоэтикетками 58x40 мм. Данные товара берутся из Google Sheets, коды Честного ЗНАКа отправляются в Telegram. API Честного ЗНАКа не используется.

## Логика

1. Бот читает лист `GTIN`.
2. На листе `GTIN` берет связку `GTIN -> Баркод`.
3. Бот читает лист `Отчёт с перечнем номенклатур`.
4. На листе номенклатур находит товар по `Баркод`.
5. Когда вы отправляете коды маркировки, бот достает GTIN из каждого кода.
6. По GTIN находит баркод, по баркоду находит товар и печатает этикетку.

## Нужные колонки

Лист `Отчёт с перечнем номенклатур`:

- `Бренд`
- `Предмет`
- `Артикул продавца`
- `Артикул WB`
- `Размер`
- `Баркод`
- `Состав` необязательно

Лист `GTIN`:

- `GTIN`
- `Баркод`

GTIN может быть 13 цифр, например `4700411459829`. В коде маркировки он обычно идет как 14 цифр с нулем впереди: `0104700411459829...`.

## Google Sheets

Файл должен быть доступен боту. Самый простой вариант для первой версии: открыть доступ по ссылке хотя бы на чтение.

В Telegram:

```text
/sheet https://docs.google.com/spreadsheets/d/ВАШ_ID/edit
```

Потом отправьте коды Честного ЗНАКа сообщением, каждый код с новой строки.

## Проверка локально без Telegram

Сначала создайте окружение и установите зависимости:

```powershell
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".\.venv\Scripts\python.exe" check_import.py
& ".\.venv\Scripts\python.exe" check_datamatrix.py
```

Генерация из тестовых CSV:

```powershell
& ".\.venv\Scripts\python.exe" cli.py `
  --nomenclature samples/nomenclature.csv `
  --gtin samples/gtin.csv `
  --codes samples/codes.txt `
  --out output/labels.pdf
```

Или напрямую из Google Sheets:

```powershell
& ".\.venv\Scripts\python.exe" cli.py `
  --google-sheet-url "https://docs.google.com/spreadsheets/d/ВАШ_ID/edit" `
  --codes samples/codes.txt `
  --out output/labels.pdf
```

## Запуск Telegram-бота

```powershell
$env:TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН"
& ".\.venv\Scripts\python.exe" main.py
```

## Проверка DataMatrix в PDF

КИЗ кодируется как GS1 DataMatrix с обязательным FNC1 (`]d2`). Перед отправкой бот
рендерит все страницы каждого PDF и повторно считывает каждый DataMatrix. Если код
не читается или отличается от исходного КИЗ, PDF пользователю не отправляется.
На тестовом компьютере полная проверка 1000 страниц заняла около 6 секунд.

При необходимости проверку можно ограничить, например первыми/средними/последними
20 страницами:

```powershell
$env:PDF_VERIFY_MAX_PAGES="20"
```
