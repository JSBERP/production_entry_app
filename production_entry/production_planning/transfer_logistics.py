# -*- coding: utf-8 -*-
"""Inter-company transfer logistics (isolated from BOM/planning sync)."""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from production_entry.production_planning.scheduler_api import (
	PLANNING_MOVEMENT_TYPE_FIELD,
	_expand_spr_name_tokens,
	_psi_order_sheet_field,
	_row_order_sheet_pp,
	get_color_chart_data,
	is_stock_movement,
	is_transfer_movement,
	normalize_movement_type,
	sync_spr_planning_table_links,
)

TRANSFER_WAREHOUSE_BY_COMPANY = {
	"Jayashree Spun Bond - 1ZT": {
		"s_warehouse": "Finished Goods - JSB-1ZT",
		"t_warehouse": "Goods In Transit - JSB-1ZT",
	},
	"J Vasanth Exports": {
		"s_warehouse": "Finished Goods Warehouse  - JVE",
		"t_warehouse": "Goods In Transit Warehouse  - JVE",
	},
	"Thusma SMS Nonwovens Private Limited - 1Z0": {
		"s_warehouse": "Finished Goods Warehouse  - TSNPL",
		"t_warehouse": "Goods In Transit Warehouse  - TSNPL",
	},
}

# Draft STE naming_series by (from_company, to_company) on Transfer Approval approve.
TRANSFER_STE_SERIES_BY_COMPANY_PAIR = {
	("Jayashree Spun Bond - 1ZT", "J Vasanth Exports"): "JSBW/2627/JV/.###",
	(
		"Thusma SMS Nonwovens Private Limited - 1Z0",
		"Varshine Retails Private Limited",
	): "TSNW/2526/VR/.###",
	(
		"Jayashree Spun Bond - 1ZT",
		"Thusma SMS Nonwovens Private Limited - 1Z0",
	): "JSBW/2627/TNSPL/.###",
	(
		"Thusma SMS Nonwovens Private Limited - 1Z0",
		"Varshine Tex (Puducherry)",
	): "TSNW/2526/VT/.###",
	("Jayashree Spun Bond - 1ZT", "Varshine Tex (Puducherry)"): "JSBW/2627/VTP/.###",
}

BOARD_KIND_TO_SCOPE = {
	"production": "only_100",
	"lamination": "lamination_only",
	"printing_105": "printing_only",
	"printed_bopp_film": "printed_bopp_pb_only",
	"slitting": "slitting_only",
	"rewinding": "rewinding_only",
	"sheet_cutting": "sheet_cutting_only",
	"box_bag": "box_bag_only",
	"w_cut_d_cut": "w_cut_d_cut_only",
}

TRANSFER_APPROVER_ROLES = frozenset({"System Manager", "Manufacturing Manager", "Administrator"})

_EXTERNAL_TRANSFER_FIELD_CANDIDATES = (
	"external_transfer",
	"custom_external_transfer",
	"is_external_transfer",
	"ge_external_transfer",
)


def _cstr(v):
	return str(v or "").strip()


_PLANNING_TRANSFER_FIELD_MAX_LEN = 500


def _truncate_planning_transfer_field(value, max_len: int = _PLANNING_TRANSFER_FIELD_MAX_LEN) -> str:
	text = _cstr(value)
	if not text or len(text) <= max_len:
		return text
	if max_len <= 3:
		return text[:max_len]
	return text[: max_len - 3] + "..."


def _compact_transfer_destination_label(label: str, company: str = "") -> str:
	"""Store a shorter destination label on planning rows (company name preferred)."""
	company = _cstr(company)
	label = _cstr(label)
	if company:
		return company
	if label.lower().startswith("transfer to "):
		return label[12:].strip() or label
	return label


def _stock_entry_external_transfer_fieldname():
	"""Resolve Stock Entry checkbox field for External Transfer (site may use custom fieldname)."""
	for fn in _EXTERNAL_TRANSFER_FIELD_CANDIDATES:
		if frappe.db.has_column("Stock Entry", fn):
			return fn
	try:
		meta = frappe.get_meta("Stock Entry")
		for df in meta.fields:
			if df.fieldtype != "Check":
				continue
			lab = (df.label or "").strip().lower()
			if lab == "external transfer" or "external transfer" in lab:
				if frappe.db.has_column("Stock Entry", df.fieldname):
					return df.fieldname
	except Exception:
		pass
	return ""


def _set_stock_entry_external_transfer(se, value=1):
	fn = _stock_entry_external_transfer_fieldname()
	if not fn:
		return False
	se.set(fn, cint(value))
	return True


def _ensure_stock_entry_external_transfer(stock_entry_name, value=1):
	fn = _stock_entry_external_transfer_fieldname()
	if not fn or not stock_entry_name:
		return False
	if cint(frappe.db.get_value("Stock Entry", stock_entry_name, fn) or 0) == cint(value):
		return True
	frappe.db.set_value("Stock Entry", stock_entry_name, fn, cint(value), update_modified=False)
	return True


_ORDER_CODE_FIELD_CANDIDATES = (
	"order_code",
	"custom_order_code",
	"party_code",
	"custom_party_code",
)

_NATURE_OF_PROCESSING_FIELD_CANDIDATES = (
	"nature_of_processing",
	"custom_nature_of_processing",
)


def _stock_entry_order_code_fieldname():
	for fn in _ORDER_CODE_FIELD_CANDIDATES:
		if frappe.db.has_column("Stock Entry", fn):
			return fn
	try:
		meta = frappe.get_meta("Stock Entry")
		for df in meta.fields:
			lab = (df.label or "").strip().lower()
			if lab in ("order code", "order_code", "party code"):
				if frappe.db.has_column("Stock Entry", df.fieldname):
					return df.fieldname
	except Exception:
		pass
	return ""


def _order_codes_from_transfer_approval(ta):
	codes = []
	for ln in ta.lines or []:
		pc = _cstr(ln.party_code)
		if pc and pc not in codes:
			codes.append(pc)
	return codes


def _set_stock_entry_order_codes(se, order_codes):
	fn = _stock_entry_order_code_fieldname()
	if not fn or not order_codes:
		return False
	val = ", ".join(order_codes) if len(order_codes) > 1 else order_codes[0]
	se.set(fn, val)
	return True


def _ensure_stock_entry_order_codes(stock_entry_name, order_codes):
	fn = _stock_entry_order_code_fieldname()
	if not fn or not order_codes or not stock_entry_name:
		return False
	val = ", ".join(order_codes) if len(order_codes) > 1 else order_codes[0]
	frappe.db.set_value("Stock Entry", stock_entry_name, fn, val, update_modified=False)
	return True


def _stock_entry_nature_of_processing_fieldname():
	for fn in _NATURE_OF_PROCESSING_FIELD_CANDIDATES:
		if frappe.db.has_column("Stock Entry", fn):
			return fn
	try:
		meta = frappe.get_meta("Stock Entry")
		for df in meta.fields:
			if (df.label or "").strip().lower() in ("nature of processing", "nature_of_processing"):
				if frappe.db.has_column("Stock Entry", df.fieldname):
					return df.fieldname
	except Exception:
		pass
	return ""


_PARTY_FIELD_CANDIDATES = (
	"party",
	"custom_party",
	"custom_transfer_party",
	"custom_party_name",
)


def _resolve_stock_entry_party_fields(to_company):
	"""Find Party (+ party_type) fields on Stock Entry; prefer Link to Company."""
	tc = _cstr(to_company).strip()
	if not tc:
		return None, None, None
	party_fn = None
	party_type_fn = None
	fieldtype = ""
	try:
		meta = frappe.get_meta("Stock Entry")
		for df in meta.fields:
			lab = (df.label or "").strip().lower()
			fn = df.fieldname
			if lab != "party" and fn not in _PARTY_FIELD_CANDIDATES:
				continue
			opts = _cstr(df.options).strip()
			ft = df.fieldtype or ""
			if ft == "Link" and opts and opts != "Company" and "Company" not in opts.split("\n"):
				continue
			if ft in ("Link", "Dynamic Link", "Data", "Small Text"):
				party_fn = fn
				fieldtype = ft
				break
	except Exception:
		pass
	if not party_fn:
		for fn in _PARTY_FIELD_CANDIDATES:
			if frappe.db.has_column("Stock Entry", fn):
				party_fn = fn
				fieldtype = "Data"
				break
	if frappe.db.has_column("Stock Entry", "party_type"):
		party_type_fn = "party_type"
	return party_fn, party_type_fn, fieldtype


def _set_stock_entry_party(se, to_company):
	"""Party on STE = destination company (To Company from transfer approval)."""
	tc = _cstr(to_company).strip()
	if not tc:
		return False
	wrote = False
	if frappe.db.has_column("Stock Entry", "custom_transfer_to_company"):
		se.set("custom_transfer_to_company", tc)
		wrote = True
	party_fn, party_type_fn, ft = _resolve_stock_entry_party_fields(tc)
	if party_fn:
		if ft == "Data" or ft == "Small Text":
			se.set(party_fn, tc)
			wrote = True
		elif ft == "Link" and frappe.db.exists("Company", tc):
			se.set(party_fn, tc)
			if party_type_fn:
				se.set(party_type_fn, "Company")
			wrote = True
		elif ft == "Dynamic Link":
			se.set(party_fn, tc)
			if party_type_fn:
				se.set(party_type_fn, "Company")
			wrote = True
	return wrote


def _set_stock_entry_nature_of_processing(se, nature):
	nat = _cstr(nature).strip()
	if not nat:
		return False
	fn = _stock_entry_nature_of_processing_fieldname()
	if not fn:
		return False
	se.set(fn, nat)
	return True


def _ensure_stock_entry_party_and_nature(stock_entry_name, to_company, nature_of_processing):
	"""Re-apply party/nature after insert (insert may drop invalid Link values)."""
	if not stock_entry_name or not frappe.db.exists("Stock Entry", stock_entry_name):
		return
	tc = _cstr(to_company).strip()
	nat = _cstr(nature_of_processing).strip()
	try:
		se = frappe.get_doc("Stock Entry", stock_entry_name)
		if tc:
			_set_stock_entry_party(se, tc)
			if frappe.db.has_column("Stock Entry", "custom_transfer_to_company"):
				se.custom_transfer_to_company = tc
		if nat:
			_set_stock_entry_nature_of_processing(se, nat)
		se.flags.ignore_validate = True
		se.flags.ignore_links = True
		se.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "_ensure_stock_entry_party_and_nature")
		party_fn, party_type_fn, _ft = _resolve_stock_entry_party_fields(tc)
		if party_fn and tc:
			frappe.db.set_value("Stock Entry", stock_entry_name, party_fn, tc, update_modified=False)
		if party_type_fn and tc:
			frappe.db.set_value("Stock Entry", stock_entry_name, party_type_fn, "Company", update_modified=False)
		fn = _stock_entry_nature_of_processing_fieldname()
		if fn and nat:
			frappe.db.set_value("Stock Entry", stock_entry_name, fn, nat, update_modified=False)


def _transfer_date_in_scope(transfer_date, view_scope=None, date=None, week=None, month=None):
	"""Filter transfer history by daily / weekly / monthly scope (same as production table)."""
	try:
		td = getdate(transfer_date) if transfer_date else None
	except Exception:
		td = None
	if not td:
		return True
	vs = _cstr(view_scope).lower() or "all"
	if vs == "all":
		return True
	if vs == "weekly" and week:
		start, end = _week_range(week)
		return getdate(start) <= td <= getdate(end)
	if vs == "monthly" and month:
		start, end = _month_range(month)
		return getdate(start) <= td <= getdate(end)
	if vs == "daily" and date:
		return td == getdate(date)
	return True


def _transfer_approval_queue_fieldname():
	if frappe.db.has_column("Transfer Approval", "custom_logistics_queue_idx"):
		return "custom_logistics_queue_idx"
	return None


def _queue_idx_for_stock_entry(ste_name, from_company=None, to_company=None):
	qf = _transfer_approval_queue_fieldname()
	if not qf or not ste_name:
		return 0
	filters = {"stock_entry": ste_name, "status": "Approved"}
	if from_company:
		filters["from_company"] = _cstr(from_company)
	if to_company:
		filters["to_company"] = _cstr(to_company)
	return cint(frappe.db.get_value("Transfer Approval", filters, qf) or 0)


