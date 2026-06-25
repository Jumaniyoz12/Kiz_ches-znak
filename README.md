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

```powershell
& "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m labelbot.cli `
  --nomenclature samples/nomenclature.csv `
  --gtin samples/gtin.csv `
  --codes samples/codes.txt `
  --out output/labels.pdf
```

Или напрямую из Google Sheets:

```powershell
& "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m labelbot.cli `
  --google-sheet-url "https://docs.google.com/spreadsheets/d/ВАШ_ID/edit" `
  --codes samples/codes.txt `
  --out output/labels.pdf
```

## Запуск Telegram-бота

```powershell
& "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN="ВАШ_ТОКЕН"
& "C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m labelbot.bot
```