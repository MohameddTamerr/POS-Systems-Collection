# Café POS — Windows Desktop Point of Sale

An independent, offline, white-label Windows desktop Point of Sale application for cafés, bakeries, and tea shops. Built natively with **Python 3**, **PySide6 (Qt Widgets)**, and **SQLite**, and packaged into a standalone **`CafePOS.exe`** executable using **PyInstaller**.

No web browser, no local web server, and no Python installation are required for end users running the Windows executable.

---

## Key Features

### Cashier Register
- **Left Pane:** Category filter strip, live product search (`Ctrl+F`), and high-contrast product tiles showing prices in the active currency.
- **Right Pane:** Active order table with inline quantity steppers (`-` / `+`), item removal, `Clear Order` confirmation, exact integer minor-unit subtotal and total calculation, cash received input with quick-cash chips, live change calculation, insufficient-cash protection, and one-click `Complete Sale (F12)`.

### White-Label Business Personalization
- **First-Launch Setup Wizard:** Configures Business Name, optional Business Logo, Theme Accent Color, Currency, Address, and Phone Number on initial startup (with a **Skip & Try Demo** option).
- **Live Settings Panel:** Modify any branding field later in **Settings (`F4`)**—changes apply immediately throughout the register, navigation bar, and printed receipts without rebuilding `CafePOS.exe`.

### Products, Sales History & Windows Printing
- **Products Management (`F2`):** Add, edit, and delete products with custom or standard categories, search filtering, and sample menu restoration.
- **Sales History (`F3`):** Browse completed orders, search by order number or item name, inspect order line details, print receipts through the native Windows print dialog, or export receipts as PDF.
- **Local SQLite Persistence:** Exact integer minor-unit monetary arithmetic (`price_minor`) stored in a local SQLite database.

---

## Local Data Storage

When running from source or as `CafePOS.exe`, user data is **never** stored inside the bundled application directory. All writable data persists under the Windows user AppData folder:

```text
%LOCALAPPDATA%\MohamedTamer\CafePOS\cafe_pos.db
```

Uploaded business logos are persisted directly inside SQLite so **Backup Database...** and **Restore Database...** in **Settings** preserve business branding alongside products and sales history.

---

## Running From Source (Developers)

Requirements: Python 3.10+ on Windows.

```powershell
cd Cafe-POS
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe src\run_cafe_pos.py
```

Run automated tests:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest tests -v
```

---

## Building `CafePOS.exe`

Run the included Windows build script from `Cafe-POS/`:

```powershell
cd Cafe-POS
.\build.ps1
```

This runs the test suite, cleans previous `build/` and `dist/` outputs, and invokes PyInstaller with `CafePOS.spec` (`--noconfirm --clean`) to produce:

```text
Cafe-POS\dist\CafePOS.exe
```

Build options:
- `.\build.ps1 -SkipTests` — Build without re-running `pytest`.
- `.\build.ps1 -CleanVenv` — Recreate `.venv` cleanly before building.

---

## Release Information

- **Planned GitHub Release Tag:** `cafe-pos-v1.0.0`
- **Release Asset:** `CafePOS.exe`

---

## License

Released under the [MIT License](../LICENSE). Copyright (c) 2026 Mohamed Tamer.