def _transfer_lane_stock_entries(
	from_company,
	to_company,
	include_submitted=1,
	view_scope=None,
	date=None,
	week=None,
	month=None,
	order_code=None,
):
	"""All approved transfers for a lane (draft + submitted) with dates and order codes."""
	fc = _cstr(from_company)
	tc = _cstr(to_company)
	if not fc or not tc:
		return []
	rows = frappe.get_all(
		"Transfer Approval",
		filters={
			"from_company": fc,
			"to_company": tc,
			"status": "Approved",
			"stock_entry": ["is", "set"],
		},
		fields=["name", "stock_entry", "modified", "creation", "to_destination_label", "approved_by"],
		order_by="modified desc",
		limit_page_length=50,
	)
	out = []
	oc_fn = _stock_entry_order_code_fieldname()
	for row in rows:
		ste = _cstr(row.get("stock_entry"))
		if not ste or not frappe.db.exists("Stock Entry", ste):
			continue
		ds = cint(frappe.db.get_value("Stock Entry", ste, "docstatus") or 0)
		if ds != 0 and not cint(include_submitted):
			continue
		ste_fields = ["posting_date", "posting_time"]
		if oc_fn:
			ste_fields.append(oc_fn)
		ste_row = frappe.db.get_value("Stock Entry", ste, ste_fields, as_dict=True) or {}
		try:
			ta = frappe.get_doc("Transfer Approval", row.name)
			order_codes = _order_codes_from_transfer_approval(ta)
			qty_total = sum(flt(ln.qty) for ln in ta.lines or [])
			line_count = len(ta.lines or [])
			submitted_status = _transfer_submitted_status_text(ta)
		except Exception:
			order_codes = []
			qty_total = 0
			line_count = 0
			submitted_status = _("Transferred")
		if oc_fn and ste_row.get(oc_fn):
			ste_oc = _cstr(ste_row.get(oc_fn))
			if ste_oc and ste_oc not in order_codes:
				order_codes = [ste_oc] + [c for c in order_codes if c != ste_oc]
		transfer_date = _cstr(ste_row.get("posting_date") or row.get("modified") or row.get("creation"))
		if not _transfer_date_in_scope(transfer_date, view_scope, date, week, month):
			continue
		oc_filter = _cstr(order_code).lower()
		if oc_filter:
			hay = " ".join(order_codes).lower()
			if oc_filter not in hay:
				continue
		qty_display = round(flt(qty_total), 2) if qty_total else 0
		out.append(
			{
				"name": ste,
				"approval": row.get("name"),
				"modified": row.get("modified"),
				"transfer_date": transfer_date,
				"docstatus": ds,
				"status": "Draft" if ds == 0 else submitted_status,
				"order_codes": order_codes,
				"order_codes_label": ", ".join(order_codes) if order_codes else "",
				"qty_total": qty_display,
				"line_count": line_count,
				"label": ste,
				"queue_idx": _queue_idx_for_stock_entry(ste, fc, tc),
				"can_reorder": ds == 0,
			}
		)
	out.sort(
		key=lambda x: (
			0 if x.get("docstatus") == 0 else 1,
			cint(x.get("queue_idx") or 0) or 9999,
			_cstr(x.get("modified")),
		)
	)
	return out


@frappe.whitelist()
def reorder_transfer_lane_queue(from_company=None, to_company=None, ste_names=None):
	"""Persist draft STE priority on Transfer Approval (logistics kanban drag-and-drop)."""
	fc = _cstr(from_company)
	tc = _cstr(to_company)
	if not fc or not tc:
		frappe.throw(_("From company and to company are required."))
	qf = _transfer_approval_queue_fieldname()
	if not qf:
		return {"updated": 0, "noop": "custom_logistics_queue_idx missing — run bench migrate"}
	if isinstance(ste_names, str):
		try:
			ste_names = json.loads(ste_names)
		except Exception:
			ste_names = [s.strip() for s in ste_names.split(",") if s.strip()]
	names = [n for n in (ste_names or []) if _cstr(n)]
	updated = 0
	for idx, ste in enumerate(names, start=1):
		if cint(frappe.db.get_value("Stock Entry", ste, "docstatus") or 0) != 0:
			continue
		ta_name = frappe.db.get_value(
			"Transfer Approval",
			{"stock_entry": ste, "from_company": fc, "to_company": tc, "status": "Approved"},
			"name",
		)
		if not ta_name:
			continue
		frappe.db.set_value("Transfer Approval", ta_name, qf, idx, update_modified=False)
		updated += 1
	frappe.db.commit()
	return {"updated": updated, "from_company": fc, "to_company": tc}


def _draft_stock_entries_for_lane(from_company, to_company):
	"""Backward-compatible: draft-only entries."""
	return [e for e in _transfer_lane_stock_entries(from_company, to_company) if e.get("docstatus") == 0]


def chart_row_transfer_fields(item):
	"""Read transfer columns from a Planning Table row dict."""
	mt = _cstr(item.get(PLANNING_MOVEMENT_TYPE_FIELD) or item.get("movement_type"))
	dest = _cstr(item.get("custom_transfer_destination"))
	status = _cstr(item.get("custom_transfer_status"))
	return mt, dest, status


def _produced_roll_count_for_chart_row(item):
	"""Rolls recorded on submitted SPR (Roll Production Results or summary field)."""
	pr = cint(item.get("produced_rolls") or 0) if isinstance(item, dict) else 0
	if pr > 0:
		return pr
	spr = _cstr(item.get("spr_name") if isinstance(item, dict) else "")
	if not spr or not frappe.db.exists("Shaft Production Run", spr):
		return 0
	if cint(frappe.db.get_value("Shaft Production Run", spr, "docstatus") or 0) != 1:
		return 0
	try:
		if frappe.db.table_exists("Roll Production Result"):
			cnt = frappe.db.count(
				"Roll Production Result",
				{"parent": spr, "parenttype": "Shaft Production Run"},
			)
			if cnt:
				return cint(cnt)
	except Exception:
		pass
	for fn in ("custom_no_of_rolls_created", "no_of_rolls", "roll_count_per_shaft"):
		if frappe.db.has_column("Shaft Production Run", fn):
			val = cint(frappe.db.get_value("Shaft Production Run", spr, fn) or 0)
			if val:
				return val
	return 0


def _transferred_roll_count_for_planning_row(ptr):
	"""Active transfer approval lines linked to this planning row (excludes rejected / orphaned STE)."""
	row_id = _cstr(ptr)
	if not row_id:
		return 0
	rows = frappe.db.sql(
		"""
		select tl.name as line_name, ta.status, ta.stock_entry
		from `tabTransfer Approval Line` tl
		inner join `tabTransfer Approval` ta on ta.name = tl.parent
		where tl.planning_table_row = %s and ifnull(ta.status, '') != 'Rejected'
		""",
		row_id,
		as_dict=True,
	)
	cnt = 0
	for r in rows:
		st = _cstr(r.status)
		if st in ("Pending Approval", "Draft"):
			cnt += 1
			continue
		if st != "Approved":
			continue
		ste_ds = _transfer_approval_ste_docstatus(r.stock_entry)
		if ste_ds in (0, 1):
			cnt += 1
	return cnt


def _transfer_status_blocks_request(status, planning_table_row=None, produced_rolls=0, transferred_rolls=0):
	st = _cstr(status).lower()
	if not st:
		return False
	if st == "rejected":
		return False
	if st in {"pending approval", "approved", "draft ste created"}:
		return True
	if st.startswith("transferred") or "partially transferred" in st:
		pr = cint(produced_rolls)
		tr = cint(transferred_rolls)
		if pr > 0 and tr < pr:
			return False
		return True
	return False


def enrich_chart_row_transfer_payload(item, wo_terminal=False, spr_docstatus=0):
	"""Build API payload fields for order tables (no change to planning sync)."""
	mt, dest, status = chart_row_transfer_fields(item)
	mt_norm = normalize_movement_type(mt)
	if not mt_norm:
		try:
			from production_entry.production_planning.scheduler_api import resolve_movement_type_for_chart_row

			mt_norm = normalize_movement_type(resolve_movement_type_for_chart_row(item))
		except Exception:
			mt_norm = ""
	ptr = _cstr(item.get("itemName") or item.get("name") or item.get("planning_table_row"))
	produced_rolls = _produced_roll_count_for_chart_row(item)
	transferred_rolls = _transferred_roll_count_for_planning_row(ptr)
	st_low = _cstr(status).lower()
	# Stale lock/destination after TA/STE delete (0 active lines) — clear DB.
	if ptr and transferred_rolls == 0 and (
		dest
		or st_low in {"pending approval", "approved", "draft ste created"}
	):
		_clear_planning_row_transfer_fields(ptr)
		dest = ""
		status = ""
		st_low = ""
		if isinstance(item, dict):
			item["custom_transfer_destination"] = ""
			item["custom_transfer_status"] = ""
	active_transfer = (
		transferred_rolls > 0
		or _transfer_status_blocks_request(status, ptr, produced_rolls, transferred_rolls)
		or st_low.startswith("transferred")
		or "partially transferred" in st_low
	)
	can_transfer = (
		is_transfer_movement(mt)
		and bool(wo_terminal)
		and cint(spr_docstatus) == 1
		and not _transfer_status_blocks_request(
			status, ptr, produced_rolls, transferred_rolls
		)
	)
	block_reason = ""
	if is_stock_movement(mt):
		block_reason = _("Stock row — covered by existing inventory")
	elif not is_transfer_movement(mt):
		block_reason = "Not a transfer row"
	elif not wo_terminal:
		block_reason = "Work order not completed"
	elif cint(spr_docstatus) != 1:
		block_reason = "SPR not done"
	elif _transfer_status_blocks_request(status, ptr, produced_rolls, transferred_rolls):
		if produced_rolls > 0 and transferred_rolls >= produced_rolls:
			block_reason = _("All {0} produced rolls already in transfer").format(produced_rolls)
		else:
			block_reason = status
	movement_display = mt_norm or ""
	if dest and is_transfer_movement(mt) and active_transfer:
		if produced_rolls > 0:
			movement_display = f"{mt_norm} → {dest} ({transferred_rolls}/{produced_rolls} rolls)"
		else:
			movement_display = f"{mt_norm} → {dest}"
	elif mt_norm == "Despatch":
		movement_display = "Despatch"
	return {
		"movement_type": mt_norm,
		"transfer_destination": dest,
		"transfer_status": status,
		"movement_display": movement_display,
		"can_transfer": can_transfer,
		"transfer_block_reason": block_reason,
		"produced_rolls": produced_rolls,
		"transferred_rolls": transferred_rolls,
	}


def _parse_chart_rows(raw):
	if raw is None:
		return []
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except Exception:
			return []
	return list(raw) if isinstance(raw, list) else []


def _chart_fetch_kwargs(
	view_scope,
	date=None,
	week=None,
	month=None,
	board_kind=None,
	board_slug=None,
):
	from production_entry.production_planning.board_access import (
		_normalize_board_slug,
		board_slug_for_board_kind,
	)

	scope = BOARD_KIND_TO_SCOPE.get(_cstr(board_kind)) or "exclude_special"
	kwargs = {"planned_only": 1, "board_process_scope": scope, "plan_name": "__all__"}
	vs = _cstr(view_scope).lower() or "daily"
	if vs == "weekly" and week:
		kwargs["start_date"], kwargs["end_date"] = _week_range(week)
	elif vs == "monthly" and month:
		kwargs["start_date"], kwargs["end_date"] = _month_range(month)
	elif date:
		kwargs["date"] = date
	else:
		kwargs["date"] = getdate()
	kwargs["board_slug"] = (
		_normalize_board_slug(board_slug)
		or board_slug_for_board_kind(board_kind)
		or "production-board"
	)
	return kwargs


def _week_range(week_val):
	"""ISO week input YYYY-Www → start/end dates."""
	try:
		parts = _cstr(week_val).split("-W")
		if len(parts) == 2:
			y, w = int(parts[0]), int(parts[1])
			from datetime import datetime, timedelta

			d = datetime.strptime(f"{y}-W{w}-1", "%G-W%V-%u").date()
			return str(d), str(d + timedelta(days=6))
	except Exception:
		pass
	return getdate(), getdate()


def _month_range(month_val):
	try:
		from frappe.utils import get_first_day, get_last_day

		d = getdate(f"{month_val}-01")
		return str(get_first_day(d)), str(get_last_day(d))
	except Exception:
		return getdate(), getdate()


