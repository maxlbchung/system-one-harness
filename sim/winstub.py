"""Stand-ins for the Windows-only modules, so the harness imports unchanged on Linux.

Import this before harness. It adds _ctypes.COMError and a uiautomation module holding only
what harness.py, benchmark.py and the tests touch. Window state lives in MINIMIZED.
"""
import _ctypes
import enum
import sys
import types

if not hasattr(_ctypes, "COMError"):
    class COMError(Exception):
        pass
    _ctypes.COMError = COMError

uia = types.ModuleType("uiautomation")


class PatternId(enum.IntEnum):
    InvokePattern = 10000
    SelectionItemPattern = 10010
    ExpandCollapsePattern = 10005
    TogglePattern = 10015
    ValuePattern = 10002


class ExpandCollapseState(enum.IntEnum):
    Collapsed = 0
    Expanded = 1
    PartiallyExpanded = 2
    LeafNode = 3


class ToggleState(enum.IntEnum):
    Off = 0
    On = 1
    Indeterminate = 2


class SW:
    ShowNoActivate = 4
    ShowMinNoActive = 7


MINIMIZED = set()  # Window handles that are minimized.


def ShowWindow(handle, command):
    if command == SW.ShowMinNoActive:
        MINIMIZED.add(handle)
    elif command == SW.ShowNoActivate:
        MINIMIZED.discard(handle)
    return True


class UIAutomationInitializerInThread:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


uia.PatternId, uia.ExpandCollapseState, uia.ToggleState, uia.SW = PatternId, ExpandCollapseState, ToggleState, SW
uia.ControlsAreSame = lambda one, other: one is other
uia.ShowWindow, uia.IsIconic = ShowWindow, lambda handle: handle in MINIMIZED
uia.UIAutomationInitializerInThread = UIAutomationInitializerInThread
uia.MINIMIZED = MINIMIZED
uia.GetRootControl = lambda: None  # The simulator replaces this with its desktop.
sys.modules.setdefault("uiautomation", uia)
