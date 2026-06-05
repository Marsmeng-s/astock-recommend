"""打包/运行时路径（支持 exe / 便携包 / 源码）"""
from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_dir() -> str:
    """数据目录：exe 同目录，或便携包根目录"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def resource_dir() -> str:
    """代码与 templates 所在目录"""
    if is_frozen():
        # PyInstaller
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
        # cx_Freeze：templates 与 exe 同级
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.isdir(os.path.join(exe_dir, "templates")):
            return exe_dir
        lib_dir = os.path.join(exe_dir, "lib")
        if os.path.isdir(os.path.join(lib_dir, "templates")):
            return lib_dir
        return exe_dir
    return os.path.dirname(os.path.abspath(__file__))
