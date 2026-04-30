import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FLAKE_UPDATE = ROOT / "host" / "runtime" / "bin" / "proxnix-flake-update"
RUST_FLAKE_UPDATE = ROOT / "host" / "rust" / "src" / "flake_update.rs"


class FlakeUpdateTests(unittest.TestCase):
    def test_runtime_command_dispatches_to_rust_controller(self) -> None:
        wrapper = FLAKE_UPDATE.read_text(encoding="utf-8")
        rust = RUST_FLAKE_UPDATE.read_text(encoding="utf-8")

        self.assertIn('exec "$PROXNIX_HOST_BIN" flake-update "$@"', wrapper)
        self.assertIn("fn run_under_flock", rust)
        self.assertIn('arg("-n")', rust)
        self.assertIn('"reconcile.lock"', rust)
        self.assertIn("fn render", rust)
        self.assertIn("fn nix_flake_update", rust)
        self.assertIn("fn persist_authority_lock", rust)
        self.assertIn("PROXNIX_FLAKE_UPDATE_INPUTS", rust)
        self.assertIn("PROXNIX_FLAKE_UPDATE_FREQUENCY", rust)


if __name__ == "__main__":
    unittest.main()
