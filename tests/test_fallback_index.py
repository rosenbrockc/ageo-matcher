"""Tests for FAISS-free lexical fallback index."""

from __future__ import annotations

import pickle

import msgpack
import numpy as np
import pytest

from sciona.cli import _load_semantic_index
from sciona.indexer.fallback_index import LexicalSemanticIndex
from sciona.types import Declaration, Prover


def _write_msgpack_index(tmp_path):
    data = {
        0: {
            "name": "fft_forward",
            "type_signature": "ndarray -> ndarray",
            "docstring": "Compute one-sided FFT spectrum from a signal.",
            "conceptual_summary": "frequency transform",
            "source_lib": "dsp",
            "prover": "python",
            "raw_code": "",
        },
        1: {
            "name": "peak_pick",
            "type_signature": "ndarray -> int",
            "docstring": "Pick dominant spectral bin index.",
            "conceptual_summary": "argmax peak detection",
            "source_lib": "dsp",
            "prover": "python",
            "raw_code": "",
        },
    }
    (tmp_path / "declarations.msgpack").write_bytes(
        msgpack.packb(data, use_bin_type=True)
    )


def test_load_and_search_msgpack(tmp_path):
    _write_msgpack_index(tmp_path)
    idx = LexicalSemanticIndex.load(tmp_path)

    hits = idx.search_by_embedding("fft spectrum", k=2)
    assert hits
    assert hits[0][0].name == "fft_forward"
    assert hits[0][1] > 0

    type_hits = idx.search_by_type("ndarray -> int", k=2)
    assert type_hits
    assert type_hits[0].name == "peak_pick"
    assert idx.get_declaration("peak_pick") is not None


def test_load_legacy_pickle(tmp_path):
    decls = {
        0: Declaration(
            name="binary_search",
            type_signature="list[int] -> int -> int",
            docstring="Find target in sorted array",
            prover=Prover.PYTHON,
        )
    }
    with open(tmp_path / "declarations.pkl", "wb") as f:
        pickle.dump(decls, f)

    idx = LexicalSemanticIndex.load(tmp_path)
    hits = idx.search_by_embedding("sorted array target", k=1)
    assert hits
    assert hits[0][0].name == "binary_search"


def test_cli_loader_falls_back_when_faiss_missing(tmp_path, monkeypatch):
    _write_msgpack_index(tmp_path)

    from sciona.indexer.faiss_store import FAISSStore

    def _raise_missing(_directory):
        raise ModuleNotFoundError("No module named 'faiss'")

    monkeypatch.setattr(FAISSStore, "load", staticmethod(_raise_missing))

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"

    idx, mode = _load_semantic_index(tmp_path, _Cfg())
    assert mode == "lexical_fallback"
    hits = idx.search_by_embedding("fft", k=1)
    assert hits and hits[0][0].name == "fft_forward"


def test_cli_loader_can_force_lexical_backend(tmp_path, monkeypatch):
    _write_msgpack_index(tmp_path)

    from sciona.indexer.faiss_store import FAISSStore

    def _should_not_run(_directory):
        raise AssertionError("FAISS loader should not be called in forced lexical mode")

    monkeypatch.setattr(FAISSStore, "load", staticmethod(_should_not_run))

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"
        semantic_index_backend = "lexical"

    idx, mode = _load_semantic_index(tmp_path, _Cfg())
    assert mode == "lexical_forced"
    hits = idx.search_by_embedding("fft", k=1)
    assert hits and hits[0][0].name == "fft_forward"


def test_cli_loader_uses_postgres_backend_without_loading_local_index(
    tmp_path, monkeypatch
):
    from sciona.indexer.faiss_store import FAISSStore
    from sciona.provider_runtime import PostgresSemanticIndex

    monkeypatch.setattr(
        FAISSStore,
        "load",
        staticmethod(lambda _directory: (_ for _ in ()).throw(AssertionError("FAISS loaded"))),
    )

    class _Cfg:
        semantic_index_backend = "postgres"

    index, mode = _load_semantic_index(tmp_path, _Cfg())

    assert isinstance(index, PostgresSemanticIndex)
    assert mode == "postgres"


def test_cli_loader_forced_faiss_reraises_missing_backend(tmp_path, monkeypatch):
    _write_msgpack_index(tmp_path)

    from sciona.indexer.faiss_store import FAISSStore

    def _raise_missing(_directory):
        raise ModuleNotFoundError("No module named 'faiss'")

    monkeypatch.setattr(FAISSStore, "load", staticmethod(_raise_missing))

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"
        semantic_index_backend = "faiss"

    with pytest.raises(ModuleNotFoundError, match="faiss"):
        _load_semantic_index(tmp_path, _Cfg())


