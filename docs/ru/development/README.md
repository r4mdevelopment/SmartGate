[SmartGate](../../../README.ru.md) / [Документация](../README.md) / Локальная разработка

[English](../../en/development/README.md) · [Русский](README.md)

# Локальная разработка

Это руководство для разработки и локальной диагностики. Сам контроллер работает в GitHub Actions без локального Python. Однако для чтения подробного зашифрованного плана пока нужен Python на доверенном устройстве — [как расшифровать отчёт](../operations/privacy.md#чтение-подробного-отчёта). Настройка начинается с [быстрого старта](../../../README.ru.md#быстрый-старт).

Требуется Python >=3.12,<3.14. Все команды выполняются из корня checkout.


## На этой странице

- [Установка](#установка)
- [Проверки](#проверки)
- [Конфигурация](#конфигурация)
- [Обновление lock-файлов](#обновление-lock-файлов)

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

Для Linux/macOS используйте .venv/bin/python.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m ruff format --check src tests tools
.\.venv\Scripts\python.exe tools/check_docs.py
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m build --no-isolation
```

Для проверки конфигурации и просмотра тестового плана без ключей и сетевых обращений:

```powershell
.\.venv\Scripts\python.exe -m smartgate validate --config config/smartgate.example.toml
.\.venv\Scripts\python.exe -m smartgate preview --config config/smartgate.example.toml --offline --fixtures tests/fixtures
```

Offline-preview использует тестовые источники и симулированные TLS-результаты.

Offline-тесты запрещают внешние socket connections и не требуют secrets. Live-тесты имеют отдельный opt-in и используют только пустой изолированный профиль.

## Конфигурация

Пример: config/smartgate.example.toml. Каталог: config/services.toml. Реальную конфигурацию храните отдельно в config/smartgate.toml; она исключена из Git.

Нельзя сохранять API keys в TOML, документах, тестовых fixtures или Git. Рабочие credentials задаются через env.

## Обновление lock-файлов

```powershell
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --strip-extras -o requirements.lock pyproject.toml
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --strip-extras --extra dev -o requirements-dev.lock pyproject.toml
```

После изменения зависимостей проверить установку --require-hashes, полный offline suite, типы и сборку.

---

[Все документы](../README.md) · [Главная](../../../README.ru.md) · [Настройка Actions](../operations/actions.md) · [Помощь при сбое](../operations/runbook.md)
