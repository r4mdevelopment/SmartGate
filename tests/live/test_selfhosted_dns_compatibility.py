"""Opt-in disposable self-hosted contract; never runs against a production binding."""

import json
import os
import uuid
from contextlib import ExitStack
from pathlib import Path

import pytest

from smartgate.compatibility import selfhosted_live_target
from smartgate.dns.adguardhome import AdGuardHomeProvider
from smartgate.dns.credentials import required_value
from smartgate.dns.technitium import TechnitiumProvider
from smartgate.dns.transport import ApiClient
from smartgate.dns.types import target_fingerprint
from smartgate.dns.verify import DnsVerifier
from smartgate.models import DesiredRewrite, DnsCredentials

pytestmark = pytest.mark.live


def test_disposable_selfhosted_exact_subtree_and_cleanup():
    target = selfhosted_live_target(os.environ)
    folder = Path(".smartgate/compatibility/selfhosted") / target_fingerprint(target)
    journal_path = folder / "journal.json"
    if journal_path.exists():
        previous = json.loads(journal_path.read_text(encoding="utf-8"))
        if previous.get("phase") != "clean":
            pytest.fail("Previous isolated journal needs manual inspection before live writes")
    folder.mkdir(parents=True, exist_ok=True)
    journal = {"phase": "prepared", "intents": [], "confirmed": [], "cleanup_verified": False}

    def persist():
        temporary = journal_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(journal, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(journal_path)

    with ExitStack() as stack:
        if target.provider == "adguardhome":
            credentials = DnsCredentials(
                username=required_value("SMARTGATE_DNS_TEST_USERNAME", os.environ),
                password=required_value("SMARTGATE_DNS_TEST_PASSWORD", os.environ),
            )
            client = ApiClient(target.api_origin, "basic", credentials)
            provider = AdGuardHomeProvider(client, allow_reference_writes=True)
        else:
            credentials = DnsCredentials(
                api_key=required_value("SMARTGATE_DNS_TEST_API_KEY", os.environ)
            )
            client = ApiClient(target.api_origin, "bearer", credentials)
            provider = TechnitiumProvider(client, target.namespace, allow_reference_writes=True)
        stack.callback(provider.close)
        verifier = DnsVerifier(target.doh_url)
        stack.callback(verifier.close)
        before = provider.list_records()
        if target.provider == "adguardhome" and before:
            pytest.fail("Disposable AdGuard rewrite list must be empty")
        if target.provider == "technitium" and any(
            r.domain != target.namespace
            or not any('"type":"' + kind + '"' in r.content for kind in ("NS", "SOA"))
            for r in before
        ):
            pytest.fail("Disposable Technitium zone must contain only apex NS/SOA baseline")
        root = "smartgate-" + uuid.uuid4().hex[:12] + "." + target.namespace
        parent, child = DesiredRewrite(root, "8.8.8.8"), DesiredRewrite("child." + root, "1.1.1.1")
        confirmed = []
        checked = False
        persist()
        try:
            journal["intents"].append({"domain": parent.domain, "ipv4": parent.ipv4})
            persist()
            record = provider.create(parent)
            confirmed.append(record)
            journal["confirmed"].append(
                {"id": record.record_id, "domain": record.domain, "content": record.content}
            )
            persist()
            inherited = DesiredRewrite(child.domain, parent.ipv4)
            checks = verifier.verify((parent, inherited))
            assert len(checks) == 8 and all(c.ok for c in checks), (
                "Subtree or alternative DNS types differ"
            )
            journal["intents"].append({"domain": child.domain, "ipv4": child.ipv4})
            persist()
            record = provider.create(child)
            confirmed.append(record)
            journal["confirmed"].append(
                {"id": record.record_id, "domain": record.domain, "content": record.content}
            )
            persist()
            checks = verifier.verify((parent, child))
            assert len(checks) == 8 and all(c.ok for c in checks), (
                "Exact precedence or bypass checks differ"
            )
            checked = True
        finally:
            journal["phase"] = "cleanup"
            persist()
            for record in reversed(confirmed):
                # Never guess identity after an unconfirmed mutation.
                if record not in provider.list_records():
                    pytest.fail("Confirmed test identity changed; inspect isolated target manually")
                provider.delete(record)
            final = provider.list_records()
            journal["checked"] = checked
            journal["cleanup_verified"] = final == before
            journal["phase"] = "clean" if final == before else "inspection-required"
            persist()
            assert final == before, (
                "Unconfirmed intent remains; inspect isolated target before retry"
            )
