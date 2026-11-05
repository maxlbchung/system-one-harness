"""Simulated UIA desktops: Character Map, Snipping Tool and Calculator among everyday windows.

Controls answer the same calls harness.py makes on real uiautomation controls, and acting on
them changes state the way the real apps do (a pick closes its dropdown, Close removes the
window, Calculator computes). Trees follow what Inspect shows for these apps on Windows 11,
trimmed: names, control types, AutomationIds, title bars and the taskbar's running-app buttons.
"""
import itertools
import re

import winstub  # noqa: F401  Must come before uiautomation.
import uiautomation as uia

_ids = itertools.count(1)


class Pattern:
    def __init__(self, **members):
        self.__dict__.update(members)


class Control:
    def __init__(self, name="", kind="Pane", *kids, id="", offscreen=False, enabled=True, cls="",
                 invoke=None, toggle=None, select=None, value=None, items=None):
        self.Name, self.ControlTypeName, self.AutomationId, self.ClassName = name, kind + "Control", id, cls
        self.kids, self.IsOffscreen, self.IsEnabled = list(kids), offscreen, enabled
        self.ProcessId, self.NativeWindowHandle, self.parent = 0, 0, None
        self.runtime = next(_ids)
        self.patterns = {}
        if invoke:
            self.patterns[uia.PatternId.InvokePattern] = Pattern(Invoke=lambda: invoke(self))
        if toggle:
            state = Pattern(ToggleState=0)
            state.Toggle = lambda: (setattr(state, "ToggleState", 1 - state.ToggleState), toggle(self))
            self.patterns[uia.PatternId.TogglePattern] = state
        if select:
            self.patterns[uia.PatternId.SelectionItemPattern] = Pattern(Select=lambda: select(self))
        if value is not None:
            box = Pattern(IsReadOnly=False, Value=value)
            box.SetValue = lambda text: setattr(box, "Value", text)
            self.patterns[uia.PatternId.ValuePattern] = box
        if items is not None:  # A dropdown: its list exists only while it's open.
            self.items = items
            box = Pattern(ExpandCollapseState=uia.ExpandCollapseState.Collapsed)

            def expand():
                box.ExpandCollapseState, self.kids = uia.ExpandCollapseState.Expanded, [items]
                adopt(self, items)

            def collapse():
                box.ExpandCollapseState, self.kids = uia.ExpandCollapseState.Collapsed, []

            box.Expand, box.Collapse = expand, collapse
            self.patterns[uia.PatternId.ExpandCollapsePattern] = box
        for kid in self.kids:
            kid.parent = self

    def GetChildren(self):
        return list(self.kids)

    def GetFirstChildControl(self):
        return self.kids[0] if self.kids else None

    def GetPattern(self, pattern):
        return self.patterns.get(pattern)

    def GetRuntimeId(self):
        return (42, self.runtime)

    def descendants(self):
        for kid in self.kids:
            yield kid
            yield from kid.descendants()

    def TextControl(self, AutomationId):
        found = next((d for d in self.descendants() if d.AutomationId == AutomationId), None)
        return found or Pattern(Exists=lambda *_: False, Name="")

    def Exists(self, *_):
        return True

    def window(self):
        node = self
        while node.parent and node.parent.parent:
            node = node.parent
        return node


def adopt(parent, kid):
    kid.parent = parent
    for d in [kid, *kid.descendants()]:
        d.ProcessId = d.ProcessId or parent.ProcessId
    for k in kid.kids:
        adopt(kid, k)


def own(tree, pid, handle=0):
    """Give a subtree its process, keeping any process already set on a hosted part."""
    def visit(node, pid):
        node.ProcessId = node.ProcessId or pid
        for kid in node.kids:
            kid.parent = node
            visit(kid, node.ProcessId)
        if hasattr(node, "items"):
            visit(node.items, node.ProcessId)
    visit(tree, pid)
    tree.NativeWindowHandle = handle
    return tree


def noop(control):
    pass


def close(control):
    window = control.window()
    if window.parent:
        window.parent.kids.remove(window)


def minimize(control):
    uia.ShowWindow(control.window().NativeWindowHandle, uia.SW.ShowMinNoActive)


def title_bar(title):
    return Control(title, "TitleBar",
                   Control("Minimize", "Button", invoke=minimize),
                   Control("Maximize", "Button", invoke=noop),
                   Control("Close", "Button", invoke=close))


def pick(control):
    """A list item: the dropdown it sits in takes its name, then closes."""
    combo = control.parent.parent
    if combo and uia.PatternId.ExpandCollapsePattern in combo.patterns:
        combo.selected = control.Name
        if combo.ControlTypeName == "ComboBoxControl" and getattr(combo, "named_by_value", False):
            combo.Name = control.Name
        if value := combo.patterns.get(uia.PatternId.ValuePattern):
            value.Value = control.Name
        combo.patterns[uia.PatternId.ExpandCollapsePattern].Collapse()


