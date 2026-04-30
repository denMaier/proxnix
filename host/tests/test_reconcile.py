import os
import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


RECONCILE = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile"
RECONCILE_BUILD_GOLDEN = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile-build-golden"
RECONCILE_BUILD = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile-build"
RECONCILE_SEED = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile-seed"
RECONCILE_SEED_OFFLINE = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile-seed-offline"
RECONCILE_ACTIVATE = Path(__file__).resolve().parents[1] / "runtime" / "bin" / "proxnix-reconcile-activate"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def fake_nix_store_stub(*, requisites: str | None = None, marker: str | None = None) -> str:
    """Shell body for a fake `nix-store` that the reconciler can drive.

    Supports the operations protect_host_closure / protect_golden_closure /
    gcroot_present / seed_closure exercise:

    * ``nix-store --realise --add-root <root> --indirect <store>`` creates the
      symlink the test expects (and acts as a successful gcroot registration).
    * ``nix-store --query --roots <store>`` scans ``$PROXNIX_GCROOT_DIR`` and
      emits any symlinks in it that point at ``<store>``. This is what the
      ``gcroot_present`` probe relies on to report the truthful
      ``protected_by_host_gc_root`` field.
    * ``nix-store --query --requisites <store>`` emits ``requisites`` if given,
      otherwise falls back to the store path itself.
    * ``nix-store --export`` emits a small dummy payload.

    ``marker`` (optional) is a path that gets ``touch``ed on every call, so a
    test can assert nix-store was (or was not) invoked.
    """
    requisites_block = (
        "\n".join(f"printf '%s\\n' {p}" for p in requisites.split())
        if requisites
        else "printf '%s\\n' \"$1\""
    )
    marker_line = f"touch {marker}\n" if marker else ""
    return r"""#!/bin/sh
""" + marker_line + r"""case "$1" in
  --realise)
    shift
    root=""
    target=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --add-root) root="$2"; shift 2;;
        --indirect) shift;;
        *) target="$1"; shift;;
      esac
    done
    if [ -n "$root" ] && [ -n "$target" ]; then
      mkdir -p "$(dirname "$root")"
      ln -sfn "$target" "$root"
    fi
    [ -n "$target" ] && printf '%s\n' "$target"
    ;;
  --query)
    shift
    case "$1" in
      --roots)
        shift
        target="$1"
        gcroot_dir="${PROXNIX_GCROOT_DIR:-${PROXNIX_DIR:-/var/lib/proxnix}/gcroots/deploy}"
        if [ -d "$gcroot_dir" ]; then
          for f in "$gcroot_dir"/*; do
            [ -L "$f" ] || continue
            link="$(readlink "$f" 2>/dev/null || true)"
            if [ "$link" = "$target" ]; then
              printf '%s\n' "$f"
            fi
          done
        fi
        ;;
      --requisites)
        shift
        REQ_TARGET="$1"
""" + (
        "        " + requisites_block.replace("\n", "\n        ") + "\n"
        if requisites
        else "        printf '%s\\n' \"$REQ_TARGET\"\n"
    ) + r"""        ;;
      *)
        exit 2
        ;;
    esac
    ;;
  --export)
    cat /dev/null
    printf 'exported\n'
    ;;
  --import)
    cat >/dev/null
    ;;
  *)
    exit 2
    ;;
esac
"""


def fake_authority_render_stub() -> str:
    return r"""#!/bin/sh
set -eu
root=""
authority=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root) root="$2"; shift 2;;
    --authority) authority="$2"; shift 2;;
    *) shift;;
  esac
done
[ -n "$authority" ] || exit 3
mkdir -p "$authority"
if [ -n "$root" ] && [ -f "$root/flake.lock" ]; then
  cp "$root/flake.lock" "$authority/flake.lock"
fi
"""


def fake_socat_bridge_stub() -> str:
    """Fake a long-running socat listener by creating the requested socket path."""
    return r"""#!/bin/sh
listen="$1"
case "$listen" in
  UNIX-LISTEN:*)
    socket="${listen#UNIX-LISTEN:}"
    socket="${socket%%,*}"
    ;;
  *)
    exit 2
    ;;
esac
rm -f "$socket"
touch "$socket"
trap 'rm -f "$socket"; exit 0' INT TERM EXIT
while :; do
  sleep 1
done
"""


