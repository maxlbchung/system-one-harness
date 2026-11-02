"""Run test_harness.py on Linux, with the Windows-only modules stubbed: python sim/run_tests.py"""
import os
import runpy
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [here, os.path.dirname(here)]
import winstub  # noqa: E402,F401

runpy.run_path(os.path.join(os.path.dirname(here), "test_harness.py"), run_name="__main__")
