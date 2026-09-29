# -*- coding: utf-8 -*-
# Copyright (c) 2026, Your Company and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime, getdate, add_days
import re

from production_entry.production_planning.planning_doctypes import (
    PLANNING_SHEET as PLANNING_SHEET_DOCTYPE,
    PLANNING_SHEET_SUBMIT_LINKS_WORK_ORDERS_ONLY,
    normalize_planning_unit_for_select,
    resolve_planning_workstation_name,
    ensure_planning_workstation_record,
    ensure_planning_unit_field_links_workstation,
    LAMINATION_UNIT,
    SLITTING_UNIT,
    SLITTING_UNIT_VTP,
    SLITTING_UNASSIGNED_UNIT,
)


def get_item_default_warehouse(item_code, company):
    """Resolve default warehouse for an item without querying a removed `tabItem.default_warehouse` column.

    ERPNext stores per-company defaults on **Item Default**; older setups may still have `Item.default_warehouse`.
    """
    if not item_code or not company:
        return None
    wh = frappe.db.get_value(
        "Item Default",
        {"parent": item_code, "company": company},
        "default_warehouse",
    )
    if wh:
        return wh
    try:
        meta = frappe.get_meta("Item")
        if meta.has_field("default_warehouse"):
            return frappe.db.get_value("Item", item_code, "default_warehouse")
    except Exception:
        pass
    return frappe.db.get_value("Company", company, "default_warehouse")


def get_default_bom_for_item(item_code, company=None):
    """Resolve an active submitted BOM for Production Plan `po_items` (BOM No is mandatory in ERPNext).

    Prefers default BOM for the company, then any active BOM for the item.
    """
    if not item_code:
        return None
    try:
        from erpnext.manufacturing.doctype.bom.bom import get_default_bom

        try:
            name = get_default_bom(item_code, company)
        except TypeError:
            name = get_default_bom(item_code)
        if name:
            return name
    except Exception:
        pass
    base_filters = {"item": item_code, "docstatus": 1, "is_active": 1}
    if company:
        rows = frappe.get_all(
            "BOM",
            filters={**base_filters, "company": company},
            fields=["name"],
            order_by="is_default desc, modified desc",
            limit_page_length=1,
        )
        if rows:
            return rows[0].name
    rows = frappe.get_all(
        "BOM",
        filters=base_filters,
        fields=["name"],
        order_by="is_default desc, modified desc",
        limit_page_length=1,
    )
    return rows[0].name if rows else None


