import json
import os
from pathlib import Path

import pytest

from kira.legal_sources._common.errors import CorpusUnavailableError
from kira.legal_sources._common.s3_corpus import CorpusLoader


FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("LEGAL_CORPUS_LOCAL_DIR", raising=False)
    monkeypatch.delenv("LEGAL_CORPUS_BUCKET", raising=False)


def test_loads_from_local_dir(tmp_path: Path, monkeypatch):
    src = json.loads((FIXTURES / "bgb_subset.json").read_text(encoding="utf-8"))
    target = tmp_path / "gesetze"
    target.mkdir()
    (target / "bgb.json").write_text(json.dumps(src), encoding="utf-8")
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path))

    loader = CorpusLoader.from_env()
    corpus = loader.load_all()

    assert "bgb" in corpus
    assert corpus["bgb"].meta.abkuerzung == "BGB"


def test_local_dir_missing_raises_corpus_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGAL_CORPUS_LOCAL_DIR", str(tmp_path / "does-not-exist"))
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()


def test_no_env_set_raises_corpus_unavailable():
    with pytest.raises(CorpusUnavailableError):
        CorpusLoader.from_env().load_all()
