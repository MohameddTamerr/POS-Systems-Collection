import io
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from cafe_pos.auth import (
    AuthenticationError,
    AuthorizationError,
    ROLE_ADMIN,
    ROLE_CASHIER,
    verify_password,
)
from cafe_pos.backups import (
    build_backup_filename,
    create_backup_archive,
    inspect_and_validate_backup,
    is_auto_backup_due,
    prune_auto_backups,
    restore_backup_archive,
    run_due_auto_backup,
)
from cafe_pos.config import BusinessConfig
from cafe_pos.db import CartLine, Database
from cafe_pos.paths import (
    get_backups_dir,
    get_database_path,
    get_product_images_dir,
    get_safety_backups_dir,
)
from cafe_pos.receipts import build_receipt_plain_text
from cafe_pos.ui.main_window import MainWindow
from cafe_pos.ui.payment_dialog import CheckoutPaymentDialog
from cafe_pos.ui.setup_dialog import SetupDialog


def _sample_png_bytes(color=(0, 91, 55)) -> bytes:
    img = Image.new("RGB", (64, 64), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_owner_and_cashiers(db: Database):
    _, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(
            business_name="Green Grounds Café",
            accent_color="#005B37",
            currency="USD",
        ),
        admin_username="owner",
        admin_password="OwnerPassword!2026",
        admin_display_name="Adam Owner",
    )
    db.set_session_actor(admin)
    c1 = db.create_user(
        "samantha",
        "CashierOne!2026",
        ROLE_CASHIER,
        display_name="Samantha W",
    )
    c2 = db.create_user(
        "karim",
        "CashierTwo!2026",
        ROLE_CASHIER,
        display_name="Karim M",
    )
    db.set_session_actor(None)
    return admin, c1, c2


# =====================================================================
# 1. INITIAL ADMIN WIZARD & AUTHENTICATION SECURITY
# =====================================================================


def test_first_launch_admin_wizard_cannot_be_bypassed(qapp, isolated_data_dir: Path, monkeypatch):
    db = Database(get_database_path())
    assert db.has_admin() is False

    # SetupDialog with require_admin_setup=True must reject on Skip/Exit click
    dlg = SetupDialog(db.get_settings(), is_first_launch=True, require_admin_setup=True)
    dlg._on_skip_or_cancel()
    assert dlg.result() == QDialog.Rejected
    assert dlg.result_config is None
    assert db.has_admin() is False

    # MainWindow must refuse workspace entry when setup wizard is cancelled
    window = MainWindow(db, qapp)
    monkeypatch.setattr(
        "cafe_pos.ui.main_window.SetupDialog.exec",
        lambda self: QDialog.Rejected,
    )
    ready = window.ensure_initial_admin_ready()
    assert ready is False
    assert window.is_authenticated is False
    assert window.root_stack.currentIndex() == 0
    window.close()


def test_argon2id_hashing_salting_and_no_hash_exposure(tmp_path: Path):
    db_path = tmp_path / "hash_test.db"
    db = Database(db_path)
    admin, c1, _c2 = _create_owner_and_cashiers(db)

    # Give cashier a matching second user with same password to check per-user salts
    db.set_session_actor(admin)
    c_dup = db.create_user("twin", "CashierOne!2026", ROLE_CASHIER, display_name="Twin Cashier")

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, username, password_hash FROM users;").fetchall()
    by_name = {r[1]: r[2] for r in rows}

    assert by_name["samantha"].startswith("$argon2id$")
    assert by_name["twin"].startswith("$argon2id$")
    assert by_name["samantha"] != by_name["twin"]
    assert verify_password(by_name["samantha"], "CashierOne!2026") is True

    # UserAccount objects never expose hashes
    listed = db.list_users(actor=admin)
    for account in listed:
        assert not hasattr(account, "password_hash")
        assert "argon2" not in repr(account)

    assert admin.id != c1.id and c1.id != c_dup.id


