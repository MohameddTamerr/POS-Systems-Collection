# POS Systems Collection

A collection of **completely independent, offline Windows desktop Point of Sale (POS) applications**, each featuring its own source code, local SQLite database, white-label business customization, and standalone Windows `.exe` distribution via GitHub Releases.

**Developed by [Mohamed Tamer](https://github.com/MohameddTamerr)**

Repository: https://github.com/MohameddTamerr/POS-Systems-Collection

---

## Architecture Philosophy

This repository is **not** a web app or a monolithic all-in-one launcher. Every business vertical lives in its own independent top-level folder so each POS system can be built, customized, versioned, and downloaded separately as a native Windows desktop executable (`.exe`).

- **100% Native Windows Desktop:** Built with Python 3, PySide6 (Qt Widgets), SQLite, and PyInstaller.
- **Offline & Local-First:** Zero servers, zero browsers, zero external databases, and zero Python installation required for end users.
- **In-App White-Labeling:** Business owners configure their business name, logo, accent color, currency, address, and phone number inside the application on first launch or later in Settings—never by editing configuration files or rebuilding the `.exe`.

---

## Applications

| Application | Directory | Status | Release Tag | Executable |
| --- | --- | --- | --- | --- |
| **Café POS** | [`Cafe-POS/`](Cafe-POS/README.md) | **Implemented** | `cafe-pos-v1.0.0` (ready for release) | `CafePOS.exe` |
| **Retail POS** | — | Planned | — | — |
| **Restaurant POS** | — | Planned | — | — |
| **Pharmacy POS** | — | Planned | — | — |

---

## End-User Installation (Windows)

Once `cafe-pos-v1.0.0` is published on GitHub Releases:

1. Open the repository's **Releases** page on GitHub.
2. Locate release **`cafe-pos-v1.0.0`**.
3. Download **`CafePOS.exe`**.
4. Double-click **`CafePOS.exe`** to launch—no Python installation or setup dependencies required.
5. Complete the first-launch business setup window (business name, logo, accent color, currency, address, phone) and start ringing up orders.

All business settings, products, logos, and sales history persist locally in `%LOCALAPPDATA%\MohamedTamer\CafePOS\cafe_pos.db`.

---

## Developer Quick Start (`Cafe-POS`)

See [`Cafe-POS/README.md`](Cafe-POS/README.md) for full details.

```powershell
cd Cafe-POS
.\build.ps1
```

Output executable:

```text
Cafe-POS\dist\CafePOS.exe
```

---

## Publishing the First GitHub Release (`cafe-pos-v1.0.0`)

1. Build and verify `Cafe-POS\dist\CafePOS.exe` using `.\build.ps1`.
2. Commit and push the repository to `main`:
   ```bash
   git add .
   git commit -m "Implement independent Windows desktop Cafe-POS MVP"
   git push origin main
   ```
3. Create and push the release tag:
   ```bash
   git tag cafe-pos-v1.0.0
   git push origin cafe-pos-v1.0.0
   ```
4. On GitHub, open **Releases → Draft a new release**, choose tag `cafe-pos-v1.0.0`, attach `Cafe-POS\dist\CafePOS.exe`, add release notes, and publish.

---

## Repository Structure

```text
POS-Systems-Collection/
├── Cafe-POS/
│   ├── src/
│   │   ├── run_cafe_pos.py
│   │   └── cafe_pos/
│   │       ├── __init__.py
│   │       ├── __main__.py
│   │       ├── main.py
│   │       ├── paths.py
│   │       ├── money.py
│   │       ├── config.py
│   │       ├── db.py
│   │       ├── theme.py
│   │       ├── receipts.py
│   │       ├── assets/
│   │       └── ui/
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
