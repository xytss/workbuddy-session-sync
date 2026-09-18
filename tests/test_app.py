import importlib
import json
import os
import sqlite3
import subprocess
import time
import tkinter.font as tkfont
from pathlib import Path

from test_core import api, owners, settings, setup_data


def test_double_click_batch_starts_gui_from_project_directory(sandbox):
    project = Path(__file__).resolve().parents[1]
    launcher = project / '启动GUI.bat'
    fake_bin = sandbox / 'bin'
    fake_bin.mkdir()
    capture = sandbox / 'launch.txt'
    (fake_bin / 'uv.cmd').write_text(
        '@echo off\r\n'
        '> "%BAT_CAPTURE%" echo %CD%^|%*\r\n'
        'ping -n 4 127.0.0.1 >nul\r\n',
        encoding='ascii',
    )
    environment = os.environ.copy()
    environment['PATH'] = str(fake_bin) + os.pathsep + environment['PATH']
    environment['BAT_CAPTURE'] = str(capture)

    started = time.monotonic()
    result = subprocess.run(
        ['cmd.exe', '/d', '/c', str(launcher)], cwd=sandbox, env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    deadline = time.monotonic() + 2
    while not capture.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    launched_from, arguments = capture.read_text().strip().split('|', 1)
    assert Path(launched_from).resolve() == project
    assert arguments == 'run --locked workbuddy-sync gui'
    assert elapsed < 1.5, '启动脚本应在后台启动 GUI 后立即关闭黑色命令窗口'


def test_gui_enables_windows_dpi_awareness_before_creating_root(monkeypatch, sandbox):
    desktop_ui = importlib.import_module('workbuddy_sync.desktop_ui')
    events = []

    class FakeRoot:
        def mainloop(self):
            events.append('mainloop')

    monkeypatch.setattr(
        desktop_ui, 'enable_windows_dpi_awareness', lambda: events.append('dpi'))
    monkeypatch.setattr(
        desktop_ui.tk, 'Tk', lambda: events.append('root') or FakeRoot())
    monkeypatch.setattr(
        desktop_ui, 'Window', lambda root, path: events.append(('window', root, path)))

    config_path = sandbox / 'settings.json'
    desktop_ui.launch(config_path)

    assert events[0:2] == ['dpi', 'root']
    assert events[-1] == 'mainloop'


def cli():
    assert importlib.util.find_spec('workbuddy_sync.cli'), '尚未实现命令行'
    return importlib.import_module('workbuddy_sync.cli')


def test_diagnose_is_readonly_and_redacts_auth(sandbox, capsys):
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth)
    path = sandbox / 'settings.json'
    cfg.save(path)
    assert cli().main(['--config', str(path), 'diagnose']) == 0
    output = capsys.readouterr().out
    assert 'secret' not in output
    assert json.loads(output)['target'] == 'A'
    assert owners(home)['s2'] == 'B'


def test_configure_enables_auto_and_selected_sessions(sandbox):
    path = sandbox / 'settings.json'
    assert cli().main(['--config', str(path), 'configure', '--auto-sync', 'on',
                       '--scope', 'selected', '--session-id', 's1']) == 0
    cfg = api().Settings.load(path)
    assert cfg.auto_sync
    assert cfg.session_ids == ['s1']


def test_gui_settings_and_preview(sandbox, tk_root):
    assert importlib.util.find_spec('workbuddy_sync.gui'), '尚未实现设置界面'
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    cfg = settings(sandbox, home, auth)
    path = sandbox / 'settings.json'
    cfg.save(path)
    window = gui.Window(tk_root, path)
    window.refresh()
    assert len(window.tree.get_children()) == 2
    window.display_result(api().Result('switching'))
    assert '切换' in window.status.get()
    window.display_result(api().Result('busy'))
    assert '下一轮' in window.status.get()
    window.auto.set(True)
    window.save()
    assert api().Settings.load(path).auto_sync
    window.auto.set(False)
    window.save()


