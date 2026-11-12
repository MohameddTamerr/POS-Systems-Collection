from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .auth import AuthorizationError, Permission, UserAccount, require_permission
from .config import SUPPORTED_PRODUCT_IMAGE_EXTS, validate_backup_frequency, validate_retention_count
from .db import CURRENT_SCHEMA_VERSION, Database
from .i18n import tr
from .paths import (
    get_backups_dir,
    get_default_receipts_dir,
    get_product_images_dir,
    get_receipts_dir,
    get_safety_backups_dir,
)

BACKUP_FORMAT_VERSION = 1
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024  # 512 MB zip-bomb safety cap
MAX_ARCHIVE_ENTRIES = 2048

_AUTO_BACKUP_RE = re.compile(
    r"^CafePOS_AutoBackup_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}(?:[_-][A-Za-z0-9-]+)?\.zip$"
)
_SAFE_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,190}$")
_SAFE_RECEIPT_PDF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,190}\.pdf$", re.IGNORECASE)


@dataclass(frozen=True)
class BackupInspectionResult:
    archive_path: Path
    format_version: int
    schema_version: int
    created_at: str
    backup_kind: str
    image_count: int
    order_count: int
    product_count: int
    user_count: int
    category_count: int = 0
    payment_count: int = 0
    receipt_pdf_count: int = 0


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(65536):
            h.update(chunk)
    return h.hexdigest()


def build_backup_filename(*, kind: str = "manual", when: datetime | None = None) -> str:
    local_dt = (when or datetime.now().astimezone())
    if kind == "auto":
        prefix = "CafePOS_AutoBackup"
        stamp = local_dt.strftime("%Y-%m-%d_%H-%M")
    elif kind == "safety":
        prefix = "CafePOS_Safety_Before_Restore"
        stamp = local_dt.strftime("%Y-%m-%d_%H-%M-%S")
    else:
        prefix = "CafePOS_Backup"
        stamp = local_dt.strftime("%Y-%m-%d_%H-%M")
    return f"{prefix}_{stamp}.zip"


