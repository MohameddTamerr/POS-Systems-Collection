from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .auth import AuthorizationError, Permission, UserAccount
from .i18n import tr, tr_category
from .payments import METHOD_CASH, validate_money_minor

EXPENSE_CATEGORIES = (
    "RENT",
    "ELECTRICITY",
    "UTILITIES",
    "SUPPLIES",
    "MEALS",
    "CLEANING",
    "MAINTENANCE",
    "MARKETING",
    "OTHER",
)
SHIFT_MOVEMENT_TYPES = ("CASH_IN", "CASH_OUT")
REFUND_STOCK_ACTIONS = ("NONE", "RETURNED", "WASTE")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso_local_dt(iso_str: str) -> datetime | None:
    raw = (iso_str or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except ValueError:
        return None


@dataclass(slots=True)
class ShiftCashMovementRecord:
    id: int
    shift_id: int
    movement_type: str
    amount_minor: int
    reason: str
    actor_user_id: int | None
    actor_name: str
    created_at: str


@dataclass(slots=True)
class ShiftRecord:
    id: int
    user_id: int | None
    cashier_name: str
    currency: str
    status: str
    opened_at: str
    closed_at: str
    opening_cash_minor: int
    counted_cash_minor: int
    expected_cash_minor: int
    discrepancy_minor: int
    cash_sales_minor: int
    digital_sales_minor: int
    cash_refunds_minor: int
    cash_in_minor: int
    cash_out_minor: int
    order_count: int
    notes: str
    closed_by_user_id: int | None
    closed_by_name: str
    movements: tuple[ShiftCashMovementRecord, ...] = ()


@dataclass(slots=True)
class ExpenseRecord:
    id: int
    category: str
    title: str
    amount_minor: int
    currency: str
    expense_date: str
    notes: str
    created_by_user_id: int | None
    created_by_name: str
    created_at: str
    updated_at: str


@dataclass(slots=True)
class DashboardBucketPoint:
    key: str
    label: str
    revenue_minor: int
    refunds_minor: int
    expenses_minor: int
    order_count: int


@dataclass(slots=True)
class BreakdownPoint:
    key: str
    label: str
    amount_minor: int
    count: int


@dataclass(slots=True)
class ExtendedAnalyticsSummary:
    start_date: str
    end_date: str
    bucket_mode: str
    gross_sales_minor: int
    discounts_total_minor: int
    refunds_total_minor: int
    net_revenue_minor: int
    today_revenue_minor: int
    today_order_count: int
    order_count: int
    items_sold: int
    average_order_minor: int
    cash_sales_minor: int
    digital_sales_minor: int
    expenses_total_minor: int
    estimated_cogs_minor: int
    estimated_gross_profit_minor: int
    has_reliable_cost_data: bool
    cost_covered_orders: int
    buckets: list[DashboardBucketPoint] = field(default_factory=list)
    payment_breakdown: list[BreakdownPoint] = field(default_factory=list)
    category_breakdown: list[BreakdownPoint] = field(default_factory=list)
    top_products: list[BreakdownPoint] = field(default_factory=list)
    cashier_breakdown: list[BreakdownPoint] = field(default_factory=list)
    order_type_breakdown: list[BreakdownPoint] = field(default_factory=list)
    expense_breakdown: list[BreakdownPoint] = field(default_factory=list)
    recent_orders: list[Any] = field(default_factory=list)


class DatabaseFinanceMixin:
    """Cashier shifts, cash movements, itemized refunds, expenses, and interactive dashboard analytics."""

    # ---------------- Cashier Shifts ----------------

    def _compute_shift_totals_in_tx(
        self, conn: sqlite3.Connection, shift_id: int, opening_cash_minor: int
    ) -> dict[str, int]:
        cash_sales = 0
        digital_sales = 0
        order_count = 0

        ord_rows = conn.execute(
            "SELECT id, total_minor, cash_received_minor, change_minor FROM orders WHERE shift_id = ?;",
            (int(shift_id),),
        ).fetchall()
        order_count = len(ord_rows)
        order_ids = [int(r["id"]) for r in ord_rows]

        if order_ids:
            placeholders = ",".join("?" for _ in order_ids)
            pay_rows = conn.execute(
                f"SELECT order_id, payment_method, payment_amount_minor FROM payments WHERE order_id IN ({placeholders});",
                tuple(order_ids),
            ).fetchall()
            paid_orders: set[int] = set()
            for pr in pay_rows:
                oid = int(pr["order_id"])
                paid_orders.add(oid)
                amt = int(pr["payment_amount_minor"] or 0)
                if str(pr["payment_method"] or "").upper() == METHOD_CASH:
                    cash_sales += amt
                else:
                    digital_sales += amt
            for r in ord_rows:
                if int(r["id"]) not in paid_orders:
                    cash_sales += max(0, int(r["cash_received_minor"] or 0) - int(r["change_minor"] or 0))

        cash_refunds_row = conn.execute(
            """
            SELECT COALESCE(SUM(refund_amount_minor), 0) AS s
            FROM order_refunds
            WHERE shift_id = ? AND refund_method = 'CASH';
            """,
            (int(shift_id),),
        ).fetchone()
        cash_refunds = int(cash_refunds_row["s"] or 0) if cash_refunds_row else 0

        mv_rows = conn.execute(
            "SELECT movement_type, amount_minor FROM shift_cash_movements WHERE shift_id = ?;",
            (int(shift_id),),
        ).fetchall()
        cash_in = 0
        cash_out = 0
        for mv in mv_rows:
            if mv["movement_type"] == "CASH_IN":
                cash_in += int(mv["amount_minor"] or 0)
            else:
                cash_out += int(mv["amount_minor"] or 0)

        expected_cash = int(opening_cash_minor) + cash_sales - cash_refunds + cash_in - cash_out
        return {
            "cash_sales_minor": cash_sales,
            "digital_sales_minor": digital_sales,
            "cash_refunds_minor": cash_refunds,
            "cash_in_minor": cash_in,
            "cash_out_minor": cash_out,
            "expected_cash_minor": expected_cash,
            "order_count": order_count,
        }

    def _hydrate_shift_in_tx(
        self, conn: sqlite3.Connection, row: sqlite3.Row, *, include_movements: bool = False
    ) -> ShiftRecord:
        sid = int(row["id"])
        opening = int(row["opening_cash_minor"] or 0)
        totals = self._compute_shift_totals_in_tx(conn, sid, opening)
        is_closed = str(row["status"]) == "CLOSED"
        expected = int(row["expected_cash_minor"]) if is_closed else totals["expected_cash_minor"]
        counted = int(row["counted_cash_minor"] or 0) if is_closed else 0
        discrepancy = (counted - expected) if is_closed else 0

        movements: tuple[ShiftCashMovementRecord, ...] = ()
        if include_movements:
            mrows = conn.execute(
                "SELECT * FROM shift_cash_movements WHERE shift_id = ? ORDER BY id DESC;",
                (sid,),
            ).fetchall()
            movements = tuple(
                ShiftCashMovementRecord(
                    id=int(m["id"]),
                    shift_id=sid,
                    movement_type=str(m["movement_type"]),
                    amount_minor=int(m["amount_minor"]),
                    reason=str(m["reason"] or ""),
                    actor_user_id=(
                        int(m["actor_user_id"]) if m["actor_user_id"] is not None else None
                    ),
                    actor_name=str(m["actor_name"] or ""),
                    created_at=str(m["created_at"] or ""),
                )
                for m in mrows
            )

        return ShiftRecord(
            id=sid,
            user_id=int(row["user_id"]) if row["user_id"] is not None else None,
            cashier_name=str(row["cashier_name"] or ""),
            currency=str(row["currency"] or "USD"),
            status=str(row["status"] or "OPEN"),
            opened_at=str(row["opened_at"] or ""),
            closed_at=str(row["closed_at"] or ""),
            opening_cash_minor=opening,
            counted_cash_minor=counted,
            expected_cash_minor=expected,
            discrepancy_minor=discrepancy,
            cash_sales_minor=totals["cash_sales_minor"],
            digital_sales_minor=totals["digital_sales_minor"],
            cash_refunds_minor=totals["cash_refunds_minor"],
            cash_in_minor=totals["cash_in_minor"],
            cash_out_minor=totals["cash_out_minor"],
            order_count=totals["order_count"],
            notes=str(row["notes"] or ""),
            closed_by_user_id=(
                int(row["closed_by_user_id"]) if row["closed_by_user_id"] is not None else None
            ),
            closed_by_name=str(row["closed_by_name"] or ""),
            movements=movements,
        )

    def get_active_shift(self, user_id: int | None = None, *, actor: UserAccount | None = None) -> ShiftRecord | None:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, None)
            target_uid = user_id if user_id is not None else (live_actor.id if live_actor else None)
            if target_uid is None:
                return None
            row = conn.execute(
                "SELECT * FROM cashier_shifts WHERE user_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1;",
                (int(target_uid),),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_shift_in_tx(conn, row, include_movements=True)

    def get_shift(self, shift_id: int, *, actor: UserAccount | None = None) -> ShiftRecord | None:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_OWN_SHIFT)
            row = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (int(shift_id),)
            ).fetchone()
            if row is None:
                return None
            if (
                live_actor is not None
                and not live_actor.is_admin
                and row["user_id"] != live_actor.id
            ):
                raise AuthorizationError(tr("shift.err.own_only"))
            return self._hydrate_shift_in_tx(conn, row, include_movements=True)

    def list_shifts(
        self,
        *,
        user_id: int | None = None,
        status: str | None = None,
        limit: int = 80,
        actor: UserAccount | None = None,
    ) -> list[ShiftRecord]:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_OWN_SHIFT)
            sql = "SELECT * FROM cashier_shifts WHERE 1=1"
            params: list[object] = []
            if live_actor is not None and not live_actor.is_admin:
                sql += " AND user_id = ?"
                params.append(live_actor.id)
            elif user_id is not None:
                sql += " AND user_id = ?"
                params.append(int(user_id))
            if status in ("OPEN", "CLOSED"):
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY id DESC LIMIT ?;"
            params.append(max(1, min(500, int(limit))))
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._hydrate_shift_in_tx(conn, r, include_movements=False) for r in rows]

    def open_shift(
        self,
        opening_cash_minor: int = 0,
        *,
        notes: str = "",
        target_user_id: int | None = None,
        actor: UserAccount | None = None,
    ) -> ShiftRecord:
        open_minor = validate_money_minor(opening_cash_minor, field_label="Opening Cash")
        clean_notes = " ".join((notes or "").strip().split())[:200]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_OWN_SHIFT)
            if live_actor is None:
                raise AuthorizationError(tr("backend.err.auth_required"))
            owner_id = live_actor.id
            owner_name = live_actor.label
            if target_user_id is not None and int(target_user_id) != live_actor.id:
                if not live_actor.is_admin:
                    raise AuthorizationError(tr("shift.err.own_only"))
                urow = conn.execute(
                    "SELECT id, username, display_name FROM users WHERE id = ? AND is_active = 1;",
                    (int(target_user_id),),
                ).fetchone()
                if urow is None:
                    raise ValueError(tr("shift.err.user_not_found"))
                owner_id = int(urow["id"])
                owner_name = (urow["display_name"] or urow["username"] or "").strip()

            existing = conn.execute(
                "SELECT id FROM cashier_shifts WHERE user_id = ? AND status = 'OPEN' LIMIT 1;",
                (owner_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(tr("shift.err.already_open"))

            srow = conn.execute(
                "SELECT currency, next_shift_seq FROM business_settings WHERE id = 1;"
            ).fetchone()
            currency = str(srow["currency"] or "USD") if srow else "USD"
            shift_seq = int(srow["next_shift_seq"] or 101) if srow and "next_shift_seq" in srow.keys() else 101
            shift_number = f"SHF-{shift_seq}"
            conn.execute(
                "UPDATE business_settings SET next_shift_seq = ? WHERE id = 1;",
                (shift_seq + 1,),
            )
            cur = conn.execute(
                """
                INSERT INTO cashier_shifts (
                    shift_number, user_id, cashier_name, currency, status, opened_at,
                    opening_cash_minor, expected_cash_minor, notes
                ) VALUES (?, ?, ?, ?, 'OPEN', ?, ?, ?, ?);
                """,
                (shift_number, owner_id, owner_name, currency, now, open_minor, open_minor, clean_notes),
            )
            shift_id = int(cur.lastrowid)
            self._write_audit(
                conn,
                "SHIFT_OPENED",
                actor=live_actor,
                target_type="shift",
                target_id=str(shift_id),
                details=f"Opened shift #{shift_id} for {owner_name} (opening_minor={open_minor})",
            )
            row = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (shift_id,)
            ).fetchone()
            assert row is not None
            return self._hydrate_shift_in_tx(conn, row, include_movements=True)

    def record_shift_cash_movement(
        self,
        shift_id: int,
        movement_type: str,
        amount_minor: int,
        *,
        reason: str = "",
        actor: UserAccount | None = None,
    ) -> ShiftRecord:
        mtype = (movement_type or "").strip().upper()
        if mtype not in SHIFT_MOVEMENT_TYPES:
            raise ValueError(f"Unsupported shift cash movement '{movement_type}'.")
        amt = int(amount_minor)
        if amt <= 0:
            raise ValueError(tr("shift.err.movement_positive"))
        clean_reason = " ".join((reason or "").strip().split())[:180]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_OWN_SHIFT)
            if live_actor is None:
                raise AuthorizationError(tr("backend.err.auth_required"))
            srow = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (int(shift_id),)
            ).fetchone()
            if srow is None:
                raise ValueError(tr("shift.err.not_found"))
            if srow["status"] != "OPEN":
                raise ValueError(tr("shift.err.already_closed"))
            if not live_actor.is_admin and srow["user_id"] != live_actor.id:
                raise AuthorizationError(tr("shift.err.own_only"))

            conn.execute(
                """
                INSERT INTO shift_cash_movements (
                    shift_id, movement_type, amount_minor, currency, reason, actor_user_id, actor_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    int(shift_id),
                    mtype,
                    amt,
                    str(srow["currency"] or "USD"),
                    clean_reason,
                    live_actor.id,
                    live_actor.label,
                    now,
                ),
            )
            totals = self._compute_shift_totals_in_tx(
                conn, int(shift_id), int(srow["opening_cash_minor"] or 0)
            )
            conn.execute(
                "UPDATE cashier_shifts SET expected_cash_minor = ? WHERE id = ?;",
                (totals["expected_cash_minor"], int(shift_id)),
            )
            self._write_audit(
                conn,
                f"SHIFT_{mtype}",
                actor=live_actor,
                target_type="shift",
                target_id=str(shift_id),
                details=f"{mtype} minor={amt} on shift #{shift_id}: {clean_reason}",
            )
            updated = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (int(shift_id),)
            ).fetchone()
            assert updated is not None
            return self._hydrate_shift_in_tx(conn, updated, include_movements=True)

    def close_shift(
        self,
        shift_id: int,
        counted_cash_minor: int,
        *,
        notes: str = "",
        actor: UserAccount | None = None,
    ) -> ShiftRecord:
        counted = validate_money_minor(counted_cash_minor, field_label="Counted Cash")
        clean_notes = " ".join((notes or "").strip().split())[:200]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_OWN_SHIFT)
            if live_actor is None:
                raise AuthorizationError(tr("backend.err.auth_required"))
            srow = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (int(shift_id),)
            ).fetchone()
            if srow is None:
                raise ValueError(tr("shift.err.not_found"))
            if srow["status"] != "OPEN":
                raise ValueError(tr("shift.err.already_closed"))
            if not live_actor.is_admin and srow["user_id"] != live_actor.id:
                raise AuthorizationError(tr("shift.err.own_only"))

            totals = self._compute_shift_totals_in_tx(
                conn, int(shift_id), int(srow["opening_cash_minor"] or 0)
            )
            expected = totals["expected_cash_minor"]
            discrepancy = counted - expected
            merged_notes = clean_notes or str(srow["notes"] or "")
            conn.execute(
                """
                UPDATE cashier_shifts
                SET status = 'CLOSED', closed_at = ?, counted_cash_minor = ?,
                    expected_cash_minor = ?, discrepancy_minor = ?, notes = ?,
                    closed_by_user_id = ?, closed_by_name = ?
                WHERE id = ?;
                """,
                (
                    now,
                    counted,
                    expected,
                    discrepancy,
                    merged_notes,
                    live_actor.id,
                    live_actor.label,
                    int(shift_id),
                ),
            )
            self._write_audit(
                conn,
                "SHIFT_CLOSED",
                actor=live_actor,
                target_type="shift",
                target_id=str(shift_id),
                details=(
                    f"Closed shift #{shift_id}: expected={expected}, counted={counted}, "
                    f"discrepancy={discrepancy}"
                ),
            )
            closed_row = conn.execute(
                "SELECT * FROM cashier_shifts WHERE id = ?;", (int(shift_id),)
            ).fetchone()
            assert closed_row is not None
            return self._hydrate_shift_in_tx(conn, closed_row, include_movements=True)

    def export_shift_csv(
        self, shift_id: int, dest_path: Path, *, actor: UserAccount | None = None
    ) -> Path:
        shift = self.get_shift(shift_id, actor=actor)
        if shift is None:
            raise ValueError(tr("shift.err.not_found"))
        out = Path(dest_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Field", "Value"])
            writer.writerow(["Shift ID", shift.id])
            writer.writerow(["Cashier", shift.cashier_name])
            writer.writerow(["Status", shift.status])
            writer.writerow(["Opened At", shift.opened_at])
            writer.writerow(["Closed At", shift.closed_at])
            writer.writerow(["Currency", shift.currency])
            writer.writerow(["Orders", shift.order_count])
            writer.writerow(["Opening Cash (minor)", shift.opening_cash_minor])
            writer.writerow(["Cash Sales (minor)", shift.cash_sales_minor])
            writer.writerow(["Digital Sales (minor)", shift.digital_sales_minor])
            writer.writerow(["Cash Refunds (minor)", shift.cash_refunds_minor])
            writer.writerow(["Cash In (minor)", shift.cash_in_minor])
            writer.writerow(["Cash Out (minor)", shift.cash_out_minor])
            writer.writerow(["Expected Cash (minor)", shift.expected_cash_minor])
            writer.writerow(["Counted Cash (minor)", shift.counted_cash_minor])
            writer.writerow(["Discrepancy (minor)", shift.discrepancy_minor])
            writer.writerow([])
            writer.writerow(["Movement ID", "Type", "Amount (minor)", "Reason", "Actor", "Timestamp"])
            for mv in shift.movements:
                writer.writerow(
                    [mv.id, mv.movement_type, mv.amount_minor, mv.reason, mv.actor_name, mv.created_at]
                )
        return out

    # ---------------- Full & Partial Itemized Refunds ----------------

    def get_refundable_item_quantities(self, order_id: int) -> dict[int, int]:
        """Return map {order_item_id: remaining_refundable_qty} cleanly."""
        with self._tx() as conn:
            irows = conn.execute(
                "SELECT id, quantity FROM order_items WHERE order_id = ?;", (int(order_id),)
            ).fetchall()
            rrows = conn.execute(
                """
                SELECT ori.order_item_id, COALESCE(SUM(ori.quantity), 0) AS ref_qty
                FROM order_refund_items ori
                JOIN order_refunds r ON r.id = ori.refund_id
                WHERE r.order_id = ?
                GROUP BY ori.order_item_id;
                """,
                (int(order_id),),
            ).fetchall()
            ref_by_item = {int(r["order_item_id"]): int(r["ref_qty"] or 0) for r in rrows}
            out: dict[int, int] = {}
            for ir in irows:
                iid = int(ir["id"])
                out[iid] = max(0, int(ir["quantity"]) - ref_by_item.get(iid, 0))
            return out

    def create_order_refund(
        self,
        order_id: int,
        items: list[dict[str, Any]],
        *,
        refund_method: str = "CASH",
        reason: str = "",
        notes: str = "",
        actor: UserAccount | None = None,
    ) -> int:
        if not items:
            raise ValueError(tr("refund.err.select_items"))
        method_norm = (refund_method or "CASH").strip().upper()
        if method_norm not in ("CASH", "EXTERNAL", "CARD", "INSTAPAY", "EWALLET", "APPLE_PAY", "CUSTOM"):
            raise ValueError(f"Unsupported refund method '{refund_method}'.")
        clean_reason = " ".join((reason or "").strip().split())[:120]
        clean_notes = " ".join((notes or "").strip().split())[:200]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.VIEW_OWN_SALES)
            if live_actor is not None and not live_actor.is_admin:
                cfg_ref = conn.execute(
                    "SELECT allow_cashier_refunds FROM business_settings WHERE id = 1;"
                ).fetchone()
                if not (cfg_ref and bool(int(cfg_ref["allow_cashier_refunds"]))):
                    raise AuthorizationError(tr("refund.err.admin_only"))
            orow = conn.execute(
                "SELECT id, order_number, total_minor, subtotal_minor, discount_minor, tax_minor, refunded_total_minor, estimated_cost_minor, currency FROM orders WHERE id = ?;",
                (int(order_id),),
            ).fetchone()
            if orow is None:
                raise ValueError(tr("refund.err.order_not_found"))

            order_total = int(orow["total_minor"] or 0)
            already_refunded = int(orow["refunded_total_minor"] or 0)
            order_est_cost = int(orow["estimated_cost_minor"] or 0) if "estimated_cost_minor" in orow.keys() else 0
            remaining_order_cap = max(0, order_total - already_refunded)
            if remaining_order_cap <= 0:
                raise ValueError(tr("refund.err.already_fully_refunded"))

            irows = conn.execute(
                "SELECT id, product_id, product_name, quantity, unit_price_minor, line_total_minor, estimated_unit_cost_minor FROM order_items WHERE order_id = ?;",
                (int(order_id),),
            ).fetchall()
            item_map = {int(ir["id"]): ir for ir in irows}

            prev_rrows = conn.execute(
                """
                SELECT ori.order_item_id, COALESCE(SUM(ori.quantity), 0) AS ref_qty
                FROM order_refund_items ori
                JOIN order_refunds r ON r.id = ori.refund_id
                WHERE r.order_id = ?
                GROUP BY ori.order_item_id;
                """,
                (int(order_id),),
            ).fetchall()
            prev_ref_qty = {int(r["order_item_id"]): int(r["ref_qty"] or 0) for r in prev_rrows}

            # Ratio to ensure discounted orders refund exact net proportional total cleanly
            order_sub = max(1, int(orow["subtotal_minor"] or 0))
            validated_entries: list[tuple[int, int | None, int, int, str, tuple[int, ...]]] = []
            raw_refund_sum = 0
            all_remaining_selected = True

            requested_qty_by_item: dict[int, int] = {}
            for entry in items:
                oid_item = int(entry["order_item_id"])
                req_qty = int(entry["quantity"])
                if oid_item not in item_map:
                    raise ValueError(tr("refund.err.invalid_item"))
                if req_qty <= 0:
                    raise ValueError(tr("refund.err.qty_positive"))
                requested_qty_by_item[oid_item] = requested_qty_by_item.get(oid_item, 0) + req_qty

            for iid, ir in item_map.items():
                avail = max(0, int(ir["quantity"]) - prev_ref_qty.get(iid, 0))
                if requested_qty_by_item.get(iid, 0) < avail:
                    all_remaining_selected = False
                if requested_qty_by_item.get(iid, 0) > avail:
                    raise ValueError(
                        tr("refund.err.qty_exceeds", name=ir["product_name"], avail=avail)
                    )

            for entry in items:
                oid_item = int(entry["order_item_id"])
                req_qty = int(entry["quantity"])
                action = str(entry.get("stock_action", "NONE") or "NONE").strip().upper()
                if action not in REFUND_STOCK_ACTIONS:
                    action = "NONE"
                ir = item_map[oid_item]
                sold_qty = max(1, int(ir["quantity"]))
                line_net_share = (int(ir["line_total_minor"]) * order_total) / order_sub
                line_refund = int(round((line_net_share * req_qty) / sold_qty))
                raw_refund_sum += max(0, line_refund)
                mod_rows = conn.execute(
                    "SELECT modifier_option_id FROM order_item_modifiers WHERE order_item_id = ? AND modifier_option_id IS NOT NULL;",
                    (oid_item,),
                ).fetchall()
                mod_ids = tuple(int(m["modifier_option_id"]) for m in mod_rows)
                validated_entries.append(
                    (
                        oid_item,
                        int(ir["product_id"]) if ir["product_id"] is not None else None,
                        req_qty,
                        max(0, line_refund),
                        action,
                        mod_ids,
                    )
                )

            if all_remaining_selected:
                total_refund = remaining_order_cap
            else:
                total_refund = min(remaining_order_cap, max(0, raw_refund_sum))

            if total_refund <= 0 and remaining_order_cap > 0:
                total_refund = min(remaining_order_cap, sum(int(item_map[e[0]]["unit_price_minor"]) * e[2] for e in validated_entries))

            active_shift_id: int | None = None
            if live_actor is not None:
                sh = conn.execute(
                    "SELECT id FROM cashier_shifts WHERE user_id = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1;",
                    (live_actor.id,),
                ).fetchone()
                if sh is not None:
                    active_shift_id = int(sh["id"])

            bset_ref = conn.execute(
                "SELECT next_refund_seq FROM business_settings WHERE id = 1;"
            ).fetchone()
            rseq = int(bset_ref["next_refund_seq"] or 1001) if bset_ref and "next_refund_seq" in bset_ref.keys() else 1001
            refund_number = f"RFD-{rseq}"
            conn.execute(
                "UPDATE business_settings SET next_refund_seq = ? WHERE id = 1;",
                (rseq + 1,),
            )
            refund_type = "FULL" if (already_refunded + total_refund >= order_total) else "PARTIAL"
            refund_status = "CASH_RETURNED" if method_norm == "CASH" else "RECORDED_MANUAL_EXTERNAL"

            rcur = conn.execute(
                """
                INSERT INTO order_refunds (
                    refund_number, order_id, shift_id, refund_type, refund_method,
                    refund_amount_minor, currency, reason, notes, refund_status,
                    external_reference, actor_user_id, actor_name, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?);
                """,
                (
                    refund_number,
                    int(order_id),
                    active_shift_id,
                    refund_type,
                    method_norm,
                    total_refund,
                    str(orow["currency"] or "USD"),
                    clean_reason,
                    clean_notes,
                    refund_status,
                    live_actor.id if live_actor else None,
                    live_actor.label if live_actor else "",
                    now,
                ),
            )
            refund_id = int(rcur.lastrowid)

            restored_cost_total = 0
            for oid_item, prod_id, req_qty, line_ref_amt, action, mod_ids in validated_entries:
                prod_name_snap = str(item_map[oid_item]["product_name"] or "")
                if action == "RETURNED":
                    ir_row = item_map[oid_item]
                    u_cost = int(ir_row["estimated_unit_cost_minor"] or 0) if "estimated_unit_cost_minor" in ir_row.keys() else 0
                    restored_cost_total += max(0, u_cost * int(req_qty))
                conn.execute(
                    """
                    INSERT INTO order_refund_items (
                        refund_id, order_item_id, product_id, product_name, quantity, refund_amount_minor, stock_action
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (refund_id, oid_item, prod_id, prod_name_snap, req_qty, line_ref_amt, action),
                )
                self._apply_refund_inventory_in_tx(
                    conn,
                    refund_id=refund_id,
                    order_id=int(order_id),
                    order_item_id=oid_item,
                    product_id=prod_id,
                    refund_qty=req_qty,
                    mod_option_ids=mod_ids,
                    stock_action=action,
                    now_iso=now,
                    actor=live_actor,
                )

            new_est_cost = max(0, order_est_cost - restored_cost_total)
            conn.execute(
                "UPDATE orders SET refunded_total_minor = ?, estimated_cost_minor = ? WHERE id = ?;",
                (already_refunded + total_refund, new_est_cost, int(order_id)),
            )
            if active_shift_id is not None and method_norm == "CASH":
                srow = conn.execute(
                    "SELECT opening_cash_minor FROM cashier_shifts WHERE id = ?;",
                    (active_shift_id,),
                ).fetchone()
                if srow is not None:
                    totals = self._compute_shift_totals_in_tx(
                        conn, active_shift_id, int(srow["opening_cash_minor"] or 0)
                    )
                    conn.execute(
                        "UPDATE cashier_shifts SET expected_cash_minor = ? WHERE id = ?;",
                        (totals["expected_cash_minor"], active_shift_id),
                    )

            if live_actor is not None:
                self._write_audit(
                    conn,
                    "ORDER_REFUNDED",
                    actor=live_actor,
                    target_type="order",
                    target_id=str(orow["order_number"]),
                    details=(
                        f"Refund #{refund_id} on {orow['order_number']} ({method_norm}, "
                        f"minor={total_refund}): {clean_reason}"
                    ),
                )
            return refund_id

    # ---------------- Operating Expenses ----------------

    def list_expenses(
        self,
        *,
        start_date: str = "",
        end_date: str = "",
        category: str = "",
    ) -> list[ExpenseRecord]:
        sql = "SELECT * FROM expenses WHERE 1=1"
        params: list[object] = []
        if start_date:
            sql += " AND expense_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND expense_date <= ?"
            params.append(end_date)
        if category:
            sql += " AND category = ?"
            params.append(category.strip().upper())
        sql += " ORDER BY expense_date DESC, id DESC;"

        with self._tx() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            ExpenseRecord(
                id=int(r["id"]),
                category=str(r["category"]),
                title=str(r["title"]),
                amount_minor=int(r["amount_minor"]),
                currency=str(r["currency"]),
                expense_date=str(r["expense_date"]),
                notes=str(r["notes"] or ""),
                created_by_user_id=(
                    int(r["created_by_user_id"]) if r["created_by_user_id"] is not None else None
                ),
                created_by_name=str(r["created_by_name"] or ""),
                created_at=str(r["created_at"] or ""),
                updated_at=str(r["updated_at"] or ""),
            )
            for r in rows
        ]

    def create_expense(
        self,
        category: str,
        title: str,
        amount_minor: int,
        expense_date: str,
        *,
        notes: str = "",
        actor: UserAccount | None = None,
    ) -> ExpenseRecord:
        cat = (category or "").strip().upper()
        if cat not in EXPENSE_CATEGORIES:
            raise ValueError(f"Unsupported expense category '{category}'.")
        clean_title = " ".join((title or "").strip().split())[:100]
        if not clean_title:
            raise ValueError(tr("expenses.err.title_required"))
        amt = int(amount_minor)
        if amt <= 0:
            raise ValueError(tr("expenses.err.amount_positive"))
        date.fromisoformat(expense_date)
        clean_notes = " ".join((notes or "").strip().split())[:220]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_EXPENSES)
            srow = conn.execute("SELECT currency FROM business_settings WHERE id = 1;").fetchone()
            currency = str(srow["currency"] or "USD") if srow else "USD"
            cur = conn.execute(
                """
                INSERT INTO expenses (
                    category, title, amount_minor, currency, expense_date, notes,
                    created_by_user_id, created_by_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    cat,
                    clean_title,
                    amt,
                    currency,
                    expense_date,
                    clean_notes,
                    live_actor.id if live_actor else None,
                    live_actor.label if live_actor else "",
                    now,
                    now,
                ),
            )
            eid = int(cur.lastrowid)
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "EXPENSE_CREATED",
                    actor=live_actor,
                    target_type="expense",
                    target_id=str(eid),
                    details=f"Recorded expense '{clean_title}' [{cat}] minor={amt} on {expense_date}",
                )
            row = conn.execute("SELECT * FROM expenses WHERE id = ?;", (eid,)).fetchone()
            assert row is not None
            return ExpenseRecord(
                id=int(row["id"]),
                category=str(row["category"]),
                title=str(row["title"]),
                amount_minor=int(row["amount_minor"]),
                currency=str(row["currency"]),
                expense_date=str(row["expense_date"]),
                notes=str(row["notes"] or ""),
                created_by_user_id=(
                    int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
                ),
                created_by_name=str(row["created_by_name"] or ""),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )

    def update_expense(
        self,
        expense_id: int,
        *,
        category: str,
        title: str,
        amount_minor: int,
        expense_date: str,
        notes: str = "",
        actor: UserAccount | None = None,
    ) -> ExpenseRecord:
        cat = (category or "").strip().upper()
        if cat not in EXPENSE_CATEGORIES:
            raise ValueError(f"Unsupported expense category '{category}'.")
        clean_title = " ".join((title or "").strip().split())[:100]
        if not clean_title:
            raise ValueError(tr("expenses.err.title_required"))
        amt = int(amount_minor)
        if amt <= 0:
            raise ValueError(tr("expenses.err.amount_positive"))
        date.fromisoformat(expense_date)
        clean_notes = " ".join((notes or "").strip().split())[:220]
        now = _utc_now_iso()

        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_EXPENSES)
            existing = conn.execute(
                "SELECT id FROM expenses WHERE id = ?;", (int(expense_id),)
            ).fetchone()
            if existing is None:
                raise ValueError(tr("expenses.err.not_found"))
            conn.execute(
                """
                UPDATE expenses
                SET category = ?, title = ?, amount_minor = ?, expense_date = ?, notes = ?, updated_at = ?
                WHERE id = ?;
                """,
                (cat, clean_title, amt, expense_date, clean_notes, now, int(expense_id)),
            )
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "EXPENSE_UPDATED",
                    actor=live_actor,
                    target_type="expense",
                    target_id=str(expense_id),
                    details=f"Updated expense #{expense_id} '{clean_title}' [{cat}] minor={amt}",
                )
            row = conn.execute(
                "SELECT * FROM expenses WHERE id = ?;", (int(expense_id),)
            ).fetchone()
            assert row is not None
            return ExpenseRecord(
                id=int(row["id"]),
                category=str(row["category"]),
                title=str(row["title"]),
                amount_minor=int(row["amount_minor"]),
                currency=str(row["currency"]),
                expense_date=str(row["expense_date"]),
                notes=str(row["notes"] or ""),
                created_by_user_id=(
                    int(row["created_by_user_id"]) if row["created_by_user_id"] is not None else None
                ),
                created_by_name=str(row["created_by_name"] or ""),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"] or ""),
            )

    def delete_expense(self, expense_id: int, *, actor: UserAccount | None = None) -> None:
        with self._tx() as conn:
            live_actor = self._resolve_actor(conn, actor, Permission.MANAGE_EXPENSES)
            existing = conn.execute(
                "SELECT title FROM expenses WHERE id = ?;", (int(expense_id),)
            ).fetchone()
            if existing is None:
                raise ValueError(tr("expenses.err.not_found"))
            conn.execute("DELETE FROM expenses WHERE id = ?;", (int(expense_id),))
            if live_actor is not None:
                self._write_audit(
                    conn,
                    "EXPENSE_DELETED",
                    actor=live_actor,
                    target_type="expense",
                    target_id=str(expense_id),
                    details=f"Deleted expense #{expense_id} '{existing['title']}'",
                )

    # ---------------- Interactive Business Dashboard Analytics ----------------

    def get_extended_analytics(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        bucket_mode: str = "day",
        cashier_user_id: int | None = None,
        payment_method: str | None = None,
        category: str | None = None,
        actor: UserAccount | None = None,
    ) -> ExtendedAnalyticsSummary:
        from .db import OrderSummary

        now_local = datetime.now().astimezone()
        today_d = now_local.date()
        eff_end = end_date or today_d
        eff_start = start_date or (eff_end - timedelta(days=6))
        if eff_start > eff_end:
            eff_start, eff_end = eff_end, eff_start

        with self._tx() as conn:
            self._resolve_actor(conn, actor, Permission.VIEW_REPORTS)
            ord_rows = conn.execute(
                """
                SELECT id, order_number, receipt_number, created_at, currency,
                       total_minor, subtotal_minor, discount_minor, refunded_total_minor,
                       cash_received_minor, change_minor, cashier_user_id, cashier_name_snapshot,
                       order_type, table_number, customer_name, shift_id,
                       estimated_cost_minor, has_cost_data
                FROM orders
                ORDER BY id ASC;
                """
            ).fetchall()
            pay_rows = conn.execute(
                """
                SELECT id, order_id, payment_method, payment_provider,
                       custom_method_label, payment_status, external_reference,
                       payment_amount_minor
                FROM payments
                ORDER BY id ASC;
                """
            ).fetchall()
            item_rows = conn.execute(
                """
                SELECT oi.id, oi.order_id, oi.product_name, oi.quantity, oi.line_total_minor,
                       COALESCE(oi.estimated_unit_cost_minor, 0) AS est_unit_cost,
                       COALESCE(
                           NULLIF(TRIM(oi.category_snapshot), ''),
                           NULLIF(TRIM(c.name_en), ''),
                           NULLIF(TRIM(p.category), ''),
                           'Other'
                       ) AS category_name
                FROM order_items oi
                LEFT JOIN products p ON p.id = oi.product_id
                LEFT JOIN categories c ON c.id = p.category_id;
                """
            ).fetchall()
            ref_rows = conn.execute(
                "SELECT id, order_id, refund_method, refund_amount_minor FROM order_refunds;"
            ).fetchall()
            ref_item_rows = conn.execute(
                """
                SELECT ori.refund_id, ori.order_item_id, ori.refund_amount_minor,
                       ori.quantity, ori.stock_action, r.order_id, r.refund_method
                FROM order_refund_items ori
                JOIN order_refunds r ON r.id = ori.refund_id;
                """
            ).fetchall()
            exp_rows = conn.execute(
                "SELECT category, amount_minor, expense_date, created_at FROM expenses;"
            ).fetchall()

        pays_by_order: dict[int, list[sqlite3.Row]] = {}
        for pr in pay_rows:
            pays_by_order.setdefault(int(pr["order_id"]), []).append(pr)

        items_by_order: dict[int, list[sqlite3.Row]] = {}
        for ir in item_rows:
            items_by_order.setdefault(int(ir["order_id"]), []).append(ir)

        refs_by_order: dict[int, list[sqlite3.Row]] = {}
        for rr in ref_rows:
            refs_by_order.setdefault(int(rr["order_id"]), []).append(rr)

        returned_qty_by_item: dict[int, int] = {}
        ref_amt_by_item_method: dict[int, list[tuple[str, int]]] = {}
        for rir in ref_item_rows:
            oi_id = int(rir["order_item_id"])
            if str(rir["stock_action"] or "").upper() == "RETURNED":
                returned_qty_by_item[oi_id] = returned_qty_by_item.get(oi_id, 0) + int(rir["quantity"] or 0)
            ref_amt_by_item_method.setdefault(oi_id, []).append(
                (str(rir["refund_method"] or METHOD_CASH).upper(), int(rir["refund_amount_minor"] or 0))
            )

        today_rev = 0
        today_orders = 0

        gross_sales = 0
        discounts_total = 0
        refunds_total = 0
        order_count = 0
        items_sold = 0
        cash_sales = 0
        digital_sales = 0
        est_cogs = 0
        cost_covered = 0

        pay_map: dict[str, tuple[int, int]] = {}
        cat_map: dict[str, tuple[int, int]] = {}
        prod_map: dict[str, tuple[int, int]] = {}
        cashier_map: dict[str, tuple[int, int]] = {}
        otype_map: dict[str, tuple[int, int]] = {}
        matching_summaries: list[OrderSummary] = []

        bucket_rev: dict[str, int] = {}
        bucket_ref: dict[str, int] = {}
        bucket_exp: dict[str, int] = {}
        bucket_cnt: dict[str, int] = {}

        single_day = eff_start == eff_end
        b_mode = bucket_mode if bucket_mode in ("hour", "day", "week", "month") else ("hour" if single_day else "day")
        if single_day and b_mode == "day":
            b_mode = "hour"

        def _bkey(dt: datetime) -> str:
            if b_mode == "hour":
                return f"{dt.hour:02d}:00"
            if b_mode == "week":
                monday = dt.date() - timedelta(days=dt.date().weekday())
                return monday.isoformat()
            if b_mode == "month":
                return dt.strftime("%Y-%m")
            return dt.date().isoformat()

        pm_filter = (payment_method or "").strip().upper()
        cat_filter = (category or "").strip()

        def _deduct_refund_into_mix(
            *,
            ref_method: str,
            ref_amt: int,
            ord_pays: list[sqlite3.Row],
        ) -> None:
            nonlocal cash_sales, digital_sales
            if ref_amt <= 0:
                return
            m_up = (ref_method or METHOD_CASH).upper()
            if m_up == METHOD_CASH:
                cash_sales -= ref_amt
                if METHOD_CASH in pay_map:
                    prev_a, prev_c = pay_map[METHOD_CASH]
                    pay_map[METHOD_CASH] = (max(0, prev_a - ref_amt), prev_c)
            else:
                digital_sales -= ref_amt
                target_m = m_up if m_up in pay_map else ""
                if not target_m:
                    non_cash = [
                        str(p["payment_method"] or "").upper()
                        for p in ord_pays
                        if str(p["payment_method"] or "").upper() != METHOD_CASH
                    ]
                    if non_cash and non_cash[0] in pay_map:
                        target_m = non_cash[0]
                if target_m and target_m in pay_map:
                    prev_a, prev_c = pay_map[target_m]
                    pay_map[target_m] = (max(0, prev_a - ref_amt), prev_c)

        for row in ord_rows:
            dt = _parse_iso_local_dt(row["created_at"])
            if dt is None:
                continue
            d = dt.date()
            oid = int(row["id"])
            tot = int(row["total_minor"] or 0)
            ref = int(row["refunded_total_minor"] or 0)
            net_ord = max(0, tot - ref)

            if d == today_d:
                today_rev += net_ord
                today_orders += 1

            if d < eff_start or d > eff_end:
                continue
            if cashier_user_id is not None and row["cashier_user_id"] != cashier_user_id:
                continue

            o_pays = pays_by_order.get(oid, [])
            if pm_filter:
                methods_in_ord = {str(p["payment_method"] or "").upper() for p in o_pays} or {METHOD_CASH}
                if pm_filter not in methods_in_ord:
                    continue

            o_items = items_by_order.get(oid, [])
            matching_items = [ir for ir in o_items if (not cat_filter or str(ir["category_name"]) == cat_filter)]
            if cat_filter and not matching_items:
                continue

            all_items_match = (not cat_filter) or (len(matching_items) == len(o_items))
            order_sub = max(1, sum(int(ir["line_total_minor"] or 0) for ir in o_items))
            matching_sub = sum(int(ir["line_total_minor"] or 0) for ir in matching_items)
            share_ratio = 1.0 if all_items_match else (matching_sub / order_sub)

            if all_items_match:
                ord_gross = tot
                ord_disc = int(row["discount_minor"] or 0)
                ord_ref = ref
                ord_net = net_ord
                ord_has_cost = bool(int(row["has_cost_data"] or 0))
                ord_cogs = int(row["estimated_cost_minor"] or 0) if ord_has_cost else 0
            else:
                ord_gross = int(round(tot * share_ratio))
                ord_disc = int(round(int(row["discount_minor"] or 0) * share_ratio))
                item_ref_list = [
                    pair
                    for ir in matching_items
                    for pair in ref_amt_by_item_method.get(int(ir["id"]), [])
                ]
                if item_ref_list:
                    ord_ref = min(ord_gross, sum(amt for _rm, amt in item_ref_list))
                else:
                    ord_ref = min(ord_gross, int(round(ref * share_ratio)))
                ord_net = max(0, ord_gross - ord_ref)
                ord_has_cost = bool(matching_items) and all(int(ir["est_unit_cost"] or 0) > 0 for ir in matching_items)
                ord_cogs = (
                    sum(
                        int(ir["est_unit_cost"] or 0)
                        * max(0, int(ir["quantity"] or 0) - returned_qty_by_item.get(int(ir["id"]), 0))
                        for ir in matching_items
                    )
                    if ord_has_cost
                    else 0
                )

            order_count += 1
            gross_sales += ord_gross
            discounts_total += ord_disc
            refunds_total += ord_ref
            if ord_has_cost:
                est_cogs += ord_cogs
                cost_covered += 1

            bk = _bkey(dt)
            bucket_rev[bk] = bucket_rev.get(bk, 0) + ord_net
            bucket_ref[bk] = bucket_ref.get(bk, 0) + ord_ref
            bucket_cnt[bk] = bucket_cnt.get(bk, 0) + 1

            if o_pays:
                for pr in o_pays:
                    m = str(pr["payment_method"] or METHOD_CASH).upper()
                    amt = int(pr["payment_amount_minor"] or 0)
                    if not all_items_match:
                        amt = int(round(amt * share_ratio))
                    if m == METHOD_CASH:
                        cash_sales += amt
                    else:
                        digital_sales += amt
                    prev_a, prev_c = pay_map.get(m, (0, 0))
                    pay_map[m] = (prev_a + amt, prev_c + 1)
            else:
                cash_sales += ord_gross
                prev_a, prev_c = pay_map.get(METHOD_CASH, (0, 0))
                pay_map[METHOD_CASH] = (prev_a + ord_gross, prev_c + 1)

            o_refs = refs_by_order.get(oid, [])
            if all_items_match:
                for rr in o_refs:
                    _deduct_refund_into_mix(
                        ref_method=str(rr["refund_method"] or METHOD_CASH),
                        ref_amt=int(rr["refund_amount_minor"] or 0),
                        ord_pays=o_pays,
                    )
            else:
                item_ref_list = [
                    pair
                    for ir in matching_items
                    for pair in ref_amt_by_item_method.get(int(ir["id"]), [])
                ]
                if item_ref_list:
                    for rm, ramt in item_ref_list:
                        _deduct_refund_into_mix(ref_method=rm, ref_amt=ramt, ord_pays=o_pays)
                elif ord_ref > 0:
                    fallback_rm = str(o_refs[0]["refund_method"] or METHOD_CASH) if o_refs else METHOD_CASH
                    _deduct_refund_into_mix(ref_method=fallback_rm, ref_amt=ord_ref, ord_pays=o_pays)

            cname = (row["cashier_name_snapshot"] or "").strip() or tr("receipt.cashier_unspecified")
            ca, cc = cashier_map.get(cname, (0, 0))
            cashier_map[cname] = (ca + ord_net, cc + 1)

            otype = str(row["order_type"] or "DINE_IN").upper()
            oa, oc = otype_map.get(otype, (0, 0))
            otype_map[otype] = (oa + ord_net, oc + 1)

            ord_item_qty = 0
            for ir in matching_items:
                q = int(ir["quantity"] or 0)
                lt = int(ir["line_total_minor"] or 0)
                ord_item_qty += q
                items_sold += q
                cn = str(ir["category_name"])
                c_a, c_u = cat_map.get(cn, (0, 0))
                cat_map[cn] = (c_a + lt, c_u + q)
                pn = str(ir["product_name"])
                p_a, p_u = prod_map.get(pn, (0, 0))
                prod_map[pn] = (p_a + lt, p_u + q)

            rkeys = row.keys()
            first_pay = o_pays[0] if o_pays else None
            matching_summaries.append(
                OrderSummary(
                    id=oid,
                    order_number=str(row["order_number"] or ""),
                    created_at=str(row["created_at"] or ""),
                    currency=str(row["currency"] or "USD"),
                    item_count=ord_item_qty,
                    total_minor=ord_gross,
                    cash_received_minor=int(row["cash_received_minor"] or 0),
                    change_minor=int(row["change_minor"] or 0),
                    cashier_user_id=int(row["cashier_user_id"]) if row["cashier_user_id"] is not None else None,
                    cashier_name_snapshot=str(row["cashier_name_snapshot"] or ""),
                    receipt_number=(
                        str(row["receipt_number"] or row["order_number"] or "")
                        if "receipt_number" in rkeys
                        else str(row["order_number"] or "")
                    ),
                    payment_method=str(first_pay["payment_method"] or METHOD_CASH) if first_pay is not None else METHOD_CASH,
                    payment_provider=str(first_pay["payment_provider"] or "") if first_pay is not None else "",
                    custom_method_label=str(first_pay["custom_method_label"] or "") if first_pay is not None else "",
                    payment_status=str(first_pay["payment_status"] or "cash_settled") if first_pay is not None else "cash_settled",
                    external_reference=str(first_pay["external_reference"] or "") if first_pay is not None else "",
                    order_type=otype,
                    table_number=str(row["table_number"] or "") if "table_number" in rkeys else "",
                    customer_name=str(row["customer_name"] or "") if "customer_name" in rkeys else "",
                    discount_minor=ord_disc,
                    refunded_total_minor=ord_ref,
                    payment_count=max(1, len(o_pays)),
                    shift_id=int(row["shift_id"]) if "shift_id" in rkeys and row["shift_id"] is not None else None,
                )
            )

        cash_sales = max(0, cash_sales)
        digital_sales = max(0, digital_sales)

        expenses_total = 0
        exp_cat_map: dict[str, tuple[int, int]] = {}
        for er in exp_rows:
            try:
                ed = date.fromisoformat(str(er["expense_date"]))
            except ValueError:
                continue
            if eff_start <= ed <= eff_end:
                amt = int(er["amount_minor"] or 0)
                expenses_total += amt
                ec = str(er["category"])
                ea, ecnt = exp_cat_map.get(ec, (0, 0))
                exp_cat_map[ec] = (ea + amt, ecnt + 1)
                if b_mode == "hour":
                    dt_exp = _parse_iso_local_dt(er["created_at"]) if "created_at" in er.keys() else None
                    if dt_exp is not None and dt_exp.date() == ed:
                        ebk = f"{dt_exp.hour:02d}:00"
                    else:
                        ebk = "12:00"
                elif b_mode == "week":
                    ebk = (ed - timedelta(days=ed.weekday())).isoformat()
                elif b_mode == "month":
                    ebk = ed.strftime("%Y-%m")
                else:
                    ebk = ed.isoformat()
                bucket_exp[ebk] = bucket_exp.get(ebk, 0) + amt

        # Build contiguous timeline buckets
        buckets: list[DashboardBucketPoint] = []
        if b_mode == "hour":
            for h in range(0, 24):
                bk = f"{h:02d}:00"
                if bk in bucket_rev or bk in bucket_ref or bk in bucket_exp or 7 <= h <= 23:
                    dt_h = datetime(2026, 1, 1, h, 0)
                    buckets.append(
                        DashboardBucketPoint(
                            key=bk,
                            label=dt_h.strftime("%I %p").lstrip("0"),
                            revenue_minor=bucket_rev.get(bk, 0),
                            refunds_minor=bucket_ref.get(bk, 0),
                            expenses_minor=bucket_exp.get(bk, 0),
                            order_count=bucket_cnt.get(bk, 0),
                        )
                    )
        elif b_mode == "week":
            cur = eff_start - timedelta(days=eff_start.weekday())
            while cur <= eff_end:
                bk = cur.isoformat()
                buckets.append(
                    DashboardBucketPoint(
                        key=bk,
                        label=cur.strftime("%b %d").replace(" 0", " "),
                        revenue_minor=bucket_rev.get(bk, 0),
                        refunds_minor=bucket_ref.get(bk, 0),
                        expenses_minor=bucket_exp.get(bk, 0),
                        order_count=bucket_cnt.get(bk, 0),
                    )
                )
                cur += timedelta(days=7)
        elif b_mode == "month":
            seen_m: list[str] = []
            cur = eff_start.replace(day=1)
            while cur <= eff_end:
                bk = cur.strftime("%Y-%m")
                if bk not in seen_m:
                    seen_m.append(bk)
                    buckets.append(
                        DashboardBucketPoint(
                            key=bk,
                            label=cur.strftime("%b %Y"),
                            revenue_minor=bucket_rev.get(bk, 0),
                            refunds_minor=bucket_ref.get(bk, 0),
                            expenses_minor=bucket_exp.get(bk, 0),
                            order_count=bucket_cnt.get(bk, 0),
                        )
                    )
                cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            cur = eff_start
            span = (eff_end - eff_start).days
            if span > 60:
                cur = eff_end - timedelta(days=59)
            while cur <= eff_end:
                bk = cur.isoformat()
                buckets.append(
                    DashboardBucketPoint(
                        key=bk,
                        label=cur.strftime("%b %d").replace(" 0", " "),
                        revenue_minor=bucket_rev.get(bk, 0),
                        refunds_minor=bucket_ref.get(bk, 0),
                        expenses_minor=bucket_exp.get(bk, 0),
                        order_count=bucket_cnt.get(bk, 0),
                    )
                )
                cur += timedelta(days=1)

        net_revenue = max(0, gross_sales - refunds_total)
        avg_order = (net_revenue // order_count) if order_count else 0
        has_reliable_cost = order_count > 0 and cost_covered == order_count
        gross_profit = (net_revenue - est_cogs) if has_reliable_cost else 0

        return ExtendedAnalyticsSummary(
            start_date=eff_start.isoformat(),
            end_date=eff_end.isoformat(),
            bucket_mode=b_mode,
            gross_sales_minor=gross_sales,
            discounts_total_minor=discounts_total,
            refunds_total_minor=refunds_total,
            net_revenue_minor=net_revenue,
            today_revenue_minor=today_rev,
            today_order_count=today_orders,
            order_count=order_count,
            items_sold=items_sold,
            average_order_minor=avg_order,
            cash_sales_minor=cash_sales,
            digital_sales_minor=digital_sales,
            expenses_total_minor=expenses_total,
            estimated_cogs_minor=est_cogs,
            estimated_gross_profit_minor=gross_profit,
            has_reliable_cost_data=has_reliable_cost,
            cost_covered_orders=cost_covered,
            buckets=buckets,
            payment_breakdown=sorted(
                [BreakdownPoint(key=k, label=k, amount_minor=v[0], count=v[1]) for k, v in pay_map.items()],
                key=lambda x: (-x.amount_minor, x.key),
            ),
            category_breakdown=sorted(
                [BreakdownPoint(key=k, label=tr_category(k), amount_minor=v[0], count=v[1]) for k, v in cat_map.items()],
                key=lambda x: (-x.amount_minor, x.key),
            ),
            top_products=sorted(
                [BreakdownPoint(key=k, label=k, amount_minor=v[0], count=v[1]) for k, v in prod_map.items()],
                key=lambda x: (-x.count, -x.amount_minor, x.key),
            )[:10],
            cashier_breakdown=sorted(
                [BreakdownPoint(key=k, label=k, amount_minor=v[0], count=v[1]) for k, v in cashier_map.items()],
                key=lambda x: (-x.amount_minor, x.key),
            ),
            order_type_breakdown=sorted(
                [BreakdownPoint(key=k, label=k, amount_minor=v[0], count=v[1]) for k, v in otype_map.items()],
                key=lambda x: (-x.amount_minor, x.key),
            ),
            expense_breakdown=sorted(
                [BreakdownPoint(key=k, label=k, amount_minor=v[0], count=v[1]) for k, v in exp_cat_map.items()],
                key=lambda x: (-x.amount_minor, x.key),
            ),
            recent_orders=list(reversed(matching_summaries[-10:])),
        )