def _transfer_row_unit_is_unassigned(unit) -> bool:
	"""Exclude generic unassigned pool rows from Transfer popup (Unit 1–4 / slitting unassigned, etc.)."""
	from production_entry.production_planning.planning_doctypes import normalize_planning_unit_for_select

	u = _cstr(normalize_planning_unit_for_select(unit)).upper().replace(" ", "")
	if not u:
		return True
	if u in ("UNASSIGNED", "MIXED"):
		return True
	return "UNASSIGNED" in u


def _row_matches_filters(row, party_code=None, customer=None, unit=None):
	if unit and _cstr(row.get("unit")) != _cstr(unit):
		return False
	pc = _cstr(party_code).lower()
	if pc:
		code = _cstr(row.get("partyCode") or row.get("party_code")).lower()
		if pc not in code:
			return False
	cu = _cstr(customer).lower()
	if cu:
		cn = _cstr(row.get("customer_name") or row.get("customer")).lower()
		if cu not in cn:
			return False
	return True


@frappe.whitelist()
def get_logistics_companies():
	rows = frappe.get_all(
		"Company",
		filters={},
		fields=["name"],
		order_by="name asc",
		limit_page_length=0,
	)
	return [{"name": r.name, "label": r.name} for r in rows]


@frappe.whitelist()
def get_transfer_destination_cards(
	from_company=None,
	view_scope=None,
	date=None,
	week=None,
	month=None,
	order_code=None,
):
	fc = _cstr(from_company)
	companies = get_logistics_companies()
	lane_kw = {
		"view_scope": view_scope,
		"date": date,
		"week": week,
		"month": month,
		"order_code": order_code,
	}
	out = []
	for c in companies:
		if fc and c["name"] == fc:
			continue
		tc = c["name"]
		history = _transfer_lane_stock_entries(fc, tc, **lane_kw) if fc else []
		out.append(
			{
				"company": tc,
				"label": _("Transfer to {0}").format(tc),
				"draft_stock_entries": [e for e in history if e.get("docstatus") == 0],
				"transfer_history": history,
			}
		)
	return out


@frappe.whitelist()
def get_transfer_eligible_rows(
	board_kind=None,
	view_scope=None,
	date=None,
	week=None,
	month=None,
	unit=None,
	party_code=None,
	customer=None,
	board_slug=None,
):
	"""Rows with movement Transport; reuses color-chart WO/SPR flags without altering sync."""
	kwargs = _chart_fetch_kwargs(
		view_scope, date, week, month, board_kind, board_slug=board_slug
	)
	try:
		raw = get_color_chart_data(**kwargs)
	except frappe.PermissionError:
		raise
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_transfer_eligible_rows")
		raw = []
	rows = _parse_chart_rows(raw)
	out = []
	for r in rows:
		mt = normalize_movement_type(r.get("movement_type"))
		if is_stock_movement(mt):
			continue
		if not is_transfer_movement(mt):
			continue
		if not _row_matches_filters(r, party_code, customer, unit):
			continue
		if _transfer_row_unit_is_unassigned(r.get("unit")):
			continue
		pt_name = r.get("itemName") or r.get("name")
		if pt_name:
			r["transfer_status"] = _resolved_planning_row_transfer_status(pt_name, r.get("transfer_status"))
			r["custom_transfer_status"] = r["transfer_status"]
			# Re-read destination after reconcile so in-memory chart data is not stale.
			if frappe.db.has_column("Planning Table", "custom_transfer_destination"):
				r["custom_transfer_destination"] = _cstr(
					frappe.db.get_value("Planning Table", pt_name, "custom_transfer_destination") or ""
				)
				r["transfer_destination"] = r["custom_transfer_destination"]
			else:
				r["custom_transfer_destination"] = ""
				r["transfer_destination"] = ""
		spr_ds = cint(r.get("spr_docstatus") or 0)
		wo_terminal = bool(r.get("wo_terminal") or (spr_ds == 1 and _cstr(r.get("spr_name"))))
		extra = enrich_chart_row_transfer_payload(
			{"custom_movement_type": mt, **r},
			wo_terminal=wo_terminal,
			spr_docstatus=spr_ds,
		)
		out.append(
			{
				"planning_table_row": pt_name,
				"planning_sheet": r.get("planningSheet"),
				"party_code": r.get("partyCode"),
				"customer_name": r.get("customer_name") or r.get("customer"),
				"item_code": r.get("itemCode"),
				"item_name": r.get("description"),
				"unit": r.get("unit"),
				"qty": flt(r.get("qty")),
				"spr_name": r.get("spr_name"),
				"spr_docstatus": spr_ds,
				"wo_terminal": wo_terminal,
				"pp_id": r.get("pp_id"),
				"transfer_destination": r.get("transfer_destination"),
				"transfer_status": r.get("transfer_status"),
				**extra,
			}
		)
	return out


def _resolve_submitted_spr_ids(spr_name):
	"""Planning rows often store comma/semicolon multi-SPR CSV — expand to submitted SPR ids."""
	tokens = _expand_spr_name_tokens(spr_name)
	if not tokens:
		return []
	valid = []
	for sn in tokens:
		if not frappe.db.exists("Shaft Production Run", sn):
			continue
		if cint(frappe.db.get_value("Shaft Production Run", sn, "docstatus") or 0) != 1:
			continue
		valid.append(sn)
	if valid:
		return valid
	# Single draft SPR → keep previous throw behaviour for callers that expect it
	if len(tokens) == 1 and frappe.db.exists("Shaft Production Run", tokens[0]):
		frappe.throw(_("SPR {0} must be submitted before transfer.").format(tokens[0]))
	return []


def _primary_submitted_spr_for_batch(spr_name, batch_no=""):
	"""Pick one submitted SPR id from CSV; prefer the SPR that owns the batch."""
	spr_ids = _resolve_submitted_spr_ids(spr_name)
	if not spr_ids:
		return ""
	bn = _cstr(batch_no)
	if bn:
		for sn in spr_ids:
			if frappe.db.exists("Shaft Production Run Item", {"parent": sn, "batch_no": bn}):
				return sn
	return spr_ids[0]


@frappe.whitelist()
def get_spr_produced_batches(
	spr_name=None,
	item_code=None,
	party_code=None,
	from_company=None,
	exclude_transferred=1,
):
	"""Batches produced on one or more submitted SPRs (CSV spr_name supported).

	exclude_transferred: when truthy, hide batches already on a non-rejected Transfer Approval.
	Despatch should pass 0 — FG can still be despatched even if a prior transfer request existed.
	"""
	spr_ids = _resolve_submitted_spr_ids(spr_name)
	if not spr_ids:
		return []

	ic_filter = _cstr(item_code)
	pc_filter = _cstr(party_code)
	skip_transferred = cint(exclude_transferred) == 1
	batches = []
	seen = set()
	transferred_batches = set()
	if skip_transferred:
		existing_transferred = frappe.db.sql(
			"""
			select tl.batch_no
			from `tabTransfer Approval Line` tl
			inner join `tabTransfer Approval` ta on ta.name = tl.parent
			where ta.status != 'Rejected' and ifnull(tl.batch_no, '') != ''
			""",
			as_dict=1,
		)
		transferred_batches = {_cstr(r.batch_no) for r in existing_transferred if _cstr(r.batch_no)}

	def _add_batch(batch_no, qty, row=None, warehouse=None):
		bn = _cstr(batch_no)
		if not bn or bn in seen or bn in transferred_batches:
			return

		stock_qty = flt(
			frappe.db.sql(
				"select sum(actual_qty) from `tabStock Ledger Entry` where batch_no=%s and is_cancelled=0",
				bn,
			)[0][0]
			or 0
		)
		# ERPNext v15: batch may live only on Serial and Batch Bundle entries
		if stock_qty <= 0:
			stock_qty = _batch_qty_from_serial_batch_bundle(bn)

		q = flt(qty or 0)
		if q <= 0:
			q = stock_qty
		if q <= 0:
			# Still surface the roll — logistics can adjust qty; Batch master may show qty
			q = flt(frappe.db.get_value("Batch", bn, "batch_qty") or 0) or 1.0

		seen.add(bn)
		batches.append(
			{
				"batch_no": bn,
				"item_code": (row or {}).get("item_code") or ic_filter,
				"item_name": (row or {}).get("item_name")
				or (frappe.db.get_value("Item", ic_filter, "item_name") if ic_filter else ""),
				"qty": q,
				"available_qty": q,
				"party_code": (row or {}).get("party_code") or pc_filter,
				"work_order": (row or {}).get("work_order"),
				"warehouse": warehouse,
				**_roll_spec_dict(row, (row or {}).get("item_code") or ic_filter),
			}
		)

	item_fields = _spr_item_query_fields("party_code", "work_order")
	spr_item_codes = set()
	for sn in spr_ids:
		for row in frappe.get_all(
			"Shaft Production Run Item",
			filters={"parent": sn},
			fields=item_fields,
			order_by="idx asc",
			limit_page_length=0,
			ignore_permissions=True,
		):
			ic = _cstr(row.get("item_code"))
			if ic:
				spr_item_codes.add(ic)
			bn = _cstr(row.get("batch_no"))
			if not bn or bn in seen:
				continue

			_add_batch(bn, row.get("net_weight") or row.get("gross_weight") or 0, row)

			# Parent prefix → split children (JS-01052611 → JS-01052611/1)
			if "/" not in bn:
				split_bns = frappe.db.sql(
					"""
					select distinct batch_no
					from `tabStock Ledger Entry`
					where batch_no like %s and is_cancelled=0
					""",
					(bn + "/%",),
					as_dict=1,
				)
				for sb in split_bns:
					_add_batch(sb.batch_no, 0, row)
				# Batch master split ids (SLE may omit batch_no on v15 bundles)
				try:
					for brow in frappe.db.sql(
						"""
						select name, ifnull(batch_id, name) as batch_id, item
						from `tabBatch`
						where (name like %s or ifnull(batch_id, '') like %s)
						  and ifnull(disabled, 0) = 0
						limit 200
						""",
						(bn + "/%", bn + "/%"),
						as_dict=1,
					) or []:
						_add_batch(
							brow.batch_id or brow.name,
							0,
							{
								"item_code": brow.item or (row or {}).get("item_code"),
								"item_name": (row or {}).get("item_name"),
								"party_code": (row or {}).get("party_code"),
								"work_order": (row or {}).get("work_order"),
							},
						)
				except Exception:
					pass

	if batches:
		return batches

	# Fallback: manufacture Stock Entries / SLE linked to any of the SPRs
	# Prefer SPR line item codes over planning-row item_code (often differs).
	se_meta = frappe.get_meta("Stock Entry")
	se_link_field = ""
	if se_meta.has_field("shaft_production_run"):
		se_link_field = "shaft_production_run"
	elif frappe.db.has_column("Stock Entry", "custom_spr_reference"):
		se_link_field = "custom_spr_reference"
	else:
		se_link_field = ""

	se_names = []
	if se_link_field:
		se_names = frappe.get_all(
			"Stock Entry",
			filters={"docstatus": 1, se_link_field: ["in", spr_ids]},
			pluck="name",
			limit_page_length=200,
			ignore_permissions=True,
		) or []

	# Also Manufacture SEs via Work Orders on the SPR (link field often blank)
	if not se_names:
		wo_set = set()
		for sn in spr_ids:
			for wo in frappe.get_all(
				"Shaft Production Run Item",
				filters={"parent": sn},
				pluck="work_order",
				limit_page_length=0,
				ignore_permissions=True,
			) or []:
				if _cstr(wo):
					wo_set.add(_cstr(wo))
		if wo_set and frappe.db.has_column("Stock Entry", "work_order"):
			se_names = frappe.get_all(
				"Stock Entry",
				filters={
					"docstatus": 1,
					"work_order": ["in", list(wo_set)],
					"purpose": "Manufacture",
				},
				pluck="name",
				limit_page_length=200,
				ignore_permissions=True,
			) or []

	def _pull_sle(item_codes=None):
		if not se_names:
			return
		sle_filters = {
			"voucher_type": "Stock Entry",
			"voucher_no": ["in", se_names],
			"actual_qty": [">", 0],
		}
		codes = [c for c in (item_codes or []) if c]
		if codes:
			sle_filters["item_code"] = ["in", codes]
		for sle in frappe.get_all(
			"Stock Ledger Entry",
			filters=sle_filters,
			fields=["batch_no", "item_code", "actual_qty", "warehouse"],
			order_by="posting_date asc, posting_time asc, creation asc",
			limit_page_length=1000,
			ignore_permissions=True,
		):
			if not _cstr(sle.get("batch_no")):
				continue
			_add_batch(
				sle.get("batch_no"),
				sle.get("actual_qty"),
				{
					"item_code": sle.get("item_code"),
					"item_name": frappe.db.get_value("Item", sle.get("item_code"), "item_name"),
				},
				sle.get("warehouse"),
			)

	# 1) SPR item codes  2) planning item  3) no item filter
	if spr_item_codes:
		_pull_sle(list(spr_item_codes))
	if not batches and ic_filter:
		_pull_sle([ic_filter])
	if not batches:
		_pull_sle(None)

	# Despatch-only last resort: active Batch masters for SPR item codes
	if not batches and spr_item_codes and not skip_transferred:
		for ic in spr_item_codes:
			for brow in frappe.get_all(
				"Batch",
				filters={"item": ic, "disabled": 0},
				fields=["name", "batch_id", "item", "batch_qty"],
				order_by="creation desc",
				limit_page_length=200,
				ignore_permissions=True,
			) or []:
				bn = _cstr(brow.batch_id or brow.name)
				if not bn:
					continue
				stock_qty = flt(
					frappe.db.sql(
						"select sum(actual_qty) from `tabStock Ledger Entry` where batch_no=%s and is_cancelled=0",
						bn,
					)[0][0]
					or 0
				)
				if stock_qty <= 0:
					stock_qty = _batch_qty_from_serial_batch_bundle(bn)
				qty = stock_qty or flt(brow.batch_qty) or 0
				if qty <= 0:
					continue
				_add_batch(
					bn,
					qty,
					{
						"item_code": brow.item or ic,
						"item_name": frappe.db.get_value("Item", ic, "item_name") or "",
					},
				)

	return batches