def test_login_throttling_disabled_user_and_dummy_verify(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "throttle.db")
    admin, c1, _c2 = _create_owner_and_cashiers(db)

    # Valid sign-in (case-insensitive username)
    signed = db.authenticate("SAMANTHA", "CashierOne!2026")
    assert signed.id == c1.id
    assert signed.last_login_at != ""

    # Wrong passwords 5 times lock account temporarily
    base_now = datetime(2026, 11, 11, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(4):
        with pytest.raises(AuthenticationError) as exc_info:
            db.authenticate("samantha", "WrongPass!", now=base_now + timedelta(seconds=i))
        assert exc_info.value.locked_seconds == 0

    # 5th bad attempt engages 30-second lockout
    with pytest.raises(AuthenticationError) as exc_lock:
        db.authenticate("samantha", "WrongPass!", now=base_now + timedelta(seconds=5))
    assert exc_lock.value.locked_seconds == 30

    # Even the CORRECT password must be rejected during lockout
    with pytest.raises(AuthenticationError) as exc_still_locked:
        db.authenticate("samantha", "CashierOne!2026", now=base_now + timedelta(seconds=10))
    assert exc_still_locked.value.locked_seconds > 0

    # After cooldown expires, correct password works and resets counter
    ok_after = db.authenticate("samantha", "CashierOne!2026", now=base_now + timedelta(seconds=40))
    assert ok_after.username == "samantha"

    # Unknown username invokes dummy verification (constant-work defense) & throttles after 5 failures
    dummy_calls = {"count": 0}
    import cafe_pos.db as db_mod

    orig_dummy = db_mod.dummy_verify

    def spy_dummy(pw: str) -> None:
        dummy_calls["count"] += 1
        orig_dummy(pw)

    monkeypatch.setattr(db_mod, "dummy_verify", spy_dummy)
    for i in range(5):
        with pytest.raises(AuthenticationError) as u_exc:
            db.authenticate("nobody_user", "GuessPass!", now=base_now + timedelta(seconds=i))
    assert dummy_calls["count"] == 5
    assert u_exc.value.locked_seconds == 30

    # Disabled account rejected even with right password
    db.set_session_actor(admin)
    db.set_user_active(c1.id, False, actor=admin)
    with pytest.raises(AuthenticationError, match="disabled"):
        db.authenticate("samantha", "CashierOne!2026", now=base_now + timedelta(minutes=2))


# =====================================================================
# 2. LOGIN UI, RBAC, USER CRUD, SESSION LOCK & CASHIER SCOPING
# =====================================================================


def test_login_view_and_workspace_visibility_rbac(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    _admin, _c1, _c2 = _create_owner_and_cashiers(db)

    window = MainWindow(db, qapp)
    window.show()
    qapp.processEvents()

    # Must boot on Login gate with navigation/workspace hidden
    assert window.is_authenticated is False
    assert window.root_stack.currentIndex() == 0
    assert window.workspace_widget.isVisible() is False

    # Password show/hide toggle works
    lv = window.login_view
    assert lv.password_edit.echoMode() == QLineEdit.Password
    lv.toggle_pw_btn.click()
    assert lv.password_edit.echoMode() == QLineEdit.Normal
    lv.toggle_pw_btn.click()
    assert lv.password_edit.echoMode() == QLineEdit.Password

    # Invalid credentials display error on Login screen
    lv.username_edit.setText("samantha")
    lv.password_edit.setText("BadPassword!")
    lv.sign_in_btn.click()
    assert window.is_authenticated is False
    assert lv.error_lbl.isVisible() is True

    # Cashier login exposes ONLY Register (0) & Sales History (2)
    lv.password_edit.setText("CashierOne!2026")
    lv.sign_in_btn.click()
    assert window.is_authenticated is True
    assert window.root_stack.currentIndex() == 1
    assert window.nav_buttons[0].isVisibleTo(window.workspace_widget) is True   # Register
    assert window.nav_buttons[1].isVisibleTo(window.workspace_widget) is False  # Products
    assert window.nav_buttons[2].isVisibleTo(window.workspace_widget) is True   # Sales History
    assert window.nav_buttons[3].isVisibleTo(window.workspace_widget) is False  # Reports
    assert window.nav_buttons[4].isVisibleTo(window.workspace_widget) is False  # Users
    assert window.nav_buttons[5].isVisibleTo(window.workspace_widget) is False  # Settings

    # Attempting to programmatically switch to restricted tabs is blocked
    window.switch_view(1)
    assert window.stack.currentIndex() == 0
    window.switch_view(4)
    assert window.stack.currentIndex() == 0
    window.switch_view(5)
    assert window.stack.currentIndex() == 0

    # Logout & sign in as Admin -> all 6 tabs visible
    window.logout(reason="manual")
    assert window.root_stack.currentIndex() == 0
    lv.username_edit.setText("owner")
    lv.password_edit.setText("OwnerPassword!2026")
    lv.sign_in_btn.click()
    assert window.current_user.is_admin is True
    assert all(btn.isVisibleTo(window.workspace_widget) for btn in window.nav_buttons)
    window.close()


def test_service_layer_rbac_and_user_management_protections(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    admin, c1, c2 = _create_owner_and_cashiers(db)
    prod = db.list_products()[0]

    # Unauthenticated calls blocked once users exist
    with pytest.raises(AuthorizationError):
        db.add_product("Blocked", "Espresso", 400)
    with pytest.raises(AuthorizationError):
        db.list_users()

    # Cashier blocked from admin actions at database/service level
    with pytest.raises(AuthorizationError):
        db.create_user("hacker", "SecretPass!123", ROLE_ADMIN, actor=c1)
    with pytest.raises(AuthorizationError):
        db.update_user(admin.id, role=ROLE_CASHIER, actor=c1)
    with pytest.raises(AuthorizationError):
        db.reset_user_password(admin.id, "Hijacked!123", actor=c1)
    with pytest.raises(AuthorizationError):
        db.set_user_active(c2.id, False, actor=c1)
    with pytest.raises(AuthorizationError):
        db.list_users(actor=c1)
    with pytest.raises(AuthorizationError):
        db.list_audit_logs(actor=c1)
    with pytest.raises(AuthorizationError):
        db.add_product("Bypass", "Espresso", 400, actor=c1)
    with pytest.raises(AuthorizationError):
        db.save_settings(db.get_settings(), actor=c1)
    with pytest.raises(AuthorizationError):
        db.backup_to_file(isolated_data_dir / "nope.db", actor=c1)
    with pytest.raises(AuthorizationError):
        create_backup_archive(db, isolated_data_dir / "nope.zip", actor=c1)
    with pytest.raises(AuthorizationError):
        db.factory_reset(actor=c1)

    # Admin cannot disable self or demote/disable last active admin
    with pytest.raises(ValueError, match="own"):
        db.set_user_active(admin.id, False, actor=admin)
    with pytest.raises(ValueError, match="last active administrator"):
        db.update_user(admin.id, role=ROLE_CASHIER, actor=admin)

    # Password reset works & edits/audit logs work in UsersView
    db.reset_user_password(c2.id, "BrandNewPass#99", actor=admin)
    with pytest.raises(AuthenticationError):
        db.authenticate("karim", "CashierTwo!2026")
    assert db.authenticate("karim", "BrandNewPass#99").id == c2.id

    # Cashier order attribution & cross-cashier isolation
    o1 = db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=prod.price_minor, quantity=1)],
        1000,
        actor=c1,
    )
    o2 = db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=prod.price_minor, quantity=2)],
        1500,
        actor=c2,
    )

    c1_orders = db.list_orders(actor=c1)
    assert [o.order_number for o in c1_orders] == [o1.order_number]
    with pytest.raises(AuthorizationError):
        db.list_orders(cashier_user_id=c2.id, actor=c1)
    with pytest.raises(AuthorizationError):
        db.get_order(o2.id, actor=c1)

    admin_all = db.list_orders(actor=admin)
    assert len(admin_all) == 2
    assert [o.order_number for o in db.list_orders(cashier_user_id=c2.id, actor=admin)] == [
        o2.order_number
    ]

    # UsersView shows accounts and audit log rows in UI
    window = MainWindow(db, qapp)
    try:
        window.login_user(admin)
        window.switch_view(4)
        assert window.users_view.table.rowCount() == 3
        window.users_view._switch_subtab("audit")
        assert window.users_view.audit_table.rowCount() >= 3
    finally:
        window.close()


