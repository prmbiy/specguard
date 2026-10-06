"""Validated execution controls shared by the CLI and benchmark runner."""
from .artifacts import GuardError

DEFAULTS = {
    "spec_turns": 120, "spec_submissions": 10, "exec_timeout": 120,
    "test_submissions": 10, "connector_submissions": 6,
    "discovery_turns": 40, "source_selection_turns": 40,
    "workers": 3, "repl_processes": 2, "lean_timeout": 120,
}


def execution_settings(config):
    result = dict(config)
    for name, default in DEFAULTS.items():
        value = result.setdefault(name, default)
        if type(value) is not int or value < 1:
            raise GuardError(f"{name} must be a positive integer")
    return result
