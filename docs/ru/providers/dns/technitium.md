[SmartGate](../../../../README.ru.md) / [Документация](../../README.md) / Technitium

[English](../../../en/providers/dns/technitium.md) · [Русский](technitium.md)

# Technitium DNS Server

Reference-адаптер API v15+ для чтения существующей выделенной authoritative zone и проверки CRUD-контракта. Production apply и автоматическое создание зон запрещены. Live-проверка ещё не выполнена.

## Подключение

[Пример конфигурации](../../../../config/dns/technitium.example.toml) использует HTTPS API origin `SMARTGATE_DNS_API_URL`, Bearer API token `SMARTGATE_DNS_API_KEY`, существующую canonical zone `SMARTGATE_DNS_NAMESPACE` и HTTPS DoH `SMARTGATE_DNS_DOH_URL`. Выдайте отдельному пользователю минимальные права на эту зону. Адаптер не создаёт пользователей, токены и зоны.

Token отправляется только в Authorization, никогда в URL или form. API и DoH используют разные клиенты. Включены TLS-проверка, запрет redirects/system proxy и ограничение запросов и времени.

## Scope и DNS-семантика

Authoritative zone — не универсальная rewrite-таблица. Создание родительской зоны для чужого сервиса может перехватить все его поддомены и другие типы DNS. Этот адаптер никогда не создаёт или удаляет зоны: все обращения явно указывают заранее подготовленную зону.

Для рабочего Smart DNS нужна отдельно проверенная политика обработки exact/subtree и A/AAAA/HTTPS/SVCB, включая сохранение остальных ответов. Если выбранная зона не обеспечивает эту семантику, назначение непригодно для такого сценария. Наличие VPS или API token само по себе эту задачу не решает.

## Записи и готовность

Все типы записей остаются видимыми, включая SOA, NS, AAAA, HTTPS и SVCB. Неподдерживаемый owner name, disabled record, повтор значения или другой zone в ответе блокируют работу. Ключ записи отражает значение и часть metadata; comments или marker не доказывают жизненный цикл и не дают права менять внешние записи. Поэтому `--apply` заблокирован.

Reference CRUD допускается только явно из изолированного compatibility harness: добавление A не перезаписывает RRset, PTR и создание reverse zone выключены. Replace сохраняет owner и metadata; результат каждой операции подтверждается чтением. Потерянный ответ требует восстановления без retry. Эти локальные проверки не заменяют live acceptance. Успех изолированного live harness не меняет встроенные capabilities и не снимает запрет production apply.

Контракт закреплён по [официальным APIDOCS](https://github.com/TechnitiumSoftware/DnsServer/blob/736bd562bea536f1dd93692ab70ad909ae03d16a/APIDOCS.md).
