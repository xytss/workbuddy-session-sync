import argparse
import json
import sqlite3
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .core import Engine, Settings, SyncError


def emit(result):
    print(json.dumps(asdict(result), default=str, ensure_ascii=False, indent=2), flush=True)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='WorkBuddy 本地会话跨账号在线接管')
    parser.add_argument('--config', type=Path, default=Settings().state_dir / 'settings.json')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('gui', help='打开中文设置窗口（默认）')
    commands.add_parser('diagnose', help='只读诊断并预览同步范围')
    commands.add_parser('sync', help='将所选会话接管给 WorkBuddy 当前账号')
    commands.add_parser('watch', help='持续按保存的自动同步设置运行；Ctrl+C 停止')
    restore = commands.add_parser('restore', help='恢复指定备份中的会话归属，并关闭自动同步')
    restore.add_argument('backup', type=Path)
    config = commands.add_parser('configure', help='保存设置')
    config.add_argument('--data-dir', type=Path)
    config.add_argument('--auth-file', type=Path)
    config.add_argument('--auto-sync', choices=['on', 'off'])
    config.add_argument('--scope', choices=['all', 'selected'])
    config.add_argument('--session-id', action='append')
    config.add_argument('--poll-seconds', type=int, choices=range(1, 61), metavar='1-60')
    args = parser.parse_args(argv)
    try:
        cfg = Settings.load(args.config)
        if args.command in (None, 'gui'):
            from .gui import launch
            launch(args.config)
            return 0
        if args.command == 'configure':
            for key in ('data_dir', 'auth_file', 'poll_seconds'):
                value = getattr(args, key)
                if value is not None:
                    setattr(cfg, key, value)
            if args.auto_sync:
                cfg.auto_sync = args.auto_sync == 'on'
            if args.scope == 'all':
                cfg.session_ids = None
            elif args.scope == 'selected':
                cfg.session_ids = args.session_id or []
            elif args.session_id:
                parser.error('--session-id 需要同时指定 --scope selected')
            cfg.save(args.config)
            print('设置已保存：' + str(args.config))
            return 0
        engine = Engine(cfg)
        if args.command == 'diagnose':
            emit(engine.preview())
            return 0
        if args.command == 'watch':
            previous = None
            while True:
                engine.settings = Settings.load(args.config)
                result = engine.tick()
                state = (result.status, result.target, result.changed)
                if state != previous:
                    emit(result)
                    previous = state
                time.sleep(engine.settings.poll_seconds)
        elif args.command == 'restore':
            cfg.auto_sync = False
            cfg.save(args.config)
            result = engine.restore(args.backup)
        else:
            result = engine.sync()
        emit(result)
        return 2 if result.status in ('waiting', 'switching', 'busy', 'logged_out') else 0
    except KeyboardInterrupt:
        return 0
    except (SyncError, OSError, sqlite3.Error, ValueError) as exc:
        print('操作已停止：' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
