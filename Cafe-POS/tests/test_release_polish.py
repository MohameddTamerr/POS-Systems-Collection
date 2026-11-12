from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest

from cafe_pos import __version__, meta
from cafe_pos.auth import ROLE_CASHIER
from cafe_pos.config import BusinessConfig
from cafe_pos.db import Database
from cafe_pos.paths import get_database_path
from cafe_pos.ui.about_dialog import AboutDialog
from cafe_pos.ui.login_view import LoginView
from cafe_pos.ui.main_window import MainWindow


def test_developer_metadata_and_link_resolution(qapp, monkeypatch):
    assert __version__ == "1.0.0"
    assert meta.APP_VERSION == "1.0.0"
    assert meta.DEVELOPER_NAME == "Mohamed Tamer"
    assert meta.GITHUB_REPOSITORY == "MohameddTamerr/POS-Systems-Collection"
    assert meta.DEVELOPER_CONTACT_URL == ""
    assert meta.DEVELOPER_CONTACT_EMAIL == ""

    assert meta.get_feature_request_url() == (
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues"
    )
    assert meta.get_bug_report_url() == (
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues"
    )
    # Without custom contact metadata configured, falls back to repository Issues page
    assert meta.has_direct_contact() is False
    assert meta.get_contact_developer_url() == meta.GITHUB_ISSUES_URL

    # Configurable overrides work cleanly
    assert meta.has_direct_contact(contact_url="https://example.com/contact") is True
    assert (
        meta.get_contact_developer_url(contact_url="https://example.com/contact")
        == "https://example.com/contact"
    )
    assert (
        meta.get_contact_developer_url(contact_url="", contact_email="dev@example.com")
        == "mailto:dev@example.com"
    )

    opened_urls: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened_urls.append(url.toString()) or True),
    )
    assert meta.open_external_url("https://github.com/MohameddTamerr/POS-Systems-Collection/issues")
    assert meta.open_external_url("mailto:dev@example.com")
    assert not meta.open_external_url("javascript:alert(1)")
    assert not meta.open_external_url("")
    assert opened_urls == [
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
        "mailto:dev@example.com",
    ]


def test_login_view_developer_contact_card_en_ar_and_auth_preserved(
    isolated_data_dir, qapp, monkeypatch
):
    db = Database(get_database_path())
    cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(business_name="Roast & Ember", app_language="en"),
        admin_username="owner",
        admin_password="OwnerPass123",
        admin_display_name="Owner",
    )

    opened: list[QUrl] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened.append(url) or True),
    )

    win = MainWindow(db, qapp)
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()

    login: LoginView = win.login_view
    assert login.contact_card.isVisible()
    assert login.contact_title_lbl.text() == "Made for cafés, built with care."
    assert (
        login.contact_sub_lbl.text()
        == "Have an idea? Need a new feature or a customized version for your business?"
    )
    assert "Developed by" in login.contact_credit_lbl.text()
    assert "Mohamed Tamer" in login.contact_credit_lbl.text()
    assert login.request_feature_btn.text() == "Request a Feature"
    assert login.contact_dev_btn.text() == "Contact Developer"

    QTest.mouseClick(login.request_feature_btn, Qt.LeftButton)
    QTest.mouseClick(login.contact_dev_btn, Qt.LeftButton)
    qapp.processEvents()

    assert [u.toString() for u in opened] == [
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
    ]

    # Switch to Arabic RTL and verify exact translated copy
    win.switch_language("ar-EG", persist_store=False)
    qapp.processEvents()
    assert qapp.layoutDirection() == Qt.RightToLeft
    assert login.contact_title_lbl.text() == "صُمم للكافيهات، بكل اهتمام."
    assert (
        login.contact_sub_lbl.text()
        == "عندك فكرة جديدة؟ محتاج ميزة إضافية أو نسخة مخصصة لنشاطك التجاري؟"
    )
    assert "Developed by" in login.contact_credit_lbl.text()
    assert "Mohamed Tamer" in login.contact_credit_lbl.text()
    assert login.request_feature_btn.text() == "اقترح ميزة"
    assert login.contact_dev_btn.text() == "تواصل مع المطور"

    # Existing login flow still works completely untouched
    signed_in_users = []
    login.authenticated.connect(signed_in_users.append)
    login.username_edit.setText("owner")
    login.password_edit.setText("OwnerPass123")
    QTest.mouseClick(login.sign_in_btn, Qt.LeftButton)
    qapp.processEvents()
    assert len(signed_in_users) == 1
    assert signed_in_users[0].id == admin.id
    win.close()


def test_about_dialog_and_profile_menu_access_admin_and_cashier(
    isolated_data_dir, qapp, monkeypatch
):
    db = Database(get_database_path())
    cfg, admin = db.initialize_owner_and_business(
        business_cfg=BusinessConfig(business_name="Roast & Ember", app_language="en"),
        admin_username="owner",
        admin_password="OwnerPass123",
        admin_display_name="Owner",
    )
    db.set_session_actor(admin)
    cashier = db.create_user(
        username="cashier1",
        password="CashierPass123",
        role=ROLE_CASHIER,
        display_name="Nora",
    )

    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(lambda url: opened.append(url.toString()) or True),
    )

    dlg = AboutDialog(cfg)
    dlg.show()
    qapp.processEvents()

    assert "1.0.0" in dlg.version_badge.text()
    assert "1.0.0" in dlg.ver_val_lbl.text()
    assert "Mohamed Tamer" in dlg.dev_val_lbl.text()
    assert "MohameddTamerr/POS-Systems-Collection" in dlg.repo_btn.text()
    assert "MIT License" in dlg.lic_val_lbl.text()
    assert dlg.feature_btn.text() == "Request Feature"
    assert dlg.bug_btn.text() == "Report Bug"
    assert dlg.contact_btn.text() == "Contact Developer"

    QTest.mouseClick(dlg.repo_btn, Qt.LeftButton)
    QTest.mouseClick(dlg.feature_btn, Qt.LeftButton)
    QTest.mouseClick(dlg.bug_btn, Qt.LeftButton)
    QTest.mouseClick(dlg.contact_btn, Qt.LeftButton)
    qapp.processEvents()
    assert opened == [
        "https://github.com/MohameddTamerr/POS-Systems-Collection",
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
        "https://github.com/MohameddTamerr/POS-Systems-Collection/issues",
    ]
    dlg.close()

    # MainWindow profile menu exposes Help & About to both Admin and Cashier
    win = MainWindow(db, qapp)
    for account in (admin, cashier):
        win.login_user(account)
        qapp.processEvents()
        assert win.menu_about_action in win.profile_menu.actions()
        assert win.menu_about_action.isVisible()
        assert win.menu_about_action.isEnabled()

    # Verify Arabic retranslation of AboutDialog
    win.switch_language("ar-EG", persist_store=False)
    ar_dlg = AboutDialog(cfg, parent=win)
    ar_dlg.retranslate_ui()
    assert "1.0.0" in ar_dlg.ver_val_lbl.text()
    assert "Mohamed Tamer" in ar_dlg.dev_val_lbl.text()
    assert ar_dlg.feature_btn.text() == "اقترح ميزة"
    assert ar_dlg.bug_btn.text() == "الإبلاغ عن خطأ"
    assert ar_dlg.contact_btn.text() == "تواصل مع المطور"
    ar_dlg.close()
    win.close()

