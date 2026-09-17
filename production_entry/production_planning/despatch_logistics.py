# -*- coding: utf-8 -*-
"""Despatch logistics: approval workflow and Delivery Note creation (mirrors transfer approvals)."""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from production_entry.production_planning.scheduler_api import (
	MOVEMENT_DESPATCH,
	get_color_chart_data,
	normalize_movement_type,
)
from production_entry.production_planning.transfer_logistics import (
	BOARD_KIND_TO_SCOPE,
	TRANSFER_WAREHOUSE_BY_COMPANY,
	_batch_match_key,
	_cstr,
	_chart_fetch_kwargs,
	_parse_chart_rows,
	_primary_submitted_spr_for_batch,
	_resolve_submitted_spr_ids,
	_roll_spec_dict,
	_spr_item_query_fields,
	_row_matches_filters,
	_transfer_date_in_scope,
	_transfer_row_unit_is_unassigned,
	_user_can_approve_transfer,
	get_logistics_companies,
	get_spr_produced_batches,
)

DESPATCH_APPROVER_ROLES = frozenset({"System Manager", "Manufacturing Manager", "Administrator"})
DESPATCH_PENDING_ARRANGEMENT_HISTORY_KEY = "despatch_pending_arrangement_history"
DESPATCH_APPROVED_ARRANGEMENT_HISTORY_KEY = "despatch_approved_arrangement_history"
DESPATCH_QUEUE_DEFAULTS_PENDING = "despatch_queue_pending"
DESPATCH_QUEUE_DEFAULTS_APPROVED = "despatch_queue_approved"


def _user_can_approve_despatch():
	roles = set(frappe.get_roles(frappe.session.user) or [])
	return bool(roles & DESPATCH_APPROVER_ROLES) or _user_can_approve_transfer()


def _clubbing_sheet_transport_fields(club_names):
	"""Map Clubbing Sheet name → vehicle_no, driver, driver_ph_no for kanban cards."""
	names = sorted({_cstr(n) for n in (club_names or []) if _cstr(n)})
	out = {}
	if not names or not frappe.db.exists("DocType", "Clubbing Sheet"):
		return out
	fields = ["name"]
	for fn in ("vehicle_no", "driver", "driver_ph_no", "driver_phone", "driver_ph"):
		if frappe.db.has_column("Clubbing Sheet", fn):
			fields.append(fn)
	if len(fields) == 1:
		return out
	rows = frappe.get_all(
		"Clubbing Sheet",
		filters={"name": ["in", names]},
		fields=fields,
		limit_page_length=len(names),
	)
	for r in rows or []:
		phone = _cstr(r.get("driver_ph_no") or r.get("driver_phone") or r.get("driver_ph"))
		out[_cstr(r.name)] = {
			"vehicle_no": _cstr(r.get("vehicle_no")),
			"driver": _cstr(r.get("driver")),
			"driver_ph_no": phone,
		}
	return out


def _fg_warehouse_for_company(company):
	wh = TRANSFER_WAREHOUSE_BY_COMPANY.get(_cstr(company))
	if wh:
		return wh.get("s_warehouse")
	return frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 0},
		"name",
		order_by="modified desc",
	)


def _batches_reserved_on_despatch():
	rows = frappe.db.sql(
		"""
		select dl.batch_no
		from `tabDespatch Approval Line` dl
		inner join `tabDespatch Approval` da on da.name = dl.parent
		where da.status != 'Rejected' and ifnull(dl.batch_no,'') != ''
		""",
		as_dict=1,
	)
	return {r.batch_no for r in rows if r.batch_no}


def _despatch_approval_queue_fieldname():
	if frappe.db.has_column("Despatch Approval", "custom_logistics_queue_idx"):
		return "custom_logistics_queue_idx"
	return None


def _despatch_pending_arrangement_history_key(from_company):
	return f"{DESPATCH_PENDING_ARRANGEMENT_HISTORY_KEY}::{_cstr(from_company)}"


def _load_despatch_pending_arrangement_history(from_company):
	key = _despatch_pending_arrangement_history_key(from_company)
	raw = frappe.defaults.get_global_default(key) or "[]"
	try:
		arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
		return arr if isinstance(arr, list) else []
	except Exception:
		return []


def _save_despatch_pending_arrangement_history(from_company, snapshot):
	key = _despatch_pending_arrangement_history_key(from_company)
	history = _load_despatch_pending_arrangement_history(from_company)
	history.append(snapshot)
	if len(history) > 20:
		history = history[-20:]
	frappe.defaults.set_global_default(key, json.dumps(history))


def _despatch_queue_defaults_key(from_company, scope):
	prefix = DESPATCH_QUEUE_DEFAULTS_PENDING if scope == "pending" else DESPATCH_QUEUE_DEFAULTS_APPROVED
	return f"{prefix}::{_cstr(from_company)}"


def _load_despatch_queue_from_defaults(from_company, scope):
	raw = frappe.defaults.get_global_default(_despatch_queue_defaults_key(from_company, scope)) or "[]"
	try:
		arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
		return [n for n in arr if _cstr(n)]
	except Exception:
		return []


def _save_despatch_queue_to_defaults(from_company, scope, approval_names):
	names = [n for n in (approval_names or []) if _cstr(n)]
	frappe.defaults.set_global_default(
		_despatch_queue_defaults_key(from_company, scope),
		json.dumps(names),
	)


def _order_approval_cards(cards, from_company, scope):
	"""Apply saved queue order (defaults + DB queue_idx fallback)."""
	items = list(cards or [])
	if not items:
		return items
	saved = _load_despatch_queue_from_defaults(from_company, scope)
	if saved:
		by_name = {c["name"]: c for c in items}
		ordered = [by_name[n] for n in saved if n in by_name]
		tail = [c for c in items if c["name"] not in saved]
		return ordered + tail
	items.sort(
		key=lambda a: (
			cint(a.get("queue_idx") or 0) or 9999,
			_cstr(a.get("creation")),
		)
	)
	return items


def _despatch_status_blocks_request(status):
	st = _cstr(status).strip().lower()
	if not st:
		return False
	if st == "rejected":
		return False
	if st in {"pending approval", "approved", "draft", "draft dn", "despatched"}:
		return True
	return False


def _has_pt_club_fields():
	return frappe.db.has_column("Planning Table", "custom_clubbing_sheet")


def _has_da_club_field():
	return frappe.db.has_column("Despatch Approval", "custom_clubbing_sheet")


def _has_dal_scan_field():
	return frappe.db.has_column("Despatch Approval Line", "custom_scanned")


def _has_da_lane_date_field():
	return frappe.db.has_column("Despatch Approval", "custom_despatch_lane_date")


def _despatch_lane_date(da):
	"""Stable logistics lane date — not bumped when DN links change."""
	if isinstance(da, dict):
		if _has_da_lane_date_field():
			ld = _cstr(da.get("custom_despatch_lane_date"))[:10]
			if ld:
				return ld
		return _cstr(da.get("creation"))[:10]
	if _has_da_lane_date_field():
		ld = _cstr(getattr(da, "custom_despatch_lane_date", None) or "")[:10]
		if ld:
			return ld
	return _cstr(getattr(da, "creation", None) or "")[:10]


def _set_despatch_lane_date(da_name, lane_date, update_modified=False):
	if not _has_da_lane_date_field() or not da_name:
		return
	frappe.db.set_value(
		"Despatch Approval",
		da_name,
		"custom_despatch_lane_date",
		getdate(lane_date),
		update_modified=update_modified,
	)


def _club_fields_for_ptrs(ptrs):
	"""Map Planning Table name → clubbing_sheet / loading_sequence / load_order / despatch_customer."""
	names = [_cstr(p) for p in (ptrs or []) if _cstr(p) and not _cstr(p).startswith("sprgrp:")]
	out = {}
	if not names or not _has_pt_club_fields():
		return out
	placeholders = ", ".join(["%s"] * len(names))
	cols = ["name", "custom_clubbing_sheet"]
	if frappe.db.has_column("Planning Table", "custom_loading_sequence"):
		cols.append("custom_loading_sequence")
	if frappe.db.has_column("Planning Table", "custom_club_load_order"):
		cols.append("custom_club_load_order")
	if frappe.db.has_column("Planning Table", "custom_despatch_customer"):
		cols.append("custom_despatch_customer")
	if frappe.db.has_column("Planning Table", "custom_despatch_sales_order"):
		cols.append("custom_despatch_sales_order")
	rows = frappe.db.sql(
		f"select {', '.join(cols)} from `tabPlanning Table` where name in ({placeholders})",
		tuple(names),
		as_dict=True,
	)
	for r in rows or []:
		out[_cstr(r.name)] = {
			"clubbing_sheet": _cstr(r.get("custom_clubbing_sheet")),
			"loading_sequence": _cstr(r.get("custom_loading_sequence")),
			"club_load_order": cint(r.get("custom_club_load_order") or 0),
			"despatch_customer": _cstr(r.get("custom_despatch_customer")),
			"despatch_sales_order": _cstr(r.get("custom_despatch_sales_order")),
		}
	return out


def _parse_delivery_notes_json(raw):
	try:
		arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
		if isinstance(arr, list):
			return [_cstr(x) for x in arr if _cstr(x)]
	except Exception:
		pass
	return []