def _batch_qty_from_serial_batch_bundle(batch_no: str) -> float:
	"""Sum qty from Serial and Batch Entry when SLE.batch_no is blank (ERPNext v15)."""
	bn = _cstr(batch_no)
	if not bn or not frappe.db.exists("DocType", "Serial and Batch Entry"):
		return 0.0
	try:
		meta = frappe.get_meta("Serial and Batch Entry")
		batch_field = next((fn for fn in ("batch_no", "batch", "batch_id") if meta.has_field(fn)), "")
		if not batch_field:
			return 0.0
		qty_field = "qty" if meta.has_field("qty") else ("stock_qty" if meta.has_field("stock_qty") else "")
		if not qty_field:
			return 0.0
		return flt(
			frappe.db.sql(
				f"""
				select sum(ifnull(`{qty_field}`, 0))
				from `tabSerial and Batch Entry`
				where ifnull(`{batch_field}`, '') = %s
				""",
				bn,
			)[0][0]
			or 0
		)
	except Exception:
		return 0.0


def _user_can_approve_transfer():
	roles = set(frappe.get_roles(frappe.session.user) or [])
	return bool(roles & TRANSFER_APPROVER_ROLES)


def _spr_name_in_planning_row(spr_name: str, row_spr_field: str) -> bool:
	sn = _cstr(spr_name).strip()
	if not sn:
		return False
	raw = _cstr(row_spr_field).strip()
	if not raw:
		return False
	for segment in raw.replace(";", ",").split(","):
		if segment.strip() == sn:
			return True
	return False


def _resolve_planning_table_row_for_spr(spr_name: str, item_code: str = "", party_code: str = "") -> dict:
	"""SPR-only: map roll line to Planning Table row for transfer approval lines."""
	sn = _cstr(spr_name).strip()
	ic = _cstr(item_code).strip()
	pc = _cstr(party_code).strip()
	if not sn or not frappe.db.table_exists("Planning Table"):
		return {}
	if not frappe.db.has_column("Planning Table", "spr_name"):
		return {}

	fields = ["name", "parent", "item_code", "spr_name"]
	if frappe.db.has_column("Planning Table", PLANNING_MOVEMENT_TYPE_FIELD):
		fields.append(PLANNING_MOVEMENT_TYPE_FIELD)
	if frappe.db.has_column("Planning Table", "party_code"):
		fields.append("party_code")
	if frappe.db.has_column("Planning Table", "custom_party_code"):
		fields.append("custom_party_code")

	rows = frappe.get_all(
		"Planning Table",
		filters={"spr_name": ["like", f"%{sn}%"]},
		fields=list(dict.fromkeys(fields)),
		limit_page_length=500,
	)
	candidates = []
	for row in rows or []:
		if not _spr_name_in_planning_row(sn, row.get("spr_name")):
			continue
		row_ic = _cstr(row.get("item_code")).strip()
		if ic and row_ic and row_ic != ic:
			continue
		mt = normalize_movement_type(row.get(PLANNING_MOVEMENT_TYPE_FIELD) or "")
		score = 2 if is_transfer_movement(mt) else (1 if not mt else 0)
		if pc:
			row_pc = _cstr(row.get("party_code") or row.get("custom_party_code") or "")
			if row_pc and row_pc != pc:
				continue
		candidates.append((score, row))

	candidates.sort(key=lambda x: (-x[0], _cstr(x[1].get("name"))))
	if not candidates:
		return {}
	best = candidates[0][1]
	return {
		"planning_table_row": best.get("name"),
		"planning_sheet": best.get("parent"),
	}


def _spr_item_query_fields(*optional_fields) -> list[str]:
	"""Only columns that exist on Shaft Production Run Item (site may lack meter_per_roll)."""
	meta = frappe.get_meta("Shaft Production Run Item")
	cols = set(frappe.db.get_table_columns("Shaft Production Run Item") or [])
	fields = {"batch_no", "item_code", "item_name", "net_weight", "gross_weight"}
	for fieldname in optional_fields + (
		"quality",
		"color",
		"gsm",
		"width_inch",
		"meter_per_roll",
		"meter_roll",
		"party_code",
	):
		if fieldname in cols or meta.has_field(fieldname):
			# Prefer DB columns when meta/DB diverge (avoids OperationalError 1054)
			if cols and fieldname not in cols:
				continue
			fields.add(fieldname)
	return sorted(fields)


def _roll_spec_dict(row=None, item_code: str = "") -> dict:
	"""Quality / colour / GSM / width for logistics batch UIs."""
	row = row or {}
	quality = _cstr(row.get("quality") or "")
	color = _cstr(row.get("color") or row.get("colour") or "")
	gsm = cint(row.get("gsm") or 0)
	width = flt(row.get("width_inch") or row.get("width") or 0)
	meters = flt(row.get("meter_per_roll") or row.get("meter_roll") or 0)
	ic = _cstr(row.get("item_code") or item_code)
	if ic and (not quality or not color or gsm <= 0 or width <= 0):
		try:
			from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
				_spr_resolve_roll_line_specs_from_item_code,
			)

			specs = _spr_resolve_roll_line_specs_from_item_code(ic, _cstr(row.get("item_name") or "")) or {}
			quality = quality or _cstr(specs.get("quality") or "")
			color = color or _cstr(specs.get("color") or "")
			gsm = gsm or cint(specs.get("gsm") or 0)
			width = width or flt(specs.get("width_inch") or specs.get("width") or 0)
		except Exception:
			pass
	return {
		"quality": quality,
		"color": color,
		"gsm": gsm,
		"width_inch": width,
		"meter_per_roll": meters,
	}


def _spr_auto_approve_transfer_approval(ta_name: str) -> dict:
	"""SPR-only auto-approve path (same steps as approve_transfer_approval, no role gate)."""
	if not ta_name or not frappe.db.exists("Transfer Approval", ta_name):
		frappe.throw(_("Transfer Approval not found."))
	ta = frappe.get_doc("Transfer Approval", ta_name)
	if ta.status == "Approved":
		return {"ok": True, "name": ta_name, "stock_entry": ta.stock_entry}
	if ta.status == "Rejected":
		frappe.throw(_("This transfer was rejected."))
	ste = _create_draft_transfer_stock_entry(ta)
	ta.stock_entry = ste
	ta.status = "Approved"
	ta.approved_by = frappe.session.user
	ta.save(ignore_permissions=True)
	_finalize_planning_rows_after_approval(ta)
	frappe.db.commit()
	return {"ok": True, "name": ta_name, "stock_entry": ste}


def _bulk_planning_ptr_map_for_spr(spr_name: str) -> dict:
	"""Map (item_code, party_code) -> planning row for all rolls on an SPR (one query)."""
	sn = _cstr(spr_name).strip()
	out: dict = {}
	if not sn or not frappe.db.table_exists("Planning Table"):
		return out
	if not frappe.db.has_column("Planning Table", "spr_name"):
		return out

	fields = ["name", "parent", "item_code", "spr_name"]
	if frappe.db.has_column("Planning Table", PLANNING_MOVEMENT_TYPE_FIELD):
		fields.append(PLANNING_MOVEMENT_TYPE_FIELD)
	if frappe.db.has_column("Planning Table", "party_code"):
		fields.append("party_code")
	if frappe.db.has_column("Planning Table", "custom_party_code"):
		fields.append("custom_party_code")

	rows = frappe.get_all(
		"Planning Table",
		filters={"spr_name": ["like", f"%{sn}%"]},
		fields=list(dict.fromkeys(fields)),
		limit_page_length=500,
	)
	candidates: dict = {}
	for row in rows or []:
		if not _spr_name_in_planning_row(sn, row.get("spr_name")):
			continue
		ic = _cstr(row.get("item_code")).strip()
		pc = _cstr(row.get("party_code") or row.get("custom_party_code") or "").strip()
		mt = normalize_movement_type(row.get(PLANNING_MOVEMENT_TYPE_FIELD) or "")
		score = 2 if is_transfer_movement(mt) else (1 if not mt else 0)
		for key in ((ic, pc), (ic, ""), ("", pc), ("", "")):
			prev = candidates.get(key)
			if not prev or score > prev[0]:
				candidates[key] = (
					score,
					{
						"planning_table_row": row.get("name"),
						"planning_sheet": row.get("parent"),
					},
				)
	for key, (_, val) in candidates.items():
		out[key] = val

	# Fallback: map by PP + item_code when spr_name CSV exists on other rows but item match failed.
	spr_pp = _cstr(frappe.db.get_value("Shaft Production Run", sn, "production_plan") or "").strip()
	if spr_pp:
		os_field = _psi_order_sheet_field()
		item_codes = {
			_cstr(r.get("item_code")).strip()
			for r in frappe.get_all(
				"Shaft Production Run Item",
				filters={"parent": sn},
				fields=["item_code"],
				limit_page_length=0,
			)
			if _cstr(r.get("item_code")).strip()
		}
		if item_codes and pp_filters:
			pp_clause_parts = []
			pp_vals = []
			if os_field:
				pp_clause_parts.append(f"IFNULL(`{os_field}`, '') = %s")
				pp_vals.append(spr_pp)
			for col in ("order_sheet", "custom_order_sheet"):
				if col != os_field and frappe.db.has_column("Planning Table", col):
					pp_clause_parts.append(f"IFNULL(`{col}`, '') = %s")
					pp_vals.append(spr_pp)
			if pp_clause_parts:
				fmt_items = ", ".join(["%s"] * len(item_codes))
				fallback_rows = frappe.db.sql(
					f"""
					SELECT name, parent, item_code, party_code, custom_party_code, spr_name
					FROM `tabPlanning Table`
					WHERE item_code IN ({fmt_items})
					  AND ({' OR '.join(pp_clause_parts)})
					""",
					tuple(list(item_codes) + pp_vals),
					as_dict=True,
				) or []
				for row in fallback_rows:
					ic = _cstr(row.get("item_code")).strip()
					pc = _cstr(row.get("party_code") or row.get("custom_party_code") or "").strip()
					val = {
						"planning_table_row": row.get("name"),
						"planning_sheet": row.get("parent"),
					}
					for key in ((ic, pc), (ic, ""), ("", pc)):
						if key not in out:
							out[key] = val
	return out


def _bulk_batch_sle_qty_map(batch_nos: list) -> dict:
	"""batch_no -> positive SLE qty sum."""
	bns = [_cstr(b).strip() for b in (batch_nos or []) if _cstr(b).strip()]
	if not bns:
		return {}
	placeholders = ", ".join(["%s"] * len(bns))
	rows = frappe.db.sql(
		f"""
		SELECT batch_no, IFNULL(SUM(actual_qty), 0) AS qty
		FROM `tabStock Ledger Entry`
		WHERE IFNULL(is_cancelled, 0) = 0
		  AND batch_no IN ({placeholders})
		GROUP BY batch_no
		""",
		tuple(bns),
		as_dict=True,
	) or []
	return {_cstr(r.batch_no): flt(r.qty) for r in rows if _cstr(r.batch_no)}


