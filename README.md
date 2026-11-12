# POS Systems Collection

A collection of **completely independent, offline Windows desktop Point of Sale (POS) applications**, each featuring its own source code, local SQLite database, white-label business customization, offline role-based security, and standalone Windows `.exe` distribution via GitHub Releases.

**Developed by [Mohamed Tamer](https://github.com/MohameddTamerr)**

Repository: https://github.com/MohameddTamerr/POS-Systems-Collection

---

## Architecture Philosophy

This repository is **not** a web app or a monolithic all-in-one launcher. Every business vertical lives in its own independent top-level folder so each POS system can be built, customized, versioned, and downloaded separately as a native Windows desktop executable (`.exe`).

- **100% Native Windows Desktop:** Built with Python 3, PySide6 (Qt Widgets), Pillow, Argon2id (`argon2-cffi`), SQLite, and PyInstaller.
- **Offline & Local-First:** Zero servers, zero browsers, zero external databases, and zero Python installation required for end users (except optional online product image search).
- **In-App White-Labeling & RBAC:** Store owners set up their storefront profile, accent color, logo, currency, and primary Administrator account inside the app on first launch—no config files or default passwords shipped.

---

## Applications

| Application | Directory | Status | Release Tag | Executable |
| --- | --- | --- | --- | --- |
| **Café POS** | [`Cafe-POS/`](Cafe-POS/README.md) | **v1.0.0** | [`cafe-pos-v1.0.0`](https://github.com/MohameddTamerr/POS-Systems-Collection/releases/tag/cafe-pos-v1.0.0) | `CafePOS-v1.0.0-Windows.exe` |
| **Retail POS** | — | Planned | — | — |
| **Restaurant POS** | — | Planned | — | — |
| **Pharmacy POS** | — | Planned | — | — |

### Highlights in `Cafe-POS` (`v1.0.0`)
- **Boutique Cashier Register (`F1`):** Studio product cutouts, illustrated category cards, configurable **Product Modifiers & Extras** (Size, Milk Type, Add-ons with independent pricing & per-option recipes), **Dine-in / Takeaway / Delivery** order types, **Hold & Resume Orders**, percentage/fixed discounts, **Split Payments**, **Cashier Shifts & Drawer Management**, and fast checkout (`F12`).
- **Bilingual English & Egyptian Arabic (`en` / `ar-EG`) with Full RTL:** Live `English | العربية` language switching across Setup, Sign-In, Settings, and user Profile Menu without restarting; full Qt RTL layout & stepper mirroring, per-user display preferences (font scale, density, reduced motion), bidi-safe formatting, Arabic-Indic digit parsing (`١٢٥٫٥٠`), localized EGP (`ج.م`) / SAR / AED formatting, configurable 58mm/80mm thermal and A5 PDF receipts.
- **Inventory, Operating Expenses & Analytics (`F2` / `F4`):** Track ingredient stock and recipe auto-deduction with Register low-stock alerts, log operating expenses, and review live revenue/profit/category/shift analytics.
- **Offline Auth, RBAC, Verified Backups & Help (`F5` / `F6`):** Mandatory initial Admin setup (zero default passwords), Argon2id password hashing, progressive login throttling, Admin vs. Cashier permissions, `Ctrl+L` lock, verified `.zip` backups with SHA-256 manifests and pre-restore safety rollback, plus Login Developer Contact Card and **Help & About**.

---

## End-User Installation (Windows)

From the [`cafe-pos-v1.0.0`](https://github.com/MohameddTamerr/POS-Systems-Collection/releases/tag/cafe-pos-v1.0.0) release (`Café POS v1.0.0 — First Windows Release`):

1. Open the repository's **Releases** page on GitHub and download **`CafePOS-v1.0.0-Windows.exe`**.
2. Double-click **`CafePOS-v1.0.0-Windows.exe`** to launch—no Python installation or setup dependencies required.
3. Complete the two-step first-launch setup wizard (storefront identity/theme + primary Administrator account) and start ringing up orders.

All business data persists locally in `%LOCALAPPDATA%\MohamedTamer\CafePOS\cafe_pos.db` (plus `product_images\`, `backups\`, and `logs\`). Existing `v1`–`v7` databases migrate non-destructively to `v8` while preserving all historical data.

---

## Developer Quick Start (`Cafe-POS`)

See [`Cafe-POS/README.md`](Cafe-POS/README.md) for full details, screenshots, shortcuts, storage layout, known limitations, and security notes.

```powershell
cd Cafe-POS
.\build.ps1
```

Output executable:

```text
Cafe-POS\dist\CafePOS.exe
```

---

## Publishing a Release (`CafePOS-v1.0.0-Windows.exe`)

1. Build and verify `Cafe-POS\dist\CafePOS.exe` using `.\build.ps1` and prepare `Cafe-POS\dist\CafePOS-v1.0.0-Windows.exe`.
2. Commit and push the repository changes, create tag `cafe-pos-v1.0.0`, and attach `CafePOS-v1.0.0-Windows.exe` to the GitHub Release titled **Café POS v1.0.0 — First Windows Release**.

---

## Repository Structure

```text
POS-Systems-Collection/
├── Cafe-POS/
│   ├── docs/
│   │   └── screenshots/
│   ├── src/
│   │   ├── run_cafe_pos.py
│   │   └── cafe_pos/
│   │       ├── __init__.py
│   │       ├── __main__.py
│   │       ├── _db_finance.py
│   │       ├── _db_inventory.py
│   │       ├── auth.py
│   │       ├── backups.py
│   │       ├── bilingual.py
│   │       ├── config.py
│   │       ├── db.py
│   │       ├── diagnostics.py
│   │       ├── i18n.py
│   │       ├── icons.py
│   │       ├── image_search.py
│   │       ├── main.py
│   │       ├── meta.py
│   │       ├── money.py
│   │       ├── paths.py
│   │       ├── payments.py
│   │       ├── receipts.py
│   │       ├── theme.py
│   │       ├── translations/
│   │       │   ├── en.json
│   │       │   └── ar_EG.json
│   │       ├── assets/
│   │       │   └── products/
│   │       └── ui/
│   │           ├── about_dialog.py
│   │           ├── animations.py
│   │           ├── category_combo.py
│   │           ├── display_preferences_dialog.py
│   │           ├── expenses_view.py
│   │           ├── held_orders_dialog.py
│   │           ├── inventory_view.py
│   │           ├── login_view.py
│   │           ├── main_window.py
│   │           ├── modifier_manager_dialog.py
│   │           ├── payment_dialog.py
│   │           ├── product_customize_dialog.py
│   │           ├── product_editor_dialog.py
│   │           ├── products_view.py
│   │           ├── receipt_dialog.py
│   │           ├── refund_dialog.py
│   │           ├── register_view.py
│   │           ├── reports_view.py
│   │           ├── sales_view.py
│   │           ├── settings_view.py
│   │           ├── setup_dialog.py
│   │           ├── shift_dialog.py
│   │           ├── users_view.py
│   │           └── widgets.py
│   ├── tests/
│   ├── CafePOS.spec
│   ├── build.ps1
│   ├── pytest.ini
│   ├── requirements.txt
│   └── README.md
├── .gitignore
├── LICENSE
└── README.md
```

---

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Mohamed Tamer.