def test_cashier_order_attribution_sales_scoping_and_receipts(qapp, isolated_data_dir: Path):
    db = Database(get_database_path())
    admin, c1, c2 = _create_owner_and_cashiers(db)
    prod = db.list_products()[0]

    o1 = db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=prod.price_minor, quantity=1)],
        1000,
        actor=c1,
    )
    o2 = db.create_order(
        [CartLine(product_id=prod.id, name=prod.name, unit_price_minor=prod.price_minor, quantity=1)],
        1000,
        actor=c2,
    )

    window = MainWindow(db, qapp)
    try:
        # Cashier 1 sees ONLY own sales + My Sales badge, no Cashier filter
        window.login_user(c1)
        window.switch_view(2)
        sv = window.sales_view
        assert sv.scope_badge.isVisibleTo(sv) is True
        assert sv.cashier_filter.isVisibleTo(sv) is False
        assert sv.table.rowCount() == 1
        assert sv.table.item(0, 0).text() == o1.order_number
        assert sv.table.item(0, 2).text() == "Samantha W"

        # Admin sees all sales + Cashier filter dropdown
        window.logout(reason="manual")
        window.login_user(admin)
        window.switch_view(2)
        assert sv.scope_badge.isVisibleTo(sv) is False
        assert sv.cashier_filter.isVisibleTo(sv) is True
        assert sv.table.rowCount() == 2

        idx_c2 = sv.cashier_filter.findData(f"user:{c2.id}")
        assert idx_c2 >= 0
        sv.cashier_filter.setCurrentIndex(idx_c2)
        assert sv.table.rowCount() == 1
        assert sv.table.item(0, 0).text() == o2.order_number
        assert sv.table.item(0, 2).text() == "Karim M"

        # Disabling Cashier 1 preserves Cashier snapshot on historic order & receipt
        db.set_user_active(c1.id, False, actor=admin)
        hist = db.get_order(o1.id, actor=admin)
        assert hist is not None
        assert hist.cashier_name_snapshot == "Samantha W"
        txt = build_receipt_plain_text(hist)
        assert "Cashier:" in txt and "Samantha W" in txt
    finally:
        window.close()