def _transferred_batch_set(batch_nos: list) -> set:
	bns = [_cstr(b).strip() for b in (batch_nos or []) if _cstr(b).strip()]
	if not bns:
		return set()
	placeholders = ", ".join(["%s"] * len(bns))
	rows = frappe.db.sql(
		f"""
		SELECT DISTINCT tl.batch_no
		FROM `tabTransfer Approval Line` tl
		INNER JOIN `tabTransfer Approval` ta ON ta.name = tl.parent
		WHERE ta.status != 'Rejected'
		  AND tl.batch_no IN ({placeholders})
		""",
		tuple(bns),
		as_dict=True,
	) or []
	return {_cstr(r.batch_no) for r in rows if _cstr(r.batch_no)}


def _get_spr_produced_batches_fast(spr_name: str, from_company: str = "") -> list:
	"""Transfer dialog batches — bulk SLE + filtered transferred set. Supports multi-SPR CSV."""
	spr_ids = _resolve_submitted_spr_ids(spr_name)
	if not spr_ids:
		return []
	ic_filter = ""
	pc_filter = ""
	rows = []
	for sn in spr_ids:
		rows.extend(
			frappe.get_all(
				"Shaft Production Run Item",
				filters={"parent": sn},
				fields=_spr_item_query_fields("party_code", "work_order"),
				order_by="idx asc",
				limit_page_length=0,
			)
			or []
		)
	batch_nos = [_cstr(r.get("batch_no")).strip() for r in rows if _cstr(r.get("batch_no")).strip()]
	if not batch_nos:
		return []
	transferred = _transferred_batch_set(batch_nos)
	sle_qty = _bulk_batch_sle_qty_map(batch_nos)
	batches = []
	seen = set()
	for row in rows:
		bn = _cstr(row.get("batch_no")).strip()
		if not bn or bn in seen or bn in transferred:
			continue
		seen.add(bn)
		qty = flt(row.get("net_weight") or row.get("gross_weight") or 0)
		if qty <= 0:
			qty = flt(sle_qty.get(bn) or 0)
		if qty <= 0:
			continue
		batches.append(
			{
				"batch_no": bn,
				"item_code": row.get("item_code") or ic_filter,
				"item_name": row.get("item_name") or "",
				"qty": qty,
				"available_qty": qty,
				"party_code": row.get("party_code") or pc_filter,
				"work_order": row.get("work_order"),
				**_roll_spec_dict(row, row.get("item_code") or ic_filter),
			}
		)
	return batches


@frappe.whitelist()
def get_spr_transfer_bootstrap(spr_name=None):
	"""Single fast API for SPR transfer dialog — companies, rolls, batches."""
	sn = _cstr(spr_name).strip()
	if not sn or not frappe.db.exists("Shaft Production Run", sn):
		frappe.throw(_("Shaft Production Run not found."))
	if cint(frappe.db.get_value("Shaft Production Run", sn, "docstatus") or 0) != 1:
		frappe.throw(_("SPR {0} must be submitted before transfer.").format(sn))

	from_company = _cstr(frappe.db.get_value("Shaft Production Run", sn, "company"))
	customer = _cstr(frappe.db.get_value("Shaft Production Run", sn, "customer") or "")
	unit = _cstr(frappe.db.get_value("Shaft Production Run", sn, "custom_unit") or "")
	ptr_map = _bulk_planning_ptr_map_for_spr(sn)
	if not ptr_map:
		try:
			sync_spr_planning_table_links(sn)
			ptr_map = _bulk_planning_ptr_map_for_spr(sn)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR transfer bootstrap link repair:{sn}")

	rolls = []
	for row in frappe.get_all(
		"Shaft Production Run Item",
		filters={"parent": sn},
		fields=_spr_item_query_fields("party_code", "order_code", "custom_order_code"),
		order_by="idx asc",
		limit_page_length=0,
	):
		bn = _cstr(row.get("batch_no")).strip()
		if not bn:
			continue
		ic = _cstr(row.get("item_code")).strip()
		pc = _cstr(
			row.get("party_code")
			or row.get("order_code")
			or row.get("custom_order_code")
			or ""
		).strip()
		pt = ptr_map.get((ic, pc)) or ptr_map.get((ic, "")) or ptr_map.get(("", pc)) or {}
		rolls.append(
			{
				"batch_no": bn,
				"item_code": ic,
				"item_name": _cstr(row.get("item_name")),
				"party_code": pc,
				"qty": flt(row.get("net_weight") or row.get("gross_weight") or 0),
				"planning_table_row": pt.get("planning_table_row") or "",
				"planning_sheet": pt.get("planning_sheet") or "",
				**_roll_spec_dict(row, ic),
			}
		)

	companies = get_logistics_companies()
	to_options = [c for c in companies if c.get("name") != from_company]
	batches = _get_spr_produced_batches_fast(sn, from_company)
	roll_map = {r["batch_no"]: r for r in rolls}
	batch_options = []
	for b in batches:
		meta = roll_map.get(b["batch_no"]) or {}
		avail = flt(b.get("qty") or meta.get("qty") or 1)
		batch_options.append(
			{
				"batch_no": b["batch_no"],
				"item_code": b.get("item_code") or meta.get("item_code"),
				"party_code": meta.get("party_code") or b.get("party_code") or "",
				"planning_table_row": meta.get("planning_table_row") or "",
				"planning_sheet": meta.get("planning_sheet") or "",
				"available_qty": avail,
				"qty": avail,
				"quality": b.get("quality") or meta.get("quality") or "",
				"color": b.get("color") or meta.get("color") or "",
				"gsm": b.get("gsm") or meta.get("gsm") or "",
				"width_inch": b.get("width_inch") or meta.get("width_inch") or "",
			}
		)

	return {
		"spr_name": sn,
		"from_company": from_company,
		"customer": customer,
		"unit": unit,
		"to_company_options": to_options,
		"rolls": rolls,
		"batches": batch_options,
	}


@frappe.whitelist()
def get_spr_transfer_context(spr_name=None):
	"""Bootstrap data for SPR Transfer dialog."""
	sn = _cstr(spr_name).strip()
	if not sn or not frappe.db.exists("Shaft Production Run", sn):
		frappe.throw(_("Shaft Production Run not found."))
	if cint(frappe.db.get_value("Shaft Production Run", sn, "docstatus") or 0) != 1:
		frappe.throw(_("SPR {0} must be submitted before transfer.").format(sn))

	spr = frappe.get_doc("Shaft Production Run", sn)
	from_company = _cstr(spr.company)
	customer = _cstr(getattr(spr, "customer", None) or "")
	unit = _cstr(getattr(spr, "custom_unit", None))
	ptr_map = _bulk_planning_ptr_map_for_spr(sn)

	rolls = []
	for row in spr.get("items") or []:
		bn = _cstr(getattr(row, "batch_no", None) or "")
		if not bn:
			continue
		ic = _cstr(getattr(row, "item_code", None) or "")
		pc = _cstr(
			getattr(row, "party_code", None)
			or getattr(row, "order_code", None)
			or getattr(row, "custom_order_code", None)
			or ""
		)
		pt = ptr_map.get((ic, pc)) or ptr_map.get((ic, "")) or ptr_map.get(("", pc)) or {}
		rolls.append(
			{
				"batch_no": bn,
				"item_code": ic,
				"item_name": _cstr(row.get("item_name")),
				"party_code": pc,
				"qty": flt(row.get("net_weight") or row.get("gross_weight") or 0),
				"planning_table_row": pt.get("planning_table_row") or "",
				"planning_sheet": pt.get("planning_sheet") or "",
			}
		)

	companies = get_logistics_companies()
	to_options = [c for c in companies if c.get("name") != from_company]

	return {
		"spr_name": sn,
		"from_company": from_company,
		"customer": customer,
		"unit": unit,
		"production_plan": _cstr(spr.production_plan),
		"rolls": rolls,
		"to_company_options": to_options,
	}


@frappe.whitelist()
def create_and_approve_transfer_from_spr(
	spr_name=None,
	from_company=None,
	to_company=None,
	to_destination_label=None,
	lines=None,
	nature_of_processing=None,
):
	"""SPR-only: create Transfer Approval then auto-approve to draft Stock Entry."""
	sn = _cstr(spr_name).strip()
	if not sn:
		frappe.throw(_("SPR is required."))
	if cint(frappe.db.get_value("Shaft Production Run", sn, "docstatus") or 0) != 1:
		frappe.throw(_("SPR {0} must be submitted before transfer.").format(sn))

	parsed = json.loads(lines) if isinstance(lines, str) else (lines or [])
	if not parsed:
		frappe.throw(_("Select at least one batch."))

	spr_doc = frappe.get_doc("Shaft Production Run", sn)
	customer = _cstr(getattr(spr_doc, "customer", None) or "")
	unit = _cstr(getattr(spr_doc, "custom_unit", None))
	built_lines = []

	for line in parsed:
		ic = _cstr(line.get("item_code"))
		pc = _cstr(line.get("party_code") or "")
		bn = _cstr(line.get("batch_no"))
		if not bn:
			frappe.throw(_("Batch is required for each line."))
		ptr = _cstr(line.get("planning_table_row"))
		pt_info = {}
		if not ptr:
			pt_info = _resolve_planning_table_row_for_spr(sn, ic, pc)
			ptr = _cstr(pt_info.get("planning_table_row"))
		if not ptr or not frappe.db.exists("Planning Table", ptr):
			frappe.throw(
				_("No Planning Table row linked to SPR {0} for item {1}. Link SPR on planning sheet first.").format(
					sn, ic or bn
				)
			)
		qty = flt(line.get("qty") or 0)
		if qty <= 0:
			qty = 1.0
		built_lines.append(
			{
				"planning_table_row": ptr,
				"planning_sheet": line.get("planning_sheet") or pt_info.get("planning_sheet"),
				"party_code": pc,
				"customer_name": line.get("customer_name") or customer,
				"item_code": ic,
				"unit": line.get("unit") or unit,
				"spr_name": sn,
				"batch_no": bn,
				"qty": qty,
				"uom": line.get("uom") or "Kg",
			}
		)

	fc = _cstr(from_company)
	tc = _cstr(to_company)
	label = _cstr(to_destination_label) or (_("Transfer to {0}").format(tc) if tc else "")

	created = create_transfer_approval_request(
		from_company=fc,
		to_company=tc,
		to_destination_label=label,
		lines=built_lines,
		nature_of_processing=nature_of_processing,
	)
	ta_name = _cstr(created.get("name"))
	approved = _spr_auto_approve_transfer_approval(ta_name)
	return {
		"ok": True,
		"transfer_approval": ta_name,
		"stock_entry": approved.get("stock_entry"),
		"status": "Approved",
	}