def _save_delivery_notes_json(da_name, names):
	if not frappe.db.has_column("Despatch Approval", "custom_delivery_notes"):
		return
	frappe.db.set_value(
		"Despatch Approval",
		da_name,
		"custom_delivery_notes",
		json.dumps([_cstr(n) for n in (names or []) if _cstr(n)]),
		update_modified=False,
	)


def _collect_despatch_delivery_note_names(da):
	"""Merge custom_delivery_notes JSON + delivery_note header (dict or doc)."""
	names = []
	seen = set()
	if isinstance(da, dict):
		raw = da.get("custom_delivery_notes")
		header = _cstr(da.get("delivery_note"))
	else:
		raw = getattr(da, "custom_delivery_notes", None)
		header = _cstr(getattr(da, "delivery_note", None))
	for nm in ([header] if header else []) + _parse_delivery_notes_json(raw or ""):
		nm = _cstr(nm)
		if nm and nm not in seen:
			seen.add(nm)
			names.append(nm)
	return names


def _active_delivery_note_names(names):
	active = []
	for nm in names or []:
		if not frappe.db.exists("Delivery Note", nm):
			continue
		ds = cint(frappe.db.get_value("Delivery Note", nm, "docstatus") or 0)
		if ds < 2:
			active.append(nm)
	return active


def _sync_despatch_delivery_notes(despatch_approval, persist=False):
	"""Drop deleted/cancelled DN refs so Create DN works again after delete."""
	if isinstance(despatch_approval, dict):
		da_dict = despatch_approval
		da_name = da_dict.get("name")
	elif isinstance(despatch_approval, str):
		da_name = despatch_approval
		da_dict = frappe.get_doc("Despatch Approval", da_name).as_dict()
	else:
		da_name = despatch_approval.name
		da_dict = despatch_approval.as_dict()

	stored = _parse_delivery_notes_json(da_dict.get("custom_delivery_notes") or "")
	all_names = _collect_despatch_delivery_note_names(da_dict)
	active = _active_delivery_note_names(all_names)

	if persist and da_name:
		primary = active[0] if active else ""
		cur_primary = _cstr(da_dict.get("delivery_note"))
		if active != stored or cur_primary != primary:
			_save_delivery_notes_json(da_name, active)
			if cur_primary != primary:
				frappe.db.set_value(
					"Despatch Approval",
					da_name,
					"delivery_note",
					primary,
					update_modified=False,
				)
	return active


def _despatch_approvals_referencing_dn(dn_name):
	"""Despatch Approvals that link to this Delivery Note (header or JSON list)."""
	dn_name = _cstr(dn_name).strip()
	if not dn_name:
		return []
	found = set()
	rows = frappe.db.sql(
		"""
		select name from `tabDespatch Approval`
		where delivery_note = %s
		""",
		dn_name,
		as_dict=True,
	) or []
	for r in rows:
		found.add(r.name)
	if frappe.db.has_column("Despatch Approval", "custom_delivery_notes"):
		extra = frappe.db.sql(
			"""
			select name from `tabDespatch Approval`
			where ifnull(custom_delivery_notes, '') like %s
			""",
			f'%"{dn_name}"%',
			as_dict=True,
		) or []
		for r in extra:
			found.add(r.name)
	return sorted(found)


def _despatch_approval_has_submitted_dn(da_name):
	"""True if any linked Delivery Note on this approval is submitted."""
	if not da_name or not frappe.db.exists("Despatch Approval", da_name):
		return False
	row = frappe.db.get_value(
		"Despatch Approval",
		da_name,
		["delivery_note", "custom_delivery_notes"],
		as_dict=True,
	)
	if not row:
		return False
	names = _active_delivery_note_names(_collect_despatch_delivery_note_names(row))
	for nm in names:
		if cint(frappe.db.get_value("Delivery Note", nm, "docstatus") or 0) >= 1:
			return True
	return False


def _remove_dn_from_despatch_approval(da_name, dn_name):
	"""Unlink one Delivery Note from Despatch Approval and refresh planning status."""
	dn_name = _cstr(dn_name).strip()
	if not da_name or not dn_name or not frappe.db.exists("Despatch Approval", da_name):
		return False
	da = frappe.get_doc("Despatch Approval", da_name)
	current = _sync_despatch_delivery_notes(da, persist=False)
	if dn_name not in current:
		return False
	remaining = [n for n in current if n != dn_name]
	_save_delivery_notes_json(da_name, remaining)
	primary = remaining[0] if remaining else ""
	if _cstr(da.delivery_note) != primary:
		frappe.db.set_value(
			"Despatch Approval", da_name, "delivery_note", primary, update_modified=False
		)
	for ln in da.lines or []:
		ptr = _cstr(ln.planning_table_row)
		if ptr:
			update_planning_row_despatch_status(ptr)
	return True


def _sync_psi_despatch_fields(pt_name, updates):
	if not updates or not frappe.db.table_exists("Planning sheet Item"):
		return
	pt = frappe.db.get_value(
		"Planning Table",
		pt_name,
		["item_code", "sales_order_item", "so_item", "parent", "source_item"],
		as_dict=True,
	)
	if not pt:
		return
	psi = _cstr(pt.get("source_item"))
	if not psi or not frappe.db.exists("Planning sheet Item", psi):
		soik = _cstr(pt.get("sales_order_item") or pt.get("so_item"))
		filters = {"parent": pt.parent, "item_code": pt.item_code}
		if soik and frappe.db.has_column("Planning sheet Item", "sales_order_item"):
			filters["sales_order_item"] = soik
		elif soik and frappe.db.has_column("Planning sheet Item", "so_item"):
			filters["so_item"] = soik
		psi = frappe.db.get_value("Planning sheet Item", filters, "name")
	if psi:
		frappe.db.set_value("Planning sheet Item", psi, updates, update_modified=False)


def update_planning_row_despatch_status(ptr):
	"""Mirror active despatch approval state onto Planning Table + Planning sheet Item."""
	ptr = _cstr(ptr)
	if not ptr or not frappe.db.exists("Planning Table", ptr):
		return
	rows = frappe.db.sql(
		"""
		select da.name, da.status, da.delivery_note
		from `tabDespatch Approval Line` dl
		inner join `tabDespatch Approval` da on da.name = dl.parent
		where dl.planning_table_row = %s and ifnull(da.status, '') != 'Rejected'
		group by da.name, da.status, da.delivery_note
		order by da.modified desc
		""",
		ptr,
		as_dict=1,
	)
	if not rows:
		updates = {"custom_despatch_status": "", "custom_despatch_approval": ""}
	else:
		statuses = [_cstr(r.get("status")) for r in rows]
		approval = _cstr(rows[0].get("name"))
		if any(s in ("Pending Approval", "Draft") for s in statuses):
			final_status = "Pending Approval"
			for r in rows:
				if r.get("status") in ("Pending Approval", "Draft"):
					approval = _cstr(r.get("name"))
					break
		elif any(s == "Approved" for s in statuses):
			final_status = "Approved"
			for r in rows:
				if r.get("status") == "Approved":
					approval = _cstr(r.get("name"))
					if _despatch_approval_has_submitted_dn(approval):
						final_status = "Despatched"
					break
		else:
			final_status = statuses[0] or ""
		updates = {"custom_despatch_status": final_status, "custom_despatch_approval": approval}
	if frappe.db.has_column("Planning Table", "custom_despatch_status"):
		frappe.db.set_value("Planning Table", ptr, updates, update_modified=False)
	_sync_psi_despatch_fields(ptr, updates)


def _resolved_planning_row_despatch_status(ptr, fallback_status=""):
	ptr = _cstr(ptr)
	if not ptr or not frappe.db.has_column("Planning Table", "custom_despatch_status"):
		return _cstr(fallback_status)
	row = frappe.db.get_value(
		"Planning Table",
		ptr,
		["custom_despatch_status", "custom_despatch_approval"],
		as_dict=True,
	)
	if not row:
		return _cstr(fallback_status)
	status = _cstr(row.get("custom_despatch_status") or fallback_status)
	if not status:
		live_rows = frappe.db.sql(
			"""
			select da.status
			from `tabDespatch Approval Line` dl
			inner join `tabDespatch Approval` da on da.name = dl.parent
			where dl.planning_table_row = %s and ifnull(da.status, '') != 'Rejected'
			order by da.modified desc
			limit 1
			""",
			ptr,
			as_dict=1,
		)
		if live_rows:
			update_planning_row_despatch_status(ptr)
			status = _cstr(
				frappe.db.get_value("Planning Table", ptr, "custom_despatch_status") or live_rows[0].get("status")
			)
	approval = _cstr(row.get("custom_despatch_approval"))
	if approval and frappe.db.exists("Despatch Approval", approval):
		live = _cstr(frappe.db.get_value("Despatch Approval", approval, "status"))
		if live == "Rejected":
			update_planning_row_despatch_status(ptr)
			return _cstr(
				frappe.db.get_value("Planning Table", ptr, "custom_despatch_status") or ""
			)
		if live and live != status:
			status = live
	return status


def _stamp_planning_rows_for_despatch_request(approval_name):
	da = frappe.get_doc("Despatch Approval", approval_name)
	for ln in da.lines or []:
		if ln.planning_table_row:
			update_planning_row_despatch_status(ln.planning_table_row)


