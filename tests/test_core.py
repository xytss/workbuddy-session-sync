import importlib
import json
import sqlite3

import pytest


def api():
    assert importlib.util.find_spec('workbuddy_sync.core'), '尚未实现同步引擎'
    return importlib.import_module('workbuddy_sync.core')


def setup_data(root):
    home = root / 'buddy'
    home.mkdir()
    auth = root / 'auth.info'
    auth.write_text(json.dumps({'account': {'uid': 'A'}, 'auth': {'token': 'secret'}}))
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.executescript('''
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT,
                deleted_at INTEGER, is_background_automation INTEGER,
                updated_at INTEGER, cwd TEXT,
                status TEXT NOT NULL DEFAULT 'completed');
            INSERT INTO sessions
                (id,user_id,title,deleted_at,is_background_automation,updated_at,cwd)
                VALUES ('s1','A','对话',NULL,0,1,'F:/Code');
            INSERT INTO sessions
                (id,user_id,title,deleted_at,is_background_automation,updated_at,cwd)
                VALUES ('s2','B','另一对话',NULL,0,2,'F:/Code');
            INSERT INTO sessions
                (id,user_id,title,deleted_at,is_background_automation,updated_at,cwd)
                VALUES ('hidden','A','已归档',123,0,3,'F:/Code');
            INSERT INTO sessions
                (id,user_id,title,deleted_at,is_background_automation,updated_at,cwd)
                VALUES ('auto','A','定时任务',NULL,1,4,'F:/Code');
            CREATE TABLE session_usage (session_id TEXT, used INTEGER);
            INSERT INTO session_usage VALUES ('s1',12);
        ''')
    logs = home / 'projects'
    logs.mkdir()
    (logs / 's1.jsonl').write_text('历史消息\n', encoding='utf-8')
    return home, auth


def owners(home):
    with sqlite3.connect(home / 'workbuddy.db') as db:
        return dict(db.execute('SELECT id,user_id FROM sessions'))


def settings(root, home, auth, **kw):
    return api().Settings(data_dir=home, auth_file=auth, state_dir=root / 'state', **kw)


def test_roundtrip_keeps_ids_and_new_messages(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth)
    engine = api().Engine(cfg, running=lambda: False)
    auth.write_text('{"account":{"uid":"B"}}')
    result = engine.sync()
    assert result.changed == 1
    assert owners(home) == {'s1': 'B', 's2': 'B', 'hidden': 'A', 'auto': 'A'}
    log = home / 'projects' / 's1.jsonl'
    log.write_text(log.read_text(encoding='utf-8') + 'B 的新消息\n', encoding='utf-8')
    auth.write_text('{"account":{"uid":"N"}}')
    assert engine.sync().changed == 2
    auth.write_text('{"account":{"uid":"A"}}')
    assert engine.sync().changed == 2
    assert 'B 的新消息' in log.read_text(encoding='utf-8')
    with sqlite3.connect(home / 'workbuddy.db') as db:
        assert db.execute('SELECT used FROM session_usage').fetchone() == (12,)
        assert db.execute("SELECT updated_at FROM sessions WHERE id='s1'").fetchone() == (1,)


def test_running_client_syncs_after_online_account_switch(sandbox):
    home, auth = setup_data(sandbox)
    auth.write_text('{"account":{"uid":"B"}}')
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: True)
    result = engine.sync()
    assert result.status == 'synced'
    assert result.changed == 1
    assert owners(home)['s1'] == 'B'
    assert result.backup.is_dir()


def test_partial_auth_file_waits_and_recovers_without_disabling_auto_sync(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, auto_sync=True)
    engine = api().Engine(cfg, running=lambda: True)
    auth.write_text('{')
    assert engine.tick().status == 'switching'
    assert cfg.auto_sync
    auth.write_text('{"account":{"uid":"B"}}')
    assert engine.tick().status == 'synced'
    assert owners(home)['s1'] == 'B'


def test_live_database_write_lock_is_retried_on_next_tick(sandbox):
    home, auth = setup_data(sandbox)
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: True)
    with sqlite3.connect(home / 'workbuddy.db') as writer:
        writer.execute('BEGIN IMMEDIATE')
        assert engine.sync().status == 'busy'
        assert owners(home)['s2'] == 'B'


def test_logout_marker_and_missing_account_stop_sync(sandbox):
    home, auth = setup_data(sandbox)
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    auth.with_name(auth.name + '.logged-out').write_text('logout')
    assert engine.sync().status == 'logged_out'
    assert owners(home)['s2'] == 'B'


