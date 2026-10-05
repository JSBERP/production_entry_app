# -*- coding: utf-8 -*-
"""Loading timer and removed-roll log for despatch approvals and transfer stock entries."""

from __future__ import annotations

import json
from datetime import datetime

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import cint, flt, get_datetime, now_datetime

ALLOWED_DOCTYPES = ("Despatch Approval", "Stock Entry")

LOAD_VALUE_FIELDS = (
	"custom_load_status",
	"custom_load_started",
	"custom_load_pause_started",
	"custom_load_paused_seconds",
	"custom_load_elapsed_seconds",
	"custom_load_stopped",
)

_FIELD_DEFS = {
	"Despatch Approval": [
		{
			"fieldname": "custom_load_status",
			"label": "Loading Status",
			"fieldtype": "Data",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "status",
		},
		{
			"fieldname": "custom_load_started",
			"label": "Loading Started",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_status",
		},
		{
			"fieldname": "custom_load_pause_started",
			"label": "Loading Pause Started",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_started",
		},
		{
			"fieldname": "custom_load_paused_seconds",
			"label": "Loading Paused Seconds",
			"fieldtype": "Float",
			"hidden": 1,
			"read_only": 1,
			"default": "0",
			"insert_after": "custom_load_pause_started",
		},
		{
			"fieldname": "custom_load_elapsed_seconds",
			"label": "Loading Elapsed Seconds",
			"fieldtype": "Float",
			"hidden": 1,
			"read_only": 1,
			"default": "0",
			"insert_after": "custom_load_paused_seconds",
		},
		{
			"fieldname": "custom_load_stopped",
			"label": "Loading Stopped",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_elapsed_seconds",
		},
		{
			"fieldname": "custom_removed_rolls",
			"label": "Removed Rolls",
			"fieldtype": "Long Text",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_stopped",
		},
	],
	"Stock Entry": [
		{
			"fieldname": "custom_load_status",
			"label": "Loading Status",
			"fieldtype": "Data",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "stock_entry_type",
		},
		{
			"fieldname": "custom_load_started",
			"label": "Loading Started",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_status",
		},
		{
			"fieldname": "custom_load_pause_started",
			"label": "Loading Pause Started",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_started",
		},
		{
			"fieldname": "custom_load_paused_seconds",
			"label": "Loading Paused Seconds",
			"fieldtype": "Float",
			"hidden": 1,
			"read_only": 1,
			"default": "0",
			"insert_after": "custom_load_pause_started",
		},
		{
			"fieldname": "custom_load_elapsed_seconds",
			"label": "Loading Elapsed Seconds",
			"fieldtype": "Float",
			"hidden": 1,
			"read_only": 1,
			"default": "0",
			"insert_after": "custom_load_paused_seconds",
		},
		{
			"fieldname": "custom_load_stopped",
			"label": "Loading Stopped",
			"fieldtype": "Datetime",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_elapsed_seconds",
		},
		{
			"fieldname": "custom_removed_rolls",
			"label": "Removed Rolls",
			"fieldtype": "Long Text",
			"hidden": 1,
			"read_only": 1,
			"insert_after": "custom_load_stopped",
		},
	],
}


def _column_exists(doctype, fieldname):
	"""Read the live table, not Frappe's cached column list."""
	table = f"tab{doctype}"
	try:
		found = frappe.db.sql(
			"""
			select 1
			from information_schema.columns
			where table_schema = database()
			  and table_name = %s
			  and column_name = %s
			limit 1
			""",
			(table, fieldname),
		)
	except Exception:
		return bool(frappe.db.has_column(doctype, fieldname))
	return bool(found)


def _bust_column_cache(doctype):
	frappe.clear_cache(doctype=doctype)
	local_cols = getattr(frappe.local, "table_columns", None)
	if isinstance(local_cols, dict):
		local_cols.pop(doctype, None)
		local_cols.pop(f"tab{doctype}", None)
	try:
		frappe.cache().delete_value(f"table_columns::{doctype}")
	except Exception:
		pass