@frappe.whitelist()
def create_transfer_approval_request(
	from_company=None,
	to_company=None,
	to_destination_label=None,
	lines=None,
	nature_of_processing=None,
):
	fc = _cstr(from_company)
	tc = _cstr(to_company)
	label = _cstr(to_destination_label) or (_("Transfer to {0}").format(tc) if tc else "")
	if not fc or not tc:
		frappe.throw(_("From company and to company are required."))
	if fc == tc:
		frappe.throw(_("From and to company must be different."))
	if not frappe.db.exists("Company", fc) or not frappe.db.exists("Company", tc):
		frappe.throw(_("Invalid company."))
	wh = TRANSFER_WAREHOUSE_BY_COMPANY.get(fc)
	if not wh:
		frappe.throw(
			_("Transfer warehouses are not configured for company {0}. Contact administrator.").format(fc)
		)
	parsed = json.loads(lines) if isinstance(lines, str) else (lines or [])
	if not parsed:
		frappe.throw(_("Select at least one line with batch and qty."))
	nature = _cstr(nature_of_processing).strip()
	if not nature:
		frappe.throw(_("Nature of Processing is required before sending for approval."))

	doc = frappe.new_doc("Transfer Approval")
	doc.from_company = fc
	doc.to_company = tc
	doc.to_destination_label = label
	if frappe.db.has_column("Transfer Approval", "nature_of_processing"):
		doc.set("nature_of_processing", nature)
	elif hasattr(doc, "nature_of_processing"):
		doc.nature_of_processing = nature
	doc.status = "Pending Approval"
	doc.requested_by = frappe.session.user

	for line in parsed:
		ptr = _cstr(line.get("planning_table_row"))
		if not ptr or not frappe.db.exists("Planning Table", ptr):
			frappe.throw(_("Invalid planning row."))
		current_status = _cstr(
			frappe.db.get_value("Planning Table", ptr, "custom_transfer_status")
			if frappe.db.has_column("Planning Table", "custom_transfer_status")
			else ""
		)
		current_status = _resolved_planning_row_transfer_status(ptr, current_status)
		bn = _cstr(line.get("batch_no"))
		if not bn:
			frappe.throw(_("Batch is required for each line."))
		spr = _primary_submitted_spr_for_batch(line.get("spr_name"), bn)
		if not spr:
			frappe.throw(_("SPR not done for row {0}. Cannot request transfer.").format(ptr))
		qty = flt(line.get("qty") or 0)
		if qty <= 0:
			qty = 1.0
		doc.append(
			"lines",
			{
				"planning_table_row": ptr,
				"planning_sheet": line.get("planning_sheet"),
				"party_code": line.get("party_code"),
				"customer_name": line.get("customer_name"),
				"item_code": line.get("item_code"),
				"unit": line.get("unit"),
				"spr_name": spr,
				"batch_no": bn,
				"qty": qty,
				"uom": line.get("uom") or "Kg",
				"transfer_allowed": 1,
				"block_reason": "",
			},
		)

	doc.insert(ignore_permissions=True)
	_stamp_planning_rows_for_transfer_request(doc.name, label)
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": doc.status}


def _clear_planning_row_transfer_fields(ptr):
	"""Wipe transfer status/destination/approval link on a Planning Table row."""
	if not ptr or not frappe.db.exists("Planning Table", ptr):
		return
	updates = {}
	if frappe.db.has_column("Planning Table", "custom_transfer_destination"):
		updates["custom_transfer_destination"] = ""
	if frappe.db.has_column("Planning Table", "custom_transfer_status"):
		updates["custom_transfer_status"] = ""
	if frappe.db.has_column("Planning Table", "custom_transfer_approval"):
		updates["custom_transfer_approval"] = ""
	if updates:
		frappe.db.set_value("Planning Table", ptr, updates, update_modified=False)
		_sync_psi_transfer_fields(ptr, updates)


def _transfer_approval_ste_docstatus(stock_entry):
	"""Return STE docstatus, or None if name empty / STE missing."""
	ste = _cstr(stock_entry)
	if not ste or not frappe.db.exists("Stock Entry", ste):
		return None
	return cint(frappe.db.get_value("Stock Entry", ste, "docstatus") or 0)


def update_planning_row_transfer_status(ptr):
	"""Recompute Planning Table transfer status from live Transfer Approvals + STEs."""
	if not ptr or not frappe.db.exists("Planning Table", ptr):
		return

	rows = frappe.db.sql(
		"""
		select
			ta.name as ta_name,
			ta.to_destination_label,
			ta.to_company,
			ta.status,
			ta.stock_entry,
			count(tl.name) as roll_count
		from `tabTransfer Approval Line` tl
		inner join `tabTransfer Approval` ta on ta.name = tl.parent
		where tl.planning_table_row = %s and ifnull(ta.status, '') != 'Rejected'
		group by ta.name
		""",
		ptr,
		as_dict=1,
	)

	if not rows:
		_clear_planning_row_transfer_fields(ptr)
		return

	dests = []
	has_pending = False
	has_draft_ste = False
	has_approved_no_ste = False
	submitted_rolls = 0
	active_rolls = 0
	latest_approval = ""

	for r in rows:
		lbl = _compact_transfer_destination_label(
			r.to_destination_label or "",
			r.to_company or "",
		)
		rc = cint(r.roll_count)
		st = _cstr(r.status)
		ste_ds = _transfer_approval_ste_docstatus(r.stock_entry)
		# Approved/Pending with deleted STE no longer blocks the row.
		if ste_ds is None and _cstr(r.stock_entry):
			frappe.db.set_value(
				"Transfer Approval",
				r.ta_name,
				"stock_entry",
				"",
				update_modified=False,
			)
			r.stock_entry = ""
			ste_ds = None

		if st == "Pending Approval" or st == "Draft":
			has_pending = True
			active_rolls += rc
		elif st == "Approved":
			if ste_ds == 1:
				submitted_rolls += rc
				active_rolls += rc
			elif ste_ds == 0:
				has_draft_ste = True
				active_rolls += rc
			else:
				# Approved but STE gone — treat as inactive so row can be re-requested.
				has_approved_no_ste = True
				continue
		else:
			active_rolls += rc

		if lbl and rc:
			dests.append(f"{lbl} ({rc} roll{'s' if rc != 1 else ''})")
		elif lbl:
			dests.append(lbl)
		if r.ta_name:
			latest_approval = r.ta_name

	if not has_pending and not has_draft_ste and submitted_rolls <= 0 and has_approved_no_ste and active_rolls <= 0:
		_clear_planning_row_transfer_fields(ptr)
		return

	produced_rolls = 0
	try:
		pt_row = frappe.db.get_value(
			"Planning Table",
			ptr,
			["spr_name", "item_code"],
			as_dict=True,
		)
		if pt_row:
			produced_rolls = _produced_roll_count_for_chart_row(
				{"spr_name": pt_row.get("spr_name"), "item_code": pt_row.get("item_code")}
			)
	except Exception:
		pass

	if has_pending:
		final_status = "Pending Approval"
	elif has_draft_ste:
		final_status = "Draft STE Created"
	elif submitted_rolls > 0 and produced_rolls > 0 and submitted_rolls < produced_rolls:
		final_status = _("Partially transferred ({0}/{1} rolls)").format(
			submitted_rolls, produced_rolls
		)
	elif submitted_rolls > 0:
		final_status = "Transferred"
	elif has_approved_no_ste:
		_clear_planning_row_transfer_fields(ptr)
		return
	else:
		final_status = ""

	updates = {}
	if frappe.db.has_column("Planning Table", "custom_transfer_destination"):
		updates["custom_transfer_destination"] = _truncate_planning_transfer_field(" | ".join(dests))
	if frappe.db.has_column("Planning Table", "custom_transfer_status"):
		updates["custom_transfer_status"] = _truncate_planning_transfer_field(final_status)
	if latest_approval and frappe.db.has_column("Planning Table", "custom_transfer_approval"):
		updates["custom_transfer_approval"] = latest_approval if final_status else ""

	if updates:
		frappe.db.set_value("Planning Table", ptr, updates, update_modified=False)
		_sync_psi_transfer_fields(ptr, updates)


def _stamp_planning_rows_for_transfer_request(approval_name, label):
	ta = frappe.get_doc("Transfer Approval", approval_name)
	seen_ptrs: set[str] = set()
	for ln in ta.lines or []:
		ptr = _cstr(ln.planning_table_row)
		if ptr and ptr not in seen_ptrs:
			seen_ptrs.add(ptr)
			update_planning_row_transfer_status(ptr)


def _sync_psi_transfer_fields(pt_name, updates):
	if not updates or not frappe.db.table_exists("Planning sheet Item"):
		return
	pt = frappe.db.get_value(
		"Planning Table",
		pt_name,
		["item_code", "sales_order_item", "so_item", "parent"],
		as_dict=True,
	)
	if not pt:
		return
	soik = _cstr(pt.get("sales_order_item") or pt.get("so_item"))
	filters = {"parent": pt.parent, "item_code": pt.item_code}
	if soik and frappe.db.has_column("Planning sheet Item", "sales_order_item"):
		filters["sales_order_item"] = soik
	psi = frappe.db.get_value("Planning sheet Item", filters, "name")
	if psi:
		frappe.db.set_value("Planning sheet Item", psi, updates, update_modified=False)


def _transfer_submitted_status_text(ta):
	if isinstance(ta, dict):
		dest = _cstr(ta.get("to_company")) or _cstr(ta.get("to_destination_label"))
	else:
		dest = _cstr(getattr(ta, "to_company", None)) or _cstr(getattr(ta, "to_destination_label", None))
	return _("Transferred to {0}").format(dest) if dest else _("Transferred")


def _stamp_planning_rows_after_transfer_submit(ta):
	seen_ptrs: set[str] = set()
	for ln in ta.lines or []:
		ptr = _cstr(ln.planning_table_row)
		if ptr and ptr not in seen_ptrs:
			seen_ptrs.add(ptr)
			update_planning_row_transfer_status(ptr)


def _resolved_planning_row_transfer_status(pt_name, fallback_status=""):
	"""Return live transfer status; heal stale Draft STE / Pending when docs were deleted."""
	ptr = _cstr(pt_name)
	if not ptr:
		return _cstr(fallback_status)

	row = None
	fields = ["custom_transfer_status"]
	if frappe.db.has_column("Planning Table", "custom_transfer_approval"):
		fields.append("custom_transfer_approval")
	if frappe.db.has_column("Planning Table", "custom_transfer_status"):
		row = frappe.db.get_value("Planning Table", ptr, fields, as_dict=True)
	if not row:
		return _cstr(fallback_status)

	status = _cstr(row.get("custom_transfer_status") or fallback_status)
	st = status.lower()

	# Stale lock statuses after Transfer Approval / STE delete — recompute from live docs.
	if st in {"pending approval", "approved", "draft ste created"}:
		update_planning_row_transfer_status(ptr)
		refreshed = ""
		if frappe.db.has_column("Planning Table", "custom_transfer_status"):
			refreshed = _cstr(
				frappe.db.get_value("Planning Table", ptr, "custom_transfer_status") or ""
			)
		return refreshed

	approval = _cstr(row.get("custom_transfer_approval")) if "custom_transfer_approval" in (row or {}) else ""
	if not approval:
		return status
	ta_row = frappe.db.get_value(
		"Transfer Approval",
		approval,
		["stock_entry", "to_company", "to_destination_label"],
		as_dict=True,
	)
	if not ta_row or not ta_row.get("stock_entry"):
		return status
	if cint(frappe.db.get_value("Stock Entry", ta_row.get("stock_entry"), "docstatus") or 0) != 1:
		return status
	transfer_status = _transfer_submitted_status_text(ta_row)
	if transfer_status != status and frappe.db.has_column("Planning Table", "custom_transfer_status"):
		updates = {"custom_transfer_status": transfer_status}
		frappe.db.set_value("Planning Table", ptr, updates, update_modified=False)
		_sync_psi_transfer_fields(ptr, updates)
	return transfer_status


def transfer_approval_on_trash(doc, method=None):
	"""When Transfer Approval is deleted, unlock planning rows so transfer can be re-requested."""
	ptrs = []
	seen = set()
	for ln in doc.get("lines") or []:
		ptr = _cstr(getattr(ln, "planning_table_row", None) or (ln.get("planning_table_row") if isinstance(ln, dict) else ""))
		if ptr and ptr not in seen:
			seen.add(ptr)
			ptrs.append(ptr)
	for ptr in ptrs:
		try:
			update_planning_row_transfer_status(ptr)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "transfer_approval_on_trash")


def stock_entry_on_trash(doc, method=None):
	"""When transfer STE is deleted, clear TA link and unlock / recompute planning status."""
	_release_planning_rows_for_deleted_transfer_ste(doc)


def stock_entry_on_cancel(doc, method=None):
	"""When transfer STE is cancelled, same unlock path as delete."""
	_release_planning_rows_for_deleted_transfer_ste(doc)


def _release_planning_rows_for_deleted_transfer_ste(doc):
	ste_name = _cstr(getattr(doc, "name", None))
	if not ste_name:
		return
	ta_names = frappe.get_all(
		"Transfer Approval",
		filters={"stock_entry": ste_name},
		pluck="name",
	)
	if not ta_names:
		return
	ptrs = set()
	for ta_name in ta_names:
		frappe.db.set_value(
			"Transfer Approval",
			ta_name,
			{"stock_entry": "", "status": "Pending Approval"},
			update_modified=False,
		)
		for ln in frappe.get_all(
			"Transfer Approval Line",
			filters={"parent": ta_name},
			pluck="planning_table_row",
		):
			if ln:
				ptrs.add(_cstr(ln))
	for ptr in ptrs:
		try:
			update_planning_row_transfer_status(ptr)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "release_planning_rows_transfer_ste")


