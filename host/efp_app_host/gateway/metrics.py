"""Traffic numbers from the gateway's access log (docs/CONTRACTS.md, section 5.4).

nginx writes one JSON line per request that reached an app to a unix
datagram socket (``paths.access_log_socket``)::

    {"k": "<log key>", "t": 1763195400.123, "app": "<app id>", "slug": "mindmap",
     "status": 200, "rt": 0.012, "uht": "0.010", "user": "12", "bytes": 5120}

``t`` is the time the request ended (epoch seconds), ``rt`` the whole request
time and ``uht`` the time until the app sent its response header; both in
seconds. ``user`` is empty for an anonymous visitor. A line arrives wrapped
in a syslog header, which is skipped.

``TrafficStore`` adds the lines up per app and UTC day and keeps the sums in
``paths.metrics_state_file``; ``build_payload`` is what goes to the Portal.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..interfaces import AppRuntime
from ..models import iso
from ..registry import write_json_atomic

logger = logging.getLogger(__name__)

STATE_VERSION = 1
# Upper bounds of the latency buckets, in milliseconds; one more bucket
# counts everything slower than the last bound.
LATENCY_BOUNDS_MS = (
    1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 750,
    1000, 1500, 2000, 3000, 5000, 7500, 10000, 15000, 30000, 60000, 120000, 300000, 600000,
)  # fmt: skip
_TIME_SEPARATORS = re.compile(r"[,:\s]+")


def day_of(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def _first_time(value: Any) -> float | None:
    """The first number of an nginx time variable ("0.004", "-", "0.004, 0.010")."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    for token in _TIME_SEPARATORS.split(str(value or "").strip()):
        if not token:
            continue
        try:
            return float(token)
        except ValueError:
            return None
    return None


def percentile(buckets: list[int], fraction: float) -> int:
    """An estimate, in milliseconds, from the latency buckets (0 when empty)."""

    total = sum(buckets)
    if total <= 0:
        return 0
    target = fraction * total
    seen = 0
    for index, count in enumerate(buckets):
        if count <= 0:
            continue
        if seen + count >= target:
            if index >= len(LATENCY_BOUNDS_MS):
                return LATENCY_BOUNDS_MS[-1]
            lower = LATENCY_BOUNDS_MS[index - 1] if index else 0
            upper = LATENCY_BOUNDS_MS[index]
            return int(round(lower + (upper - lower) * (target - seen) / count))
        seen += count
    return LATENCY_BOUNDS_MS[-1]


@dataclass
class AppDay:
    requests: int = 0
    errors_4xx: int = 0
    errors_5xx: int = 0
    latency: list[int] = field(default_factory=lambda: [0] * (len(LATENCY_BOUNDS_MS) + 1))
    # user id -> [requests, time of the last one]
    users: dict[int, list[float]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "errors_4xx": self.errors_4xx,
            "errors_5xx": self.errors_5xx,
            "latency": list(self.latency),
            "users": {str(user_id): [int(value[0]), float(value[1])] for user_id, value in self.users.items()},
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], *, keep_latency: bool) -> "AppDay":
        item = cls(
            requests=int(data.get("requests") or 0),
            errors_4xx=int(data.get("errors_4xx") or 0),
            errors_5xx=int(data.get("errors_5xx") or 0),
        )
        latency = data.get("latency")
        if keep_latency and isinstance(latency, list) and len(latency) == len(item.latency):
            item.latency = [int(count) for count in latency]
        for user_id, value in (data.get("users") or {}).items():
            item.users[int(user_id)] = [int(value[0]), float(value[1])]
        return item


