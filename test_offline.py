"""Run test_harness.py where UIA doesn't exist (Linux, macOS, CI): python test_offline.py

Stands in for the two Windows-only imports, uiautomation and _ctypes.COMError, with just
the names the harness reads. It checks the walk's mechanics only. Nothing here calls a
model or a real desktop, so it says nothing about how well the agent chooses.
"""
import _ctypes
import runpy
import sys
import types


class COMError(Exception):
    pass


uia = types.ModuleType("uiautomation")
uia.PatternId = types.SimpleNamespace(InvokePattern=1, TogglePattern=2, SelectionItemPattern=3, ExpandCollapsePattern=4, ValuePattern=5)
uia.ExpandCollapseState = types.SimpleNamespace(Collapsed=0, Expanded=1)
uia.SW = types.SimpleNamespace(ShowNoActivate=4, ShowMinNoActive=7)
uia.IsIconic = lambda handle: False
uia.ShowWindow = lambda handle, command: None

if not hasattr(_ctypes, "COMError"):
    _ctypes.COMError = COMError
sys.modules.setdefault("uiautomation", uia)
runpy.run_path("test_harness.py", run_name="__main__")
