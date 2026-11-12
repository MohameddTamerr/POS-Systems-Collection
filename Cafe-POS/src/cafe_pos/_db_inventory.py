from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .auth import Permission, UserAccount
from .i18n import get_language, normalize_language, tr

INGREDIENT_UNITS = ("g", "kg", "ml", "l", "pcs")
MOVEMENT_TYPES = ("RESTOCK", "ADJUSTMENT", "SALE_DEDUCTION", "WASTE", "REFUND_RETURN")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_ingredient_unit(unit: str) -> str:
    cleaned = (unit or "").strip().lower()
    if cleaned not in INGREDIENT_UNITS:
        raise ValueError(f"Unsupported unit '{unit}'. Must be one of {', '.join(INGREDIENT_UNITS)}.")
    return cleaned


def parse_qty_to_milli(value: str | int | float | Decimal, *, allow_negative: bool = False) -> int:
    """Convert numeric quantity (up to 3 decimal places) into exact integer milli-units."""
    if isinstance(value, bool):
        raise ValueError("Invalid quantity value.")
    raw = str(value).strip().replace(",", ".")
    if not raw:
        raise ValueError("Quantity is required.")
    try:
        dec = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError("Quantity must be a valid number.") from exc
    if not dec.is_finite():
        raise ValueError("Quantity must be finite.")
    scaled = (dec * Decimal("1000")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    milli = int(scaled)
    if not allow_negative and milli < 0:
        raise ValueError("Quantity cannot be negative.")
    return milli


def format_qty_milli(milli: int) -> str:
    """Format integer milli-units neatly (1500 -> '1.5', 250 -> '0.25', 1000 -> '1')."""
    val = Decimal(int(milli)) / Decimal("1000")
    s = format(val.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@dataclass(slots=True)
class IngredientRecord:
    id: int
    name_en: str
    name_ar: str
    unit: str
    stock_qty_milli: int
    min_threshold_milli: int
    cost_per_unit_minor: int
    track_stock: bool
    is_active: bool
    is_archived: bool
    created_at: str
    updated_at: str

    def display_name(self, lang: str | None = None) -> str:
        eff = normalize_language(lang or get_language())
        en = (self.name_en or "").strip()
        ar = (self.name_ar or "").strip()
        if eff == "ar-EG":
            return ar or en
        return en or ar

    @property
    def is_low_stock(self) -> bool:
        return (
            self.track_stock
            and self.is_active
            and not self.is_archived
            and self.stock_qty_milli <= self.min_threshold_milli
        )


@dataclass(slots=True)
class RecipeEntryRecord:
    ingredient_id: int
    ingredient_name_en: str
    ingredient_name_ar: str
    unit: str
    qty_milli: int
    cost_per_unit_minor: int = 0
    stock_qty_milli: int = 0

    def display_ingredient_name(self, lang: str | None = None) -> str:
        eff = normalize_language(lang or get_language())
        if eff == "ar-EG":
            return (self.ingredient_name_ar or self.ingredient_name_en or "").strip()
        return (self.ingredient_name_en or self.ingredient_name_ar or "").strip()


@dataclass(slots=True)
class StockMovementRecord:
    id: int
    ingredient_id: int
    ingredient_name_en: str
    ingredient_name_ar: str
    unit: str
    movement_type: str
    delta_qty_milli: int
    balance_after_milli: int
    unit_cost_minor: int
    reference_type: str
    reference_id: str
    notes: str
    actor_user_id: int | None
    actor_name: str
    created_at: str


def _row_to_ingredient(row: sqlite3.Row) -> IngredientRecord:
    return IngredientRecord(
        id=int(row["id"]),
        name_en=(row["name_en"] or "").strip(),
        name_ar=(row["name_ar"] or "").strip(),
        unit=str(row["unit"] or "pcs"),
        stock_qty_milli=int(row["stock_qty_milli"] or 0),
        min_threshold_milli=int(row["min_threshold_milli"] or 0),
        cost_per_unit_minor=int(row["cost_per_unit_minor"] or 0),
        track_stock=bool(int(row["track_stock"])),
        is_active=bool(int(row["is_active"])),
        is_archived=bool(int(row["is_archived"])),
        created_at=str(row["created_at"] or ""),
        updated_at=str(row["updated_at"] or ""),
    )


class DatabaseInventoryMixin:
    """Recipe-based inventory, ingredients, milli-unit stock movements, and low-stock alerts."""

    def list_ingredients(
        self,
        *,
        include_archived: bool = False,
        only_active: bool = False,
        search: str = "",
        actor: UserAccount | None = None,
    ) -> list[IngredientRecord]:
        sql = "SELECT * FROM ingredients WHERE 1=1"
        params: list[object] = []
        if not include_archived:
            sql += " AND is_archived = 0"
        if only_active:
            sql += " AND is_active = 1"
        q = (search or "").strip()
        if q:
            like = f"%{q}%"
            sql += " AND (name_en LIKE ? OR name_ar LIKE ? OR unit LIKE ?)"
            params.extend([like, like, like])
        sql += " ORDER BY is_archived ASC, COALESCE(NULLIF(name_en, ''), name_ar) COLLATE NOCASE ASC;"

        with self._tx() as conn:
            self._resolve_actor(conn, actor, None)
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [_row_to_ingredient(r) for r in rows]

    def get_ingredient(self, ingredient_id: int) -> IngredientRecord | None:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
        return _row_to_ingredient(row) if row is not None else None

    def create_ingredient(
        self,
        name_en: str,
        name_ar: str = "",
        *,
        unit: str = "g",
        stock_qty_milli: int = 0,
        min_threshold_milli: int = 0,
        cost_per_unit_minor: int = 0,
        track_stock: bool = True,
        actor: UserAccount | None = None,
    ) -> IngredientRecord:
        clean_en = " ".join((name_en or "").strip().split())[:80]
        clean_ar = " ".join((name_ar or "").strip().split())[:80]
        if not clean_en and not clean_ar:
            raise ValueError(tr("inventory.err.name_required"))
        eff_unit = validate_ingredient_unit(unit)
        stock_m = int(stock_qty_milli)
        min_m = max(0, int(min_threshold_milli))
        cost_m = max(0, int(cost_per_unit_minor))
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            dup = conn.execute(
                """
                SELECT id FROM ingredients
                WHERE LOWER(TRIM(COALESCE(NULLIF(name_en, ''), name_ar))) = LOWER(?)
                  AND is_archived = 0
                LIMIT 1;
                """,
                ((clean_en or clean_ar),),
            ).fetchone()
            if dup is not None:
                raise ValueError(
                    tr("inventory.err.duplicate_name", name=(clean_en or clean_ar))
                )

            cur = conn.execute(
                """
                INSERT INTO ingredients (
                    name_en, name_ar, unit, stock_qty_milli, min_threshold_milli,
                    cost_per_unit_minor, track_stock, is_active, is_archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?);
                """,
                (
                    clean_en,
                    clean_ar,
                    eff_unit,
                    stock_m,
                    min_m,
                    cost_m,
                    1 if track_stock else 0,
                    now,
                    now,
                ),
            )
            ing_id = int(cur.lastrowid)
            if stock_m != 0:
                conn.execute(
                    """
                    INSERT INTO stock_movements (
                        ingredient_id, movement_type, delta_qty_milli, balance_after_milli,
                        unit_cost_minor, reference_type, reference_id, client_deduction_key,
                        notes, actor_user_id, actor_name, created_at
                    ) VALUES (?, 'RESTOCK', ?, ?, ?, 'opening', ?, '', ?, ?, ?, ?);
                    """,
                    (
                        ing_id,
                        stock_m,
                        stock_m,
                        cost_m,
                        str(ing_id),
                        "Initial stock",
                        live_actor.id if live_actor else None,
                        live_actor.label if live_actor else "",
                        now,
                    ),
                )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "INGREDIENT_CREATED",
                    actor=live_actor,
                    target_type="ingredient",
                    target_id=str(ing_id),
                    details=f"Created ingredient '{clean_en or clean_ar}' ({eff_unit})",
                )
            row = conn.execute("SELECT * FROM ingredients WHERE id = ?;", (ing_id,)).fetchone()
            assert row is not None
            return _row_to_ingredient(row)

    def update_ingredient(
        self,
        ingredient_id: int,
        *,
        name_en: str,
        name_ar: str = "",
        unit: str = "g",
        min_threshold_milli: int = 0,
        cost_per_unit_minor: int = 0,
        track_stock: bool = True,
        is_active: bool = True,
        actor: UserAccount | None = None,
    ) -> IngredientRecord:
        clean_en = " ".join((name_en or "").strip().split())[:80]
        clean_ar = " ".join((name_ar or "").strip().split())[:80]
        if not clean_en and not clean_ar:
            raise ValueError(tr("inventory.err.name_required"))
        eff_unit = validate_ingredient_unit(unit)
        min_m = max(0, int(min_threshold_milli))
        cost_m = max(0, int(cost_per_unit_minor))
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            existing = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            if existing is None:
                raise ValueError(tr("inventory.err.not_found"))

            conn.execute(
                """
                UPDATE ingredients
                SET name_en = ?, name_ar = ?, unit = ?, min_threshold_milli = ?,
                    cost_per_unit_minor = ?, track_stock = ?, is_active = ?, updated_at = ?
                WHERE id = ?;
                """,
                (
                    clean_en,
                    clean_ar,
                    eff_unit,
                    min_m,
                    cost_m,
                    1 if track_stock else 0,
                    1 if is_active else 0,
                    now,
                    int(ingredient_id),
                ),
            )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "INGREDIENT_UPDATED",
                    actor=live_actor,
                    target_type="ingredient",
                    target_id=str(ingredient_id),
                    details=f"Updated ingredient '{clean_en or clean_ar}'",
                )
            row = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            assert row is not None
            return _row_to_ingredient(row)

    def archive_ingredient(
        self, ingredient_id: int, *, restore: bool = False, actor: UserAccount | None = None
    ) -> IngredientRecord:
        now = _utc_now_iso()
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            row = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("inventory.err.not_found"))
            conn.execute(
                "UPDATE ingredients SET is_archived = ?, updated_at = ? WHERE id = ?;",
                (0 if restore else 1, now, int(ingredient_id)),
            )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "INGREDIENT_RESTORED" if restore else "INGREDIENT_ARCHIVED",
                    actor=live_actor,
                    target_type="ingredient",
                    target_id=str(ingredient_id),
                    details=f"{'Restored' if restore else 'Archived'} ingredient '{row['name_en'] or row['name_ar']}'",
                )
            updated = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            assert updated is not None
            return _row_to_ingredient(updated)

    def adjust_ingredient_stock(
        self,
        ingredient_id: int,
        movement_type: str,
        qty_milli: int,
        *,
        notes: str = "",
        unit_cost_minor: int | None = None,
        actor: UserAccount | None = None,
    ) -> IngredientRecord:
        mtype = (movement_type or "").strip().upper()
        if mtype not in ("RESTOCK", "ADJUSTMENT", "WASTE"):
            raise ValueError(f"Invalid manual movement type: {movement_type}")

        qty_val = int(qty_milli)
        if mtype in ("RESTOCK", "WASTE") and qty_val <= 0:
            raise ValueError(tr("inventory.err.qty_positive"))
        if mtype == "ADJUSTMENT" and qty_val == 0:
            raise ValueError(tr("inventory.err.qty_nonzero"))

        delta = qty_val if mtype in ("RESTOCK", "ADJUSTMENT") else -qty_val
        clean_notes = " ".join((notes or "").strip().split())[:200]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            row = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            if row is None:
                raise ValueError(tr("inventory.err.not_found"))
            cur_bal = int(row["stock_qty_milli"] or 0)
            new_bal = cur_bal + delta
            eff_cost = (
                max(0, int(unit_cost_minor))
                if unit_cost_minor is not None
                else int(row["cost_per_unit_minor"] or 0)
            )
            if mtype == "RESTOCK" and unit_cost_minor is not None:
                conn.execute(
                    """
                    UPDATE ingredients
                    SET stock_qty_milli = ?, cost_per_unit_minor = ?, updated_at = ?
                    WHERE id = ?;
                    """,
                    (new_bal, eff_cost, now, int(ingredient_id)),
                )
            else:
                conn.execute(
                    "UPDATE ingredients SET stock_qty_milli = ?, updated_at = ? WHERE id = ?;",
                    (new_bal, now, int(ingredient_id)),
                )

            conn.execute(
                """
                INSERT INTO stock_movements (
                    ingredient_id, movement_type, delta_qty_milli, balance_after_milli,
                    unit_cost_minor, reference_type, reference_id, client_deduction_key,
                    notes, actor_user_id, actor_name, created_at
                ) VALUES (?, ?, ?, ?, ?, 'manual', '', '', ?, ?, ?, ?);
                """,
                (
                    int(ingredient_id),
                    mtype,
                    delta,
                    new_bal,
                    eff_cost,
                    clean_notes,
                    live_actor.id if live_actor else None,
                    live_actor.label if live_actor else "",
                    now,
                ),
            )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    f"STOCK_{mtype}",
                    actor=live_actor,
                    target_type="ingredient",
                    target_id=str(ingredient_id),
                    details=(
                        f"{mtype} '{row['name_en'] or row['name_ar']}': "
                        f"delta={format_qty_milli(delta)} {row['unit']}, bal={format_qty_milli(new_bal)}"
                    ),
                )
            updated = conn.execute(
                "SELECT * FROM ingredients WHERE id = ?;", (int(ingredient_id),)
            ).fetchone()
            assert updated is not None
            return _row_to_ingredient(updated)

    def list_stock_movements(
        self,
        *,
        ingredient_id: int | None = None,
        movement_type: str | None = None,
        limit: int = 200,
    ) -> list[StockMovementRecord]:
        sql = """
            SELECT sm.*, i.name_en, i.name_ar, i.unit
            FROM stock_movements sm
            JOIN ingredients i ON i.id = sm.ingredient_id
            WHERE 1=1
        """
        params: list[object] = []
        if ingredient_id is not None:
            sql += " AND sm.ingredient_id = ?"
            params.append(int(ingredient_id))
        if movement_type:
            sql += " AND sm.movement_type = ?"
            params.append(str(movement_type).upper())
        sql += " ORDER BY sm.id DESC LIMIT ?;"
        params.append(max(1, min(1000, int(limit))))

        with self._tx() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            StockMovementRecord(
                id=int(r["id"]),
                ingredient_id=int(r["ingredient_id"]),
                ingredient_name_en=str(r["name_en"] or ""),
                ingredient_name_ar=str(r["name_ar"] or ""),
                unit=str(r["unit"] or "pcs"),
                movement_type=str(r["movement_type"]),
                delta_qty_milli=int(r["delta_qty_milli"]),
                balance_after_milli=int(r["balance_after_milli"]),
                unit_cost_minor=int(r["unit_cost_minor"] or 0),
                reference_type=str(r["reference_type"] or ""),
                reference_id=str(r["reference_id"] or ""),
                notes=str(r["notes"] or ""),
                actor_user_id=(
                    int(r["actor_user_id"]) if r["actor_user_id"] is not None else None
                ),
                actor_name=str(r["actor_name"] or ""),
                created_at=str(r["created_at"] or ""),
            )
            for r in rows
        ]

    def get_product_recipe(self, product_id: int) -> list[RecipeEntryRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT pr.ingredient_id, pr.qty_milli,
                       i.name_en, i.name_ar, i.unit, i.cost_per_unit_minor, i.stock_qty_milli
                FROM product_recipes pr
                JOIN ingredients i ON i.id = pr.ingredient_id
                WHERE pr.product_id = ?
                ORDER BY pr.id ASC;
                """,
                (int(product_id),),
            ).fetchall()
        return [
            RecipeEntryRecord(
                ingredient_id=int(r["ingredient_id"]),
                ingredient_name_en=str(r["name_en"] or ""),
                ingredient_name_ar=str(r["name_ar"] or ""),
                unit=str(r["unit"] or "pcs"),
                qty_milli=int(r["qty_milli"]),
                cost_per_unit_minor=int(r["cost_per_unit_minor"] or 0),
                stock_qty_milli=int(r["stock_qty_milli"] or 0),
            )
            for r in rows
        ]

    def set_product_recipe(
        self,
        product_id: int,
        entries: list[tuple[int, int]],
        *,
        actor: UserAccount | None = None,
    ) -> list[RecipeEntryRecord]:
        deduped: dict[int, int] = {}
        for ing_id, qty_m in entries:
            qm = int(qty_m)
            if qm <= 0:
                raise ValueError(tr("inventory.err.qty_positive"))
            deduped[int(ing_id)] = deduped.get(int(ing_id), 0) + qm

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            prow = conn.execute(
                "SELECT id, name FROM products WHERE id = ?;", (int(product_id),)
            ).fetchone()
            if prow is None:
                raise ValueError(tr("backend.err.product_not_found"))

            for ing_id in deduped:
                if conn.execute(
                    "SELECT 1 FROM ingredients WHERE id = ?;", (ing_id,)
                ).fetchone() is None:
                    raise ValueError(tr("inventory.err.not_found"))

            conn.execute("DELETE FROM product_recipes WHERE product_id = ?;", (int(product_id),))
            for ing_id, qm in deduped.items():
                conn.execute(
                    "INSERT INTO product_recipes (product_id, ingredient_id, qty_milli) VALUES (?, ?, ?);",
                    (int(product_id), ing_id, qm),
                )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "PRODUCT_RECIPE_UPDATED",
                    actor=live_actor,
                    target_type="product",
                    target_id=str(product_id),
                    details=f"Updated recipe for '{prow['name']}' ({len(deduped)} ingredients)",
                )
        return self.get_product_recipe(product_id)

    def get_modifier_option_recipe(self, modifier_option_id: int) -> list[RecipeEntryRecord]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT mor.ingredient_id, mor.qty_milli,
                       i.name_en, i.name_ar, i.unit, i.cost_per_unit_minor, i.stock_qty_milli
                FROM modifier_option_recipes mor
                JOIN ingredients i ON i.id = mor.ingredient_id
                WHERE mor.modifier_option_id = ?
                ORDER BY mor.id ASC;
                """,
                (int(modifier_option_id),),
            ).fetchall()
        return [
            RecipeEntryRecord(
                ingredient_id=int(r["ingredient_id"]),
                ingredient_name_en=str(r["name_en"] or ""),
                ingredient_name_ar=str(r["name_ar"] or ""),
                unit=str(r["unit"] or "pcs"),
                qty_milli=int(r["qty_milli"]),
                cost_per_unit_minor=int(r["cost_per_unit_minor"] or 0),
                stock_qty_milli=int(r["stock_qty_milli"] or 0),
            )
            for r in rows
        ]

    def set_modifier_option_recipe(
        self,
        modifier_option_id: int,
        entries: list[tuple[int, int]],
        *,
        actor: UserAccount | None = None,
    ) -> list[RecipeEntryRecord]:
        deduped: dict[int, int] = {}
        for ing_id, qty_m in entries:
            qm = int(qty_m)
            if qm <= 0:
                raise ValueError(tr("inventory.err.qty_positive"))
            deduped[int(ing_id)] = deduped.get(int(ing_id), 0) + qm

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_INVENTORY)
            mrow = conn.execute(
                "SELECT id, name_en, name_ar FROM modifier_options WHERE id = ?;",
                (int(modifier_option_id),),
            ).fetchone()
            if mrow is None:
                raise ValueError(tr("backend.err.modifier_option_not_found", name=str(modifier_option_id)))

            for ing_id in deduped:
                if conn.execute(
                    "SELECT 1 FROM ingredients WHERE id = ?;", (ing_id,)
                ).fetchone() is None:
                    raise ValueError(tr("inventory.err.not_found"))

            conn.execute(
                "DELETE FROM modifier_option_recipes WHERE modifier_option_id = ?;",
                (int(modifier_option_id),),
            )
            for ing_id, qm in deduped.items():
                conn.execute(
                    "INSERT INTO modifier_option_recipes (modifier_option_id, ingredient_id, qty_milli) VALUES (?, ?, ?);",
                    (int(modifier_option_id), ing_id, qm),
                )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "MODIFIER_RECIPE_UPDATED",
                    actor=live_actor,
                    target_type="modifier_option",
                    target_id=str(modifier_option_id),
                    details=f"Updated recipe for modifier '{mrow['name_en'] or mrow['name_ar']}' ({len(deduped)} ingredients)",
                )
        return self.get_modifier_option_recipe(modifier_option_id)

    def get_low_stock_ingredients(self) -> list[IngredientRecord]:
        """Return active tracked ingredients at or below threshold, or [] when tracking is disabled."""
        with self._tx() as conn:
            cfg_row = conn.execute(
                "SELECT inventory_tracking_enabled FROM business_settings WHERE id = 1;"
            ).fetchone()
            if cfg_row is not None and not bool(int(cfg_row["inventory_tracking_enabled"])):
                return []
            rows = conn.execute(
                """
                SELECT * FROM ingredients
                WHERE is_archived = 0 AND is_active = 1 AND track_stock = 1
                  AND stock_qty_milli <= min_threshold_milli
                ORDER BY (stock_qty_milli - min_threshold_milli) ASC,
                         COALESCE(NULLIF(name_en, ''), name_ar) COLLATE NOCASE ASC;
                """
            ).fetchall()
        return [_row_to_ingredient(r) for r in rows]

    def _compute_and_deduct_sale_inventory_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        order_id: int,
        order_number: str,
        order_items_payload: list[tuple[int, int | None, int, tuple[int, ...]]],
        now_iso: str,
        actor: UserAccount | None,
    ) -> tuple[int, bool]:
        """Deduct product + modifier recipes atomically and compute reliable order COGS estimate."""
        cfg_row = conn.execute(
            "SELECT inventory_tracking_enabled, auto_deduct_inventory FROM business_settings WHERE id = 1;"
        ).fetchone()
        tracking_on = (
            True
            if cfg_row is None
            else (
                bool(int(cfg_row["inventory_tracking_enabled"]))
                and bool(int(cfg_row["auto_deduct_inventory"]))
            )
        )

        order_cost_total_minor = 0
        all_items_costed = bool(order_items_payload)

        for order_item_id, product_id, quantity, mod_option_ids in order_items_payload:
            per_ing_milli: dict[int, int] = {}
            if product_id is not None:
                p_recs = conn.execute(
                    "SELECT ingredient_id, qty_milli FROM product_recipes WHERE product_id = ?;",
                    (int(product_id),),
                ).fetchall()
                for pr in p_recs:
                    iid = int(pr["ingredient_id"])
                    per_ing_milli[iid] = per_ing_milli.get(iid, 0) + int(pr["qty_milli"])

            for opt_id in mod_option_ids:
                m_recs = conn.execute(
                    "SELECT ingredient_id, qty_milli FROM modifier_option_recipes WHERE modifier_option_id = ?;",
                    (int(opt_id),),
                ).fetchall()
                for mr in m_recs:
                    iid = int(mr["ingredient_id"])
                    per_ing_milli[iid] = per_ing_milli.get(iid, 0) + int(mr["qty_milli"])

            if not per_ing_milli:
                all_items_costed = False
                continue

            item_unit_cost = 0
            item_all_ing_costed = True
            for iid, unit_milli in per_ing_milli.items():
                irow = conn.execute(
                    "SELECT id, stock_qty_milli, cost_per_unit_minor, track_stock, is_archived FROM ingredients WHERE id = ?;",
                    (iid,),
                ).fetchone()
                if irow is None:
                    item_all_ing_costed = False
                    continue
                unit_cost_minor = int(irow["cost_per_unit_minor"] or 0)
                if unit_cost_minor > 0:
                    item_unit_cost += int(round((unit_milli * unit_cost_minor) / 1000.0))
                else:
                    item_all_ing_costed = False

                if tracking_on and bool(int(irow["track_stock"])) and not bool(int(irow["is_archived"])):
                    ded_qty = unit_milli * int(quantity)
                    ded_key = f"sale:{order_id}:{order_item_id}:{iid}"
                    if (
                        conn.execute(
                            "SELECT 1 FROM stock_movements WHERE client_deduction_key = ? LIMIT 1;",
                            (ded_key,),
                        ).fetchone()
                        is None
                    ):
                        new_bal = int(irow["stock_qty_milli"] or 0) - ded_qty
                        conn.execute(
                            "UPDATE ingredients SET stock_qty_milli = ?, updated_at = ? WHERE id = ?;",
                            (new_bal, now_iso, iid),
                        )
                        conn.execute(
                            """
                            INSERT INTO stock_movements (
                                ingredient_id, movement_type, delta_qty_milli, balance_after_milli,
                                unit_cost_minor, reference_type, reference_id, client_deduction_key,
                                notes, actor_user_id, actor_name, created_at
                            ) VALUES (?, 'SALE_DEDUCTION', ?, ?, ?, 'order', ?, ?, ?, ?, ?, ?);
                            """,
                            (
                                iid,
                                -ded_qty,
                                new_bal,
                                unit_cost_minor,
                                order_number,
                                ded_key,
                                f"Order {order_number}",
                                actor.id if actor else None,
                                actor.label if actor else "",
                                now_iso,
                            ),
                        )

            if item_all_ing_costed and item_unit_cost > 0:
                order_cost_total_minor += item_unit_cost * int(quantity)
                conn.execute(
                    "UPDATE order_items SET estimated_unit_cost_minor = ? WHERE id = ?;",
                    (item_unit_cost, int(order_item_id)),
                )
            else:
                all_items_costed = False

        order_has_cost_data = bool(all_items_costed and order_items_payload)
        if order_has_cost_data:
            conn.execute(
                "UPDATE orders SET estimated_cost_minor = ?, has_cost_data = 1 WHERE id = ?;",
                (order_cost_total_minor, int(order_id)),
            )
        elif order_cost_total_minor > 0:
            conn.execute(
                "UPDATE orders SET estimated_cost_minor = ?, has_cost_data = 0 WHERE id = ?;",
                (order_cost_total_minor, int(order_id)),
            )
        return order_cost_total_minor, order_has_cost_data

    def _apply_refund_inventory_in_tx(
        self,
        conn: sqlite3.Connection,
        *,
        refund_id: int,
        order_id: int,
        order_item_id: int,
        product_id: int | None,
        refund_qty: int,
        mod_option_ids: tuple[int, ...],
        stock_action: str,
        now_iso: str,
        actor: UserAccount | None,
    ) -> None:
        if stock_action == "NONE":
            return
        per_ing_milli: dict[int, int] = {}
        if product_id is not None:
            for pr in conn.execute(
                "SELECT ingredient_id, qty_milli FROM product_recipes WHERE product_id = ?;",
                (int(product_id),),
            ).fetchall():
                iid = int(pr["ingredient_id"])
                per_ing_milli[iid] = per_ing_milli.get(iid, 0) + int(pr["qty_milli"])
        for opt_id in mod_option_ids:
            for mr in conn.execute(
                "SELECT ingredient_id, qty_milli FROM modifier_option_recipes WHERE modifier_option_id = ?;",
                (int(opt_id),),
            ).fetchall():
                iid = int(mr["ingredient_id"])
                per_ing_milli[iid] = per_ing_milli.get(iid, 0) + int(mr["qty_milli"])

        for iid, unit_milli in per_ing_milli.items():
            irow = conn.execute(
                "SELECT id, stock_qty_milli, cost_per_unit_minor, track_stock FROM ingredients WHERE id = ?;",
                (iid,),
            ).fetchone()
            if irow is None or not bool(int(irow["track_stock"])):
                continue
            qty_m = unit_milli * int(refund_qty)
            if stock_action == "RETURNED":
                new_bal = int(irow["stock_qty_milli"] or 0) + qty_m
                conn.execute(
                    "UPDATE ingredients SET stock_qty_milli = ?, updated_at = ? WHERE id = ?;",
                    (new_bal, now_iso, iid),
                )
                conn.execute(
                    """
                    INSERT INTO stock_movements (
                        ingredient_id, movement_type, delta_qty_milli, balance_after_milli,
                        unit_cost_minor, reference_type, reference_id, client_deduction_key,
                        notes, actor_user_id, actor_name, created_at
                    ) VALUES (?, 'REFUND_RETURN', ?, ?, ?, 'refund', ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        iid,
                        qty_m,
                        new_bal,
                        int(irow["cost_per_unit_minor"] or 0),
                        str(refund_id),
                        f"refund:{refund_id}:{order_item_id}:{iid}",
                        f"Returned from refund #{refund_id}",
                        actor.id if actor else None,
                        actor.label if actor else "",
                        now_iso,
                    ),
                )
            elif stock_action == "WASTE":
                # Already deducted at sale time; log waste attribution without double-subtracting stock
                cur_bal = int(irow["stock_qty_milli"] or 0)
                conn.execute(
                    """
                    INSERT INTO stock_movements (
                        ingredient_id, movement_type, delta_qty_milli, balance_after_milli,
                        unit_cost_minor, reference_type, reference_id, client_deduction_key,
                        notes, actor_user_id, actor_name, created_at
                    ) VALUES (?, 'WASTE', 0, ?, ?, 'refund', ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        iid,
                        cur_bal,
                        int(irow["cost_per_unit_minor"] or 0),
                        str(refund_id),
                        f"refund_waste:{refund_id}:{order_item_id}:{iid}",
                        f"Refund #{refund_id} marked waste ({format_qty_milli(qty_m)} consumed)",
                        actor.id if actor else None,
                        actor.label if actor else "",
                        now_iso,
                    ),
                )
