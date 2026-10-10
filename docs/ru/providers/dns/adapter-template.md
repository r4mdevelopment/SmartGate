[SmartGate](../../../../README.ru.md) / [Документация](../../README.md) / Новый DNS-адаптер

[English](../../../en/providers/dns/adapter-template.md) · [Русский](adapter-template.md)

# Новый DNS-адаптер

Реализуйте `DnsProvider`: `list_records`, `create`, `delete`, `replace`. List должен сохранять посторонние записи или блокировать неоднозначный ответ, а не скрывать его. Mutation подтверждается read-back; неизвестный результат требует RecoveryRequired и не повторяется автоматически.

## Минимальная граница

```python
from smartgate.models import DesiredRewrite, DnsRecord


class ExampleDnsProvider:
    def list_records(self) -> tuple[DnsRecord, ...]:
        # Decode the server's complete scoped snapshot, preserving foreign records.
        raise NotImplementedError

    def create(self, record: DesiredRewrite) -> DnsRecord:
        # Validate public IPv4, issue one mutation, confirm exact read-back.
        raise NotImplementedError

    def delete(self, record: DnsRecord) -> None:
        # Confirm the before identity and confirm its absence after deletion.
        raise NotImplementedError

    def replace(self, before: DnsRecord, after: DesiredRewrite) -> DnsRecord:
        # Keep the domain; document atomicity and uncertain intermediate states.
        raise NotImplementedError
```

Это эскиз контракта, не зарегистрированный рабочий провайдер. Добавьте типизированную конфигурацию auth/connection/verify и явный binding в `dns/registry.py`. Возвращайте `DnsBundle` с DnsTarget, встроенными DnsCapabilities и endpoint-bound verifier. Credentials разрешаются только через нужные env; transport ограничивает origin, TLS, redirects, размер, время и число запросов.

## Проверка возможностей

| Свойство | Что подтвердить |
| --- | --- |
| Identity | Постоянный ID или механизм владения; одинаковое значение после delete/recreate не доказывает ownership |
| Scope | Изоляция назначения, foreign records, duplicates, concurrent writer |
| Replace | Atomic или delete/create, read-back, recovery после потерянного ответа |
| DNS | A, AAAA, HTTPS, SVCB, exact/subtree precedence, исключения |
| State | Fingerprint API origin/scope/DoH; drift блокирует план, apply, recovery и rollback |

Начинайте с `production_apply=False`. Контрактные тесты используют имитированный HTTP; live-проверки требуют отдельного пустого назначения, явного разрешения записей и cleanup ledger. Таблица готовности должна различать реализовано, contract-tested и live-verified. Пользовательский флаг в TOML не может повысить capabilities.

Reference-образцы: [AdGuard Home](adguardhome.md) и [Technitium](technitium.md); production-образец — [NextDNS](nextdns.md). Общий набор `tests/contract/test_dns_provider_contract.py` проверяет CRUD read-back, сохранение посторонней записи, stale precondition и запрет непубличного IPv4. Ошибки транспорта, неоднозначность, DNS-семантика и recovery проверяются дополнительно профильными contract/unit/integration тестами. Сам live harness не повышает capabilities в registry.
