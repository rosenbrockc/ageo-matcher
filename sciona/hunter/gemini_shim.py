"""Persistent Gemini shim client backed by socket-connected Node workers."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(min_value, value)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _resolve_gemini_cli_root() -> str:
    executable = shutil.which("gemini")
    if not executable:
        raise RuntimeError("gemini executable not found in PATH")
    resolved = Path(executable).resolve()
    if resolved.name == "index.js" and resolved.parent.name == "dist":
        return str(resolved.parent.parent)
    raise RuntimeError(f"Unable to resolve Gemini CLI root from {resolved}")


def _stage_gemini_home(runtime_dir: Path, source_home: Path | None = None) -> Path:
    """Create a writable HOME for Gemini and seed it from the user's config."""
    source_home = source_home or Path.home()
    staged_home = runtime_dir / "home"
    staged_home.mkdir(parents=True, exist_ok=True)

    source_gemini_dir = source_home / ".gemini"
    staged_gemini_dir = staged_home / ".gemini"
    if source_gemini_dir.exists():
        shutil.copytree(
            source_gemini_dir,
            staged_gemini_dir,
            dirs_exist_ok=True,
            symlinks=True,
            ignore_dangling_symlinks=True,
        )
    else:
        staged_gemini_dir.mkdir(parents=True, exist_ok=True)

    source_gcloud_dir = source_home / ".config" / "gcloud"
    staged_gcloud_dir = staged_home / ".config" / "gcloud"
    if source_gcloud_dir.exists():
        staged_gcloud_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_gcloud_dir, staged_gcloud_dir, dirs_exist_ok=True)

    return staged_home


@dataclass
class _GeminiWorker:
    process: asyncio.subprocess.Process
    socket_path: Path
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    pid: int | None = None
    pending: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    request_id: int = 0


