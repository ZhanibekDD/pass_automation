# Pass Automation / Pass Docs

Репозиторий содержит существующий генератор пакета АСДПО и изолированный
локальный AI-sidecar первого этапа. AI-модуль не заменяет текущий генератор,
не подключается к production-базе и не изменяет исходные документы.

## Что уже было в проекте

- загрузка одного сотрудника из `data/input/package_input.json`;
- проверка кодов, PDF-формата и лимита 1,8 МБ;
- копирование документов в структуру АСДПО;
- заполнение листа `Реестр` в Excel через `xlwings`;
- ZIP-сборка и отдельный SMTP-скрипт.

В исходной `main` нет Django, ORM-моделей, REST API, Docker/Nginx, очереди
фоновых задач, OCR/vision и автоматических тестов. Поэтому AI первого этапа
реализован как sidecar с отдельной SQLite-базой.

## Архитектура AI phase 1

| Компонент | Назначение |
|---|---|
| `app/ai/adapters.py` | Read-only адаптер текущего `package_input.json` |
| `app/ai/providers.py` | Ollama и OpenAI-compatible vLLM |
| `app/ai/vision.py` | Безопасный рендер PDF, JPG и PNG |
| `app/ai/rules.py` | Детерминированные проверки после извлечения |
| `app/ai/db.py` | Отдельные результаты, очередь, fingerprint и аудит |
| `app/ai/service.py` | Оркестрация анализа, без записи в Pass Docs |
| `app/ai/api.py` | Локальный FastAPI |

Vision-модель возвращает строгий JSON. Отсутствующие или неразборчивые поля
должны быть `null` с `confidence=0`; любые дополнительные поля отклоняются.
Решения о сроках, совпадении ФИО/ИИН и дублях принимает обычный Python-код,
а не языковая модель.

## Быстрый запуск

Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-ai.txt
cp .env.example .env
```

Запустите Ollama отдельно, загрузите локальную vision-модель и измените `.env`:

```bash
ollama pull qwen2.5vl:7b
```

```dotenv
AI_ENABLED=true
AI_PROVIDER=ollama
AI_BASE_URL=http://127.0.0.1:11434
AI_VISION_MODEL=qwen2.5vl:7b
```

Инициализация и анализ текущего пакета:

```bash
python scripts/ai_analyze_package.py
```

API:

```bash
uvicorn app.ai.api:app --host 127.0.0.1 --port 8090
```

Если задан `AI_API_KEY`, передавайте его в заголовке `X-API-Key`. Для действий
оператора передавайте стабильный неперсональный ID в `X-Operator-ID`.
`AI_MODEL_API_KEY` используется отдельно как Bearer token vLLM.

## API

- `GET /api/ai/health`
- `GET /api/ai/summary`
- `GET /api/ai/review-queue`
- `PATCH /api/ai/review-queue/{id}`
- `GET /api/ai/employees/{id}/completeness`
- `GET /api/ai/documents/{id}/analysis`

В текущем JSON-адаптере `employee_id` равен `employee_index`, а `document_id`
имеет вид `{employee_index}:{document_code}`, например `1:6`.

Пример подтверждения результата:

```bash
curl -X PATCH http://127.0.0.1:8090/api/ai/review-queue/1 \
  -H "Content-Type: application/json" \
  -H "X-API-Key: LOCAL_SECRET" \
  -H "X-Operator-ID: operator-17" \
  -d '{"status":"confirmed","comment":"Сверено с оригиналом"}'
```

## Docker

API публикуется только на loopback-интерфейсе:

```bash
docker compose -f docker-compose.ai.yml --profile ollama up -d --build
docker compose -f docker-compose.ai.yml exec ollama ollama pull qwen2.5vl:7b
```

Для внешнего vLLM установите `AI_PROVIDER=vllm`, `AI_BASE_URL` и имя модели,
после чего запустите только `ai-api`. Сервис имеет read-only root filesystem;
на запись доступен только именованный Docker volume `ai_data`.

## Проверки

```bash
pytest -q
python -m compileall -q app scripts
git diff --check
```

## Безопасность данных

- не добавляйте в Git оригиналы паспортов, ИИН, токены и `.env`;
- реальные файлы держите вне репозитория или в `data/private/`;
- AI API по умолчанию выключен и привязан в Docker к `127.0.0.1`;
- модель получает по одной странице, результаты сохраняются с файлом и номером
  страницы;
- production-база не используется; удаление и изменение исходных документов
  отсутствуют;
- локальная SQLite может содержать извлечённые персональные данные, поэтому
  каталог `data/ai/` исключён из Git и должен быть защищён правами ОС.

Детали реализации и ограничения: [docs/ai-phase1.md](docs/ai-phase1.md).
