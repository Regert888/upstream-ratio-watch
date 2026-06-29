from __future__ import annotations

import hashlib
import json
import smtplib
import sqlite3
import threading
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
DB_PATH = DATA_DIR / "app.db"
DEFAULT_INTERVAL_MINUTES = 3
MIN_INTERVAL_MINUTES = 1
HTTP_TIMEOUT_SECONDS = 15
SCAN_INTERVAL_SECONDS = 10

DB_LOCK = threading.RLock()
STOP_EVENT = threading.Event()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_iso_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize_base_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return value
    return value


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    with DB_LOCK, connect_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                base_url TEXT NOT NULL UNIQUE,
                platform TEXT NOT NULL DEFAULT 'newapi',
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_minutes INTEGER NOT NULL DEFAULT 3,
                focus_keywords TEXT,
                login_enabled INTEGER NOT NULL DEFAULT 0,
                login_username TEXT,
                login_password TEXT,
                access_token TEXT,
                access_user_id TEXT,
                status TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT,
                last_check_at TEXT,
                next_check_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                current_groups_json TEXT,
                current_login_groups_json TEXT,
                login_last_error TEXT,
                login_last_check_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '/api/user/groups',
                groups_json TEXT,
                raw_json TEXT,
                hash TEXT,
                error_message TEXT,
                checked_at TEXT NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL,
                change_type TEXT NOT NULL,
                group_name TEXT,
                old_value TEXT,
                new_value TEXT,
                change_percent REAL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                acknowledged INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(site_id) REFERENCES sites(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                qq_enabled INTEGER NOT NULL DEFAULT 0,
                qq_app_id TEXT,
                qq_client_secret TEXT,
                qq_group_openid TEXT,
                qq_access_token TEXT,
                qq_token_expires_at TEXT,
                qq_last_error TEXT,
                qq_last_sent_at TEXT,
                email_enabled INTEGER NOT NULL DEFAULT 0,
                smtp_host TEXT,
                smtp_port INTEGER NOT NULL DEFAULT 465,
                smtp_username TEXT,
                smtp_password TEXT,
                smtp_use_ssl INTEGER NOT NULL DEFAULT 1,
                smtp_from TEXT,
                smtp_to TEXT,
                email_last_error TEXT,
                email_last_sent_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                target TEXT,
                message TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_sites_enabled_next_check ON sites(enabled, next_check_at);
            CREATE INDEX IF NOT EXISTS idx_snapshots_site_checked ON snapshots(site_id, checked_at DESC);
            CREATE INDEX IF NOT EXISTS idx_changes_site_created ON changes(site_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_notification_logs_created ON notification_logs(created_at DESC);
            """
        )
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(sites)").fetchall()
        }
        if "focus_keywords" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN focus_keywords TEXT")
        if "login_enabled" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_enabled INTEGER NOT NULL DEFAULT 0")
        if "login_username" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_username TEXT")
        if "login_password" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_password TEXT")
        if "access_token" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN access_token TEXT")
        if "access_user_id" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN access_user_id TEXT")
        if "current_login_groups_json" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN current_login_groups_json TEXT")
        if "login_last_error" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_last_error TEXT")
        if "login_last_check_at" not in columns:
            conn.execute("ALTER TABLE sites ADD COLUMN login_last_check_at TEXT")
        conn.execute("UPDATE sites SET login_username = '', login_password = ''")
        setting_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(notification_settings)").fetchall()
        }
        notification_columns = {
            "email_enabled": "INTEGER NOT NULL DEFAULT 0",
            "smtp_host": "TEXT",
            "smtp_port": "INTEGER NOT NULL DEFAULT 465",
            "smtp_username": "TEXT",
            "smtp_password": "TEXT",
            "smtp_use_ssl": "INTEGER NOT NULL DEFAULT 1",
            "smtp_from": "TEXT",
            "smtp_to": "TEXT",
            "email_last_error": "TEXT",
            "email_last_sent_at": "TEXT",
        }
        for column_name, column_type in notification_columns.items():
            if column_name not in setting_columns:
                conn.execute(f"ALTER TABLE notification_settings ADD COLUMN {column_name} {column_type}")
        setting = conn.execute("SELECT id FROM notification_settings WHERE id = 1").fetchone()
        if not setting:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO notification_settings
                (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, qq_access_token, qq_token_expires_at, qq_last_error, qq_last_sent_at, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, email_last_error, email_last_sent_at, created_at, updated_at)
                VALUES (1, 0, '', '', '', NULL, NULL, NULL, NULL, 0, '', 465, '', '', 1, '', '', NULL, NULL, ?, ?)
                """,
                (now, now),
            )
        conn.execute("UPDATE notification_settings SET qq_enabled = 0, qq_last_error = NULL")


def dict_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def db_query_all(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with DB_LOCK, connect_db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict_from_row(row) for row in rows]


def db_query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    with DB_LOCK, connect_db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
        return dict_from_row(row) if row else None


def db_execute(sql: str, params: Iterable[Any] = ()) -> int:
    with DB_LOCK, connect_db() as conn:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid


def db_execute_many(sql: str, params_list: Iterable[Iterable[Any]]) -> None:
    with DB_LOCK, connect_db() as conn:
        conn.executemany(sql, params_list)
        conn.commit()


def json_request(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    method: str = "POST",
) -> Tuple[int, Dict[str, Any], str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
    }
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        try:
            payload_obj = json.loads(raw) if raw else {}
        except Exception:
            payload_obj = {"raw": raw}
        if not isinstance(payload_obj, dict):
            payload_obj = {"raw": raw}
        return resp.status, payload_obj, raw


def parse_groups_payload(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for name in sorted(data.keys()):
        info = data.get(name) or {}
        if not isinstance(info, dict):
            info = {}

        ratio = info.get("ratio")
        if isinstance(ratio, (int, float)):
            ratio_value: Any = float(ratio)
            ratio_type = "number"
        elif isinstance(ratio, str):
            stripped = ratio.strip()
            try:
                ratio_value = float(stripped)
                ratio_type = "number"
            except ValueError:
                ratio_value = stripped
                ratio_type = "text"
        else:
            ratio_value = ratio
            ratio_type = "text"

        normalized[name] = {
            "ratio": ratio_value,
            "ratio_type": ratio_type,
            "desc": info.get("desc", ""),
        }
    return normalized


def stable_hash(obj: Any) -> str:
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def next_check_iso(interval_minutes: int) -> str:
    return (datetime.now().astimezone() + timedelta(minutes=max(MIN_INTERVAL_MINUTES, interval_minutes))).isoformat(timespec="seconds")


def fetch_newapi_groups(base_url: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    url = f"{normalize_base_url(base_url)}/api/user/groups"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Upstream-Ratio-Watch/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            if not isinstance(payload, dict) or not payload.get("success"):
                return False, payload if isinstance(payload, dict) else {"raw": body}, "success=false"
            return True, payload, None
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        return False, {"status": exc.code, "raw": raw}, f"HTTP {exc.code}"
    except Exception as exc:
        return False, {"error": str(exc)}, str(exc)


def fetch_newapi_groups_with_access_token(base_url: str, access_token: str, user_id: str = "") -> Tuple[bool, Dict[str, Any], Optional[str]]:
    token = (access_token or "").strip()
    if not token:
        return False, {}, "访问令牌为空"

    headers = {
        "Accept": "application/json",
        "User-Agent": "Upstream-Ratio-Watch/1.0",
        "Authorization": token.removeprefix("Bearer ").removeprefix("bearer ").strip(),
    }
    if str(user_id or "").strip():
        headers["New-Api-User"] = str(user_id).strip()
    errors: List[str] = []
    for path in ("/api/user/self/groups", "/api/user/groups"):
        url = f"{normalize_base_url(base_url)}{path}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("success"):
                    return True, payload, None
                message = payload.get("message") if isinstance(payload, dict) else None
                errors.append(f"{path}: {message or 'success=false'}")
        except urllib.error.HTTPError as exc:
            errors.append(f"{path}: HTTP {exc.code}")
        except Exception as exc:
            errors.append(f"{path}: {exc}")

    return False, {"errors": errors}, "访问令牌分组采集失败：" + "；".join(errors)


def probe_newapi_groups(base_url: str) -> Dict[str, Any]:
    ok, payload, error_message = fetch_newapi_groups(base_url)
    if not ok:
        return {
            "success": False,
            "message": error_message or "request failed",
            "groups_count": 0,
            "groups": {},
            "raw": payload,
        }

    groups = parse_groups_payload(payload)
    return {
        "success": True,
        "message": "ok",
        "groups_count": len(groups),
        "groups": groups,
    }


def get_last_success_snapshot(site_id: int) -> Optional[Dict[str, Any]]:
    return db_query_one(
        """
        SELECT * FROM snapshots
        WHERE site_id = ? AND status = 'success'
        ORDER BY id DESC
        LIMIT 1
        """,
        (site_id,),
    )


def diff_groups(old_groups: Dict[str, Dict[str, Any]], new_groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    old_names = set(old_groups.keys())
    new_names = set(new_groups.keys())

    for name in sorted(new_names - old_names):
        changes.append({
            "change_type": "group_added",
            "group_name": name,
            "old_value": None,
            "new_value": new_groups[name],
            "change_percent": None,
            "message": f"新增分组 {name}",
        })

    for name in sorted(old_names - new_names):
        changes.append({
            "change_type": "group_removed",
            "group_name": name,
            "old_value": old_groups[name],
            "new_value": None,
            "change_percent": None,
            "message": f"删除分组 {name}",
        })

    for name in sorted(old_names & new_names):
        old_item = old_groups[name]
        new_item = new_groups[name]
        if old_item.get("ratio") != new_item.get("ratio"):
            old_ratio = old_item.get("ratio")
            new_ratio = new_item.get("ratio")
            change_percent = None
            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)) and old_ratio != 0:
                change_percent = round((float(new_ratio) - float(old_ratio)) / float(old_ratio) * 100, 2)

            if isinstance(old_ratio, (int, float)) and isinstance(new_ratio, (int, float)):
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"
            else:
                message = f"{name} 倍率 {old_ratio} -> {new_ratio}"

            changes.append({
                "change_type": "ratio_changed",
                "group_name": name,
                "old_value": old_item,
                "new_value": new_item,
                "change_percent": change_percent,
                "message": message,
            })

        if old_item.get("desc") != new_item.get("desc"):
            changes.append({
                "change_type": "desc_changed",
                "group_name": name,
                "old_value": old_item.get("desc"),
                "new_value": new_item.get("desc"),
                "change_percent": None,
                "message": f"{name} 描述变化",
            })

    return changes


def get_notification_settings() -> Dict[str, Any]:
    row = db_query_one("SELECT * FROM notification_settings WHERE id = 1")
    if row:
        return row
    now = utc_now_iso()
    db_execute(
        """
        INSERT OR IGNORE INTO notification_settings
        (id, qq_enabled, qq_app_id, qq_client_secret, qq_group_openid, email_enabled, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_from, smtp_to, created_at, updated_at)
        VALUES (1, 0, '', '', '', 0, '', 465, '', '', 1, '', '', ?, ?)
        """,
        (now, now),
    )
    return db_query_one("SELECT * FROM notification_settings WHERE id = 1") or {}


def notification_settings_payload() -> Dict[str, Any]:
    settings = get_notification_settings()
    return {
        "email_enabled": bool(settings.get("email_enabled")),
        "smtp_host": settings.get("smtp_host") or "",
        "smtp_port": int(settings.get("smtp_port") or 465),
        "smtp_username": settings.get("smtp_username") or "",
        "has_smtp_password": bool(settings.get("smtp_password")),
        "smtp_use_ssl": bool(settings.get("smtp_use_ssl")),
        "smtp_from": settings.get("smtp_from") or "",
        "smtp_to": settings.get("smtp_to") or "",
        "email_last_error": settings.get("email_last_error"),
        "email_last_sent_at": settings.get("email_last_sent_at"),
        "updated_at": settings.get("updated_at"),
    }


def update_notification_settings(body: Dict[str, Any]) -> None:
    settings = get_notification_settings()
    email_enabled = bool(body.get("email_enabled", False))
    smtp_host = str(body.get("smtp_host") or "").strip()
    smtp_port = int(body.get("smtp_port") or 465)
    smtp_username = str(body.get("smtp_username") or "").strip()
    smtp_password = str(body.get("smtp_password") or "")
    smtp_use_ssl = bool(body.get("smtp_use_ssl", True))
    smtp_from = str(body.get("smtp_from") or "").strip()
    smtp_to = str(body.get("smtp_to") or "").strip()

    if email_enabled:
        if not smtp_host or not smtp_port or not smtp_username or not (smtp_password or settings.get("smtp_password")) or not smtp_to:
            raise ValueError("启用邮箱推送时需要填写 SMTP 服务器、端口、账号、密码和收件人")
        if not smtp_from:
            smtp_from = smtp_username

    fields = [
        "qq_enabled = 0",
        "email_enabled = ?",
        "smtp_host = ?",
        "smtp_port = ?",
        "smtp_username = ?",
        "smtp_use_ssl = ?",
        "smtp_from = ?",
        "smtp_to = ?",
        "updated_at = ?",
    ]
    params: List[Any] = [
        1 if email_enabled else 0,
        smtp_host,
        smtp_port,
        smtp_username,
        1 if smtp_use_ssl else 0,
        smtp_from,
        smtp_to,
        utc_now_iso(),
    ]
    if smtp_password:
        fields.append("smtp_password = ?")
        params.append(smtp_password)
    params.append(1)
    db_execute(f"UPDATE notification_settings SET {', '.join(fields)} WHERE id = ?", params)


def log_notification(channel: str, status: str, target: str, message: str, error_message: Optional[str] = None) -> None:
    db_execute(
        """
        INSERT INTO notification_logs (channel, status, target, message, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (channel, status, target, message, error_message, utc_now_iso()),
    )


def send_email_message(subject: str, message: str) -> Tuple[bool, Optional[str]]:
    settings = get_notification_settings()
    if not settings.get("email_enabled"):
        return True, "邮箱推送未启用，未发送测试邮件"

    smtp_host = str(settings.get("smtp_host") or "").strip()
    smtp_port = int(settings.get("smtp_port") or 465)
    smtp_username = str(settings.get("smtp_username") or "").strip()
    smtp_password = str(settings.get("smtp_password") or "")
    smtp_from = str(settings.get("smtp_from") or smtp_username).strip()
    smtp_to = str(settings.get("smtp_to") or "").strip()
    smtp_use_ssl = bool(settings.get("smtp_use_ssl"))
    if not smtp_host or not smtp_port or not smtp_username or not smtp_password or not smtp_to:
        return False, "邮箱 SMTP 配置不完整"

    recipients = [item.strip() for item in smtp_to.replace("，", ",").split(",") if item.strip()]
    email = EmailMessage()
    email["Subject"] = subject
    email["From"] = smtp_from
    email["To"] = ", ".join(recipients)
    email.set_content(message)

    try:
        if smtp_use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
                smtp.starttls()
                smtp.login(smtp_username, smtp_password)
                smtp.send_message(email)
    except Exception as exc:
        error = f"邮箱推送失败：{exc}"
        db_execute(
            "UPDATE notification_settings SET email_last_error = ?, updated_at = ? WHERE id = 1",
            (error, utc_now_iso()),
        )
        log_notification("email", "failed", smtp_to, message, error)
        return False, error

    sent_at = utc_now_iso()
    db_execute(
        """
        UPDATE notification_settings
        SET email_last_error = NULL, email_last_sent_at = ?, updated_at = ?
        WHERE id = 1
        """,
        (sent_at, sent_at),
    )
    log_notification("email", "success", smtp_to, message, None)
    return True, None


def format_change_value(raw: Any) -> str:
    if raw is None:
        return "-"
    if isinstance(raw, dict) and "ratio" in raw:
        ratio = raw.get("ratio")
        try:
            return f"{float(ratio):.2f}x"
        except Exception:
            return str(ratio)
    return str(raw)


def ratio_number(raw: Any) -> Optional[float]:
    if isinstance(raw, dict):
        raw = raw.get("ratio")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def ratio_direction(change: Dict[str, Any]) -> str:
    old_ratio = ratio_number(change.get("old_value"))
    new_ratio = ratio_number(change.get("new_value"))
    if old_ratio is None or new_ratio is None:
        return "changed"
    if new_ratio > old_ratio:
        return "up"
    if new_ratio < old_ratio:
        return "down"
    return "changed"


def percent_text(change: Dict[str, Any]) -> str:
    percent = change.get("change_percent")
    if isinstance(percent, (int, float)):
        return f"{abs(percent):.2f}".rstrip("0").rstrip(".") + "%"
    return ""


def fmt_local_time_for_message(value: str) -> str:
    dt = parse_iso_dt(value)
    if not dt:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_change_subject(site: Dict[str, Any], changes: List[Dict[str, Any]]) -> str:
    site_name = site["name"]
    ratio_changes = [item for item in changes if item.get("change_type") == "ratio_changed"]
    if len(ratio_changes) == 1:
        change = ratio_changes[0]
        label = "倍率上涨" if ratio_direction(change) == "up" else "倍率下降" if ratio_direction(change) == "down" else "倍率变动"
        return f"【{label}】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}"
    if len(ratio_changes) > 1:
        return f"【倍率变动】{site_name}：{len(ratio_changes)} 个分组有变化"

    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    if len(added) == 1 and not removed:
        change = added[0]
        return f"【新增分组】{site_name} / {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}"
    if len(removed) == 1 and not added:
        change = removed[0]
        return f"【删除分组】{site_name} / {change.get('group_name') or '-'}"
    return f"【分组变化】{site_name}：{len(changes)} 条变化"


def format_change_notification(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> str:
    up_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "up"]
    down_changes = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "down"]
    changed_ratio = [item for item in changes if item.get("change_type") == "ratio_changed" and ratio_direction(item) == "changed"]
    added = [item for item in changes if item.get("change_type") == "group_added"]
    removed = [item for item in changes if item.get("change_type") == "group_removed"]
    desc_changed = [item for item in changes if item.get("change_type") == "desc_changed"]

    lines = [
        "NewAPI 倍率哨兵",
        f"站点：{site['name']}",
        f"时间：{fmt_local_time_for_message(checked_at)}",
        f"本次共 {len(changes)} 条变化",
    ]

    def append_ratio_block(title: str, items: List[Dict[str, Any]], suffix: str) -> None:
        if not items:
            return
        lines.extend(["", title])
        for change in items[:6]:
            percent = percent_text(change)
            extra = f"，{suffix} {percent}" if percent else f"，{suffix}"
            lines.append(
                f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}{extra}"
            )

    append_ratio_block("涨价了，钱包先别眨眼：", up_changes, "上涨")
    append_ratio_block("降价了，这波可以多看两眼：", down_changes, "下降")

    if changed_ratio:
        lines.extend(["", "倍率变了，但方向不太好判断："])
        for change in changed_ratio[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('old_value'))} -> {format_change_value(change.get('new_value'))}")

    if added:
        lines.extend(["", "新分组上线："])
        for change in added[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：{format_change_value(change.get('new_value'))}")

    if removed:
        lines.extend(["", "分组下线了："])
        for change in removed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}：原倍率 {format_change_value(change.get('old_value'))}")

    if desc_changed:
        lines.extend(["", "描述有变化："])
        for change in desc_changed[:6]:
            lines.append(f"- {change.get('group_name') or '-'}")

    if len(changes) > 8:
        lines.append("")
        lines.append(f"其余 {len(changes) - 8} 条变化请在面板查看")
    return "\n".join(lines)


def notify_changes(site: Dict[str, Any], changes: List[Dict[str, Any]], checked_at: str) -> None:
    if not changes:
        return
    send_email_message(format_change_subject(site, changes), format_change_notification(site, changes, checked_at))


def detect_site(site_id: int) -> Dict[str, Any]:
    site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        return {"success": False, "message": "site not found"}

    checked_at = utc_now_iso()
    ok, payload, error_message = fetch_newapi_groups(site["base_url"])
    latest_success = get_last_success_snapshot(site_id)

    if not ok:
        db_execute(
            """
            INSERT INTO snapshots (site_id, status, source, raw_json, error_message, checked_at, hash)
            VALUES (?, 'failed', '/api/user/groups', ?, ?, ?, NULL)
            """,
            (site_id, json.dumps(payload, ensure_ascii=False), error_message, checked_at),
        )

        consecutive_failures = int(site["consecutive_failures"] or 0) + 1
        status = "failed" if consecutive_failures >= 3 else "warning"
        next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
        db_execute(
            """
            UPDATE sites
            SET status = ?, last_error = ?, last_check_at = ?, next_check_at = ?, consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error_message, checked_at, next_check_at, consecutive_failures, checked_at, site_id),
        )
        return {"success": False, "message": error_message, "status": status}

    new_groups = parse_groups_payload(payload)
    groups_json = json.dumps(new_groups, ensure_ascii=False, sort_keys=True)
    hash_value = stable_hash(new_groups)
    login_groups: Dict[str, Dict[str, Any]] = {}
    login_groups_json: Optional[str] = None
    login_error: Optional[str] = None

    db_execute(
        """
        INSERT INTO snapshots (site_id, status, source, groups_json, raw_json, hash, error_message, checked_at)
        VALUES (?, 'success', '/api/user/groups', ?, ?, ?, NULL, ?)
        """,
        (site_id, groups_json, json.dumps(payload, ensure_ascii=False), hash_value, checked_at),
    )

    changes: List[Dict[str, Any]] = []
    if latest_success and latest_success.get("groups_json"):
        try:
            old_groups = json.loads(latest_success["groups_json"])
            if isinstance(old_groups, dict):
                changes = diff_groups(old_groups, new_groups)
        except Exception:
            changes = []

    if site.get("login_enabled") and site.get("access_token") and site.get("access_user_id"):
        login_ok, login_payload, login_error_message = fetch_newapi_groups_with_access_token(
            site["base_url"],
            site["access_token"],
            site.get("access_user_id") or "",
        )
        if login_ok:
            login_groups = parse_groups_payload(login_payload)
            login_groups_json = json.dumps(login_groups, ensure_ascii=False, sort_keys=True)
            old_login_groups = {}
            if site.get("current_login_groups_json"):
                try:
                    parsed_old_login = json.loads(site["current_login_groups_json"])
                    if isinstance(parsed_old_login, dict):
                        old_login_groups = parsed_old_login
                except Exception:
                    old_login_groups = {}
            login_changes = diff_groups(old_login_groups, login_groups) if old_login_groups else []
            for change in login_changes:
                change["message"] = f"认证增强 {change['message']}"
            changes.extend(login_changes)
        else:
            login_error = login_error_message or "认证增强采集失败"

    for change in changes:
        severity = "info"
        if change["change_type"] in {"group_removed"}:
            severity = "critical"
        elif change["change_type"] == "ratio_changed":
            percent = change.get("change_percent")
            if isinstance(percent, (int, float)) and percent > 0:
                severity = "warning"

        db_execute(
            """
            INSERT INTO changes
            (site_id, change_type, group_name, old_value, new_value, change_percent, message, created_at, acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                site_id,
                change["change_type"],
                change.get("group_name"),
                json.dumps(change.get("old_value"), ensure_ascii=False) if change.get("old_value") is not None else None,
                json.dumps(change.get("new_value"), ensure_ascii=False) if change.get("new_value") is not None else None,
                change.get("change_percent"),
                change["message"],
                checked_at,
            ),
        )
        change["severity"] = severity

    notify_changes(site, changes, checked_at)

    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
    effective_status = "warning" if login_error else "ok"
    db_execute(
        """
        UPDATE sites
        SET status = ?,
            last_error = NULL,
            last_check_at = ?,
            next_check_at = ?,
            consecutive_failures = 0,
            current_groups_json = ?,
            current_login_groups_json = COALESCE(?, current_login_groups_json),
            login_last_error = ?,
            login_last_check_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            effective_status,
            checked_at,
            next_check_at,
            groups_json,
            login_groups_json,
            login_error,
            checked_at if site.get("login_enabled") else None,
            checked_at,
            site_id,
        ),
    )

    return {
        "success": not bool(login_error),
        "message": login_error or "ok",
        "checked_at": checked_at,
        "groups": new_groups,
        "login_groups": login_groups,
        "changes": changes,
    }