def _pending_approval_names_sorted(from_company):
	qf = _despatch_approval_queue_fieldname()
	fields = ["name", "status", "creation"]
	if qf:
		fields.append(qf)
	rows = frappe.get_all(
		"Despatch Approval",
		filters={"from_company": _cstr(from_company), "status": ["in", ["Pending Approval", "Draft"]]},
		fields=fields,
		order_by="modified desc",
		limit_page_length=200,
	)
	rows.sort(
		key=lambda a: (
			cint(a.get(qf) or 0) or 9999 if qf else 9999,
			_cstr(a.get("creation")),
		)
	)
	return [_cstr(r.name) for r in rows if _cstr(r.name)]


def _apply_despatch_pending_queue(from_company, approval_names):
	qf = _despatch_approval_queue_fieldname()
	if not qf:
		return 0
	fc = _cstr(from_company)
	names = [n for n in (approval_names or []) if _cstr(n)]
	updated = 0
	for idx, name in enumerate(names, start=1):
		if not frappe.db.exists("Despatch Approval", name):
			continue
		st = _cstr(frappe.db.get_value("Despatch Approval", name, "status"))
		if st not in ("Pending Approval", "Draft"):
			continue
		if fc and _cstr(frappe.db.get_value("Despatch Approval", name, "from_company")) != fc:
			continue
		frappe.db.set_value("Despatch Approval", name, qf, idx, update_modified=False)
		updated += 1
	_save_despatch_queue_to_defaults(fc, "pending", names)
	frappe.db.commit()
	return updated


def _approved_approval_names_sorted(from_company):
	qf = _despatch_approval_queue_fieldname()
	fields = ["name", "status", "creation"]
	if qf:
		fields.append(qf)
	rows = frappe.get_all(
		"Despatch Approval",
		filters={"from_company": _cstr(from_company), "status": "Approved"},
		fields=fields,
		order_by="modified desc",
		limit_page_length=200,
	)
	rows.sort(
		key=lambda a: (
			cint(a.get(qf) or 0) or 9999 if qf else 9999,
			_cstr(a.get("creation")),
		)
	)
	return [_cstr(r.name) for r in rows if _cstr(r.name)]


def _apply_despatch_approved_queue(from_company, approval_names):
	qf = _despatch_approval_queue_fieldname()
	fc = _cstr(from_company)
	names = [n for n in (approval_names or []) if _cstr(n)]
	updated = 0
	for idx, name in enumerate(names, start=1):
		if not frappe.db.exists("Despatch Approval", name):
			continue
		if _cstr(frappe.db.get_value("Despatch Approval", name, "status")) != "Approved":
			continue
		if fc and _cstr(frappe.db.get_value("Despatch Approval", name, "from_company")) != fc:
			continue
		if qf:
			frappe.db.set_value("Despatch Approval", name, qf, idx, update_modified=False)
		updated += 1
	_save_despatch_queue_to_defaults(fc, "approved", names)
	frappe.db.commit()
	return updated


def _resolve_customer(customer_name):
	cn = _cstr(customer_name)
	if not cn:
		return ""
	if frappe.db.exists("Customer", cn):
		return cn
	found = frappe.db.get_value("Customer", {"customer_name": cn}, "name")
	return found or ""


@frappe.whitelist()
def get_despatch_company_cards(
	from_company=None,
	view_scope=None,
	date=None,
	week=None,
	month=None,
	order_code=None,
):
	"""Company cards for despatch mode with pending/approved approval chips."""
	fc = _cstr(from_company)
	companies = get_logistics_companies()
	oc = _cstr(order_code).strip().lower()
	out = []
	for c in companies:
		name = c["name"]
		if fc and name != fc:
			continue
		filters = {"from_company": name}
		if oc:
			pass
		qf = _despatch_approval_queue_fieldname()
		approval_fields = ["name", "status", "delivery_note", "modified", "creation"]
		if qf:
			approval_fields.append(qf)
		if _has_da_club_field():
			approval_fields.append("custom_clubbing_sheet")
		if frappe.db.has_column("Despatch Approval", "custom_delivery_notes"):
			approval_fields.append("custom_delivery_notes")
		if _has_da_lane_date_field():
			approval_fields.append("custom_despatch_lane_date")
		approvals = frappe.get_all(
			"Despatch Approval",
			filters=filters,
			fields=approval_fields,
			order_by="modified desc",
			limit_page_length=80,
		)
		club_ids_for_transport = []
		if _has_da_club_field():
			club_ids_for_transport = [
				_cstr(a.get("custom_clubbing_sheet"))
				for a in (approvals or [])
				if _cstr(a.get("custom_clubbing_sheet"))
			]
		club_transport = _clubbing_sheet_transport_fields(club_ids_for_transport)
		enriched = []
		for da in approvals or []:
			despatch_date = _despatch_lane_date(da)
			if not _transfer_date_in_scope(despatch_date, view_scope, date, week, month):
				continue
			line_fields = ["party_code", "customer_name", "item_code", "qty", "batch_no", "name"]
			if frappe.db.has_column("Despatch Approval Line", "custom_loading_sequence"):
				line_fields.append("custom_loading_sequence")
			if frappe.db.has_column("Despatch Approval Line", "custom_club_load_order"):
				line_fields.append("custom_club_load_order")
			if _has_dal_scan_field():
				line_fields.append("custom_scanned")
			lines = frappe.get_all(
				"Despatch Approval Line",
				filters={"parent": da.name},
				fields=line_fields,
				limit_page_length=500,
			)
			codes = []
			customers = []
			items = set()
			batches = set()
			order_map = {}
			scanned_total = 0
			line_total = 0
			for ln in lines:
				pc = _cstr(ln.get("party_code"))
				if pc and pc not in codes:
					codes.append(pc)
				cn = _cstr(ln.get("customer_name"))
				if cn and cn not in customers:
					customers.append(cn)
				ic = _cstr(ln.get("item_code"))
				if ic:
					items.add(ic)
				bn = _cstr(ln.get("batch_no"))
				if bn:
					batches.add(bn)
				line_total += 1
				scanned = cint(ln.get("custom_scanned") or 0)
				if scanned:
					scanned_total += 1
				if pc:
					om = order_map.setdefault(
						pc,
						{
							"party_code": pc,
							"customer_name": cn,
							"loading_sequence": _cstr(ln.get("custom_loading_sequence")),
							"club_load_order": cint(ln.get("custom_club_load_order") or 0),
							"total": 0,
							"scanned": 0,
						},
					)
					om["total"] += 1
					om["scanned"] += scanned
					if not om["loading_sequence"]:
						om["loading_sequence"] = _cstr(ln.get("custom_loading_sequence"))
					if not om["club_load_order"]:
						om["club_load_order"] = cint(ln.get("custom_club_load_order") or 0)
					if cn and not om["customer_name"]:
						om["customer_name"] = cn
			if oc and not any(oc in _cstr(x).lower() for x in codes):
				continue
			total_qty = round(sum(flt(ln.get("qty")) for ln in lines), 3)
			dn_list = _sync_despatch_delivery_notes(da, persist=True)
			dn_name = dn_list[0] if dn_list else ""
			dn_docstatus = 0
			all_submitted = True
			any_draft = False
			if dn_list:
				for dn in dn_list:
					if frappe.db.exists("Delivery Note", dn):
						ds = cint(frappe.db.get_value("Delivery Note", dn, "docstatus") or 0)
						if ds >= 1:
							dn_docstatus = max(dn_docstatus, 1)
						else:
							all_submitted = False
							any_draft = True
					else:
						all_submitted = False
			elif dn_name and frappe.db.exists("Delivery Note", dn_name):
				dn_docstatus = cint(frappe.db.get_value("Delivery Note", dn_name, "docstatus") or 0)
				all_submitted = dn_docstatus >= 1
				any_draft = dn_docstatus == 0
			else:
				all_submitted = False

			roll_count = len(batches) or len(lines)
			item_count = len(items) or len(lines)
			card_status = da.status
			if da.status == "Approved" and all_submitted and (dn_list or dn_name):
				card_status = "Despatched"
			elif da.status == "Approved" and (any_draft or (dn_name and dn_docstatus == 0)):
				card_status = "Draft DN"
			queue_idx = cint(da.get(qf) or 0) if qf else 0
			club_orders = sorted(
				order_map.values(),
				key=lambda o: (cint(o.get("club_load_order") or 0) or 9999, _cstr(o.get("party_code"))),
			)
			scan_complete = line_total > 0 and scanned_total >= line_total
			# Non-club approvals: treat scan as complete so Create DN stays available
			club_id = _cstr(da.get("custom_clubbing_sheet")) if _has_da_club_field() else ""
			if not club_id:
				scan_complete = True
			transport = club_transport.get(club_id) or {}
			enriched.append(
				{
					"name": da.name,
					"status": da.status,
					"card_status": card_status,
					"delivery_note": dn_name,
					"delivery_notes": dn_list,
					"dn_docstatus": dn_docstatus,
					"all_dns_submitted": bool(dn_list and all_submitted),
					"has_draft_dns": bool(any_draft),
					"roll_count": roll_count,
					"item_count": item_count,
					"line_count": len(lines),
					"order_codes_label": ", ".join(codes),
					"customers_label": ", ".join(customers),
					"qty_total": total_qty,
					"creation": da.creation,
					"despatch_date": despatch_date,
					"queue_idx": queue_idx,
					"clubbing_sheet": club_id,
					"club_orders": club_orders,
					"scanned_total": scanned_total,
					"scan_line_total": line_total,
					"scan_complete": scan_complete,
					"vehicle_no": transport.get("vehicle_no") or "",
					"driver": transport.get("driver") or "",
					"driver_ph_no": transport.get("driver_ph_no") or "",
				}
			)
		pending = [a for a in enriched if a["status"] in ("Pending Approval", "Draft")]
		pending = _order_approval_cards(pending, name, "pending")
		approved_ready_dn = [
			a
			for a in enriched
			if a["status"] == "Approved"
			and (not a.get("delivery_note") or cint(a.get("dn_docstatus")) == 0)
		]
		despatched = [a for a in enriched if a.get("card_status") == "Despatched"]
		approved_on_card = [a for a in enriched if a["status"] == "Approved"]
		approved_on_card = _order_approval_cards(approved_on_card, name, "approved")
		out.append(
			{
				"company": name,
				"label": name,
				"pending_approvals": pending,
				"approved_ready_dn": approved_ready_dn,
				"approved_approvals": approved_on_card,
				"despatched_approvals": despatched,
				"despatch_history": enriched,
			}
		)
	return out


