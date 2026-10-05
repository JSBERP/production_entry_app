# -*- coding: utf-8 -*-
"""Planning Sheet bag-process stock check: batch inquiry, preview, apply Stock movement."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from production_entry.production_planning.scheduler_api import (
	ALL_BAG_FG_PROCESS_CODES,
	MOVEMENT_DESPATCH,
	MOVEMENT_STOCK,
	MOVEMENT_TRANSFER,
	PLANNING_MOVEMENT_TYPE_FIELD,
	_bom_item_process_code,
	_is_printed_bopp_item_code,
	_item_process_prefix,
	_planning_row_sort_key,
	_production_sort_rank,
	_same_fg_design_family,
	_so_line_order_and_fg_map,
)

# Operator can cover these from warehouse stock.
# 100 is the base fabric: stock there skips fabric production and the next process stays open.
# Printed BOPP and Oeko-Tex non woven are never offered.
STOCK_CHECK_PROCESSES = frozenset(
	{
		"100",
		"102",
		"103",
		"104",
		"105",
		"106",
		"107",
		"108",
		"109",
		"113",
		"200",
		"201",
		"202",
		"203",
		"204",
		"205",
		"211",
		"212",
		"213",
		"214",
		"216",
		"217",
		"221",
		"222",
		"223",
		"224",
		"231",
		"232",
		"233",
		"241",
		"242",
		"243",
		"251",
		"252",
		"253",
		"254",
		"255",
		"256",
		"325",
		"326",
	}
)


def _cstr(v) -> str:
	return str(v).strip() if v is not None else ""


def is_stock_movement(movement_type) -> bool:
	from production_entry.production_planning.scheduler_api import normalize_movement_type

	return normalize_movement_type(movement_type) == MOVEMENT_STOCK


def planning_row_hidden_from_board(item) -> bool:
	"""True when row uses Stock movement and must not appear on production boards."""
	if not item:
		return False
	if not frappe.db.has_column("Planning Table", PLANNING_MOVEMENT_TYPE_FIELD):
		return False
	from production_entry.production_planning.scheduler_api import normalize_movement_type

	mt = normalize_movement_type(item.get(PLANNING_MOVEMENT_TYPE_FIELD) or "")
	return is_stock_movement(mt)


def should_skip_movement_restamp(row_name: str, doctype: str = "Planning Table") -> bool:
	if not row_name:
		return False
	if frappe.db.has_column(doctype, "custom_stock_locked"):
		if cint(frappe.db.get_value(doctype, row_name, "custom_stock_locked") or 0):
			return True
	if frappe.db.has_column(doctype, PLANNING_MOVEMENT_TYPE_FIELD):
		mt = frappe.db.get_value(doctype, row_name, PLANNING_MOVEMENT_TYPE_FIELD)
		if is_stock_movement(mt):
			return True
	return False


def _soi_key(row) -> str:
	return _cstr(row.get("sales_order_item") or row.get("so_item"))


def _is_oekotex_nonwoven(row) -> bool:
	blob = " ".join(
		[
			_cstr(row.get("item_name")),
			_cstr(row.get("item_code")),
			_cstr(row.get("quality")),
		]
	).upper().replace("-", "").replace(" ", "")
	return "OEKOTEX" in blob


def _stock_check_excluded(row) -> bool:
	ic = _cstr(row.get("item_code"))
	if not ic:
		return True
	if _is_printed_bopp_item_code(ic) or ic.upper().startswith("PB"):
		return True
	if _is_oekotex_nonwoven(row):
		return True
	return False


def _stock_check_process(row) -> str:
	ic = _cstr(row.get("item_code"))
	return _cstr(_item_process_prefix(ic) or _bom_item_process_code(ic))


def _stock_check_row_allowed(row) -> bool:
	if _stock_check_excluded(row):
		return False
	return _stock_check_process(row) in STOCK_CHECK_PROCESSES


def _planning_sheet_has_bag_fg(planning_sheet_name: str) -> bool:
	rows = frappe.get_all(
		"Planning Table",
		filters={"parent": planning_sheet_name},
		fields=["item_code"],
		limit_page_length=0,
	) or []
	for r in rows:
		if _item_process_prefix(r.get("item_code")) in ALL_BAG_FG_PROCESS_CODES:
			return True
	return False


def _is_despatch_fg_row(row, so_fg_by_soi) -> bool:
	ic = _cstr(row.get("item_code"))
	soik = _soi_key(row)
	if not ic or not soik:
		return False
	so_it = so_fg_by_soi.get(soik)
	if not so_it:
		return False
	so_ic = _cstr(getattr(so_it, "item_code", None) or (so_it.get("item_code") if isinstance(so_it, dict) else ""))
	return bool(so_ic and (ic == so_ic or _same_fg_design_family(ic, so_ic)))


def _load_planning_table_rows(planning_sheet_name: str) -> list[dict]:
	fields = ["name", "item_code", "item_name", "qty", "uom", "idx", "parent"]
	for col in (
		"sales_order_item",
		"so_item",
		PLANNING_MOVEMENT_TYPE_FIELD,
		"custom_stock_locked",
		"custom_stock_batch_no",
		"custom_stock_warehouse",
		"custom_stock_company",
	):
		if frappe.db.has_column("Planning Table", col):
			fields.append(col)
	return frappe.get_all(
		"Planning Table",
		filters={"parent": planning_sheet_name},
		fields=fields,
		order_by="idx asc",
		limit_page_length=0,
	) or []


def _warehouse_company_map() -> dict[str, str]:
	out = {}
	for r in frappe.get_all("Warehouse", fields=["name", "company"], limit_page_length=0) or []:
		out[_cstr(r.name)] = _cstr(r.company)
	return out


def query_batches_all_warehouses(item_code: str) -> list[dict]:
	"""Batch stock across all warehouses with company names."""
	item_code = _cstr(item_code)
	if not item_code:
		return []
	wh_company = _warehouse_company_map()
	acc: dict[tuple, float] = {}

	for r in frappe.db.sql(
		"""
		SELECT batch_no, warehouse, SUM(actual_qty) AS qty
		FROM `tabStock Ledger Entry`
		WHERE IFNULL(is_cancelled, 0) = 0
		  AND IFNULL(item_code, '') = %s
		  AND IFNULL(batch_no, '') != ''
		GROUP BY batch_no, warehouse
		HAVING SUM(actual_qty) > 0
		""",
		(item_code,),
		as_dict=True,
	):
		bn = _cstr(r.get("batch_no"))
		wh = _cstr(r.get("warehouse"))
		q = flt(r.get("qty") or 0)
		if bn and wh and q > 0:
			key = (bn, wh)
			acc[key] = acc.get(key, 0.0) + q

	if frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
		try:
			if frappe.db.exists("DocType", "Serial and Batch Entry"):
				sb_meta = frappe.get_meta("Serial and Batch Entry")
				batch_field = next(
					(fn for fn in ("batch_no", "batch", "batch_id") if sb_meta.has_field(fn)),
					"",
				)
				qty_field = next((fn for fn in ("qty", "quantity") if sb_meta.has_field(fn)), "")
				if batch_field and qty_field:
					rows = frappe.db.sql(
						f"""
						SELECT
							sbe.`{batch_field}` AS batch_no,
							sle.warehouse,
							SUM(
								CASE
									WHEN IFNULL(sle.actual_qty, 0) < 0
										THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
									ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
								END
							) AS qty
						FROM `tabStock Ledger Entry` sle
						INNER JOIN `tabSerial and Batch Entry` sbe
							ON sbe.parent = sle.serial_and_batch_bundle
						WHERE IFNULL(sle.is_cancelled, 0) = 0
						  AND IFNULL(sle.item_code, '') = %s
						  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
						  AND IFNULL(sbe.`{batch_field}`, '') != ''
						GROUP BY sbe.`{batch_field}`, sle.warehouse
						HAVING SUM(
							CASE
								WHEN IFNULL(sle.actual_qty, 0) < 0
									THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
								ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
							END
						) > 0
						""",
						(item_code,),
						as_dict=True,
					)
					for r in rows or []:
						bn = _cstr(r.get("batch_no"))
						wh = _cstr(r.get("warehouse"))
						q = flt(r.get("qty") or 0)
						if bn and wh and q > 0:
							key = (bn, wh)
							acc[key] = max(acc.get(key, 0.0), q)
		except Exception:
			pass

	out = []
	for (bn, wh), q in acc.items():
		co = wh_company.get(wh) or ""
		out.append(
			{
				"batch_no": bn,
				"warehouse": wh,
				"company": co,
				"company_name": frappe.db.get_value("Company", co, "company_name") if co else co,
				"qty": flt(q, 3),
			}
		)
	out.sort(key=lambda x: flt(x.get("qty") or 0), reverse=True)
	return out


def _pick_best_batch(batches: list[dict], required_qty: float) -> dict | None:
	need = flt(required_qty)
	if need <= 0 or not batches:
		return None
	for b in batches:
		if flt(b.get("qty") or 0) + 1e-9 >= need:
			return b
	return None


def _board_label_for_process(proc: str) -> str:
	proc = _cstr(proc)
	mapping = {
		"100": _("Fabric board"),
		"103": _("Slitting board"),
		"104": _("Lamination board"),
		"105": _("Printing board"),
		"106": _("Printing board"),
		"107": _("Lamination board"),
		"102": _("Rewinding board"),
	}
	if proc in ALL_BAG_FG_PROCESS_CODES:
		return _("Box Bag / W-D-CUT board")
	return mapping.get(proc, _("Production board"))


def _build_stock_row_context(row, so_fg_by_soi, so_line_order, parent_first=False) -> dict | None:
	if not _stock_check_row_allowed(row):
		return None
	ic = _cstr(row.get("item_code"))
	if not ic:
		return None
	proc = _stock_check_process(row)
	required = flt(row.get("qty") or 0)
	batches = query_batches_all_warehouses(ic)
	total_avail = flt(sum(flt(b.get("qty") or 0) for b in batches), 3)
	sufficient = total_avail + 1e-9 >= required if required > 0 else False
	best = _pick_best_batch(batches, required)
	return {
		"planning_table_row": row.get("name"),
		"item_code": ic,
		"item_name": _cstr(row.get("item_name")),
		"process": proc,
		"sales_order_item": _soi_key(row),
		"required_qty": flt(required, 3),
		"uom": _cstr(row.get("uom")),
		"available_qty": total_avail,
		"sufficient": sufficient,
		"movement_type": _cstr(row.get(PLANNING_MOVEMENT_TYPE_FIELD)),
		"stock_locked": cint(row.get("custom_stock_locked") or 0),
		"batches": batches,
		"proposed_batch": best,
		"sort_key": _planning_row_sort_key(row, so_line_order, parent_first, so_fg_by_soi),
		"production_rank": _production_sort_rank(ic),
	}


def _group_rows_by_soi(rows_ctx: list[dict]) -> list[dict]:
	groups: dict[str, list] = {}
	for r in rows_ctx:
		k = _cstr(r.get("sales_order_item")) or "__none__"
		groups.setdefault(k, []).append(r)
	out = []
	for soi, lines in groups.items():
		lines.sort(key=lambda x: x.get("sort_key") or (99999, 999, 0, ""))
		out.append({"sales_order_item": soi, "lines": lines})
	return out


@frappe.whitelist()
def get_planning_sheet_stock_check_context(planning_sheet_name: str | None = None):
	planning_sheet_name = _cstr(planning_sheet_name)
	if not planning_sheet_name or not frappe.db.exists("Planning sheet", planning_sheet_name):
		frappe.throw(_("Planning Sheet not found."), title=_("Stock Check"))
	if not any(_stock_check_row_allowed(row) for row in _load_planning_table_rows(planning_sheet_name)):
		frappe.throw(
			_("This Planning Sheet has no rows that can be covered from stock."),
			title=_("Stock Check"),
		)
	so_name = frappe.db.get_value("Planning sheet", planning_sheet_name, "sales_order")
	so_line_order, so_fg_by_soi = _so_line_order_and_fg_map(so_name)
	from production_entry.production_planning.scheduler_api import _planning_sheet_uses_parent_first_sort

	parent_first = _planning_sheet_uses_parent_first_sort(planning_sheet_name)
	rows = _load_planning_table_rows(planning_sheet_name)
	ctx_rows = []
	for row in rows:
		built = _build_stock_row_context(row, so_fg_by_soi, so_line_order, parent_first)
		if built:
			ctx_rows.append(built)
	mode = _cstr(frappe.db.get_value("Planning sheet", planning_sheet_name, "custom_stock_check_mode") or "Manual")
	if mode not in ("Manual", "Auto"):
		mode = "Manual"
	return {
		"planning_sheet": planning_sheet_name,
		"stock_check_mode": mode,
		"groups": _group_rows_by_soi(ctx_rows),
		"eligible_count": len(ctx_rows),
		"sufficient_count": sum(1 for r in ctx_rows if r.get("sufficient")),
	}


def _parse_selections(selections_json):
	if isinstance(selections_json, str):
		selections_json = json.loads(selections_json or "[]")
	if not isinstance(selections_json, list):
		frappe.throw(_("Invalid selections payload."))
	return selections_json


def _row_map(planning_sheet_name: str) -> dict[str, dict]:
	return {r["name"]: r for r in _load_planning_table_rows(planning_sheet_name)}


def _cascade_descendant_row_names(parent_row: dict, all_rows: list[dict]) -> list[str]:
	"""Downstream BOM rows on same SO line (lower production rank = fabric-first chain)."""
	parent_soi = _soi_key(parent_row)
	parent_rank = _production_sort_rank(parent_row.get("item_code"))
	out = []
	for r in all_rows:
		if _soi_key(r) != parent_soi:
			continue
		if _cstr(r.get("name")) == _cstr(parent_row.get("name")):
			continue
		if _production_sort_rank(r.get("item_code")) >= parent_rank:
			continue
		if not _stock_check_row_allowed(r):
			continue
		out.append(r.get("name"))
	return out


def _preview_one_selection(row, all_rows, selection=None) -> dict:
	sel = selection or {}
	required = flt(row.get("qty") or 0)
	batches = query_batches_all_warehouses(_cstr(row.get("item_code")))
	batch_no = _cstr(sel.get("batch_no"))
	warehouse = _cstr(sel.get("warehouse"))
	pick = None
	if batch_no and warehouse:
		for b in batches:
			if b.get("batch_no") == batch_no and b.get("warehouse") == warehouse:
				pick = b
				break
	if not pick:
		pick = _pick_best_batch(batches, required)
	can_apply = bool(pick and flt(pick.get("qty") or 0) + 1e-9 >= required)
	proc = _item_process_prefix(row.get("item_code"))
	cascade = []
	for cn in _cascade_descendant_row_names(row, all_rows):
		cr = next((x for x in all_rows if x.get("name") == cn), None)
		if not cr:
			continue
		cproc = _item_process_prefix(cr.get("item_code"))
		cascade.append(
			{
				"planning_table_row": cn,
				"item_code": _cstr(cr.get("item_code")),
				"process": cproc,
				"cascade_reason": _("Hidden from {0} because parent {1} is covered by stock").format(
					_board_label_for_process(cproc),
					proc or "—",
				),
			}
		)
	return {
		"planning_table_row": row.get("name"),
		"item_code": _cstr(row.get("item_code")),
		"item_name": _cstr(row.get("item_name")),
		"process": proc,
		"required_qty": flt(required, 3),
		"uom": _cstr(row.get("uom")),
		"can_apply": can_apply,
		"proposed_batch": pick,
		"batches": batches,
		"cascade": cascade,
	}


@frappe.whitelist()
def preview_planning_sheet_stock_apply(
	planning_sheet_name: str | None = None,
	selections_json=None,
	mode: str = "manual",
):
	planning_sheet_name = _cstr(planning_sheet_name)
	if not planning_sheet_name:
		frappe.throw(_("Planning Sheet is required."))
	all_rows = _load_planning_table_rows(planning_sheet_name)
	by_name = _row_map(planning_sheet_name)
	mode = _cstr(mode).lower() or "manual"
	preview_lines = []

	if mode == "auto":
		so_name = frappe.db.get_value("Planning sheet", planning_sheet_name, "sales_order")
		so_line_order, so_fg_by_soi = _so_line_order_and_fg_map(so_name)
		from production_entry.production_planning.scheduler_api import _planning_sheet_uses_parent_first_sort

		parent_first = _planning_sheet_uses_parent_first_sort(planning_sheet_name)
		for row in all_rows:
			built = _build_stock_row_context(row, so_fg_by_soi, so_line_order, parent_first)
			if built and built.get("sufficient"):
				preview_lines.append(_preview_one_selection(row, all_rows, built.get("proposed_batch")))
	else:
		for sel in _parse_selections(selections_json):
			rn = _cstr(sel.get("planning_table_row") or sel.get("name"))
			row = by_name.get(rn)
			if not row:
				continue
			preview_lines.append(_preview_one_selection(row, all_rows, sel))

	seen_cascade = set()
	cascade_all = []
	for pl in preview_lines:
		for c in pl.get("cascade") or []:
			cn = _cstr(c.get("planning_table_row"))
			if cn and cn not in seen_cascade:
				seen_cascade.add(cn)
				cascade_all.append(c)

	return {
		"planning_sheet": planning_sheet_name,
		"mode": mode,
		"preview_lines": preview_lines,
		"cascade_lines": cascade_all,
		"can_apply_any": any(pl.get("can_apply") for pl in preview_lines),
	}


def _mirror_stock_to_psi(planning_sheet_name: str, pt_row: dict, values: dict):
	if not frappe.db.table_exists("Planning sheet Item"):
		return
	soi = _soi_key(pt_row)
	ic = _cstr(pt_row.get("item_code"))
	filters = {"parent": planning_sheet_name, "item_code": ic}
	if frappe.db.has_column("Planning sheet Item", "sales_order_item") and soi:
		filters["sales_order_item"] = soi
	elif frappe.db.has_column("Planning sheet Item", "so_item") and soi:
		filters["so_item"] = soi
	names = frappe.get_all("Planning sheet Item", filters=filters, pluck="name", limit=1)
	if not names:
		return
	frappe.db.set_value("Planning sheet Item", names[0], values, update_modified=False)


def _apply_stock_to_row(planning_sheet_name: str, pt_name: str, batch_info: dict, cascade: bool = False):
	if cascade:
		batch_info = {}
	values = {
		PLANNING_MOVEMENT_TYPE_FIELD: MOVEMENT_STOCK,
		"custom_stock_locked": 1,
		"custom_stock_batch_no": _cstr(batch_info.get("batch_no")),
		"custom_stock_warehouse": _cstr(batch_info.get("warehouse")),
		"custom_stock_company": _cstr(batch_info.get("company")),
	}
	frappe.db.set_value("Planning Table", pt_name, values, update_modified=False)
	row = frappe.db.get_value(
		"Planning Table",
		pt_name,
		["item_code", "sales_order_item", "so_item"],
		as_dict=True,
	)
	if row:
		_mirror_stock_to_psi(planning_sheet_name, row, values)


@frappe.whitelist()
def apply_planning_sheet_stock_selections(
	planning_sheet_name: str | None = None,
	selections_json=None,
	confirmed=0,
):
	if not cint(confirmed):
		frappe.throw(_("Confirm stock assignment before applying."), title=_("Confirmation required"))
	planning_sheet_name = _cstr(planning_sheet_name)
	if not planning_sheet_name:
		frappe.throw(_("Planning Sheet is required."))
	preview = preview_planning_sheet_stock_apply(planning_sheet_name, selections_json, mode="manual")
	applied = []
	for pl in preview.get("preview_lines") or []:
		if not pl.get("can_apply"):
			continue
		pick = pl.get("proposed_batch") or {}
		pt_name = _cstr(pl.get("planning_table_row"))
		_apply_stock_to_row(planning_sheet_name, pt_name, pick)
		applied.append(pt_name)
		for c in pl.get("cascade") or []:
			cn = _cstr(c.get("planning_table_row"))
			if cn and cn not in applied:
				_apply_stock_to_row(planning_sheet_name, cn, pick, cascade=True)
				applied.append(cn)
	frappe.db.commit()
	return {"status": "ok", "applied_rows": applied, "count": len(applied)}


@frappe.whitelist()
def apply_planning_sheet_stock_auto(planning_sheet_name: str | None = None, confirmed=0):
	if not cint(confirmed):
		frappe.throw(_("Confirm stock assignment before applying."), title=_("Confirmation required"))
	planning_sheet_name = _cstr(planning_sheet_name)
	preview = preview_planning_sheet_stock_apply(planning_sheet_name, selections_json="[]", mode="auto")
	applied = []
	for pl in preview.get("preview_lines") or []:
		if not pl.get("can_apply"):
			continue
		pick = pl.get("proposed_batch") or {}
		pt_name = _cstr(pl.get("planning_table_row"))
		_apply_stock_to_row(planning_sheet_name, pt_name, pick)
		applied.append(pt_name)
		for c in pl.get("cascade") or []:
			cn = _cstr(c.get("planning_table_row"))
			if cn and cn not in applied:
				_apply_stock_to_row(planning_sheet_name, cn, pick, cascade=True)
				applied.append(cn)
	frappe.db.commit()
	return {"status": "ok", "applied_rows": applied, "count": len(applied)}


@frappe.whitelist()
def clear_planning_sheet_stock(planning_sheet_name: str | None = None, planning_table_rows_json=None, confirmed=0):
	if not cint(confirmed):
		frappe.throw(_("Confirm before clearing Stock movement."), title=_("Confirmation required"))
	planning_sheet_name = _cstr(planning_sheet_name)
	row_names = _parse_selections(planning_table_rows_json)
	if not row_names:
		rows = _load_planning_table_rows(planning_sheet_name)
		row_names = [
			{"planning_table_row": r.get("name")}
			for r in rows
			if is_stock_movement(r.get(PLANNING_MOVEMENT_TYPE_FIELD))
		]
	so_name = frappe.db.get_value("Planning sheet", planning_sheet_name, "sales_order")
	_, so_fg_by_soi = _so_line_order_and_fg_map(so_name)
	cleared = []
	for sel in row_names:
		rn = _cstr(sel.get("planning_table_row") or sel.get("name"))
		if not rn:
			continue
		row = frappe.db.get_value("Planning Table", rn, ["item_code", "sales_order_item", "so_item"], as_dict=True)
		if not row:
			continue
		mt = MOVEMENT_DESPATCH if _is_despatch_fg_row(row, so_fg_by_soi) else MOVEMENT_TRANSFER
		values = {
			PLANNING_MOVEMENT_TYPE_FIELD: mt,
			"custom_stock_locked": 0,
			"custom_stock_batch_no": "",
			"custom_stock_warehouse": "",
			"custom_stock_company": "",
		}
		frappe.db.set_value("Planning Table", rn, values, update_modified=False)
		_mirror_stock_to_psi(planning_sheet_name, row, values)
		cleared.append(rn)
	frappe.db.commit()
	return {"status": "ok", "cleared_rows": cleared, "count": len(cleared)}


def revert_unconfirmed_stock_on_planning_sheet(doc):
	"""Stock movement may only be set via Check Stock (custom_stock_locked=1)."""
	if not doc:
		return
	so_name = _cstr(doc.get("sales_order"))
	_, so_fg_by_soi = _so_line_order_and_fg_map(so_name)
	reverted = []

	def _check_row(row, table_label):
		if not row:
			return
		mt = row.get(PLANNING_MOVEMENT_TYPE_FIELD)
		if not is_stock_movement(mt):
			return
		if cint(row.get("custom_stock_locked") or 0):
			return
		row[PLANNING_MOVEMENT_TYPE_FIELD] = (
			MOVEMENT_DESPATCH if _is_despatch_fg_row(row, so_fg_by_soi) else MOVEMENT_TRANSFER
		)
		row["custom_stock_locked"] = 0
		row["custom_stock_batch_no"] = ""
		row["custom_stock_warehouse"] = ""
		row["custom_stock_company"] = ""
		reverted.append(_cstr(row.get("item_code")) or table_label)

	for row in doc.get("items") or []:
		_check_row(row, "items")
	for row in doc.get("planned_items") or []:
		_check_row(row, "planned_items")

	if reverted:
		frappe.msgprint(
			_("Movement Type Stock was removed on {0} — use Actions → Check Stock to confirm stock before applying.").format(
				", ".join(reverted[:8]) + ("…" if len(reverted) > 8 else "")
			),
			title=_("Stock confirmation required"),
			indicator="orange",
		)