def ensure_loading_fields():
	"""Create hidden timer and removed-roll fields when they are not on the site yet."""
	pending = {}
	for dt, fields in _FIELD_DEFS.items():
		if not frappe.db.exists("DocType", dt):
			continue
		missing = [f for f in fields if not _column_exists(dt, f["fieldname"])]
		if missing:
			pending[dt] = missing
	if pending:
		try:
			create_custom_fields(pending, ignore_validate=True, update=True)
			for dt in pending:
				frappe.db.updatedb(dt)
				_bust_column_cache(dt)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "ensure_loading_fields")
			return False
	return all(
		_column_exists(dt, f["fieldname"])
		for dt, fields in _FIELD_DEFS.items()
		if frappe.db.exists("DocType", dt)
		for f in fields
	)


def _as_dt(value):
	if not value:
		return None
	if isinstance(value, datetime):
		return value
	try:
		return get_datetime(value)
	except Exception:
		return None


def _elapsed_seconds(status, started, pause_started, paused_seconds, stored_elapsed, now=None):
	status = (status or "idle").strip().lower()
	paused_seconds = flt(paused_seconds)
	if status == "stopped":
		return max(int(round(flt(stored_elapsed))), 0)
	started = _as_dt(started)
	if status not in ("running", "paused") or not started:
		return 0
	if status == "paused":
		end = _as_dt(pause_started) or started
	else:
		end = now or now_datetime()
	return max(int(round((end - started).total_seconds() - paused_seconds)), 0)


def _idle_session():
	return {
		"status": "idle",
		"elapsed_seconds": 0,
		"started_at": "",
		"pause_started_at": "",
		"stopped_at": "",
		"paused_seconds": 0,
	}


def public_session_from_row(row, now=None):
	if not row:
		return _idle_session()
	status = (row.get("custom_load_status") or "idle").strip().lower() or "idle"
	if status not in ("idle", "running", "paused", "stopped"):
		status = "idle"
	now = now or now_datetime()
	started = row.get("custom_load_started")
	pause_started = row.get("custom_load_pause_started")
	stopped = row.get("custom_load_stopped")
	return {
		"status": status,
		"elapsed_seconds": _elapsed_seconds(
			status,
			started,
			pause_started,
			row.get("custom_load_paused_seconds"),
			row.get("custom_load_elapsed_seconds"),
			now,
		),
		"started_at": str(started or ""),
		"pause_started_at": str(pause_started or ""),
		"stopped_at": str(stopped or ""),
		"paused_seconds": flt(row.get("custom_load_paused_seconds")),
	}