def test_logout_inactivity_auto_lock_clears_transient_state(
    qapp, isolated_data_dir: Path, monkeypatch
):
    db = Database(get_database_path())
    admin, c1, _c2 = _create_owner_and_cashiers(db)

    # Configure 1-minute auto-lock
    cfg = db.get_settings()
    cfg.auto_lock_minutes = 1
    db.save_settings(cfg, actor=admin)

    window = MainWindow(db, qapp)
    window.login_user(c1)

    # Complete 1 order for c1 + stage an unfinished second cart with search input
    monkeypatch.setattr("cafe_pos.ui.main_window.ReceiptDialog.exec", lambda self: 1)
    latte = db.list_products()[0]
    window.register_view.add_product_to_cart(latte)

    def _confirm_payment(attempt: int = 0) -> None:
        dlg = qapp.activeModalWidget()
        if isinstance(dlg, CheckoutPaymentDialog):
            dlg.cash_edit.setText("20.00")
            if dlg.confirm_btn.isEnabled():
                dlg.confirm_btn.click()
            else:
                dlg.reject()
        elif attempt < 30:
            QTimer.singleShot(10, lambda: _confirm_payment(attempt + 1))

    QTimer.singleShot(0, _confirm_payment)
    window.register_view.trigger_checkout()

    window.register_view.add_product_to_cart(latte)
    window.register_view.search_input.setText("Latte")

    # Open an active modal child dialog to ensure auto-lock closes child dialogs cleanly
    child_dlg = QDialog(window)
    child_dlg.show()
    assert child_dlg.isVisible() is True

    # Trigger inactivity timeout past 60s
    locked = window.trigger_inactivity_check(simulated_idle_seconds=65.0)
    assert locked is True
    assert window.is_authenticated is False
    assert window.root_stack.currentIndex() == 0
    assert db.session_actor is None
    assert child_dlg.isVisible() is False

    # Transient UI state from prior session must be wiped cleanly
    assert len(window.register_view.cart) == 0
    assert window.register_view.search_input.text() == ""
    assert window.sales_view.table.rowCount() == 0
    assert "inactivity" in window.login_view.notice_lbl.text().lower()

    # Sign in as Cashier 2 -> sees ONLY 0 own orders, not Cashier 1's order
    window.login_user(db.authenticate("karim", "CashierTwo!2026"))
    window.switch_view(2)
    assert window.sales_view.table.rowCount() == 0
    assert window.sales_view.scope_badge.isVisibleTo(window.sales_view) is True

    window.close()