@frappe.whitelist()
def get_despatch_eligible_rows(
	board_kind=None,
	view_scope=None,
	date=None,
	week=None,
	month=None,
	unit=None,
	party_code=None,
	customer=None,
	from_company=None,
	board_slug=None,
	clubbing_sheet=None,
):
	"""Planning rows with movement Despatch and submitted SPR."""
	kwargs = _chart_fetch_kwargs(
		view_scope, date, week, month, board_kind, board_slug=board_slug
	)
	try:
		raw = get_color_chart_data(**kwargs)
	except frappe.PermissionError:
		raise
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_despatch_eligible_rows")
		raw = []
	rows = _parse_chart_rows(raw)
	club_filter = _cstr(clubbing_sheet).strip().lower()
	out = []
	for r in rows:
		mt = normalize_movement_type(r.get("movement_type"))
		if mt != MOVEMENT_DESPATCH:
			continue
		if not _row_matches_filters(r, party_code, customer, unit):
			continue
		if _transfer_row_unit_is_unassigned(r.get("unit")):
			continue
		spr = _cstr(r.get("spr_name"))
		spr_ds = cint(r.get("spr_docstatus") or 0)
		ptr = _cstr(r.get("itemName") or r.get("name"))
		despatch_status = _resolved_planning_row_despatch_status(
			ptr, _cstr(r.get("custom_despatch_status") or r.get("despatch_status"))
		)
		can = bool(spr and spr_ds == 1)
		block = ""
		if not spr:
			block = _("No SPR linked")
		elif spr_ds != 1:
			block = _("SPR must be submitted")
		elif _despatch_status_blocks_request(despatch_status):
			can = False
			block = despatch_status or _("Despatch approval in progress")
		out.append(
			{
				"planning_table_row": ptr,
				"planning_sheet": r.get("planningSheet"),
				"party_code": r.get("partyCode"),
				"customer_name": r.get("customer_name") or r.get("customer"),
				"item_code": r.get("itemCode"),
				"item_name": r.get("description"),
				"unit": r.get("unit"),
				"qty": flt(r.get("qty")),
				"spr_name": spr,
				"spr_docstatus": spr_ds,
				"can_despatch": can,
				"despatch_block_reason": block,
				"despatch_status": despatch_status,
				"movement_type": mt,
				"clubbing_sheet": "",
				"loading_sequence": "",
				"club_load_order": 0,
			}
		)

	club_map = _club_fields_for_ptrs([o["planning_table_row"] for o in out])
	filtered = []
	for o in out:
		info = club_map.get(_cstr(o["planning_table_row"])) or {}
		o["clubbing_sheet"] = info.get("clubbing_sheet") or ""
		o["loading_sequence"] = info.get("loading_sequence") or ""
		o["club_load_order"] = cint(info.get("club_load_order") or 0)
		o["despatch_customer"] = info.get("despatch_customer") or ""
		o["despatch_sales_order"] = info.get("despatch_sales_order") or ""
		# Prefer Despatch Customer display for logistics / DN grouping
		if o["despatch_customer"]:
			disp = frappe.db.get_value("Customer", o["despatch_customer"], "customer_name")
			o["customer_name"] = disp or o["despatch_customer"]
		if club_filter and club_filter not in _cstr(o["clubbing_sheet"]).lower():
			continue
		filtered.append(o)
	return filtered


@frappe.whitelist()
def get_despatch_spr_batches(spr_name=None, item_code=None, party_code=None, from_company=None):
	"""Batches from SPR produced rolls for despatch (direct SPR Item read first).

	Planning rows and SPR rolls for the same order often share the same item_code
	(e.g. F26187 / 1001030010241015). We read `tabShaft Production Run Item` directly
	so transfer-approval filters / item mismatches cannot hide valid rolls.
	"""
	spr_ids = _resolve_submitted_spr_ids(spr_name)
	if not spr_ids:
		return []

	pc = _cstr(party_code)
	ic = _cstr(item_code)
	reserved = _batches_reserved_on_despatch()
	seen = set()
	out = []

	def _append(bn, row_ic, row_name, qty, row_pc="", warehouse="", row=None):
		bn = _cstr(bn)
		if not bn or bn in seen or bn in reserved:
			return
		seen.add(bn)
		q = flt(qty or 0)
		if q <= 0:
			q = flt(
				frappe.db.sql(
					"select sum(actual_qty) from `tabStock Ledger Entry` where batch_no=%s and is_cancelled=0",
					bn,
				)[0][0]
				or 0
			)
		if q <= 0:
			q = flt(frappe.db.get_value("Batch", bn, "batch_qty") or 0) or 1.0
		spec = _roll_spec_dict(row or {}, _cstr(row_ic) or ic)
		out.append(
			{
				"batch_no": bn,
				"item_code": _cstr(row_ic) or ic,
				"item_name": _cstr(row_name)
				or (frappe.db.get_value("Item", _cstr(row_ic) or ic, "item_name") if (_cstr(row_ic) or ic) else ""),
				"qty": q,
				"available_qty": q,
				"net_weight": q,
				"party_code": _cstr(row_pc) or pc,
				"warehouse": warehouse or "",
				"source": "spr_item",
				**spec,
			}
		)

	placeholders = ", ".join(["%s"] * len(spr_ids))
	has_roll = frappe.db.has_column("Shaft Production Run Item", "roll_no")
	roll_col = ", roll_no" if has_roll else ""
	spec_cols = ""
	for col in ("quality", "color", "gsm", "width_inch", "meter_per_roll", "meter_roll"):
		if frappe.db.has_column("Shaft Production Run Item", col):
			spec_cols += f", {col}"
	rows = frappe.db.sql(
		f"""
		select parent, idx, batch_no, item_code, item_name, net_weight, gross_weight,
		       party_code, work_order{roll_col}{spec_cols}
		from `tabShaft Production Run Item`
		where parent in ({placeholders})
		order by parent asc, idx asc
		""",
		tuple(spr_ids),
		as_dict=1,
	) or []

	def _row_batch(r):
		bn = _cstr(r.get("batch_no"))
		if bn:
			return bn
		if has_roll:
			return _cstr(r.get("roll_no"))
		return ""

	# Match order code when provided; otherwise all rolls with a batch
	matched = []
	rest = []
	for r in rows:
		bn = _row_batch(r)
		if not bn:
			continue
		if pc and _cstr(r.get("party_code")) and _cstr(r.get("party_code")) != pc:
			rest.append((bn, r))
		else:
			matched.append((bn, r))

	candidates = matched if matched else rest

	# Prefer same item_code when provided
	if ic:
		same_item = [(bn, r) for bn, r in candidates if not _cstr(r.get("item_code")) or _cstr(r.get("item_code")) == ic]
		if same_item:
			candidates = same_item

	for bn, r in candidates:
		_append(
			bn,
			r.get("item_code"),
			r.get("item_name"),
			r.get("net_weight") or r.get("gross_weight") or 0,
			r.get("party_code"),
			row=r,
		)

	if out:
		return out

	# Shared helper (manufacture SE / Batch masters) — never exclude transferred for despatch
	fallback = (
		get_spr_produced_batches(
			spr_name=spr_name,
			item_code=ic or "",
			party_code=pc,
			from_company=from_company,
			exclude_transferred=0,
		)
		or []
	)
	for b in fallback:
		_append(
			b.get("batch_no"),
			b.get("item_code"),
			b.get("item_name"),
			b.get("qty") or b.get("available_qty") or 0,
			b.get("party_code"),
			b.get("warehouse"),
			row=b,
		)
	if out:
		return out

	# Stock for item codes on this SPR (covers rolls with blank batch_no but Batch stock exists)
	spr_items = sorted({_cstr(r.get("item_code")) for r in rows if _cstr(r.get("item_code"))})
	if ic and ic not in spr_items:
		spr_items.append(ic)
	for spr_ic in spr_items:
		for b in get_despatch_other_batches(spr_ic, from_company, pc) or []:
			_append(
				b.get("batch_no"),
				b.get("item_code") or spr_ic,
				b.get("item_name"),
				b.get("available_qty") or b.get("qty") or 0,
				pc,
				b.get("warehouse"),
			)
	return out


