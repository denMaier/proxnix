import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLAKE_UPDATE = ROOT / "host" / "runtime" / "bin" / "proxnix-flake-update"


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def fake_authority_render() -> str:
    return """#!/bin/sh
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


def fake_nix_update() -> str:
    return """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$PROXNIX_NIX_ARGS_FILE"
flake=""
while [ $# -gt 0 ]; do
  case "$1" in
    --flake) flake="$2"; shift 2;;
    *) shift;;
  esac
done
[ -n "$flake" ] || exit 4
mkdir -p "$flake"
printf '%s\n' '{"nodes":{"nixpkgs":{"locked":"new"}}}' > "$flake/flake.lock"
"""


class FlakeUpdateTests(unittest.TestCase):
    def make_env(self, tmp: str) -> tuple[dict[str, str], Path, Path, Path]:
        root = Path(tmp) / "proxnix"
        authority = root / "authority"
        fake_bin = Path(tmp) / "bin"
        run_dir = Path(tmp) / "run"
        state_dir = root / "state"
        fake_bin.mkdir()
        root.mkdir()
        write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
        write_executable(fake_bin / "nix", fake_nix_update())
        renderer = fake_bin / "proxnix-authority-render"
        write_executable(renderer, fake_authority_render())

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "PROXNIX_DIR": str(root),
                "PROXNIX_AUTHORITY_DIR": str(authority),
                "PROXNIX_AUTHORITY_RENDER": str(renderer),
                "PROXNIX_RUN_DIR": str(run_dir),
                "PROXNIX_STATE_DIR": str(state_dir),
                "PROXNIX_NODE_NAME": "pve1",
                "PROXNIX_FLAKE_UPDATE_NOW": "1000",
                "PROXNIX_NIX_ARGS_FILE": str(Path(tmp) / "nix-args"),
            }
        )
        return env, root, authority, Path(tmp) / "nix-args"

    def test_updates_authority_lock_and_persists_root_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, root, authority, nix_args = self.make_env(tmp)

            result = subprocess.run(
                [str(FLAKE_UPDATE)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("updated flake lock", result.stdout)
            self.assertEqual(
                (authority / "flake.lock").read_text(encoding="utf-8"),
                '{"nodes":{"nixpkgs":{"locked":"new"}}}\n',
            )
            self.assertEqual(
                (root / "flake.lock").read_text(encoding="utf-8"),
                '{"nodes":{"nixpkgs":{"locked":"new"}}}\n',
            )
            self.assertIn(f"flake update --flake {authority}", nix_args.read_text(encoding="utf-8"))
            self.assertEqual((root / "state" / "flake-update.last-success").read_text(encoding="utf-8"), "1000\n")

    def test_skips_when_frequency_is_not_due(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, root, _authority, nix_args = self.make_env(tmp)
            env["PROXNIX_FLAKE_UPDATE_FREQUENCY"] = "weekly"
            (root / "state").mkdir()
            (root / "state" / "flake-update.last-success").write_text("900\n", encoding="utf-8")

            result = subprocess.run(
                [str(FLAKE_UPDATE)],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("flake update skipped", result.stdout)
            self.assertFalse(nix_args.exists())

    def test_force_updates_even_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, root, authority, nix_args = self.make_env(tmp)
            env["PROXNIX_FLAKE_UPDATE_FREQUENCY"] = "disabled"
            (root / "state").mkdir()
            (root / "state" / "flake-update.last-success").write_text("999\n", encoding="utf-8")

            result = subprocess.run(
                [str(FLAKE_UPDATE), "--force"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"flake update --flake {authority}", nix_args.read_text(encoding="utf-8"))

    def test_passes_configured_inputs_to_nix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env, _root, authority, nix_args = self.make_env(tmp)
            env["PROXNIX_FLAKE_UPDATE_INPUTS"] = "nixpkgs proxnix-extra"

            result = subprocess.run(
                [str(FLAKE_UPDATE), "--input", "site-overrides"],
                check=False,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                f"flake update nixpkgs proxnix-extra site-overrides --flake {authority}",
                nix_args.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