def unique_backup_path(dest_dir: Path, filename: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    for idx in range(2, 100):
        alt = dest_dir / f"{stem}_{idx}{suffix}"
        if not alt.exists():
            return alt
    sec_tag = datetime.now().strftime("%S%f")[:6]
    return dest_dir / f"{stem}_{sec_tag}{suffix}"


def resolve_backup_destination(dest_str: str) -> Path:
    cleaned = (dest_str or "").strip()
    if cleaned:
        return Path(cleaned).expanduser()
    return get_backups_dir()


def _is_safe_archive_member(info: zipfile.ZipInfo) -> tuple[bool, str]:
    name = info.filename
    if not name or "\\" in name or name.startswith("/") or name.startswith(".."):
        return False, tr("backend.err.backup_unsafe_path", name=name)
    if ":" in name or "\x00" in name:
        return False, tr("backend.err.backup_unsafe_path", name=name)
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        return False, tr("backend.err.backup_unsafe_symlink", name=name)
    if info.is_dir():
        return name in {"product_images/", "receipts/"}, tr(
            "backend.err.backup_unsafe_dir", name=name
        )

    parts = name.split("/")
    if len(parts) == 1:
        if name in {"manifest.json", "cafe_pos.db"}:
            return True, ""
        return False, tr("backend.err.backup_unsafe_file", name=name)
    if len(parts) == 2 and parts[0] == "product_images":
        fn = parts[1]
        if not _SAFE_IMAGE_NAME_RE.match(fn) or Path(fn).suffix.lower() not in SUPPORTED_PRODUCT_IMAGE_EXTS:
            return False, tr("backend.err.backup_unsafe_img", name=name)
        return True, ""
    if len(parts) == 2 and parts[0] == "receipts":
        fn = parts[1]
        if not _SAFE_RECEIPT_PDF_RE.match(fn):
            return False, tr("backend.err.backup_unsafe_receipt", name=name)
        return True, ""
    return False, tr("backend.err.backup_unsafe_entry", name=name)


def _collect_managed_receipt_pdfs(cfg_export_dir: str) -> list[Path]:
    """Collect safe PDF receipt files from the default managed dir and optional custom export dir."""
    dirs_to_scan: list[Path] = [get_default_receipts_dir()]
    if (cfg_export_dir or "").strip():
        custom = get_receipts_dir(cfg_export_dir)
        if custom not in dirs_to_scan:
            dirs_to_scan.append(custom)

    seen_names: set[str] = set()
    files: list[Path] = []
    for directory in dirs_to_scan:
        if not directory.exists() or not directory.is_dir():
            continue
        for p in sorted(directory.iterdir()):
            if (
                p.is_file()
                and not p.is_symlink()
                and _SAFE_RECEIPT_PDF_RE.match(p.name)
                and p.name not in seen_names
            ):
                seen_names.add(p.name)
                files.append(p)
    return files


def create_backup_archive(
    db: Database,
    dest_zip: Path,
    *,
    backup_kind: str = "manual",
    actor: UserAccount | None = None,
    require_auth: bool = True,
) -> Path:
    """Create a transactionally consistent ZIP backup of SQLite data + product images."""
    effective_actor = actor if actor is not None else db.session_actor
    if require_auth:
        if effective_actor is not None or db.has_any_users():
            require_permission(
                effective_actor,
                Permission.MANAGE_BACKUPS,
                tr("backend.err.backup_create_unauth"),
            )

    dest = Path(dest_zip)
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    dest.parent.mkdir(parents=True, exist_ok=True)

    images_dir = get_product_images_dir()
    try:
        cfg = db.get_settings()
        export_dir_setting = cfg.receipt_export_dir
    except Exception:
        export_dir_setting = ""

    with tempfile.TemporaryDirectory(prefix="cafepos_bkp_stage_") as tmp:
        stage_dir = Path(tmp)
        staged_db = stage_dir / "cafe_pos.db"
        db.create_consistent_sqlite_snapshot(staged_db)
        db_sha256 = _sha256_file(staged_db)

        image_hashes: dict[str, str] = {}
        image_files: list[Path] = []
        if images_dir.exists():
            for p in sorted(images_dir.iterdir()):
                if (
                    p.is_file()
                    and not p.is_symlink()
                    and _SAFE_IMAGE_NAME_RE.match(p.name)
                    and p.suffix.lower() in SUPPORTED_PRODUCT_IMAGE_EXTS
                ):
                    image_hashes[p.name] = _sha256_file(p)
                    image_files.append(p)

        receipt_hashes: dict[str, str] = {}
        receipt_files = _collect_managed_receipt_pdfs(export_dir_setting)
        for rp in receipt_files:
            receipt_hashes[rp.name] = _sha256_file(rp)

        manifest = {
            "app": "CafePOS",
            "format_version": BACKUP_FORMAT_VERSION,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "backup_kind": backup_kind,
            "db_filename": "cafe_pos.db",
            "db_sha256": db_sha256,
            "image_count": len(image_files),
            "images": image_hashes,
            "receipt_pdf_count": len(receipt_files),
            "receipts": receipt_hashes,
        }

        tmp_zip = stage_dir / f"tmp_{dest.name}"
        with zipfile.ZipFile(tmp_zip, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            zf.write(staged_db, arcname="cafe_pos.db")
            for img_path in image_files:
                zf.write(img_path, arcname=f"product_images/{img_path.name}")
            for rec_path in receipt_files:
                zf.write(rec_path, arcname=f"receipts/{rec_path.name}")

        final_tmp = dest.parent / f".{dest.name}.tmp"
        shutil.copy2(tmp_zip, final_tmp)
        final_tmp.replace(dest)

    audit_action = "BACKUP_AUTO_CREATED" if backup_kind == "auto" else "BACKUP_MANUAL_CREATED"
    if backup_kind != "safety":
        db.record_audit_event(
            audit_action,
            actor=effective_actor,
            target_type="backup",
            target_id=dest.name,
            details=(
                f"Created {backup_kind} ZIP backup "
                f"({len(image_files)} images, {len(receipt_files)} receipts)"
            ),
        )
    return dest


def inspect_and_validate_backup(archive_path: Path) -> BackupInspectionResult:
    """Validate ZIP structure, path safety, checksums, schema compatibility, and SQLite integrity."""
    src = Path(archive_path)
    if not src.exists() or not src.is_file():
        raise ValueError(tr("backend.err.backup_not_found"))
    if src.stat().st_size < 128:
        raise ValueError(tr("backend.err.backup_too_small"))

    try:
        zf = zipfile.ZipFile(src, mode="r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError(tr("backend.err.backup_not_zip")) from exc

    with zf, tempfile.TemporaryDirectory(prefix="cafepos_inspect_") as tmp:
        infos = zf.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(tr("backend.err.backup_too_many_entries"))

        total_uncompressed = 0
        member_names: set[str] = set()
        for info in infos:
            ok, reason = _is_safe_archive_member(info)
            if not ok:
                raise ValueError(reason)
            total_uncompressed += max(0, int(info.file_size))
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise ValueError(tr("backend.err.backup_too_large"))
            if not info.is_dir():
                member_names.add(info.filename)

        if "manifest.json" not in member_names or "cafe_pos.db" not in member_names:
            raise ValueError(tr("backend.err.backup_missing_required"))

        try:
            manifest_raw = zf.read("manifest.json").decode("utf-8")
            manifest = json.loads(manifest_raw)
        except Exception as exc:
            raise ValueError(tr("backend.err.backup_bad_manifest")) from exc

        if not isinstance(manifest, dict) or manifest.get("app") != "CafePOS":
            raise ValueError(tr("backend.err.backup_not_cafe"))

        fmt_ver = manifest.get("format_version")
        if fmt_ver != BACKUP_FORMAT_VERSION:
            raise ValueError(tr("backend.err.backup_bad_fmt", fmt=fmt_ver))

        schema_ver = manifest.get("schema_version")
        if not isinstance(schema_ver, int) or schema_ver < 1 or schema_ver > CURRENT_SCHEMA_VERSION:
            raise ValueError(
                tr(
                    "backend.err.backup_bad_schema",
                    ver=schema_ver,
                    max_ver=CURRENT_SCHEMA_VERSION,
                )
            )

        stage_dir = Path(tmp)
        staged_db = stage_dir / "cafe_pos.db"
        with zf.open("cafe_pos.db") as zf_in, staged_db.open("wb") as out_f:
            shutil.copyfileobj(zf_in, out_f)

        expected_db_sha = str(manifest.get("db_sha256") or "").strip().lower()
        actual_db_sha = _sha256_file(staged_db).lower()
        if not expected_db_sha or actual_db_sha != expected_db_sha:
            raise ValueError(tr("backend.err.backup_db_sha"))

        expected_images = manifest.get("images")
        if not isinstance(expected_images, dict):
            expected_images = {}

        img_entries = [m for m in member_names if m.startswith("product_images/")]
        for entry in img_entries:
            fn = entry.split("/", 1)[1]
            staged_img = stage_dir / f"img_{fn}"
            with zf.open(entry) as zin, staged_img.open("wb") as fout:
                shutil.copyfileobj(zin, fout)
            if fn in expected_images:
                if _sha256_file(staged_img).lower() != str(expected_images[fn]).lower():
                    raise ValueError(tr("backend.err.backup_img_sha", name=fn))

        expected_receipts = manifest.get("receipts")
        if not isinstance(expected_receipts, dict):
            expected_receipts = {}

        receipt_entries = [m for m in member_names if m.startswith("receipts/")]
        for entry in receipt_entries:
            fn = entry.split("/", 1)[1]
            staged_rec = stage_dir / f"rcp_{fn}"
            with zf.open(entry) as zin, staged_rec.open("wb") as fout:
                shutil.copyfileobj(zin, fout)
            if fn in expected_receipts:
                if _sha256_file(staged_rec).lower() != str(expected_receipts[fn]).lower():
                    raise ValueError(tr("backend.err.backup_receipt_sha", name=fn))

        db_schema_ver = Database.validate_backup_file(staged_db)
        if db_schema_ver > CURRENT_SCHEMA_VERSION:
            raise ValueError(tr("backend.err.backup_newer_schema"))

        conn = sqlite3.connect(f"file:{staged_db}?mode=ro", uri=True)
        try:
            order_count = int(conn.execute("SELECT COUNT(*) FROM orders;").fetchone()[0])
            product_count = int(conn.execute("SELECT COUNT(*) FROM products;").fetchone()[0])
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table';"
                ).fetchall()
            }
            user_count = (
                int(conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0])
                if "users" in tables
                else 0
            )
            category_count = (
                int(conn.execute("SELECT COUNT(*) FROM categories;").fetchone()[0])
                if "categories" in tables
                else 0
            )
            payment_count = (
                int(conn.execute("SELECT COUNT(*) FROM payments;").fetchone()[0])
                if "payments" in tables
                else 0
            )
        finally:
            conn.close()

        return BackupInspectionResult(
            archive_path=src,
            format_version=fmt_ver,
            schema_version=schema_ver,
            created_at=str(manifest.get("created_at") or ""),
            backup_kind=str(manifest.get("backup_kind") or "manual"),
            image_count=len(img_entries),
            order_count=order_count,
            product_count=product_count,
            user_count=user_count,
            category_count=category_count,
            payment_count=payment_count,
            receipt_pdf_count=len(receipt_entries),
        )


