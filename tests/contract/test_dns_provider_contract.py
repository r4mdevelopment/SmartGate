import json
from dataclasses import replace

import httpx
import pytest

from smartgate.errors import SafetyBlocked
from smartgate.models import DesiredRewrite
from tests.contract.test_adguardhome import backend as adguard_backend
from tests.contract.test_nextdns import provider as nextdns_provider
from tests.contract.test_technitium import backend as technitium_backend


@pytest.fixture(params=["nextdns", "adguardhome", "technitium"])
def adapter(request):
    if request.param == "adguardhome":
        return adguard_backend()[0]
    if request.param == "technitium":
        return technitium_backend()[0]
    records = []
    count = [0]

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": records})
        if req.method == "POST":
            count[0] += 1
            row = {"id": "id-" + str(count[0]), "type": "A", **json.loads(req.content)}
            records.append(row)
            return httpx.Response(200, json={"data": row})
        identifier = req.url.path.rsplit("/", 1)[1]
        records[:] = [r for r in records if r["id"] != identifier]
        return httpx.Response(204)

    return nextdns_provider(handler)


def test_shared_create_replace_delete_readback_preserves_unrelated_record(adapter):
    foreign = adapter.create(DesiredRewrite("other.isolated.example", "9.9.9.9"))
    old = adapter.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
    assert old in adapter.list_records()
    new = adapter.replace(old, DesiredRewrite("probe.isolated.example", "1.1.1.1"))
    assert new in adapter.list_records() and old not in adapter.list_records()
    adapter.delete(new)
    assert adapter.list_records() == (foreign,)


def test_shared_stale_precondition_blocks_mutation(adapter):
    record = adapter.create(DesiredRewrite("probe.isolated.example", "8.8.8.8"))
    with pytest.raises(SafetyBlocked):
        adapter.delete(replace(record, content="1.1.1.1"))
    assert adapter.list_records() == (record,)


def test_shared_nonpublic_rewrite_cannot_be_created(adapter):
    with pytest.raises(SafetyBlocked):
        adapter.create(DesiredRewrite("probe.isolated.example", "127.0.0.1"))
    assert adapter.list_records() == ()