class ReconcileDryRunTests(unittest.TestCase):
    def setUp(self) -> None:
        previous = os.environ.get("PROXNIX_AUTHORITY_RENDER")
        renderer_tmp = tempfile.TemporaryDirectory()
        renderer = Path(renderer_tmp.name) / "proxnix-authority-render"
        write_executable(renderer, fake_authority_render_stub())
        os.environ["PROXNIX_AUTHORITY_RENDER"] = str(renderer)
        self.addCleanup(renderer_tmp.cleanup)
        self.addCleanup(self._restore_authority_render, previous)

    @staticmethod
    def _restore_authority_render(previous: str | None) -> None:
        if previous is None:
            os.environ.pop("PROXNIX_AUTHORITY_RENDER", None)
        else:
            os.environ["PROXNIX_AUTHORITY_RENDER"] = previous

    def test_golden_template_build_warms_and_protects_local_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            gcroots = root / "gcroots" / "deploy"
            fake_bin.mkdir()
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
printf '%s\n' "$*" > "$PROXNIX_NIX_ARGS_FILE"
out_link=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out-link) out_link="$2"; shift 2;;
    *) shift;;
  esac
done
[ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/golden-template-system "$out_link"; }
printf '%s\n' /nix/store/golden-template-system
""",
            )
            write_executable(fake_bin / "nix-store", fake_nix_store_stub())

            env = os.environ.copy()
            nix_args_file = Path(tmp) / "nix-args"
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_GCROOT_DIR": str(gcroots),
                    "PROXNIX_NODE_NAME": "pve1",
                    "PROXNIX_NIX_ARGS_FILE": str(nix_args_file),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_BUILD_GOLDEN)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("golden-template built /nix/store/golden-template-system", result.stdout)
            self.assertIn(
                "#nixosConfigurations.proxnix-golden-template.config.system.build.toplevel",
                nix_args_file.read_text(encoding="utf-8"),
            )
            self.assertNotIn("--no-write-lock-file", nix_args_file.read_text(encoding="utf-8"))
            self.assertEqual(os.readlink(gcroots / "golden-template"), "/nix/store/golden-template-system")

    def test_golden_template_build_uses_published_lock_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            authority = root / "authority"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (root / "flake.lock").write_text('{"nodes":{}}\n', encoding="utf-8")

            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
printf '%s\n' "$*" > "$PROXNIX_NIX_ARGS_FILE"
printf '%s\n' /nix/store/golden-template-system
""",
            )
            write_executable(fake_bin / "nix-store", fake_nix_store_stub())

            env = os.environ.copy()
            nix_args_file = Path(tmp) / "nix-args"
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_AUTHORITY_DIR": str(authority),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_NODE_NAME": "pve1",
                    "PROXNIX_NIX_ARGS_FILE": str(nix_args_file),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_BUILD_GOLDEN)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((authority / "flake.lock").read_text(encoding="utf-8"), '{"nodes":{}}\n')
            self.assertNotIn("--no-write-lock-file", nix_args_file.read_text(encoding="utf-8"))

    def test_dry_run_prints_planned_actions_for_selected_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\n[ \"$1\" = status ] && exit 0\nexit 2\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","system":"/nix/store/system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "101 build /nix/store/system-101",
                    "101 keep local CT",
                    "101 seed desired closure",
                    "101 activate desired system",
                ],
            )

    def test_dry_run_rejects_missing_selected_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "nix", "#!/bin/sh\nprintf '%s\\n' '{\"containers\":{}}'\n")

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(Path(tmp) / "pve" / "lxc"),
                    "PROXNIX_RUN_DIR": str(Path(tmp) / "run"),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("VMID 101 is not present", result.stderr)

    def test_dry_run_skips_nonlocal_selected_vmid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\nexit 1\n")
            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nprintf '%s\\n' '{\"containers\":{\"101\":{\"vmid\":101,\"system\":\"/nix/store/system-101\"}}}'\n",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(Path(tmp) / "run"),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 skip not-local")

    def test_dry_run_prefers_cluster_placement_for_remote_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\n[ \"$1\" = status ] && exit 0\nexit 2\n")
            write_executable(
                fake_bin / "pvesh",
                """#!/bin/sh
cat <<'JSON'
[{"vmid":101,"type":"lxc","node":"pve2"}]
JSON
""",
            )
            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nprintf '%s\\n' '{\"containers\":{\"101\":{\"vmid\":101,\"system\":\"/nix/store/system-101\"}}}'\n",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(Path(tmp) / "run"),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 skip not-local")

    def test_dry_run_uses_cluster_placement_for_local_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\nexit 2\n")
            write_executable(
                fake_bin / "pvesh",
                """#!/bin/sh
cat <<'JSON'
[{"vmid":101,"type":"lxc","node":"pve1"}]
JSON
""",
            )
            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nprintf '%s\\n' '{\"containers\":{\"101\":{\"vmid\":101,\"system\":\"/nix/store/system-101\"}}}'\n",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(Path(tmp) / "run"),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--dry-run", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "101 build /nix/store/system-101",
                    "101 keep local CT",
                    "101 seed desired closure",
                    "101 activate desired system",
                ],
            )

    def test_build_only_writes_status_without_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\n[ \"$1\" = status ] && exit 0\nexit 2\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":{"commit":"abc123"},"system":"/nix/store/eval-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/eval-system-101 "$out_link"; }
    printf '%s\n' /nix/store/eval-system-101
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(fake_bin / "nix-store", fake_nix_store_stub())

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--build-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 built /nix/store/eval-system-101")
            status = (status_dir / "101.json").read_text(encoding="utf-8")
            self.assertIn('"desiredSystem": "/nix/store/eval-system-101"', status)
            self.assertIn('"desired_system": "/nix/store/eval-system-101"', status)
            self.assertIn('"host_has_closure": true', status)
            self.assertIn('"protected_by_host_gc_root": true', status)
            self.assertIn('"lastBuildStatus": "ok"', status)
            self.assertIn('"lastDeployStatus": "not-run"', status)
            self.assertIn('"currentSystem": null', status)
            gcroot = root / "gcroots" / "deploy" / "101-desired"
            self.assertTrue(gcroot.is_symlink())
            self.assertEqual(os.readlink(gcroot), "/nix/store/eval-system-101")

    def test_build_only_skips_when_status_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/system-101",
                        "currentSystem": "/nix/store/system-101",
                        "previousSystem": "/nix/store/previous-system-101",
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "ok",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "pct", "#!/bin/sh\n[ \"$1\" = status ] && exit 0\nexit 2\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","system":"/nix/store/system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    printf 'unexpected build\\n' >&2
    exit 9
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--build-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 noop current system matches desired")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["desiredSystem"], "/nix/store/system-101")
            self.assertEqual(status["currentSystem"], "/nix/store/system-101")
            self.assertEqual(status["previousSystem"], "/nix/store/previous-system-101")
            self.assertEqual(status["lastDeployStatus"], "noop-current")

    def test_seed_only_imports_closure_and_marks_status_seeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            run_dir.mkdir()
            status_dir.mkdir(parents=True)
            (run_dir / "ct-101.sock").write_text("stale socket", encoding="utf-8")
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "currentSystem": None,
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                        "lastError": None,
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/dep-a /nix/store/built-system-101"),
            )
            nix_remote_marker = Path(tmp) / "nix-remote"
            write_executable(
                fake_bin / "nix",
                f"""#!/bin/sh
if [ "$1" = "copy" ]; then
  printf '%s\\n' "$NIX_REMOTE" > {nix_remote_marker}
  exit 0
fi
exit 2
""",
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" = "status" ]; then
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
if [ "$4" = "nix-store" ] && [ "$5" = "--import" ]; then
  cat >/dev/null
  exit 0
fi
if [ "$4" = "test" ] && [ "$5" = "-x" ]; then
  exit 0
fi
exit 2
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--seed-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 seeded /nix/store/built-system-101")
            self.assertEqual(nix_remote_marker.read_text(encoding="utf-8").strip(), f"unix://{run_dir}/ct-101.sock")
            self.assertFalse((run_dir / "ct-101.sock").exists())
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "seeded")
            self.assertIsNone(status["lastError"])

    def test_seed_only_records_failed_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "nix-store", fake_nix_store_stub())
            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nif [ \"$1\" = copy ]; then echo copy failed >&2; exit 1; fi\nexit 2\n",
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                "#!/bin/sh\nif [ \"$1\" = status ]; then exit 0; fi\nexit 2\n",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--seed-only", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "failed")
            self.assertIn("closure seed failed", status["lastError"])
            self.assertFalse((run_dir / "ct-101.sock").exists())

    def test_seed_offline_copies_to_rootfs_and_sets_boot_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            rootfs = Path(tmp) / "rootfs"
            fake_bin = Path(tmp) / "bin"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (rootfs / "etc").mkdir(parents=True)
            (rootfs / "sbin").mkdir()
            (rootfs / "sbin" / "init").write_text("# old concrete NixOS LXC init\n", encoding="utf-8")
            status_file = status_dir / "101.json"
            status_file.write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "currentSystem": "/nix/store/old-system-101",
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                        "lastError": None,
                    }
                ),
                encoding="utf-8",
            )
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
if [ "$1" != "copy" ] || [ "$3" != "--to" ]; then
  exit 2
