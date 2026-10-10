[SmartGate](../../../README.ru.md) / [Документация](../README.md) / Проверка совместимости DNS

[English](../../en/development/compatibility.md) · [Русский](compatibility.md)

# Проверка совместимости DNS

## NextDNS: изолированный профиль

Статус: базовая live-проверка пройдена 07.10.2026 на отдельном пустом профиле. Это проверка контракта тестового назначения; актуальная эксплуатационная приёмка описана в [проверках готовности](../project/acceptance.md).

Harness проверяет create/list/delete/replace, parent/child precedence и ответы A/AAAA/HTTPS через DoH конкретного тестового профиля. Подтверждённые обезличенные результаты сохранены в tests/fixtures/nextdns/compatibility.json и tests/fixtures/dns/compatibility.json.


## На этой странице

- [Изоляция](#изоляция)
- [Настройка env](#настройка-env)
- [Запуск](#запуск)
- [Журнал и очистка](#журнал-и-очистка)
- [Условие успеха](#условие-успеха)
- [Подтверждённый контракт](#подтверждённый-контракт)
- [Производственный адаптер](#производственный-адаптер)

## Изоляция

Используется отдельный пустой профиль. Рабочий профиль, системный DNS, hosts, proxy/VPN, router и browser DNS не изменяются.

Перед любой записью явно задаются ID тестового и защищённого рабочего профилей. Они должны различаться. Если рабочий DNS не использует NextDNS, вместо ID рабочего профиля явно указывается not-nextdns; пустое или неизвестное значение не допускается. Непустой профиль не модифицируется. Все API-запросы harness используют только ID тестового профиля.

## Настройка env

Переменные задаются только локально в терминале, из которого запускается тест:

- SMARTGATE_LIVE=1
- NEXTDNS_TEST_ACK=isolated-profile-only
- NEXTDNS_TEST_PROFILE_ID — ID отдельного пустого профиля.
- NEXTDNS_ACTIVE_PROFILE_ID — ID защищённого рабочего профиля; not-nextdns только при подтверждённом использовании другого DNS-сервиса.
- SMARTGATE_DNS_API_KEY — ключ аккаунта; вводить через secure prompt, не сохранять в script/config.

Пример безопасного ввода ключа в PowerShell:

```powershell
$smartGateKey = Read-Host "NextDNS API key" -AsSecureString
$env:SMARTGATE_DNS_API_KEY = [System.Net.NetworkCredential]::new("", $smartGateKey).Password
```

Настройки ID не изменяют DNS устройства.

## Запуск

```powershell
.\.venv\Scripts\python.exe -m pytest tests/live/test_nextdns_compatibility.py -m live -q
```

Без SMARTGATE_LIVE=1 тест пропускается. Skip не является подтверждением совместимости.

Тест создаёт временные rewrites в изолированном профиле: уникальное поддерево example.com для parent/child и cloudflare.com для проверки подавления реальных AAAA/HTTPS records. Он выполняет только API/DoH-запросы, не подключает устройство к этому профилю и не обращается по HTTP к выбранным тестовым IP.

## Журнал и очистка

Перед create намерение сохраняется в .smartgate/compatibility/<test-profile>/journal.json. В finally удаляются только записи с точным совпадением тестового намерения из первоначально пустого профиля. Неоднозначные совпадения и посторонние записи сохраняются.

evidence.json и journal.json содержат runtime IDs и исключены из Git. Ключ не сохраняется.

После аварийного завершения или cleanup_required проверьте отдельный тестовый профиль и локальный журнал. Не удаляйте рабочие записи или весь профиль без проверки. Неполный предыдущий журнал блокирует повторный test run.

После использования удалите SMARTGATE_DNS_API_KEY из окружения терминала:

```powershell
Remove-Item Env:SMARTGATE_DNS_API_KEY
```

## Условие успеха

API roundtrip и DNS assertions должны пройти, cleanup должен вернуть тестовый профиль в пустое состояние. Только после этого подтверждаются фактические API/DNS contracts.

## Подтверждённый контракт

GET /profiles/{profile_id}/rewrites возвращает data со списком записей. POST с name/content возвращает data с id, name, type и content. DELETE /profiles/{profile_id}/rewrites/{id} возвращает HTTP 204 без тела; удаление подтверждается повторным GET. Замена проверена через DELETE и POST. PATCH для rewrites не проверен.

Родительская подмена распространяется на поддомены; более конкретная дочерняя запись имеет приоритет. В проверенном профиле IPv4-подмена подавила реальные AAAA и HTTPS ответы cloudflare.com. После записи API DNS может некоторое время возвращать старое значение; тест наблюдает ответы до 60 секунд и блокирует продолжение, если они не сходятся.

Полный live roundtrip занял 58.70 секунды, итоговый GET подтвердил пустой список rewrites. Результаты относятся к проверенному профилю и дате. Для другого профиля требуется повторная проверка.

## Производственный адаптер

Дополнительный roundtrip использует NextDnsProvider и DnsVerifier с теми же env guards. Запуск: `python -m pytest tests/live/test_nextdns_production.py -m live -q`. Журнал и evidence сохраняются в ignored .smartgate/production-compatibility/. Проверка включает SVCB, а базовый harness остаётся независимым тестом API capability. Поведение адаптера описано в [DNS-документации](../architecture/dns.md).

Production roundtrip пройден 08.10.2026 (Europe/Moscow): fixture фиксирует 52.55 секунды самого roundtrip, исторический запуск pytest — 52.77 секунды. Подтверждены 20 DNS-проверок и очистка трёх оставшихся test-owned записей; итоговый список rewrites пуст. Обезличенный результат сохранён в tests/fixtures/nextdns/production-roundtrip.json. SVCB в проверенных ответах отсутствовал; наличие исходных SVCB records отдельно не характеризовалось.

---

[Все документы](../README.md) · [Главная](../../../README.ru.md) · [Настройка Actions](../operations/actions.md) · [Помощь при сбое](../operations/runbook.md)

## Self-hosted reference-адаптеры

HTTP-контракты AdGuard Home и Technitium проверяются offline, с закреплёнными официальными API-версиями. Это не live acceptance и не право включить production apply.

Опциональный `tests/live/test_selfhosted_dns_compatibility.py` требует `SMARTGATE_LIVE=1`, `SMARTGATE_DNS_TEST_ACK=isolated-target-only`, отдельные TEST_API_URL / TEST_NAMESPACE / TEST_DOH_URL и TEST_PROVIDER (`adguardhome` или `technitium`) в namespace `SMARTGATE_DNS_`. Задайте `SMARTGATE_DNS_PROTECTED_FINGERPRINT` действующего назначения; совпадение с тестовым блокирует записи. Credentials тестового сервера задаются отдельно через TEST_USERNAME/TEST_PASSWORD или TEST_API_KEY.

TEST_NAMESPACE должен быть canonical доменом тестового поддерева; для Technitium это также имя существующей зоны. AdGuard rewrite list должен быть пустым; Technitium использует существующую выделенную зону только с baseline apex NS/SOA. Harness сохраняет ignored намерения до mutation, подтверждённые результаты и cleanup. Неподтверждённая запись не присваивается автоматически; оставшийся intent требует ручного осмотра disposable target. Проверяются create/list/delete, exact/subtree precedence и A/AAAA/HTTPS/SVCB; replace покрыт offline contract tests, но этот self-hosted live harness его не выполняет. Без SMARTGATE_LIVE=1 тест пропускается. При включённом live режиме отсутствие обязательных guards или небезопасное назначение блокируют тест до записи. Успешный тест не изменяет registry capabilities: production apply остаётся запрещённым.

Изолированные NextDNS-тесты принимают общий `SMARTGATE_DNS_API_KEY` с временной проверяемой legacy-совместимостью. `NEXTDNS_TEST_PROFILE_ID` и `NEXTDNS_ACTIVE_PROFILE_ID` относятся к этому конкретному adapter harness; рабочий управляемый профиль использовать для write-тестов запрещено.


Для исключения рабочего API harness также требует `SMARTGATE_DNS_PROTECTED_PROVIDER`, `SMARTGATE_DNS_PROTECTED_API_URL` и для Technitium `SMARTGATE_DNS_PROTECTED_NAMESPACE`. Сравнивается именно назначение записи: API origin для AdGuard Home, API origin и zone для Technitium. Адрес DoH и локальное имя instance не создают изоляцию; регистр hostname, порт HTTPS по умолчанию и завершающий slash нормализуются. Один fingerprint полной конфигурации не заменяет эту проверку.
