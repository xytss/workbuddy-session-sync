from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture
def sandbox():
    # 保留测试数据；不使用会自动清理文件的临时目录 fixture。
    root = Path(__file__).resolve().parents[1] / 'work' / 'test-runs' / uuid4().hex
    root.mkdir(parents=True)
    return root


@pytest.fixture(scope='session')
def tk_master():
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def tk_root(tk_master):
    import tkinter as tk

    root = tk.Toplevel(tk_master)
    root.withdraw()
    yield root
    root.destroy()
