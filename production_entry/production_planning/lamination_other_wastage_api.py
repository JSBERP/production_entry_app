# -*- coding: utf-8 -*-
"""Lamination Other Wastage — one document per date + shift + unit + shaft.

Used by Lamination Production Entry Other Waste dialog (not saved on SPR).
Default rows: WASTE - 012 … 016 and WASTE - 006.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate

DOCTYPE = "Lamination Other Wastage"
CHILD = "Other Wastages"

# Fixed lamination other-waste items (auto-filled on first open)
LAMINATION_DEFAULT_WASTE_ITEMS = (
	"WASTE - 012",
	"WASTE - 013",
	"WASTE - 014",
	"WASTE - 015",
	"WASTE - 016",
	"WASTE - 006",
)

# Slitting other waste is a single item.
SLITTING_DEFAULT_WASTE_ITEMS = ("WASTE - 012",)


def _cstr(val) -> str:
	return (val or "").strip() if val is not None else ""


def _normalize_shift(shift: str) -> str:
	s = _cstr(shift)
	if "night" in s.lower():
		return "Night Shift"
	if "day" in s.lower():
		return "Day Shift"
	return s or "Day Shift"


def _normalize_shaft(shaft) -> str:
	s = _cstr(shaft)
	return s or "1"


def _parse_rows(rows) -> list:
	if isinstance(rows, str):
		try:
			rows = json.loads(rows)
		except Exception:
			rows = []
	return rows if isinstance(rows, list) else []


def _item_details(item_code: str) -> dict:
	item_code = _cstr(item_code)
	if not item_code or not frappe.db.exists("Item", item_code):
		return {"item_code": item_code, "item_name": "", "uom": "Kg"}
	row = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True) or {}
	return {
		"item_code": item_code,
		"item_name": _cstr(row.get("item_name")),
		"uom": _cstr(row.get("stock_uom")) or "Kg",
	}


def _waste_profile(profile) -> str:
	return "slitting" if _cstr(profile).lower() == "slitting" else "lamination"


def _profile_item_codes(profile) -> tuple:
	if _waste_profile(profile) == "slitting":
		return SLITTING_DEFAULT_WASTE_ITEMS
	return LAMINATION_DEFAULT_WASTE_ITEMS


def _default_rows(profile=None) -> list:
	out = []
	for code in _profile_item_codes(profile):
		det = _item_details(code)
		if not det.get("item_code") or not frappe.db.exists("Item", code):
			# Still show placeholder so operator sees the expected codes
			out.append(
				{
					"item_code": code,
					"item_name": code,
					"quantity": 0,
					"uom": "Kg",
					"missing_item": 1,
				}
			)
			continue
		out.append(
			{
				"item_code": det["item_code"],
				"item_name": det["item_name"],
				"quantity": 0,
				"uom": det["uom"],
			}
		)
	return out


def _row_payload(row) -> dict:
	return {
		"name": _cstr(getattr(row, "name", None)),
		"item_code": _cstr(getattr(row, "item_code", None)),
		"item_name": _cstr(getattr(row, "item_name", None)),
		"quantity": flt(getattr(row, "quantity", None) or 0),
		"uom": _cstr(getattr(row, "uom", None)) or "Kg",
	}


def _doc_payload(doc) -> dict:
	return {
		"name": doc.name,
		"run_date": str(doc.run_date or ""),
		"shift": doc.shift or "",
		"custom_unit": doc.custom_unit or "",
		"shaft": _normalize_shaft(doc.shaft),
		"gsm_shift_session": doc.gsm_shift_session or "",
		"rows": [_row_payload(r) for r in (doc.items or [])],
	}


def _doctype_ready() -> bool:
	return bool(frappe.db.exists("DocType", DOCTYPE)) and bool(frappe.db.exists("DocType", CHILD))


def _find_doc(run_date=None, shift=None, custom_unit=None, shaft=None, gsm_shift_session=None, name=None):
	if not _doctype_ready():
		return None
	name = _cstr(name)
	if name and frappe.db.exists(DOCTYPE, name):
		return name
	unit = _cstr(custom_unit)
	shift_n = _normalize_shift(shift)
	shaft_n = _normalize_shaft(shaft)
	rd = getdate(run_date) if run_date else None
	session = _cstr(gsm_shift_session)
	if rd and shift_n and unit and shaft_n:
		found = frappe.db.get_value(
			DOCTYPE,
			{"run_date": rd, "shift": shift_n, "custom_unit": unit, "shaft": shaft_n},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	if session and unit and shaft_n:
		found = frappe.db.get_value(
			DOCTYPE,
			{"gsm_shift_session": session, "custom_unit": unit, "shaft": shaft_n},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	return None


def _get_or_create(run_date=None, shift=None, custom_unit=None, shaft=None, gsm_shift_session=None, name=None):
	if not _doctype_ready():
		frappe.throw(_("Lamination Other Wastage is not installed. Run bench migrate."))
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		shaft=shaft,
		gsm_shift_session=gsm_shift_session,
		name=name,
	)
	if found:
		doc = frappe.get_doc(DOCTYPE, found)
	else:
		unit = _cstr(custom_unit)
		shift_n = _normalize_shift(shift)
		shaft_n = _normalize_shaft(shaft)
		rd = getdate(run_date) if run_date else None
		if not (rd and shift_n and unit):
			frappe.throw(_("Date, Shift, and Unit are required to save other wastage."))
		doc = frappe.new_doc(DOCTYPE)
		doc.run_date = rd
		doc.shift = shift_n
		doc.custom_unit = unit
		doc.shaft = shaft_n
	session = _cstr(gsm_shift_session)
	if session:
		doc.gsm_shift_session = session
	shaft_n = _normalize_shaft(shaft or getattr(doc, "shaft", None))
	doc.shaft = shaft_n
	return doc


def _ensure_default_items(doc, profile=None):
	"""Ensure the profile waste items exist as rows (qty preserved if already present)."""
	existing = {
		_cstr(getattr(r, "item_code", None)): r for r in (doc.items or []) if _cstr(getattr(r, "item_code", None))
	}
	qty_map = {code: flt(getattr(row, "quantity", None) or 0) for code, row in existing.items()}
	doc.items = []
	for raw in _default_rows(profile):
		code = raw["item_code"]
		doc.append(
			"items",
			{
				"item_code": code,
				"item_name": raw.get("item_name") or code,
				"quantity": qty_map.get(code, 0),
				"uom": raw.get("uom") or "Kg",
			},
		)


@frappe.whitelist()
def get_lamination_other_wastage(
	run_date=None,
	shift=None,
	custom_unit=None,
	shaft=None,
	gsm_shift_session=None,
	doc_name=None,
	waste_profile=None,
):
	"""Load or preview Other Wastage. Lamination uses six items; slitting uses WASTE - 012 only."""
	profile = _waste_profile(waste_profile)
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		shaft=shaft,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	if found:
		doc = frappe.get_doc(DOCTYPE, found)
		# Keep defaults present even on older docs
		before = [( _cstr(getattr(r, "item_code", None)), flt(getattr(r, "quantity", None) or 0)) for r in (doc.items or [])]
		_ensure_default_items(doc, profile)
		after = [( _cstr(getattr(r, "item_code", None)), flt(getattr(r, "quantity", None) or 0)) for r in (doc.items or [])]
		if after != before:
			doc.save(ignore_permissions=True)
		return _doc_payload(doc)

	# Preview (not yet saved)
	return {
		"name": "",
		"run_date": str(run_date or ""),
		"shift": _normalize_shift(shift),
		"custom_unit": _cstr(custom_unit),
		"shaft": _normalize_shaft(shaft),
		"gsm_shift_session": _cstr(gsm_shift_session),
		"rows": _default_rows(profile),
		"waste_profile": profile,
	}


@frappe.whitelist()
def save_lamination_other_wastage(
	run_date=None,
	shift=None,
	custom_unit=None,
	shaft=None,
	gsm_shift_session=None,
	doc_name=None,
	rows=None,
	waste_profile=None,
):
	"""Save Other Wastage quantities — standalone DocType, not SPR."""
	profile = _waste_profile(waste_profile)
	rows = _parse_rows(rows)
	doc = _get_or_create(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		shaft=shaft,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	# Merge submitted qty onto default item set
	qty_by_code = {}
	for raw in rows:
		if not isinstance(raw, dict):
			continue
		code = _cstr(raw.get("item_code"))
		if not code:
			continue
		qty_by_code[code] = flt(raw.get("quantity") or 0)

	doc.items = []
	for raw in _default_rows(profile):
		code = raw["item_code"]
		if not frappe.db.exists("Item", code):
			frappe.throw(_("Item {0} not found. Create the Item before saving.").format(code))
		det = _item_details(code)
		doc.append(
			"items",
			{
				"item_code": code,
				"item_name": det.get("item_name") or code,
				"quantity": qty_by_code.get(code, 0),
				"uom": det.get("uom") or "Kg",
			},
		)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	return _doc_payload(doc)