def test_cli_loader_reraises_non_faiss_import_errors(tmp_path, monkeypatch):
    _write_msgpack_index(tmp_path)

    from sciona.indexer.faiss_store import FAISSStore

    def _raise_other(_directory):
        raise ImportError("broken_dependency")

    monkeypatch.setattr(FAISSStore, "load", staticmethod(_raise_other))

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"
        embedding_backend = "fastembed"

    with pytest.raises(ImportError, match="broken_dependency"):
        _load_semantic_index(tmp_path, _Cfg())


def test_cli_loader_prefers_stored_embedding_backend_metadata(tmp_path, monkeypatch):
    from sciona.indexer.models import IndexMetadata
    from sciona.types import Prover

    _write_msgpack_index(tmp_path)

    class _FakeStore:
        _declarations = {
            0: Declaration(
                name="fft_forward",
                type_signature="ndarray -> ndarray",
                prover=Prover.PYTHON,
            )
        }
        _metadata = IndexMetadata(
            num_entries=1,
            prover=Prover.PYTHON,
            source_lib="dsp",
            embedding_model="stored-fastembed-model",
            embedding_backend="fastembed",
        )

        def search(self, query_vec, k=10):
            del query_vec, k
            return []

    class _FakeEmbedder:
        def __init__(self):
            self.backend = "fastembed"
            self.model_name = "stored-fastembed-model"

        def embed(self, text):
            del text
            return np.array([1.0], dtype=np.float32)

    from sciona.indexer.faiss_store import FAISSStore
    import sciona.indexer.embedder as embedder_mod

    monkeypatch.setattr(FAISSStore, "load", staticmethod(lambda _directory: _FakeStore()))
    monkeypatch.setattr(
        embedder_mod,
        "create_embedder",
        lambda backend, model_name: _FakeEmbedder()
        if (backend, model_name) == ("fastembed", "stored-fastembed-model")
        else (_ for _ in ()).throw(AssertionError((backend, model_name))),
    )

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"
        embedding_backend = "unixcoder"

    idx, mode = _load_semantic_index(tmp_path, _Cfg())
    assert mode == "faiss"
    assert idx.get_declaration("fft_forward") is not None


def test_cli_loader_merges_manifest_index_when_present(tmp_path, monkeypatch):
    from sciona.indexer.models import IndexMetadata

    _write_msgpack_index(tmp_path)
    manifest_dir = tmp_path / ".sciona"
    manifest_dir.mkdir()
    manifest_path = manifest_dir / "manifest.sqlite"
    manifest_path.write_text("placeholder", encoding="utf-8")

    local_decl = Declaration(
        name="local_decl",
        type_signature="ndarray -> ndarray",
        prover=Prover.PYTHON,
    )
    manifest_decl = Declaration(
        name="manifest_decl",
        type_signature="float -> float",
        prover=Prover.PYTHON,
    )

    class _FakeStore:
        _declarations = {0: local_decl}
        _metadata = IndexMetadata(
            num_entries=1,
            prover=Prover.PYTHON,
            source_lib="dsp",
            embedding_model="stored-fastembed-model",
            embedding_backend="fastembed",
        )

        def search(self, query_vec, k=10):
            del query_vec, k
            return [(local_decl, 0.6)]

    class _FakeManifestStore:
        _declarations = {0: manifest_decl}
        _metadata = IndexMetadata(
            num_entries=1,
            prover=Prover.PYTHON,
            source_lib="manifest",
            embedding_model="stored-fastembed-model",
            embedding_backend="fastembed",
        )

        def search(self, query_vec, k=10):
            del query_vec, k
            return [(manifest_decl, 0.8)]

    class _FakeEmbedder:
        backend = "fastembed"
        model_name = "stored-fastembed-model"

        def embed(self, text):
            del text
            return np.array([1.0], dtype=np.float32)

    from sciona.indexer.faiss_store import FAISSStore
    import sciona.indexer.embedder as embedder_mod

    monkeypatch.setattr(FAISSStore, "load", staticmethod(lambda _directory: _FakeStore()))
    monkeypatch.setattr(
        embedder_mod,
        "create_embedder",
        lambda backend, model_name: _FakeEmbedder()
        if (backend, model_name) == ("fastembed", "stored-fastembed-model")
        else (_ for _ in ()).throw(AssertionError((backend, model_name))),
    )
    monkeypatch.setattr(
        "sciona.indexer.builder.build_index_from_manifest_sqlite",
        lambda manifest_path_arg, embedder=None: _FakeManifestStore(),
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    class _Cfg:
        embedding_model = "microsoft/unixcoder-base"
        embedding_backend = "unixcoder"

    idx, mode = _load_semantic_index(tmp_path, _Cfg())
    assert mode == "faiss+manifest"
    hits = idx.search_by_embedding("query", k=2)
    assert [decl.name for decl, _ in hits] == ["manifest_decl", "local_decl"]