def restore_backup_archive(
    db: Database,
    archive_path: Path,
    *,
    actor: UserAccount | None = None,
    admin_password: str | None = None,
    verify_admin_password: bool = False,
) -> BackupInspectionResult:
    """Safely verify, pre-backup, restore SQLite + product images with rollback on failure."""
    effective_actor = actor if actor is not None else db.session_actor
    if effective_actor is not None or db.has_any_users():
        verified_admin = require_permission(
            effective_actor,
            Permission.RESTORE_BACKUP,
            tr("backend.err.backup_unauth"),
        )
        if verify_admin_password:
            if not db.verify_user_password(verified_admin.id, admin_password or ""):
                raise AuthorizationError(tr("backend.err.backup_verify_pw"))
    else:
        verified_admin = None

    src = Path(archive_path)
    if src.suffix.lower() == ".db":
        # Legacy raw .db restore fallback with full safety backup + rollback
        Database.validate_backup_file(src)
        safety_path = unique_backup_path(
            get_safety_backups_dir(), build_backup_filename(kind="safety")
        )
        create_backup_archive(db, safety_path, backup_kind="safety", actor=verified_admin, require_auth=False)
        try:
            db.restore_from_file(src, actor=verified_admin)
            db.set_session_actor(None)
            db.record_audit_event(
                "BACKUP_RESTORED",
                actor=None,
                actor_username_override=(verified_admin.username if verified_admin else ""),
                actor_role_override=(verified_admin.role if verified_admin else ""),
                target_type="backup",
                target_id=src.name,
                details="Restored legacy SQLite .db backup safely",
            )
        except Exception:
            db.set_session_actor(None)
            raise
        return BackupInspectionResult(
            archive_path=src,
            format_version=0,
            schema_version=CURRENT_SCHEMA_VERSION,
            created_at="",
            backup_kind="legacy_db",
            image_count=0,
            order_count=db.get_order_count(),
            product_count=len(db.list_products()),
            user_count=0,
        )

    inspection = inspect_and_validate_backup(src)

    # Step 7: Create safety backup before modifying live data
    safety_path = unique_backup_path(
        get_safety_backups_dir(), build_backup_filename(kind="safety")
    )
    create_backup_archive(
        db, safety_path, backup_kind="safety", actor=verified_admin, require_auth=False
    )

    images_dir = get_product_images_dir()
    default_receipts_dir = get_default_receipts_dir()
    with tempfile.TemporaryDirectory(prefix="cafepos_restore_") as tmp:
        stage_dir = Path(tmp)
        extract_dir = stage_dir / "extracted"
        rollback_dir = stage_dir / "rollback"
        extract_dir.mkdir(parents=True, exist_ok=True)
        rollback_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(src, mode="r") as zf:
            for info in zf.infolist():
                ok, reason = _is_safe_archive_member(info)
                if not ok:
                    raise ValueError(reason)
                if info.is_dir():
                    continue
                if info.filename == "cafe_pos.db":
                    with zf.open(info) as zin, (extract_dir / "cafe_pos.db").open("wb") as fout:
                        shutil.copyfileobj(zin, fout)
                elif info.filename.startswith("product_images/"):
                    fn = info.filename.split("/", 1)[1]
                    img_stage = extract_dir / "product_images"
                    img_stage.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as zin, (img_stage / fn).open("wb") as fout:
                        shutil.copyfileobj(zin, fout)
                elif info.filename.startswith("receipts/"):
                    fn = info.filename.split("/", 1)[1]
                    rec_stage = extract_dir / "receipts"
                    rec_stage.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as zin, (rec_stage / fn).open("wb") as fout:
                        shutil.copyfileobj(zin, fout)

        staged_new_db = extract_dir / "cafe_pos.db"
        staged_new_images = extract_dir / "product_images"
        staged_new_receipts = extract_dir / "receipts"

        # Step 8: Release active connections & snapshot rollback state
        db.close()
        rollback_db = rollback_dir / "prev_cafe_pos.db"
        if db.db_path.exists():
            shutil.copy2(db.db_path, rollback_db)

        rollback_images = rollback_dir / "prev_images"
        rollback_images.mkdir(parents=True, exist_ok=True)
        if images_dir.exists():
            for existing in images_dir.iterdir():
                if existing.is_file():
                    shutil.copy2(existing, rollback_images / existing.name)

        rollback_receipts = rollback_dir / "prev_receipts"
        rollback_receipts.mkdir(parents=True, exist_ok=True)
        if default_receipts_dir.exists():
            for existing in default_receipts_dir.iterdir():
                if existing.is_file() and _SAFE_RECEIPT_PDF_RE.match(existing.name):
                    shutil.copy2(existing, rollback_receipts / existing.name)

        # Step 9: Swap DB via SQLite backup API + atomic images & receipts replacement
        try:
            src_conn = sqlite3.connect(f"file:{staged_new_db}?mode=ro", uri=True)
            dst_conn = sqlite3.connect(str(db.db_path), timeout=10.0)
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()

            db._init_schema()

            if images_dir.exists():
                for old_file in list(images_dir.iterdir()):
                    if old_file.is_file():
                        old_file.unlink()
            images_dir.mkdir(parents=True, exist_ok=True)
            if staged_new_images.exists():
                for new_img in staged_new_images.iterdir():
                    if new_img.is_file():
                        shutil.copy2(new_img, images_dir / new_img.name)

            if default_receipts_dir.exists():
                for old_rec in list(default_receipts_dir.iterdir()):
                    if old_rec.is_file() and _SAFE_RECEIPT_PDF_RE.match(old_rec.name):
                        old_rec.unlink()
            default_receipts_dir.mkdir(parents=True, exist_ok=True)
            if staged_new_receipts.exists():
                for new_rec in staged_new_receipts.iterdir():
                    if new_rec.is_file():
                        shutil.copy2(new_rec, default_receipts_dir / new_rec.name)

        except Exception as exc:
            # Recover previous working DB, images, and receipts if anything failed
            db.close()
            if rollback_db.exists():
                shutil.copy2(rollback_db, db.db_path)
            if images_dir.exists():
                for bad_file in list(images_dir.iterdir()):
                    if bad_file.is_file():
                        bad_file.unlink()
            images_dir.mkdir(parents=True, exist_ok=True)
            if rollback_images.exists():
                for prev_img in rollback_images.iterdir():
                    if prev_img.is_file():
                        shutil.copy2(prev_img, images_dir / prev_img.name)
            if default_receipts_dir.exists():
                for bad_rec in list(default_receipts_dir.iterdir()):
                    if bad_rec.is_file() and _SAFE_RECEIPT_PDF_RE.match(bad_rec.name):
                        bad_rec.unlink()
            default_receipts_dir.mkdir(parents=True, exist_ok=True)
            if rollback_receipts.exists():
                for prev_rec in rollback_receipts.iterdir():
                    if prev_rec.is_file():
                        shutil.copy2(prev_rec, default_receipts_dir / prev_rec.name)
            db._init_schema()
            try:
                db.record_audit_event(
                    "BACKUP_RESTORE_FAILED",
                    actor=verified_admin,
                    target_type="backup",
                    target_id=src.name,
                    details=f"Restore rolled back after error: {exc}",
                )
            except Exception:
                pass
            raise ValueError(tr("backend.err.backup_rollback", exc=exc)) from exc

    # Step 10: Clear session so users must authenticate again after restoration
    db.set_session_actor(None)
    db.record_audit_event(
        "BACKUP_RESTORED",
        actor=None,
        actor_username_override=(verified_admin.username if verified_admin else ""),
        actor_role_override=(verified_admin.role if verified_admin else ""),
        target_type="backup",
        target_id=src.name,
        details=(
            f"Restored ZIP archive ({inspection.order_count} orders, "
            f"{inspection.image_count} images, {inspection.receipt_pdf_count} receipts)"
        ),
    )
    return inspection


