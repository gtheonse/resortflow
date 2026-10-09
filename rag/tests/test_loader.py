"""T036 — corpus loader tests."""

from __future__ import annotations

import pytest

from content.loader import KNOWN_DOMAINS, KNOWN_TENANTS, CorpusError, load_corpus, load_domain


@pytest.mark.parametrize("tenant_id", ["MGV", "MHB", "mgv", "mhb"])
def test_load_corpus_all_domains(tenant_id: str) -> None:
    corpus = load_corpus(tenant_id)
    assert set(corpus.keys()) == set(KNOWN_DOMAINS)
    for domain, data in corpus.items():
        assert data["domain"] == domain
        assert data["tenant_id"].upper() == tenant_id.upper()


def test_all_known_tenants_load() -> None:
    for tenant_id in KNOWN_TENANTS:
        load_corpus(tenant_id)


def test_missing_file_raises(tmp_path, monkeypatch) -> None:
    import content.loader as loader_module

    monkeypatch.setattr(loader_module, "CORPUS_ROOT", tmp_path)
    with pytest.raises(CorpusError):
        load_domain("MGV", "dining")


def test_tenant_mismatch_raises(tmp_path, monkeypatch) -> None:
    import content.loader as loader_module

    monkeypatch.setattr(loader_module, "CORPUS_ROOT", tmp_path)
    tenant_dir = tmp_path / "mgv"
    tenant_dir.mkdir()
    (tenant_dir / "dining.yaml").write_text(
        "tenant_id: MHB\ndomain: dining\nvenues: []\n", encoding="utf-8"
    )
    with pytest.raises(CorpusError, match="does not match"):
        load_domain("MGV", "dining")


def test_domain_mismatch_raises(tmp_path, monkeypatch) -> None:
    import content.loader as loader_module

    monkeypatch.setattr(loader_module, "CORPUS_ROOT", tmp_path)
    tenant_dir = tmp_path / "mgv"
    tenant_dir.mkdir()
    (tenant_dir / "dining.yaml").write_text(
        "tenant_id: MGV\ndomain: rooms\nvenues: []\n", encoding="utf-8"
    )
    with pytest.raises(CorpusError, match="does not match"):
        load_domain("MGV", "dining")