# Class name must equal DocType name with spaces removed (Frappe get_controller), e.g. "Planning sheet" -> Planningsheet.
class Planningsheet(Document):
    def _sanitize_order_sheet_link_values(self):
        """``order_sheet`` is Link → Production Plan (single). Legacy Sync wrote CSV of all PPs
        on the header, which breaks Frappe link validation on every save (e.g. setting planned_date).
        Keep one valid PP or clear; per-row Order Sheets on the board remain the source of truth.
        """
        def _one_valid_pp(raw):
            s = str(raw or "").strip()
            if not s:
                return ""
            # CSV / multi — pick first existing Production Plan name
            parts = [p.strip() for p in s.replace(";", ",").split(",") if p and p.strip()]
            if not parts:
                return ""
            if len(parts) == 1 and frappe.db.exists("Production Plan", parts[0]):
                return parts[0]
            for p in parts:
                if frappe.db.exists("Production Plan", p):
                    return p
            return ""

        def _assign(doc, value):
            cleaned = value or None
            if hasattr(doc, "set"):
                doc.set("order_sheet", cleaned)
            else:
                doc.order_sheet = cleaned

        if self.meta.has_field("order_sheet"):
            # Header is one link. A comma list of colour plans is not a document name.
            raw = str(self.get("order_sheet") or "").strip()
            if "," in raw or ";" in raw:
                _assign(self, None)
            else:
                _assign(self, _one_valid_pp(raw) or None)

        for table_key in ("items", "planned_items"):
            for row in self.get(table_key) or []:
                if not hasattr(row, "order_sheet"):
                    continue
                raw = str(row.get("order_sheet") or "").strip() if hasattr(row, "get") else str(getattr(row, "order_sheet", None) or "").strip()
                if not raw:
                    continue
                if "," in raw or ";" in raw or not frappe.db.exists("Production Plan", raw):
                    _assign(row, _one_valid_pp(raw) or None)

    def _validate_links(self):
        """Run before Document's link check: Frappe calls _validate_links() before validate()/hooks."""
        if not self.flags.get("ignore_links") and getattr(self, "_action", None) != "cancel":
            self._sanitize_order_sheet_link_values()
            self._fix_planned_items_source_item_links()
            # Whites → UNASSIGNED; colors → Unit 1–4 by width before Select normalization.
            self._recompute_line_units_from_width_and_color()
            # Runs before super(): insert() also validates links before before_validate.
            if not frappe.flags.get("planning_unit_field_meta_patched"):
                try:
                    ensure_planning_unit_field_links_workstation()
                    frappe.flags.planning_unit_field_meta_patched = True
                except Exception:
                    frappe.log_error(frappe.get_traceback(), "planning_sheet:ensure_unit_workstation_link")
            self._normalize_child_table_units()
        super()._validate_links()

    def _recompute_line_units_from_width_and_color(self):
        """Only white orders stay UNASSIGNED; other colors get machine unit from width (and resolved color)."""
        if cint(self.docstatus) != 0:
            return
        from production_entry.production_planning.scheduler_api import (
            LAMINATION_FLOW_ENABLED,
            PRINTED_BOPP_FILM_UNIT,
            SHEET_CUTTING_UNIT,
            compute_default_production_unit,
            _get_color_by_code,
            _is_lamination_parent_process,
            _item_process_prefix,
            resolve_color_name_for_planning_row,
        )

        linked_psi_names = set()
        for pr in self.get("planned_items") or []:
            si = (getattr(pr, "source_item", None) or "").strip()
            if si:
                linked_psi_names.add(si)

        for table_key in ("items", "planned_items"):
            for row in self.get(table_key) or []:
                item_code = str(getattr(row, "item_code", None) or "").strip()
                if item_code.upper().startswith("PB-"):
                    row.unit = PRINTED_BOPP_FILM_UNIT
                    continue
                if LAMINATION_FLOW_ENABLED and _is_lamination_parent_process(item_code):
                    row.unit = LAMINATION_UNIT
                    continue
                if getattr(row, "planned_date", None) and str(row.planned_date).strip():
                    continue
                # Board row drives unit for linked legacy line — do not overwrite Planning sheet Item from width.
                if table_key == "items":
                    psi_name = (getattr(row, "name", None) or "").strip()
                    if psi_name and psi_name in linked_psi_names:
                        continue
                color = (getattr(row, "color", None) or "").strip()
                resolved = resolve_color_name_for_planning_row(
                    getattr(row, "item_code", None),
                    getattr(row, "item_name", None),
                    color,
                )
                if resolved and not color:
                    row.color = resolved
                    color = resolved
                elif not color:
                    color = resolved or ""
                width = flt(getattr(row, "width_inch", None))
                # Hard rule: process 103 defaults to Unassigned Slitting Unit and color from Colour Master code.
                process_prefix = _item_process_prefix(item_code)
                if process_prefix == "103":
                    digits = "".join(ch for ch in item_code if ch.isdigit())
                    code = digits[6:9] if len(digits) >= 9 else ""
                    mapped = _get_color_by_code(code) if code else ""
                    if mapped:
                        row.color = mapped
                        color = mapped
                    current = normalize_planning_unit_for_select(getattr(row, "unit", None))
                    row.unit = current if current in (SLITTING_UNIT, SLITTING_UNIT_VTP, SLITTING_UNASSIGNED_UNIT) else SLITTING_UNASSIGNED_UNIT
                    continue
                if process_prefix == "251":
                    row.unit = SHEET_CUTTING_UNIT
                    continue
                row.unit = compute_default_production_unit(color, width, getattr(row, "item_code", None))

    def _sync_linked_planning_units(self):
        """Keep legacy `items` and board `planned_items` units aligned when linked by `source_item`.

        Desk often POSTs a stale value for the grid the user did not edit. Compare to DB so we detect
        which side changed: only board changed → copy to legacy; only legacy changed → copy to board;
        both changed → prefer legacy (same as user editing the snapshot grid first).
        """
        if cint(self.docstatus) != 0:
            return
        from production_entry.production_planning.scheduler_api import (
            LAMINATION_FLOW_ENABLED,
            PRINTED_BOPP_FILM_UNIT,
            SHEET_CUTTING_UNIT,
            _get_color_by_code,
            _is_lamination_parent_process,
            _item_process_prefix,
        )

        items_by_name = {((getattr(r, "name", None) or "").strip()): r for r in (self.get("items") or []) if getattr(r, "name", None)}
        legacy_by_so_line = {}
        for leg in self.get("items") or []:
            so_line = (getattr(leg, "so_item", None) or getattr(leg, "sales_order_item", None) or "").strip()
            ic = str(getattr(leg, "item_code", None) or "").strip()
            if so_line and ic:
                legacy_by_so_line[(so_line, ic)] = leg

        for pr in self.get("planned_items") or []:
            si = (getattr(pr, "source_item", None) or "").strip()
            leg = None
            if si and si in items_by_name:
                leg = items_by_name[si]
            if not leg:
                pr_so = (
                    getattr(pr, "so_item", None)
                    or getattr(pr, "sales_order_item", None)
                    or ""
                ).strip()
                pr_ic = str(getattr(pr, "item_code", None) or "").strip()
                if pr_so and pr_ic:
                    leg = legacy_by_so_line.get((pr_so, pr_ic))
            if not leg:
                continue
            pr_ic = str(getattr(pr, "item_code", None) or "").strip()
            leg_ic = str(getattr(leg, "item_code", None) or "").strip()
            if pr_ic.upper().startswith("PB-") or leg_ic.upper().startswith("PB-"):
                pr.unit = PRINTED_BOPP_FILM_UNIT
                leg.unit = PRINTED_BOPP_FILM_UNIT
                continue
            if LAMINATION_FLOW_ENABLED and (
                _is_lamination_parent_process(pr_ic) or _is_lamination_parent_process(leg_ic)
            ):
                pr.unit = LAMINATION_UNIT
                leg.unit = LAMINATION_UNIT
                continue
            # Hard lock for process 103 across linked rows (default to unassigned, preserve selected slitting machine).
            item_code = str(getattr(pr, "item_code", None) or getattr(leg, "item_code", None) or "").strip()
            if _item_process_prefix(item_code) == "103":
                digits = "".join(ch for ch in item_code if ch.isdigit())
                code = digits[6:9] if len(digits) >= 9 else ""
                mapped = _get_color_by_code(code) if code else ""
                if mapped:
                    pr.color = mapped
                    leg.color = mapped
                pr_unit = normalize_planning_unit_for_select(getattr(pr, "unit", None))
                leg_unit = normalize_planning_unit_for_select(getattr(leg, "unit", None))
                chosen = pr_unit if pr_unit in (SLITTING_UNIT, SLITTING_UNIT_VTP, SLITTING_UNASSIGNED_UNIT) else (
                    leg_unit if leg_unit in (SLITTING_UNIT, SLITTING_UNIT_VTP, SLITTING_UNASSIGNED_UNIT) else SLITTING_UNASSIGNED_UNIT
                )
                pr.unit = chosen
                leg.unit = chosen
                continue
            if _item_process_prefix(item_code) == "251":
                pr.unit = SHEET_CUTTING_UNIT
                leg.unit = SHEET_CUTTING_UNIT
                continue
            nu = normalize_planning_unit_for_select(getattr(leg, "unit", None))
            bu = normalize_planning_unit_for_select(getattr(pr, "unit", None))
            if nu == bu:
                continue

            pr_name = (getattr(pr, "name", None) or "").strip()
            leg_db_n = None
            pr_db_n = None
            if frappe.db.exists("Planning sheet Item", si):
                leg_db_n = normalize_planning_unit_for_select(
                    frappe.db.get_value("Planning sheet Item", si, "unit")
                )
            if pr_name and frappe.db.exists("Planning Table", pr_name):
                pr_db_n = normalize_planning_unit_for_select(
                    frappe.db.get_value("Planning Table", pr_name, "unit")
                )

            leg_changed = leg_db_n is None or nu != leg_db_n
            board_changed = pr_db_n is None or bu != pr_db_n

            if leg_changed and not board_changed:
                pr.unit = nu
            elif board_changed and not leg_changed:
                leg.unit = bu
            elif leg_changed and board_changed:
                pr.unit = nu
                leg.unit = nu
            else:
                pr.unit = nu
                leg.unit = nu

    def _sync_linked_planned_dates(self):
        """Keep board ``planned_date`` and items ``custom_item_planned_date`` aligned when linked.

        Desk often POSTs a stale value for the grid the user did not edit. Prefer the side that
        changed vs DB; if both changed, prefer the board schedule date (primary planning field).
        """
        if cint(self.docstatus) != 0:
            return

        def _norm_date(v):
            if v is None or str(v).strip() == "":
                return ""
            try:
                return str(getdate(v))
            except Exception:
                return str(v).strip()[:10]

        items_by_name = {
            ((getattr(r, "name", None) or "").strip()): r
            for r in (self.get("items") or [])
            if getattr(r, "name", None)
        }
        legacy_by_so_line = {}
        for leg in self.get("items") or []:
            so_line = (getattr(leg, "so_item", None) or getattr(leg, "sales_order_item", None) or "").strip()
            ic = str(getattr(leg, "item_code", None) or "").strip()
            if so_line and ic:
                legacy_by_so_line[(so_line, ic)] = leg

        for pr in self.get("planned_items") or []:
            si = (getattr(pr, "source_item", None) or "").strip()
            leg = None
            if si and si in items_by_name:
                leg = items_by_name[si]
            if not leg:
                pr_so = (
                    getattr(pr, "so_item", None)
                    or getattr(pr, "sales_order_item", None)
                    or ""
                ).strip()
                pr_ic = str(getattr(pr, "item_code", None) or "").strip()
                if pr_so and pr_ic:
                    leg = legacy_by_so_line.get((pr_so, pr_ic))
            if not leg:
                continue

            board_d = _norm_date(getattr(pr, "planned_date", None))
            leg_d = _norm_date(getattr(leg, "custom_item_planned_date", None))
            if board_d == leg_d:
                continue

            pr_name = (getattr(pr, "name", None) or "").strip()
            leg_db = None
            pr_db = None
            if si and frappe.db.exists("Planning sheet Item", si):
                leg_db = _norm_date(frappe.db.get_value("Planning sheet Item", si, "custom_item_planned_date"))
            if pr_name and frappe.db.exists("Planning Table", pr_name):
                pr_db = _norm_date(frappe.db.get_value("Planning Table", pr_name, "planned_date"))

            leg_changed = leg_db is None or leg_d != leg_db
            board_changed = pr_db is None or board_d != pr_db

            if board_changed and not leg_changed:
                leg.custom_item_planned_date = board_d or None
            elif leg_changed and not board_changed:
                pr.planned_date = leg_d or None
            else:
                # Prefer board schedule when both changed or both are new.
                if board_d:
                    leg.custom_item_planned_date = board_d
                elif leg_d:
                    pr.planned_date = leg_d

    def _normalize_child_table_units(self):
        """Map ``unit`` to existing Workstation names (L1/L2 Leader, Unit 1–4, etc.)."""
        for row in self.get("planned_items") or []:
            row.unit = self._resolve_row_workstation_unit(getattr(row, "unit", None))
        for row in self.get("items") or []:
            row.unit = self._resolve_row_workstation_unit(getattr(row, "unit", None))

    def _resolve_row_workstation_unit(self, raw):
        norm = normalize_planning_unit_for_select(raw)
        resolved = resolve_planning_workstation_name(norm or raw)
        if resolved and frappe.db.exists("Workstation", resolved):
            return resolved
        if norm:
            ensure_planning_workstation_record(norm)
            if frappe.db.exists("Workstation", norm):
                return norm
        if norm in ("Unit 1", "Unit 2", "Unit 3", "Unit 4", "UNASSIGNED"):
            return norm
        return resolved or norm or ""

    def _recompute_printed_bopp_total_colours_on_child_rows(self):
        """Board grid: persist total colours when white tint / design token change (matches Printed BOPP table)."""
        try:
            from production_entry.production_planning.scheduler_api import (
                _apply_printed_bopp_planning_fields_to_row,
                _apply_printed_bopp_total_colours_to_row,
                _is_printed_bopp_item_code,
            )

            for table_key in ("planned_items", "items"):
                for row in self.get(table_key) or []:
                    ic = str(getattr(row, "item_code", None) or "").strip()
                    if ic and _is_printed_bopp_item_code(ic):
                        _apply_printed_bopp_planning_fields_to_row(row, ic)
                    _apply_printed_bopp_total_colours_to_row(row)
        except Exception:
            pass

    def _ensure_107_extras_on_rows(self):
        """Guarantee 107 parent rows always have BOPP/LAM GSM, finishing, design name, white tint."""
        if cint(self.docstatus) != 0:
            return
        try:
            from production_entry.production_planning.scheduler_api import (
                LAMINATION_FLOW_ENABLED,
                _lamination_process_from_item_code,
                _parse_107_item_code,
                _planning_row_dict_107_lamination_extras,
                _pb_design_name_from_sales_order_item,
            )
        except Exception:
            return
        if not LAMINATION_FLOW_ENABLED:
            return

        for table_key in ("planned_items", "items"):
            for row in self.get(table_key) or []:
                ic = str(getattr(row, "item_code", None) or "").strip()
                if _lamination_process_from_item_code(ic) != "107":
                    continue
                soi = (getattr(row, "so_item", None) or getattr(row, "sales_order_item", None) or "").strip()
                parsed = _parse_107_item_code(ic) or {}

                # Populate finishing / GSMs / tint using the same helper as scheduler creation/regenerate.
                extras = _planning_row_dict_107_lamination_extras(ic, parsed, soi) or {}
                for k, v in (extras or {}).items():
                    if v is None:
                        continue
                    try:
                        setattr(row, k, v)
                    except Exception:
                        pass

                dn = _pb_design_name_from_sales_order_item(soi) if soi else ""
                if dn and hasattr(row, "custom_design_name") and not str(getattr(row, "custom_design_name", "") or "").strip():
                    row.custom_design_name = dn

    def validate(self):
        """Validate planning sheet before saving"""
        if cint(self.docstatus) == 1:
            from production_entry.production_planning.board_access import _is_privileged_user

            if not _is_privileged_user():
                frappe.throw(
                    _("Submitted Planning Sheet cannot be modified. Cancel the document to make changes."),
                    title=_("Not allowed"),
                )
            self._enforce_admin_submitted_board_unit_only_edit()
            self._sync_linked_planning_units()
            self._sync_submitted_board_units_to_legacy()
            return
        self._sync_linked_planning_units()
        self._sync_linked_planned_dates()
        self.validate_items()
        self.calculate_totals()
        self.parse_item_details()
        self._enrich_rows_from_item_codes()
        self._sync_line_plan_codes()
        self._recompute_printed_bopp_total_colours_on_child_rows()
        self._ensure_parent_child_trace_ids_on_rows()
        self._ensure_107_extras_on_rows()
        # Removed reorder_planning_sheet_child_tables_in_doc to preserve user's manual row order in child tables

    def _enforce_admin_submitted_board_unit_only_edit(self):
        """After submit, privileged users may only change ``planned_items.unit`` (board POC testing)."""
        prev = self.get_doc_before_save()
        if not prev:
            return
        skip = {
            "name",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "docstatus",
            "idx",
            "parent",
            "parenttype",
            "parentfield",
            "doctype",
            "__islocal",
            "__unsaved",
            "__unedited",
        }

        def _parent_changed():
            for df in self.meta.fields:
                if df.fieldtype in ("Table", "Section Break", "Column Break", "Tab Break", "HTML"):
                    continue
                if (self.get(df.fieldname) or "") != (prev.get(df.fieldname) or ""):
                    return True
            return False

        if _parent_changed():
            frappe.throw(
                _("Only board Unit may be changed on a submitted Planning Sheet (testing)."),
                title=_("Not allowed"),
            )

        if len(self.items or []) != len(prev.items or []):
            frappe.throw(
                _("Cannot add or remove legacy Items rows on a submitted Planning Sheet."),
                title=_("Not allowed"),
            )

        prev_items = {r.name: r for r in (prev.items or [])}
        for row in self.items or []:
            old = prev_items.get(row.name)
            if not old:
                frappe.throw(_("Cannot add legacy Items rows on a submitted Planning Sheet."))
            for k, v in row.as_dict().items():
                if k in skip or k == "unit":
                    continue
                if (old.get(k) or "") != (v or ""):
                    frappe.throw(
                        _("Only board Unit may be changed on a submitted Planning Sheet (testing)."),
                        title=_("Not allowed"),
                    )

        if len(self.planned_items or []) != len(prev.planned_items or []):
            frappe.throw(
                _("Cannot add or remove board rows on a submitted Planning Sheet."),
                title=_("Not allowed"),
            )

        prev_board = {r.name: r for r in (prev.planned_items or [])}
        for row in self.planned_items or []:
            old = prev_board.get(row.name)
            if not old:
                frappe.throw(_("Cannot add board rows on a submitted Planning Sheet."))
            for k, v in row.as_dict().items():
                if k in skip or k == "unit":
                    continue
                if (old.get(k) or "") != (v or ""):
                    frappe.throw(
                        _("Only board Unit may be changed on a submitted Planning Sheet (testing)."),
                        title=_("Not allowed"),
                    )

    def _sync_submitted_board_units_to_legacy(self):
        """Mirror board unit onto linked Planning sheet Item after admin save."""
        from production_entry.production_planning.scheduler_api import (
            _force_sync_unit_both_planning_tables,
        )

        for row in self.planned_items or []:
            if not row.name:
                continue
            unit = normalize_planning_unit_for_select(getattr(row, "unit", None))
            plan_code = (getattr(row, "custom_plan_code", None) or getattr(row, "plan_name", None) or "").strip() or None
            try:
                _force_sync_unit_both_planning_tables(row, unit, plan_code)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"planning_sheet:sync board unit:{row.name}")

    def _enrich_rows_from_item_codes(self):
        """Fill quality/colour/GSM, sheet size, 255 trace, PB design from item_code on desk save."""
        try:
            from production_entry.production_planning.scheduler_api import enrich_planning_child_row_from_item_code
        except Exception:
            return
        ps_name = (getattr(self, "name", None) or "").strip() or None
        for table_key in ("items", "planned_items"):
            for row in self.get(table_key) or []:
                enrich_planning_child_row_from_item_code(row, ps_name)

    def _planning_trace_sheet_context(self):
        """Build board rows + SO map for trace resolver (same as post-sync backfill)."""
        ps_name = (getattr(self, "name", None) or "").strip() or None
        pt_rows = []
        for pr in self.get("planned_items") or []:
            pt_rows.append(
                {
                    "item_code": getattr(pr, "item_code", None),
                    "sales_order_item": getattr(pr, "sales_order_item", None) or getattr(pr, "so_item", None),
                    "so_item": getattr(pr, "so_item", None) or getattr(pr, "sales_order_item", None),
                    "custom_parent_child_trace_id": getattr(pr, "custom_parent_child_trace_id", None),
                }
            )
        so_by_name = {}
        so_name = (getattr(self, "sales_order", None) or "").strip()
        if so_name and frappe.db.exists("Sales Order", so_name):
            try:
                so_doc = frappe.get_doc("Sales Order", so_name)
                so_by_name = {str(it.name): it for it in (so_doc.items or [])}
            except Exception:
                so_by_name = {}
        return ps_name, pt_rows, so_by_name

    def _ensure_parent_child_trace_ids_on_rows(self):
        """Fill empty Parent Child Trace ID only — never overwrite stable traces on desk save."""
        try:
            from production_entry.production_planning.scheduler_api import (
                _resolve_trace_id_for_planning_row,
                _set_trace_id_if_supported,
                _should_apply_trace_to_row,
            )
        except Exception:
            return
        ps_name, pt_rows, so_by_name = self._planning_trace_sheet_context()
        for table_key in ("planned_items", "items"):
            for row in self.get(table_key) or []:
                ic = str(getattr(row, "item_code", None) or "").strip()
                if not ic:
                    continue
                soi = (
                    str(getattr(row, "sales_order_item", None) or "").strip()
                    or str(getattr(row, "so_item", None) or "").strip()
                )
                cur = str(getattr(row, "custom_parent_child_trace_id", None) or "").strip()
                row_probe = {
                    "item_code": ic,
                    "sales_order_item": soi,
                    "so_item": soi,
                }
                tid = _resolve_trace_id_for_planning_row(row_probe, ps_name, pt_rows, so_by_name)
                if not tid:
                    continue
                if not cur:
                    _set_trace_id_if_supported(row, tid, item_code=ic)
                    continue
                if not _should_apply_trace_to_row(ic, cur, tid):
                    continue
                _set_trace_id_if_supported(row, tid, item_code=ic)
                row_name = getattr(row, "name", None)
                if row_name and ps_name:
                    doctype = "Planning Table" if table_key == "planned_items" else "Planning sheet Item"
                    try:
                        from production_entry.production_planning.scheduler_api import (
                            _safe_set_planning_trace_id,
                        )

                        _safe_set_planning_trace_id(doctype, row_name, ic, tid, current_trace_id=cur)
                    except Exception:
                        pass

    def _sync_line_plan_codes(self):
        """Fill Planning sheet Item / board row Plan Code from active plan + date + unit (color chart alignment)."""
        from production_entry.production_planning.scheduler_api import update_sheet_plan_codes

        update_sheet_plan_codes(self, include_legacy=True)

    def _fix_planned_items_source_item_links(self):
        """Ensure planned_items.source_item points to Planning sheet Item, not a Planning Table row id.

        Board rows and legacy rows share similar autonames; desk / splits sometimes store the wrong id in
        the Link field, which causes LinkValidationError on save.
        """

        def _resolve_to_planning_sheet_item(stored):
            """Follow Planning Table.source_item chain until a valid Planning sheet Item name or give up."""
            cur = (stored or "").strip()
            for _ in range(6):
                if not cur:
                    return None
                if frappe.db.exists("Planning sheet Item", cur):
                    return cur
                if frappe.db.exists("Planning Table", cur):
                    cur = frappe.db.get_value("Planning Table", cur, "source_item") or ""
                    continue
                return None
            return None

        planned = list(self.planned_items or [])
        if not planned:
            return

        legacy = sorted(
            list(self.get("items") or []),
            key=lambda x: (cint(x.idx), getattr(x, "name", None) or ""),
        )
        planned_sorted = sorted(
            planned,
            key=lambda x: (cint(x.idx), getattr(x, "name", None) or ""),
        )
        can_pos_map = len(legacy) == len(planned_sorted) and bool(legacy)

        for pos, row in enumerate(planned_sorted):
            si = (row.source_item or "").strip()
            if not si:
                continue
            if frappe.db.exists("Planning sheet Item", si):
                continue

            resolved = _resolve_to_planning_sheet_item(si)

            row_id = (row.name or "").strip()
            if not resolved and row_id:
                resolved = _resolve_to_planning_sheet_item(row_id)

            if resolved:
                row.source_item = resolved
                continue

            if can_pos_map and pos < len(legacy):
                ln = legacy[pos].name
                if ln and frappe.db.exists("Planning sheet Item", ln):
                    row.source_item = ln
                    continue

            row.source_item = None
    
    def before_validate(self):
        """Clear a comma-separated Order Sheet before Frappe's link check."""
        self._sanitize_order_sheet_link_values()

    def before_save(self):
        """Allocate unit before saving"""
        if not self.allocated_unit:
            self.allocate_unit_to_sheet()

    def on_update(self):
        """Notify boards/tables to refresh after sheet edits.

        Users often edit planned_date/unit on the Planning Sheet directly; boards/tables are separate pages
        and rely on realtime events to refresh without manual browser reload.
        """
        try:
            frappe.publish_realtime("production_board_update", {"planning_sheet": self.name})
        except Exception:
            pass
    
    def on_submit(self):
        """Update queue and create Production Plans on submit (WOs are created on PP submit)."""
        self.update_queue_position()
        self.create_production_docs()
        self.planning_status = "Finalized"

    def _resolve_company_for_production_docs(self):
        """Planning sheet JSON may not include `company`; never use bare self.company (AttributeError)."""
        co = self.get("company")
        if co:
            return co
        if self.sales_order:
            co = frappe.db.get_value("Sales Order", self.sales_order, "company")
            if co:
                return co
        co = frappe.defaults.get_user_default("Company")
        if co:
            return co
        return frappe.db.get_single_value("Global Defaults", "default_company")

    def _production_plan_header_fields_from_planning_sheet(self):
        """Optional Production Plan header fields so list views are not blank (custom fields vary per site)."""
        out = {}
        pp = "Production Plan"
        cust = self.get("customer")
        if not cust and self.sales_order:
            cust = frappe.db.get_value("Sales Order", self.sales_order, "customer")
        if cust and frappe.db.has_column(pp, "customer"):
            out["customer"] = cust
        if frappe.db.has_column(pp, "custom_planning_sheet"):
            out["custom_planning_sheet"] = self.name
        plan_label = (self.get("custom_plan_name") or self.get("plan_name") or "").strip()
        if plan_label and frappe.db.has_column(pp, "custom_plan_name"):
            out["custom_plan_name"] = plan_label
        code = (self.get("custom_plan_code") or "").strip()
        if code and frappe.db.has_column(pp, "custom_plan_code"):
            out["custom_plan_code"] = code
        return out

    def _planning_item_work_order_field_enabled(self):
        return frappe.db.has_column("Planning sheet Item", "work_order")

    def _lookup_work_order_from_production_plan(self, item, pp_name):
        """Resolve Work Order from Production Plan + line match (does not write to planning row)."""
        if not pp_name or not frappe.db.exists("Production Plan", pp_name):
            return None
        pp = frappe.get_doc("Production Plan", pp_name)
        so_item = item.get("so_item") or item.get("sales_order_item")
        ppi_name = None
        for ppi in pp.get("po_items") or []:
            if (ppi.get("item_code") or "") != (item.item_code or ""):
                continue
            if (ppi.get("sales_order_item") or "") != (so_item or ""):
                continue
            ppi_name = ppi.name
            break
        if not ppi_name:
            return None
        wo_name = frappe.db.get_value(
            "Work Order",
            {
                "production_plan": pp.name,
                "production_plan_item": ppi_name,
                "docstatus": ["<", 2],
            },
            "name",
        )
        if not wo_name:
            rows = frappe.db.sql(
                """
                SELECT name FROM `tabWork Order`
                WHERE production_plan = %s AND docstatus < 2
                ORDER BY creation DESC
                LIMIT 1
                """,
                pp.name,
            )
            wo_name = rows[0][0] if rows else None
        return wo_name

    def _resolve_work_order_for_item(self, item, pp_name=None):
        """Work Order for a planning line: stored value (legacy) or lookup from Production Plan."""
        if self._planning_item_work_order_field_enabled():
            wo = (item.get("work_order") or "").strip()
            if wo:
                return wo
        if pp_name:
            return self._lookup_work_order_from_production_plan(item, pp_name)
        from production_entry.production_planning.scheduler_api import (
            _get_item_level_production_plan,
            _production_plan_usable,
            _resolve_existing_production_plan_for_planning_sheet,
        )

        sheet_level_pp = _resolve_existing_production_plan_for_planning_sheet(self.name)
        item_pp = _get_item_level_production_plan(item.name)
        pp_for_row = _production_plan_usable(item_pp) or sheet_level_pp
        if pp_for_row:
            return self._lookup_work_order_from_production_plan(item, pp_for_row)
        return None

    def _try_link_work_order_from_existing_production_plan(self, item, pp_name):
        """
        If this Planning sheet row belongs to a submitted Production Plan, optionally persist
        work_order on the line (legacy). Returns True when a WO exists for the PP line.
        """
        wo_name = self._lookup_work_order_from_production_plan(item, pp_name)
        if not wo_name:
            return False
        if self._planning_item_work_order_field_enabled():
            item.work_order = wo_name
        return True

    def link_work_orders_for_production_plan(self, pp_name):
        """After a Production Plan is submitted, attach Work Orders to Planning sheet Item rows that use that PP."""
        if not self._planning_item_work_order_field_enabled():
            return False
        from production_entry.production_planning.scheduler_api import (
            _get_item_level_production_plan,
            _production_plan_usable,
            _resolve_existing_production_plan_for_planning_sheet,
        )

        pp_name = (pp_name or "").strip()
        if not pp_name or not frappe.db.exists("Production Plan", pp_name):
            return False
        if cint(frappe.db.get_value("Production Plan", pp_name, "docstatus")) != 1:
            return False
        sheet_level_pp = _resolve_existing_production_plan_for_planning_sheet(self.name)
        updated = False
        for item in self.items:
            item_pp = _get_item_level_production_plan(item.name)
            pp_for_row = _production_plan_usable(item_pp) or sheet_level_pp
            if pp_for_row != pp_name:
                continue
            if self._try_link_work_order_from_existing_production_plan(item, pp_for_row):
                frappe.db.set_value(
                    "Planning sheet Item",
                    item.name,
                    "work_order",
                    item.work_order,
                    update_modified=False,
                )
                updated = True
        if updated:
            from production_entry.production_planning.scheduler_api import (
                _sync_planning_board_work_orders_from_items,
            )

            _sync_planning_board_work_orders_from_items(self.name)
        return updated

    def create_production_docs(self):
        """Link Work Orders from existing Production Plan(s). Optionally create PP per line only in legacy mode."""
        from production_entry.production_planning.scheduler_api import (
            _get_item_level_production_plan,
            _production_plan_usable,
            _resolve_existing_production_plan_for_planning_sheet,
        )

        company = self._resolve_company_for_production_docs()
        if not company:
            frappe.throw(
                _("Set Company on Planning sheet or link a Sales Order with Company before submitting."),
                title=_("Company missing"),
            )

        sheet_level_pp = _resolve_existing_production_plan_for_planning_sheet(self.name)
        links_only = PLANNING_SHEET_SUBMIT_LINKS_WORK_ORDERS_ONLY

        for item in self.items:
            item_pp = _get_item_level_production_plan(item.name)
            pp_for_row = _production_plan_usable(item_pp) or sheet_level_pp

            if pp_for_row:
                if self._try_link_work_order_from_existing_production_plan(item, pp_for_row):
                    continue
                ds = frappe.db.get_value("Production Plan", pp_for_row, "docstatus")
                if cint(ds) == 0:
                    # Draft PP: Work Orders do not exist yet. Skip this line; user can submit each PP in any order.
                    # WO links are filled when each PP is submitted (see on_production_plan_submitted) or on re-save.
                    continue
                if cint(ds) == 1:
                    frappe.throw(
                        _(
                            "Production Plan {0} is submitted but no Work Order matched this line "
                            "(item {1}). Check item code and Sales Order line on the Production Plan."
                        ).format(pp_for_row, item.item_code or ""),
                        title=_("Work Order not found for line"),
                    )
                frappe.throw(
                    _("Production Plan {0} is not in a valid state to link Work Orders.").format(pp_for_row),
                    title=_("Production Plan state"),
                )

            if links_only:
                frappe.throw(
                    _(
                        "No Production Plan linked to this Planning sheet or row. "
                        "Link Production Plan(s) on the sheet and lines, submit the Production Plan "
                        "(Work Orders are created from the Production Plan), then finalize this Planning sheet."
                    ),
                    title=_("Production Plan required"),
                )

            bom_no = get_default_bom_for_item(item.item_code, company)
            if not bom_no:
                frappe.throw(
                    _(
                        "No active default BOM found for item {0}. Set a default BOM on the BOM master before finalizing the Planning sheet."
                    ).format(item.item_code),
                    title=_("BOM No required"),
                )
            # Legacy: create one Production Plan per Planning sheet item (avoid when PLANNING_SHEET_SUBMIT_LINKS_WORK_ORDERS_ONLY is True)
            pp_dict = {
                "doctype": "Production Plan",
                "naming_series": "PP-",
                "company": company,
                "get_items_from": "Sales Order",
                "posting_date": getdate(),
                "custom_unit": self.allocated_unit,
                "po_items": [
                    {
                        "sales_order": self.sales_order,
                        "sales_order_item": item.so_item,
                        "item_code": item.item_code,
                        "bom_no": bom_no,
                        "planned_qty": item.qty,
                        "warehouse": item.warehouse or get_item_default_warehouse(item.item_code, company)
                    }
                ],
            }
            pp_dict.update(self._production_plan_header_fields_from_planning_sheet())
            pp = frappe.get_doc(pp_dict)
            pp.insert()
            pp.submit()

            ppi_name = (pp.po_items[0].name if pp.get("po_items") else None)
            wo_name = None
            if ppi_name:
                wo_name = frappe.db.get_value(
                    "Work Order",
                    {
                        "production_plan": pp.name,
                        "production_plan_item": ppi_name,
                        "docstatus": ["<", 2],
                    },
                    "name",
                )
            if not wo_name:
                rows = frappe.db.sql(
                    """
                    SELECT name FROM `tabWork Order`
                    WHERE production_plan = %s AND docstatus < 2
                    ORDER BY creation DESC
                    LIMIT 1
                    """,
                    pp.name,
                )
                wo_name = rows[0][0] if rows else None
            if wo_name:
                if self._planning_item_work_order_field_enabled():
                    item.work_order = wo_name

        self.db_update()

    def validate_items(self):
        """Validate that items are present"""
        if not self.planned_items:
            frappe.throw("Please add at least one item to the Planning Sheet")
    
    @frappe.whitelist()
    def make_consolidated_production_entry(self):
        """Generate a single step Production Entry for all Work Orders in this Planning Sheet"""
        self.check_permission("write")
        
        entry_results = []
        for item in self.items:
            wo_name = self._resolve_work_order_for_item(item)
            if not wo_name:
                continue

            wo = frappe.get_doc("Work Order", wo_name)
            if wo.status in ["Completed", "Closed"]:
                continue
            
            try:
                # Calculate what's left to produce
                pending_qty = flt(wo.qty) - flt(wo.produced_qty)
                if pending_qty <= 0:
                    continue
                
                # Logic to determine production qty (for now full pending)
                # In a real scenario, this would be passed from a dialog
                prod_qty = pending_qty
                
                # Create Stock Entry for Manufacture
                from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
                se = frappe.get_doc(make_stock_entry(wo.name, "Manufacture", prod_qty))
                se.insert()
                se.submit()
                
                entry_results.append(f"Created Entry {se.name} for {item.item_code}")
                
            except Exception as e:
                frappe.log_error(f"Production Entry Error for {wo_name}: {str(e)}")
                entry_results.append(f"Error for {item.item_code}: {str(e)}")
        
        return entry_results

    
    def calculate_totals(self):
        """Calculate total quantity and weight"""
        total_qty = 0
        total_weight = 0
        
        for item in self.planned_items:
            # Calculate weight per item if not already set
            if not item.total_weight and item.weight_per_roll and item.no_of_rolls:
                item.total_weight = flt(item.weight_per_roll) * flt(item.no_of_rolls)
            
            total_qty += flt(item.qty)
            total_weight += flt(item.total_weight)
        
        self.total_quantity = total_qty
        self.total_weight = total_weight
        
        # Calculate estimated production days
        if self.allocated_unit and self.total_weight:
            capacity = get_unit_daily_capacity(self.allocated_unit)
            if capacity:
                self.estimated_production_days = flt(self.total_weight / capacity, 2)
    
    def parse_item_details(self):
        """Parse quality/color from item code via Quality Master + Colour Master."""
        for table_key in ("planned_items", "items"):
            for item in self.get(table_key) or []:
                item_code = str(getattr(item, "item_code", None) or "").strip()
                item_name = str(getattr(item, "item_name", None) or "").strip()
                if not item_code and not item_name:
                    continue
                quality, color = extract_quality_and_color(item_name, item_code=item_code)
                if quality:
                    item.quality = quality
                if color:
                    item.color = color
    
    def allocate_unit_to_sheet(self):
        """Allocate unit based on quality, GSM and capacity"""
        # Quality rules
        UNIT_1 = ["SUPER PLATINUM", "PLATINUM", "PREMIUM", "GOLD", "SUPER CLASSIC"]
        UNIT_2 = ["GOLD", "SILVER", "BRONZE", "CLASSIC", "ECO SPECIAL", "ECO SPL"]
        UNIT_3 = ["SUPER PLATINUM", "PLATINUM", "PREMIUM", "GOLD", "SILVER", "BRONZE"]
        
        # Collect all items data
        items_data = []
        for item in self.planned_items:
            items_data.append({
                "quality": item.quality.upper() if item.quality else "",
                "gsm": flt(item.gsm),
                "weight": flt(item.total_weight)
            })
        
        if not items_data:
            return
        
        # Get dominant quality (most common)
        quality_counts = {}
        total_weight = 0
        avg_gsm = 0
        
        for item_data in items_data:
            qual = item_data["quality"]
            if qual:
                quality_counts[qual] = quality_counts.get(qual, 0) + item_data["weight"]
            total_weight += item_data["weight"]
            avg_gsm += item_data["gsm"] * item_data["weight"]
        
        avg_gsm = avg_gsm / total_weight if total_weight > 0 else 0
        dominant_quality = max(quality_counts, key=quality_counts.get) if quality_counts else ""
        
        # Allocate unit based on rules
        allocated_unit = None
        
        if avg_gsm > 50 and dominant_quality in UNIT_1:
            allocated_unit = "Unit 1"
        elif avg_gsm > 20 and dominant_quality in UNIT_2:
            allocated_unit = "Unit 2"
        elif avg_gsm > 10 and dominant_quality in UNIT_3:
            allocated_unit = "Unit 3"
        elif avg_gsm > 10:
            allocated_unit = "Unit 4"
        
        # Check capacity and assign to best available unit
        if allocated_unit:
            capacity_info = frappe.db.get_value("Unit Capacity", 
                                               allocated_unit,
                                               ["day_shift_capacity_kg", "night_shift_capacity_kg", "current_queue_weight"],
                                               as_dict=True)
            
            if capacity_info:
                self.allocated_unit = allocated_unit
                self.unit_capacity_day = capacity_info.day_shift_capacity_kg
                self.unit_capacity_night = capacity_info.night_shift_capacity_kg
        
        return allocated_unit
    
    def update_queue_position(self):
        """Update queue position based on delivery date and priority"""
        if not self.allocated_unit:
            return
        
        # Get all finalized planning sheets for this unit
        existing_sheets = frappe.get_all(
            PLANNING_SHEET_DOCTYPE,
            filters={
                "allocated_unit": self.allocated_unit,
                "planning_status": ["in", ["Finalized", "In Production"]],
                "docstatus": 1,
                "name": ["!=", self.name],
            },
            fields=["name", "queue_position", "dod"],
            order_by="queue_position asc",
        )
        
        # Calculate new queue position
        if existing_sheets:
            max_position = max([sheet.queue_position or 0 for sheet in existing_sheets])
            self.queue_position = max_position + 1
        else:
            self.queue_position = 1
        
        # Update unit capacity
        update_unit_capacity_usage(self.allocated_unit)


