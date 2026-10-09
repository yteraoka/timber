"""TimeTree の指定日の予定を取得する。

TimeTree Web 版 (https://timetreeapp.com) が内部で利用している API を使う。
認証情報は環境変数 TIMETREE_USERNAME / TIMETREE_PASSWORD から読む。

    uv run timetree 2026-09-30
    uv run timetree 2026-09-30 --calendar 家族の予定 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dateutil.rrule import rrulestr

BASE_URL = "https://timetreeapp.com"
CATEGORY_KEEP = 2  # キープ (日付未確定のメモ的な予定)
CLIENT_HEADER = "web/2.1.0/ja"


class TimeTreeError(Exception):
    pass


class RateLimitError(TimeTreeError):
    pass


def default_session_file() -> Path:
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(cache_home) / "timetree" / "session.json"


class TimeTreeClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) timetree-cli",
                "X-TimeTreeA": CLIENT_HEADER,
                "Content-Type": "application/json",
            }
        )

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        r = self.session.request(method, BASE_URL + path, timeout=30, **kwargs)
        if r.status_code == 429:
            raise RateLimitError(
                f"{method} {path}: HTTP 429: リクエスト回数の制限に掛かりました。しばらく待ってから再実行してください"
            )
        if r.status_code >= 400:
            raise TimeTreeError(f"{method} {path}: HTTP {r.status_code}: {r.text[:200]}")
        return r

    def login(self, username: str, password: str) -> None:
        # サインインページの meta タグから CSRF トークンを取得する (同時に _session_id cookie が発行される)
        html = self._request("GET", "/signin").text
        m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        if not m:
            raise TimeTreeError("CSRF token not found on signin page")
        self.session.headers["X-CSRF-Token"] = m.group(1)
        self._request(
            "PUT",
            "/api/v1/auth/email/signin",
            json={"uid": username, "password": password, "uuid": uuid.uuid4().hex},
        )

    def login_with_cache(self, username: str, password: str, session_file: Path) -> None:
        """保存済みのセッションが有効ならそれを使い、無効な場合だけログインする。

        ログイン API には回数制限 (HTTP 429) があるため、実行の度にログインしないようにする。
        """
        if self._restore_session(username, session_file):
            return
        self.login(username, password)
        self._save_session(username, session_file)

    def _restore_session(self, username: str, session_file: Path) -> bool:
        try:
            data = json.loads(session_file.read_text())
        except (OSError, ValueError):
            return False
        if data.get("username") != username or not data.get("session_id"):
            return False
        self.session.cookies.set("_session_id", data["session_id"], domain="timetreeapp.com")
        self.session.headers["X-CSRF-Token"] = data.get("csrf_token", "")
        # 期限切れなどで無効なセッションは 400/401 になる
        r = self.session.get(BASE_URL + "/api/v1/user", timeout=30)
        if r.status_code == 429:
            raise RateLimitError("HTTP 429: リクエスト回数の制限に掛かりました。しばらく待ってから再実行してください")
        if r.ok:
            return True
        self.session.cookies.clear()
        self.session.headers.pop("X-CSRF-Token", None)
        return False

    def _save_session(self, username: str, session_file: Path) -> None:
        data = {
            "username": username,
            "session_id": self.session.cookies.get("_session_id", domain="timetreeapp.com"),
            "csrf_token": self.session.headers.get("X-CSRF-Token", ""),
        }
        session_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # セッション ID は認証情報なので所有者のみ読み書きできるようにする
        fd = os.open(session_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(session_file, 0o600)

    def calendars(self) -> list[dict]:
        cals = self._request("GET", "/api/v2/calendars").json()["calendars"]
        return [c for c in cals if not c.get("deactivated_at")]

    def events(self, calendar_id: int) -> list[dict]:
        # 範囲指定の API は無く、Web 版と同じく sync API で全件を取得する。
        # chunk が true の間は返ってきた since を付けて続きを取得する。
        path = f"/api/v1/calendar/{calendar_id}/events/sync"
        params: dict = {}
        events: list[dict] = []
        while True:
            data = self._request("GET", path, params=params).json()
            events.extend(data.get("events", []))
            if not data.get("chunk"):
                return events
            params = {"since": data["since"]}


@dataclass
class Occurrence:
    calendar: str
    title: str
    all_day: bool
    start: str
    end: str
    location: str
    note: str
    url: str
    category: int

    def format(self) -> str:
        if self.all_day:
            s, e = self.start, self.end
            when = "終日" if s == e else f"終日 {s} - {e}"
        else:
            when = f"{self.start[11:16]} - {self.end[11:16]}"
            if self.start[:10] != self.end[:10]:
                when = f"{self.start[:16].replace('T', ' ')} - {self.end[:16].replace('T', ' ')}"
        line = f"{when}\t{self.title}\t[{self.calendar}]"
        if self.location:
            line += f"\t@{self.location}"
        return line


def _ms_to_dt(ms: int, tz: ZoneInfo | timezone) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz)


def _normalize_rules(rules: list[str], tz: ZoneInfo) -> str:
    """RRULE/EXDATE を naive な DTSTART で展開できる形に揃える。

    UTC 指定 (末尾 Z) の UNTIL / EXDATE はイベントのタイムゾーンの壁時計時刻に変換する。
    """

    def conv(m: re.Match) -> str:
        v = m.group(0)
        dt = datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%Y%m%dT%H%M%S")

    return "\n".join(re.sub(r"\d{8}T\d{6}Z", conv, r) for r in rules)


def occurrences_on(event: dict, day: date, local_tz: ZoneInfo) -> list[tuple[datetime, datetime]]:
    """event が day に掛かる場合、その (start, end) を返す。繰り返し予定は展開する。"""
    all_day = event["all_day"]
    if all_day:
        # 終日予定は UTC 0 時の値で、end_at は最終日 (その日を含む)
        tz: ZoneInfo | timezone = timezone.utc
        win_start = datetime.combine(day, time.min)
        win_end = win_start + timedelta(days=1)
    else:
        tz = ZoneInfo(event.get("start_timezone") or "UTC")
        win_start = datetime.combine(day, time.min, local_tz)
        win_end = win_start + timedelta(days=1)

    start = _ms_to_dt(event["start_at"], tz).replace(tzinfo=None)
    end = _ms_to_dt(event["end_at"], tz).replace(tzinfo=None)
    duration = end - start

    def overlaps(s: datetime, e: datetime) -> bool:
        if all_day:
            return s <= win_start and win_start <= e
        s_aware, e_aware = s.replace(tzinfo=tz), e.replace(tzinfo=tz)
        return s_aware < win_end and (e_aware > win_start or s_aware >= win_start)

    def to_output(s: datetime, e: datetime) -> tuple[datetime, datetime]:
        if all_day:
            return s, e
        return s.replace(tzinfo=tz).astimezone(local_tz), e.replace(tzinfo=tz).astimezone(local_tz)

    rules = event.get("recurrences") or []
    if not rules:
        return [to_output(start, end)] if overlaps(start, end) else []

    rrset = rrulestr(
        _normalize_rules(rules, tz if isinstance(tz, ZoneInfo) else ZoneInfo("UTC")),
        dtstart=start,
        forceset=True,
    )
    # 展開は壁時計時刻 (naive) で行うので、探索範囲は余裕を持たせてから overlaps で絞る
    if all_day:
        lo, hi = win_start - duration, win_start
    else:
        lo = win_start.astimezone(tz).replace(tzinfo=None) - duration - timedelta(days=1)
        hi = win_end.astimezone(tz).replace(tzinfo=None) + timedelta(days=1)
    result = []
    for s in rrset.between(lo, hi, inc=True):
        e = s + duration
        if overlaps(s, e):
            result.append(to_output(s, e))
    return result


def collect(
    client: TimeTreeClient,
    day: date,
    local_tz: ZoneInfo,
    calendar_filters: list[str],
    exclude_keep: bool = False,
) -> list[Occurrence]:
    cals = client.calendars()
    if calendar_filters:
        cals = [
            c
            for c in cals
            if any(f in (c["name"], c["alias_code"], str(c["id"])) for f in calendar_filters)
        ]
        if not cals:
            raise TimeTreeError(f"calendar not found: {', '.join(calendar_filters)}")

    results: list[Occurrence] = []
    for cal in cals:
        for ev in client.events(cal["id"]):
            if ev.get("deactivated_at"):
                continue
            if exclude_keep and ev.get("category") == CATEGORY_KEEP:
                continue
            for s, e in occurrences_on(ev, day, local_tz):
                if ev["all_day"]:
                    start_s, end_s = s.date().isoformat(), e.date().isoformat()
                else:
                    start_s, end_s = s.isoformat(), e.isoformat()
                results.append(
                    Occurrence(
                        calendar=cal["name"],
                        title=ev.get("title") or "",
                        all_day=ev["all_day"],
                        start=start_s,
                        end=end_s,
                        location=ev.get("location") or "",
                        note=ev.get("note") or "",
                        url=ev.get("url") or "",
                        category=ev.get("category", 1),
                    )
                )
    # 終日予定を先頭に、その後は開始時刻順
    results.sort(key=lambda o: (not o.all_day, o.start, o.title))
    return results


def add_fetch_arguments(parser: argparse.ArgumentParser) -> None:
    """予定の取得に使う引数を追加する (timetree / timetree-tts で共通)。"""
    parser.add_argument("date", nargs="?", help="YYYY-MM-DD (省略時は今日)")
    parser.add_argument(
        "-c", "--calendar", action="append", default=[],
        help="対象カレンダー (名前 / alias_code / id)。複数指定可。省略時は全カレンダー",
    )
    parser.add_argument("--tz", default="Asia/Tokyo", help="日付の解釈と表示に使うタイムゾーン (default: Asia/Tokyo)")
    parser.add_argument("--exclude-keep", action="store_true", help="キープ (category=2) を除外する")
    parser.add_argument(
        "--session-file", type=Path, default=default_session_file(),
        help="ログインセッションの保存先 (default: $XDG_CACHE_HOME/timetree/session.json)",
    )


def fetch(args: argparse.Namespace) -> tuple[date, list[Occurrence]]:
    """add_fetch_arguments で追加した引数に従って予定を取得する。"""
    local_tz = ZoneInfo(args.tz)
    day = date.fromisoformat(args.date) if args.date else datetime.now(local_tz).date()

    username = os.environ.get("TIMETREE_USERNAME")
    password = os.environ.get("TIMETREE_PASSWORD")
    if not username or not password:
        raise TimeTreeError("TIMETREE_USERNAME and TIMETREE_PASSWORD must be set")

    client = TimeTreeClient()
    client.login_with_cache(username, password, args.session_file)
    return day, collect(client, day, local_tz, args.calendar, args.exclude_keep)


def main() -> int:
    parser = argparse.ArgumentParser(description="TimeTree の指定日の予定を取得する")
    add_fetch_arguments(parser)
    parser.add_argument("--json", action="store_true", help="JSON で出力する")
    args = parser.parse_args()

    try:
        _, results = fetch(args)
    except (TimeTreeError, requests.RequestException) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([asdict(o) for o in results], ensure_ascii=False, indent=2))
    else:
        for o in results:
            print(o.format())
    return 0


if __name__ == "__main__":
    sys.exit(main())
