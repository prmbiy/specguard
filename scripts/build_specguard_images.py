"""Build task images and validate their snapshots; never run tests or LLM agents."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT/'data/specguard/harbor'


def check_cli(task, logs):
    p = task/'provenance.json'
    provenance = json.loads(p.read_text())
    name = 'specguard-image-check-' + uuid.uuid4().hex[:12]
    result = {'task': task.name, 'status': 'cli_check_failed'}
    log = logs/(task.name+'.cli.log')
    try:
        with log.open('w') as stream:
            def command(*args):
                return subprocess.run(list(args), stdout=stream, stderr=subprocess.STDOUT,
                                      check=True, timeout=60)
            command('docker', 'run', '-d', '--name', name, '--network=none', '--cap-drop=ALL',
                    '--security-opt=no-new-privileges', '--memory=1g',
                    provenance['image_id'], 'sleep', '180')
            command('docker', 'exec', name, 'mkdir', '-p', '/opt/specguard/src')
            command('docker', 'cp', str(ROOT/'src/conflict_certifier'), name+':/opt/specguard/src/')
            command('docker', 'exec', name, 'specguard', '--help')
        provenance.update(cli_load_verified=True, cli_load_log=str(log.relative_to(ROOT)))
        result['status'] = 'cli_loaded'
    except (subprocess.SubprocessError, OSError, KeyError) as exc:
        result['error'] = type(exc).__name__
        provenance['cli_load_verified'] = False
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
    p.write_text(json.dumps(provenance, indent=2)+'\n')
    print(result['status'].upper(), task.name, flush=True)
    return result


def build(task, logs):
    p = task/'provenance.json'
    provenance = json.loads(p.read_text())
    tag = provenance['image_tag']
    log = logs/(task.name+'.log')
    start = time.monotonic()
    result = {'task': task.name, 'image_tag': tag, 'build_log': str(log.relative_to(ROOT))}
    try:
        with log.open('w') as stream:
            subprocess.run(['docker', 'build', '-t', tag, str(task/'environment')],
                           stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=1800)
        image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()
        probe = '''import hashlib,json,sys
from pathlib import Path
import yaml,tqdm,anthropic,openai,httpx,pytest
p=json.load(sys.stdin)
assert not Path('/testbed/.git').exists()
assert not Path('/var/run/docker.sock').exists()
assert not Path('/opt/specguard/src').exists(), 'Tool code must be supplied only by launcher'
source=Path('/testbed')/p['test_selector'].split('::')[0]
assert hashlib.sha256(source.read_bytes()).hexdigest()==p['test_source_sha256']
print('PASS: original test SHA256; no Git history, Docker socket or baked tool source; CLI dependencies import')
'''
        checked = subprocess.run(['docker', 'run', '--rm', '--network=none', '--cap-drop=ALL',
                '--security-opt=no-new-privileges', '--memory=1g', '-i', image, 'python', '-c', probe],
                input=json.dumps(provenance), text=True, capture_output=True, timeout=90)
        (logs/(task.name+'.validation.log')).write_text(checked.stdout+checked.stderr)
        checked.check_returncode()
        frozen = subprocess.check_output(['docker', 'run', '--rm', '--network=none', '--memory=1g', image,
                                        'python', '-m', 'pip', 'freeze'], text=True, timeout=60)
        freeze = logs/(task.name+'.requirements.txt')
        freeze.write_text(frozen)
        provenance.update(build_verified=True, image_id=image, build_log=str(log.relative_to(ROOT)),
                          installed_versions=str(freeze.relative_to(ROOT)), snapshot_check_verified=True)
        result.update(status='built_and_snapshot_checked', image_id=image)
    except (subprocess.SubprocessError, OSError) as exc:
        provenance.update(build_verified=False, build_log=str(log.relative_to(ROOT)))
        result.update(status='failed', error=type(exc).__name__)
    result['seconds'] = round(time.monotonic()-start, 2)
    p.write_text(json.dumps(provenance, indent=2)+'\n')
    print(result['status'].upper(), task.name, result['seconds'], flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task', action='append')
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--check-cli', action='store_true', help='Check CLI loading in existing images; no agents or LLM calls')
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('workers must be positive')
    tasks = [p.parent for p in sorted(TASKS.glob('*/provenance.json'))
             if (p.parent.name in args.task if args.task else
                 'test_source_sha256' in json.loads(p.read_text()) and
                 (args.check_cli or not json.loads(p.read_text()).get('build_verified')))]
    if args.task and set(args.task)-{p.name for p in tasks}:
        parser.error('Unknown task')
    logs = ROOT/'output/specguard_builds'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    logs.mkdir(parents=True, exist_ok=False)
    results = []
    print(f'{"Checking CLI in" if args.check_cli else "Building"} {len(tasks)} images; logs: {logs}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check_cli if args.check_cli else build, task, logs) for task in tasks]
        for future in as_completed(futures):
            results.append(future.result())
            (logs/'summary.json').write_text(json.dumps(sorted(results, key=lambda r:r['task']), indent=2)+'\n')
    raise SystemExit(any(r['status'] in {'failed', 'cli_check_failed'} for r in results))


if __name__ == '__main__':
    main()