# Utility Functions

def _quality_code_lookup_candidates(q_code: str):
    """Normalize quality code variants for Quality Master lookup (127, 0127, etc.)."""
    q = str(q_code or "").strip()
    if not q:
        return []
    out = []
    seen = set()
    variants = [
        q,
        q.lstrip("0") or "0",
        q.zfill(2),
        q.zfill(3),
        (q.lstrip("0") or "0").zfill(2),
        (q.lstrip("0") or "0").zfill(3),
    ]
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _quality_name_by_code(q_code: str) -> str:
    """Resolve Quality Master document name from item-code quality segment (e.g. 127 → HARINI BAGS - GOLD)."""
    if not q_code:
        return ""
    q_code = str(q_code).strip()
    _HARDCODED_QUALITY = {
        "A": "PREMIUM",
        "B": "PLATINUM",
        "C": "SUPER PLATINUM",
        "D": "GOLD",
        "E": "SILVER",
        "F": "BRONZE",
        "G": "CLASSIC",
        "H": "SUPER CLASSIC",
        "I": "LIFE STYLE",
        "J": "ECO SPECIAL",
        "K": "ECO GREEN",
        "L": "SUPER ECO",
        "M": "ULTRA",
        "N": "DELUXE",
        "O": "VIRGIN MIX - GOLD MIX",
        "P": "MID MIX - CLASSIC MIX",
        "Q": "ECO MIX",
        "R": "DELUXE MIX",
    }
    if len(q_code) == 1 and q_code.upper() in _HARDCODED_QUALITY:
        return _HARDCODED_QUALITY[q_code.upper()]

    if not frappe.db.exists("DocType", "Quality Master"):
        return ""

    code_fields = ("custom_quality_code", "quality_code", "short_code", "code")
    for cand in _quality_code_lookup_candidates(q_code):
        try:
            if frappe.db.exists("Quality Master", cand):
                master_name = frappe.db.get_value("Quality Master", cand, "name")
                if master_name:
                    return str(master_name).strip()
        except Exception:
            pass
        for fn in code_fields:
            try:
                if not frappe.db.has_column("Quality Master", fn):
                    continue
                master_name = frappe.db.get_value("Quality Master", {fn: cand}, "name")
                if master_name:
                    return str(master_name).strip()
            except Exception:
                continue

    return ""