def schedule_worker() -> None:
    while not STOP_EVENT.is_set():
        try:
            now = datetime.now().astimezone()
            due_sites = db_query_all(
                """
                SELECT * FROM sites
                WHERE enabled = 1
                  AND platform = 'newapi'
                  AND (next_check_at IS NULL OR next_check_at <= ?)
                ORDER BY
                  CASE WHEN next_check_at IS NULL THEN 0 ELSE 1 END,
                  next_check_at ASC,
                  id ASC
                """,
                (now.isoformat(timespec="seconds"),),
            )
            for site in due_sites:
                if STOP_EVENT.is_set():
                    break
                try:
                    detect_site(int(site["id"]))
                except Exception:
                    checked_at = utc_now_iso()
                    err = traceback.format_exc(limit=2)
                    consecutive_failures = int(site["consecutive_failures"] or 0) + 1
                    next_check_at = next_check_iso(int(site["interval_minutes"] or DEFAULT_INTERVAL_MINUTES))
                    db_execute(
                        """
                        UPDATE sites
                        SET status = ?,
                            last_error = ?,
                            last_check_at = ?,
                            next_check_at = ?,
                            consecutive_failures = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            "failed" if consecutive_failures >= 3 else "warning",
                            err,
                            checked_at,
                            next_check_at,
                            consecutive_failures,
                            checked_at,
                            site["id"],
                        ),
                    )
        except Exception:
            pass
        STOP_EVENT.wait(SCAN_INTERVAL_SECONDS)


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    raw = handler.rfile.read(length).decode("utf-8") if length > 0 else "{}"
    return json.loads(raw or "{}")


def site_summary(site: Dict[str, Any]) -> Dict[str, Any]:
    groups = {}
    login_groups = {}
    if site.get("current_groups_json"):
        try:
            groups = json.loads(site["current_groups_json"]) or {}
        except Exception:
            groups = {}
    if site.get("current_login_groups_json"):
        try:
            login_groups = json.loads(site["current_login_groups_json"]) or {}
        except Exception:
            login_groups = {}
    latest_snapshot = db_query_one(
        "SELECT checked_at, status, error_message FROM snapshots WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
    )
    latest_change = db_query_one(
        "SELECT * FROM changes WHERE site_id = ? ORDER BY id DESC LIMIT 1",
        (site["id"],),
    )
    return {
        "id": site["id"],
        "name": site["name"],
        "base_url": site["base_url"],
        "platform": site["platform"],
        "enabled": bool(site["enabled"]),
        "interval_minutes": site["interval_minutes"],
        "login_enabled": bool(site.get("login_enabled")),
        "has_access_token": bool(site.get("access_token")),
        "access_user_id": site.get("access_user_id") or "",
        "login_last_error": site.get("login_last_error"),
        "login_last_check_at": site.get("login_last_check_at"),
        "status": site["status"],
        "last_error": site["last_error"],
        "last_check_at": site["last_check_at"],
        "next_check_at": site["next_check_at"],
        "consecutive_failures": site["consecutive_failures"],
        "current_groups": groups,
        "current_groups_count": len(groups) if isinstance(groups, dict) else 0,
        "current_login_groups": login_groups,
        "current_login_groups_count": len(login_groups) if isinstance(login_groups, dict) else 0,
        "latest_snapshot": latest_snapshot,
        "latest_change": latest_change,
    }


def overview_payload() -> Dict[str, Any]:
    sites = db_query_all("SELECT * FROM sites ORDER BY id DESC")
    changes = db_query_all("SELECT * FROM changes ORDER BY id DESC LIMIT 8")
    totals = {
        "sites_total": len(sites),
        "sites_enabled": sum(1 for s in sites if s["enabled"]),
        "sites_ok": sum(1 for s in sites if s["status"] == "ok"),
        "sites_failed": sum(1 for s in sites if s["status"] in {"failed", "warning"}),
        "changes_today": db_query_one(
            "SELECT COUNT(*) AS count FROM changes WHERE created_at >= ?",
            (datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),),
        ) or {"count": 0},
    }
    return {
        "stats": {
            "sites_total": totals["sites_total"],
            "sites_enabled": totals["sites_enabled"],
            "sites_ok": totals["sites_ok"],
            "sites_failed": totals["sites_failed"],
            "changes_today": totals["changes_today"]["count"],
        },
        "sites": [site_summary(site) for site in sites],
        "changes": changes,
    }


def list_sites_payload() -> List[Dict[str, Any]]:
    sites = db_query_all("SELECT * FROM sites ORDER BY id DESC")
    return [site_summary(site) for site in sites]


def list_snapshots(site_id: int) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM snapshots
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT 100
        """,
        (site_id,),
    )