@frappe.whitelist()
def get_despatch_other_batches(item_code=None, from_company=None, party_code=None):
	"""All batches with stock for item in company warehouses (excluding reserved despatch batches)."""
	ic = _cstr(item_code)
	fc = _cstr(from_company)
	if not ic:
		return []
	reserved = _batches_reserved_on_despatch()
	wh_list = frappe.get_all("Warehouse", filters={"company": fc}, pluck="name") if fc else []
	if not wh_list:
		return []
	seen = set()
	out = []
	for sle in frappe.db.sql(
		"""
		select batch_no, item_code, sum(actual_qty) as qty, warehouse
		from `tabStock Ledger Entry`
		where is_cancelled = 0
		  and ifnull(batch_no,'') != ''
		  and item_code = %s
		  and warehouse in ({whs})
		group by batch_no, item_code, warehouse
		having sum(actual_qty) > 0.001
		order by batch_no asc
		limit 500
		""".format(whs=", ".join(["%s"] * len(wh_list))),
		tuple([ic] + wh_list),
		as_dict=1,
	):
		bn = _cstr(sle.get("batch_no"))
		if not bn or bn in seen or bn in reserved:
			continue
		seen.add(bn)
		out.append(
			{
				"batch_no": bn,
				"item_code": sle.item_code,
				"item_name": frappe.db.get_value("Item", ic, "item_name") or "",
				"qty": flt(sle.qty),
				"available_qty": flt(sle.qty),
				"warehouse": sle.warehouse,
				"party_code": party_code or "",
				"source": "other",
				**_roll_spec_dict({}, sle.item_code),
			}
		)
	return out


