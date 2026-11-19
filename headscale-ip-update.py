#!/usr/bin/env python3
"""Change one IPv4 address in the Homelab Headscale SQLite database."""

import argparse
from contextlib import closing
import fcntl
import ipaddress
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


DB_PATH = Path("/home/osadmin/Docker/Headscale-stack/headscale/data/db.sqlite")
IPV4_PREFIX = ipaddress.IPv4Network("100.64.0.0/24")
CONTAINER = "headscale"


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "List node IDs from the Docker Compose project directory:\n"
            "  docker compose exec -T headscale headscale nodes list"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("node_id", type=int, help="numeric ID from the ID column")
    parser.add_argument("ipv4", type=ipaddress.IPv4Address)
    parser.add_argument("--apply", action="store_true", help="stop Headscale if running, back up SQLite and apply the change")
    args = parser.parse_args()
    if not 0 < args.node_id <= 2**63 - 1:
        parser.error("node_id must be a positive SQLite integer")
    if args.ipv4 not in IPV4_PREFIX or args.ipv4 in (IPV4_PREFIX.network_address, IPV4_PREFIX.broadcast_address):
        parser.error(f"choose a host address in {IPV4_PREFIX}")
    return args


def open_db(mode):
    return sqlite3.connect(f"{DB_PATH.as_uri()}?mode={mode}", uri=True)


def check_change(db, node_id, new_ip):
    node = db.execute(
        "SELECT given_name, ipv4 FROM nodes WHERE id = ? AND deleted_at IS NULL",
        (node_id,),
    ).fetchone()
    if node is None:
        raise ValueError(f"no active node with ID {node_id}")

    owner = db.execute(
        "SELECT id, given_name FROM nodes WHERE ipv4 = ? AND id <> ? LIMIT 1",
        (new_ip, node_id),
    ).fetchone()
    if owner is not None:
        raise ValueError(f"{new_ip} is already stored for node {owner[0]} ({owner[1]})")
    return node


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def update_db(node_id, new_ip, planned_node):
    with closing(open_db("rw")) as db, db:
        db.execute("BEGIN IMMEDIATE")
        if check_change(db, node_id, new_ip) != planned_node:
            raise ValueError("the node changed since the preview; review it again")

        backup_dir = Path(tempfile.mkdtemp(prefix="headscale-before-ip-", dir=DB_PATH.parent))
        backup_path = backup_dir / DB_PATH.name
        with closing(open_db("ro")) as source, closing(sqlite3.connect(backup_path)) as backup:
            source.backup(backup)
            if backup.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError(f"SQLite verification failed for {backup_path}")
        print(f"SQLite backup: {backup_path}", flush=True)

        updated = db.execute("UPDATE nodes SET ipv4 = ? WHERE id = ?", (new_ip, node_id))
        if updated.rowcount != 1:
            raise ValueError(f"expected one updated node, got {updated.rowcount}")


def apply_change(node_id, new_ip, planned_node):
    with (DB_PATH.parent / ".headscale-ip-update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = docker("inspect", "--format", "{{.State.Status}}", CONTAINER)
        if state not in ("running", "exited"):
            raise ValueError(f"unexpected container state: {state}")

        try:
            if state == "running":
                docker("stop", CONTAINER)
            if docker("inspect", "--format", "{{.State.Status}}", CONTAINER) != "exited":
                raise ValueError("Headscale must be stopped before modifying SQLite")
            update_db(node_id, new_ip, planned_node)
            print("SQLite update committed.", flush=True)
        finally:
            if state == "running":
                docker("start", CONTAINER)
                print("Docker started Headscale.")


def main():
    args = parse_args()
    new_ip = str(args.ipv4)
    with closing(open_db("ro")) as db:
        node = check_change(db, args.node_id, new_ip)

    given_name, old_ip = node
    print(f"Node {args.node_id} ({given_name}): {old_ip or 'no IPv4 address'} -> {new_ip}", flush=True)
    if old_ip == new_ip:
        print("This node already has that address.")
    elif args.apply:
        apply_change(args.node_id, new_ip, node)
    else:
        print("Read-only preview. Add --apply to make the change.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(f"Docker error: {error.stderr.strip() or error}", file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