def test_gui_uses_three_pages_and_shows_account_name_and_history(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    current_uid = 'dcb78cfa-f171-49c9-b936-c8b4b874b416'
    auth.write_text(json.dumps({'account': {'uid': current_uid, 'nickname': 'Alice'}}))
    auth.with_name('auth.2026-09-16.info').write_text(
        json.dumps({'account': {'uid': 'B', 'nickname': 'Bob'}}))
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)
    assert [window.notebook.tab(tab, 'text') for tab in window.notebook.tabs()] == [
        '同步概览', '账号历史', '设置与恢复',
    ]
    assert window.current_account_name.get() == 'Alice'
    assert window.current_account_id.get() == current_uid
    assert set(window.account_tree.get_children()) == {current_uid, 'B'}
    window.account_tree.selection_set('B')
    window.show_account_history()
    assert window.history_account_name.get() == 'Bob'
    assert window.history_account_id.get() == 'B'
    assert window.history_tree.get_children() == ('s2',)
    assert window.restore_button.cget('style') == 'Warning.TButton'


def test_gui_navigation_has_stable_size_and_toggle_controls_have_no_x(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    assert tkfont.nametofont('TkDefaultFont', root=tk_root).actual('family') == (
        'Microsoft YaHei UI'
    )
    assert {button.cget('width') for button in window.nav_buttons} == {14}
    assert window.nav_buttons[0].cget('style') == 'NavSelected.TButton'
    assert window.auto_button.winfo_class() == 'TButton'
    assert window.auto_button.cget('text') == '自动同步：开启'
    assert window.all_scope_button.winfo_class() == 'TButton'
    assert window.all_scope_button.cget('style') == 'SegmentSelected.TButton'
    assert window.tree.cget('displaycolumns') == (
        'title', 'owner', 'id', 'fill')

    window.show_page(1)

    assert {button.cget('width') for button in window.nav_buttons} == {14}
    assert window.nav_buttons[0].cget('style') == 'Nav.TButton'
    assert window.nav_buttons[1].cget('style') == 'NavSelected.TButton'


def test_selected_scope_uses_visible_checkbox_column(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)
    window.all_sessions.set(False)
    window.toggle_scope()
    assert window.tree.cget('displaycolumns')[0] == 'selected'
    window.toggle_session('s1')
    assert window.selection_summary.get() == '已选 1 / 2'
    assert window.tree.set('s1', 'selected') == '☑'
    window.save()
    assert api().Settings.load(path).session_ids == ['s1']
    window.all_sessions.set(True)
    window.toggle_scope()
    assert 'selected' not in window.tree.cget('displaycolumns')


def test_selected_scope_can_select_or_clear_all_visible_sessions(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    window.set_scope(False)
    window.select_all_sessions()

    assert window.selected_sessions == {'s1', 's2'}
    assert window.selection_summary.get() == '已选 2 / 2'
    assert {window.tree.set(item, 'selected') for item in ('s1', 's2')} == {'☑'}

    window.clear_selected_sessions()

    assert window.selected_sessions == set()
    assert window.selection_summary.get() == '已选 0 / 2'


def test_bulk_selection_controls_only_appear_in_selected_scope(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    assert window.select_all_button.winfo_manager() == ''
    assert window.clear_selection_button.winfo_manager() == ''
    assert window.bulk_actions.winfo_manager() == ''
    assert '无需手动选择' in window.scope_hint.get()

    window.set_scope(False)

    assert window.select_all_button.winfo_manager() == 'pack'
    assert window.clear_selection_button.winfo_manager() == 'pack'
    assert window.bulk_actions.winfo_manager() == 'grid'
    assert '表格左侧' in window.scope_hint.get()


def test_sync_settings_are_grouped_as_borderless_rows_inside_one_card(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    assert window.scope_card.cget('style') == 'Card.TFrame'
    assert window.auto_row.cget('style') == 'CardContent.TFrame'
    assert window.scope_row.cget('style') == 'CardContent.TFrame'
    assert window.auto_button.master == window.auto_row
    assert window.all_scope_button.master == window.scope_row
    assert window.scope_separator.winfo_manager() == 'grid'


def test_overview_status_matches_auto_sync_and_actions_follow_selection(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth, auto_sync=True).save(path)

    window = gui.Window(tk_root, path)

    assert window.status_heading.get() == '自动同步运行中'
    assert '监测' in window.status.get()
    assert window.sync_button.cget('text') == '立即同步'
    assert window.refresh_button.cget('text') == '重新读取'
    assert window.open_button.instate(['disabled'])

    window.tree.selection_set('s1')
    window._update_action_states()
    assert not window.open_button.instate(['disabled'])

    window.tree.selection_remove('s1')
    window._update_action_states()
    assert window.open_button.instate(['disabled'])

    window.toggle_auto()
    assert window.status_heading.get() == '自动同步已关闭'
    assert not api().Settings.load(path).auto_sync


def test_overview_scope_and_session_selection_save_immediately(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    assert not hasattr(window, 'save_button')
    window.set_scope(False)
    assert api().Settings.load(path).session_ids == []

    window.toggle_session('s1')
    assert api().Settings.load(path).session_ids == ['s1']

    window.select_all_sessions()
    assert api().Settings.load(path).session_ids == ['s1', 's2']

    window.clear_selected_sessions()
    assert api().Settings.load(path).session_ids == []


def test_session_table_shows_full_ids_with_native_column_boundaries(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    session_id = '01e3e2ed-6cf0-4dba-a9a7-1234567890ab'
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET id=? WHERE id='s1'", (session_id,))
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    assert window.tree.set(session_id, 'id') == session_id
    assert int(window.tree.column('id', 'width')) >= 440
    assert tuple(window.tree.cget('displaycolumns'))[:3] == ('title', 'owner', 'id')


def test_session_owner_column_shows_the_full_account_name(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    account_name = 'demo-account@example.com'
    auth.write_text(json.dumps({'account': {'uid': 'A', 'nickname': account_name}}))
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    assert window.tree.set('s1', 'owner') == account_name


def test_owner_and_session_id_columns_are_adjacent_and_left_aligned(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    displayed = tuple(window.tree.cget('displaycolumns'))
    owner_index = displayed.index('owner')
    assert displayed[owner_index + 1] == 'id'
    assert str(window.tree.heading('owner', 'anchor')) == 'w'
    assert str(window.tree.heading('id', 'anchor')) == 'w'


def test_session_metadata_columns_use_left_aligned_fixed_headers(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    assert str(window.tree.heading('owner', 'anchor')) == 'w'
    assert str(window.tree.heading('id', 'anchor')) == 'w'
    assert window.tree.column('owner', 'stretch') == 0
    assert window.tree.column('id', 'stretch') == 0
    displayed = tuple(window.tree.cget('displaycolumns'))
    assert displayed[displayed.index('id'):displayed.index('id') + 2] == ('id', 'fill')


def test_session_table_caps_title_width_and_uses_trailing_fill_space(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    window._fit_session_table_columns(1800)

    displayed = tuple(window.tree.cget('displaycolumns'))
    total_width = sum(int(window.tree.column(key, 'width')) for key in displayed)
    assert int(window.tree.column('title', 'width')) <= 720
    assert displayed[-1] == 'fill'
    assert int(window.tree.column('fill', 'width')) > 1
    assert total_width == 1800


def test_session_table_blocks_column_resize_drag(sandbox, monkeypatch, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)
    event = type('Event', (), {'x': 10, 'y': 10})()

    monkeypatch.setattr(window.tree, 'identify_region', lambda _x, _y: 'separator')

    assert window._block_table_resize(event) == 'break'
    assert window.tree.bind('<B1-Motion>')


def test_escape_clears_session_highlight_and_disables_open(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)
    window.tree.selection_set('s1')
    window._update_action_states()

    assert tk_root.bind('<Escape>')
    window._clear_session_highlight()

    assert window.tree.selection() == ()
    assert window.open_button.instate(['disabled'])
    assert '取消' in window.status.get()


def test_action_buttons_show_help_on_hover(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    expected = {
        window.sync_button: '立即将当前同步范围应用到当前登录账号。',
        window.refresh_button: '重新读取当前账号和会话列表，不会修改会话数据。',
        window.open_button: '打开列表中高亮的会话。按 Esc 可取消高亮。',
    }
    for button, help_text in expected.items():
        button.event_generate('<Enter>')
        tk_root.update()
        assert window.action_help.get().startswith(help_text)
        assert window.action_help_label.winfo_manager() == 'grid'
        button.event_generate('<Leave>')
        tk_root.update()
        assert window.action_help.get() == '鼠标停留在按钮上查看说明 · 同步规则会自动保存'


def test_restored_window_keeps_enough_room_for_the_session_table(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    gui.Window(tk_root, path)

    minimum_width, minimum_height = tk_root.minsize()
    assert minimum_width >= 1100
    assert minimum_height >= 860


def test_account_and_session_id_columns_resist_clipping(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    assert int(window.tree.column('owner', 'width')) >= 280
    assert int(window.tree.column('id', 'width')) >= 440


def test_long_ids_are_shortened_for_tables():
    gui = importlib.import_module('workbuddy_sync.gui')
    assert gui.short_id('dcb78cfa-f171-49c9-b936-c8b4b874b416') == 'dcb78cfa…b416'
    assert gui.short_id('short') == 'short'


def test_session_titles_with_line_breaks_stay_inside_one_table_row(sandbox, tk_root):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute(
            "UPDATE sessions SET title=? WHERE id='s1'",
            ('https://status.ciii.club/status/codex\n```\n回复微信公众号文章',),
        )
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)

    window = gui.Window(tk_root, path)

    expected = 'https://status.ciii.club/status/codex ``` 回复微信公众号文章'
    assert window.tree.set('s1', 'title') == expected
    assert window.history_tree.set('s1', 'title') == expected


def test_gui_refresh_keeps_selection_and_reattaches_session(
    sandbox, monkeypatch, tk_root,
):
    gui = importlib.import_module('workbuddy_sync.gui')
    opened = []
    monkeypatch.setattr(gui.os, 'startfile', opened.append)
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)
    window.all_sessions.set(False)
    window.toggle_scope()
    window.toggle_session('s1')
    window.save()
    window.tree.selection_set(['s2'])
    window.refresh()
    assert window.tree.selection() == ('s2',)
    window.open_selected()
    assert opened == ['workbuddy://chat/s2']
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET deleted_at=1 WHERE id='s1'")
    window.refresh()
    assert 's1' not in window.tree.get_children()
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET deleted_at=NULL WHERE id='s1'")
    window.refresh()
    assert 's1' in window.tree.get_children()


def test_gui_refresh_hides_archived_session_and_restores_unarchived_session(
    sandbox, tk_root,
):
    gui = importlib.import_module('workbuddy_sync.gui')
    home, auth = setup_data(sandbox)
    path = sandbox / 'settings.json'
    settings(sandbox, home, auth).save(path)
    window = gui.Window(tk_root, path)

    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET status='archived' WHERE id='s1'")
    window.refresh()
    assert 's1' not in window.tree.get_children()

    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("UPDATE sessions SET status='completed' WHERE id='s1'")
    window.refresh()
    assert 's1' in window.tree.get_children()


def test_account_changes_during_backup_rolls_back_for_next_tick(sandbox):
    home, auth = setup_data(sandbox)
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: True)
    make_backup = engine.make_backup

    def switch_account(*args):
        backup = make_backup(*args)
        auth.write_text('{"account":{"uid":"B"}}')
        return backup

    engine.make_backup = switch_account
    assert engine.sync().status == 'switching'
    assert owners(home)['s2'] == 'B'


def test_sql_failure_rolls_back_all_rows(sandbox):
    home, auth = setup_data(sandbox)
    auth.write_text('{"account":{"uid":"N"}}')
    with sqlite3.connect(home / 'workbuddy.db') as db:
        db.execute("CREATE TRIGGER reject_s2 BEFORE UPDATE ON sessions WHEN OLD.id='s2' "
                   "BEGIN SELECT RAISE(ABORT,'test failure'); END")
    engine = api().Engine(settings(sandbox, home, auth), running=lambda: False)
    import pytest
    with pytest.raises(sqlite3.IntegrityError):
        engine.sync()
    assert owners(home)['s1'] == 'A'
    assert owners(home)['s2'] == 'B'