def _color_name_by_code(c_code: str) -> str:
    """Resolve color name by code from Colour Master."""
    if not c_code:
        return ""
    c_code = str(c_code).strip()

    def _norm_tokens(v: str):
        s = str(v or "").strip()
        if not s:
            return set()
        d = "".join(ch for ch in s if ch.isdigit())
        out = {s}
        if d:
            out.add(d)
            out.add(d.lstrip("0") or "0")
            out.add((d.lstrip("0") or "0").zfill(3))
            if len(d) >= 3:
                out.add(d[-3:])
        return {x.strip() for x in out if str(x or "").strip()}

    wanted = _norm_tokens(c_code)
    for fn in ("colour_code", "custom_colour_code", "custom_color_code", "color_code", "short_code", "code"):
        try:
            row = frappe.db.get_value(
                "Colour Master",
                {fn: c_code},
                ["name", "colour_name", "custom_colour_name", "color_name", "colour", "color"],
                as_dict=True,
            )
            if row:
                return str(
                    row.get("colour_name")
                    or row.get("custom_colour_name")
                    or row.get("color_name")
                    or row.get("colour")
                    or row.get("color")
                    or row.get("name")
                    or ""
                ).strip().upper()
        except Exception:
            continue
    # Fallback: compare normalized tokens to support messy code formats.
    try:
        cols = set(frappe.db.get_table_columns("Colour Master") or [])
        code_cols = [c for c in ("colour_code", "custom_colour_code", "custom_color_code", "color_code", "short_code", "code") if c in cols]
        name_cols = [c for c in ("colour_name", "custom_colour_name", "color_name", "colour", "color") if c in cols]
        if code_cols:
            rows = frappe.get_all("Colour Master", fields=list(dict.fromkeys(["name"] + code_cols + name_cols)), limit_page_length=0) or []
            for rr in rows:
                row_tokens = set()
                for c in code_cols:
                    row_tokens |= _norm_tokens(rr.get(c))
                if not row_tokens.intersection(wanted):
                    continue
                for ncol in ("colour_name", "custom_colour_name", "color_name", "colour", "color", "name"):
                    v = str(rr.get(ncol) or "").strip()
                    if v:
                        return v.upper()
    except Exception:
        pass
    return ""


