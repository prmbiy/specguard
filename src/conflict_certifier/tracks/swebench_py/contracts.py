"""Mechanical contract checks executed inside each task image."""

from __future__ import annotations

from conflict_certifier.tracks.swebench_py.pyrunner import ContainerResult, TaskPythonRunner


_LOADER = '''\
import importlib.util
import sys

sys.path.insert(0, "/testbed")

def cc_load(name, filename):
    spec = importlib.util.spec_from_file_location(name, "/tmp/conflict_certifier/" + filename)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load " + filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''


def spec_contract(runner: TaskPythonRunner, code: str) -> ContainerResult:
    contract = _LOADER + '''\
spec = cc_load("cc_agent_spec", "spec.py")
if not callable(getattr(spec, "run", None)):
    raise TypeError("spec.py must define callable run(spec_input)")
'''
    return runner.run_files({"spec.py": code, "contract.py": contract},
                            entrypoint="contract.py")


def tests_contract(runner: TaskPythonRunner, code: str) -> ContainerResult:
    contract = _LOADER + '''\
tests = cc_load("cc_agent_tests", "tests.py")
if not isinstance(getattr(tests, "TESTS", None), list):
    raise TypeError("tests.py must define TESTS")
'''
    return runner.run_files({"tests.py": code, "contract.py": contract},
                            entrypoint="contract.py")


def connector_contract(
    runner: TaskPythonRunner,
    spec_code: str,
    tests_code: str,
    connector_code: str,
    *,
    inputs: tuple[str, ...] | list[str],
    context: str,
) -> ContainerResult:
    contract = _LOADER + f'''\
spec = cc_load("cc_agent_spec", "spec.py")
tests = cc_load("cc_agent_tests", "tests.py")
connector = cc_load("cc_agent_connector", "connector.py")
if not callable(getattr(connector, "check", None)):
    raise TypeError("connector.py must define callable check")
frozen_inputs = {tuple(inputs)!r}
input_context = {context!r}
for pair in tests.TESTS:
    for test_name in ("test_0", "test_1"):
        result = connector.check(
            pair["input_number"], pair[test_name], spec.run,
            frozen_inputs, input_context)
        if result is not None and type(result) is not bool:
            raise TypeError("Connector.check must return True, False, or None")
'''
    return runner.run_files(
        {
            "spec.py": spec_code,
            "tests.py": tests_code,
            "connector.py": connector_code,
            "contract.py": contract,
        },
        entrypoint="contract.py",
    )


def loader_source() -> str:
    return _LOADER
