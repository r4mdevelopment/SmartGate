[SmartGate](../../README.ru.md) / Документация

[English](../en/README.md) · [Русский](README.md)

# Документация SmartGate

Выберите задачу: настроить контроллер, проверить доступ, восстановить запуск или подключить собственный relay.

## Начать пользоваться

| Документ | Что вы найдёте |
| --- | --- |
| [Первый запуск](getting-started/quick-start.md) | Пошаговая настройка новой установки |
| [GitHub Actions](operations/actions.md) | Secrets, ручные запуски, расписание и красные Deployments |
| [Приватность](operations/privacy.md) | Шифрование, ключ, безопасный fork и подробные отчёты |
| [CLI и отчёты](operations/cli.md) | Команды, параметры, статусы и коды завершения |
| [Проверка устройства](getting-started/device-check.md) | Подключение DNS на Android, Windows, Linux, macOS и iPhone; сценарии ChatGPT |
| [Приёмка](project/acceptance.md) | Готовность MVP, подтверждённые проверки и ограничения |
| [Планы развития](project/roadmap.md) | Что готово и какие доработки имеют приоритет |

## Эксплуатация и расширение

| Документ | Что вы найдёте |
| --- | --- |
| [Причины ошибок в Summary](operations/diagnostics.md) | Безопасные коды и следующие действия |
| [Действия при сбое](operations/runbook.md) | Blocked, degraded, pending и восстановление |
| [Смена ключа](operations/key-rotation.md) | Проверка, encrypted backup и подтверждённый переход |
| [Проверки восстановления](operations/recovery-drills.md) | Проверки обновления и backup на отдельном профиле |
| [Смена relay](operations/migration.md) | Переход с GeoHide на собственный manifest и возврат |
| [Relay-провайдеры](providers/relays.md) | Источники GeoHide и формат собственного manifest |
| [Синхронизация DNS](architecture/reconcile.md) | План изменений, apply, recovery и rollback |

## Устройство контроллера

| Документ | Что вы найдёте |
| --- | --- |
| [Архитектура](architecture/architecture.md) | Компоненты, интерфейсы и границы системы |
| [Выбор маршрутов](architecture/routing.md) | Приоритеты, свежесть данных и сохранение маршрута |
| [TLS/SNI-проверки](architecture/health.md) | Проверка endpoint, сроки и бюджет кандидатов |
| [DNS и DoH](architecture/dns.md) | API read-back и профильные DNS-проверки |
| [Состояние и журнал](operations/state.md) | Ownership, CAS, Git branch и восстановление |

## Разработка и проверки

| Документ | Что вы найдёте |
| --- | --- |
| [Локальная разработка](development/README.md) | Установка, offline-тесты и проверки качества |
| [Совместимость DNS](development/compatibility.md) | Изолированные live-тесты и очистка |
| [Архитектурные решения](architecture/decisions.md) | Обоснование ключевых решений проекта |

> [!TIP]
> Для первого знакомства начните с [главного README](../../README.ru.md), затем переходите к [настройке Actions](operations/actions.md). Технические подробности можно изучать по мере необходимости.

---

[Главная](../../README.ru.md) · [Начать настройку](operations/actions.md) · [Помощь при сбое](operations/runbook.md)

## DNS-провайдеры

[Выбор и готовность](providers/dns/README.md) · [NextDNS](providers/dns/nextdns.md) · [AdGuard Home](providers/dns/adguardhome.md) · [Technitium](providers/dns/technitium.md) · [Новый адаптер](providers/dns/adapter-template.md)

## Каталог сервисов

[Включить готовый набор или добавить свой сервис](providers/services.md).

## Релизы и обновления

[Обновление настроенного форка](operations/upgrading.md) · [Подготовка релиза](project/releases.md) · [Changelog](../../CHANGELOG.md)
