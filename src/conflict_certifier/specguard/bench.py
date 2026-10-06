"""Run configured Harbor task images without modifying experiment pipelines.

Trusted launcher only. Credentials travel through stdin to the CLI, never Docker
arguments, Docker configuration, the repository, or agent bash environments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import signal
import subprocess
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from datetime import datetime, timezone

from dotenv import dotenv_values
from tqdm import tqdm
import yaml
from .settings import execution_settings
from .repl_service import ReplService


ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = ROOT / "data/specguard/harbor"


def lean_runtime_mount() -> tuple[str, str | None]:
    """Mount the configured host Lean tree at the same path inside a task image."""
    from .runtime import ImageLeanEnv, lean_environment
    env = lean_environment()
    if isinstance(env, ImageLeanEnv):
        return str(env.container_mount_root), None
    root = env.container_mount_root
    return str(root), f"{root}:{root}:ro"


def artifact_directories(stdout: str, task_id: str) -> list[str]:
    """Accept only complete task artifact paths, never an empty/container-root path."""
    base = PurePosixPath("/testbed/output/specguard")
    paths = []
    for raw in stdout.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        path = PurePosixPath(raw)
        if not path.is_absolute() or path.name != task_id or len(path.parents) < 3:
            continue
        if path.parents[2] != base:
            continue
        paths.append(str(path))
    return paths


def selected_tasks(config, override=None):
    names = override if override is not None else config.get("tasks")
    if not isinstance(names, list) or not names or any(
        not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", n) for n in names
    ):
        raise ValueError("Provide a nonempty tasks list in the config or --task TASK_ID")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate task IDs")
    tasks = []
    for name in names:
        task = TASK_ROOT / name
        if task.is_symlink() or not task.is_dir():
            raise ValueError(f"Unknown task: {name}")
        for path in ("instruction.md", "environment/Dockerfile", "tests/test.sh", "provenance.json"):
            if not (task / path).is_file():
                raise ValueError(f"Missing task file: {task / path}")
        provenance = json.loads((task / "provenance.json").read_text())
        image = provenance.get("image_tag", f"specguard-{name}:demo")
        tasks.append((task, image))
    return tasks


def run(*args, **kwargs):
    return subprocess.run(list(args), check=True, **kwargs)


def main():
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(prog="specguard-bench", description=__doc__)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--reproduce-only", action="store_true")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/specguard.yaml")
    parser.add_argument("--task", action="append", help="Task ID; repeat to override the config tasks list")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config_path = args.config.resolve()
    config = yaml.safe_load(config_path.read_text())
    if not isinstance(config, dict):
        parser.error("Config must be a YAML mapping")
    config = execution_settings(config)
    tasks = selected_tasks(config, args.task)
    # Validate all images before launching any paid work.
    for task, image in tasks:
        if args.build:
            run("docker", "build", "-t", image, str(task / "environment"))
        run("docker", "image", "inspect", image, stdout=subprocess.DEVNULL)
    model_dir = re.sub(r"[^A-Za-z0-9_.-]", "_", config["model"])
    print(f"[run] {len(tasks)} tasks, model={config['model']} -> {ROOT / 'output/specguard' / model_dir / stamp}")
    counts = {"passed": 0, "failed": 0, "errors": 0} if args.reproduce_only else {
        "conflict": 0, "no-conflict": 0, "inconclusive": 0}
    results = []
    control = TaskControl()
    runtime = nullcontext(None) if args.reproduce_only else ReplService(config["repl_processes"], config["lean_timeout"])
    with runtime as service, ThreadPoolExecutor(max_workers=config["workers"]) as executor:
        futures = {}
        try:
            futures = {executor.submit(run_one, task, image, config, config_path, stamp,
                                       args.reproduce_only, service=service, control=control): task
                       for task, image in tasks}
            with tqdm(total=len(tasks), desc="tasks", unit="task", dynamic_ncols=True) as bar:
                bar.set_postfix(counts)
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = {"verdict": "inconclusive", "reason": "Launcher failure: " + type(exc).__name__}
                    if args.reproduce_only:
                        code = result.get("exit_code")
                        label = "passed" if code == 0 else "failed" if code == 1 else "errors"
                    else:
                        label = result.get("verdict", "inconclusive")
                        if label not in counts:
                            label = "inconclusive"
                    counts[label] += 1
                    results.append((task.name, label, result.get("reason", "")))
                    bar.set_postfix(counts, refresh=False)
                    bar.update()
        except BaseException:
            for future in futures:
                future.cancel()
            control.stop()
            raise
    print("\n==================== RESULTS ====================")
    for name, label, reason in results:
        print(f"  {name:<36} {label.upper():<14} {reason}")
    print("-------------------------------------------------")
    for label, count in counts.items():
        print(f"  {label.upper():<14} = {count}/{len(tasks)}")


class TaskControl:
    """Cancellation limited to this launcher's task containers."""
    def __init__(self):
        self.lock = threading.Lock()
        self.stopped = False
        self.names = set()

    def start(self, name, launch):
        with self.lock:
            if self.stopped:
                raise RuntimeError("Benchmark interrupted")
            # Register before launch, including partially successful Docker starts.
            self.names.add(name)
            launch()

    def stop(self):
        with self.lock:
            self.stopped = True
            names = list(self.names)
        def interrupt(name):
            try:
                subprocess.run(["docker", "exec", name, "python", "-c",
                    "import os,signal; from pathlib import Path; Path('/tmp/specguard-cancelled').touch(); "
                    "p=Path('/opt/specguard/run.pid'); "
                    "os.kill(int(p.read_text()),signal.SIGTERM) if p.exists() else None"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
        with ThreadPoolExecutor(max_workers=max(1, len(names))) as ex:
            list(ex.map(interrupt, names))


def run_one(task, image_tag, config, config_path, stamp, reproduce_only=False, *, service=None, control=None):
    control = control or TaskControl()
    model = config["model"]
    model_dir = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    output = ROOT / "output/specguard" / model_dir / stamp / task.name
    output.mkdir(parents=True, exist_ok=False)
    for directory in ("config", "intermediates", "trajs"):
        (output / directory).mkdir()
    snapshot = output / "config/launcher.yaml"
    snapshot.write_text(yaml.safe_dump(config))
    name = "specguard-demo-" + uuid.uuid4().hex[:10]
    lean_root, lean_mount = lean_runtime_mount()
    image = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", image_tag], text=True).strip()
    (output / "config/demo.json").write_text(json.dumps({"image": image, "task": str(task),
        "instruction": (task / "instruction.md").read_text(),
        "cli_source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (ROOT / "src/conflict_certifier/specguard").rglob("*")
                              if p.is_file() and p.suffix in {".py", ".txt"}}}, indent=2))
    created = False
    console = (output / "trajs/launcher.log").open("w")
    def run(*args, **kwargs):
        kwargs.setdefault("stdout", console)
        kwargs.setdefault("stderr", console)
        return subprocess.run(list(args), check=True, **kwargs)
    try:
        # The socket is for the trusted CLI's sibling Lean REPL, not agent bash.
        # Bubblewrap constructs a separate root without socket, keys or /opt.
        created = True
        control.start(name, lambda: run("docker", "run", "-d", "--name", name, "--label", "specguard.demo=true",
            "--cap-add", "SYS_ADMIN", "--cap-add", "NET_ADMIN",
            "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
            "--security-opt", "systempaths=unconfined",
            "--memory", "8g", "--pids-limit", "512",
            "-e", "SPECGUARD_LEAN_MEMORY=8g",
            *(["-e", f"SPECGUARD_LEAN_ROOT={lean_root}"] if lean_mount else []),
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            *(["-v", lean_mount] if lean_mount else []),
            *(["-e", f"SPECGUARD_LEAN_IMAGE={service.pool.env.container_image}"]
              if service and lean_mount is None else []),
            *(["-v", service.mount] if service else []), image_tag, timeout=60))
        run("docker", "exec", name, "mkdir", "-p", "/opt/specguard/src")
        run("docker", "cp", str(ROOT / "src/conflict_certifier"), name + ":/opt/specguard/src/")
        run("docker", "cp", str(snapshot), name + ":/opt/specguard/config.yaml")
        with (output / "config/requirements.freeze.txt").open("w") as log:
            run("docker", "exec", name, "python", "-m", "pip", "freeze", stdout=log)
        # Reproduction is optional and separate from classification: buggy source
        # may satisfy a faulty test, so neither a pass nor failure gates SpecGuard.
        if reproduce_only:
            run("docker", "cp", str(task / "tests/test.sh"), name + ":/opt/specguard/test.sh")
            with (output / "trajs/reproduction.log").open("w") as log:
                p = subprocess.run(["docker", "exec", name, "bash", "/opt/specguard/test.sh"],
                                   stdout=log, stderr=subprocess.STDOUT, timeout=120)
            (output / "intermediates/reproduction.json").write_text(json.dumps({"exit_code": p.returncode}))
            return {"exit_code": p.returncode}
        run("docker", "exec", name, "python", "-c",
            "from conflict_certifier.specguard.workspace import BashWorkspace; "
            "from conflict_certifier.specguard.checker import Checker; "
            "BashWorkspace.preflight(); Checker.preflight(); print('Isolation/runtime preflight passed')")
        probe = '''
import tempfile
from pathlib import Path
from conflict_certifier.specguard.workspace import BashWorkspace
with tempfile.TemporaryDirectory() as temp:
    box = BashWorkspace(Path(temp) / 'source')
    try:
        box.prepare()
        result = box.run("python - <<'PY'\\nimport os\\nfrom pathlib import Path\\nassert not any(k.endswith('API_KEY') for k in os.environ)\\nfor p in ['/testbed', '/opt/specguard', '/var/run/docker.sock', '/mnt/data/workspace']:\\n assert not Path(p).exists(), p\\nroutes=Path('/proc/net/route').read_text().splitlines()\\nassert len(routes)==1, routes\\nstatus=Path('/proc/self/status').read_text()\\nassert 'CapEff:\\t0000000000000000' in status\\nprint('PASS: no keys, original checkout, tool source, Docker socket, host workspace, external IPv4 routes or effective capabilities')\\nPY", timeout=120)
        print(result)
        assert result.startswith('exit=0\\n'), result
    finally:
        box.close()
'''
        with (output / "trajs/isolation.log").open("w") as log:
            run("docker", "exec", name, "python", "-c", probe, stdout=log, stderr=subprocess.STDOUT)
        env = {**dotenv_values(ROOT / ".env"), **os.environ}
        credential_names = [config["api_key_env"]]
        if config.get("api_base_env"):
            credential_names.append(config["api_base_env"])
        credentials = {k: env[k] for k in credential_names if env.get(k)}
        if len(credentials) != len(set(credential_names)):
            raise RuntimeError("Missing configured credential or base URL")
        with control.lock:
            if control.stopped:
                raise RuntimeError("Benchmark interrupted")
        if service:
            credentials.update(service.client_env)
        bootstrap = (
            "import json,os,sys; p=json.load(sys.stdin); os.environ.update(p['env']); "
            "from pathlib import Path; Path('/opt/specguard/run.pid').write_text(str(os.getpid())); "
            "sys.exit(130) if Path('/tmp/specguard-cancelled').exists() else None; "
            "from conflict_certifier.specguard.cli import main; "
            "sys.exit(main([p['task'],'--config','/opt/specguard/config.yaml','--task-id',p['task_id']]))"
        )
        payload = json.dumps({"env": credentials, "task": (task / "instruction.md").read_text(), "task_id": task.name})
        # Keep Docker and per-stage chatter out of the progress bar.
        p = subprocess.run(["docker", "exec", "-i", name, "python", "-c", bootstrap],
                           input=payload, text=True, stdout=console, stderr=subprocess.STDOUT,
                           timeout=config.get("time_budget", 1800) + 180)
        print(f"CLI exit: {p.returncode}", file=console, flush=True)
    except (Exception, KeyboardInterrupt) as exc:
        (output / "result.json").write_text(json.dumps({
            "verdict": "inconclusive", "in_progress": False,
            "reason": "Launcher interrupted" if isinstance(exc, KeyboardInterrupt) else "Launcher failure: " + type(exc).__name__,
        }))
        raise
    finally:
        if created:
            try:
                # Give the CLI a chance to close its sibling REPL if the launcher was interrupted.
                subprocess.run(["docker", "exec", name, "python", "-c",
                    "import os,signal,time; from pathlib import Path; p=Path('/opt/specguard/run.pid'); "
                    "pid=int(p.read_text()) if p.exists() else 0; "
                    "alive=pid and Path('/proc/'+str(pid)).exists(); "
                    "os.kill(pid,signal.SIGTERM) if alive else None; time.sleep(3) if alive else None"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                listing = subprocess.run(["docker", "exec", name, "python", "-c",
                    "from pathlib import Path; import sys; sys.stdout.write('\\n'.join(str(p.parent) for p in Path('/testbed/output/specguard').glob('*/*/" + task.name + "/result.json')))"],
                    capture_output=True, text=True)
                for artifact_dir in artifact_directories(listing.stdout, task.name):
                    subprocess.run(["docker", "cp", name + ":" + artifact_dir + "/.", str(output)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            finally:
                subprocess.run(["docker", "rm", "-f", name], stdout=console, stderr=subprocess.STDOUT)
                with control.lock:
                    control.names.discard(name)
        console.close()
    result_path = output / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    result = {"verdict": "inconclusive", "in_progress": False, "reason": "CLI produced no result"}
    result_path.write_text(json.dumps(result))
    return result


if __name__ == "__main__":
    main()
