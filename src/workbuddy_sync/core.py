from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psutil


class SyncError(Exception):
    """可向用户展示的同步错误。"""


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path.home() / '.workbuddy-ai')
    auth_file: Path = field(default_factory=lambda: Path.home() / (
        'AppData/Local/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop-ai.info'
    ))
    state_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / 'state')
    auto_sync: bool = True
    session_ids: list[str] | None = None
    poll_seconds: int = 3

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), default=str, ensure_ascii=False, indent=2),
                        encoding='utf-8')

    @classmethod
    def load(cls, path: Path):
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding='utf-8'))
        for key in ('data_dir', 'auth_file', 'state_dir'):
            data[key] = Path(data[key])
        return cls(**data)


@dataclass
class Result:
    status: str
    target: str = ''
    changed: int = 0
    backup: Path | None = None
    sessions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Account:
    uid: str
    name: str
    current: bool = False


def workbuddy_running() -> bool:
    """包含主程序以及命令行指向 WorkBuddy 的独立 Node 进程。"""
    for process in psutil.process_iter(['name']):
        try:
            name = (process.info['name'] or '').lower()
            if name in ('workbuddyai.exe', 'workbuddy.exe'):
                return True
            if name in ('node.exe', 'bun.exe'):
                try:
                    command = ' '.join(process.cmdline()).lower()
                except psutil.AccessDenied as exc:
                    raise SyncError('无法检查一个 Node/Bun 进程，请关闭相关进程后再同步。') from exc
                if 'workbuddy' in command:
                    return True
        except psutil.NoSuchProcess:
            continue
    return False


