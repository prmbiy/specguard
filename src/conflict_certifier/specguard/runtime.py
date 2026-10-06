"""Select an existing host toolchain or a self-contained Lean Docker image."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from conflict_certifier.config import LeanEnv
from .artifacts import GuardError

IMAGE = "specguard-lean:4.24.0"
IMAGE_ROOT = Path("/opt/specguard-lean")


@dataclass(frozen=True)
class ImageLeanEnv(LeanEnv):
    """Paths belong to the image, so do not require them on the host."""

    def validate(self):
        if self.repl_processes < 1 or self.repl_bin is None:
            raise GuardError("Invalid image REPL configuration")


def runtime_file() -> Path:
    return Path.home() / ".config/specguard/runtime.json"


def lean_environment() -> LeanEnv:
    selected = {}
    # Explicit environment settings override the saved setup choice.
    if not os.environ.get("SPECGUARD_LEAN_ROOT") and not os.environ.get("SPECGUARD_LEAN_IMAGE"):
        path = runtime_file()
        if path.exists():
            try:
                selected = json.loads(path.read_text())
                if not isinstance(selected, dict) or selected.get("backend") not in {"host", "image"}:
                    raise ValueError("expected host or image backend")
                key = "image" if selected["backend"] == "image" else "root"
                if not isinstance(selected.get(key), str) or not selected[key].strip():
                    raise ValueError(f"expected a nonempty {key}")
            except (ValueError, OSError) as exc:
                raise GuardError(f"Invalid Lean runtime configuration {path}: {exc}") from exc
    image = os.environ.get("SPECGUARD_LEAN_IMAGE") or selected.get("image")
    managed = bool(image) and not os.environ.get("SPECGUARD_LEAN_ROOT")
    root = IMAGE_ROOT if managed else Path(os.environ.get("SPECGUARD_LEAN_ROOT") or
                                          selected.get("root", "/mnt/data/lean")).expanduser().resolve()
    cls = ImageLeanEnv if managed else LeanEnv
    return cls(project_dir=root / "workspace", repl_bin=root / "repl/.lake/build/bin/repl",
               container_mount_root=root, repl_processes=1,
               container_image=image if managed else "debian:bookworm-slim",
               container_memory=os.environ.get("SPECGUARD_LEAN_MEMORY", "3500m"))


def setup(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="specguard setup", description=
        "Build the pinned Lean Docker image, or select an existing Lean directory. No model API calls.")
    parser.add_argument("--lean-root", type=Path, help="Use an existing directory containing workspace/, repl/, and elan/ instead of building")
    parser.add_argument("--image", default=IMAGE, help=f"Docker image tag to build (default: {IMAGE})")
    args = parser.parse_args(argv)
    from .checker import OwnedRepl
    from conflict_certifier.lean.repl import docker_available, _image_present
    try:
        if not docker_available():
            raise GuardError("Install Docker and ensure its daemon is accessible before setup")
        if args.lean_root:
            root = args.lean_root.expanduser().resolve()
            selected = {"backend": "host", "root": str(root)}
            env = LeanEnv(project_dir=root / "workspace", repl_bin=root / "repl/.lake/build/bin/repl",
                          container_mount_root=root)
            env.validate()
            if not _image_present(env.container_image):
                subprocess.run(["docker", "pull", env.container_image], check=True)
        else:
            if not args.image.strip() or args.image.startswith("-"):
                raise GuardError("Provide a valid Docker image tag")
            print("[specguard] Building Lean 4.24.0, Mathlib and REPL; first setup downloads several GB.", flush=True)
            subprocess.run(["docker", "build", "--tag", args.image,
                            str(Path(__file__).with_name("runtime"))], check=True)
            selected = {"backend": "image", "image": args.image}
            env = ImageLeanEnv(project_dir=IMAGE_ROOT / "workspace",
                               repl_bin=IMAGE_ROOT / "repl/.lake/build/bin/repl",
                               container_mount_root=IMAGE_ROOT, container_image=args.image)
        # Select only after the real REPL imports Mathlib and verifies a proof offline.
        repl = OwnedRepl(env)
        try:
            print("[specguard] Verifying the Lean REPL with networking disabled...", flush=True)
            repl.start()
            ok, message = repl.compile("example : (1 : Nat) + 1 = 2 := by decide", timeout=60)
            if not ok:
                raise GuardError("Lean setup check failed: " + message)
        finally:
            repl.close()
        path = runtime_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(selected, indent=2) + "\n")
        temporary.replace(path)
        print(f"[specguard] Lean runtime ready. Saved selection in {path}")
        if os.environ.get("SPECGUARD_LEAN_ROOT") or os.environ.get("SPECGUARD_LEAN_IMAGE"):
            print("[specguard] Your SPECGUARD_LEAN_ROOT/SPECGUARD_LEAN_IMAGE environment overrides this saved choice.")
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"[specguard] Setup failed: {exc}")
        return 2
    except KeyboardInterrupt:
        print("[specguard] Setup interrupted; previous runtime selection preserved.")
        return 130
