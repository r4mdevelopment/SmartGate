[SmartGate](../../../README.ru.md) / [Документация](../README.md) / Командный интерфейс

[English](../../en/operations/cli.md) · [Русский](cli.md)

# Командный интерфейс

CLI доступен как `python -m smartgate` и как `smartgate` после установки пакета. Команды контроллера принимают `--config PATH`; у `report-decrypt` вместо него отдельные параметры файла и привязки отчёта. CLI не меняет DNS-настройки устройства.


## На этой странице

- [Команды](#команды)
- [Offline-проверка](#offline-проверка)
- [Live preview](#live-preview)
- [Первый apply](#первый-apply)
- [Отчёты и коды завершения](#отчёты-и-коды-завершения)
- [Приёмка без изменений DNS](#приёмка-без-изменений-dns)
- [Обновление состояния](#обновление-состояния)
- [Явное шифрование состояния](#явное-шифрование-состояния)

## Команды

| Команда | Назначение |
| --- | --- |
| `validate` | Проверить TOML и каталог без сети и ключей |
| `preview` | Получить источники, проверить TLS и показать свежий план |
| `sync` | Построить план; применить только с `--apply` |
| `doctor` | Прочитать профиль, проверить маршруты и DNS для owned записей |
| `state-encrypt` | Явно зашифровать существующее Git-состояние без изменений DNS |
| `report-decrypt` | Локально расшифровать artifact в новый файл без сетевых обращений |
| `recover` | Сопоставить сохранённый журнал с профилем |
| `rollback --run-id ID` | Откатить последний сохранённый журнал с указанным ID |
| `state-upgrade` | Проверить переход legacy ledger; с `--apply` обновить только формат состояния |

Без `--apply` нет DNS-записей и сохранения state. При pending journal просмотр возвращает статус partial, а `sync --apply` выполняет только recovery; новый план потребует отдельного запуска. Такой порядок отделяет восстановление от новых изменений маршрутов.

`sync.enabled=false` блокирует DNS apply, включая recovery и rollback. `state-upgrade --apply` и `state-encrypt --apply` меняют только ledger и не требуют включения DNS apply. Для DNS-записей нужны одновременно разрешение в конфигурации и `--apply`. Команды `preview`, `doctor` и `validate` не допускают apply.

## Offline-проверка

```console
python -m smartgate validate --config config/smartgate.example.toml
python -m smartgate preview --config config/smartgate.example.toml --offline --fixtures tests/fixtures --report-dir .smartgate/reports
```

Offline использует curated hosts fixture, пустой DNS/state и явно симулированные TLS-результаты. Источник переоценивается на время демонстрации. Эти результаты показывают поведение программы и не подтверждают доступность IP. Отчёт содержит `offline: true`; offline apply запрещён. Fixture одинаково подставляется вместо каждого настроенного relay, чтобы проверить конфигурацию без сети.

## Live preview

Выберите [пример DNS-адаптера](../../../config/dns), скопируйте его в локальный `config/smartgate.toml` и задайте необходимые переменные из его руководства. Пути в TOML считаются относительно конфигурационного файла: при переносе примера из `config/dns/` в `config/` замените `catalog_path` на `"services.toml"`, а `[state].path` — на `"../.smartgate/state.json"`. Затем выполните `validate` с новым файлом. Не сохраняйте credentials в TOML.

Пример для адаптера NextDNS в PowerShell без вывода ключа:

```powershell
$env:SMARTGATE_DNS_PROFILE_ID = Read-Host 'NextDNS profile ID'
$env:SMARTGATE_DNS_API_KEY = [Net.NetworkCredential]::new('', (Read-Host 'NextDNS API key' -AsSecureString)).Password
python -m smartgate preview --config config/smartgate.toml --report-dir .smartgate/reports
```

Preview читает API выбранного DNS-провайдера и state, получает relay-источники и открывает проверяемые TLS-соединения непосредственно к IP. Оно не требует переключения системного DNS. `doctor` дополнительно обращается к DoH выбранного назначения; это отдельные HTTPS-запросы.

## Первый apply

Используйте допущенный к production apply адаптер и отдельное пустое DNS-назначение, проверьте каталог доменов и preview. Проверьте также лимиты `max_mutations`, `max_records` и причины блокировки. Если первый план превышает `max_mutations=20`, измените предел только после просмотра конкретных операций и повторите preview; после первого успешного применения верните обычный предел. Затем установите `sync.enabled=true` в своём конфиге:

```console
python -m smartgate sync --config config/smartgate.toml --apply --bootstrap --report-dir .smartgate/reports
python -m smartgate sync --config config/smartgate.toml --apply --require-no-change --report-dir .smartgate/reports
```

`--bootstrap` требуется только для явной инициализации пустого профиля и отсутствующего ownership. После первого запуска уберите флаг. Если кандидаты не проходят TLS, bootstrap может сохранить source/cursor для следующей ограниченной проверки, но DNS-записи не создаёт и возвращает degraded.

Без этого сохранения ограниченный batch мог бы проверять одни и те же неработающие IP. Обычный preview курсоры не изменяет. Сохранённые snapshots не продлевают свою свежесть при ошибке загрузки; после 24 часов они не используются для выбора маршрутов. Последний корректный snapshot остаётся базой сравнения качества следующего источника.

Для профиля, обслуживаемого Actions, локальная команда тоже обязана использовать GitBranchStateStore:

```powershell
$env:GITHUB_REPOSITORY = 'owner/SmartGate'
$env:GITHUB_TOKEN = [Net.NetworkCredential]::new('', (Read-Host 'GitHub token' -AsSecureString)).Password
$env:SMARTGATE_STATE_ENCRYPTION_KEY = [Net.NetworkCredential]::new('', (Read-Host 'State encryption key' -AsSecureString)).Password
$env:GITHUB_RUN_ID = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
$env:GITHUB_RUN_ATTEMPT = '1'
python -m smartgate preview --config config/smartgate.actions.toml --state-provider git_branch --report-dir .smartgate/reports
```

Задайте также DNS-переменные той же установки. Ключ должен совпадать с Environment Secret. `GITHUB_RUN_ID` и `GITHUB_RUN_ATTEMPT` нужны даже локально для привязки encrypted report; сохраните их значения для `report-decrypt`, выбирайте новый положительный числовой ID для каждого локального запуска. Токен должен разрешать чтение репозитория и запись state при apply. Независимый локальный ledger для того же профиля использовать нельзя.

## Отчёты и коды завершения

`--report-dir PATH` сохраняет отчёты. В Actions и live Git state публичные `report.json`/`report.md` содержат статус и агрегаты; подробный JSON сохраняется в `report.enc.json`, а полный Markdown не публикуется. `--private-report` включает такую же проекцию явно. Ошибки конфигурации и инициализации также показываются без произвольных строк.

Обычные локальные отчёты могут содержать домены и IP: сохраняйте их вне Git. Подробности источников, проверок, плана и `journal_run_id` доступны в расшифрованном artifact. [Ключ и локальная расшифровка](privacy.md#чтение-подробного-отчёта).

| Код | Значение |
| --- | --- |
| 0 | Preview, no_change или applied |
| 2 | Ошибка аргументов, конфигурации или необходимых env |
| 3 | Degraded: источник, TLS или DNS не подтверждают полную работоспособность |
| 4 | Блокировка безопасности, ownership или CAS |
| 5 | Внешняя ошибка до подтверждённых mutations |
| 6 | Partial, pending journal или ошибка после возможной записи |

Отсутствие пригодного required-domain не даёт код 0. Успех DNS/TLS-проверок не заменяет проверку приложения с устройства пользователя.


## Приёмка без изменений DNS

Для живого `sync --apply` добавьте `--require-no-change`. Свежий план с любой DNS-операцией и незавершённый журнал блокируются до записей. Флаг несовместим с bootstrap, offline, read-only командами и recovery/rollback. При нулевом плане выполняются проверки DNS и API; наблюдения в state могут обновляться. В Actions этому соответствует input `require_no_change=true`.

## Обновление состояния

`state-upgrade --config <path>` проверяет переход legacy ledger к target-bound формату. Добавление `--apply` выполняет только изменение состояния с backup/CAS и read-back, без DNS mutations. Команда допускается только в live режиме, без bootstrap. Незавершённый журнал сначала восстановите прежней конфигурацией. Для основной пользовательской установки используйте одноимённый ручной command в Actions; локальный CLI — дополнительный способ эксплуатации.

## Явное шифрование состояния

`state-encrypt --config <path>` проверяет существующий bound ledger через API без записи. `--apply` переводит тот же документ в encrypted Git storage с CAS/read-back; DNS не изменяется. Требуются закрытый журнал, Git provider и ключ. Bootstrap, offline, takeover, rollback ID и `--require-no-change` запрещены. Schema 1 сначала переводится прежним контроллером в schema 2. [Полный порядок](privacy.md#перевод-существующей-установки).

---

[Все документы](../README.md) · [Главная](../../../README.ru.md) · [Настройка Actions](actions.md) · [Помощь при сбое](runbook.md)


## Смена ключа состояния

`state-rotate` требует живого привязанного Git-состояния, `SMARTGATE_STATE_NEW_ENCRYPTION_KEY` и выключенного автоприменения. Без `--apply` выполняется только проверка; с `--apply` создаётся backup и перешифровывается журнал без DNS-записей. Bootstrap, offline, rollback ID и флаг приёмки без изменений не допускаются. [Порядок перехода](key-rotation.md).