def stock_entry_on_submit(doc, method=None):
	"""When transfer STE is submitted, mark source planning rows as transferred to destination."""
	ste_name = _cstr(getattr(doc, "name", None))
	if not ste_name or not frappe.db.exists("Transfer Approval", {"stock_entry": ste_name}):
		return
	ta_name = frappe.db.get_value("Transfer Approval", {"stock_entry": ste_name}, "name")
	if not ta_name:
		return
	ta = frappe.get_doc("Transfer Approval", ta_name)
	_stamp_planning_rows_after_transfer_submit(ta)


@frappe.whitelist()
def get_transfer_approvals(status_filter=None, limit=200, from_date=None, to_date=None, order_code=None):
	"""List transfer approvals for dashboard with status/date/order filters."""
	sf = _cstr(status_filter).lower() or "pending"
	filters = {}
	if sf == "pending":
		filters["status"] = ["in", ["Pending Approval", "Draft"]]
	elif sf == "approved":
		filters["status"] = "Approved"
	elif sf == "rejected":
		filters["status"] = "Rejected"
	elif sf == "draft":
		filters["status"] = "Draft"
	# sf == "all" → no status filter
	fd = _cstr(from_date).strip()
	td = _cstr(to_date).strip()
	if fd and len(fd) == 10:
		fd = fd + " 00:00:00"
	if td and len(td) == 10:
		td = td + " 23:59:59"
	if fd and td:
		filters["creation"] = ["between", [fd, td]]
	elif fd:
		filters["creation"] = [">=", fd]
	elif td:
		filters["creation"] = ["<=", td]

	rows = frappe.get_all(
		"Transfer Approval",
		filters=filters,
		fields=[
			"name",
			"from_company",
			"to_company",
			"to_destination_label",
			"status",
			"owner",
			"modified",
			"stock_entry",
			"requested_by",
			"nature_of_processing",
			"creation",
		],
		order_by="modified desc",
		limit_page_length=cint(limit) or 200,
	)
	if not rows:
		return rows
	names = [r.name for r in rows]
	line_rows = frappe.get_all(
		"Transfer Approval Line",
		filters={"parent": ["in", names]},
		fields=["parent", "party_code"],
		limit_page_length=0,
	) or []
	codes_by_parent = {}
	for ln in line_rows:
		pc = _cstr(ln.get("party_code")).strip()
		if not pc:
			continue
		codes_by_parent.setdefault(ln.get("parent"), [])
		if pc not in codes_by_parent[ln.get("parent")]:
			codes_by_parent[ln.get("parent")].append(pc)
	oc = _cstr(order_code).strip().lower()
	out = []
	for row in rows:
		codes = codes_by_parent.get(row.name, [])
		row["order_codes"] = codes
		row["order_codes_label"] = ", ".join(codes)
		row["transfer_date"] = str(row.get("creation") or "")[:10]
		if oc and not any(oc in _cstr(c).lower() for c in codes):
			continue
		out.append(row)
	return out


@frappe.whitelist()
def get_pending_transfer_approvals(limit=200):
	"""Backward-compatible alias."""
	return get_transfer_approvals(status_filter="pending", limit=limit)


@frappe.whitelist()
def get_transfer_approval_detail(name=None):
	if not name or not frappe.db.exists("Transfer Approval", name):
		frappe.throw(_("Transfer Approval not found."))
	doc = frappe.get_doc("Transfer Approval", name)
	return doc.as_dict()


@frappe.whitelist()
def approve_transfer_approval(name=None):
	if not _user_can_approve_transfer():
		frappe.throw(_("You do not have permission to approve transfers."), frappe.PermissionError)
	if not name or not frappe.db.exists("Transfer Approval", name):
		frappe.throw(_("Transfer Approval not found."))
	ta = frappe.get_doc("Transfer Approval", name)
	if ta.status == "Approved":
		return {"ok": True, "name": name, "stock_entry": ta.stock_entry}
	if ta.status == "Rejected":
		frappe.throw(_("This transfer was rejected."))
	ste = _create_draft_transfer_stock_entry(ta)
	ta.stock_entry = ste
	ta.status = "Approved"
	ta.approved_by = frappe.session.user
	ta.save(ignore_permissions=True)
	_finalize_planning_rows_after_approval(ta)
	frappe.db.commit()
	return {"ok": True, "name": name, "stock_entry": ste}


@frappe.whitelist()
def reorder_transfer_approval_lines(name=None, line_names=None):
	"""Reorder transfer lines before approval (same UX as sequence approval)."""
	if not name or not frappe.db.exists("Transfer Approval", name):
		frappe.throw(_("Transfer Approval not found."))
	ta = frappe.get_doc("Transfer Approval", name)
	if ta.status not in ("Pending Approval", "Draft"):
		frappe.throw(_("Only pending transfers can be reordered."))
	if isinstance(line_names, str):
		try:
			line_names = json.loads(line_names)
		except Exception:
			line_names = [x.strip() for x in line_names.split(",") if x.strip()]
	names = [n for n in (line_names or []) if _cstr(n)]
	if not names:
		return {"ok": True}
	by_name = {ln.name: ln for ln in (ta.lines or [])}
	new_lines = []
	for nm in names:
		if nm in by_name:
			new_lines.append(by_name[nm])
	for ln in ta.lines or []:
		if ln.name not in names:
			new_lines.append(ln)
	for i, ln in enumerate(new_lines, start=1):
		ln.idx = i
	ta.lines = new_lines
	ta.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "name": name}


@frappe.whitelist()
def reject_transfer_approval(name=None):
	if not _user_can_approve_transfer():
		frappe.throw(_("You do not have permission to reject transfers."), frappe.PermissionError)
	ta = frappe.get_doc("Transfer Approval", name)
	ta.status = "Rejected"
	ta.approved_by = frappe.session.user
	ta.save(ignore_permissions=True)
	for ln in ta.lines or []:
		if ln.planning_table_row:
			update_planning_row_transfer_status(ln.planning_table_row)
	frappe.db.commit()
	return {"ok": True, "name": name}


def _transfer_ste_naming_series(from_company, to_company):
	"""Stock Entry naming_series for logistics transfer pair; empty if unmapped."""
	fc = _cstr(from_company).strip()
	tc = _cstr(to_company).strip()
	if not fc or not tc:
		return ""
	return _cstr(TRANSFER_STE_SERIES_BY_COMPANY_PAIR.get((fc, tc)) or "").strip()


def _create_draft_transfer_stock_entry(ta):
	fc = _cstr(ta.from_company)
	wh = TRANSFER_WAREHOUSE_BY_COMPANY.get(fc)
	if not wh:
		frappe.throw(_("Warehouses not configured for {0}").format(fc))
	s_wh = wh["s_warehouse"]
	t_wh = wh["t_warehouse"]
	if not frappe.db.exists("Warehouse", s_wh):
		frappe.throw(_("Source warehouse missing: {0}").format(s_wh))
	if not frappe.db.exists("Warehouse", t_wh):
		frappe.throw(_("Target warehouse missing: {0}").format(t_wh))

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Transfer"
	se.company = fc
	series = _transfer_ste_naming_series(fc, ta.to_company)
	if series and frappe.db.has_column("Stock Entry", "naming_series"):
		se.naming_series = series
	se.set_posting_time = 1
	se.posting_date = getdate()
	se.posting_time = now_datetime().strftime("%H:%M:%S")
	_set_stock_entry_external_transfer(se, 1)
	if frappe.db.has_column("Stock Entry", "add_to_transit"):
		se.add_to_transit = 1
	unit_val = ""
	for ln in ta.lines or []:
		if ln.unit:
			unit_val = ln.unit
			break
	if unit_val and frappe.db.has_column("Stock Entry", "unit"):
		se.unit = unit_val
	order_codes = _order_codes_from_transfer_approval(ta)
	_set_stock_entry_order_codes(se, order_codes)
	_set_stock_entry_party(se, ta.to_company)
	nature = _cstr(getattr(ta, "nature_of_processing", None)).strip()
	_set_stock_entry_nature_of_processing(se, nature)

	for ln in ta.lines or []:
		qty = flt(ln.qty or 0)
		if qty <= 0:
			qty = 1.0
		ic = ln.item_code
		
		row = {
			"item_code": ic,
			"qty": qty,
			"s_warehouse": s_wh,
			"t_warehouse": t_wh,
			"uom": ln.uom or frappe.db.get_value("Item", ic, "stock_uom") or "Kg",
			"batch_no": ln.batch_no,
		}
		
		if frappe.db.has_column("Stock Entry Detail", "use_serial_batch_fields"):
			row["use_serial_batch_fields"] = 0
		if frappe.db.has_column("Stock Entry Detail", "scanned_qty"):
			row["scanned_qty"] = 0.0
		if frappe.db.has_column("Stock Entry Detail", "custom_scanned_qty"):
			row["custom_scanned_qty"] = 0.0
			
		se.append("items", row)

	se.insert(ignore_permissions=True)
	if series and frappe.db.has_column("Stock Entry", "naming_series"):
		cur = _cstr(frappe.db.get_value("Stock Entry", se.name, "naming_series") or "")
		if cur != series:
			frappe.db.set_value("Stock Entry", se.name, "naming_series", series, update_modified=False)
	_ensure_stock_entry_order_codes(se.name, order_codes)
	_ensure_stock_entry_party_and_nature(se.name, ta.to_company, nature)
	if not _ensure_stock_entry_external_transfer(se.name, 1):
		frappe.log_error(
			title="Transfer Stock Entry — External Transfer field missing",
			message=_("Could not set External Transfer on {0}. Add a Check field labeled 'External Transfer' on Stock Entry.").format(
				se.name
			),
		)
	return se.name


def _finalize_planning_rows_after_approval(ta):
	for ln in ta.lines or []:
		ptr = ln.planning_table_row
		if not ptr:
			continue
		updates = {}
		if frappe.db.has_column("Planning Table", "custom_transfer_status"):
			updates["custom_transfer_status"] = "Draft STE Created"
		if frappe.db.has_column("Planning Table", "custom_transfer_destination"):
			updates["custom_transfer_destination"] = _truncate_planning_transfer_field(
				_compact_transfer_destination_label(
					_cstr(ta.to_destination_label),
					_cstr(ta.to_company),
				)
			)
		if updates:
			frappe.db.set_value("Planning Table", ptr, updates, update_modified=False)
			_sync_psi_transfer_fields(ptr, updates)


def _normalize_transfer_scan(raw):
	"""Fix common scanner misreads on roll labels (e.g. #S → JS, missing hyphen)."""
	s = (raw or "").strip().upper()
	s = re.sub(r"[\s\r\n\t]+", "", s)
	s = re.sub(r"\*+$", "", s)
	if s.startswith("#S"):
		s = "JS" + s[2:]
	elif s.startswith("IS-"):
		s = "JS-" + s[3:]
	elif s.startswith("IS") and len(s) > 2:
		s = "JS" + s[2:]
	for prefix in ("JS", "JV", "TS", "TT", "VR", "VT", "JVE", "TTT", "VTP"):
		if re.match(rf"^{prefix}\d", s):
			s = f"{prefix}-" + s[len(prefix) :]
			break
	if re.match(r"^\d{6,}/\d", s):
		s = "JS-" + s
	return s


def _batch_match_key(value):
	return re.sub(r"[^A-Z0-9/]", "", _normalize_transfer_scan(value))


def _levenshtein(a, b):
	if a == b:
		return 0
	if len(a) < len(b):
		a, b = b, a
	prev = list(range(len(b) + 1))
	for i, ca in enumerate(a, 1):
		cur = [i]
		for j, cb in enumerate(b, 1):
			cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if ca == cb else 1)))
		prev = cur
	return prev[-1]


def _batch_fuzzy_equal(a, b):
	if not a or not b:
		return False
	if a == b:
		return True
	ka, kb = _batch_match_key(a), _batch_match_key(b)
	if ka == kb:
		return True
	ma = re.search(r"/(\d+)$", ka)
	mb = re.search(r"/(\d+)$", kb)
	if ma and mb and ma.group(1) == mb.group(1):
		return True
	if ka and kb and min(len(ka), len(kb)) >= 8:
		return _levenshtein(ka, kb) <= 2
	return False


