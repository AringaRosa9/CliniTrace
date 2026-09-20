"""Offline, consistent DB/object backup. Run with a dedicated administrator login.

The caller stops ingress, API, dispatcher, workers and lifecycle writers first.
DB share locks protect the snapshot against accidental writes while copying.
Only an EMPTY database and EMPTY private bucket can be restored.
"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db import s1, s2, s3, s4  # noqa: F401
from app.integrations.storage.s3 import Storage


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def pg_command(
    cfg: Settings,
    tool: str,
    args: list[str],
    container: str | None,
    output: Any = None,
    source: Any = None,
) -> None:
    url = make_url(cfg.database_url)
    env = dict(os.environ, PGPASSWORD=url.password or "")
    host, port = url.host or "localhost", str(url.port or 5432)
    prefix: list[str] = []
    if container:
        if cfg.app_env == "production" or host not in ("localhost", "127.0.0.1"):
            raise ValueError("Container shortcut is restricted to local synthetic drills")
        prefix = ["docker", "exec", "-i", "-e", "PGPASSWORD", container]
        host, port = "127.0.0.1", "5432"
    # Credentials are never part of argv, stdout or exception text.
    command = (
        prefix
        + [tool, "-h", host, "-p", port, "-U", url.username or "", "-d", url.database or ""]
        + args
    )
    result = subprocess.run(
        command, env=env, stdout=output, stdin=source, stderr=subprocess.PIPE, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{tool} failed; consult protected DBA diagnostics")


def inventory(storage: Storage) -> dict[str, dict[str, Any]]:
    return {
        obj["Key"]: {"size": obj["Size"], "etag": obj["ETag"]}
        for page in storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket)
        for obj in page.get("Contents", [])
    }


def table_fingerprints(engine: Engine) -> dict[str, dict[str, Any]]:
    """Hash complete rows including facts and immutable review snapshots; no PHI in report."""
    result = {}
    with engine.connect() as conn:
        for name in sorted(inspect(conn).get_table_names()):
            # Inspector-returned names are quoted, never interpolated from a manifest.
            quoted = conn.dialect.identifier_preparer.quote(name)
            rows = conn.execute(text(f"SELECT to_jsonb(t)::text FROM {quoted} AS t")).scalars()
            hashes = sorted(hashlib.sha256(row.encode()).hexdigest() for row in rows)
            result[name] = {
                "rows": len(hashes),
                "sha256": hashlib.sha256("".join(hashes).encode()).hexdigest(),
            }
    return result


def verify_references(cfg: Settings, engine: Engine, storage: Storage) -> int:
    count = 0
    with engine.connect() as conn:
        refs: list[tuple[str, str | None]] = []
        refs += list(
            conn.execute(select(s1.versions.c.object_key, s1.versions.c.sha256)).tuples().all()
        )
        refs += [(r, None) for r in conn.execute(select(s1.artifacts.c.object_key)).scalars()]
        refs += [(r, None) for r in conn.execute(select(s2.results.c.raw_object_key)).scalars()]
        refs += [
            (r, None)
            for r in conn.execute(
                select(s2.measurements.c.raw_object_key).where(
                    s2.measurements.c.raw_object_key.is_not(None)
                )
            ).scalars()
        ]
        refs += list(
            conn.execute(
                select(s3.export_files.c.object_key, s3.export_files.c.sha256)
                .join(s3.exports, s3.exports.c.id == s3.export_files.c.export_id)
                .where(s3.exports.c.expires_at > datetime.now(UTC))
            )
            .tuples()
            .all()
        )
        for key, expected in refs:
            data = storage.read(key)
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Referenced object hash mismatch")
            if key.startswith("parsed/") and key.endswith("/result.json"):
                # Page images are referenced inside the parse manifest, not separate DB rows.
                parsed = json.loads(data)
                for page in parsed.get("pages", []):
                    image = page.get("image")
                    if image:
                        storage.client.head_object(
                            Bucket=cfg.s3_bucket,
                            Key=key.rsplit("/", 1)[0] + f"/page-{page['page']}.png",
                        )
            count += 1
    return count


def backup(cfg: Settings, destination: Path, container: str | None = None) -> dict[str, Any]:
    if destination.exists():
        raise ValueError("Backup destination must be new")
    destination.mkdir(parents=True, mode=0o700)
    (destination / "objects").mkdir(mode=0o700)
    engine, storage = create_engine(cfg.database_url), Storage(cfg)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET LOCAL lock_timeout = '10s'"))
                names = inspect(conn).get_table_names()
                quoted = [conn.dialect.identifier_preparer.quote(name) for name in names]
                conn.execute(text("LOCK TABLE " + ",".join(quoted) + " IN SHARE MODE"))
                if conn.execute(select(s1.jobs.c.id).where(s1.jobs.c.status == "running")).first():
                    raise ValueError("Running jobs remain; stop writers and drain first")
                snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                before = inventory(storage)
                references = verify_references(cfg, engine, storage)
                tables = table_fingerprints(engine)
                with (destination / "database.dump").open("wb") as output:
                    pg_command(
                        cfg,
                        "pg_dump",
                        ["-Fc", "--no-owner", "--snapshot", snapshot],
                        container,
                        output=output,
                    )
                objects = []
                for index, (key, info) in enumerate(sorted(before.items())):
                    local = destination / "objects" / str(index)
                    storage.client.download_file(storage.bucket, key, str(local))
                    if local.stat().st_size != info["size"]:
                        raise ValueError("Object changed during backup")
                    objects.append(
                        {
                            "key": key,
                            "file": f"objects/{index}",
                            "sha256": sha(local),
                            "size": info["size"],
                        }
                    )
                if inventory(storage) != before:
                    raise ValueError("Object inventory changed; backup rejected")
                manifest = {
                    "format": 1,
                    "created_at": datetime.now(UTC).isoformat(),
                    "database_sha256": sha(destination / "database.dump"),
                    "tables": tables,
                    "objects": objects,
                    "references": references,
                }
                (destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
        return {
            "objects": len(objects),
            "tables": len(tables),
            "references": references,
            "manifest_sha256": sha(destination / "manifest.json"),
        }
    finally:
        engine.dispose()


def validate_bundle(source: Path, expected_manifest_sha256: str) -> dict[str, Any]:
    if sha(source / "manifest.json") != expected_manifest_sha256:
        raise ValueError("Backup manifest does not match the independently retained digest")
    manifest: dict[str, Any] = json.loads((source / "manifest.json").read_text())
    if manifest["format"] != 1 or sha(source / "database.dump") != manifest["database_sha256"]:
        raise ValueError("Invalid backup database checksum")
    keys: set[str] = set()
    for index, item in enumerate(manifest["objects"]):
        if item["file"] != f"objects/{index}" or item["key"] in keys:
            raise ValueError("Invalid backup object index")
        keys.add(item["key"])
        local = source / item["file"]
        if (
            local.is_symlink()
            or local.stat().st_size != item["size"]
            or sha(local) != item["sha256"]
        ):
            raise ValueError("Invalid backup object checksum")
    return manifest


def restore(
    cfg: Settings, source: Path, expected_manifest_sha256: str, container: str | None = None
) -> dict[str, Any]:
    manifest = validate_bundle(source, expected_manifest_sha256)
    engine, storage = create_engine(cfg.database_url), Storage(cfg)
    try:
        if inspect(engine).get_table_names() or inventory(storage):
            raise ValueError("Restore requires an empty isolated database and bucket")
        # Roles must be provisioned by the DBA; pg_dump does not contain cluster roles.
        for item in manifest["objects"]:
            with (source / item["file"]).open("rb") as stream:
                storage.put(item["key"], stream, "application/octet-stream")
        with (source / "database.dump").open("rb") as stream:
            pg_command(
                cfg,
                "pg_restore",
                ["--no-owner", "--exit-on-error", "--single-transaction"],
                container,
                source=stream,
                output=subprocess.DEVNULL,
            )
        if table_fingerprints(engine) != manifest["tables"]:
            raise ValueError("Restored database differs from backup")
        # Verify uploaded bytes too, including files not directly referenced by rows.
        for item in manifest["objects"]:
            if hashlib.sha256(storage.read(item["key"])).hexdigest() != item["sha256"]:
                raise ValueError("Restored object differs from backup")
        refs = verify_references(cfg, engine, storage)
        return {
            "tables": len(manifest["tables"]),
            "objects": len(manifest["objects"]),
            "references": refs,
            "consistent": True,
        }
    finally:
        engine.dispose()
