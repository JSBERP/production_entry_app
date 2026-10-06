# -*- coding: utf-8 -*-
"""Kapada Usage — FG rolls issued for packing, one document per date + shift + unit."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

DOCTYPE = "Kapada Usage"


def _cstr(val) -> str:
	return (val or "").strip() if val is not None else ""


def _normalize_shift(shift: str) -> str:
	s = _cstr(shift)
	if "night" in s.lower():
		return "Night Shift"
	if "day" in s.lower():
		return "Day Shift"
	return s


def _parse_rows(rows) -> list:
	if isinstance(rows, str):
		try:
			rows = json.loads(rows)
		except Exception:
			rows = []
	return rows if isinstance(rows, list) else []


def _doctype_ready() -> bool:
	return bool(frappe.db.exists("DocType", DOCTYPE) and frappe.db.table_exists(DOCTYPE))


def _find_doc(run_date=None, shift=None, custom_unit=None, gsm_shift_session=None, name=None):
	if not _doctype_ready():
		return None
	name = _cstr(name)
	if name and frappe.db.exists(DOCTYPE, name):
		return name
	session = _cstr(gsm_shift_session)
	unit = _cstr(custom_unit)
	shift_n = _normalize_shift(shift)
	rd = getdate(run_date) if run_date else None
	if session and unit:
		found = frappe.db.get_value(
			DOCTYPE,
			{"gsm_shift_session": session, "custom_unit": unit},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	if rd and shift_n and unit:
		found = frappe.db.get_value(
			DOCTYPE,
			{"run_date": rd, "shift": shift_n, "custom_unit": unit},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	return None


def _get_or_create(run_date=None, shift=None, custom_unit=None, gsm_shift_session=None, name=None):
	if not _doctype_ready():
		frappe.throw(_("Kapada Usage is not installed. Run bench migrate."))
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=name,
	)
	if found:
		return frappe.get_doc(DOCTYPE, found)
	unit = _cstr(custom_unit)
	shift_n = _normalize_shift(shift)
	rd = getdate(run_date) if run_date else None
	if not (rd and shift_n and unit):
		frappe.throw(_("Date, Shift, and Unit are required."))
	doc = frappe.new_doc(DOCTYPE)
	doc.run_date = rd
	doc.shift = shift_n
	doc.custom_unit = unit
	session = _cstr(gsm_shift_session)
	if session:
		doc.gsm_shift_session = session
	return doc


def _row_payload(row) -> dict:
	return {
		"batch_no": _cstr(getattr(row, "batch_no", None)),
		"quality": _cstr(getattr(row, "quality", None)),
		"color": _cstr(getattr(row, "color", None)),
		"gsm": flt(getattr(row, "gsm", None) or 0),
		"width": flt(getattr(row, "width", None) or 0),
		"used_qty": flt(getattr(row, "used_qty", None) or 0),
	}


def _open_rows(doc) -> list:
	raw = getattr(doc, "open_rolls", None) or "[]"
	if isinstance(raw, list):
		rows = raw
	else:
		try:
			rows = json.loads(raw or "[]")
		except Exception:
			rows = []
	clean = []
	for row in rows if isinstance(rows, list) else []:
		if not isinstance(row, dict) or not _cstr(row.get("batch_no")):
			continue
		clean.append(
			{
				"batch_no": _cstr(row.get("batch_no")),
				"quality": _cstr(row.get("quality")),
				"color": _cstr(row.get("color")),
				"gsm": flt(row.get("gsm") or 0),
				"width": flt(row.get("width") or 0),
				"qty": flt(row.get("qty") or 0, 3),
				"balance_qty": "",
			}
		)
	return clean


def _doc_payload(doc) -> dict:
	return {
		"name": doc.name,
		"run_date": str(doc.run_date or ""),
		"shift": doc.shift or "",
		"custom_unit": doc.custom_unit or "",
		"gsm_shift_session": doc.gsm_shift_session or "",
		"material_issue": doc.material_issue or "",
		"rows": [_row_payload(r) for r in (doc.rolls or [])],
		"open_rows": _open_rows(doc),
	}


def _resolve_batch(batch_no: str) -> dict:
	bn = _cstr(batch_no)
	if not bn or not frappe.db.exists("DocType", "Batch"):
		frappe.throw(_("Batch {0} was not found.").format(bn or "—"))
	name = bn if frappe.db.exists("Batch", bn) else ""
	if not name:
		name = _cstr(frappe.db.get_value("Batch", {"batch_id": bn}, "name"))
	if not name:
		frappe.throw(_("Batch {0} was not found.").format(bn))
	row = frappe.db.get_value("Batch", name, ["name", "batch_id", "item"], as_dict=True) or {}
	batch_id = _cstr(row.get("batch_id") or row.get("name"))
	return {
		"name": _cstr(row.get("name")),
		"batch_id": batch_id,
		"item_code": _cstr(row.get("item")),
	}


def _batch_keys(batch: dict) -> list:
	return [k for k in dict.fromkeys([_cstr(batch.get("batch_id")), _cstr(batch.get("name"))]) if k]


def _positive_rows(rows) -> list:
	clean = []
	for row in rows or []:
		qty = flt(row.get("qty") or 0, 3)
		warehouse = _cstr(row.get("warehouse"))
		item_code = _cstr(row.get("item_code"))
		if qty > 0.0001 and warehouse and item_code:
			clean.append({"item_code": item_code, "warehouse": warehouse, "qty": qty})
	clean.sort(key=lambda r: r["qty"], reverse=True)
	return clean


def _classic_sle_rows(keys) -> list:
	if not keys:
		return []
	return _positive_rows(
		frappe.db.sql(
			"""
			SELECT item_code, warehouse, SUM(actual_qty) AS qty
			FROM `tabStock Ledger Entry`
			WHERE batch_no IN %(batches)s AND IFNULL(is_cancelled, 0) = 0
			GROUP BY item_code, warehouse
			HAVING SUM(actual_qty) > 0.0001
			""",
			{"batches": tuple(keys)},
			as_dict=True,
		)
	)


def _bundle_sle_rows(keys) -> list:
	if not keys or not frappe.db.exists("DocType", "Serial and Batch Entry"):
		return []
	if not frappe.get_meta("Stock Ledger Entry").has_field("serial_and_batch_bundle"):
		return []
	meta = frappe.get_meta("Serial and Batch Entry")
	batch_field = next((fn for fn in ("batch_no", "batch", "batch_id") if meta.has_field(fn)), "")
	qty_field = next((fn for fn in ("qty", "quantity") if meta.has_field(fn)), "")
	if not batch_field or not qty_field:
		return []
	return _positive_rows(
		frappe.db.sql(
			f"""
			SELECT sle.item_code AS item_code, sle.warehouse AS warehouse,
				SUM(CASE WHEN IFNULL(sle.actual_qty, 0) < 0
					THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
					ELSE ABS(IFNULL(sbe.`{qty_field}`, 0)) END) AS qty
			FROM `tabStock Ledger Entry` sle
			INNER JOIN `tabSerial and Batch Entry` sbe
				ON sbe.parent = sle.serial_and_batch_bundle
			WHERE IFNULL(sle.is_cancelled, 0) = 0
			  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
			  AND IFNULL(sbe.`{batch_field}`, '') IN %(batches)s
			GROUP BY sle.item_code, sle.warehouse
			HAVING qty > 0.0001
			""",
			{"batches": tuple(keys)},
			as_dict=True,
		)
	)


def _erpnext_batch_rows(batch: dict) -> list:
	item_code = _cstr(batch.get("item_code"))
	try:
		from erpnext.stock.doctype.batch.batch import get_batch_qty
	except Exception:
		return []
	rows = []
	for batch_no in _batch_keys(batch):
		try:
			data = get_batch_qty(batch_no=batch_no, item_code=item_code or None)
		except Exception:
			continue
		if isinstance(data, (int, float)):
			continue
		for entry in data or []:
			if isinstance(entry, dict):
				rows.append(
					{
						"item_code": _cstr(entry.get("item_code") or item_code),
						"warehouse": _cstr(entry.get("warehouse")),
						"qty": flt(entry.get("qty") or 0),
					}
				)
	return _positive_rows(rows)


def _batch_master_rows(batch: dict) -> list:
	item_code = _cstr(batch.get("item_code"))
	qty = flt(frappe.db.get_value("Batch", batch.get("name"), "batch_qty") or 0, 3)
	if qty <= 0 or not item_code:
		return []
	warehouses = frappe.db.sql(
		"""
		SELECT warehouse, SUM(actual_qty) AS qty
		FROM `tabStock Ledger Entry`
		WHERE item_code = %(item_code)s AND IFNULL(is_cancelled, 0) = 0
		GROUP BY warehouse
		HAVING SUM(actual_qty) > 0.0001
		ORDER BY SUM(actual_qty) DESC
		""",
		{"item_code": item_code},
		as_dict=True,
	) or []
	if not warehouses:
		return []
	warehouse = _cstr(warehouses[0].get("warehouse"))
	for row in warehouses:
		if "finished goods" in _cstr(row.get("warehouse")).lower():
			warehouse = _cstr(row.get("warehouse"))
			break
	if not warehouse:
		return []
	return [{"item_code": item_code, "warehouse": warehouse, "qty": qty}]


def _stock_rows(batch: dict) -> list:
	for finder in (_classic_sle_rows, _bundle_sle_rows):
		rows = finder(_batch_keys(batch))
		if rows:
			return rows
	rows = _erpnext_batch_rows(batch)
	if rows:
		return rows
	return _batch_master_rows(batch)


def _roll_spec(batch_no: str, item_code: str) -> dict:
	from production_entry.production_planning.transfer_logistics import (
		_roll_spec_dict,
		_spr_item_query_fields,
	)

	row = {}
	if batch_no and frappe.db.table_exists("Shaft Production Run Item"):
		spr = frappe.db.get_value(
			"Shaft Production Run Item",
			{"batch_no": batch_no},
			_spr_item_query_fields(),
			as_dict=True,
		)
		if spr:
			row.update(spr)
	if item_code and not row.get("item_code"):
		row["item_code"] = item_code
	return _roll_spec_dict(row, row.get("item_code") or item_code)


def _available_qty(stock_rows) -> float:
	return flt(sum(flt(r.get("qty") or 0) for r in stock_rows or []), 3)


@frappe.whitelist()
def lookup_kapada_roll(batch_no=None):
	batch = _resolve_batch(batch_no)
	stock_rows = _stock_rows(batch)
	qty = _available_qty(stock_rows)
	if qty <= 0:
		frappe.throw(_("Roll {0} has no balance left.").format(batch["batch_id"]))
	item_code = _cstr(batch.get("item_code") or (stock_rows[0].get("item_code") if stock_rows else ""))
	spec = _roll_spec(batch["batch_id"], item_code)
	return {
		"batch_no": batch["batch_id"],
		"item_code": item_code,
		"quality": spec.get("quality") or "",
		"color": spec.get("color") or "",
		"gsm": cint(spec.get("gsm") or 0) or flt(spec.get("gsm") or 0),
		"width": flt(spec.get("width_inch") or 0),
		"qty": qty,
	}


def _issue_lines(batch: dict, used_qty: float) -> list:
	remaining = flt(used_qty, 3)
	lines = []
	for row in _stock_rows(batch):
		if remaining <= 0:
			break
		available = flt(row.get("qty") or 0, 3)
		if available <= 0:
			continue
		take = flt(min(available, remaining), 3)
		if take <= 0:
			continue
		lines.append(
			{
				"item_code": _cstr(row.get("item_code") or batch.get("item_code")),
				"warehouse": _cstr(row.get("warehouse")),
				"batch_no": batch["batch_id"],
				"qty": take,
			}
		)
		remaining = flt(remaining - take, 3)
	if remaining > 0.001 or not lines:
		frappe.throw(
			_("Roll {0} does not have {1} kg in stock.").format(batch["batch_id"], flt(used_qty, 3))
		)
	return lines


def _create_material_issue(lines: list) -> str:
	if not lines:
		frappe.throw(_("Scan at least one roll."))
	warehouses = [ln["warehouse"] for ln in lines if ln.get("warehouse")]
	company = ""
	if warehouses:
		company = _cstr(frappe.db.get_value("Warehouse", warehouses[0], "company"))
	if not company:
		frappe.throw(_("Warehouse company was not found for this roll."))
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Issue"
	se.purpose = "Material Issue"
	se.company = company
	if frappe.db.has_column("Stock Entry", "use_serial_batch_fields"):
		se.use_serial_batch_fields = 1
	for ln in lines:
		item_code = ln["item_code"]
		uom = _cstr(frappe.db.get_value("Item", item_code, "stock_uom")) or "Kg"
		row = {
			"item_code": item_code,
			"qty": flt(ln["qty"], 3),
			"s_warehouse": ln["warehouse"],
			"uom": uom,
			"stock_uom": uom,
			"conversion_factor": 1,
			"batch_no": ln["batch_no"],
		}
		if frappe.db.has_column("Stock Entry Detail", "use_serial_batch_fields"):
			row["use_serial_batch_fields"] = 1
		se.append("items", row)
	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


@frappe.whitelist()
def get_kapada_usage(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	doc_name=None,
):
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	if found:
		return _doc_payload(frappe.get_doc(DOCTYPE, found))
	return {
		"name": "",
		"run_date": str(run_date or ""),
		"shift": _normalize_shift(shift),
		"custom_unit": _cstr(custom_unit),
		"gsm_shift_session": _cstr(gsm_shift_session),
		"material_issue": "",
		"rows": [],
		"open_rows": [],
	}


@frappe.whitelist()
def save_kapada_usage(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	doc_name=None,
	rows=None,
):
	"""Save scanned rolls without issuing. Issue only rows that have a balance qty."""
	incoming = _parse_rows(rows)
	doc = _get_or_create(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	issued_batches = {_cstr(r.batch_no) for r in (doc.rolls or []) if _cstr(getattr(r, "batch_no", None))}
	open_keep = []
	prepared = []
	issue_lines = []
	seen = set()
	if not incoming:
		frappe.throw(_("Scan a roll first."))
	for raw in incoming:
		if not isinstance(raw, dict):
			continue
		batch = _resolve_batch(raw.get("batch_no"))
		batch_id = batch["batch_id"]
		if batch_id in seen:
			continue
		seen.add(batch_id)
		if batch_id in issued_batches:
			frappe.throw(_("Roll {0} is already recorded for this shift.").format(batch_id))
		balance_raw = raw.get("balance_qty")
		spec_quality = _cstr(raw.get("quality"))
		spec_color = _cstr(raw.get("color"))
		spec_gsm = flt(raw.get("gsm") or 0)
		spec_width = flt(raw.get("width") or 0)
		if balance_raw in (None, ""):
			stock_rows = _stock_rows(batch)
			available = _available_qty(stock_rows) or flt(raw.get("qty") or 0, 3)
			open_keep.append(
				{
					"batch_no": batch_id,
					"quality": spec_quality,
					"color": spec_color,
					"gsm": spec_gsm,
					"width": spec_width,
					"qty": available,
				}
			)
			continue
		stock_rows = _stock_rows(batch)
		available = _available_qty(stock_rows)
		if available <= 0:
			frappe.throw(_("Roll {0} has no balance left.").format(batch_id))
		balance = flt(balance_raw, 3)
		if balance < 0:
			frappe.throw(_("Balance qty for {0} cannot be negative.").format(batch_id))
		if balance >= available:
			frappe.throw(
				_("Balance qty for {0} must be less than {1} kg. Enter 0 to use the full roll.").format(
					batch_id, available
				)
			)
		used = flt(available - balance, 3)
		if used <= 0:
			frappe.throw(_("Used qty for {0} must be greater than 0.").format(batch_id))
		item_code = _cstr(batch.get("item_code") or (stock_rows[0].get("item_code") if stock_rows else ""))
		spec = _roll_spec(batch_id, item_code)
		prepared.append(
			{
				"batch_no": batch_id,
				"quality": spec.get("quality") or spec_quality,
				"color": spec.get("color") or spec_color,
				"gsm": flt(spec.get("gsm") or spec_gsm or 0),
				"width": flt(spec.get("width_inch") or spec_width or 0),
				"used_qty": used,
			}
		)
		issue_lines.extend(_issue_lines(batch, used))
	if not open_keep and not prepared:
		frappe.throw(_("Scan a roll first."))
	stock_entry = ""
	if prepared:
		stock_entry = _create_material_issue(issue_lines)
		for row in prepared:
			doc.append("rolls", row)
		doc.material_issue = stock_entry
	doc.open_rolls = json.dumps(open_keep)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	payload = _doc_payload(doc)
	payload["issued_now"] = [row["used_qty"] for row in prepared]
	payload["stock_entry"] = stock_entry
	return payload
