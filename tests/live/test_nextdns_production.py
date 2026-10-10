"""Production adapter roundtrip on an explicitly isolated, initially empty profile."""

import json
import os
import time
import uuid
from pathlib import Path

import pytest

from smartgate.compatibility import live_settings
from smartgate.dns.nextdns import NextDnsProvider
from smartgate.dns.verify import DnsVerifier
from smartgate.errors import SafetyBlocked
from smartgate.models import DesiredRewrite

pytestmark = pytest.mark.live


def persist(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def test_production_nextdns_roundtrip():
    settings = live_settings(os.environ)
    folder = (
        Path(__file__).parents[2]
        / ".smartgate"
        / "production-compatibility"
        / settings.test_profile_id
    )
    path = folder / "journal.json"
    if path.exists() and json.loads(path.read_text(encoding="utf-8")).get("phase") != "clean":
        raise SafetyBlocked("Previous production roundtrip needs manual inspection")
    api = NextDnsProvider(settings.test_profile_id, settings.api_key)
    verifier = DnsVerifier("https://dns.nextdns.io/" + settings.test_profile_id)
    journal = {"phase": "prepared", "intents": [], "cleanup": []}
    evidence = {"checked": False, "api": [], "dns": []}
    started = time.monotonic()
    root = "smartgate-" + uuid.uuid4().hex[:12] + ".example.com"

    def intend(desired):
        journal["intents"].append({"domain": desired.domain, "ipv4": desired.ipv4})
        persist(path, journal)

    def create(desired):
        intend(desired)
        record = api.create(desired)
        evidence["api"].append(
            {
                "method": "create",
                "id": record.record_id,
                "domain": record.domain,
                "content": record.content,
            }
        )
        return record

    def verify(desired):
        checks = verifier.verify((desired,))
        evidence["dns"].extend(
            [
                {
                    "domain": c.domain,
                    "qtype": c.qtype,
                    "answers": c.answers,
                    "expected": c.expected,
                    "ok": c.ok,
                    "reason": c.reason,
                    "checked_at": c.checked_at.isoformat(),
                }
                for c in checks
            ]
        )
        assert len(checks) == 4 and all(c.ok for c in checks), (
            "Profile DNS did not confirm production rewrite"
        )

    try:
        if api.list_records():
            raise SafetyBlocked("Production roundtrip requires an empty disposable profile")
        journal["phase"] = "applying"
        persist(path, journal)
        parent = create(DesiredRewrite(root, "8.8.8.8"))
        verify(DesiredRewrite(root, "8.8.8.8"))
        verify(DesiredRewrite("child." + root, "8.8.8.8"))
        child = create(DesiredRewrite("child." + root, "1.1.1.1"))
        verify(DesiredRewrite(child.domain, child.content))
        after = DesiredRewrite(root, "8.8.4.4")
        intend(after)
        changed = api.replace(parent, after)
        assert changed.record_id != parent.record_id
        evidence["api"].append(
            {
                "method": "replace",
                "id": changed.record_id,
                "domain": changed.domain,
                "content": changed.content,
            }
        )
        verify(after)
        cloud = create(DesiredRewrite("cloudflare.com", "8.8.8.8"))
        verify(DesiredRewrite(cloud.domain, cloud.content))
        evidence["checked"] = True
    finally:
        try:
            if journal["intents"]:
                actual = api.list_records()
                for intent in journal["intents"]:
                    matches = [
                        r
                        for r in actual
                        if (r.domain, r.content) == (intent["domain"], intent["ipv4"])
                    ]
                    if len(matches) > 1:
                        raise SafetyBlocked("Ambiguous intent; records preserved")
                    for record in matches:
                        api.delete(record)
                        journal["cleanup"].append(record.record_id)
                        persist(path, journal)
                if api.list_records():
                    raise SafetyBlocked("Unexpected remaining records preserved")
            journal["phase"] = "clean"
            persist(path, journal)
        except Exception:
            journal["phase"] = "cleanup_required"
            evidence["checked"] = False
            persist(path, journal)
            raise SafetyBlocked("Production cleanup incomplete; inspect isolated profile") from None
        finally:
            evidence["duration_s"] = round(time.monotonic() - started, 2)
            evidence["checked_at"] = verifier._now().isoformat()
            persist(folder / "evidence.json", evidence)
            api.close()
            verifier.close()