def extract_quality_and_color(item_name, item_code=None):
    """Extract quality and color (prefer item-code index + masters, fallback item-name parse)."""
    QUAL_LIST = ["SUPER PLATINUM", "SUPER CLASSIC", "SUPER ECO", "ECO SPECIAL", 
                 "ECO GREEN", "ECO SPL", "LIFE STYLE", "LIFESTYLE", "PREMIUM", 
                 "PLATINUM", "CLASSIC", "DELUXE", "BRONZE", "SILVER", "ULTRA", 
                 "GOLD", "UV"]
    QUAL_LIST.sort(key=len, reverse=True)
    
    COL_LIST = ["GOLDEN YELLOW", "BRIGHT WHITE", "SUPER WHITE", "BLACK", "RED", 
                "BLUE", "GREEN", "MILKY WHITE", "SUNSHINE WHITE", "BLEACH WHITE", 
                "LEMON YELLOW", "BRIGHT ORANGE", "DARK ORANGE", "BABY PINK", 
                "DARK PINK", "CRIMSON RED", "LIGHT MAROON", "DARK MAROON", 
                "MEDICAL BLUE", "PEACOCK BLUE", "RELIANCE GREEN", "PARROT GREEN", 
                "ROYAL BLUE", "NAVY BLUE", "LIGHT GREY", "DARK GREY", 
                "CHOCOLATE BROWN", "LIGHT BEIGE", "DARK BEIGE", "PURPLE", "WHITE MIX", 
                "BLACK MIX", "COLOR MIX", "BEIGE MIX", "WHITE"]
    COL_LIST.sort(key=len, reverse=True)
    
    quality = ""
    color = ""
    item_upper = (item_name or "").upper()

    # 1) Prefer item_code index decoding for all process codes:
    #    quality code -> [3:6], color code -> [6:9]
    ic = str(item_code or "").strip()
    digits = "".join(ch for ch in ic if ch.isdigit())
    if len(digits) >= 9:
        q_code = digits[3:6]
        c_code = digits[6:9]
        quality = _quality_name_by_code(q_code) or quality
        color = _color_name_by_code(c_code) or color

    # Item-name fallback only when code/master lookup did not resolve.
    if not quality:
        for qual in QUAL_LIST:
            if qual in item_upper:
                quality = qual
                break

    if not color:
        for col in COL_LIST:
            if col in item_upper:
                color = col
                break
    
    return (quality or "").strip().upper(), (color or "").strip().upper()


