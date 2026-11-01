"""Resolve the project-owned Qt installation before importing PySide6."""
import sys
import os
from pathlib import Path

_dll_directories=[]


def enable():
    runtime=Path(__file__).resolve().parents[1]/'artifacts/runtime/pyside6-6.8.3'
    if runtime.is_dir()and str(runtime)not in sys.path:sys.path.insert(0,str(runtime))
    package=runtime/'PySide6'
    if package.is_dir():
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH']=str(package/'plugins/platforms')
        if os.name=='nt'and not _dll_directories:_dll_directories.append(os.add_dll_directory(str(package)))
