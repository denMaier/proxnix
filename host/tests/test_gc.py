import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GC = ROOT / "host" / "runtime" / "bin" / "proxnix-gc"
RUST_GC = ROOT / "host" / "rust" / "src" / "gc.rs"


class ProxnixGcTests(unittest.TestCase):
    def test_runtime_command_dispatches_to_rust_controller(self) -> None:
        wrapper = GC.read_text(encoding="utf-8")
        rust = RUST_GC.read_text(encoding="utf-8")

        self.assertIn('exec "$PROXNIX_HOST_BIN" gc "$@"', wrapper)
        self.assertIn("fn prune_stage_dirs", rust)
        self.assertIn("fn prune_gc_roots", rust)
        self.assertIn("golden-template", rust)
        self.assertIn("released stage dir for booted CT", rust)


if __name__ == "__main__":
    unittest.main()