def test_selected_scope_and_idempotent_backup(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, session_ids=['s1'])
    engine = api().Engine(cfg, running=lambda: False)
    assert engine.sync().status == 'current'
    auth.write_text('{"account":{"uid":"B"}}')
    first = engine.sync()
    assert first.changed == 1
    assert engine.sync().status == 'current'
    assert len(list((sandbox / 'state' / 'backups').iterdir())) == 1
    with sqlite3.connect(first.backup / 'before.db') as db:
        assert db.execute("SELECT user_id FROM sessions WHERE id='s1'").fetchone() == ('A',)


def test_restore_only_ownership_preserves_later_messages(sandbox):
    home, auth = setup_data(sandbox)
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    result = engine.sync()
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET title='新标题',updated_at=99 WHERE id='s2'")
    assert engine.restore(result.backup).changed == 1
    assert owners(home)['s2'] == 'B'
    with sqlite3.connect(home / 'workbuddy.db') as db:
        assert db.execute("SELECT title,updated_at FROM sessions WHERE id='s2'").fetchone() == ('新标题',99)


def test_unknown_schema_fails_without_modification(sandbox):
    home, auth = setup_data(sandbox)
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute('ALTER TABLE sessions RENAME COLUMN user_id TO owner')
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    with pytest.raises(api().SyncError, match='结构'):
        engine.sync()


def test_new_settings_default_to_auto_sync_all_sessions():
    cfg = api().Settings()
    assert cfg.auto_sync
    assert cfg.session_ids is None


def test_auto_disabled_and_enabled(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, auto_sync=False)
    engine = api().Engine(cfg, running=lambda: False)
    assert engine.tick().status == 'disabled'
    cfg.auto_sync = True
    assert engine.tick().changed == 1
    assert engine.tick().status == 'current'


def test_auth_does_not_infer_from_most_sessions(sandbox):
    home, auth = setup_data(sandbox)
    auth.write_text('{"account":{"uid":"NEW"}}')
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    assert engine.preview().target == 'NEW'
    auth.write_text('{}')
    assert engine.sync().status == 'logged_out'


def test_accounts_include_current_and_historical_login_names(sandbox):
    home, auth = setup_data(sandbox)
    auth.write_text(json.dumps({'account': {'uid': 'A', 'nickname': 'Alice'}}))
    auth.with_name('auth.2026-09-16.info').write_text(
        json.dumps({'account': {'uid': 'B', 'nickname': 'Bob'}}))
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)

    accounts = engine.accounts()

    assert [(item.uid, item.name, item.current) for item in accounts] == [
        ('A', 'Alice', True),
        ('B', 'Bob', False),
    ]


def test_sessions_for_account_ignores_sync_selection_scope(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, session_ids=['s1'])
    engine = api().Engine(cfg, running=lambda: False)

    assert [row['id'] for row in engine.sessions_for_account('B')] == ['s2']


def test_archived_session_is_excluded_until_workbuddy_unarchives_it(sandbox):
    home, auth = setup_data(sandbox)
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET status='archived' WHERE id='s1'")

    assert [row['id'] for row in engine.preview().sessions] == ['s2']
    assert engine.sessions_for_account('A') == []

    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET status='completed' WHERE id='s1'")

    assert [row['id'] for row in engine.preview().sessions] == ['s1', 's2']


def test_settings_roundtrip(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, auto_sync=True, session_ids=['s1'])
    path = sandbox / 'settings.json'
    cfg.save(path)
    loaded = api().Settings.load(path)
    assert loaded == cfg
    assert 'secret' not in path.read_text()


def test_backup_includes_committed_wal(sandbox):
    home, auth = setup_data(sandbox)
    with sqlite3.connect(home / 'workbuddy.db') as active:
        active.execute('PRAGMA journal_mode=WAL')
        active.execute("UPDATE sessions SET title='WAL新标题' WHERE id='s2'")
        active.commit()
        engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
        result = engine.sync()
        with sqlite3.connect(result.backup / 'before.db') as backup:
            assert backup.execute("SELECT title FROM sessions WHERE id='s2'").fetchone() == ('WAL新标题',)


def test_running_client_already_current_does_not_request_exit(sandbox):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth, session_ids=['s1'])
    engine = api().Engine(cfg, running=lambda: True)
    assert engine.sync().status == 'current'
    assert not (sandbox / 'state' / 'backups').exists()