def get_unit_daily_capacity(unit_name):
    """Get total daily capacity for a unit"""
    capacity = frappe.db.get_value("Unit Capacity", 
                                   unit_name, 
                                   ["day_shift_capacity_kg", "night_shift_capacity_kg"],
                                   as_dict=True)
    
    if capacity:
        return flt(capacity.day_shift_capacity_kg) + flt(capacity.night_shift_capacity_kg)
    return 0


def update_unit_capacity_usage(unit_name):
    """Update current queue weight and available capacity"""
    # Get all finalized sheets for this unit
    sheets = frappe.get_all(
        PLANNING_SHEET_DOCTYPE,
        filters={
            "allocated_unit": unit_name,
            "planning_status": ["in", ["Finalized", "In Production"]],
            "docstatus": 1,
        },
        fields=["total_weight"],
    )
    
    total_queue_weight = sum([flt(sheet.total_weight) for sheet in sheets])
    
    # Update unit capacity
    unit_capacity = frappe.get_doc("Unit Capacity", unit_name)
    unit_capacity.current_queue_weight = total_queue_weight
    unit_capacity.queue_count = len(sheets)
    
    total_capacity = flt(unit_capacity.day_shift_capacity_kg) + flt(unit_capacity.night_shift_capacity_kg)
    unit_capacity.available_capacity = total_capacity - total_queue_weight
    unit_capacity.last_updated = now_datetime()
    
    unit_capacity.save(ignore_permissions=True)


