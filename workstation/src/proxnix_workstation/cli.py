from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from .config import load_workstation_config
from .errors import ProxnixWorkstationError
from .json_api import ok, print_json
from .manager_api import build_status
from .planning import PlanRunner
from .publish_tree import build_desired_config_tree
from .resources import MirrorTree


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxnix",
        description="Unified proxnix workstation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  proxnix status --json\n"
            "  proxnix config show\n"
            "  proxnix publish --dry-run\n"
            "  proxnix doctor --site-only\n"
            "  proxnix secrets ls 120\n"
            "  proxnix tui\n"
            "  proxnix exercise lxc --host root@node1 --base-vmid 940"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to the proxnix workstation config file",
    )
    parser.add_argument(
        "command",
        choices=["status", "config", "publish", "doctor", "validation", "secrets", "tui", "ui", "exercise"],
        help="Command group to run",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _strip_remainder_prefix(args: list[str]) -> list[str]:
    if args[:1] == ["--"]:
        return args[1:]
    return args


def _prepend_config(args: list[str], config: Path | None) -> list[str]:
    if config is None:
        return args
    if "--config" in args:
        return args
    return ["--config", str(config), *args]


def _forward(main_fn, args: list[str], *, config: Path | None, accepts_config: bool, prog: str) -> int:
    forwarded = _strip_remainder_prefix(list(args))
    if accepts_config:
        forwarded = _prepend_config(forwarded, config)
    return main_fn(forwarded, prog=prog)


def _run_status(args: argparse.Namespace) -> int:
    status = build_status(args.config)
    if args.json:
        print_json(ok(status, warnings=list(status["warnings"])))
        return 0

    config = status["config"]
    assert isinstance(config, dict)
    print(f"config: {status['configPath']}")
    print(f"site: {config.get('siteDir') or '(not set)'}")
    print(f"containers: {len(status['containers'])}")
    warnings = status["warnings"]
    if isinstance(warnings, list):
        for warning in warnings:
            print(f"warning: {warning}")
    return 0


def _run_show_config(args: argparse.Namespace) -> int:
    config = load_workstation_config(args.config)
    print(config.to_json())
    return 0


def _build_config_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxnix config", description="Inspect or plan workstation config state")
    subparsers = parser.add_subparsers(dest="config_command", required=True)
    subparsers.add_parser("show", help="Print normalized workstation config")

    plan_parser = subparsers.add_parser(
        "plan-tree",
        help="Plan or apply the config-only publish tree to a local output path",
    )
    plan_parser.add_argument("output", type=Path, help="Destination root to converge")
    plan_parser.add_argument("--vmid", help="Restrict the desired tree to one VMID")
    plan_parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the planned changes instead of running in check mode",
    )
    return parser


def _build_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxnix status", description="Inspect workstation and site state")
    parser.add_argument("--json", action="store_true", help="Emit a structured JSON envelope")
    return parser


def _run_plan_config_tree(args: argparse.Namespace) -> int:
    config = load_workstation_config(args.config)
    with tempfile.TemporaryDirectory(prefix="proxnix-config-tree.") as temp_dir:
        desired_root = build_desired_config_tree(
            config,
            Path(temp_dir) / "desired",
            target_vmid=args.vmid,
        )
        report = PlanRunner(
            [
                MirrorTree(
                    desired_root,
                    args.output.expanduser(),
                    name="config-tree",
                )
            ]
        ).run(check=not args.apply)

    print(report.render_text())
    return 1 if report.has_failures else 0


def _build_exercise_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="proxnix exercise", description="Run automated proxnix exercise labs")
    parser.add_argument(
        "exercise_command",
        choices=["lxc"],
        help="Exercise lab to run",
    )
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            status_args = _build_status_parser().parse_args(_strip_remainder_prefix(args.args))
            status_args.config = args.config
            return _run_status(status_args)

        if args.command == "config":
            config_args = _build_config_parser().parse_args(_strip_remainder_prefix(args.args))
            config_args.config = args.config
            if config_args.config_command == "show":
                return _run_show_config(config_args)
            if config_args.config_command == "plan-tree":
                return _run_plan_config_tree(config_args)

        if args.command == "publish":
            from .publish_cli import main as publish_main

            return _forward(publish_main, args.args, config=args.config, accepts_config=True, prog="proxnix publish")
        if args.command in {"doctor", "validation"}:
            from .doctor_cli import main as doctor_main

            prog = "proxnix validation" if args.command == "validation" else "proxnix doctor"
            return _forward(doctor_main, args.args, config=args.config, accepts_config=True, prog=prog)
        if args.command == "secrets":
            from .secrets_cli import main as secrets_main

            return _forward(secrets_main, args.args, config=args.config, accepts_config=True, prog="proxnix secrets")
        if args.command in {"tui", "ui"}:
            from .tui import main as tui_main

            return _forward(tui_main, args.args, config=args.config, accepts_config=True, prog="proxnix tui")
        if args.command == "exercise":
            exercise_args = _build_exercise_parser().parse_args(_strip_remainder_prefix(args.args))
            if exercise_args.exercise_command == "lxc":
                from .exercise_cli import main as exercise_main

                return _forward(
                    exercise_main,
                    exercise_args.args,
                    config=args.config,
                    accepts_config=True,
                    prog="proxnix exercise lxc",
                )
    except ProxnixWorkstationError as exc:
        print(f"error: {exc}")
        return 2

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