def dropdown(name, entries, id="", visible=None, named_by_value=False, value=None):
    """A dropdown. WinUI ones take their selection as their name; Win32 ones keep a label and hold it as their value."""
    visible = len(entries) if visible is None else visible
    listed = Control(name, "List", *[Control(e, "ListItem", select=pick, offscreen=i >= visible) for i, e in enumerate(entries)])
    combo = Control(name, "ComboBox", id=id, items=listed, value=value)
    combo.named_by_value, combo.selected = named_by_value, name
    return combo


FONTS = ["Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Cambria Math", "Candara", "Cascadia Code",
         "Comic Sans MS", "Consolas", "Constantia", "Corbel", "Courier New", "Ebrima", "Franklin Gothic Medium",
         "Gabriola", "Gadugi", "Georgia", "HoloLens MDL2 Assets", "Impact", "Ink Free", "Javanese Text",
         "Leelawadee UI", "Lucida Console", "Lucida Sans Unicode", "Malgun Gothic", "Marlett", "Microsoft Himalaya",
         "Microsoft JhengHei", "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Sans Serif",
         "Microsoft Tai Le", "Microsoft YaHei", "Microsoft Yi Baiti", "MingLiU-ExtB", "Mongolian Baiti", "MS Gothic",
         "MV Boli", "Myanmar Text", "Nirmala UI", "Palatino Linotype", "Segoe Fluent Icons", "Segoe MDL2 Assets",
         "Segoe Print", "Segoe Script", "Segoe UI", "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol",
         "SimSun", "Sitka Text", "Sylfaen", "Symbol", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana",
         "Webdings", "Wingdings", "Yu Gothic"]


def character_map():
    chars = Control("Characters to copy :", "Edit", id="104", value="")

    def select(control):
        chars.patterns[uia.PatternId.ValuePattern].Value += "!"

    return Control("Character Map", "Window",
                   title_bar("Character Map"),
                   Control("Font :", "Text"),
                   dropdown("Font :", FONTS, id="105", visible=16, value="Arial"),
                   Control("Help", "Button", id="107", invoke=noop),
                   Control("", "Pane", id="100"),  # The character grid, drawn by hand, lists nothing to UIA.
                   Control("Characters to copy :", "Text"),
                   chars,
                   Control("Select", "Button", id="102", invoke=select),
                   Control("Copy", "Button", id="103", enabled=True, invoke=noop),
                   Control("Advanced view", "CheckBox", id="109", toggle=noop),
                   Control("U+0021: Exclamation Mark", "Text"),
                   cls="#32770")


def snipping_tool():
    def button(name, id, **kw):
        return Control(name, "Button", Control(name, "Text"), id=id, **kw)

    return Control("Snipping Tool", "Window",
                   title_bar("Snipping Tool"),
                   Control("", "Pane",
                           button("New", "NewSnipButton", invoke=noop),
                           Control("Capture mode", "Button", Control("Snip", "Text"), id="CaptureModeToggleButton", toggle=noop),
                           dropdown("Rectangle", ["Rectangle", "Window", "Full screen", "Freeform"], id="SnippingAreaComboBox", named_by_value=True),
                           dropdown("No delay", ["No delay", "Delay snip for 3 seconds", "Delay snip for 5 seconds", "Delay snip for 10 seconds"],
                                    id="SnipDelayComboBox", named_by_value=True),
                           Control("See more", "Button", id="SeeMoreButton",
                                   items=Control("See more", "Menu",
                                                 Control("Settings", "MenuItem", invoke=noop),
                                                 Control("Send feedback", "MenuItem", invoke=noop))),
                           Control("Press Windows logo key + Shift + S to start a snip.", "Text")),
                   cls="WinUIDesktopWin32WindowClass")