@frappe.whitelist()
def get_despatch_approval_roll_list(approval_name=None):
	"""Batches selected on Production Despatch (for Logistics Kanban View Rolls + print)."""
	name = _cstr(approval_name).strip()
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	meta = frappe.get_meta("Despatch Approval Line")
	fields = ["batch_no", "item_code", "qty", "party_code", "customer_name"]
	for f in ("quality", "color", "gsm", "width_inch", "net_weight", "meter_per_roll", "spr_name"):
		if meta.has_field(f):
			fields.append(f)
	lines = frappe.get_all(
		"Despatch Approval Line",
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
		# Prefer SPR produced row when quality/GSM/width still blank
		if (
			not spec.get("quality")
			or not spec.get("color")
			or not cint(spec.get("gsm") or 0)
			or flt(spec.get("width_inch") or 0) <= 0
		) and bn:
			spr_row = frappe.db.get_value(
				"Shaft Production Run Item",
				{"batch_no": bn},
				_spr_item_query_fields(),
				as_dict=True,
			)
			if spr_row:
				merged = dict(spr_row)
				for k in ("quality", "color", "gsm", "width_inch", "meter_per_roll", "meter_roll", "item_code"):
					if ln.get(k) not in (None, "", 0, 0.0):
						merged[k] = ln.get(k)
				spec = _roll_spec_dict(merged, merged.get("item_code") or ln.get("item_code"))
				if not flt(ln.get("qty")):
					ln["qty"] = flt(spr_row.get("net_weight") or spr_row.get("gross_weight") or 0)
		qty = flt(ln.get("qty") or ln.get("net_weight") or 0)
		rolls.append(
			{
				"batch_no": bn,
				"item_code": _cstr(ln.get("item_code")),
				"party_code": _cstr(ln.get("party_code")),
				"customer_name": _cstr(ln.get("customer_name")),
				"net_weight": qty,
				"gross_weight": qty,
				"meter_per_roll": flt(spec.get("meter_per_roll") or 0),
				**spec,
			}
		)
	da = frappe.db.get_value(
		"Despatch Approval",
		name,
		["name", "custom_clubbing_sheet"] if _has_da_club_field() else ["name"],
		as_dict=True,
	) or {}
	return {
		"approval_name": name,
		"clubbing_sheet": _cstr(da.get("custom_clubbing_sheet") or ""),
		"rolls": rolls,
	}


@frappe.whitelist()
def create_despatch_approval_request(from_company=None, lines=None):
	fc = _cstr(from_company)
	if not fc or not frappe.db.exists("Company", fc):
		frappe.throw(_("From company is required."))
	parsed = json.loads(lines) if isinstance(lines, str) else (lines or [])
	if not parsed:
		frappe.throw(_("Select at least one line with batch and qty."))

	doc = frappe.new_doc("Despatch Approval")
	doc.from_company = fc
	doc.status = "Pending Approval"
	doc.requested_by = frappe.session.user
	if _has_da_lane_date_field():
		doc.custom_despatch_lane_date = getdate()

	club_ids = set()
	for line in parsed:
		ptr = _cstr(line.get("planning_table_row"))
		if ptr and not ptr.startswith("sprgrp:"):
			st = _resolved_planning_row_despatch_status(ptr, "")
			if _despatch_status_blocks_request(st):
				frappe.throw(
					_("Row {0} already has despatch status: {1}. Reject the approval to request again.").format(
						ptr, st
					)
				)
		bn = _cstr(line.get("batch_no"))
		if not bn:
			frappe.throw(_("Batch is required for each line."))
		spr = _primary_submitted_spr_for_batch(line.get("spr_name"), bn)
		if line.get("spr_name") and not spr:
			frappe.throw(_("SPR not submitted for row {0}.").format(ptr or bn))
		qty = flt(line.get("qty") or 0)
		nw = flt(line.get("net_weight") or qty)
		if qty <= 0:
			qty = nw or 1.0

		club = _cstr(line.get("clubbing_sheet") or line.get("custom_clubbing_sheet"))
		seq = _cstr(line.get("loading_sequence") or line.get("custom_loading_sequence"))
		load_order = cint(line.get("club_load_order") or line.get("custom_club_load_order") or 0)
		if not club and ptr and not ptr.startswith("sprgrp:") and _has_pt_club_fields():
			info = _club_fields_for_ptrs([ptr]).get(ptr) or {}
			club = info.get("clubbing_sheet") or ""
			seq = seq or info.get("loading_sequence") or ""
			load_order = load_order or cint(info.get("club_load_order") or 0)
		if club:
			club_ids.add(club)

		row = {
			"planning_table_row": ptr,
			"planning_sheet": line.get("planning_sheet"),
			"party_code": line.get("party_code"),
			"customer_name": line.get("customer_name"),
			"item_code": line.get("item_code"),
			"unit": line.get("unit"),
			"spr_name": spr,
			"batch_no": bn,
			"net_weight": nw,
			"qty": qty,
			"uom": line.get("uom") or "Kg",
		}
		if frappe.db.has_column("Despatch Approval Line", "custom_loading_sequence"):
			row["custom_loading_sequence"] = seq
		if frappe.db.has_column("Despatch Approval Line", "custom_club_load_order"):
			row["custom_club_load_order"] = load_order
		dc = _cstr(line.get("despatch_customer") or line.get("custom_despatch_customer"))
		dso = _cstr(line.get("despatch_sales_order") or line.get("custom_despatch_sales_order"))
		if not dc and ptr and not ptr.startswith("sprgrp:") and _has_pt_club_fields():
			info = _club_fields_for_ptrs([ptr]).get(ptr) or {}
			dc = info.get("despatch_customer") or ""
			dso = dso or info.get("despatch_sales_order") or ""
		if frappe.db.has_column("Despatch Approval Line", "custom_despatch_customer"):
			row["custom_despatch_customer"] = dc
		if frappe.db.has_column("Despatch Approval Line", "custom_despatch_sales_order"):
			row["custom_despatch_sales_order"] = dso
		if dc:
			disp = frappe.db.get_value("Customer", dc, "customer_name")
			row["customer_name"] = disp or dc
		doc.append("lines", row)

	if _has_da_club_field() and len(club_ids) == 1:
		doc.custom_clubbing_sheet = next(iter(club_ids))

	doc.insert(ignore_permissions=True)
	_stamp_planning_rows_for_despatch_request(doc.name)
	frappe.db.commit()
	return {"ok": True, "name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_despatch_approvals(status_filter=None, limit=200, from_date=None, to_date=None, order_code=None):
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
		"Despatch Approval",
		filters=filters,
		fields=[
			"name",
			"from_company",
			"status",
			"owner",
			"modified",
			"delivery_note",
			"requested_by",
			"creation",
		],
		order_by="modified desc",
		limit_page_length=cint(limit) or 200,
	)
	if not rows:
		return rows
	names = [r.name for r in rows]
	line_rows = frappe.get_all(
		"Despatch Approval Line",
		filters={"parent": ["in", names]},
		fields=["parent", "party_code", "customer_name"],
		limit_page_length=0,
	) or []
	codes_by_parent = {}
	customers_by_parent = {}
	for ln in line_rows:
		pc = _cstr(ln.get("party_code")).strip()
		if pc:
			codes_by_parent.setdefault(ln.get("parent"), [])
			if pc not in codes_by_parent[ln.get("parent")]:
				codes_by_parent[ln.get("parent")].append(pc)
		cn = _cstr(ln.get("customer_name")).strip()
		if cn:
			customers_by_parent.setdefault(ln.get("parent"), [])
			if cn not in customers_by_parent[ln.get("parent")]:
				customers_by_parent[ln.get("parent")].append(cn)
	oc = _cstr(order_code).strip().lower()
	out = []
	for row in rows:
		codes = codes_by_parent.get(row.name, [])
		row["order_codes"] = codes
		row["order_codes_label"] = ", ".join(codes)
		row["customers_label"] = ", ".join(customers_by_parent.get(row.name, []))
		row["despatch_date"] = _despatch_lane_date(row)
		dn_name = _cstr(row.get("delivery_note"))
		row["dn_docstatus"] = 0
		if dn_name and frappe.db.exists("Delivery Note", dn_name):
			row["dn_docstatus"] = cint(frappe.db.get_value("Delivery Note", dn_name, "docstatus") or 0)
		if row.get("status") == "Approved" and row["dn_docstatus"] >= 1:
			row["card_status"] = "Despatched"
		elif row.get("status") == "Approved" and dn_name:
			row["card_status"] = "Draft DN"
		else:
			row["card_status"] = row.get("status")
		if oc and not any(oc in _cstr(c).lower() for c in codes):
			continue
		out.append(row)
	return out


@frappe.whitelist()
def get_despatch_approval_detail(name=None):
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	doc = frappe.get_doc("Despatch Approval", name).as_dict()
	dn_name = _cstr(doc.get("delivery_note"))
	doc["dn_docstatus"] = 0
	if dn_name and frappe.db.exists("Delivery Note", dn_name):
		doc["dn_docstatus"] = cint(frappe.db.get_value("Delivery Note", dn_name, "docstatus") or 0)
	lines = doc.get("lines") or []
	codes = []
	customers = []
	items = set()
	batches = set()
	for ln in lines:
		pc = _cstr(ln.get("party_code"))
		if pc and pc not in codes:
			codes.append(pc)
		cn = _cstr(ln.get("customer_name"))
		if cn and cn not in customers:
			customers.append(cn)
		ic = _cstr(ln.get("item_code"))
		if ic:
			items.add(ic)
		bn = _cstr(ln.get("batch_no"))
		if bn:
			batches.add(bn)
	doc["order_codes_label"] = ", ".join(codes)
	doc["customers_label"] = ", ".join(customers)
	doc["item_count"] = len(items) or len(lines)
	doc["roll_count"] = len(batches) or len(lines)
	return doc


@frappe.whitelist()
def approve_despatch_approval(name=None):
	if not _user_can_approve_despatch():
		frappe.throw(_("You do not have permission to approve despatch."), frappe.PermissionError)
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	if da.status == "Approved":
		return {"ok": True, "name": name, "delivery_note": da.delivery_note}
	if da.status == "Rejected":
		frappe.throw(_("This despatch was rejected."))
	if _has_da_lane_date_field() and not _cstr(getattr(da, "custom_despatch_lane_date", None)):
		da.custom_despatch_lane_date = getdate()
	da.status = "Approved"
	da.approved_by = frappe.session.user
	da.save(ignore_permissions=True)
	_stamp_planning_rows_for_despatch_request(da.name)
	frappe.db.commit()
	return {"ok": True, "name": name, "delivery_note": da.delivery_note}


@frappe.whitelist()
def reject_despatch_approval(name=None):
	if not _user_can_approve_despatch():
		frappe.throw(_("You do not have permission to reject despatch."), frappe.PermissionError)
	da = frappe.get_doc("Despatch Approval", name)
	da.status = "Rejected"
	da.approved_by = frappe.session.user
	da.save(ignore_permissions=True)
	_stamp_planning_rows_for_despatch_request(da.name)
	frappe.db.commit()
	return {"ok": True, "name": name}


@frappe.whitelist()
def reorder_despatch_pending_queue(from_company=None, approval_names=None):
	"""Persist pending despatch approval priority (logistics kanban drag-and-drop)."""
	fc = _cstr(from_company)
	if not fc:
		frappe.throw(_("From company is required."))
	if isinstance(approval_names, str):
		try:
			approval_names = json.loads(approval_names)
		except Exception:
			approval_names = [s.strip() for s in approval_names.split(",") if s.strip()]
	updated = _apply_despatch_pending_queue(fc, approval_names or [])
	return {"updated": updated, "from_company": fc}


@frappe.whitelist()
def save_despatch_pending_arrangement(from_company=None, approval_names=None):
	"""Save pending despatch queue order and snapshot previous order for restore."""
	fc = _cstr(from_company)
	if not fc:
		frappe.throw(_("From company is required."))
	if isinstance(approval_names, str):
		try:
			approval_names = json.loads(approval_names)
		except Exception:
			approval_names = [s.strip() for s in approval_names.split(",") if s.strip()]
	names = [n for n in (approval_names or []) if _cstr(n)]
	current = _pending_approval_names_sorted(fc)
	if current:
		_save_despatch_pending_arrangement_history(
			fc,
			{"approval_names": current, "saved_at": _cstr(now_datetime())},
		)
	updated = _apply_despatch_pending_queue(fc, names)
	return {"updated": updated, "from_company": fc, "approval_names": names}


@frappe.whitelist()
def restore_despatch_pending_arrangement(from_company=None):
	"""Restore previous pending despatch queue snapshot."""
	fc = _cstr(from_company)
	if not fc:
		frappe.throw(_("From company is required."))
	history = _load_despatch_pending_arrangement_history(fc)
	if not history:
		frappe.throw(_("No previous arrangement to restore."))
	last = history.pop()
	frappe.defaults.set_global_default(
		_despatch_pending_arrangement_history_key(fc),
		json.dumps(history),
	)
	names = last.get("approval_names") or []
	updated = _apply_despatch_pending_queue(fc, names)
	return {"updated": updated, "from_company": fc, "approval_names": names}


def _despatch_approved_arrangement_history_key(from_company):
	return f"{DESPATCH_APPROVED_ARRANGEMENT_HISTORY_KEY}::{_cstr(from_company)}"


def _load_despatch_approved_arrangement_history(from_company):
	raw = frappe.defaults.get_global_default(_despatch_approved_arrangement_history_key(from_company)) or "[]"
	try:
		arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
		return arr if isinstance(arr, list) else []
	except Exception:
		return []


def _save_despatch_approved_arrangement_history(from_company, snapshot):
	history = _load_despatch_approved_arrangement_history(from_company)
	history.append(snapshot)
	if len(history) > 20:
		history = history[-20:]
	frappe.defaults.set_global_default(
		_despatch_approved_arrangement_history_key(from_company),
		json.dumps(history),
	)


@frappe.whitelist()
def save_despatch_approved_arrangement(from_company=None, approval_names=None):
	"""Save approved despatch delivery queue order."""
	fc = _cstr(from_company)
	if not fc:
		frappe.throw(_("From company is required."))
	if isinstance(approval_names, str):
		try:
			approval_names = json.loads(approval_names)
		except Exception:
			approval_names = [s.strip() for s in approval_names.split(",") if s.strip()]
	names = [n for n in (approval_names or []) if _cstr(n)]
	current = _approved_approval_names_sorted(fc)
	if current:
		_save_despatch_approved_arrangement_history(
			fc,
			{"approval_names": current, "saved_at": _cstr(now_datetime())},
		)
	updated = _apply_despatch_approved_queue(fc, names)
	return {"updated": updated, "from_company": fc, "approval_names": names}


@frappe.whitelist()
def restore_despatch_approved_arrangement(from_company=None):
	fc = _cstr(from_company)
	if not fc:
		frappe.throw(_("From company is required."))
	history = _load_despatch_approved_arrangement_history(fc)
	if not history:
		frappe.throw(_("No previous arrangement to restore."))
	last = history.pop()
	frappe.defaults.set_global_default(
		_despatch_approved_arrangement_history_key(fc),
		json.dumps(history),
	)
	names = last.get("approval_names") or []
	updated = _apply_despatch_approved_queue(fc, names)
	return {"updated": updated, "from_company": fc, "approval_names": names}


@frappe.whitelist()
def reorder_despatch_approval_lines(name=None, line_names=None):
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	if da.status not in ("Pending Approval", "Draft"):
		frappe.throw(_("Only pending despatch can be reordered."))
	if isinstance(line_names, str):
		try:
			line_names = json.loads(line_names)
		except Exception:
			line_names = [x.strip() for x in line_names.split(",") if x.strip()]
	names = [n for n in (line_names or []) if _cstr(n)]
	if not names:
		return {"ok": True}
	by_name = {ln.name: ln for ln in (da.lines or [])}
	new_lines = []
	for nm in names:
		if nm in by_name:
			new_lines.append(by_name[nm])
	for ln in da.lines or []:
		if ln.name not in names:
			new_lines.append(ln)
	for i, ln in enumerate(new_lines, start=1):
		ln.idx = i
	da.lines = new_lines
	da.save(ignore_permissions=True)
	frappe.db.commit()
	return {"ok": True, "name": name}


@frappe.whitelist()
def prepare_delivery_note_from_despatch_approval(name=None):
	"""Return unsaved Delivery Note doc for desk form (operator saves manually)."""
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	if da.status != "Approved":
		frappe.throw(_("Despatch must be approved before creating Delivery Note."))
	active_dns = _sync_despatch_delivery_notes(da, persist=True)
	if active_dns:
		primary = active_dns[0]
		ds = cint(frappe.db.get_value("Delivery Note", primary, "docstatus") or 0)
		return {
			"ok": True,
			"mode": "existing",
			"delivery_note": primary,
			"docstatus": ds,
		}
	from production_entry.production_planning.despatch_delivery import build_delivery_note_from_despatch

	dn = build_delivery_note_from_despatch(da)
	return {"ok": True, "mode": "new", "doc": dn.as_dict(), "despatch_approval": da.name}


@frappe.whitelist()
def link_delivery_note_to_despatch(despatch_approval=None, delivery_note=None):
	"""Link saved Delivery Note back to Despatch Approval."""
	da_name = _cstr(despatch_approval)
	dn_name = _cstr(delivery_note)
	if not da_name or not frappe.db.exists("Despatch Approval", da_name):
		frappe.throw(_("Despatch Approval not found."))
	if not dn_name or not frappe.db.exists("Delivery Note", dn_name):
		frappe.throw(_("Delivery Note not found."))
	cur = _cstr(frappe.db.get_value("Despatch Approval", da_name, "delivery_note"))
	if cur and cur != dn_name and frappe.db.exists("Delivery Note", cur):
		frappe.throw(_("Despatch Approval already linked to {0}.").format(cur))
	frappe.db.set_value("Despatch Approval", da_name, "delivery_note", dn_name, update_modified=False)
	if frappe.db.has_column("Delivery Note", "custom_despatch_approval"):
		frappe.db.set_value("Delivery Note", dn_name, "custom_despatch_approval", da_name)
	frappe.db.commit()
	return {"ok": True, "despatch_approval": da_name, "delivery_note": dn_name}


@frappe.whitelist()
def get_delivery_note_item_rolls(delivery_note=None, item_code=None, child_name=None):
	"""Roll detail list for one Delivery Note item row (JSON or Despatch Approval fallback)."""
	dn_name = _cstr(delivery_note).strip()
	if not dn_name or not frappe.db.exists("Delivery Note", dn_name):
		frappe.throw(_("Delivery Note not found."))

	from production_entry.production_planning.despatch_delivery import (
		_line_get,
		_refresh_rolls_from_batch,
		_roll_detail_from_batch,
	)

	dn = frappe.get_doc("Delivery Note", dn_name)
	target_item = _cstr(item_code)
	dn_item = None
	if child_name:
		for row in dn.items or []:
			if row.name == child_name:
				dn_item = row
				break
	if not dn_item and target_item:
		for row in dn.items or []:
			if _cstr(row.item_code) == target_item:
				dn_item = row
				break

	rolls = []
	if dn_item:
		target_item = target_item or _cstr(dn_item.item_code)
		raw = _cstr(getattr(dn_item, "custom_despatch_rolls_json", None) or "")
		if raw:
			try:
				parsed = json.loads(raw)
				if isinstance(parsed, list):
					rolls = parsed
			except Exception:
				rolls = []

		# Fallback: rebuild rolls from Serial and Batch Bundle entries
		if not rolls:
			bundle = _cstr(getattr(dn_item, "serial_and_batch_bundle", None) or "")
			if bundle and frappe.db.exists("Serial and Batch Bundle", bundle):
				entries = frappe.get_all(
					"Serial and Batch Entry",
					filters={"parent": bundle},
					fields=["batch_no", "qty"],
					order_by="idx asc",
				)
				for e in entries:
					bn = _cstr(e.get("batch_no"))
					if not bn:
						continue
					rolls.append(_roll_detail_from_batch(bn, abs(flt(e.get("qty")))))

	if not rolls:
		da_name = _cstr(getattr(dn, "custom_despatch_approval", None) or "")
		if not da_name:
			linked = _despatch_approvals_referencing_dn(dn_name)
			da_name = linked[0] if linked else ""
		if da_name and frappe.db.exists("Despatch Approval", da_name):
			da = frappe.get_doc("Despatch Approval", da_name)
			for ln in da.lines or []:
				if target_item and _cstr(ln.item_code) != target_item:
					continue
				bn = _cstr(_line_get(ln, "batch_no"))
				qty = flt(_line_get(ln, "qty")) or flt(_line_get(ln, "net_weight"))
				if bn:
					rolls.append(_roll_detail_from_batch(bn, qty))

	# Always re-read quality/color/gsm/width/meters from Batch master
	rolls = _refresh_rolls_from_batch(rolls)

	total_kg = sum(flt(r.get("net_weight")) for r in rolls)
	return {
		"rolls": rolls,
		"item_code": target_item,
		"total_kg": total_kg,
		"roll_count": len(rolls),
		"delivery_note": dn_name,
		"docstatus": cint(dn.docstatus),
	}


@frappe.whitelist()
def move_despatch_approval_to_date(name=None, lane_date=None):
	"""Move approved despatch to another logistics lane date (like Production Table pull)."""
	da_name = _cstr(name).strip()
	if not da_name or not frappe.db.exists("Despatch Approval", da_name):
		frappe.throw(_("Despatch Approval not found."))
	if not lane_date:
		frappe.throw(_("Despatch date is required."))
	if not _has_da_lane_date_field():
		frappe.throw(_("Despatch lane date field missing — run bench migrate."))
	_set_despatch_lane_date(da_name, lane_date, update_modified=False)
	frappe.db.commit()
	return {"ok": True, "name": da_name, "lane_date": str(getdate(lane_date))}


@frappe.whitelist()
def get_despatch_approvals_for_delivery_note(delivery_note=None):
	"""List Despatch Approvals linked to this Delivery Note (read-only)."""
	dn_name = _cstr(delivery_note).strip()
	if not dn_name:
		return {"approvals": []}
	return {"approvals": _despatch_approvals_referencing_dn(dn_name)}


@frappe.whitelist()
def unlink_delivery_note_from_despatch(delivery_note=None, despatch_approval=None):
	"""Unlink Delivery Note from one Despatch Approval without deleting the DN."""
	dn_name = _cstr(delivery_note).strip()
	da_name = _cstr(despatch_approval).strip()
	if not dn_name:
		frappe.throw(_("Delivery Note is required."))
	if not da_name:
		frappe.throw(_("Despatch Approval is required."))
	if not _remove_dn_from_despatch_approval(da_name, dn_name):
		frappe.throw(
			_("Delivery Note {0} is not linked to Despatch Approval {1}.").format(dn_name, da_name)
		)
	frappe.db.commit()
	return {"ok": True, "unlinked": [da_name], "delivery_note": dn_name}


@frappe.whitelist()
def delete_draft_delivery_note(delivery_note=None, despatch_approval=None):
	"""Unlink then delete one draft DN that belongs to this Despatch Approval only."""
	dn_name = _cstr(delivery_note).strip()
	da_name = _cstr(despatch_approval).strip()
	if not dn_name or not frappe.db.exists("Delivery Note", dn_name):
		frappe.throw(_("Delivery Note not found."))
	if not da_name or not frappe.db.exists("Despatch Approval", da_name):
		frappe.throw(_("Despatch Approval is required."))
	ds = cint(frappe.db.get_value("Delivery Note", dn_name, "docstatus") or 0)
	if ds != 0:
		frappe.throw(_("Only draft Delivery Notes can be deleted. Cancel submitted notes instead."))

	da = frappe.get_doc("Despatch Approval", da_name)
	linked = _sync_despatch_delivery_notes(da, persist=False)
	if dn_name not in linked:
		frappe.throw(
			_("Delivery Note {0} is not linked to Despatch Approval {1}.").format(dn_name, da_name)
		)

	_remove_dn_from_despatch_approval(da_name, dn_name)
	frappe.delete_doc("Delivery Note", dn_name, ignore_permissions=True)
	frappe.db.commit()
	return {
		"ok": True,
		"deleted": dn_name,
		"despatch_approval": da_name,
		"clubbing_sheet": _cstr(getattr(da, "custom_clubbing_sheet", None) or ""),
	}


@frappe.whitelist()
def create_delivery_note_from_despatch_approval(name=None):
	"""Legacy alias — opens existing DN or returns unsaved doc (no auto-insert)."""
	return prepare_delivery_note_from_despatch_approval(name)


@frappe.whitelist()
def get_despatch_approval_club_detail(name=None):
	"""Club scan progress for one Despatch Approval."""
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	club = _cstr(getattr(da, "custom_clubbing_sheet", None) or "")
	orders = {}
	scanned_total = 0
	line_total = 0
	for ln in da.lines or []:
		pc = _cstr(ln.party_code) or _("(no order)")
		om = orders.setdefault(
			pc,
			{
				"party_code": pc,
				"customer_name": _cstr(ln.customer_name),
				"loading_sequence": _cstr(getattr(ln, "custom_loading_sequence", None) or ""),
				"club_load_order": cint(getattr(ln, "custom_club_load_order", None) or 0),
				"total": 0,
				"scanned": 0,
				"batches": [],
			},
		)
		line_total += 1
		sc = cint(getattr(ln, "custom_scanned", None) or 0)
		if sc:
			scanned_total += 1
			om["scanned"] += 1
		om["total"] += 1
		om["batches"].append(
			{
				"line_name": ln.name,
				"batch_no": _cstr(ln.batch_no),
				"scanned": sc,
				"qty": flt(ln.qty),
			}
		)
		if not om["loading_sequence"]:
			om["loading_sequence"] = _cstr(getattr(ln, "custom_loading_sequence", None) or "")
		if not om["club_load_order"]:
			om["club_load_order"] = cint(getattr(ln, "custom_club_load_order", None) or 0)

	club_orders = sorted(
		orders.values(),
		key=lambda o: (cint(o.get("club_load_order") or 0) or 9999, _cstr(o.get("party_code"))),
	)
	# Active = first incomplete order
	active = ""
	for o in club_orders:
		if cint(o.get("scanned") or 0) < cint(o.get("total") or 0):
			active = o["party_code"]
			break

	dn_list = _sync_despatch_delivery_notes(da, persist=True)

	return {
		"ok": True,
		"name": da.name,
		"status": da.status,
		"clubbing_sheet": club,
		"club_orders": club_orders,
		"active_party_code": active,
		"scanned_total": scanned_total,
		"scan_line_total": line_total,
		"scan_complete": line_total > 0 and scanned_total >= line_total,
		"delivery_notes": dn_list,
		"delivery_note": dn_list[0] if dn_list else "",
	}


def _despatch_roll_scan_parts(value):
	"""Split a roll barcode into (batch prefix, roll number).

	'JS-0107269/6' -> ('JS0107269', 6). Roll number is None for a batch-only scan.
	"""
	key = _batch_match_key(value)
	if not key:
		return "", None
	m = re.match(r"^(.*?)/(\d+)$", key)
	if not m:
		return key, None
	return m.group(1), int(m.group(2))


def _despatch_roll_scan_equal(line_batch, scanned):
	"""Exact roll-level match for despatch scanning.

	Each roll is its own line even when the batch series repeats, so
	JS-0107269/6 must never match JS-0107269/7 (same batch, different roll) or
	JS-0107268/6 (same roll number, different batch). Only scanner normalisation
	(prefix fixes, punctuation, /6 vs /06) is tolerated — never fuzzy distance.
	"""
	lb = _cstr(line_batch).strip()
	sc = _cstr(scanned).strip()
	if not lb or not sc:
		return False
	if lb == sc:
		return True
	lk, sk = _batch_match_key(lb), _batch_match_key(sc)
	if not lk or not sk:
		return False
	if lk == sk:
		return True
	line_prefix, line_roll = _despatch_roll_scan_parts(lb)
	scan_prefix, scan_roll = _despatch_roll_scan_parts(sc)
	if line_roll is None or scan_roll is None:
		return False
	return line_prefix == scan_prefix and line_roll == scan_roll


def _despatch_find_scan_match(lines, barcode, unscanned_only=False):
	"""Resolve a scanned barcode to one despatch line.

	Returns (line, ambiguous_lines). A batch-only scan (no roll number) matches
	just one remaining roll of that batch; otherwise it is reported ambiguous so
	the operator scans the full roll number.
	"""
	pool = [
		ln
		for ln in (lines or [])
		if not unscanned_only or not cint(getattr(ln, "custom_scanned", None) or 0)
	]
	for ln in pool:
		if _despatch_roll_scan_equal(_cstr(ln.batch_no), barcode):
			return ln, []

	scan_prefix, scan_roll = _despatch_roll_scan_parts(barcode)
	if scan_roll is None and scan_prefix:
		candidates = [
			ln
			for ln in pool
			if _despatch_roll_scan_parts(_cstr(ln.batch_no))[0] == scan_prefix
		]
		if len(candidates) == 1:
			return candidates[0], []
		if len(candidates) > 1:
			return None, candidates
	return None, []


@frappe.whitelist()
def record_despatch_club_scan(name=None, barcode=None):
	"""Mark one approved batch as scanned (order-by-order for club cards)."""
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	bc = _cstr(barcode).strip()
	if not bc:
		frappe.throw(_("Scan a batch barcode."))
	if not _has_dal_scan_field():
		frappe.throw(_("Scan field missing — run bench migrate (custom_scanned)."))

	da = frappe.get_doc("Despatch Approval", name)
	if da.status != "Approved":
		frappe.throw(_("Despatch must be Approved before scanning."))

	# Determine active order (first incomplete by load order)
	orders = {}
	for ln in da.lines or []:
		pc = _cstr(ln.party_code) or ""
		om = orders.setdefault(
			pc,
			{"load_order": cint(getattr(ln, "custom_club_load_order", None) or 0), "lines": []},
		)
		om["lines"].append(ln)
		if not om["load_order"]:
			om["load_order"] = cint(getattr(ln, "custom_club_load_order", None) or 0)

	ordered_pcs = sorted(orders.keys(), key=lambda p: (orders[p]["load_order"] or 9999, p))
	active_pc = None
	for pc in ordered_pcs:
		lines = orders[pc]["lines"]
		if any(not cint(getattr(ln, "custom_scanned", None) or 0) for ln in lines):
			active_pc = pc
			break
	if active_pc is None:
		return {"ok": True, "already_complete": True, "message": _("All rolls already scanned.")}

	active_lines = orders[active_pc]["lines"]
	match, ambiguous = _despatch_find_scan_match(active_lines, bc, unscanned_only=True)
	if not match and ambiguous:
		frappe.throw(
			_("Batch {0} has {1} rolls pending. Scan the full roll number (e.g. {2}).").format(
				bc, len(ambiguous), _cstr(ambiguous[0].batch_no)
			)
		)

	if not match:
		# Same roll already scanned for this order?
		done, _amb = _despatch_find_scan_match(active_lines, bc)
		if done:
			return {
				"ok": True,
				"duplicate": True,
				"batch_no": _cstr(done.batch_no) or bc,
				"party_code": active_pc,
				"message": _("Roll {0} already scanned.").format(_cstr(done.batch_no) or bc),
			}
		# Wrong order / unknown roll
		for ln in da.lines or []:
			if _despatch_roll_scan_equal(_cstr(ln.batch_no), bc):
				frappe.throw(
					_("Roll {0} belongs to order {1}. Finish order {2} first.").format(
						bc, _cstr(ln.party_code), active_pc or "—"
					)
				)
		frappe.throw(_("Roll {0} is not on this despatch approval.").format(bc))

	bc = _cstr(match.batch_no) or bc
	match.custom_scanned = 1
	da.save(ignore_permissions=True)
	frappe.db.commit()
	detail = get_despatch_approval_club_detail(name)
	detail["batch_no"] = bc
	detail["party_code"] = active_pc
	detail["message"] = _("Scanned {0} for order {1}").format(bc, active_pc)
	return detail


@frappe.whitelist()
def create_draft_delivery_notes_from_despatch(name=None):
	"""Create draft Delivery Notes (one per order). Club cards require full scan first."""
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	if da.status != "Approved":
		frappe.throw(_("Despatch must be approved before creating Delivery Notes."))

	existing = _sync_despatch_delivery_notes(da, persist=True)
	if existing:
		return {"ok": True, "mode": "existing", "delivery_notes": existing}

	club = _cstr(getattr(da, "custom_clubbing_sheet", None) or "")
	if club and _has_dal_scan_field():
		unscanned = [ln for ln in (da.lines or []) if not cint(getattr(ln, "custom_scanned", None) or 0)]
		if unscanned:
			frappe.throw(_("Scan all rolls before creating Delivery Notes ({0} remaining).").format(len(unscanned)))

	from production_entry.production_planning.despatch_delivery import (
		create_draft_delivery_notes_by_order,
	)

	names = create_draft_delivery_notes_by_order(da)
	if not names:
		frappe.throw(_("No Delivery Notes created."))

	_save_delivery_notes_json(da.name, names)
	frappe.db.set_value("Despatch Approval", da.name, "delivery_note", names[0], update_modified=False)
	frappe.db.commit()
	return {"ok": True, "mode": "created", "delivery_notes": names}


@frappe.whitelist()
def submit_delivery_notes_from_despatch(name=None):
	"""Submit draft Delivery Notes linked to this Despatch Approval."""
	if not name or not frappe.db.exists("Despatch Approval", name):
		frappe.throw(_("Despatch Approval not found."))
	da = frappe.get_doc("Despatch Approval", name)
	dn_list = _sync_despatch_delivery_notes(da, persist=True)
	if not dn_list:
		frappe.throw(_("Create Delivery Notes first."))

	submitted = []
	for dn_name in dn_list:
		if not frappe.db.exists("Delivery Note", dn_name):
			continue
		dn = frappe.get_doc("Delivery Note", dn_name)
		if dn.docstatus == 0:
			dn.submit()
			submitted.append(dn_name)
		elif dn.docstatus == 1:
			submitted.append(dn_name)

	frappe.db.commit()
	# Refresh planning despatch status via existing path if any
	try:
		for ln in da.lines or []:
			ptr = _cstr(ln.planning_table_row)
			if ptr:
				update_planning_row_despatch_status(ptr)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "submit_delivery_notes_from_despatch status")

	return {"ok": True, "delivery_notes": dn_list, "submitted": submitted}