class GeminiShimClient:
    """LLM client that reuses live Gemini workers over Unix sockets."""

    def __init__(
        self,
        *,
        model: str,
        max_tokens: int,
        pool_size: int | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._telemetry_provider = "gemini_shim"
        self._telemetry_model = model
        self._fake_mode = os.getenv("SCIONA_GEMINI_DAEMON_FAKE", "").strip() == "1"
        self._pool_size = pool_size or _env_int("SCIONA_GEMINI_SHIM_POOL_SIZE", 2)
        self._timeout_s = _env_float("SCIONA_SUBPROCESS_TIMEOUT_S", 90.0)
        self._retry_backoff_s = _env_float("SCIONA_SUBPROCESS_RETRY_BACKOFF_S", 1.5)
        runtime_root = os.getenv("SCIONA_GEMINI_SHIM_RUNTIME_DIR", "/tmp")
        self._runtime_dir = Path(tempfile.mkdtemp(prefix="ags-", dir=runtime_root))
        self._staged_home = _stage_gemini_home(self._runtime_dir)
        self._daemon_script = Path(__file__).with_name("gemini_daemon.mjs")
        self._cli_root = "" if self._fake_mode else _resolve_gemini_cli_root()
        self._node_bin = shutil.which("node") or "node"
        self._init_lock = asyncio.Lock()
        self._workers: list[_GeminiWorker] = []
        self._next_worker = itertools.count()
        self._closed = False
        self._last_completion_metadata: dict[str, object] = {}
        self._last_error_metadata: dict[str, object] = {}

    async def complete(self, system: str, user: str) -> str:
        attempts = 2
        last_error: Exception | None = None
        self._last_error_metadata = {}
        for attempt in range(1, attempts + 1):
            worker = await self._acquire_worker()
            try:
                result = await self._rpc(
                    worker,
                    "complete",
                    {"system": system, "user": user, "max_tokens": self._max_tokens},
                )
                self._last_completion_metadata = {
                    "shim_provider": "gemini_shim",
                    "shim_worker_pid": result.get("pid"),
                    "shim_request_count": result.get("requestCount"),
                    "shim_pool_size": self._pool_size,
                    "shim_was_cold_start": bool(result.get("coldStart", False)),
                }
                text = result.get("text", "")
                return text if isinstance(text, str) else str(text)
            except Exception as exc:
                last_error = exc
                self._last_error_metadata = {
                    "provider_error_phase": "rpc",
                    "provider_transport": "socket_shim",
                    "provider_cli": "gemini",
                    "provider_model": self._model,
                    "provider_attempt": attempt,
                    "provider_attempts_total": attempts,
                    "shim_worker_pid": worker.pid,
                    "shim_pool_size": self._pool_size,
                    "provider_error_excerpt": str(exc)[:240],
                }
                await self._restart_worker(worker)
                if attempt < attempts:
                    await asyncio.sleep(self._retry_backoff_s)
                    continue
        raise RuntimeError(f"gemini_shim failed: {last_error}") from last_error

    async def complete_with_grammar(self, system: str, user: str, grammar: str) -> str:
        return await self.complete(system, user)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for worker in list(self._workers):
            await self._shutdown_worker(worker)
        self._workers.clear()
        shutil.rmtree(self._runtime_dir, ignore_errors=True)

    def get_last_completion_metadata(self) -> dict[str, object]:
        return dict(self._last_completion_metadata)

    def get_last_error_metadata(self) -> dict[str, object]:
        return dict(self._last_error_metadata)

    async def warmup(self) -> None:
        """Spawn and connect the persistent worker pool up front."""
        await self._ensure_workers()

    async def _ensure_workers(self) -> None:
        if self._workers:
            return
        async with self._init_lock:
            if self._workers:
                return
            for index in range(self._pool_size):
                self._workers.append(await self._spawn_worker(index))

    async def _acquire_worker(self) -> _GeminiWorker:
        await self._ensure_workers()
        start = next(self._next_worker) % len(self._workers)
        ordered = self._workers[start:] + self._workers[:start]
        worker = min(ordered, key=lambda item: item.pending)
        worker.pending += 1
        return worker

    async def _spawn_worker(self, index: int) -> _GeminiWorker:
        socket_path = self._runtime_dir / f"worker-{index}.sock"
        env = dict(os.environ)
        env["SCIONA_GEMINI_CLI_ROOT"] = self._cli_root
        env["HOME"] = str(self._staged_home)
        env.setdefault("XDG_CONFIG_HOME", str(self._staged_home / ".config"))
        env.setdefault("XDG_CACHE_HOME", str(self._staged_home / ".cache"))
        env.setdefault("XDG_STATE_HOME", str(self._staged_home / ".local" / "state"))
        process = await asyncio.create_subprocess_exec(
            self._node_bin,
            str(self._daemon_script),
            "--socket",
            str(socket_path),
            "--model",
            self._model,
            "--cwd",
            os.getcwd(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            reader, writer = await self._wait_for_socket(process, socket_path)
        except Exception:
            await self._terminate_process(process)
            stderr_text = await self._read_stream(process.stderr)
            stdout_text = await self._read_stream(process.stdout)
            details = stderr_text.strip() or stdout_text.strip()
            self._last_error_metadata = {
                "provider_error_phase": "startup",
                "provider_transport": "socket_shim",
                "provider_cli": "gemini",
                "provider_model": self._model,
                "provider_exit_code": process.returncode,
                "provider_stderr_excerpt": details[:240],
            }
            raise RuntimeError(
                f"Failed to start Gemini shim worker: {details}"
            ) from None

        worker = _GeminiWorker(
            process=process,
            socket_path=socket_path,
            reader=reader,
            writer=writer,
            pid=process.pid,
        )
        await self._rpc(worker, "ping", {})
        return worker

    async def _wait_for_socket(
        self,
        process: asyncio.subprocess.Process,
        socket_path: Path,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        deadline = asyncio.get_running_loop().time() + self._timeout_s
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                stderr_text = await self._read_stream(process.stderr)
                raise RuntimeError(
                    f"Gemini shim worker exited early with code {process.returncode}: {stderr_text.strip()}"
                )
            if socket_path.exists():
                try:
                    return await asyncio.open_unix_connection(str(socket_path))
                except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                    last_error = exc
            await asyncio.sleep(0.05)
        raise RuntimeError(f"Timed out waiting for Gemini socket {socket_path}: {last_error}")

    async def _rpc(
        self,
        worker: _GeminiWorker,
        method: str,
        params: dict[str, object],
    ) -> dict[str, object]:
        async with worker.lock:
            worker.request_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": worker.request_id,
                "method": method,
                "params": params,
            }
            try:
                worker.writer.write((json.dumps(payload) + "\n").encode())
                await worker.writer.drain()
                raw = await asyncio.wait_for(worker.reader.readline(), timeout=self._timeout_s)
            finally:
                worker.pending = max(0, worker.pending - 1)
            if not raw:
                raise RuntimeError("Gemini shim socket closed unexpectedly")
            response = json.loads(raw.decode())
            if "error" in response:
                error = response["error"]
                if isinstance(error, dict):
                    message = error.get("message", "unknown Gemini shim error")
                else:
                    message = str(error)
                raise RuntimeError(message)
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Invalid Gemini shim response: {response!r}")
            return result

    async def _restart_worker(self, worker: _GeminiWorker) -> None:
        index = self._workers.index(worker)
        await self._shutdown_worker(worker)
        self._workers[index] = await self._spawn_worker(index)

    async def _shutdown_worker(self, worker: _GeminiWorker) -> None:
        try:
            worker.writer.close()
            await worker.writer.wait_closed()
        except Exception:
            pass
        await self._terminate_process(worker.process)
        try:
            worker.socket_path.unlink(missing_ok=True)
        except Exception:
            pass

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()

    async def _read_stream(self, stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        try:
            data = await asyncio.wait_for(stream.read(), timeout=0.2)
        except Exception:
            return ""
        return data.decode(errors="replace")