class TrafficStore:
    def __init__(
        self,
        state_file: Path | None = None,
        *,
        log_key: str | Callable[[], str] = "",
        known_app: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self._state_file = state_file
        self.log_key = log_key
        self._known_app = known_app
        self._clock = clock
        self._days: dict[str, dict[str, AppDay]] = {}
        self.dirty = False
        self.rejected = 0

    # ---- counting -------------------------------------------------------------
    def record(
        self,
        app_id: str,
        *,
        at: float,
        status: int,
        latency_ms: float | None = None,
        user_id: int | None = None,
    ) -> None:
        item = self._days.setdefault(day_of(at), {}).setdefault(app_id, AppDay())
        item.requests += 1
        if 400 <= status < 500:
            item.errors_4xx += 1
        elif 500 <= status < 600:
            item.errors_5xx += 1
        if latency_ms is not None and latency_ms >= 0:
            index = len(LATENCY_BOUNDS_MS)
            for position, bound in enumerate(LATENCY_BOUNDS_MS):
                if latency_ms <= bound:
                    index = position
                    break
            item.latency[index] += 1
        if user_id is not None:
            seen = item.users.setdefault(int(user_id), [0, 0.0])
            seen[0] += 1
            seen[1] = max(seen[1], float(at))
        self.dirty = True

    def ingest(self, raw: bytes | str) -> bool:
        """Count one access-log line. Returns whether it was counted."""

        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        start = text.find("{")
        if start < 0:
            return self._reject()
        try:
            entry = json.loads(text[start:])
        except ValueError:
            return self._reject()
        if not isinstance(entry, dict):
            return self._reject()
        expected = self.log_key() if callable(self.log_key) else self.log_key
        presented = entry.get("k")
        if not expected or not isinstance(presented, str):
            return self._reject()
        if not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8")):
            return self._reject()

        app_id = entry.get("app")
        if not isinstance(app_id, str) or not app_id:
            return False  # not a request that reached an app
        if self._known_app is not None and not self._known_app(app_id):
            return False
        try:
            at = float(entry["t"])
            status = int(entry["status"])
        except (KeyError, TypeError, ValueError):
            return self._reject()
        now = self._clock()
        if day_of(at) not in self._window(now):
            return False

        # Time to the first byte of the answer says how fast the app is even
        # for a download, an event stream or a WebSocket, whose whole request
        # time is as long as the visitor stays.
        seconds = _first_time(entry.get("uht"))
        if seconds is None and status != 101:
            seconds = _first_time(entry.get("rt"))
        user_id: int | None = None
        user = entry.get("user")
        if isinstance(user, int) and not isinstance(user, bool):
            user_id = user
        elif isinstance(user, str) and user.strip().isdigit():
            user_id = int(user)
        self.record(
            app_id,
            at=at,
            status=status,
            latency_ms=None if seconds is None else seconds * 1000.0,
            user_id=user_id,
        )
        return True

    def _reject(self) -> bool:
        self.rejected += 1
        return False

    # ---- reading --------------------------------------------------------------
    def _window(self, now: float) -> tuple[str, str]:
        """The days that are still reported: yesterday and today (UTC)."""

        today = datetime.fromtimestamp(now, timezone.utc)
        return (today - timedelta(days=1)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def traffic(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Section 5.4 ``traffic``: running totals of yesterday and today."""

        rows: list[dict[str, Any]] = []
        for day in self._window(self._clock() if now is None else now):
            for app_id, item in sorted(self._days.get(day, {}).items()):
                rows.append(
                    {
                        "day": day,
                        "app_id": app_id,
                        "requests": item.requests,
                        "errors_4xx": item.errors_4xx,
                        "errors_5xx": item.errors_5xx,
                        "p95_ms": percentile(item.latency, 0.95),
                        "visitors": len(item.users),
                        "users": [
                            {
                                "user_id": user_id,
                                "requests": int(value[0]),
                                "last_at": iso(datetime.fromtimestamp(value[1], timezone.utc)),
                            }
                            for user_id, value in sorted(item.users.items())
                        ],
                    }
                )
        return rows

    def prune(self, *, now: float | None = None) -> None:
        keep = self._window(self._clock() if now is None else now)
        stale = [day for day in self._days if day not in keep]
        for day in stale:
            del self._days[day]
        if stale:
            self.dirty = True

    # ---- persistence ----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "bounds": list(LATENCY_BOUNDS_MS),
            "days": {day: {app_id: item.to_json() for app_id, item in apps.items()} for day, apps in self._days.items()},
        }

    def write(self, snapshot: dict[str, Any]) -> None:
        if self._state_file is not None:
            write_json_atomic(self._state_file, snapshot, mode=0o600)

    def save(self) -> None:
        self.dirty = False
        self.write(self.snapshot())

    def load(self) -> None:
        """Take over what an earlier run of the manager counted."""

        if self._state_file is None:
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
                raise ValueError("unknown format")
            keep_latency = data.get("bounds") == list(LATENCY_BOUNDS_MS)
            days = {
                str(day): {str(app_id): AppDay.from_json(item, keep_latency=keep_latency) for app_id, item in apps.items()}
                for day, apps in (data.get("days") or {}).items()
            }
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
            logger.warning("metrics state %s is unreadable and is started afresh: %s", self._state_file, exc)
            return
        self._days = days
        self.prune()
        self.dirty = False


def build_payload(metrics: TrafficStore, runtime: AppRuntime, *, now: float | None = None) -> dict[str, Any]:
    """The body of ``POST /metrics/ingest`` (section 5.4)."""

    current = time.time() if now is None else now
    return {
        "sent_at": iso(datetime.fromtimestamp(current, timezone.utc)),
        "traffic": metrics.traffic(now=current),
        "apps": [status.to_dict() for status in runtime.statuses()],
        "host": runtime.host_info(),
    }


class AccessLogListener:
    """Receives nginx's access-log datagrams on a unix socket.

    Unix datagram sockets do not exist everywhere (Windows): ``start`` then
    reports ``False`` and the manager runs without traffic numbers.
    """

    def __init__(self, path: Path, deliver: Callable[[bytes], Any], *, max_per_wake: int = 500):
        self.path = path
        self._deliver = deliver
        self._max_per_wake = max_per_wake
        self._socket: socket.socket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> bool:
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            logger.warning("no unix sockets on this platform: the access log is not collected")
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        listener = socket.socket(family, socket.SOCK_DGRAM)
        try:
            listener.bind(str(self.path))
            # nginx's workers run as another user. What they send is checked
            # by the key in every line, not by who may write here.
            os.chmod(self.path, 0o666)
            listener.setblocking(False)
            try:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            except OSError:
                pass
            self._loop = asyncio.get_running_loop()
            self._loop.add_reader(listener.fileno(), self._readable)
        except Exception:
            listener.close()
            raise
        self._socket = listener
        return True

    def _readable(self) -> None:
        listener = self._socket
        if listener is None:
            return
        for _ in range(self._max_per_wake):
            try:
                data = listener.recv(65535)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            try:
                self._deliver(data)
            except Exception:  # one bad line must not stop the listener
                logger.exception("an access-log line could not be counted")

    def stop(self) -> None:
        listener, self._socket = self._socket, None
        if listener is None:
            return
        if self._loop is not None:
            try:
                self._loop.remove_reader(listener.fileno())
            except (RuntimeError, ValueError, OSError):
                pass
        listener.close()
        try:
            self.path.unlink()
        except OSError:
            pass