def list_changes(limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        "SELECT * FROM changes ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def list_site_changes(site_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    return db_query_all(
        """
        SELECT * FROM changes
        WHERE site_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (site_id, limit),
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "NewAPIPriceWatch/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/":
            return self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._serve_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        if path == "/styles.css":
            return self._serve_file(STATIC_DIR / "styles.css", "text/css; charset=utf-8")

        if path == "/api/overview":
            return json_response(self, overview_payload())
        if path == "/api/sites":
            return json_response(self, {"data": list_sites_payload()})
        if path == "/api/changes":
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_changes(limit)})
        if path == "/api/notifications/settings":
            return json_response(self, {"data": notification_settings_payload()})
        if path == "/api/notifications/logs":
            return json_response(self, {"data": db_query_all("SELECT * FROM notification_logs ORDER BY id DESC LIMIT 30")})
        if path.startswith("/api/sites/") and path.endswith("/snapshots"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            return json_response(self, {"data": list_snapshots(site_id)})
        if path.startswith("/api/sites/") and path.endswith("/changes"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return self.send_error(HTTPStatus.BAD_REQUEST, "invalid site id")
            params = parse_qs(parsed.query)
            limit = int(params.get("limit", ["100"])[0] or 100)
            return json_response(self, {"data": list_site_changes(site_id, limit)})

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/api/check-connection":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                if not base_url:
                    return json_response(self, {"success": False, "message": "base_url required"}, 400)
                result = probe_newapi_groups(base_url)
                return json_response(self, result)

            if path == "/api/check-login":
                body = read_json_body(self)
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                if not base_url or not access_token or not access_user_id:
                    return json_response(self, {"success": False, "message": "Base URL、系统访问令牌、NewAPI 用户 ID 都需要填写"}, 400)
                groups_ok, groups_payload, groups_error = fetch_newapi_groups_with_access_token(base_url, access_token, access_user_id)
                groups = parse_groups_payload(groups_payload) if groups_ok else {}
                return json_response(self, {
                    "success": groups_ok,
                    "message": groups_error or "访问令牌验证成功",
                    "groups_count": len(groups),
                    "groups": groups,
                })

            if path == "/api/sites":
                body = read_json_body(self)
                name = str(body.get("name") or "").strip()
                base_url = normalize_base_url(str(body.get("base_url") or ""))
                enabled = bool(body.get("enabled", True))
                interval = int(body.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
                interval = max(MIN_INTERVAL_MINUTES, interval)
                login_enabled = bool(body.get("login_enabled", False))
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                platform = "newapi"
                if not name or not base_url:
                    return json_response(self, {"success": False, "message": "name/base_url required"}, 400)
                if login_enabled and (not access_token or not access_user_id):
                    return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                now = utc_now_iso()
                site_id = db_execute(
                    """
                    INSERT INTO sites
                    (name, base_url, platform, enabled, interval_minutes, login_enabled, login_username, login_password, access_token, access_user_id, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
                    """,
                    (
                        name,
                        base_url,
                        platform,
                        1 if enabled else 0,
                        interval,
                        1 if login_enabled else 0,
                        "",
                        "",
                        access_token if login_enabled else "",
                        access_user_id if login_enabled else "",
                        next_check_iso(interval),
                        now,
                        now,
                    ),
                )
                return json_response(self, {"success": True, "id": site_id})

            if path.startswith("/api/sites/") and path.endswith("/check"):
                try:
                    site_id = int(path.split("/")[3])
                except Exception:
                    return json_response(self, {"success": False, "message": "invalid site id"}, 400)
                result = detect_site(site_id)
                return json_response(self, result)

            if path == "/api/notifications/test-email":
                body = read_json_body(self)
                if body:
                    update_notification_settings(body)
                message = "这是一封 NewAPI 分组倍率监控测试邮件。"
                ok, error_message = send_email_message("NewAPI 邮箱推送测试", message)
                return json_response(self, {"success": ok, "message": error_message or "测试邮件已发送"})

            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:
            return json_response(self, {"success": False, "message": str(exc)}, 500)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            body = read_json_body(self)
            site = db_query_one("SELECT * FROM sites WHERE id = ?", (site_id,))
            if not site:
                return json_response(self, {"success": False, "message": "site not found"}, 404)
            fields = []
            params = []

            if "name" in body:
                fields.append("name = ?")
                params.append(str(body["name"]).strip())
            if "base_url" in body:
                fields.append("base_url = ?")
                params.append(normalize_base_url(str(body["base_url"])))
            if "enabled" in body:
                fields.append("enabled = ?")
                params.append(1 if body["enabled"] else 0)
            if "interval_minutes" in body:
                fields.append("interval_minutes = ?")
                params.append(max(MIN_INTERVAL_MINUTES, int(body["interval_minutes"])))
            if "login_enabled" in body:
                login_enabled = bool(body["login_enabled"])
                access_token = str(body.get("access_token") or "").strip()
                access_user_id = str(body.get("access_user_id") or "").strip()
                existing_access_token = site.get("access_token") or ""
                existing_access_user_id = site.get("access_user_id") or ""
                has_token_after_update = bool(access_token or existing_access_token)
                has_user_id_after_update = bool(access_user_id or existing_access_user_id)
                if login_enabled and (not has_token_after_update or not has_user_id_after_update):
                    return json_response(self, {"success": False, "message": "使用系统访问令牌时需要填写 NewAPI 用户 ID"}, 400)
                fields.append("login_enabled = ?")
                params.append(1 if login_enabled else 0)
                fields.append("login_username = ?")
                params.append("")
                fields.append("login_password = ?")
                params.append("")
                if not login_enabled:
                    fields.append("access_token = ?")
                    params.append("")
                    fields.append("access_user_id = ?")
                    params.append("")
                if login_enabled and access_token:
                    fields.append("access_token = ?")
                    params.append(access_token)
                if login_enabled and access_user_id:
                    fields.append("access_user_id = ?")
                    params.append(access_user_id)
            if "status" in body:
                fields.append("status = ?")
                params.append(str(body["status"]))

            if not fields:
                return json_response(self, {"success": False, "message": "no fields"}, 400)

            fields.append("updated_at = ?")
            params.append(utc_now_iso())
            params.append(site_id)

            db_execute(f"UPDATE sites SET {', '.join(fields)} WHERE id = ?", params)
            return json_response(self, {"success": True})

        if path == "/api/notifications/settings":
            body = read_json_body(self)
            try:
                update_notification_settings(body)
            except ValueError as exc:
                return json_response(self, {"success": False, "message": str(exc)}, 400)
            return json_response(self, {"success": True, "data": notification_settings_payload()})

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/sites/"):
            try:
                site_id = int(path.split("/")[3])
            except Exception:
                return json_response(self, {"success": False, "message": "invalid site id"}, 400)
            db_execute("DELETE FROM sites WHERE id = ?", (site_id,))
            return json_response(self, {"success": True})
        self.send_error(HTTPStatus.NOT_FOUND)


def bootstrap_demo_data() -> None:
    if db_query_one("SELECT id FROM sites LIMIT 1"):
        return

    now = utc_now_iso()
    db_execute(
        """
        INSERT INTO sites
        (name, base_url, platform, enabled, interval_minutes, status, last_error, last_check_at, next_check_at, consecutive_failures, current_groups_json, created_at, updated_at)
        VALUES (?, ?, 'newapi', 1, 3, 'unknown', NULL, NULL, ?, 0, NULL, ?, ?)
        """,
        (
            "Demo NewAPI",
            "http://127.0.0.1:3000",
            next_check_iso(3),
            now,
            now,
        ),
    )


def main() -> None:
    ensure_dirs()
    init_db()
    bootstrap_demo_data()

    worker = threading.Thread(target=schedule_worker, daemon=True)
    worker.start()

    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Upstream Ratio Watch running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
