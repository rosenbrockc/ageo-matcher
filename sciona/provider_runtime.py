"""Remote catalog retrieval and opt-in PEP 420 provider installation."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
import importlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import pkgutil
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from sciona.api.models import CatalogEntry, ProviderInstallInfo
from sciona.catalog_query import expand_catalog_query_tokens
from sciona.types import Declaration, Prover

_DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._+!-]*[A-Za-z0-9])?$")
_DEFAULT_INSTALL_TIMEOUT_SECONDS = 300.0
_DEFAULT_LOCK_TIMEOUT_SECONDS = 120.0
_DEFAULT_MAX_WHEEL_BYTES = 512 * 1024 * 1024
_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_REQUIRED_CONTEXT_RE = re.compile(r"\[requires-context:([^\]]+)\]", re.I)


def _normalized_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


class RemoteCatalogClient:
    """Search the Postgres-backed catalog through the platform API."""

    def __init__(self, api_url: str | None = None, *, token: str = "") -> None:
        self.api_url = (
            api_url
            or os.environ.get("SCIONA_API_URL", "")
            or "https://api.sciona.dev"
        ).rstrip("/")
        self.token = token

    async def search(
        self,
        query: str,
        *,
        domain_tag: str | None = None,
        limit: int = 20,
    ) -> list[CatalogEntry]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Remote catalog search requires httpx") from exc
        params: dict[str, Any] = {"q": query, "limit": limit}
        if domain_tag:
            params["domain_tag"] = domain_tag
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(
                f"{self.api_url}/catalog/search",
                params=params,
                headers=headers,
            )
            response.raise_for_status()
        return [CatalogEntry.model_validate(row) for row in response.json()]

    async def search_artifacts(
        self,
        query: str,
        *,
        domain_tag: str | None = None,
        limit: int = 20,
    ) -> list[CatalogEntry]:
        """Search graph and atom artifacts through the unified catalog API."""
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Remote catalog search requires httpx") from exc
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

        async def request(search_query: str) -> list[CatalogEntry]:
            params: dict[str, Any] = {"q": search_query, "limit": limit}
            if domain_tag:
                params["domain_tag"] = domain_tag
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(
                    f"{self.api_url}/catalog/search-artifacts",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
            return [CatalogEntry.model_validate(row) for row in response.json()]

        rows = await request(query)
        if query and not any(row.artifact_kind == "cdg" for row in rows):
            expanded_query = " ".join(sorted(expand_catalog_query_tokens(query)))
            if expanded_query and expanded_query != query.lower():
                expanded_rows = await request(expanded_query)
                if expanded_rows:
                    by_fqdn = {row.fqdn: row for row in rows}
                    by_fqdn.update({row.fqdn: row for row in expanded_rows})
                    rows = list(by_fqdn.values())
        atom_rows = await self.search(query, domain_tag=domain_tag, limit=limit)
        expanded_query = " ".join(sorted(expand_catalog_query_tokens(query)))
        if expanded_query and expanded_query != query.lower():
            atom_rows.extend(
                await self.search(
                    expanded_query,
                    domain_tag=domain_tag,
                    limit=limit,
                )
            )
        expanded_tokens = expand_catalog_query_tokens(query)
        if "ecef" in expanded_tokens and "ecef" not in query.lower():
            atom_rows.extend(
                await self.search(
                    "WGS84 latitude longitude altitude to ECEF coordinates",
                    domain_tag=domain_tag,
                    limit=limit,
                )
            )
        by_fqdn = {row.fqdn: row for row in rows}
        by_fqdn.update({row.fqdn: row for row in atom_rows})
        rows = list(by_fqdn.values())
        return rows

    async def artifact_document(self, fqdn: str) -> dict[str, Any]:
        """Fetch one complete artifact document, including CDG bindings."""
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Remote catalog retrieval requires httpx") from exc
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(
                f"{self.api_url}/catalog/artifact/{fqdn}",
                headers=headers,
            )
            response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise TypeError(f"Artifact {fqdn!r} returned a non-object document")
        return document

    async def select_artifact(
        self,
        query: str,
        *,
        domain_tag: str | None = None,
        limit: int = 40,
    ) -> CatalogEntry:
        """Select the best context-compatible executable artifact."""
        rows = await self.search_artifacts(query, domain_tag=domain_tag, limit=limit)
        if not rows:
            raise LookupError(f"No executable artifact matched {query!r}")
        query_tokens = expand_catalog_query_tokens(query)

        def rank(row: CatalogEntry) -> tuple[float, str]:
            text = " ".join(
                [row.fqdn, row.description, " ".join(row.domain_tags)]
            ).lower()
            tokens = set(_TOKEN_RE.findall(text.replace("_", " ")))
            overlap = len(query_tokens & tokens) / max(1, len(query_tokens))
            required = {
                token.strip().lower()
                for match in _REQUIRED_CONTEXT_RE.findall(text)
                for token in match.split(",")
                if token.strip()
            }
            mismatch = bool(required) and not bool(required & query_tokens)
            context_match = bool(required) and bool(required & query_tokens)
            trust = 0.25 if row.trust_readiness == "ready" else 0.0
            composition = 0.15 if row.artifact_kind == "cdg" else 0.0
            roundtrip_mismatch = "roundtrip" in tokens and "roundtrip" not in query_tokens
            score = (
                overlap
                + float(row.score or 0.0)
                + trust
                + composition
                + (1.0 if context_match else 0.0)
                - (2.0 if mismatch else 0.0)
                - (0.5 if roundtrip_mismatch else 0.0)
            )
            return (-score, row.fqdn)

        cdgs = [row for row in rows if row.artifact_kind == "cdg"]
        atoms = [row for row in rows if row.artifact_kind == "atom"]
        query_text = query.lower()
        conversion_request = bool(
            {"convert", "map", "transform", "translate"} & query_tokens
        ) and bool(
            "ecef" in query_text
            or "earth-centered" in query_text
            or "earth centered" in query_text
            or "earth-fixed" in query_text
            or "earth fixed" in query_text
            or {"cartesian", "coordinates", "frame", "axes"} & query_tokens
        )
        if conversion_request and atoms:
            target_atoms = atoms
            if (
                "ecef" in query_text
                or "earth-centered" in query_text
                or "earth centered" in query_text
                or "earth-fixed" in query_text
                or "earth fixed" in query_text
            ):
                directional = [row for row in atoms if row.fqdn.endswith("to_ecef")]
                if directional:
                    target_atoms = directional
                elif cdgs:
                    return sorted(cdgs, key=rank)[0]
            return sorted(target_atoms, key=rank)[0]
        if cdgs:
            return sorted(cdgs, key=rank)[0]
        return sorted(rows, key=rank)[0]

    async def find(self, fqdn: str) -> CatalogEntry:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Remote catalog retrieval requires httpx") from exc
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            response = await client.get(
                f"{self.api_url}/catalog/find/{quote(fqdn, safe='')}",
                headers=headers,
            )
            if response.status_code == 404:
                raise LookupError(f"Published atom {fqdn!r} was not found")
            response.raise_for_status()
        return CatalogEntry.model_validate(response.json())


class ProviderInstaller:
    """Install exactly one selected provider and materialize its callable."""

    def __init__(
        self,
        *,
        python_executable: str | Path | None = None,
        allow_local_artifacts: bool = False,
    ) -> None:
        self.python_executable = str(python_executable or sys.executable)
        self.allow_local_artifacts = allow_local_artifacts
        self.installed_distributions: list[str] = []

    def materialize(self, candidate: CatalogEntry) -> Any:
        provider = candidate.provider
        if provider is None:
            raise RuntimeError(f"Atom {candidate.fqdn!r} has no provider installation metadata")
        if Path(self.python_executable).resolve() != Path(sys.executable).resolve():
            raise RuntimeError(
                "Materialization must install into the currently running virtualenv"
            )
        self.ensure_installed(provider)
        self.refresh_namespace()
        module = importlib.import_module(provider.import_module)
        try:
            return getattr(module, provider.import_symbol)
        except AttributeError as exc:
            raise ImportError(
                f"Provider {provider.provider_id!r} does not export "
                f"{provider.import_module}.{provider.import_symbol}"
            ) from exc

    def ensure_installed(self, provider: ProviderInstallInfo) -> None:
        """Install the provider only when the target environment needs it."""
        if self._provider_available_in_target(provider):
            return
        with self._installation_lock(provider.distribution_name):
            if self._provider_available_in_target(provider):
                return
            self.install(provider)
            if Path(self.python_executable).resolve() == Path(sys.executable).resolve():
                self.refresh_namespace()
            if not self._provider_available_in_target(provider):
                raise ImportError(
                    f"Provider {provider.distribution_name}=={provider.distribution_version} "
                    "was not importable after installation"
                )
            self.installed_distributions.append(provider.distribution_name)

    def install(self, provider: ProviderInstallInfo) -> None:
        self._validate_provider(provider)
        install_target = provider.install_requirement
        downloaded_path: Path | None = None
        try:
            if provider.wheel_url:
                downloaded_path = self._download_verified_wheel(provider)
                install_target = str(downloaded_path)
            subprocess.run(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    install_target,
                ],
                check=True,
                timeout=self._positive_env_float(
                    "SCIONA_PROVIDER_INSTALL_TIMEOUT_SECONDS",
                    _DEFAULT_INSTALL_TIMEOUT_SECONDS,
                ),
            )
        finally:
            if downloaded_path is not None:
                downloaded_path.unlink(missing_ok=True)
                downloaded_path.parent.rmdir()

    @contextmanager
    def _installation_lock(self, distribution_name: str):
        lock_root = Path(tempfile.gettempdir()) / "sciona-provider-locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / f"{_normalized_distribution(distribution_name)}.lock"
        handle = lock_path.open("a+")
        try:
            try:
                import fcntl
            except ImportError:
                yield
                return
            deadline = time.monotonic() + self._positive_env_float(
                "SCIONA_PROVIDER_INSTALL_LOCK_TIMEOUT_SECONDS",
                _DEFAULT_LOCK_TIMEOUT_SECONDS,
            )
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"Timed out waiting to install {distribution_name!r}"
                        )
                    time.sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    @staticmethod
    def _positive_env_float(name: str, default: float) -> float:
        value = float(os.environ.get(name, str(default)))
        if value <= 0:
            raise ValueError(f"{name} must be positive")
        return value

    @staticmethod
    def refresh_namespace() -> None:
        importlib.invalidate_caches()
        for package_name in ("sciona", "sciona.atoms"):
            package = sys.modules.get(package_name)
            package_path = getattr(package, "__path__", None)
            if package is not None and package_path is not None:
                package.__path__ = pkgutil.extend_path(package_path, package_name)

    @staticmethod
    def _module_available(import_module: str) -> bool:
        try:
            return importlib.util.find_spec(import_module) is not None
        except (ImportError, ModuleNotFoundError, AttributeError):
            return False

    @classmethod
    def _provider_available(cls, provider: ProviderInstallInfo) -> bool:
        try:
            installed_version = importlib.metadata.version(provider.distribution_name)
        except importlib.metadata.PackageNotFoundError:
            return False
        return (
            installed_version == provider.distribution_version
            and cls._module_available(provider.import_module)
        )

    def _provider_available_in_target(self, provider: ProviderInstallInfo) -> bool:
        if Path(self.python_executable).resolve() == Path(sys.executable).resolve():
            return self._provider_available(provider)
        check = (
            "import importlib, importlib.metadata; "
            f"assert importlib.metadata.version({provider.distribution_name!r}) == "
            f"{provider.distribution_version!r}; "
            f"importlib.import_module({provider.import_module!r})"
        )
        result = subprocess.run(
            [self.python_executable, "-c", check],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0

    @staticmethod
    def _validate_provider(provider: ProviderInstallInfo) -> None:
        if not _DISTRIBUTION_RE.fullmatch(provider.distribution_name):
            raise ValueError("Invalid provider distribution name")
        if not _VERSION_RE.fullmatch(provider.distribution_version):
            raise ValueError("Invalid provider distribution version")
        expected = f"{provider.distribution_name}=={provider.distribution_version}"
        requirement_parts = provider.install_requirement.split("==", 1)
        if len(requirement_parts) != 2:
            raise ValueError("Provider install requirement must pin one exact version")
        if (
            _normalized_distribution(requirement_parts[0])
            != _normalized_distribution(provider.distribution_name)
            or requirement_parts[1] != provider.distribution_version
        ):
            raise ValueError(
                f"Provider install requirement must be equivalent to {expected!r}"
            )
        if provider.wheel_url and not provider.wheel_sha256:
            raise ValueError("A provider wheel URL requires a SHA-256 digest")

    def _download_verified_wheel(self, provider: ProviderInstallInfo) -> Path:
        parsed = urlparse(provider.wheel_url)
        if parsed.scheme not in {"https", "file"}:
            raise ValueError("Provider wheel URL must use https")
        if parsed.scheme == "file" and not self.allow_local_artifacts:
            raise ValueError("Local provider wheels are disabled")
        max_bytes = int(
            self._positive_env_float(
                "SCIONA_PROVIDER_MAX_WHEEL_BYTES", _DEFAULT_MAX_WHEEL_BYTES
            )
        )
        with urlopen(provider.wheel_url, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise ValueError("Provider wheel exceeds configured size limit")
            payload = response.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise ValueError("Provider wheel exceeds configured size limit")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != provider.wheel_sha256:
            raise ValueError("Provider wheel SHA-256 mismatch")
        wheel_name = Path(parsed.path).name
        if not wheel_name.endswith(".whl") or "/" in wheel_name or "\\" in wheel_name:
            raise ValueError("Provider wheel URL must identify a wheel file")
        download_dir = Path(tempfile.mkdtemp(prefix="sciona-provider-"))
        wheel_path = download_dir / wheel_name
        wheel_path.write_bytes(payload)
        return wheel_path


class PostgresSemanticIndex:
    """SemanticIndex adapter over the platform's Postgres catalog API."""

    def __init__(self, api_url: str | None = None, *, token: str = "") -> None:
        self.api_url = (
            api_url
            or os.environ.get("SCIONA_API_URL", "")
            or "https://api.sciona.dev"
        ).rstrip("/")
        self.token = token
        self._declarations: dict[str, Declaration] = {}

    def search_by_embedding(
        self, query_text: str, k: int = 10
    ) -> list[tuple[Declaration, float]]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("Postgres semantic search requires httpx") from exc
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = httpx.get(
            f"{self.api_url}/catalog/search",
            params={"q": query_text, "limit": k},
            headers=headers,
            timeout=60.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        entries = [CatalogEntry.model_validate(row) for row in response.json()]
        results: list[tuple[Declaration, float]] = []
        for rank, entry in enumerate(entries[:k]):
            provider = entry.provider
            declaration = Declaration(
                name=entry.fqdn,
                type_signature="",
                docstring=entry.description,
                source_lib=provider.import_module if provider else "postgres_catalog",
                prover=Prover.PYTHON,
                provider_id=provider.provider_id if provider else "",
                distribution_name=provider.distribution_name if provider else "",
                distribution_version=provider.distribution_version if provider else "",
                install_requirement=provider.install_requirement if provider else "",
                import_module=provider.import_module if provider else "",
                import_symbol=provider.import_symbol if provider else "",
                wheel_url=provider.wheel_url if provider else "",
                wheel_sha256=provider.wheel_sha256 if provider else "",
            )
            self._declarations[declaration.name] = declaration
            rank_score = 1.0 - (rank / max(1, len(entries)))
            results.append((declaration, max(float(entry.score), rank_score)))
        return results

    def search_by_type(self, type_signature: str, k: int = 10) -> list[Declaration]:
        return [decl for decl, _score in self.search_by_embedding(type_signature, k=k)]

    def get_declaration(self, name: str) -> Declaration | None:
        return self._declarations.get(name)
