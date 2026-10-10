[SmartGate](../../../../README.ru.md) / [Документация](../../README.md) / AdGuard Home

[English](../../../en/providers/dns/adguardhome.md) · [Русский](adguardhome.md)

# AdGuard Home

Reference-адаптер для собственного DNS-сервера. Подключение и CRUD-контракт покрыты локальными HTTP-тестами; рабочий `--apply` заблокирован. Live-проверка собственного сервера ещё не выполнена.

## Подключение

Используйте [пример конфигурации](../../../../config/dns/adguardhome.example.toml). Требуются HTTPS API origin в `SMARTGATE_DNS_API_URL`, логин и пароль в `SMARTGATE_DNS_USERNAME` / `SMARTGATE_DNS_PASSWORD`, отдельный scope в `SMARTGATE_DNS_NAMESPACE` и HTTPS DoH endpoint в `SMARTGATE_DNS_DOH_URL`. Scope — имя выделенного сервера или его изолированной конфигурации; API rewrites здесь не поддерживает отдельный профиль.

Basic credentials отправляются только API origin. DoH проверяется отдельным клиентом без авторизации API. TLS-проверка обязательна, redirects и системные proxy отключены. Адаптер не устанавливает сервер и не меняет его глобальные настройки.

## Записи и ограничения владения

Официальный API возвращает domain/answer/enabled без постоянного record ID. Адаптер вычисляет ключ пары значений для поиска записи; этот ключ не доказывает, что удалённая и вновь созданная запись принадлежит SmartGate. Рабочие create/delete/replace поэтому запрещены даже при явном `--apply`.

IPv6 и CNAME остаются видимыми как посторонние записи. Дубли, отключённые правила и неподдерживаемый синтаксис домена блокируют чтение вместо потери правил при построении плана. Семантика поддоменов и A/AAAA/HTTPS/SVCB требует отдельной live-проверки.

Изолированный compatibility harness может явно разрешить reference CRUD непосредственно в адаптере. Add/delete используют POST; update использует PUT с target/update. Каждый результат подтверждается чтением. Неизвестный результат записи требует восстановления; автоматических повторов нет. Такая проверка не включает production apply и не меняет встроенные capabilities после успешного live run.

Контракт API 0.107 закреплён по [официальному OpenAPI](https://github.com/AdguardTeam/AdGuardHome/blob/336e9c9df4efa3fb5918e7f33827300805f46bf3/openapi/openapi.yaml), версия источника хранится в тестовой metadata.
