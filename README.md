# Процессор розничных цен Apple

Веб-сервис на **FastAPI**: из сырого оптового прайса (текст или CSV) формирует розничный прайс в **CSV** по базам из `data/`. Поддерживаются **iPhone** (включая объединённые базы 13–16, Air, 17), **Apple Watch**, **iPad**, **AirPods**, **MacBook**, а также **смешанный** опт по категориям.

## Стек

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/)
- Статический UI: `static/index.html`
- Базы номенклатуры: JSON в каталоге `data/`

## Структура (важное)

| Путь | Назначение |
|------|------------|
| `fastapi_app.py` | Точка входа ASGI, маршруты, загрузка баз при старте |
| `iphone_processor_all.py`, `watch_processor.py`, … | Логика разбора и сопоставления с базой |
| `data/*.json` | Эталонные списки моделей/конфигураций |
| `static/` | Страница для вставки прайса и вызова API |
| `requirements.txt` | Зависимости для `pip` |

## Локальный запуск

```bash
cd /path/to/new_processor_src
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn fastapi_app:app --reload --host 127.0.0.1 --port 8000
```

- Интерфейс: http://127.0.0.1:8000/
- Swagger: http://127.0.0.1:8000/docs
- Проверка живости: `GET /health`

## API (кратко)

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/` | HTML-форма |
| GET | `/docs` | OpenAPI (Swagger UI) |
| GET | `/health` | `{"status": "ok"}` |
| POST | `/process/iphone-all` | iPhone по полной базе → CSV |
| POST | `/process/iphone` | iPhone, опционально merge двух прайсов |
| POST | `/process/watch` | Watch |
| POST | `/process/ipad` | iPad |
| POST | `/process/airpods` | AirPods |
| POST | `/process/macbook` | MacBook |
| POST | `/process/mixed` | Смешанный прайс, свои наценки USD по категориям |
| POST | `/process/iphone-17-site` | Розница iPhone 17 + Air (BYN без пересчёта): min BYN на «память + цвет» внутри линейки, в названии без SIM |

Ответы обработки — **CSV** с заголовком `Content-Disposition` для скачивания файла.

## Деплой на VPS (пример: systemd)

На сервере приложение обычно кладут в один каталог (например `/root/price_processor`), поднимают **uvicorn** и оформляют как **systemd**-сервис.

**Юнит** (пример `price_processor.service`):

- `WorkingDirectory` — каталог с кодом и `data/`
- `ExecStart`: `.../venv/bin/uvicorn fastapi_app:app --host 0.0.0.0 --port 8000`

После правок кода на своей машине — синхронизация и перезапуск (подставьте пользователя, хост и путь):

```bash
cd /path/to/new_processor_src

rsync -avz --delete \
  --filter 'protect /venv/' \
  --exclude '.git' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude '.cursor' \
  ./ user@YOUR_SERVER:/path/to/app/

ssh user@YOUR_SERVER 'cd /path/to/app && ./venv/bin/pip install -r requirements.txt -q && sudo systemctl restart price_processor.service'
```

Проверка: `GET http://YOUR_SERVER:8000/health` и открытие `/docs`.

## Безопасность

При прослушивании `0.0.0.0:8000` API доступен из интернета всем, кто знает адрес. Для ограничения доступа (команда, VPN, Basic Auth за Nginx и т.д.) настраивают отдельно — в коде встроенной авторизации нет.
