import pytest

from smartgate.catalog import load_catalog, normalize_domain, suffix_matches
from smartgate.errors import ConfigError


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("CHATGPT.COM.", "chatgpt.com"), ("пример.рф", "xn--e1afmkfd.xn--p1ai")],
)
def test_hostname_normalization(raw, expected):
    assert normalize_domain(raw) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://chatgpt.com",
        "*.openai.com",
        "a_b.com",
        "example.com:443",
        "",
        ".example.com",
        "example..com",
        "-example.com",
        "example-.com",
        "localhost",
        "1.2.3.4",
        "example.com..",
        "a" * 64 + ".com",
    ],
)
def test_unsafe_hostname_is_rejected(value):
    with pytest.raises(ConfigError):
        normalize_domain(value)


def test_suffix_respects_dns_label_boundary():
    assert suffix_matches("auth.openai.com", "openai.com")
    assert suffix_matches("openai.com", "openai.com")
    assert not suffix_matches("evilopenai.com", "openai.com")


def test_empty_enabled_service_is_rejected(tmp_path):
    path = tmp_path / "services.toml"
    path.write_text("[services.empty]\nenabled = true\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_catalog(path)


def test_conflicting_service_policies_are_rejected(tmp_path):
    path = tmp_path / "services.toml"
    path.write_text(
        '[services.one]\ninclude_suffixes=["openai.com"]\nprovider_order=["a"]\n'
        '[services.two]\ninclude_suffixes=["auth.openai.com"]\nprovider_order=["b"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_catalog(path)


def test_exclusion_does_not_match_unrelated_sibling(tmp_path):
    path = tmp_path / "services.toml"
    path.write_text(
        '[services.chatgpt]\ninclude_suffixes=["openai.com"]\n'
        'exclude_suffixes=["mail.openai.com"]\nprovider_order=["a"]\n'
        'required_domains=["auth.openai.com"]\n',
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    assert catalog.select("auth.openai.com").id == "chatgpt"
    assert catalog.select("mail.openai.com") is None
    assert catalog.select("x.mail.openai.com") is None
    assert catalog.select("evilopenai.com") is None
    with pytest.raises(ConfigError):
        catalog.validate_publication("openai.com")


def test_required_domain_cannot_be_excluded(tmp_path):
    path = tmp_path / "services.toml"
    path.write_text(
        '[services.chatgpt]\ninclude_suffixes=["openai.com"]\n'
        'exclude_suffixes=["auth.openai.com"]\nrequired_domains=["auth.openai.com"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_catalog(path)


def test_subtree_publication_requires_explicit_acceptance(tmp_path):
    path = tmp_path / "services.toml"
    path.write_text(
        '[services.chatgpt]\ninclude_suffixes=["chatgpt.com"]\nprovider_order=["a"]\n',
        encoding="utf-8",
    )
    catalog = load_catalog(path)
    with pytest.raises(ConfigError):
        catalog.validate_publication("chatgpt.com")