# =====================================================================
# 3. ZIP BACKUP, SAFE RESTORE, SCHEDULED AUTO-BACKUP & LEGACY UPGRADE
# =====================================================================


def test_zip_backup_inspect_restore_tamper_rejection_and_rollback(
    qapp, isolated_data_dir: Path, tmp_path: Path
):
    db = Database(get_database_path())
    admin, c1, _c2 = _create_owner_and_cashiers(db)

    # Add a product with a real cached image in product_images/ + a completed order
    img_dir = get_product_images_dir()
    img_dir.mkdir(parents=True, exist_ok=True)
    img_name = "prod_cardamom.png"
    img_path = img_dir / img_name
    img_bytes = _sample_png_bytes((40, 110, 75))
    img_path.write_bytes(img_bytes)

    cardamom = db.add_product(
        "Cardamom Latte",
        "Specialty",
        540,
        image_filename=img_name,
        image_source="local",
        actor=admin,
    )
    order1 = db.create_order(
        [CartLine(product_id=cardamom.id, name=cardamom.name, unit_price_minor=540, quantity=2)],
        1500,
        actor=c1,
    )

    # Create manual backup ZIP
    bkp_name = build_backup_filename(kind="manual")
    assert bkp_name.startswith("CafePOS_Backup_")
    bkp_zip = get_backups_dir() / bkp_name
    out_path = create_backup_archive(db, bkp_zip, backup_kind="manual", actor=admin)
    assert out_path.exists()

    # Validate ZIP contents & manifest SHA-256
    with zipfile.ZipFile(out_path, "r") as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "cafe_pos.db" in names
        assert f"product_images/{img_name}" in names
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        assert manifest["app"] == "CafePOS"
        assert manifest["image_count"] == 1
        assert img_name in manifest["images"]

    info = inspect_and_validate_backup(out_path)
    assert info.order_count == 1
    assert info.image_count == 1
    assert info.user_count == 3

    # Corrupt archives must be rejected without touching live DB
    bad_nonzip = tmp_path / "not_a_zip.zip"
    bad_nonzip.write_text("not zip bytes" * 30, encoding="utf-8")
    with pytest.raises(ValueError, match="ZIP"):
        inspect_and_validate_backup(bad_nonzip)

    # Tamper DB inside copy of ZIP -> SHA-256 checksum failure
    tampered_zip = tmp_path / "tampered_db.zip"
    with zipfile.ZipFile(out_path, "r") as zin, zipfile.ZipFile(tampered_zip, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "cafe_pos.db":
                data = data[:-4] + b"XXXX"
            zout.writestr(item, data)
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_and_validate_backup(tampered_zip)

    # Path traversal member rejected
    traversal_zip = tmp_path / "traversal.zip"
    with zipfile.ZipFile(out_path, "r") as zin, zipfile.ZipFile(traversal_zip, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("../escape.txt", b"evil")
    with pytest.raises(ValueError, match="Unsafe"):
        inspect_and_validate_backup(traversal_zip)

    # Wrong Admin password fails restore authorization before mutating state
    with pytest.raises(AuthorizationError):
        restore_backup_archive(
            db,
            out_path,
            actor=admin,
            admin_password="WrongPassword!",
            verify_admin_password=True,
        )

    # Mutate live store AFTER backup: add ORD-1002, remove image, remove product
    extra_prod = db.add_product("After Backup Pastry", "Bakery", 420, actor=admin)
    db.create_order(
        [CartLine(product_id=extra_prod.id, name=extra_prod.name, unit_price_minor=420, quantity=1)],
        500,
        actor=c1,
    )
    db.delete_product(cardamom.id, actor=admin)
    img_path.unlink()
    assert not img_path.exists()
    assert db.get_order_count() == 2

    # Perform valid restore via MainWindow + Settings handler
    window = MainWindow(db, qapp)
    window.login_user(admin)

    restore_info = restore_backup_archive(
        db,
        out_path,
        actor=admin,
        admin_password="OwnerPassword!2026",
        verify_admin_password=True,
    )
    assert restore_info.order_count == 1
    window._on_data_restored()

    # Safety backup created inside safety_backups/
    safety_files = list(get_safety_backups_dir().glob("CafePOS_Safety_Before_Restore_*.zip"))
    assert len(safety_files) == 1

    # Restored live state matches snapshot + image restored + session cleared + forced re-auth
    assert db.get_order_count() == 1
    assert db.list_orders(actor=admin)[0].order_number == order1.order_number
    assert img_path.exists() and img_path.read_bytes() == img_bytes
    assert len(db.list_products(search="Cardamom Latte")) == 1
    assert len(db.list_products(search="After Backup Pastry")) == 0

    assert db.session_actor is None
    assert window.is_authenticated is False
    assert window.root_stack.currentIndex() == 0
    assert "restored safely" in window.login_view.notice_lbl.text().lower()

    window.close()


def test_scheduled_auto_backup_due_and_safe_retention_pruning(
    isolated_data_dir: Path, tmp_path: Path
):
    db = Database(get_database_path())
    admin, _c1, _c2 = _create_owner_and_cashiers(db)

    now = datetime(2026, 11, 11, 14, 0, 0, tzinfo=timezone.utc)
    assert is_auto_backup_due(
        backup_enabled=False, frequency="daily", last_auto_backup_at="", now_utc=now
    ) is False
    assert is_auto_backup_due(
        backup_enabled=True, frequency="daily", last_auto_backup_at="", now_utc=now
    ) is True
    assert is_auto_backup_due(
        backup_enabled=True,
        frequency="daily",
        last_auto_backup_at=(now - timedelta(hours=12)).isoformat(),
        now_utc=now,
    ) is False
    assert is_auto_backup_due(
        backup_enabled=True,
        frequency="daily",
        last_auto_backup_at=(now - timedelta(hours=25)).isoformat(),
        now_utc=now,
    ) is True
    assert is_auto_backup_due(
        backup_enabled=True,
        frequency="weekly",
        last_auto_backup_at=(now - timedelta(days=5)).isoformat(),
        now_utc=now,
    ) is False
    assert is_auto_backup_due(
        backup_enabled=True,
        frequency="weekly",
        last_auto_backup_at=(now - timedelta(days=7, minutes=1)).isoformat(),
        now_utc=now,
    ) is True

    dest_dir = tmp_path / "auto_dest"
    cfg = db.get_settings()
    cfg.backup_enabled = True
    cfg.backup_frequency = "daily"
    cfg.backup_destination = str(dest_dir)
    cfg.backup_retention_count = 2
    db.save_settings(cfg, actor=admin)

    created = run_due_auto_backup(db, now_utc=now)
    assert created is not None
    assert created.parent == dest_dir
    assert created.name.startswith("CafePOS_AutoBackup_")
    assert db.get_settings().last_auto_backup_at != ""
    # Calling again immediately does NOT create a duplicate backup
    assert run_due_auto_backup(db, now_utc=now + timedelta(minutes=5)) is None

    # Create additional AutoBackup files + Manual backup + unrelated file to test safe pruning
    import os
    import time

    base_mtime = time.time() - 1000
    auto_names = [
        "CafePOS_AutoBackup_2026-11-01_09-00.zip",
        "CafePOS_AutoBackup_2026-11-02_09-00.zip",
        "CafePOS_AutoBackup_2026-11-03_09-00.zip",
    ]
    for idx, fn in enumerate(auto_names):
        p = dest_dir / fn
        p.write_bytes(b"auto")
        os.utime(p, (base_mtime + idx * 10, base_mtime + idx * 10))

    manual_keep = dest_dir / "CafePOS_Backup_2026-11-01_08-00.zip"
    manual_keep.write_bytes(b"manual")
    unrelated = dest_dir / "notes.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    deleted = prune_auto_backups(dest_dir, retention_count=2)
    deleted_names = {p.name for p in deleted}
    assert deleted_names == {
        "CafePOS_AutoBackup_2026-11-01_09-00.zip",
        "CafePOS_AutoBackup_2026-11-02_09-00.zip",
    }
    assert (dest_dir / "CafePOS_AutoBackup_2026-11-03_09-00.zip").exists()
    assert created.exists()
    assert manual_keep.exists()
    assert unrelated.exists()


def test_legacy_v1_migration_and_restore_triggers_admin_setup_without_fk_error(
    qapp, isolated_data_dir: Path, tmp_path: Path, monkeypatch
):
    # Create an authentic v1 database like MoCoffee without users table
    v1_db_path = get_database_path()
    with sqlite3.connect(str(v1_db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE business_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                business_name TEXT NOT NULL,
                accent_color TEXT NOT NULL,
                currency TEXT NOT NULL,
                address TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                logo_blob BLOB NOT NULL DEFAULT X'',
                logo_ext TEXT NOT NULL DEFAULT '',
                setup_completed INTEGER NOT NULL DEFAULT 0,
                next_order_seq INTEGER NOT NULL DEFAULT 1001
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price_minor INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                currency TEXT NOT NULL,
                subtotal_minor INTEGER NOT NULL,
                total_minor INTEGER NOT NULL,
                cash_received_minor INTEGER NOT NULL,
                change_minor INTEGER NOT NULL,
                business_name TEXT NOT NULL,
                business_address TEXT NOT NULL DEFAULT '',
                business_phone TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                unit_price_minor INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                line_total_minor INTEGER NOT NULL
            );
            INSERT INTO app_meta VALUES ('seeded_defaults', '1');
            INSERT INTO business_settings VALUES (
                1, 'MoCoffee', '#3A5A40', 'EGP', 'Cairo', '0100', X'', '', 1, 1002
            );
            INSERT INTO products VALUES (1, 'Flat White', 'Espresso', 4500, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
            INSERT INTO orders VALUES (
                1, 'ORD-1001', '2026-01-01T10:00:00Z', 'EGP', 4500, 4500, 5000, 500, 'MoCoffee', 'Cairo', '0100'
            );
            INSERT INTO order_items VALUES (1, 1, 1, 'Flat White', 4500, 1, 4500);
            """
        )

    # Also save a copy of this raw v1 DB as a legacy .db restore file before opening Database
    legacy_backup = tmp_path / "mocoffee_v1_backup.db"
    legacy_backup.write_bytes(v1_db_path.read_bytes())

    db = Database(v1_db_path)
    cfg = db.get_settings()
    assert cfg.business_name == "MoCoffee"
    assert cfg.currency == "EGP"
    assert cfg.accent_color == "#3A5A40"
    assert cfg.setup_completed is True
    assert db.has_admin() is False

    # SetupDialog opens straight to Step 2 (Admin Account) while prefilling preserved MoCoffee details
    dlg = SetupDialog(cfg, is_first_launch=False, require_admin_setup=True)
    assert dlg.step_stack.currentIndex() == 1
    assert dlg.name_edit.text() == "MoCoffee"

    # Legacy order receipt omits Cashier line cleanly instead of fabricating an identity
    legacy_order = db.get_order(1)
    assert legacy_order is not None
    assert legacy_order.cashier_user_id is None
    assert legacy_order.cashier_name_snapshot == ""
    assert "Cashier:" not in build_receipt_plain_text(legacy_order)

    # Initialize first admin on upgraded DB -> preserves ORD-1001 & MoCoffee branding
    saved_cfg, admin = db.initialize_owner_and_business(
        business_cfg=cfg,
        admin_username="tamer",
        admin_password="MoCoffeeStrong!1",
        admin_display_name="Mohamed Tamer",
    )
    assert db.has_admin() is True
    assert db.get_order_count() == 1
    assert any(label == "Legacy (Unassigned)" for _cid, label in db.list_order_cashiers(actor=admin))

    # Restoring the raw legacy .db backup into live v3 store must succeed cleanly (no FK crash on audit log)
    # and MainWindow._on_data_restored() must prompt admin setup cleanly!
    window = MainWindow(db, qapp)
    window.login_user(admin)
    restore_backup_archive(db, legacy_backup, actor=admin)
    assert db.has_admin() is False
    assert db.get_order_count() == 1

    # Mock SetupDialog rejection in _on_data_restored -> closes cleanly without crash
    monkeypatch.setattr(window, "ensure_initial_admin_ready", lambda: False)
    window._on_data_restored()
    window.close()
