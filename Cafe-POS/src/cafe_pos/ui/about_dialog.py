from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import BusinessConfig
from ..icons import get_svg_icon
from ..i18n import bidi_ltr, tr
from ..meta import (
    APP_VERSION,
    DEVELOPER_NAME,
    GITHUB_REPOSITORY,
    GITHUB_REPO_URL,
    LICENSE_COPYRIGHT,
    LICENSE_NAME,
    get_bug_report_url,
    get_contact_developer_url,
    get_feature_request_url,
    has_direct_contact,
    open_external_url,
)
from ..theme import contrast_text_hex, scaled_logo_pixmap
from .animations import attach_button_feedback


def _esc_amp(text: str) -> str:
    return (text or "").replace("&", "&&")


class AboutDialog(QDialog):
    """Compact Boutique Help & About modal with project metadata and feedback links."""

    def __init__(
        self,
        config: BusinessConfig,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setMaximumWidth(620)
        self._build_ui()
        self.retranslate_ui()
        self.refresh_branding(config)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Top Identity Card
        header_card = QFrame()
        header_card.setProperty("class", "BoutiqueCard")
        hdr_lay = QHBoxLayout(header_card)
        hdr_lay.setContentsMargins(16, 14, 16, 14)
        hdr_lay.setSpacing(14)

        self.logo_lbl = QLabel()
        self.logo_lbl.setObjectName("BrandLogoFrame")
        self.logo_lbl.setFixedSize(52, 52)
        self.logo_lbl.setAlignment(Qt.AlignCenter)

        title_col = QVBoxLayout()
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(3)

        top_line = QHBoxLayout()
        top_line.setSpacing(8)
        self.app_name_lbl = QLabel("")
        self.app_name_lbl.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.version_badge = QLabel("")
        self.version_badge.setProperty("class", "PrimaryBadge")
        top_line.addWidget(self.app_name_lbl)
        top_line.addWidget(self.version_badge)
        top_line.addStretch(1)

        self.tagline_lbl = QLabel("")
        self.tagline_lbl.setProperty("class", "MutedLabel")
        self.tagline_lbl.setWordWrap(True)

        title_col.addLayout(top_line)
        title_col.addWidget(self.tagline_lbl)

        hdr_lay.addWidget(self.logo_lbl)
        hdr_lay.addLayout(title_col, 1)
        root.addWidget(header_card)

        # Details Card (Version, Developer, Repository, License)
        info_card = QFrame()
        info_card.setProperty("class", "CozySubCard")
        form = QFormLayout(info_card)
        form.setContentsMargins(16, 14, 16, 14)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setLabelAlignment(Qt.AlignLeading | Qt.AlignVCenter)

        self.ver_key_lbl = QLabel("")
        self.ver_key_lbl.setProperty("class", "MutedLabel")
        self.ver_val_lbl = QLabel(bidi_ltr(APP_VERSION))
        self.ver_val_lbl.setStyleSheet("font-weight: 700;")

        self.dev_key_lbl = QLabel("")
        self.dev_key_lbl.setProperty("class", "MutedLabel")
        self.dev_val_lbl = QLabel(bidi_ltr(DEVELOPER_NAME))
        self.dev_val_lbl.setStyleSheet("font-weight: 700;")

        self.repo_key_lbl = QLabel("")
        self.repo_key_lbl.setProperty("class", "MutedLabel")
        self.repo_btn = QPushButton(bidi_ltr(GITHUB_REPOSITORY))
        self.repo_btn.setProperty("class", "AboutLinkBtn")
        self.repo_btn.setCursor(Qt.PointingHandCursor)
        self.repo_btn.setAutoDefault(False)
        self.repo_btn.clicked.connect(lambda: open_external_url(GITHUB_REPO_URL))

        self.lic_key_lbl = QLabel("")
        self.lic_key_lbl.setProperty("class", "MutedLabel")
        self.lic_val_lbl = QLabel("")
        self.lic_val_lbl.setWordWrap(True)

        form.addRow(self.ver_key_lbl, self.ver_val_lbl)
        form.addRow(self.dev_key_lbl, self.dev_val_lbl)
        form.addRow(self.repo_key_lbl, self.repo_btn)
        form.addRow(self.lic_key_lbl, self.lic_val_lbl)
        root.addWidget(info_card)

        # Quick Help & Feedback Buttons
        actions_lbl = QVBoxLayout()
        actions_lbl.setSpacing(8)

        self.links_title_lbl = QLabel("")
        self.links_title_lbl.setStyleSheet("font-size: 12.5px; font-weight: 700;")
        self.links_sub_lbl = QLabel("")
        self.links_sub_lbl.setProperty("class", "MutedLabel")
        self.links_sub_lbl.setWordWrap(True)
        actions_lbl.addWidget(self.links_title_lbl)
        actions_lbl.addWidget(self.links_sub_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.feature_btn = QPushButton("")
        self.feature_btn.setProperty("class", "LoginContactBtn")
        self.feature_btn.setCursor(Qt.PointingHandCursor)
        self.feature_btn.setMinimumHeight(36)
        self.feature_btn.setAutoDefault(False)
        self.feature_btn.clicked.connect(
            lambda: open_external_url(get_feature_request_url())
        )
        attach_button_feedback(self.feature_btn)

        self.bug_btn = QPushButton("")
        self.bug_btn.setProperty("class", "LoginContactBtn")
        self.bug_btn.setCursor(Qt.PointingHandCursor)
        self.bug_btn.setMinimumHeight(36)
        self.bug_btn.setAutoDefault(False)
        self.bug_btn.clicked.connect(lambda: open_external_url(get_bug_report_url()))
        attach_button_feedback(self.bug_btn)

        self.contact_btn = QPushButton("")
        self.contact_btn.setProperty("class", "LoginContactBtn")
        self.contact_btn.setCursor(Qt.PointingHandCursor)
        self.contact_btn.setMinimumHeight(36)
        self.contact_btn.setAutoDefault(False)
        self.contact_btn.clicked.connect(
            lambda: open_external_url(get_contact_developer_url())
        )
        attach_button_feedback(self.contact_btn)

        btn_row.addWidget(self.feature_btn, 1)
        btn_row.addWidget(self.bug_btn, 1)
        btn_row.addWidget(self.contact_btn, 1)
        actions_lbl.addLayout(btn_row)
        root.addLayout(actions_lbl)

        # Footer Close
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_btn = QPushButton("")
        self.close_btn.setProperty("role", "primary")
        self.close_btn.setMinimumHeight(36)
        self.close_btn.setMinimumWidth(110)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.accept)
        attach_button_feedback(self.close_btn)
        footer.addWidget(self.close_btn)
        root.addLayout(footer)

    def refresh_branding(self, config: BusinessConfig) -> None:
        self.config = config
        self.logo_lbl.setPixmap(scaled_logo_pixmap(config, 36, 36))
        accent = getattr(config, "accent_color", "#005B37")
        self.repo_btn.setIcon(get_svg_icon("external", accent, 13))
        self.repo_btn.setIconSize(QSize(13, 13))
        self.feature_btn.setIcon(get_svg_icon("sparkles", accent, 14))
        self.feature_btn.setIconSize(QSize(14, 14))
        self.bug_btn.setIcon(get_svg_icon("bug", "#5E6B5F", 14))
        self.bug_btn.setIconSize(QSize(14, 14))
        self.contact_btn.setIcon(get_svg_icon("message", accent, 14))
        self.contact_btn.setIconSize(QSize(14, 14))
        fg = contrast_text_hex(accent)
        self.close_btn.setIcon(get_svg_icon("check", fg, 14))
        self.close_btn.setIconSize(QSize(14, 14))

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("about.title"))
        self.app_name_lbl.setText(tr("about.app_name"))
        self.version_badge.setText(bidi_ltr(f"v{APP_VERSION}"))
        self.tagline_lbl.setText(tr("about.tagline"))

        self.ver_key_lbl.setText(tr("about.version_label"))
        self.dev_key_lbl.setText(tr("about.developer_label"))
        self.repo_key_lbl.setText(tr("about.repo_label"))
        self.repo_btn.setToolTip(GITHUB_REPO_URL)
        self.lic_key_lbl.setText(tr("about.license_label"))
        self.lic_val_lbl.setText(
            tr(
                "about.license_value",
                license=LICENSE_NAME,
                copyright=bidi_ltr(LICENSE_COPYRIGHT),
            )
        )

        self.links_title_lbl.setText(tr("about.feedback_heading"))
        self.links_sub_lbl.setText(tr("about.feedback_desc"))
        self.feature_btn.setText(_esc_amp(tr("about.request_feature_btn")))
        self.feature_btn.setToolTip(get_feature_request_url())
        self.bug_btn.setText(_esc_amp(tr("about.report_bug_btn")))
        self.bug_btn.setToolTip(get_bug_report_url())
        self.contact_btn.setText(_esc_amp(tr("about.contact_btn")))
        self.contact_btn.setToolTip(
            get_contact_developer_url()
            if has_direct_contact()
            else tr("about.contact_issues_tip")
        )
        self.close_btn.setText(_esc_amp(tr("common.close")))