def _transfer_scan_candidates(raw):
	raw = (raw or "").strip()
	cands = []

	def add(v):
		v = (v or "").strip()
		if v and v not in cands:
			cands.append(v)

	add(raw)
	add(raw.upper())
	norm = _normalize_transfer_scan(raw)
	add(norm)
	add(_batch_match_key(norm))
	if "/" in norm:
		parts = norm.split("/")
		add(parts[-1])
		if len(parts) >= 2:
			add(f"{parts[-2]}/{parts[-1]}")
	m = re.search(r"/(\d+)", norm)
	if m:
		add("/" + m.group(1))
		if len(m.group(1)) == 1:
			add("/" + m.group(1) + "0")
	return cands


def _row_needs_scan(row):
	qty = flt(row.qty)
	scanned = flt(row.get("scanned_qty") or row.get("custom_scanned_qty") or 0)
	return qty > scanned + 0.01


def _ste_row_batch_values(row, has_roll_no):
	bn = (row.batch_no or "").strip()
	roll = (row.get("custom_roll_no") or "").strip() if has_roll_no else ""
	return [x for x in (bn, roll) if x]


def _find_transfer_ste_row_for_barcode(se, barcode):
	"""Match STE line by batch scan; supports multi-item STE and noisy scanner input."""
	candidates = _transfer_scan_candidates(barcode)
	if not candidates:
		return None

	rows = list(se.items or [])
	has_roll_no = frappe.db.has_column("Stock Entry Detail", "custom_roll_no")

	for cand in candidates:
		ck = _batch_match_key(cand)
		for row in rows:
			for bn in _ste_row_batch_values(row, has_roll_no):
				if cand == bn or ck == _batch_match_key(bn):
					return row

	ta_name = frappe.db.get_value("Transfer Approval", {"stock_entry": se.name}, "name")
	if ta_name:
		ta_lines = frappe.get_all(
			"Transfer Approval Line",
			filters={"parent": ta_name},
			fields=["batch_no", "item_code"],
		)
		for line in ta_lines:
			abn = (line.batch_no or "").strip()
			if not abn:
				continue
			for cand in candidates:
				if not _batch_fuzzy_equal(cand, abn):
					continue
				for row in rows:
					if row.item_code != line.item_code:
						continue
					row_batches = _ste_row_batch_values(row, has_roll_no)
					if any(_batch_fuzzy_equal(rb, abn) for rb in row_batches) or not row_batches:
						return row
				item_rows = [r for r in rows if r.item_code == line.item_code]
				if len(item_rows) == 1:
					return item_rows[0]

	for cand in candidates:
		m = re.search(r"/(\d+)", _batch_match_key(cand))
		if not m:
			continue
		suffix = "/" + m.group(1)
		matched = [
			r
			for r in rows
			if any(suffix in _batch_match_key(b) or _batch_match_key(b).endswith(suffix) for b in _ste_row_batch_values(r, has_roll_no))
		]
		if len(matched) == 1:
			return matched[0]
		if len(m.group(1)) == 1:
			suffix10 = "/" + m.group(1) + "0"
			matched = [
				r
				for r in rows
				if any(_batch_match_key(b).endswith(suffix10) for b in _ste_row_batch_values(r, has_roll_no))
			]
			if len(matched) == 1:
				return matched[0]

	for cand in candidates:
		if frappe.db.exists("Batch", cand):
			item = frappe.db.get_value("Batch", cand, "item")
			for row in rows:
				if row.item_code == item:
					return row
		if "/" in cand:
			tail = (cand.split("/")[-1] or "").strip()
			if tail.isdigit():
				for row in rows:
					for bn in _ste_row_batch_values(row, has_roll_no):
						if bn.endswith(f"/{tail}") or bn.endswith(f"/{tail.lstrip('0') or '0'}"):
							return row

	for cand in candidates:
		digits = re.sub(r"\D", "", cand)
		if len(digits) < 8:
			continue
		item = frappe.db.get_value("Item", {"barcode": cand}, "name")
		if not item and digits != cand:
			item = frappe.db.get_value("Item", {"barcode": digits}, "name")
		if not item:
			continue
		item_rows = [r for r in rows if r.item_code == item]
		unscanned = [r for r in item_rows if _row_needs_scan(r)]
		if len(unscanned) == 1:
			return unscanned[0]
		if len(item_rows) == 1:
			return item_rows[0]

	return None


@frappe.whitelist()
def record_transfer_barcode_scan(stock_entry, barcode, confirm_unscan=0):
	"""Material Transfer: first scan marks the roll. Second scan can set scanned qty to 0."""
	barcode = (barcode or "").strip()
	if not barcode:
		return {"ok": False, "error": _("No barcode provided")}
	if not stock_entry:
		return {"ok": False, "error": _("Stock Entry is required")}

	se = frappe.get_doc("Stock Entry", stock_entry)
	if se.docstatus != 0:
		return {"ok": False, "error": _("Cannot scan on a submitted Stock Entry")}
	if (se.stock_entry_type or "").strip() != "Material Transfer":
		return {"ok": False, "error": _("Barcode scan is only for Material Transfer")}
	if not stock_entry_requires_logistics_scan(se):
		return {"ok": False, "error": _("Barcode scan applies only to logistics transfer Stock Entries.")}

	has_scanned = frappe.db.has_column("Stock Entry Detail", "scanned_qty")
	has_custom_scanned = frappe.db.has_column("Stock Entry Detail", "custom_scanned_qty")

	match = _find_transfer_ste_row_for_barcode(se, barcode)

	if not match:
		return {"ok": False, "error": _("Batch {0} is not in the approved transfer list").format(barcode)}

	approved_qty = flt(match.qty)
	current_scanned = max(
		flt(match.get("scanned_qty") or 0),
		flt(match.get("custom_scanned_qty") or 0),
	)
	batch_label = (match.batch_no or "").strip() or barcode
	if current_scanned > 0 and not cint(confirm_unscan):
		return {
			"ok": True,
			"ask_unscan": True,
			"row_name": match.name,
			"idx": match.idx,
			"scanned_qty": current_scanned,
			"qty": approved_qty,
			"batch_no": batch_label,
			"item_code": match.item_code,
			"message": _("Roll {0} already scanned.").format(batch_label),
		}
	new_scanned = 0 if cint(confirm_unscan) else (approved_qty if approved_qty > 0 else 0)

	updates = {}
	if has_scanned:
		updates["scanned_qty"] = new_scanned
	if has_custom_scanned:
		updates["custom_scanned_qty"] = new_scanned
	if updates:
		frappe.db.set_value("Stock Entry Detail", match.name, updates, update_modified=False)
	if flt(match.qty) != approved_qty:
		frappe.db.set_value("Stock Entry Detail", match.name, "qty", approved_qty, update_modified=False)

	return {
		"ok": True,
		"row_name": match.name,
		"idx": match.idx,
		"scanned_qty": new_scanned,
		"qty": approved_qty,
		"batch_no": (match.batch_no or "").strip() or barcode,
		"item_code": match.item_code,
	}


@frappe.whitelist()
def get_transfer_approval_roll_list(approval_name=None, stock_entry=None):
	"""Batches on Transfer Approval for Approved Rolls dialog + print."""
	name = _cstr(approval_name).strip()
	ste = _cstr(stock_entry).strip()
	if not name and ste:
		name = _cstr(frappe.db.get_value("Transfer Approval", {"stock_entry": ste}, "name") or "")
	if not name or not frappe.db.exists("Transfer Approval", name):
		frappe.throw(_("Transfer Approval not found."))
	meta = frappe.get_meta("Transfer Approval Line")
	fields = ["batch_no", "item_code", "qty", "party_code", "customer_name"]
	for f in ("quality", "color", "gsm", "width_inch", "spr_name"):
		if meta.has_field(f):
			fields.append(f)
	lines = frappe.get_all(
		"Transfer Approval Line",
		filters={"parent": name},
		fields=fields,
		order_by="idx asc",
		limit_page_length=0,
	) or []
	rolls = []
	for ln in lines:
		bn = _cstr(ln.get("batch_no"))
		if not bn:
			continue
		spec = _roll_spec_dict(ln, ln.get("item_code"))
		# Always fill missing width (and other specs) from SPR — quality/GSM alone can be present.
		if (
			not spec.get("quality")
			or not spec.get("color")
			or not cint(spec.get("gsm") or 0)
			or flt(spec.get("width_inch") or 0) <= 0
			or flt(spec.get("meter_per_roll") or 0) <= 0
		):
			spr_row = frappe.db.get_value(
				"Shaft Production Run Item",
				{"batch_no": bn},
				_spr_item_query_fields(),
				as_dict=True,
			)
			if spr_row:
				merged = dict(spr_row)
				# Prefer non-empty TA line values, fill gaps from SPR
				for k in ("quality", "color", "gsm", "width_inch", "meter_per_roll", "meter_roll", "item_code"):
					if ln.get(k) not in (None, "", 0, 0.0):
						merged[k] = ln.get(k)
				spec = _roll_spec_dict(merged, merged.get("item_code") or ln.get("item_code"))
		qty = flt(ln.get("qty") or 0)
		party = _cstr(ln.get("party_code") or "")
		rolls.append(
			{
				"batch_no": bn,
				"item_code": _cstr(ln.get("item_code")),
				"party_code": party,
				"order_code": party,
				"net_weight": qty,
				"gross_weight": qty,
				"meter_per_roll": flt(spec.get("meter_per_roll") or 0),
				**spec,
			}
		)
	# Header order code from Transfer Approval / linked Stock Entry when lines lack party_code
	header_order = ""
	ta_meta = frappe.get_meta("Transfer Approval")
	for fn in ("party_code", "order_code", "custom_order_code"):
		if ta_meta.has_field(fn):
			header_order = _cstr(frappe.db.get_value("Transfer Approval", name, fn) or "")
			if header_order:
				break
	if not header_order and ste:
		se_meta = frappe.get_meta("Stock Entry")
		for fn in ("custom_order_code", "order_code", "party_code"):
			if se_meta.has_field(fn):
				header_order = _cstr(frappe.db.get_value("Stock Entry", ste, fn) or "")
				if header_order:
					break
	if header_order:
		for r in rolls:
			if not _cstr(r.get("party_code") or r.get("order_code")):
				r["party_code"] = header_order
				r["order_code"] = header_order
	return {"approval_name": name, "rolls": rolls, "order_code": header_order}


def stock_entry_requires_logistics_scan(stock_entry):
	"""True only for Material Transfer STE created from Transfer Approval / logistics (external transfer)."""
	if isinstance(stock_entry, str):
		if not stock_entry or not frappe.db.exists("Stock Entry", stock_entry):
			return False
		stock_entry = frappe.get_doc("Stock Entry", stock_entry)
	se = stock_entry
	if (se.stock_entry_type or "").strip() != "Material Transfer":
		return False
	fn = _stock_entry_external_transfer_fieldname()
	if fn and cint(se.get(fn)):
		return True
	if se.name and frappe.db.exists("Transfer Approval", {"stock_entry": se.name}):
		return True
	return False


def stock_entry_validate_logistics_scan(doc, method=None):
	"""Server guard on STE submit only — not when approving transfer / creating draft STE."""
	if not stock_entry_requires_logistics_scan(doc):
		return
	pending = []
	for row in doc.items or []:
		qty = flt(row.qty)
		scanned = max(
			flt(row.get("scanned_qty") or 0),
			flt(row.get("custom_scanned_qty") or 0),
		)
		if qty > 0 and scanned >= qty - 0.01:
			if frappe.db.has_column("Stock Entry Detail", "scanned_qty"):
				row.scanned_qty = qty
			if frappe.db.has_column("Stock Entry Detail", "custom_scanned_qty"):
				row.custom_scanned_qty = qty
			continue
		if qty > scanned + 0.01:
			pending.append(row)
	scanned_rows = [row for row in (doc.items or []) if max(flt(row.get("scanned_qty") or 0), flt(row.get("custom_scanned_qty") or 0)) > 0]
	if not scanned_rows:
		frappe.throw(
			_("Scan at least one roll before submit."),
			title=_("Scan validation"),
		)
	# Deliver scanned rolls only. Unscanned rows stay off this submit; the approval line is unchanged.
	if pending:
		doc.items = scanned_rows
		for row in doc.items:
			scanned = max(flt(row.get("scanned_qty") or 0), flt(row.get("custom_scanned_qty") or 0))
			row.qty = scanned


@frappe.whitelist()
def stock_entry_is_logistics_transfer(stock_entry=None):
	"""Client helper: whether scan / approved-roll rules apply on this STE."""
	return bool(stock_entry_requires_logistics_scan(stock_entry))
