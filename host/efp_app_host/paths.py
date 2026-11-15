"""Where things live on the volume and on the container's own disk (docs/CONTRACTS.md, section 7)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass(frozen=True)
class HostPaths:
    store: Path
    runtime: Path
    run: Path

    @classmethod
    def of(cls, settings: Settings) -> "HostPaths":
        return cls(store=settings.store_dir, runtime=settings.runtime_dir, run=settings.run_dir)

    # ---- volume ---------------------------------------------------------
    @property
    def registry_file(self) -> Path:
        return self.store / "registry.json"

    @property
    def keys_dir(self) -> Path:
        return self.store / "keys"

    @property
    def session_key_file(self) -> Path:
        return self.keys_dir / "session.key"

    @property
    def metrics_state_file(self) -> Path:
        return self.store / "metrics" / "state.json"

    @property
    def jobs_dir(self) -> Path:
        return self.store / "jobs"

    def app_dir(self, app_id: str) -> Path:
        return self.store / "apps" / app_id

    def env_file(self, app_id: str) -> Path:
        return self.app_dir(app_id) / "env.json"

    def release_dir(self, app_id: str, sha: str) -> Path:
        return self.app_dir(app_id) / "releases" / sha

    def code_dir(self, app_id: str, sha: str) -> Path:
        return self.release_dir(app_id, sha) / "code"

    def venv_dir(self, app_id: str, sha: str) -> Path:
        return self.release_dir(app_id, sha) / "venv"

    def wheels_dir(self, app_id: str, sha: str) -> Path:
        return self.release_dir(app_id, sha) / "wheels"

    def lock_file(self, app_id: str, sha: str) -> Path:
        return self.release_dir(app_id, sha) / "requirements.lock"

    def manifest_file(self, app_id: str, sha: str) -> Path:
        return self.release_dir(app_id, sha) / "manifest.json"

    def data_dir(self, app_id: str) -> Path:
        return self.app_dir(app_id) / "data"

    def objects_dir(self, app_id: str) -> Path:
        return self.data_dir(app_id) / "objects"

    def logs_dir(self, app_id: str) -> Path:
        return self.app_dir(app_id) / "logs"

    def log_file(self, app_id: str) -> Path:
        return self.logs_dir(app_id) / "app.log"

    # ---- container-local disk (hidden mode) ------------------------------
    def local_app_dir(self, app_id: str) -> Path:
        return self.runtime / app_id

    def local_release_dir(self, app_id: str, sha: str) -> Path:
        return self.local_app_dir(app_id) / "releases" / sha

    def local_tmp_dir(self, app_id: str) -> Path:
        return self.local_app_dir(app_id) / "tmp"

    # ---- sockets and generated configuration ----------------------------
    @property
    def storage_socket(self) -> Path:
        return self.run / "storage.sock"

    @property
    def access_log_socket(self) -> Path:
        return self.run / "access-log.sock"

    @property
    def supervisor_socket(self) -> Path:
        return self.run / "supervisor.sock"

    @property
    def supervisor_apps_dir(self) -> Path:
        return self.run / "supervisor.d"

    @property
    def gateway_secret_file(self) -> Path:
        return self.run / "gateway.secret"