# Scheduled Tasks

def daily_capacity_reset():
    """Reset capacity counters daily"""
    units = frappe.get_all("Unit Capacity", filters={"is_active": 1})
    
    for unit in units:
        update_unit_capacity_usage(unit.name)


def update_production_queue():
    """Update production queue hourly"""
    # Get all units
    units = frappe.get_all("Unit Capacity", filters={"is_active": 1}, pluck="name")
    
    for unit in units:
        # Get all sheets in production
        sheets = frappe.get_all(
            PLANNING_SHEET_DOCTYPE,
            filters={
                "allocated_unit": unit,
                "planning_status": "In Production",
                "docstatus": 1,
            },
            fields=["name", "total_weight", "estimated_production_days"],
        )
        
        # Check if any sheets are completed (logic can be enhanced)
        for sheet in sheets:
            # This is a placeholder - implement actual completion logic
            pass
# ------------------------------------------------------------
# AUTOMATED PLANNING SHEET CREATION (SALES ORDER HOOK)
# ------------------------------------------------------------

def auto_create_planning_sheet(doc, method=None):
    """Called on Sales Order Submit to create a Planning Sheet automatically."""
    try:
        # Avoid double creation
        if frappe.db.exists("Planning Sheet", {"sales_order": doc.name, "docstatus": ["<", 2]}):
            return

        ps = frappe.new_doc("Planning Sheet")
        ps.sales_order = doc.name
        ps.customer = doc.customer
        ps.ordered_date = doc.transaction_date
        ps.delivery_date = doc.delivery_date
        ps.planning_status = "Draft"
        
        # Populate Items (skip CY-* cylinder reference lines — not planned)
        from production_entry.production_planning.scheduler_api import _is_cylinder_yield_so_item
        for item in doc.items:
            if _is_cylinder_yield_so_item(item.item_code):
                continue
            ps.append("planned_items", {
                "sales_order_item": item.name,
                "item_code": item.item_code,
                "item_name": item.item_name,
                "qty": item.qty,
                "uom": item.uom,
                "gsm": item.get("gsm") or 0,
                "width_inch": item.get("width_inch") or 0
            })

        # Fix MandatoryError: quality
        if not ps.get("quality"):
            ps.quality = "Standard"

        ps.flags.ignore_permissions = True
        ps.insert()
        frappe.db.commit()
        
        frappe.msgprint(f"Planning Sheet <b>{ps.name}</b> created and synced from Sales Order.")
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Auto Create Planning Sheet Failed")