fi
root="${4#local?root=}"
system="$5"
mkdir -p "${root}${system}/bin"
printf '#!/bin/sh\\nexit 0\\n' > "${root}${system}/bin/switch-to-configuration"
chmod +x "${root}${system}/bin/switch-to-configuration"
printf '#!/bin/sh\\nexit 0\\n' > "${root}${system}/init"
chmod +x "${root}${system}/init"
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_SEED_OFFLINE), "--vmid", "101", "--rootfs", str(rootfs)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 offline-seeded /nix/store/built-system-101")
            runtime = rootfs / "var" / "lib" / "proxnix" / "runtime"
            self.assertEqual(
                (runtime / "next-system").read_text(encoding="utf-8").strip(),
                "/nix/store/built-system-101",
            )
            self.assertEqual(
                (runtime / "previous-system").read_text(encoding="utf-8").strip(),
                "/nix/store/old-system-101",
            )
            profiles = rootfs / "nix" / "var" / "nix" / "profiles"
            system_profile = profiles / "system"
            self.assertTrue(system_profile.is_symlink())
            generation_link = profiles / os.readlink(system_profile)
            self.assertTrue(generation_link.is_symlink())
            self.assertEqual(os.readlink(generation_link), "/nix/store/built-system-101")
            self.assertTrue((rootfs / "sbin" / "init").is_symlink())
            self.assertEqual(os.readlink(rootfs / "sbin" / "init"), "/nix/var/nix/profiles/system/init")
            self.assertTrue((rootfs / "nix" / "var" / "nix" / "gcroots" / "profiles").is_symlink())
            status = json.loads(status_file.read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "offline-seeded")
            self.assertTrue(status["container_has_closure"])

    def test_seed_offline_records_guest_activation_marker_without_trusting_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            rootfs = Path(tmp) / "rootfs"
            status_dir = root / "status"
            runtime = rootfs / "var" / "lib" / "proxnix" / "runtime"
            status_dir.mkdir(parents=True)
            (rootfs / "etc").mkdir(parents=True)
            runtime.mkdir(parents=True)
            (runtime / "activated-system").write_text("/nix/store/built-system-101\n", encoding="utf-8")
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "currentSystem": "/nix/store/old-system-101",
                        "previousSystem": "/nix/store/old-system-101",
                        "lastBuildStatus": "failed",
                        "lastDeployStatus": "offline-seeded",
                        "lastError": None,
                    }
                ),
                encoding="utf-8",
            )

            env = os.environ.copy()
            env.update({"PROXNIX_DIR": str(root)})

            result = subprocess.run(
                [str(RECONCILE_SEED_OFFLINE), "--vmid", "101", "--rootfs", str(rootfs)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 offline seed skipped build not ok")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["currentSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "offline-seeded")
            self.assertEqual(status["guestActivatedSystem"], "/nix/store/built-system-101")
            self.assertTrue(status["guestActivationMarkerDrift"])

    def test_seed_offline_repairs_profile_even_when_status_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            rootfs = Path(tmp) / "rootfs"
            fake_bin = Path(tmp) / "bin"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (rootfs / "etc").mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/built-system-101",
                        "currentSystem": "/nix/store/built-system-101",
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "not-run",
                    }
                ),
                encoding="utf-8",
            )
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
root="${4#local?root=}"
system="$5"
mkdir -p "${root}${system}/bin"
printf '#!/bin/sh\\nexit 0\\n' > "${root}${system}/bin/switch-to-configuration"
chmod +x "${root}${system}/bin/switch-to-configuration"
printf '#!/bin/sh\\nexit 0\\n' > "${root}${system}/init"
chmod +x "${root}${system}/init"
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_SEED_OFFLINE), "--vmid", "101", "--rootfs", str(rootfs)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 offline-seeded /nix/store/built-system-101")
            profiles = rootfs / "nix" / "var" / "nix" / "profiles"
            generation_link = profiles / os.readlink(profiles / "system")
            self.assertEqual(os.readlink(generation_link), "/nix/store/built-system-101")

    def test_phase_commands_wrap_build_seed_and_activate(self) -> None:
        self.assertIn("--build-only", RECONCILE_BUILD.read_text(encoding="utf-8"))
        self.assertIn("--seed-only", RECONCILE_SEED.read_text(encoding="utf-8"))
        self.assertIn("--rootfs", RECONCILE_SEED.read_text(encoding="utf-8"))
        self.assertIn("proxnix-reconcile-seed-offline", RECONCILE_SEED.read_text(encoding="utf-8"))
        self.assertIn("nix copy", RECONCILE_SEED_OFFLINE.read_text(encoding="utf-8"))
        self.assertIn("--activate-only", RECONCILE_ACTIVATE.read_text(encoding="utf-8"))
        self.assertIn("--start-stopped", RECONCILE.read_text(encoding="utf-8"))

    def test_seed_wrapper_rejects_stopped_container_without_rootfs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / "bin"
            fake_bin.mkdir()
            write_executable(fake_bin / "pct", "#!/bin/sh\nprintf '%s\\n' 'status: stopped'\n")

            env = os.environ.copy()
            env.update({"PATH": f"{fake_bin}:{env['PATH']}"})

            result = subprocess.run(
                [str(RECONCILE_SEED), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("VMID 101 is stopped; pass --rootfs", result.stderr)

    def test_activate_only_activates_recorded_desired_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/desired-system-101",
                        "currentSystem": "/nix/store/old-system-101",
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "seeded",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(fake_bin / "nix-store", fake_nix_store_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/desired-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  /nix/store/desired-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_ACTIVATE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 activated /nix/store/desired-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["currentSystem"], "/nix/store/desired-system-101")
            self.assertEqual(status["hostActivatedSystem"], "/nix/store/desired-system-101")
            self.assertEqual(status["previousSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "ok")

    def test_activate_only_rejects_stopped_container_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/desired-system-101",
                        "currentSystem": "/nix/store/old-system-101",
                        "previousSystem": None,
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "seeded",
                    }
                ),
                encoding="utf-8",
            )
            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\n' 'status: stopped'
  exit 0
fi
if [ "$1" = "start" ]; then
  exit 9
fi
exit 2
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE_ACTIVATE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pass --start-stopped", result.stderr)

    def test_full_reconcile_activates_and_records_previous_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":{"commit":"abc123"},"system":"/nix/store/built-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/built-system-101 "$out_link"; }
    printf '%s\n' /nix/store/built-system-101
    ;;
  copy)
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/dep-a /nix/store/built-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/built-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/built-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 activated /nix/store/built-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["desiredSystem"], "/nix/store/built-system-101")
            self.assertEqual(status["currentSystem"], "/nix/store/built-system-101")
            self.assertEqual(status["hostActivatedSystem"], "/nix/store/built-system-101")
            self.assertEqual(status["desired_system"], "/nix/store/built-system-101")
            self.assertEqual(status["current_system"], "/nix/store/built-system-101")
            self.assertTrue(status["container_is_local"])
            self.assertTrue(status["host_has_closure"])
            self.assertTrue(status["container_has_closure"])
            self.assertTrue(status["protected_by_host_gc_root"])
            self.assertEqual(status["previousSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "ok")
            self.assertIsNone(status["lastError"])

    def test_full_reconcile_without_vmid_only_processes_running_local_containers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            (root / "containers" / "102").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")
            (pve / "102.conf").write_text("ostype: nixos\nhostname: ct102\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","system":"/nix/store/built-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}},"102":{"vmid":102,"hostname":"ct102","system":"/nix/store/built-system-102","systemAttr":"nixosConfigurations.ct102.config.system.build.toplevel","pve":{"hostname":"ct102"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/built-system-101 "$out_link"; }
    printf '%s\n' /nix/store/built-system-101
    ;;
  copy)
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/dep-a /nix/store/built-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  case "$2" in
    101) printf '%s\\n' 'status: running' ;;
    102) printf '%s\\n' 'status: stopped' ;;
    *) exit 2 ;;
  esac
  exit 0