# ---------------- Scheduled Automatic Backups & Retention ----------------


def is_auto_backup_due(
    *,
    backup_enabled: bool,
    frequency: str,
    last_auto_backup_at: str,
    now_utc: datetime | None = None,
) -> bool:
    if not backup_enabled:
        return False
    if not last_auto_backup_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_auto_backup_at)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        last_dt = last_dt.astimezone(timezone.utc)
    except ValueError:
        return True

    current = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    freq = validate_backup_frequency(frequency)
    threshold = timedelta(days=7) if freq == "weekly" else timedelta(days=1)
    return (current - last_dt) >= threshold


def prune_auto_backups(dest_dir: Path, retention_count: int) -> list[Path]:
    """Delete only older CafePOS_AutoBackup_*.zip files beyond retention count."""
    keep = validate_retention_count(retention_count)
    if not dest_dir.exists() or not dest_dir.is_dir():
        return []

    matching: list[Path] = []
    for entry in dest_dir.iterdir():
        if entry.is_file() and not entry.is_symlink() and _AUTO_BACKUP_RE.match(entry.name):
            matching.append(entry)

    matching.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    deleted: list[Path] = []
    for old_path in matching[keep:]:
        try:
            old_path.unlink()
            deleted.append(old_path)
        except OSError:
            pass
    return deleted


def run_due_auto_backup(db: Database, *, now_utc: datetime | None = None) -> Path | None:
    """Run scheduled automatic backup if enabled and due; prunes only AutoBackup archives."""
    cfg = db.get_settings()
    current = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not is_auto_backup_due(
        backup_enabled=cfg.backup_enabled,
        frequency=cfg.backup_frequency,
        last_auto_backup_at=cfg.last_auto_backup_at,
        now_utc=current,
    ):
        return None

    dest_dir = resolve_backup_destination(cfg.backup_destination)
    target = unique_backup_path(
        dest_dir, build_backup_filename(kind="auto", when=current.astimezone())
    )
    created = create_backup_archive(
        db,
        target,
        backup_kind="auto",
        actor=None,
        require_auth=False,
    )
    db.mark_auto_backup_completed(current.isoformat(timespec="seconds"))
    prune_auto_backups(dest_dir, cfg.backup_retention_count)
    return created