@frappe.whitelist()
def sync_to_planning_table(doc, method=None):
    """Sync Production Plan data back to Planning Table if needed."""
    pass


# Whitelisted Methods

@frappe.whitelist()
def get_unit_queue_status(unit_name):
    """Get current queue status for a unit"""
    sheets = frappe.get_all(
        PLANNING_SHEET_DOCTYPE,
        filters={
            "allocated_unit": unit_name,
            "planning_status": ["in", ["Finalized", "In Production"]],
            "docstatus": 1,
        },
        fields=["name", "customer", "total_weight", "queue_position", "dod", "planning_status"],
        order_by="queue_position asc",
    )
    
    capacity = frappe.db.get_value("Unit Capacity", unit_name, 
                                   ["current_queue_weight", "available_capacity", 
                                    "day_shift_capacity_kg", "night_shift_capacity_kg"],
                                   as_dict=True)
    
    return {
        "sheets": sheets,
        "capacity": capacity
    }


@frappe.whitelist()
def get_quality_based_recommendation(quality, gsm):
    """Get unit recommendation based on quality and GSM"""
    UNIT_1 = ["SUPER PLATINUM", "PLATINUM", "PREMIUM", "GOLD", "SUPER CLASSIC"]
    UNIT_2 = ["GOLD", "SILVER", "BRONZE", "CLASSIC", "ECO SPECIAL", "ECO SPL"]
    UNIT_3 = ["SUPER PLATINUM", "PLATINUM", "PREMIUM", "GOLD", "SILVER", "BRONZE"]
    
    quality_upper = quality.upper() if quality else ""
    gsm_value = flt(gsm)
    
    recommended_unit = None
    
    if gsm_value > 50 and quality_upper in UNIT_1:
        recommended_unit = "Unit 1"
    elif gsm_value > 20 and quality_upper in UNIT_2:
        recommended_unit = "Unit 2"
    elif gsm_value > 10 and quality_upper in UNIT_3:
        recommended_unit = "Unit 3"
    elif gsm_value > 10:
        recommended_unit = "Unit 4"
    
    return recommended_unit


# Validation Hook
def validate_planning_sheet(doc, method):
    """Called from hooks on validate"""
    doc.validate()

# Unit Allocation Hook
def allocate_unit(doc, method):
    """Called from hooks before save"""
    doc.before_save()

# Queue Update Hook
def update_queue(doc, method):
    """Called from hooks on submit"""
    doc.on_submit()

