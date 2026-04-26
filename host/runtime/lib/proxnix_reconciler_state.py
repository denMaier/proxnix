#!/usr/bin/env python3
"""Node-local SQLite journal for proxnix reconciliation state."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_DB = "/var/lib/proxnix/state/proxnix-reconciler.sqlite"

SCHEMA = """
create table if not exists container_observations (
  vmid integer primary key,
  node text not null,
  desired_system text,
  current_system text,
  container_is_local integer not null,
  last_phase text,
  last_status text,
  last_error text,
  updated_at text not null
);

create table if not exists closure_observations (
  store_path text primary key,
  host_has_closure integer,
  container_has_closure integer,
  protected_by_host_gc_root integer not null default 0,
  gc_root_path text,
  updated_at text not null
);

create table if not exists deployment_attempts (
  id integer primary key autoincrement,
  vmid integer not null,
  store_path text,
  phase text not null,
  status text not null,
  error text,
  started_at text not null,
  finished_at text
);

create index if not exists deployment_attempts_vmid_idx
  on deployment_attempts(vmid);
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean: {value}")


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def observe_container(
    conn: sqlite3.Connection,
    *,
    vmid: int,
    node: str,
    desired_system: str | None,
    current_system: str | None,
    container_is_local: bool,
    last_phase: str | None,
    last_status: str | None,
    last_error: str | None,
    updated_at: str | None = None,
) -> None:
    init_db(conn)
    conn.execute(
        """
        insert into container_observations (
          vmid, node, desired_system, current_system, container_is_local,
          last_phase, last_status, last_error, updated_at
        )
        values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(vmid) do update set
          node = excluded.node,
          desired_system = excluded.desired_system,
          current_system = excluded.current_system,
          container_is_local = excluded.container_is_local,
          last_phase = excluded.last_phase,
          last_status = excluded.last_status,
          last_error = excluded.last_error,
          updated_at = excluded.updated_at
        """,
        (
            vmid,
            node,
            desired_system,
            current_system,
            bool_to_int(container_is_local),
            last_phase,
            last_status,
            last_error,
            updated_at or now(),
        ),
    )
    conn.commit()


def observe_closure(
    conn: sqlite3.Connection,
    *,
    store_path: str,
    host_has_closure: bool | None,
    container_has_closure: bool | None,
    protected_by_host_gc_root: bool,
    gc_root_path: str | None,
    updated_at: str | None = None,
) -> None:
    init_db(conn)
    conn.execute(
        """
        insert into closure_observations (
          store_path, host_has_closure, container_has_closure,
          protected_by_host_gc_root, gc_root_path, updated_at
        )
        values (?, ?, ?, ?, ?, ?)
        on conflict(store_path) do update set
          host_has_closure = excluded.host_has_closure,
          container_has_closure = excluded.container_has_closure,
          protected_by_host_gc_root = excluded.protected_by_host_gc_root,
          gc_root_path = excluded.gc_root_path,
          updated_at = excluded.updated_at
        """,
        (
            store_path,
            bool_to_int(host_has_closure),
            bool_to_int(container_has_closure),
            bool_to_int(protected_by_host_gc_root),
            gc_root_path,
            updated_at or now(),
        ),
    )
    conn.commit()


def record_attempt(
    conn: sqlite3.Connection,
    *,
    vmid: int,
    store_path: str | None,
    phase: str,
    status: str,
    error: str | None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> int:
    init_db(conn)
    cursor = conn.execute(
        """
        insert into deployment_attempts (
          vmid, store_path, phase, status, error, started_at, finished_at
        )
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        (vmid, store_path, phase, status, error, started_at or now(), finished_at),
    )
    conn.commit()
    return int(cursor.lastrowid)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    container = sub.add_parser("observe-container")
    container.add_argument("--vmid", type=int, required=True)
    container.add_argument("--node", required=True)
    container.add_argument("--desired-system")
    container.add_argument("--current-system")
    container.add_argument("--container-is-local", type=parse_bool, required=True)
    container.add_argument("--last-phase")
    container.add_argument("--last-status")
    container.add_argument("--last-error")

    closure = sub.add_parser("observe-closure")
    closure.add_argument("--store-path", required=True)
    closure.add_argument("--host-has-closure", type=parse_bool)
    closure.add_argument("--container-has-closure", type=parse_bool)
    closure.add_argument("--protected-by-host-gc-root", type=parse_bool, default=False)
    closure.add_argument("--gc-root-path")

    attempt = sub.add_parser("record-attempt")
    attempt.add_argument("--vmid", type=int, required=True)
    attempt.add_argument("--store-path")
    attempt.add_argument("--phase", required=True)
    attempt.add_argument("--status", required=True)
    attempt.add_argument("--error")
    attempt.add_argument("--finished-at")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with connect(args.db) as conn:
        if args.command == "init":
            init_db(conn)
        elif args.command == "observe-container":
            observe_container(
                conn,
                vmid=args.vmid,
                node=args.node,
                desired_system=args.desired_system,
                current_system=args.current_system,
                container_is_local=args.container_is_local,
                last_phase=args.last_phase,
                last_status=args.last_status,
                last_error=args.last_error,
            )
        elif args.command == "observe-closure":
            observe_closure(
                conn,
                store_path=args.store_path,
                host_has_closure=args.host_has_closure,
                container_has_closure=args.container_has_closure,
                protected_by_host_gc_root=args.protected_by_host_gc_root,
                gc_root_path=args.gc_root_path,
            )
        elif args.command == "record-attempt":
            attempt_id = record_attempt(
                conn,
                vmid=args.vmid,
                store_path=args.store_path,
                phase=args.phase,
                status=args.status,
                error=args.error,
                finished_at=args.finished_at,
            )
            print(attempt_id)
        else:
            raise AssertionError(args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