class Engine:
    def __init__(self, settings: Settings, running: Callable[[], bool] = workbuddy_running):
        self.settings = settings
        self.running = running

    @property
    def database(self):
        return (self.settings.data_dir / 'workbuddy.db').resolve()

    def account(self) -> str | None:
        path = self.settings.auth_file
        if path.with_name(path.name + '.logged-out').exists() or not path.exists():
            return ''
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeError):
            # WorkBuddy 切换账号时会重写此文件；下一轮读取稳定内容即可。
            return None
        uid = data.get('account', {}).get('uid')
        return uid.strip() if isinstance(uid, str) else ''

    def accounts(self) -> list[Account]:
        current_uid = self.account()
        path = self.settings.auth_file
        snapshots = sorted(
            path.parent.glob(f'{path.stem}.*{path.suffix}'), key=lambda item: item.name,
            reverse=True,
        )
        records: dict[str, Account] = {}
        for source in [path, *snapshots]:
            if not source.exists():
                continue
            try:
                data = json.loads(source.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, UnicodeError):
                continue
            candidates = [data.get('account')]
            for key in ('accounts', 'allAccounts'):
                value = data.get(key)
                if isinstance(value, list):
                    candidates.extend(value)
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                uid = candidate.get('uid')
                if not isinstance(uid, str) or not uid.strip():
                    continue
                uid = uid.strip()
                name = next((candidate.get(key).strip() for key in (
                    'nickname', 'displayName', 'name', 'username', 'email'
                ) if isinstance(candidate.get(key), str) and candidate.get(key).strip()),
                    '未命名账号')
                account = Account(uid, name, uid == current_uid)
                if uid not in records or account.current:
                    records[uid] = account
        return sorted(records.values(), key=lambda item: (not item.current, item.name.lower()))

    def connect(self, mode='ro'):
        db = sqlite3.connect(self.database.as_uri() + '?mode=' + mode, uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        columns = {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}
        if not {'id', 'user_id', 'title', 'deleted_at', 'is_background_automation'} <= columns:
            db.close()
            raise SyncError('数据库结构不符合 WorkBuddy 5.5.2 会话格式，已停止操作。')
        return db

    def all_rows(self, db):
        return [dict(row) for row in db.execute(
            'SELECT id,user_id,title FROM sessions WHERE deleted_at IS NULL '
            'AND COALESCE(is_background_automation,0)=0 ORDER BY id'
        )]

    def rows(self, db):
        rows = self.all_rows(db)
        selected = self.settings.session_ids
        return rows if selected is None else [r for r in rows if r['id'] in selected]

    def sessions_for_account(self, uid: str):
        with closing(self.connect()) as db:
            return [row for row in self.all_rows(db) if row['user_id'] == uid]

    def preview(self):
        target = self.account()
        with closing(self.connect()) as db:
            rows = self.rows(db)
        if target is None:
            return Result('switching', sessions=rows)
        changed = sum(r['user_id'] != target for r in rows) if target else 0
        return Result('preview' if target else 'logged_out', target, changed, sessions=rows)

    def make_backup(self, target: str, changes: list[dict], kind: str):
        folder = self.settings.state_dir / 'backups' / (
            datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f') + '-' + uuid4().hex[:8]
        )
        folder.mkdir(parents=True)
        # 调用方持有 BEGIN IMMEDIATE；另一个只读连接备份已提交快照（包含 WAL）。
        with closing(self.connect()) as source, closing(sqlite3.connect(folder / 'before.db')) as dest:
            source.backup(dest)
        manifest = {'database': str(self.database), 'target': target, 'kind': kind,
                    'changes': changes, 'created_at': datetime.now(UTC).isoformat()}
        (folder / 'operation.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        return folder

    def sync(self):
        preview = self.preview()
        target = preview.target
        if preview.status == 'switching':
            return Result('switching')
        if not target:
            return Result('logged_out')
        if not preview.changed:
            return Result('current', target)
        try:
            with closing(self.connect('rw')) as db, db:
                db.execute('BEGIN IMMEDIATE')
                changes = [{'id': r['id'], 'before': r['user_id'], 'after': target}
                           for r in self.rows(db) if r['user_id'] != target]
                if not changes:
                    return Result('current', target)
                backup = self.make_backup(target, changes, 'sync')
                if self.account() != target:
                    db.rollback()
                    return Result('switching')
                for row in changes:
                    db.execute('UPDATE sessions SET user_id=? WHERE id=? AND user_id=?',
                               (target, row['id'], row['before']))
                if self.account() != target:
                    db.rollback()
                    return Result('switching')
        except sqlite3.OperationalError as exc:
            code = exc.sqlite_errorcode & 0xFF
            if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                return Result('busy', target)
            raise
        return Result('synced', target, len(changes), backup)

    def tick(self):
        return self.sync() if self.settings.auto_sync else Result('disabled')

    def restore(self, folder: Path):
        if self.running():
            return Result('waiting')
        manifest = json.loads((folder / 'operation.json').read_text(encoding='utf-8'))
        if Path(manifest['database']).resolve() != self.database:
            raise SyncError('该备份属于另一个数据库。')
        with closing(self.connect('rw')) as db, db:
            db.execute('BEGIN IMMEDIATE')
            changes = []
            for row in manifest['changes']:
                current = db.execute('SELECT user_id FROM sessions WHERE id=?',
                                     (row['id'],)).fetchone()
                if current is None:
                    raise SyncError('待恢复的会话已不存在，未执行恢复。')
                if current['user_id'] == row['before']:
                    continue
                if current['user_id'] != row['after']:
                    raise SyncError('会话已被后续操作接管，请先恢复较新的操作。')
                changes.append({'id': row['id'], 'before': row['after'], 'after': row['before']})
            if not changes:
                return Result('current')
            backup = self.make_backup('', changes, 'restore')
            if self.running():
                raise SyncError('WorkBuddy 已启动，未执行恢复。')
            for row in changes:
                db.execute('UPDATE sessions SET user_id=? WHERE id=?', (row['after'], row['id']))
            if self.running():
                raise SyncError('WorkBuddy 已启动，恢复事务已回滚。')
        return Result('restored', changed=len(changes), backup=backup)