class Calculator:
    NAMES = {"Zero": "0", "One": "1", "Two": "2", "Three": "3", "Four": "4", "Five": "5", "Six": "6", "Seven": "7",
             "Eight": "8", "Nine": "9", "Decimal separator": ".", "Plus": "+", "Minus": "-", "Multiply by": "*", "Divide by": "/"}

    def __init__(self):
        self.expression = ""
        self.display = Control("Display is 0", "Text", id="CalculatorResults")
        self.history = Control("", "Text", id="CalculatorExpression")  # Like Windows: "Expression is 1 +".

    def show(self, value):
        self.display.Name = f"Display is {value}"

    def written(self, expression):
        self.history.Name = f"Expression is {' '.join(re.findall(r'[0-9.]+|[-+*/=]', expression))}" if expression else ""

    def press(self, control):
        name = control.Name
        if name in self.NAMES:
            self.expression += self.NAMES[name]
            numbers = [n for n in re.split(r"[-+*/]", self.expression) if n]
            self.show(numbers[-1] if numbers else 0)  # After an operator the display keeps the number.
            operator = max(self.expression.rfind(o) for o in "+-*/")
            self.written(self.expression[:operator + 1] if operator >= 0 else "")
        elif name == "Equals":
            self.written(self.expression + "=")
            try:
                result = eval(self.expression or "0", {"__builtins__": {}})  # Only digits and operators reach here.
                result = int(result) if float(result).is_integer() else round(result, 6)
            except (SyntaxError, ZeroDivisionError):
                result = "Cannot divide by zero"
            self.expression = str(result)
            self.show(result)
        elif name in ("Clear", "Clear entry"):
            self.expression = ""
            self.written("")
            self.show(0)

    def tree(self):
        def keys(group, names, prefix):
            return Control(group, "Group", *[Control(n, "Button", id=f"{prefix}{i}Button", invoke=self.press) for i, n in names])

        digits = [(i, n) for n, i in zip(["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"], range(10))]
        content = Control("Calculator", "Window",
                          Control("", "Custom",
                                  Control("Open Navigation", "Button", id="TogglePaneButton", invoke=noop),
                                  Control("Standard Calculator mode", "Text", id="Header"),
                                  Control("Keep on top", "Button", id="NormalAlwaysOnTopButton", invoke=noop),
                                  self.history,
                                  self.display,
                                  Control("Memory controls", "Group",
                                          *[Control(n, "Button", id=f"Memory{n.split()[-1]}", invoke=noop) for n in
                                            ["Memory Clear", "Memory Recall", "Memory Add", "Memory Subtract", "Memory Store"]]),
                                  Control("Display controls", "Group",
                                          *[Control(n, "Button", id=f"{n.replace(' ', '')}Button", invoke=self.press) for n in ["Clear entry", "Clear", "Backspace"]]),
                                  Control("Standard functions", "Group",
                                          *[Control(n, "Button", id=f"{n.replace(' ', '')}Button", invoke=noop) for n in ["Percent", "Reciprocal", "Square", "Square root"]]),
                                  Control("Standard operators", "Group",
                                          *[Control(n, "Button", id=f"{n.split()[0].lower()}Button", invoke=self.press) for n in
                                            ["Divide by", "Multiply by", "Minus", "Plus", "Equals"]]),
                                  keys("Number pad", digits, "num"),
                                  ), cls="Windows.UI.Core.CoreWindow")
        own(content, 303)
        return Control("Calculator", "Window", title_bar("Calculator"), content, cls="ApplicationFrameWindow")


def file_explorer():
    return Control("Documents - File Explorer", "Window",
                   title_bar("Documents - File Explorer"),
                   Control("", "Pane",
                           Control("Navigation Pane", "Tree", *[Control(n, "TreeItem", select=noop) for n in
                                                                 ["Home", "Gallery", "Desktop", "Downloads", "Documents", "Pictures", "Music", "Videos"]]),
                           Control("Items View", "List", *[Control(n, "ListItem", select=noop) for n in
                                                           ["Budget 2026.xlsx", "Meeting notes.docx", "Screenshots", "Tax return.pdf", "Trip photos"]]),
                           Control("Address: Documents", "ToolBar", Control("Back", "Button", invoke=noop), Control("Refresh", "Button", invoke=noop))),
                   cls="CabinetWClass")


def browser():
    return Control("Weather forecast - Microsoft Edge", "Window",
                   title_bar("Weather forecast - Microsoft Edge"),
                   Control("", "Pane",
                           Control("Tab bar", "Tab", Control("Weather forecast", "TabItem", select=noop), Control("Inbox - Outlook", "TabItem", select=noop),
                                   Control("New Tab", "Button", invoke=noop)),
                           Control("Address and search bar", "Edit", value="https://weather.example"),
                           Control("Weather forecast", "Document", Control("Today: sunny, 18°C", "Text"), Control("10-day forecast", "Hyperlink", invoke=noop))),
                   cls="Chrome_WidgetWin_1")


def taskbar(running):
    return Control("Taskbar", "Pane",
                   Control("Start", "Button", invoke=noop),
                   Control("Search", "Button", invoke=noop),
                   Control("Task View", "Button", invoke=noop),
                   Control("", "ToolBar", *[Control(n, "Button", invoke=noop) for n in
                                           ["File Explorer - 1 running window", "Microsoft Edge - 1 running window", *[f"{r} - 1 running window" for r in running]]]),
                   Control("Clock 10:42 AM", "Button", invoke=noop),
                   cls="Shell_TrayWnd")


APPS = {"Character Map": (character_map, 101, 1001), "Snipping Tool": (snipping_tool, 202, 2002), "Calculator": (None, 300, 3003)}


def desktop(app_title):
    """A fresh desktop with the test app open on top. Returns (root, the processes the agent may act in, calculator or None)."""
    calc = None
    if app_title == "Calculator":
        calc = Calculator()
        window = calc.tree()
    else:
        window = APPS[app_title][0]()
    _, pid, handle = APPS[app_title]
    own(window, pid, handle)
    windows = [window, own(browser(), 401, 4001), own(file_explorer(), 402, 4002)]
    root = Control("Desktop 1", "Pane", *windows, own(taskbar([app_title]), 400, 4000), own(Control("Program Manager", "Pane", Control("Desktop", "List", *[
        Control(n, "ListItem", select=noop) for n in ["Recycle Bin", "Microsoft Edge", "Project files"]])), 403, 4003))
    for w in root.kids:
        w.parent = root
    uia.MINIMIZED.clear()
    framed = window.ClassName == "ApplicationFrameWindow"
    mine = {k.ProcessId for k in window.kids} - {window.ProcessId} if framed else {window.ProcessId}
    return root, mine, calc