def _require(doctype, name, perm="read"):
	doctype = (doctype or "").strip()
	name = (name or "").strip()
	if doctype not in ALLOWED_DOCTYPES:
		frappe.throw(_("Loading time is only available on despatch and transfer."))
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} not found.").format(doctype))
	if not frappe.has_permission(doctype, perm, name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return doctype, name


def _read_row(doctype, name):
	fields = ["name"] + [fn for fn in LOAD_VALUE_FIELDS if frappe.db.has_column(doctype, fn)]
	row = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
	return row


def _write(doctype, name, updates):
	for fieldname, value in updates.items():
		if frappe.db.has_column(doctype, fieldname):
			frappe.db.set_value(doctype, name, fieldname, value, update_modified=False)


@frappe.whitelist()
def get_loading_session(doctype=None, name=None):
	doctype, name = _require(doctype, name, "read")
	ensure_loading_fields()
	return public_session_from_row(_read_row(doctype, name))


@frappe.whitelist()
def set_loading_session(doctype=None, name=None, action=None):
	doctype, name = _require(doctype, name, "write")
	action = (action or "").strip().lower()
	if action not in ("start", "pause", "continue", "stop"):
		frappe.throw(_("Unknown loading action."))
	if not ensure_loading_fields():
		frappe.throw(_("Loading timer fields are missing. Run bench migrate."))
	if doctype == "Stock Entry":
		if cint(frappe.db.get_value("Stock Entry", name, "docstatus") or 0) != 0:
			frappe.throw(_("Loading time can only change on a draft transfer."))
	now = now_datetime()
	row = _read_row(doctype, name)
	status = (row.get("custom_load_status") or "idle").strip().lower() or "idle"
	paused_seconds = flt(row.get("custom_load_paused_seconds"))

	if action == "start":
		if status == "running":
			return public_session_from_row(row, now)
		_write(
			doctype,
			name,
			{
				"custom_load_status": "running",
				"custom_load_started": now,
				"custom_load_pause_started": None,
				"custom_load_paused_seconds": 0,
				"custom_load_elapsed_seconds": 0,
				"custom_load_stopped": None,
			},
		)
	elif action == "pause":
		if status != "running":
			return public_session_from_row(row, now)
		elapsed = _elapsed_seconds(
			"running",
			row.get("custom_load_started"),
			None,
			paused_seconds,
			0,
			now,
		)
		_write(
			doctype,
			name,
			{
				"custom_load_status": "paused",
				"custom_load_pause_started": now,
				"custom_load_elapsed_seconds": elapsed,
			},
		)
	elif action == "continue":
		if status != "paused":
			return public_session_from_row(row, now)
		pause_started = _as_dt(row.get("custom_load_pause_started"))
		extra = (now - pause_started).total_seconds() if pause_started else 0
		_write(
			doctype,
			name,
			{
				"custom_load_status": "running",
				"custom_load_pause_started": None,
				"custom_load_paused_seconds": paused_seconds + max(extra, 0),
			},
		)
	elif action == "stop":
		if status not in ("running", "paused"):
			return public_session_from_row(row, now)
		elapsed = _elapsed_seconds(
			status,
			row.get("custom_load_started"),
			row.get("custom_load_pause_started"),
			paused_seconds,
			row.get("custom_load_elapsed_seconds"),
			now,
		)
		_write(
			doctype,
			name,
			{
				"custom_load_status": "stopped",
				"custom_load_stopped": now,
				"custom_load_elapsed_seconds": elapsed,
				"custom_load_pause_started": None,
			},
		)
	return public_session_from_row(_read_row(doctype, name), now_datetime())


def _parse_removed(raw):
	if not raw:
		return []
	if isinstance(raw, list):
		return raw
	try:
		rows = json.loads(raw)
	except Exception:
		return []
	return rows if isinstance(rows, list) else []


def _removed_roll_entry(batch_no, party_code="", item_code="", removed_by="", removed_at=""):
	return {
		"batch_no": (batch_no or "").strip(),
		"party_code": (party_code or "").strip(),
		"item_code": (item_code or "").strip(),
		"removed_by": (removed_by or "").strip(),
		"removed_at": (removed_at or "").strip(),
	}


def _insert_removed_roll_log(doctype, name, entry):
	if not frappe.db.exists("DocType", "Removed Roll Log"):
		frappe.throw(_("Removed roll log is not installed. Run bench migrate."))
	if not frappe.db.table_exists("Removed Roll Log"):
		frappe.throw(_("Removed roll log table is missing. Run bench migrate."))
	already = frappe.db.exists(
		"Removed Roll Log",
		{
			"reference_doctype": doctype,
			"reference_name": name,
			"batch_no": entry["batch_no"],
			"removed_at": entry["removed_at"],
		},
	)
	if already:
		return
	doc = frappe.get_doc(
		{
			"doctype": "Removed Roll Log",
			"reference_doctype": doctype,
			"reference_name": name,
			"batch_no": entry["batch_no"],
			"party_code": entry["party_code"],
			"item_code": entry["item_code"],
			"removed_by": entry["removed_by"],
			"removed_at": entry["removed_at"] or now_datetime(),
		}
	)
	doc.insert(ignore_permissions=True)


def log_removed_roll(doctype, name, batch_no, party_code="", item_code=""):
	"""Append one confirmed un-scan. Called from the scan APIs after the roll is cleared."""
	doctype = (doctype or "").strip()
	name = (name or "").strip()
	batch_no = (batch_no or "").strip()
	if doctype not in ALLOWED_DOCTYPES or not name or not batch_no:
		return
	entry = _removed_roll_entry(
		batch_no,
		party_code,
		item_code,
		frappe.session.user,
		str(now_datetime()),
	)
	_insert_removed_roll_log(doctype, name, entry)
	if not ensure_loading_fields() or not _column_exists(doctype, "custom_removed_rolls"):
		return
	rows = _parse_removed(frappe.db.get_value(doctype, name, "custom_removed_rolls"))
	rows.append(entry)
	payload = json.dumps(rows)
	frappe.db.set_value(doctype, name, "custom_removed_rolls", payload, update_modified=False)
	frappe.clear_document_cache(doctype, name)


def _roll_details_for_batch(batch_no, party_code="", item_code="", parent_doctype="", parent_name=""):
	"""Quality, colour, GSM, width, metres and weights for a removed roll."""
	from production_entry.production_planning.transfer_logistics import (
		_cstr,
		_roll_spec_dict,
		_spr_item_query_fields,
	)

	batch_no = _cstr(batch_no)
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
	line = None
	line_doctype = ""
	if parent_doctype == "Despatch Approval" and parent_name and frappe.db.table_exists("Despatch Approval Line"):
		line_doctype = "Despatch Approval Line"
	elif parent_doctype == "Stock Entry" and parent_name and frappe.db.table_exists("Stock Entry Detail"):
		line_doctype = "Stock Entry Detail"
	if line_doctype:
		fields = ["item_code", "qty"]
		meta = frappe.get_meta(line_doctype)
		for fn in (
			"party_code",
			"batch_no",
			"quality",
			"color",
			"gsm",
			"width_inch",
			"net_weight",
			"gross_weight",
			"meter_per_roll",
		):
			if meta.has_field(fn):
				fields.append(fn)
		line = frappe.db.get_value(
			line_doctype,
			{"parent": parent_name, "batch_no": batch_no},
			fields,
			as_dict=True,
		)
	if line:
		for key, val in line.items():
			if val not in (None, "", 0, 0.0) or key not in row:
				if val not in (None, ""):
					row[key] = val
	if item_code and not row.get("item_code"):
		row["item_code"] = item_code
	spec = _roll_spec_dict(row, row.get("item_code") or item_code)
	net = flt(row.get("net_weight") or row.get("qty") or 0)
	gross = flt(row.get("gross_weight") or net)
	return {
		"item_code": _cstr(row.get("item_code") or item_code),
		"party_code": _cstr(party_code or row.get("party_code")),
		"quality": spec.get("quality") or "",
		"color": spec.get("color") or "",
		"gsm": cint(spec.get("gsm") or 0),
		"width_inch": flt(spec.get("width_inch") or 0),
		"meter_per_roll": flt(spec.get("meter_per_roll") or 0),
		"net_weight": net,
		"gross_weight": gross,
	}


def _removed_roll_log_rows(doctype, name):
	if not frappe.db.exists("DocType", "Removed Roll Log") or not frappe.db.table_exists("Removed Roll Log"):
		return []
	return frappe.get_all(
		"Removed Roll Log",
		filters={"reference_doctype": doctype, "reference_name": name},
		fields=["batch_no", "party_code", "item_code", "removed_by", "removed_at"],
		order_by="removed_at asc",
		limit_page_length=0,
		ignore_permissions=True,
	) or []


@frappe.whitelist()
def get_removed_rolls(doctype=None, name=None):
	doctype, name = _require(doctype, name, "read")
	ensure_loading_fields()
	rows = []
	if _column_exists(doctype, "custom_removed_rolls"):
		rows.extend(_parse_removed(frappe.db.get_value(doctype, name, "custom_removed_rolls")))
	rows.extend(_removed_roll_log_rows(doctype, name))
	clean = []
	seen = set()
	for row in rows:
		if not isinstance(row, dict):
			continue
		entry = _removed_roll_entry(
			row.get("batch_no"),
			row.get("party_code"),
			row.get("item_code"),
			row.get("removed_by"),
			str(row.get("removed_at") or ""),
		)
		if not entry["batch_no"]:
			continue
		key = (entry["batch_no"], entry["removed_at"])
		if key in seen:
			continue
		seen.add(key)
		logged_party = entry.get("party_code") or ""
		entry.update(
			_roll_details_for_batch(
				entry["batch_no"],
				logged_party,
				entry.get("item_code"),
				doctype,
				name,
			)
		)
		if logged_party:
			entry["party_code"] = logged_party
		clean.append(entry)
	return {"rolls": clean}