fi
if [ "$1" != "exec" ] || [ "$2" != "101" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/built-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/built-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout.splitlines(),
                [
                    "101 activated /nix/store/built-system-101",
                    "102 skip stopped",
                ],
            )
            self.assertTrue((status_dir / "101.json").is_file())
            self.assertFalse((status_dir / "102.json").exists())

    def test_full_reconcile_skips_build_when_current_system_matches_desired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            build_marker = Path(tmp) / "build-called"
            store_marker = Path(tmp) / "store-called"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                f"""#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{{"nodeName":"pve1","containers":{{"101":{{"vmid":101,"hostname":"ct101","sourceRevision":{{"commit":"abc123"}},"system":"/nix/store/current-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{{"hostname":"ct101"}}}}}}}}
JSON
    ;;
  build)
    touch {build_marker}
    exit 99
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                f"#!/bin/sh\ntouch {store_marker}\nexit 99\n",
            )
            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    printf '%s\n' /nix/store/current-system-101
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 noop current system matches desired")
            self.assertFalse(build_marker.exists(), "nix build should not run for already-current CTs")
            self.assertFalse(store_marker.exists(), "nix-store should not run for already-current CTs")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["desiredSystem"], "/nix/store/current-system-101")
            self.assertEqual(status["currentSystem"], "/nix/store/current-system-101")
            self.assertEqual(status["desired_system"], "/nix/store/current-system-101")
            self.assertEqual(status["current_system"], "/nix/store/current-system-101")
            self.assertTrue(status["container_is_local"])
            self.assertTrue(status["container_has_closure"])
            self.assertEqual(status["lastDeployStatus"], "noop-current")

    def test_full_reconcile_records_build_failure_without_seeding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            store_marker = Path(tmp) / "store-called"
            activation_marker = Path(tmp) / "activation-called"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":{"commit":"abc123"},"system":"/nix/store/desired-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    printf '%s\n' 'substituter unavailable and local build failed' >&2
    exit 1
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                f"#!/bin/sh\ntouch {store_marker}\nexit 99\n",
            )
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    printf '%s\\n' /nix/store/old-system-101
    ;;
  /nix/store/desired-system-101/bin/switch-to-configuration)
    touch {activation_marker}
    exit 99
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr.strip(), "101 build failed")
            self.assertFalse(store_marker.exists(), "nix-store import should not run after build failure")
            self.assertFalse(activation_marker.exists(), "activation should not run after build failure")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["desiredSystem"], "/nix/store/desired-system-101")
            self.assertEqual(status["currentSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["desired_system"], "/nix/store/desired-system-101")
            self.assertEqual(status["current_system"], "/nix/store/old-system-101")
            self.assertFalse(status["host_has_closure"])
            self.assertFalse(status["container_has_closure"])
            self.assertEqual(status["lastBuildStatus"], "failed")
            self.assertEqual(status["lastDeployStatus"], "build-failed")
            self.assertIn("nix build failed", status["lastError"])

    def test_full_reconcile_keeps_gcroot_after_seed_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":null,"system":"/nix/store/desired-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/desired-system-101 "$out_link"; }
    printf '%s\n' /nix/store/desired-system-101
    ;;
  copy)
    printf '%s\n' 'copy failed' >&2
    exit 1
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/dep-a /nix/store/desired-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                """#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    printf '%s\n' /nix/store/old-system-101
    ;;
  nix-store)
    cat >/dev/null
    printf '%s\n' 'import failed' >&2
    exit 1
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr.strip(), "101 seed failed")
            gcroot = root / "gcroots" / "deploy" / "101-desired"
            self.assertTrue(gcroot.is_symlink())
            self.assertEqual(os.readlink(gcroot), "/nix/store/desired-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lastDeployStatus"], "failed")
            self.assertEqual(status["currentSystem"], "/nix/store/old-system-101")
            self.assertTrue(status["host_has_closure"])
            self.assertFalse(status["container_has_closure"])
            self.assertTrue(status["protected_by_host_gc_root"])

    def test_full_reconcile_stops_when_locality_is_lost_before_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            status_calls = Path(tmp) / "status-calls"
            import_marker = Path(tmp) / "import-called"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":null,"system":"/nix/store/desired-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/desired-system-101 "$out_link"; }
    printf '%s\n' /nix/store/desired-system-101
    ;;
  copy)
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/desired-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  count=0
  [ -f {status_calls} ] && count=$(cat {status_calls})
  count=$((count + 1))
  printf '%s\\n' "$count" > {status_calls}
  if [ "$count" -ge 3 ]; then
    exit 1
  fi
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    printf '%s\\n' /nix/store/old-system-101
    ;;
  nix-store)
    touch {import_marker}
    exit 99
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr.strip(), "101 lost locality")
            self.assertFalse(import_marker.exists(), "seed should not start after locality loss")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertFalse(status["local"])
            self.assertFalse(status["container_is_local"])
            self.assertEqual(status["lastDeployStatus"], "lost-locality")
            self.assertIn("before seed", status["lastError"])

    def test_full_reconcile_stops_when_locality_is_lost_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            status_dir = root / "status"
            status_calls = Path(tmp) / "status-calls"
            activation_marker = Path(tmp) / "activation-called"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")
            (pve / "101.conf").write_text("ostype: nixos\nhostname: ct101\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":null,"system":"/nix/store/desired-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101"}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/desired-system-101 "$out_link"; }
    printf '%s\n' /nix/store/desired-system-101
    ;;
  copy)
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/desired-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  count=0
  [ -f {status_calls} ] && count=$(cat {status_calls})
  count=$((count + 1))
  printf '%s\\n' "$count" > {status_calls}
  if [ "$count" -ge 4 ]; then
    exit 1
  fi
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    printf '%s\\n' /nix/store/old-system-101
    ;;
  nix-store)
    cat >/dev/null
    exit 0
    ;;
  test)
    exit 0
    ;;
  /nix/store/desired-system-101/bin/switch-to-configuration)
    touch {activation_marker}
    exit 99
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stderr.strip(), "101 lost locality")
            self.assertFalse(activation_marker.exists(), "activation should not run after locality loss")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertFalse(status["container_is_local"])
            self.assertEqual(status["lastDeployStatus"], "lost-locality")
            self.assertIn("before activation", status["lastError"])

    def test_recreate_missing_calls_create_lxc_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            pve = Path(tmp) / "pve" / "lxc"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "activated"
            create_args = Path(tmp) / "create-args"
            fake_create = Path(tmp) / "proxnix-create-lxc"
            fake_bin.mkdir()
            (root / "containers" / "101").mkdir(parents=True)
            pve.mkdir(parents=True)

            for name in ("base.nix", "common.nix", "security-policy.nix"):
                (root / name).write_text("{ ... }: {}\n", encoding="utf-8")

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix",
                """#!/bin/sh
case "$1" in
  eval)
    cat <<'JSON'
{"nodeName":"pve1","containers":{"101":{"vmid":101,"hostname":"ct101","sourceRevision":null,"system":"/nix/store/built-system-101","systemAttr":"nixosConfigurations.ct101.config.system.build.toplevel","pve":{"hostname":"ct101","memory":2048,"swap":512,"cores":2,"rootfs":"local-lvm:vm-101-disk-0,size=8G","net0":"name=eth0,bridge=vmbr0,ip=dhcp","unprivileged":true},"placement":{"node":"pve1","local":false}}}}
JSON
    ;;
  build)
    shift
    out_link=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --out-link) out_link="$2"; shift 2;;
        *) shift;;
      esac
    done
    [ -n "$out_link" ] && { mkdir -p "$(dirname "$out_link")"; ln -sfn /nix/store/built-system-101 "$out_link"; }
    printf '%s\n' /nix/store/built-system-101
    ;;
  copy)
    exit 0
    ;;
  *)
    exit 2
    ;;
esac
""",
            )
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/built-system-101"),
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_create,
                f"""#!/bin/sh
printf '%s\\n' "$@" > {create_args}
mkdir -p {pve}
printf '%s\\n' 'ostype: nixos' 'hostname: ct101' > {pve / "101.conf"}
""",
            )
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  [ -f {pve / "101.conf"} ] || exit 1
  printf '%s\\n' 'status: stopped'
  exit 0
fi
if [ "$1" = "start" ]; then
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/built-system-101
    else
      printf '%s\\n' /nix/store/old-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/built-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_PVE_LXC_DIR": str(pve),
                    "PROXNIX_RUN_DIR": str(run_dir),
                    "PROXNIX_NODE_NAME": "pve1",
                    "PROXNIX_CREATE_LXC": str(fake_create),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--vmid", "101", "--recreate-missing", "--start-stopped"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = create_args.read_text(encoding="utf-8").splitlines()
            self.assertIn("--no-doctor", args)
            self.assertIn("--no-start", args)
            self.assertIn("local-lvm", args)
            self.assertIn("8", args)
            self.assertIn("vmbr0", args)
            self.assertIn("dhcp", args)

    def test_rollback_activates_previous_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "proxnix"
            fake_bin = Path(tmp) / "bin"
            run_dir = Path(tmp) / "run"
            marker = Path(tmp) / "rolled-back"
            status_dir = root / "status"
            fake_bin.mkdir()
            status_dir.mkdir(parents=True)
            (status_dir / "101.json").write_text(
                json.dumps(
                    {
                        "vmid": 101,
                        "hostname": "ct101",
                        "desiredSystem": "/nix/store/new-system-101",
                        "currentSystem": "/nix/store/new-system-101",
                        "previousSystem": "/nix/store/old-system-101",
                        "lastBuildStatus": "ok",
                        "lastDeployStatus": "ok",
                    }
                ),
                encoding="utf-8",
            )

            write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
            write_executable(
                fake_bin / "nix-store",
                fake_nix_store_stub(requisites="/nix/store/old-system-101"),
            )
            write_executable(
                fake_bin / "nix",
                "#!/bin/sh\nif [ \"$1\" = copy ]; then exit 0; fi\nexit 2\n",
            )
            write_executable(fake_bin / "socat", fake_socat_bridge_stub())
            write_executable(
                fake_bin / "pct",
                f"""#!/bin/sh
if [ "$1" = "status" ]; then
  printf '%s\\n' 'status: running'
  exit 0
fi
if [ "$1" != "exec" ]; then
  exit 2
fi
case "$4" in
  true)
    exit 0
    ;;
  readlink)
    if [ -f {marker} ]; then
      printf '%s\\n' /nix/store/old-system-101
    else
      printf '%s\\n' /nix/store/new-system-101
    fi
    ;;
  nix-store)
    cat >/dev/null
    ;;
  test)
    exit 0
    ;;
  /nix/store/old-system-101/bin/switch-to-configuration)
    touch {marker}
    ;;
  *)
    exit 2
    ;;
esac
""",
            )

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "PROXNIX_DIR": str(root),
                    "PROXNIX_RUN_DIR": str(run_dir),
                }
            )

            result = subprocess.run(
                [str(RECONCILE), "--rollback", "--vmid", "101"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "101 rolled back /nix/store/old-system-101")
            status = json.loads((status_dir / "101.json").read_text(encoding="utf-8"))
            self.assertEqual(status["currentSystem"], "/nix/store/old-system-101")
            self.assertEqual(status["lastDeployStatus"], "rollback-ok")
            self.assertIsNone(status["lastError"])


if __name__ == "__main__":
    unittest.main()
