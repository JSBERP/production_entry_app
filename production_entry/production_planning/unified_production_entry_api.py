"""GSM Production Entry — additive APIs only. Does not modify Production Table core."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
	_cstr,
	_spr_exc_message,
	_count_combination_segments,
	_parse_combination_widths_inches,
	_resolve_wos_for_pp_job_row,
	_segment_weights_kg,
	_spr_count_roll_lines_for_job,
	_spr_count_roll_lines_for_job_width,
	_spr_is_real_roll_item_row,
	_spr_roll_has_production,
	_spr_job_keys_match,
	_spr_job_max_roll_lines,
	_spr_job_rows,
	_spr_net_kg_per_shaft_for_pp_line_width,
	compute_mix_roll_planned_qty_kg,
	delete_gsm_roll_line_from_spr,
	delete_gsm_bundle_packaging_from_spr,
	import_gsm_roll_lines_to_spr,
	parse_item_code,
	resolve_label_from_pp_doc,
	resolve_label_from_planning_sheet_doc,
	normalize_label_template_link,
	get_label_template_print_spec,
	save_gsm_roll_line_to_spr,
	spr_doc_is_mix_roll,
	spr_get_tolerance_violations,
	_gsm_serialize_spr_roll_lines_for_grid,
	_gsm_serialize_roll_waste_for_grid,
	_gsm_roll_suffix_from_batch_no,
	_spr_resolve_roll_line_specs_from_item_code,
	_spr_roll_starting_for_gsm_session,
	_spr_next_available_batch_no,
	_gsm_batch_available_kg,
	_gsm_patty_stock_from_batch_doc,
)
from production_entry.production_planning.scheduler_api import (
	create_item_spr,
	create_mix_item,
	create_mix_spr,
	get_current_shift,
	save_mix_roll_data,
)
from production_entry.production_planning.planning_doctypes import (
	get_mix_roll_unit_max_shaft_inches,
	normalize_planning_unit_for_select,
	validate_mix_shaft_width,
)


def _pick_value(row, keys, default=None):
	if not row:
		return default
	if isinstance(row, dict):
		getter = row.get
	else:
		getter = lambda k, d=None: getattr(row, k, d)
	for k in keys:
		v = getter(k, None)
		if v is not None and str(v).strip() != "":
			return v
	return default


def _meter_keys():
	return [
		"meter__roll_mtrs",
		"meter__roll",
		"meter_roll_mtrs",
		"meter_per_roll",
		"meter_roll",
		"roll_mtrs",
		"custom_meter_roll_mtrs",
		"custom_meter_per_roll",
		"custom_meterperroll",
		"meter_per_roll_mtrs",
		"roll",
		"meter",
		"length_per_roll",
		"length_roll",
		"length",
		"planned_length",
	]


def _planning_table_meter_select_fields():
	"""Planning Table meter columns vary by site — only SELECT fields that exist."""
	fields = ["name"]
	try:
		meta = frappe.get_meta("Planning Table")
		for fn in _meter_keys():
			if meta.has_field(fn):
				fields.append(fn)
	except Exception:
		for fn in _meter_keys():
			if frappe.db.has_column("Planning Table", fn):
				fields.append(fn)
	return list(dict.fromkeys(fields))


def _shaft_width_inch(shaft_row) -> float:
	w = flt(_pick_value(shaft_row, ["total_width", "combined_width", "width", "total_width_inches"], 0))
	if w > 0:
		return w
	comb = _cstr(_pick_value(shaft_row, ["combination", "combined_width", "shaft", "shaft_details"], ""))
	if comb and "+" not in comb:
		try:
			return flt(comb)
		except Exception:
			pass
	widths = _parse_combination_widths_inches(comb) if comb else []
	return flt(widths[0]) if widths else 0.0


def _shaft_gsm(shaft_row) -> int:
	try:
		return int(flt(_pick_value(shaft_row, ["gsm"], 0)))
	except Exception:
		return 0


def _spr_draft_uses_batch_prefix(spr_name: str, prefix: str) -> bool:
	prefix = _cstr(prefix).strip()
	spr_name = _cstr(spr_name).strip()
	if not prefix or not spr_name:
		return False
	try:
		return bool(
			frappe.db.exists(
				"Shaft Production Run Item",
				{"parent": spr_name, "batch_no": ["like", f"{prefix}/%"]},
			)
		)
	except Exception:
		return False


def _gsm_unit_key(unit: str) -> str:
	"""Unit 4 / UNIT 4 / unit-4 → UNIT 4 so SPR custom_unit matches the GSM header."""
	raw = _cstr(unit).strip()
	if not raw:
		return ""
	try:
		sel = _cstr(normalize_planning_unit_for_select(raw)).strip()
		if sel and sel.upper() != "UNASSIGNED":
			raw = sel
	except Exception:
		pass
	compact = " ".join(raw.upper().replace("-", " ").replace("_", " ").split())
	parts = compact.split()
	if len(parts) >= 2 and parts[0] == "UNIT" and parts[1].isdigit():
		return f"UNIT {parts[1]}"
	if len(parts) == 1 and parts[0].isdigit():
		return f"UNIT {parts[0]}"
	return compact


def _gsm_units_match(a, b) -> bool:
	ka, kb = _gsm_unit_key(a), _gsm_unit_key(b)
	return bool(ka and kb and ka == kb)


def _find_draft_spr_for_pp(pp_id: str, unit=None, run_date=None, shift=None, series_prefix=None) -> str | None:
	"""Return draft (docstatus=0) SPR for PP, preferring this GSM session's unit/date/shift."""
	if not pp_id:
		return None
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift) if shift else _cstr(shift).strip()
	run_d = getdate(run_date) if run_date else None
	prefix = _cstr(series_prefix).strip()

	def _lookup(filters: dict) -> str | None:
		row = frappe.db.get_value(
			"Shaft Production Run",
			filters,
			"name",
			order_by="modified desc",
		)
		return _cstr(row).strip() or None

	def _match_unit_among(filters: dict) -> str | None:
		rows = frappe.get_all(
			"Shaft Production Run",
			filters=filters,
			fields=["name", "custom_unit"],
			order_by="modified desc",
			limit=20,
		) or []
		for row in rows:
			if _gsm_units_match(row.get("custom_unit"), unit):
				return _cstr(row.get("name")).strip() or None
		return None

	if unit and run_d and shift:
		found = _lookup(
			{"production_plan": pp_id, "docstatus": 0, "custom_unit": unit, "run_date": run_d, "shift": shift}
		)
		if found:
			return found
		found = _match_unit_among(
			{"production_plan": pp_id, "docstatus": 0, "run_date": run_d, "shift": shift}
		)
		if found:
			return found
	# Same date+shift SPR tagged with a different unit (e.g. UNIT 3 vs Unit 4).
	if run_d and shift:
		found = _lookup({"production_plan": pp_id, "docstatus": 0, "run_date": run_d, "shift": shift})
		if found:
			return found
	if unit:
		found = _lookup({"production_plan": pp_id, "docstatus": 0, "custom_unit": unit})
		if found:
			return found
		found = _match_unit_among({"production_plan": pp_id, "docstatus": 0})
		if found:
			return found
	if prefix:
		rows = frappe.get_all(
			"Shaft Production Run",
			filters={"production_plan": pp_id, "docstatus": 0},
			pluck="name",
			order_by="modified desc",
			limit=20,
		) or []
		for sn in rows:
			if _spr_draft_uses_batch_prefix(sn, prefix):
				return sn
	if unit:
		# No same-unit draft and no shift-batch match — do not steal another unit's SPR.
		return None
	return _lookup({"production_plan": pp_id, "docstatus": 0})


def _find_spr_for_pp(pp_id: str, prefer_draft: bool = True) -> str | None:
	if not pp_id:
		return None
	sprs = frappe.get_all(
		"Shaft Production Run",
		filters={"production_plan": pp_id, "docstatus": ["<", 2]},
		fields=["name", "docstatus"],
		order_by="modified desc",
		limit=20,
	)
	if not sprs:
		return None
	if prefer_draft:
		for row in sprs:
			if cint(row.docstatus) == 0:
				return row.name
		return None
	return sprs[0].name


def _match_shaft_job_for_line(spr_doc, pp_id: str, gsm=None, width_inch=None, production_plan_item=None):
	target_gsm = cint(gsm) if gsm is not None else 0
	target_w = flt(width_inch) if width_inch is not None else 0.0
	ppi = _cstr(production_plan_item).strip()

	for sj in _spr_job_rows(spr_doc):
		if ppi and _cstr(getattr(sj, "production_plan_item", None)) == ppi:
			return sj
		jg = cint(getattr(sj, "gsm", 0) or 0)
		if target_gsm and jg and jg != target_gsm:
			continue
		comb = _cstr(getattr(sj, "combination", None))
		if target_w > 0:
			widths = _parse_combination_widths_inches(comb) if comb else []
			if widths:
				if any(abs(flt(w) - target_w) < 0.05 for w in widths):
					return sj
			elif abs(_shaft_width_inch(sj) - target_w) < 0.05:
				return sj
			elif comb and abs(flt(comb) - target_w) < 0.05:
				return sj

	if pp_id and target_gsm and target_w > 0:
		pp = frappe.get_doc("Production Plan", pp_id)
		pp_shafts = pp.get("custom_shaft_details") or pp.get("shaft_details") or []
		for idx, shaft in enumerate(pp_shafts, start=1):
			if target_gsm and _shaft_gsm(shaft) != target_gsm:
				continue
			if abs(_shaft_width_inch(shaft) - target_w) >= 0.05:
				comb = _cstr(_pick_value(shaft, ["combination", "combined_width", "shaft", "shaft_details"], ""))
				widths = _parse_combination_widths_inches(comb) if comb else []
				if not any(abs(flt(w) - target_w) < 0.05 for w in widths):
					continue
			for sj in _spr_job_rows(spr_doc):
				jid = _cstr(getattr(sj, "job_id", None) or getattr(sj, "job_no", None))
				if jid == str(idx):
					return sj
	return None


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_current_shift():
	return get_current_shift()


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def ping_gsm_desk_session():
	"""Always callable — Guest gets ok=0 instead of a whitelist error."""
	user = _cstr(frappe.session.user).strip()
	ok = bool(user) and user != "Guest"
	return {"ok": 1 if ok else 0, "user": user if ok else ""}


_GSM_SHIFT_SESSION_DOCTYPE = "GSM Shift Session"
_SHIFT_WISE_ENTRY_DOCTYPE = "Shift Wise Production Entry"
_GSM_SHIFT_LABELS = ("Day Shift", "Night Shift")
_GSM_REOPEN_REASONS = (
	"Accidental submit",
	"Missed rolls / incomplete entry",
	"Wrong data entered",
	"Other",
)


def _gsm_shift_session_table_exists() -> bool:
	return bool(frappe.db.table_exists(_GSM_SHIFT_SESSION_DOCTYPE))


def _gsm_session_has_coordinator() -> bool:
	return bool(frappe.db.has_column(_GSM_SHIFT_SESSION_DOCTYPE, "coordinator"))


def _gsm_require_desk_user():
	"""GSM shop-floor APIs — any logged-in Desk user, not Guest."""
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Please log in to continue."), frappe.AuthenticationError)


def _gsm_session_with_coordinator(fields: list[str]) -> list[str]:
	out = list(fields)
	if _gsm_session_has_coordinator() and "coordinator" not in out:
		out.append("coordinator")
	return out


def _normalize_gsm_shift_label(shift) -> str:
	s = _cstr(shift).strip()
	if not s:
		return ""
	low = s.lower()
	if "night" in low:
		return "Night Shift"
	if "day" in low:
		return "Day Shift"
	return s


def _serialize_gsm_shift_session(doc) -> dict:
	if not doc:
		return {}
	if isinstance(doc, dict):
		row = doc
	else:
		row = doc.as_dict() if hasattr(doc, "as_dict") else {}
	return {
		"name": row.get("name"),
		"run_date": str(row.get("run_date") or ""),
		"shift": row.get("shift") or "",
		"unit": row.get("custom_unit") or "",
		"operator": row.get("operator") or "",
		"supervisor": row.get("supervisor") or "",
		"coordinator": row.get("coordinator") or "",
		"batch_series_prefix": row.get("batch_series_prefix") or "",

		"status": row.get("status") or "",
		"opened_at": str(row.get("opened_at") or ""),
		"closed_at": str(row.get("closed_at") or ""),
		"is_reopen": cint(row.get("is_reopen") or 0),
		"reopen_reason": row.get("reopen_reason") or "",
		"reopen_remarks": row.get("reopen_remarks") or "",
		"previous_session": row.get("previous_session") or "",
		"opened_by": row.get("opened_by") or "",
		"zero_production_close": cint(row.get("zero_production_close") or 0),
		"reused_from_session": row.get("reused_from_session") or "",
		"selection_locked": cint(row.get("selection_locked") or 0),
	}


def _gsm_shift_suffix_from_prefix(prefix: str, root_5: str, doc) -> int:
	"""Extract shift digit(s) after root_5 from a series prefix (with or without /roll)."""
	prefix = _cstr(prefix).strip()
	if not prefix or not root_5 or not prefix.startswith(root_5):
		return 0
	if "/" in prefix:
		return doc._suffix_after_root(prefix, root_5)
	s_part = prefix[len(root_5) :]
	try:
		return int(s_part) if s_part else 0
	except ValueError:
		return 0


def _gsm_allocate_fresh_batch_prefix(run_date, shift, unit) -> str:
	"""Allocate next batch series prefix for a new GSM shift session.

	Always increments the shift digit — never reuses an existing same-shift SPR
	prefix. Considers Batch/SPR rows and GSM Shift Session history (open + closed).
	"""
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return ""
	doc = frappe.new_doc("Shaft Production Run")
	doc.run_date = getdate(run_date)
	doc.custom_unit = unit
	doc.shift = shift
	rd = getdate(run_date)
	comp_id, unit_num = doc._batch_prefix_parts()
	root_5 = f"{comp_id}-{unit_num}{rd.month:02d}{rd.year % 100:02d}"
	max_s = doc._spr_max_shift_suffix_for_root(root_5)
	if _gsm_shift_session_table_exists():
		# All sessions for this unit + month root (not only same run_date) — closed
		# batches from prior days must still advance the shift digit.
		session_prefixes = frappe.get_all(
			_GSM_SHIFT_SESSION_DOCTYPE,
			filters={
				"custom_unit": unit,
				"batch_series_prefix": ["like", f"{root_5}%"],
			},
			pluck="batch_series_prefix",
		)
		for pref in session_prefixes or []:
			max_s = max(max_s, _gsm_shift_suffix_from_prefix(pref, root_5, doc))
	next_s = (max_s + 1) if max_s >= 0 else 1
	return f"{root_5}{next_s}"


def _gsm_batch_prefix_has_rolls(prefix: str) -> bool:
	"""True when any SPR roll line exists under this batch series prefix."""
	prefix = _cstr(prefix).strip()
	if not prefix:
		return False
	row = frappe.db.sql(
		"""
		SELECT 1
		FROM `tabShaft Production Run Item`
		WHERE IFNULL(batch_no, '') LIKE %s
		LIMIT 1
		""",
		(f"{prefix}/%",),
	)
	return bool(row)


def _gsm_session_prefix_sort_key(prefix: str, run_date, unit: str) -> int:
	prefix = _cstr(prefix).strip()
	if not prefix:
		return 10**9
	doc = frappe.new_doc("Shaft Production Run")
	doc.run_date = getdate(run_date)
	doc.custom_unit = _cstr(unit).strip()
	comp_id, unit_num = doc._batch_prefix_parts()
	root_5 = f"{comp_id}-{unit_num}{getdate(run_date).month:02d}{getdate(run_date).year % 100:02d}"
	return _gsm_shift_suffix_from_prefix(prefix, root_5, doc)


def _gsm_find_reusable_batch_prefix(run_date, unit) -> dict | None:
	"""Oldest unused batch prefix from zero-production closed sessions (any shift, same run_date + unit)."""
	if not _gsm_shift_session_table_exists():
		return None
	unit = _cstr(unit).strip()
	rd = getdate(run_date)
	if not unit or not rd:
		return None
	open_prefixes = {
		_cstr(p).strip()
		for p in frappe.get_all(
			_GSM_SHIFT_SESSION_DOCTYPE,
			filters={"custom_unit": unit, "status": "Open"},
			pluck="batch_series_prefix",
		)
		if _cstr(p).strip()
	}
	fields = ["name", "batch_series_prefix", "shift", "zero_production_close"]
	if not frappe.get_meta(_GSM_SHIFT_SESSION_DOCTYPE).has_field("zero_production_close"):
		fields = ["name", "batch_series_prefix", "shift"]
	closed = frappe.get_all(
		_GSM_SHIFT_SESSION_DOCTYPE,
		filters={"run_date": rd, "custom_unit": unit, "status": "Closed"},
		fields=fields,
		limit_page_length=100,
	) or []
	candidates = []
	for row in closed:
		prefix = _cstr(row.get("batch_series_prefix")).strip()
		if not prefix or prefix in open_prefixes:
			continue
		if _gsm_batch_prefix_has_rolls(prefix):
			continue
		candidates.append(row)
	if not candidates:
		return None
	candidates.sort(key=lambda r: _gsm_session_prefix_sort_key(r.get("batch_series_prefix"), rd, unit))
	best = candidates[0]
	return {
		"prefix": _cstr(best.get("batch_series_prefix")).strip(),
		"from_session": best.get("name"),
		"from_shift": best.get("shift") or "",
	}


def _gsm_shift_batch_prefix(run_date, shift, unit) -> dict:
	reusable = _gsm_find_reusable_batch_prefix(run_date, unit)
	if reusable and reusable.get("prefix"):
		prefix = reusable["prefix"]
		return {
			"series_prefix": prefix,
			"sample_batch_no": f"{prefix}/1",
			"reused": True,
			"reused_from_session": reusable.get("from_session") or "",
			"reused_from_shift": reusable.get("from_shift") or "",
		}
	series_prefix = _gsm_allocate_fresh_batch_prefix(run_date, shift, unit)
	if not series_prefix:
		return {"series_prefix": "", "sample_batch_no": "", "reused": False}
	return {
		"series_prefix": series_prefix,
		"sample_batch_no": f"{series_prefix}/1",
		"reused": False,
	}


def _gsm_cleanup_empty_session_sprs(run_date, shift, unit) -> list[str]:
	"""Delete draft SPRs with no roll lines for this shift session."""
	removed = []
	for row in frappe.get_all(
		"Shaft Production Run",
		filters={
			"run_date": getdate(run_date),
			"shift": _normalize_gsm_shift_label(shift),
			"custom_unit": _cstr(unit).strip(),
			"docstatus": 0,
		},
		pluck="name",
		limit_page_length=50,
	) or []:
		try:
			from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
				_spr_is_real_roll_item_row,
			)

			spr = frappe.get_doc("Shaft Production Run", row)
			if any(_spr_is_real_roll_item_row(it) for it in (spr.items or [])):
				continue
			spr.delete(ignore_permissions=True)
			removed.append(row)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"GSM cleanup empty SPR:{row}")
	return removed


def _gsm_validate_employee_link(employee: str, label: str):
	employee = _cstr(employee).strip()
	if not employee:
		frappe.throw(_("{0} is required.").format(label))
	if not frappe.db.exists("Employee", employee):
		frappe.throw(_("{0} {1} not found.").format(label, employee))
	return employee


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def gsm_employee_link_query(doctype, txt, searchfield, start, page_len, filters):
	"""Active employees for GSM Operator / Supervisor / Co-ordinator pickers.

	Ignores User Permissions so shop-floor logins can see every Active employee.
	"""
	if doctype != "Employee":
		return []
	txt = _cstr(txt)
	start = cint(start)
	page_len = cint(page_len) or 20
	conds = ["status = 'Active'"]
	params = {"start": start, "page_len": page_len}
	if txt:
		conds.append("(name LIKE %(txt)s OR IFNULL(employee_name, '') LIKE %(txt)s)")
		params["txt"] = f"%{txt}%"
	return frappe.db.sql(
		f"""
		SELECT name, employee_name
		FROM `tabEmployee`
		WHERE {" AND ".join(conds)}
		ORDER BY IFNULL(employee_name, name)
		LIMIT %(start)s, %(page_len)s
		""",
		params,
	)


def _gsm_employee_id_and_name(employee) -> tuple[str, str]:
	emp_id = _cstr(employee).strip()
	if not emp_id:
		return "", ""
	emp_name = emp_id
	if frappe.db.exists("Employee", emp_id):
		emp_name = _cstr(frappe.db.get_value("Employee", emp_id, "employee_name")) or emp_id
	return emp_id, emp_name


def _gsm_shift_wise_set_person_fields(opts: dict, emp_id: str, id_fields: tuple, name_fields: tuple) -> None:
	"""Write Employee ID onto link/id fields and display name onto name fields.

	Shift Wise Production Entry uses custom_operator1 / custom_supervisor1 / coordinator
	for Employee links; custom_operator / custom_supervisor / coordinator_name for names.
	"""
	emp_id, emp_name = _gsm_employee_id_and_name(emp_id)
	if not emp_id and not emp_name:
		return
	meta = None
	if frappe.db.exists("DocType", _SHIFT_WISE_ENTRY_DOCTYPE):
		try:
			meta = frappe.get_meta(_SHIFT_WISE_ENTRY_DOCTYPE)
		except Exception:
			meta = None

	def _field(fn: str):
		if not meta:
			return None
		return meta.get_field(fn)

	for fn in id_fields:
		df = _field(fn)
		if meta and not df:
			continue
		# Always prefer Employee ID on id/link fields (even if fieldtype is Data on some sites).
		opts[fn] = emp_id or emp_name
	for fn in name_fields:
		df = _field(fn)
		if meta and not df:
			continue
		opts[fn] = emp_name or emp_id


def _gsm_coordinator_from_shift_sprs(run_date, shift, unit) -> str:
	"""Best-effort coordinator from submitted SPRs for this GSM shift."""
	if not run_date or not shift:
		return ""
	filters = {"docstatus": 1, "run_date": getdate(run_date), "shift": _normalize_gsm_shift_label(shift)}
	unit = _cstr(unit).strip()
	fields = ["name"]
	for col in ("custom_coordinator", "coordinator", "custom_unit"):
		if frappe.db.has_column("Shaft Production Run", col):
			fields.append(col)
	rows = frappe.get_all("Shaft Production Run", filters=filters, fields=fields, limit=40) or []
	for row in rows:
		row_unit = _cstr(row.get("custom_unit")).strip()
		if unit and row_unit and row_unit.lower() != unit.lower():
			continue
		for key in ("custom_coordinator", "coordinator"):
			val = _cstr(row.get(key)).strip()
			if val:
				return val
	return ""


def _gsm_shift_wise_route_options(doc, operator=None, supervisor=None, coordinator=None) -> dict:
	"""Prefill Shift Wise Production Entry from the closed GSM session."""
	operator = _cstr(operator).strip() or _cstr(getattr(doc, "operator", "")).strip()
	supervisor = _cstr(supervisor).strip() or _cstr(getattr(doc, "supervisor", "")).strip()
	coordinator = (
		_cstr(coordinator).strip()
		or _cstr(getattr(doc, "coordinator", "")).strip()
		or _gsm_coordinator_from_shift_sprs(doc.run_date, doc.shift, doc.custom_unit)
	)
	opts = {
		"posting_date": str(doc.run_date),
		"shift": doc.shift,
		"unit": doc.custom_unit,
		"custom_unit": doc.custom_unit,
		"batch_no": doc.batch_series_prefix,
	}
	_gsm_shift_wise_set_person_fields(
		opts,
		operator,
		("custom_operator1", "operator", "custom_shift_operator", "shift_operator"),
		("custom_operator", "operator_name", "custom_operator_name", "custom_shift_operator_name"),
	)
	_gsm_shift_wise_set_person_fields(
		opts,
		supervisor,
		("custom_supervisor1", "supervisor", "custom_shift_supervisor", "shift_supervisor"),
		("custom_supervisor", "supervisor_name", "custom_supervisor_name", "custom_shift_supervisor_name"),
	)
	if coordinator:
		_gsm_shift_wise_set_person_fields(
			opts,
			coordinator,
			("coordinator", "custom_coordinator"),
			("coordinator_name", "custom_coordinator_name"),
		)
	return {k: v for k, v in opts.items() if v not in (None, "")}


def _gsm_latest_closed_session(run_date, shift, unit):
	if not _gsm_shift_session_table_exists():
		return None
	rows = frappe.get_all(
		_GSM_SHIFT_SESSION_DOCTYPE,
		filters={
			"run_date": getdate(run_date),
			"shift": _normalize_gsm_shift_label(shift),
			"custom_unit": _cstr(unit).strip(),
			"status": "Closed",
		},
		fields=["name", "batch_series_prefix", "closed_at"],
		order_by="closed_at desc, modified desc",
		limit=1,
	)
	return rows[0] if rows else None


def _gsm_validate_reopen_reason(reopen_reason, reopen_remarks, required: bool):
	reopen_reason = _cstr(reopen_reason).strip()
	reopen_remarks = _cstr(reopen_remarks).strip()
	if not required:
		return reopen_reason, reopen_remarks
	if not reopen_reason:
		frappe.throw(_("Select a reason for re-opening this shift."))
	if reopen_reason not in _GSM_REOPEN_REASONS:
		frappe.throw(_("Invalid re-open reason."))
	if reopen_reason == "Other" and not reopen_remarks:
		frappe.throw(_("Enter remarks when re-open reason is Other."))
	return reopen_reason, reopen_remarks


@frappe.whitelist(methods=["GET", "POST"])
def check_gsm_shift_reopen_required(run_date=None, shift=None, unit=None):
	"""Return whether opening this shift requires a re-open reason (prior Closed session exists)."""
	if not _gsm_shift_session_table_exists():
		return {"required": False}
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return {"required": False}
	open_name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if open_name:
		return {"required": False, "already_open": True}
	closed = _gsm_latest_closed_session(run_date, shift, unit)
	if not closed:
		reusable = _gsm_find_reusable_batch_prefix(run_date, unit)
		if reusable:
			return {
				"required": False,
				"reused_batch": reusable.get("prefix") or "",
				"reused_from_shift": reusable.get("from_shift") or "",
			}
		return {"required": False}
	if _gsm_batch_prefix_has_rolls(_cstr(closed.batch_series_prefix)):
		return {
			"required": True,
			"previous_session": closed.name,
			"closed_batch": closed.batch_series_prefix or "",
			"reason_options": list(_GSM_REOPEN_REASONS),
		}
	reusable = _gsm_find_reusable_batch_prefix(run_date, unit)
	return {
		"required": False,
		"previous_session": closed.name,
		"closed_batch": closed.batch_series_prefix or "",
		"reused_batch": (reusable or {}).get("prefix") or closed.batch_series_prefix or "",
		"reused_from_shift": (reusable or {}).get("from_shift") or closed.shift or "",
	}


@frappe.whitelist(methods=["GET", "POST"])
def preview_gsm_shift_batch_prefix(run_date=None, shift=None, unit=None):
	"""Preview batch series prefix for a shift before opening."""
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		frappe.throw(_("Set Run Date, Unit, and Shift to preview batch."))
	return _gsm_shift_batch_prefix(run_date, shift, unit)


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_shift_session(run_date=None, shift=None, unit=None):
	"""Return the Open GSM shift session for run_date + shift + unit, if any."""
	if not _gsm_shift_session_table_exists():
		return {"ready": False, "session": None}
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return {"ready": True, "session": None}
	name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if not name:
		return {"ready": True, "session": None}
	doc = frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, name)
	return {"ready": True, "session": _serialize_gsm_shift_session(doc)}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_shift_sessions_for_date(run_date=None, unit=None):
	"""Day/Night status chips for GSM header."""
	if not _gsm_shift_session_table_exists():
		return {"ready": False, "shifts": {}}
	unit = _cstr(unit).strip()
	if not unit or not run_date:
		return {"ready": True, "shifts": {}}
	rd = getdate(run_date)
	out = {}
	for shift in _GSM_SHIFT_LABELS:
		open_row = frappe.db.get_value(
			_GSM_SHIFT_SESSION_DOCTYPE,
			{"run_date": rd, "shift": shift, "custom_unit": unit, "status": "Open"},
			_gsm_session_with_coordinator(["name", "status", "batch_series_prefix", "operator", "supervisor"]),
			as_dict=True,
		)
		if open_row:
			row = open_row
		else:
			closed_rows = frappe.get_all(
				_GSM_SHIFT_SESSION_DOCTYPE,
				filters={"run_date": rd, "shift": shift, "custom_unit": unit, "status": "Closed"},
				fields=_gsm_session_with_coordinator(["name", "status", "batch_series_prefix", "operator", "supervisor"]),
				order_by="modified desc",
				limit=1,
			)
			row = closed_rows[0] if closed_rows else None
		if not row:
			out[shift] = {"status": "Not started", "batch_series_prefix": ""}
		else:
			out[shift] = {
				"name": row.name,
				"status": row.status or "Open",
				"batch_series_prefix": row.batch_series_prefix or "",
				"operator": row.operator or "",
				"supervisor": row.supervisor or "",
				"coordinator": _cstr(row.get("coordinator") if isinstance(row, dict) else getattr(row, "coordinator", "")),
			}
	return {"ready": True, "shifts": out}


@frappe.whitelist(methods=["GET", "POST"])
def open_gsm_shift_session(
	run_date=None,
	shift=None,
	unit=None,
	operator=None,
	supervisor=None,
	coordinator=None,
	reopen_reason=None,
	reopen_remarks=None,
):
	"""Open a GSM shift session for run_date + shift + unit (Day and Night are separate)."""
	if not _gsm_shift_session_table_exists():
		frappe.throw(_("GSM Shift Session DocType is not installed. Run bench migrate."))
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		frappe.throw(_("Run Date, Unit, and Shift are required."))
	operator = _gsm_validate_employee_link(operator, _("Operator"))
	supervisor = _gsm_validate_employee_link(supervisor, _("Supervisor"))
	coordinator = _gsm_validate_employee_link(coordinator, _("Co-ordinator"))

	existing_name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if existing_name:
		doc = frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, existing_name)
		changed = False
		if doc.operator != operator:
			doc.operator = operator
			changed = True
		if doc.supervisor != supervisor:
			doc.supervisor = supervisor
			changed = True
		if _gsm_session_has_coordinator() and _cstr(getattr(doc, "coordinator", "")) != coordinator:
			doc.coordinator = coordinator
			changed = True
		if changed:
			doc.save(ignore_permissions=True)
		return {"session": _serialize_gsm_shift_session(doc), "reused": True}

	other_open = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{"custom_unit": unit, "status": "Open"},
		["name", "shift"],
		as_dict=True,
	)
	if other_open and other_open.name:
		frappe.throw(
			_("Close the open {0} session ({1}) before opening {2}.").format(
				other_open.shift or _("shift"), other_open.name, shift
			)
		)

	closed_prior = _gsm_latest_closed_session(run_date, shift, unit)
	prefix_info = _gsm_shift_batch_prefix(run_date, shift, unit)
	prefix = prefix_info.get("series_prefix") or ""
	batch_reused = cint(prefix_info.get("reused") or 0)
	prior_had_production = bool(
		closed_prior
		and _gsm_batch_prefix_has_rolls(_cstr(closed_prior.batch_series_prefix))
	)
	reopen_reason, reopen_remarks = _gsm_validate_reopen_reason(
		reopen_reason,
		reopen_remarks,
		required=bool(prior_had_production and not batch_reused),
	)
	if not prefix:
		frappe.throw(_("Could not allocate a batch series prefix for this shift."))

	doc = frappe.get_doc(
		{
			"doctype": _GSM_SHIFT_SESSION_DOCTYPE,
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"operator": operator,
			"supervisor": supervisor,
			"batch_series_prefix": prefix,
			"status": "Open",
			"opened_by": frappe.session.user,
			"opened_at": now_datetime(),
			"is_reopen": 1 if prior_had_production else 0,
			"reopen_reason": reopen_reason if prior_had_production else "",
			"reopen_remarks": reopen_remarks if prior_had_production else "",
			"previous_session": (
				(prefix_info.get("reused_from_session") or "")
				if batch_reused
				else (closed_prior.name if closed_prior else "")
			),
		}
	)
	if batch_reused and frappe.get_meta(_GSM_SHIFT_SESSION_DOCTYPE).has_field("reused_from_session"):
		doc.reused_from_session = prefix_info.get("reused_from_session") or ""
	if _gsm_session_has_coordinator():
		doc.coordinator = coordinator
	doc.insert(ignore_permissions=True)
	return {
		"session": _serialize_gsm_shift_session(doc),
		"reused": batch_reused,
		"batch_reused_from_shift": prefix_info.get("reused_from_shift") or "",
	}


@frappe.whitelist(methods=["GET", "POST"])
def validate_gsm_shift_close(run_date=None, shift=None, unit=None, session_sprs=None):
	"""Ensure shift can close — submitted SPRs, no draft rolls blocking."""
	if not _gsm_shift_session_table_exists():
		return {"ok": True, "errors": []}
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	session_sprs = _parse_json_arg(session_sprs, [])
	errors = []
	name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if not name:
		errors.append(_("No open shift session found for {0} on {1}.").format(shift, unit))
	for row in session_sprs or []:
		if not isinstance(row, dict):
			continue
		spr_name = _cstr(row.get("spr_name")).strip()
		if not spr_name:
			continue
		if not frappe.db.exists("Shaft Production Run", spr_name):
			continue
		ds = cint(frappe.db.get_value("Shaft Production Run", spr_name, "docstatus") or 0)
		if ds != 1:
			if not _gsm_spr_has_submittable_rolls(spr_name):
				continue
			errors.append(_("SPR {0} has rolls but is not submitted.").format(spr_name))
	return {"ok": not errors, "errors": errors}


@frappe.whitelist(methods=["GET", "POST"])
def close_gsm_shift_session(run_date=None, shift=None, unit=None, operator=None, supervisor=None, coordinator=None):
	"""Close the open GSM shift session and return Shift Wise redirect payload."""
	if not _gsm_shift_session_table_exists():
		frappe.throw(_("GSM Shift Session DocType is not installed. Run bench migrate."))
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if not name:
		frappe.throw(_("No open shift session found."))
	doc = frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, name)
	fallback_operator = _cstr(operator).strip()
	fallback_supervisor = _cstr(supervisor).strip()
	fallback_coordinator = _cstr(coordinator).strip()
	if fallback_operator and not _cstr(doc.operator).strip():
		doc.operator = fallback_operator
	if fallback_supervisor and not _cstr(doc.supervisor).strip():
		doc.supervisor = fallback_supervisor
	if fallback_coordinator and _gsm_session_has_coordinator() and not _cstr(getattr(doc, "coordinator", "")).strip():
		doc.coordinator = fallback_coordinator
	prefix = _cstr(doc.batch_series_prefix).strip()
	zero_prod = not _gsm_batch_prefix_has_rolls(prefix)
	if frappe.get_meta(_GSM_SHIFT_SESSION_DOCTYPE).has_field("zero_production_close"):
		doc.zero_production_close = 1 if zero_prod else 0
	doc.status = "Closed"
	doc.closed_at = now_datetime()
	meta = frappe.get_meta(_GSM_SHIFT_SESSION_DOCTYPE)
	if meta.has_field("locked_jobs"):
		doc.locked_jobs = []
	if meta.has_field("selection_locked"):
		doc.selection_locked = 0
	doc.save(ignore_permissions=True)
	removed_sprs = []
	if zero_prod:
		removed_sprs = _gsm_cleanup_empty_session_sprs(doc.run_date, doc.shift, doc.custom_unit)
	next_shift = "Night Shift" if shift == "Day Shift" else "Day Shift"
	return {
		"session": _serialize_gsm_shift_session(doc),
		"zero_production_close": zero_prod,
		"removed_empty_sprs": removed_sprs,
		"next_shift": next_shift,
		"redirect": {
			"doctype": _SHIFT_WISE_ENTRY_DOCTYPE,
			"route_options": _gsm_shift_wise_route_options(
				doc,
				operator=fallback_operator or doc.operator,
				supervisor=fallback_supervisor or doc.supervisor,
				coordinator=fallback_coordinator or getattr(doc, "coordinator", None),
			),
		},
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_open_gsm_shift_for_unit(unit=None):
	"""Return the single open GSM shift session for a unit (any run_date/shift)."""
	if not _gsm_shift_session_table_exists():
		return {"ready": False, "session": None}
	unit = _cstr(unit).strip()
	if not unit:
		return {"ready": True, "session": None}
	row = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{"custom_unit": unit, "status": "Open"},
		_gsm_session_with_coordinator(["name", "run_date", "shift", "custom_unit", "batch_series_prefix", "operator", "supervisor", "opened_by", "opened_at", "status"]),
		as_dict=True,
	)
	if not row:
		return {"ready": True, "session": None}
	return {"ready": True, "session": _serialize_gsm_shift_session(row)}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_pp_orders_for_date(planned_date=None, unit=None):
	"""PP-submitted fabric rows for GSM sidebar — supplements color chart when planned_date was missing."""
	planned_date = getdate(planned_date) if planned_date else None
	unit = _normalize_mix_unit(unit) if unit else ""
	if not planned_date:
		return []
	try:
		from production_entry.production_planning.scheduler_api import get_color_chart_data

		rows = get_color_chart_data(
			date=str(planned_date),
			board_process_scope="only_100",
		) or []
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_gsm_pp_orders_for_date")
		return []
	out = []
	seen_pp = set()
	pd_str = str(planned_date)
	for r in rows:
		if cint(r.get("pp_docstatus") or 0) != 1:
			continue
		row_unit = _normalize_mix_unit(r.get("unit") or "")
		if unit and row_unit and row_unit != unit:
			continue
		pp_id = _cstr(r.get("pp_id") or "").strip()
		if not pp_id or pp_id in seen_pp:
			continue
		seen_pp.add(pp_id)
		planned = _cstr(r.get("plannedDate") or r.get("planned_date") or pd_str).strip()
		if planned and planned != pd_str:
			continue
		out.append(
			{
				"pp_id": pp_id,
				"pp_docstatus": 1,
				"partyCode": _cstr(r.get("partyCode") or r.get("party_code") or ""),
				"party_code": _cstr(r.get("partyCode") or r.get("party_code") or ""),
				"customer_name": _cstr(r.get("customer_name") or r.get("customer") or ""),
				"unit": row_unit,
				"plannedDate": planned or pd_str,
				"planned_date": planned or pd_str,
				"itemName": _cstr(r.get("itemName") or r.get("name") or ""),
				"name": _cstr(r.get("name") or r.get("itemName") or ""),
				"gsm": r.get("gsm"),
				"width_inch": r.get("width_inch") or r.get("width"),
				"qty": r.get("qty"),
			}
		)
	return out


def _gsm_label_type_display(raw: str) -> str:
	"""Human-readable label type for GSM header (Label Template link or free text)."""
	v = normalize_label_template_link(_cstr(raw))
	if not v:
		return ""
	if frappe.db.exists("DocType", "Label Template") and frappe.db.exists("Label Template", v):
		lt_meta = frappe.get_meta("Label Template")
		for fn in ("label_name", "template_name", "label"):
			if lt_meta.has_field(fn):
				disp = frappe.db.get_value("Label Template", v, fn)
				if _cstr(disp).strip():
					return _cstr(disp).strip()
	return v


def _gsm_label_type_for_pp_spr(pp_id: str | None = None, spr_name: str | None = None) -> str:
	"""Resolve label type for GSM — SPR Label Template first, then Production Plan."""
	pp_id = _cstr(pp_id).strip()
	spr_name = _cstr(spr_name).strip()
	label = ""
	if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
		label = _spr_pick_doc_field(frappe.get_doc("Shaft Production Run", spr_name), "custom_label")
	if not label and pp_id and frappe.db.exists("Production Plan", pp_id):
		pp = frappe.get_doc("Production Plan", pp_id)
		label = resolve_label_from_pp_doc(pp)
		if not label:
			pp_meta = frappe.get_meta("Production Plan")
			for sheet_fn in ("custom_planning_sheet", "planning_sheet", "custom_planning_sheet_name"):
				if not pp_meta.has_field(sheet_fn):
					continue
				sheet_name = _cstr(pp.get(sheet_fn)).strip()
				if sheet_name and frappe.db.exists("Planning sheet", sheet_name):
					label = resolve_label_from_planning_sheet_doc(frappe.get_doc("Planning sheet", sheet_name))
					if label:
						break
	return _gsm_label_type_display(label)


def _gsm_draft_sprs_for_session(run_date, shift, unit) -> list[dict]:
	"""Draft SPR headers for an active GSM shift (run_date + shift + unit)."""
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return []
	filters = {
		# Include both draft and submitted SPRs for an open GSM shift session.
		# Users can switch computers / reopen the entry screen without losing
		# already-posted (submitted) roll rows.
		"docstatus": ["in", [0, 1]],
		"run_date": getdate(run_date),
		"shift": shift,
		"custom_unit": unit,
	}
	fields = ["name", "production_plan", "custom_order_code", "modified"]
	if frappe.db.has_column("Shaft Production Run", "custom_party_code"):
		fields.append("custom_party_code")
	if frappe.db.has_column("Shaft Production Run", "custom_label"):
		fields.append("custom_label")
	if frappe.db.has_column("Shaft Production Run", "is_mix_roll"):
		fields.append("is_mix_roll")
	rows = frappe.get_all(
		"Shaft Production Run",
		filters=filters,
		fields=fields,
		order_by="modified desc",
		limit=50,
	)
	out = []
	for row in rows or []:
		if cint(row.get("is_mix_roll")):
			continue
		pp_id = _cstr(row.get("production_plan")).strip()
		is_trial = 0
		if not pp_id:
			# Standalone Trail Order SPR — key GSM session/job-board by the SPR name.
			pp_id = row.name
			is_trial = 1
		order_code = _cstr(row.get("custom_order_code") or row.get("custom_party_code") or "")
		if not order_code and not is_trial:
			order_code = _gsm_order_code_for_pp(pp_id)
		out.append(
			{
				"pp_id": pp_id,
				"spr_name": row.name,
				"order_code": order_code,
				"is_trial": is_trial,
				"label_type": _gsm_label_type_for_pp_spr(pp_id, row.name)
				or _gsm_label_type_display(row.get("custom_label") or ""),
			}
		)
	return out


def _gsm_attach_locked_job_draft_sprs(session_doc, run_date, shift, unit, session_sprs: list) -> list:
	"""Recover draft SPRs for locked jobs when header unit/date/shift was missing."""
	out = list(session_sprs or [])
	known_spr = {_cstr(s.get("spr_name")) for s in out}
	known_pp = {_cstr(s.get("pp_id")) for s in out if _cstr(s.get("pp_id"))}
	prefix = _cstr(getattr(session_doc, "batch_series_prefix", "") or "")
	for locked in _gsm_locked_jobs_from_session(session_doc):
		pp_id = _cstr(locked.get("pp_id")).strip()
		if not pp_id or pp_id in known_pp:
			continue
		spr_name = _find_draft_spr_for_pp(
			pp_id, unit=unit, run_date=run_date, shift=shift, series_prefix=prefix
		)
		if not spr_name or spr_name in known_spr:
			continue
		if not frappe.db.exists("Shaft Production Run", spr_name):
			continue
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			continue
		if _apply_gsm_session_header_to_spr(spr, run_date, shift, unit):
			spr.save(ignore_permissions=True)
		out.append(
			{
				"pp_id": pp_id,
				"spr_name": spr_name,
				"order_code": _cstr(spr.get("custom_order_code") or _gsm_order_code_for_pp(pp_id)),
				"is_trial": 1 if _gsm_is_standalone_spr_key(pp_id) else 0,
				"label_type": _gsm_label_type_for_pp_spr(pp_id, spr_name),
			}
		)
		known_spr.add(spr_name)
		known_pp.add(pp_id)
	if prefix:
		item_parents = frappe.db.sql(
			"""
			select distinct parent
			from `tabShaft Production Run Item`
			where ifnull(batch_no, '') like %s
			""",
			(f"{prefix}/%",),
		) or []
		for (spr_name,) in item_parents:
			spr_name = _cstr(spr_name).strip()
			if not spr_name or spr_name in known_spr:
				continue
			if not frappe.db.exists("Shaft Production Run", spr_name):
				continue
			spr = frappe.get_doc("Shaft Production Run", spr_name)
			if cint(spr.docstatus) != 0 or spr_doc_is_mix_roll(spr):
				continue
			pp_id = _cstr(spr.get("production_plan")).strip() or spr_name
			if _apply_gsm_session_header_to_spr(spr, run_date, shift, unit):
				spr.save(ignore_permissions=True)
			out.append(
				{
					"pp_id": pp_id,
					"spr_name": spr_name,
					"order_code": _cstr(spr.get("custom_order_code") or _gsm_order_code_for_pp(pp_id)),
					"is_trial": 1 if _gsm_is_standalone_spr_key(pp_id) else 0,
					"label_type": _gsm_label_type_for_pp_spr(pp_id, spr_name),
				}
			)
			known_spr.add(spr_name)
			known_pp.add(pp_id)
	return out


def _gsm_mix_sprs_for_session(run_date, shift, unit) -> list[dict]:
	"""Draft/submitted mix-roll SPRs for the open GSM shift (same date + shift + unit)."""
	unit = _normalize_mix_unit(unit)
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return []
	if not frappe.db.has_column("Shaft Production Run", "is_mix_roll"):
		return []
	rows = frappe.get_all(
		"Shaft Production Run",
		filters={
			"docstatus": ["in", [0, 1]],
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"is_mix_roll": 1,
		},
		fields=["name", "custom_order_code", "custom_unit", "docstatus"],
		order_by="modified desc",
		limit=20,
	)
	out = []
	for row in rows or []:
		row_unit = _normalize_mix_unit(row.get("custom_unit") or "")
		if row_unit and row_unit != unit:
			continue
		out.append(
			{
				"pp_id": "",
				"spr_name": row.name,
				"order_code": _cstr(row.get("custom_order_code") or ""),
				"custom_unit": row_unit or unit,
				"is_mix_roll": 1,
				"label_type": "Mix Roll",
			}
		)
	return out


def _gsm_session_roll_revision(session_doc, session_sprs: list) -> str:
	"""Revision token that changes when any session SPR roll or waste row changes."""
	parts = [_cstr(getattr(session_doc, "name", "")), str(getattr(session_doc, "modified", "") or "")]
	roll_count = 0
	waste_count = 0
	batch_tokens = []
	for spr_row in session_sprs or []:
		spr_name = _cstr(spr_row.get("spr_name") if isinstance(spr_row, dict) else "").strip()
		if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
			continue
		spr_mod = frappe.db.get_value("Shaft Production Run", spr_name, "modified")
		parts.append(f"{spr_name}:{spr_mod or ''}")
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		for it in spr.items or []:
			if not _spr_is_real_roll_item_row(it):
				continue
			roll_count += 1
			bn = _cstr(getattr(it, "batch_no", ""))
			if bn:
				batch_tokens.append(f"A:{bn}")
		for waste in spr.get("custom_roll_waste") or []:
			waste_count += 1
			bn = _cstr(getattr(waste, "batch_no", ""))
			if bn:
				batch_tokens.append(f"W:{bn}")
	parts.append(f"rolls={roll_count}")
	parts.append(f"waste={waste_count}")
	parts.append("batches=" + ",".join(sorted(batch_tokens)))
	locked = _gsm_locked_jobs_from_session(session_doc)
	if locked:
		parts.append(
			"locked="
			+ ",".join(sorted(f"{j['pp_id']}:{j['job_id']}" for j in locked))
		)
	parts.append(f"sel_lock={cint(getattr(session_doc, 'selection_locked', 0) or 0)}")
	return "|".join(parts)


def _gsm_locked_jobs_from_session(session_doc) -> list[dict]:
	out = []
	if not session_doc:
		return out
	for row in getattr(session_doc, "locked_jobs", None) or []:
		pp_id = _cstr(getattr(row, "pp_id", None)).strip()
		job_id = _cstr(getattr(row, "job_id", None)).strip()
		if pp_id and job_id:
			out.append({"pp_id": pp_id, "job_id": job_id})
	return out


def _gsm_open_shift_session_doc(run_date, shift, unit):
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		return None
	name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if not name:
		return None
	return frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, name)


def _gsm_publish_session_selection_update(session_doc) -> None:
	try:
		frappe.publish_realtime(
			"gsm_production_entry_updated",
			{
				"run_date": str(session_doc.run_date or ""),
				"shift": _cstr(session_doc.shift),
				"unit": _cstr(session_doc.custom_unit),
				"selection_updated": True,
				"modified": str(session_doc.modified or ""),
			},
		)
	except Exception:
		pass


@frappe.whitelist(methods=["GET", "POST"])
def save_gsm_session_job_selections(
	run_date=None, shift=None, unit=None, entries=None, selection_locked=0
):
	"""Persist GSM locked job picks on the open shift session (cross-device sync)."""
	entries = _parse_json_arg(entries, [])
	session_doc = _gsm_open_shift_session_doc(run_date, shift, unit)
	if not session_doc:
		frappe.throw(_("No open GSM shift session for this run date, shift, and unit."))

	session_doc.locked_jobs = []
	seen = set()
	for entry in entries or []:
		if not isinstance(entry, dict):
			continue
		pp_id = _cstr(entry.get("pp_id") or entry.get("ppId")).strip()
		job_id = _cstr(entry.get("job_id") or entry.get("jobId")).strip()
		if not pp_id or not job_id:
			continue
		key = (pp_id, job_id)
		if key in seen:
			continue
		seen.add(key)
		session_doc.append("locked_jobs", {"pp_id": pp_id, "job_id": job_id})

	session_doc.selection_locked = cint(selection_locked)
	session_doc.save(ignore_permissions=True)
	_gsm_publish_session_selection_update(session_doc)
	return {
		"status": "ok",
		"selection_locked": cint(session_doc.selection_locked),
		"job_count": len(session_doc.locked_jobs or []),
		"session": _serialize_gsm_shift_session(session_doc),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_active_shift_resume(run_date=None, shift=None, unit=None):
	"""Hydrate GSM grid from server — draft SPR roll lines for an open shift session."""
	unit = _cstr(unit).strip()
	shift = _normalize_gsm_shift_label(shift)
	if not unit or not run_date or not shift:
		frappe.throw(_("Run Date, Unit, and Shift are required."))

	session_name = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{
			"run_date": getdate(run_date),
			"shift": shift,
			"custom_unit": unit,
			"status": "Open",
		},
		"name",
	)
	if not session_name:
		return {
			"status": "no_open_session",
			"session": None,
			"session_sprs": [],
			"roll_lines": [],
			"job_selections": [],
			"selection_locked": 0,
		}

	session_doc = frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, session_name)
	session_sprs = _gsm_draft_sprs_for_session(run_date, shift, unit)
	session_sprs = _gsm_attach_locked_job_draft_sprs(session_doc, run_date, shift, unit, session_sprs)
	mix_sprs = _gsm_mix_sprs_for_session(run_date, shift, unit)
	roll_lines = []
	job_keys = set()

	# Global LIFO must be stable across edits. Child ``modified`` timestamps can
	# change for every row in an SPR when one row is saved, which moves that
	# order's rows as a block. The shift batch suffix is assigned globally when
	# the roll is created, so it is the durable creation order across all SPRs.
	staged: list[tuple] = []
	for spr_row in list(session_sprs) + list(mix_sprs):
		spr_name = spr_row.get("spr_name")
		pp_id = spr_row.get("pp_id")
		if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
			continue
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		is_mix_roll = spr_doc_is_mix_roll(spr) or cint(spr_row.get("is_mix_roll"))
		for line in _gsm_serialize_spr_roll_lines_for_grid(spr):
			line["is_mix_roll_row"] = 1 if is_mix_roll else 0
			if is_mix_roll:
				line["planned_qty"] = 0
				line["custom_unit"] = _cstr(spr.get("custom_unit") or "")
				if not _cstr(line.get("party_code")).strip():
					line["party_code"] = _cstr(spr.get("custom_order_code") or "")
			batch_suffix = _gsm_roll_suffix_from_batch_no(_cstr(line.get("batch_no")))
			child_idx = cint(line.get("child_idx") or 0)
			staged.append((batch_suffix, child_idx, spr_name, pp_id, line, False))
			jid = _cstr(line.get("job_id") or "")
			if jid and pp_id and not is_mix_roll:
				job_keys.add((pp_id, jid))
		if is_mix_roll:
			continue
		seen_waste_batches = set()
		for waste in spr.get("custom_roll_waste") or []:
			line = _gsm_serialize_roll_waste_for_grid(spr, waste, pp_id)
			bn = _cstr(line.get("batch_no") or "").strip()
			if bn and bn in seen_waste_batches:
				continue
			if bn:
				seen_waste_batches.add(bn)
			batch_suffix = _gsm_roll_suffix_from_batch_no(bn)
			child_idx = cint(getattr(waste, "idx", 0) or 0)
			staged.append((batch_suffix, child_idx, spr_name, pp_id, line, True))
			jid = _cstr(line.get("job_id") or "")
			if jid and pp_id:
				job_keys.add((pp_id, jid))

	staged.sort(key=lambda t: (t[0], t[1]), reverse=True)
	total = len(staged)
	for idx, (_suffix, _child_idx, spr_name, pp_id, line, is_waste) in enumerate(staged):
		seq = _suffix if _suffix > 0 else (total - idx)
		prefix = "resume-waste" if is_waste else "resume"
		line["_id"] = f"{prefix}-{spr_name}-{seq}"
		# Batch suffix is the durable creation order — LIFO display uses this.
		line["creation_seq"] = seq
		line["spr_name"] = spr_name
		roll_lines.append(line)

	# Job strip must reflect what the operator locked/selected — NOT every job
	# that happens to have rolls on session SPRs (that inflated "10 job(s)").
	locked = _gsm_locked_jobs_from_session(session_doc)
	if locked:
		job_selections = [{"pp_id": j["pp_id"], "job_id": j["job_id"]} for j in locked]
	else:
		job_selections = []

	# roll_lines are newest-first (LIFO) via batch suffix descending.

	server_revision = _gsm_session_roll_revision(session_doc, list(session_sprs) + list(mix_sprs))

	return {
		"status": "ok",
		"session": _serialize_gsm_shift_session(session_doc),
		"session_sprs": session_sprs,
		"roll_lines": roll_lines,
		"job_selections": job_selections,
		"selection_locked": cint(getattr(session_doc, "selection_locked", 0) or 0),
		"roll_count": len(roll_lines),
		"server_modified": str(session_doc.modified or ""),
		"server_revision": server_revision,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_session_full_state(unit=None):
	"""Server-first GSM bootstrap — open shift session + rolls + job selections (cross-device recovery).

	Requires an explicit unit; never returns another unit's open session.
	"""
	if not _gsm_shift_session_table_exists():
		return {"status": "no_open_session"}
	unit = _cstr(unit).strip()
	if not unit:
		return {"status": "no_open_session"}
	row = frappe.db.get_value(
		_GSM_SHIFT_SESSION_DOCTYPE,
		{"status": "Open", "custom_unit": unit},
		_gsm_session_with_coordinator(["name", "run_date", "shift", "custom_unit", "batch_series_prefix", "operator", "supervisor", "opened_by", "opened_at", "status", "modified"]),
		as_dict=True,
	)
	if not row:
		return {"status": "no_open_session"}

	run_date = row.run_date
	shift = row.shift
	unit = _cstr(row.custom_unit)
	resume = get_gsm_active_shift_resume(run_date=run_date, shift=shift, unit=unit)
	if resume.get("status") != "ok":
		return resume

	pp_ids = list({_cstr(s.get("pp_id")).strip() for s in resume.get("session_sprs") or [] if _cstr(s.get("pp_id")).strip()})
	job_board = {}
	if pp_ids:
		job_board = get_gsm_pp_job_board(pp_ids=pp_ids, run_date=run_date, shift=shift, unit=unit)

	resume["unit"] = unit
	resume["run_date"] = str(run_date)
	resume["shift"] = shift
	resume["job_board"] = job_board
	return resume


@frappe.whitelist(methods=["GET", "POST"])
def preview_spr_batch_numbers_for_entry(
	unit,
	run_date,
	shift,
	count=1,
	client_max_roll=None,
	client_series_prefix=None,
	existing_batches=None,
	session_local=None,
	gsm_shift_prefix=None,
):
	"""Read-only batch/roll preview for GSM Production Entry (no SPR document required).

	When session_local=1, roll suffix uses max(DB global max, grid max + 1) so GSM continues
	from desk SPR rolls already saved (e.g. /1 /2 in DB → next /3).
	"""
	count = cint(count)
	if count < 1:
		return []
	unit = _cstr(unit).strip()
	shift = _cstr(shift).strip()
	if not unit or not run_date or not shift:
		frappe.throw(_("Set Run Date, Unit, and Shift to preview batch numbers."))

	doc = frappe.new_doc("Shaft Production Run")
	doc.run_date = run_date
	doc.custom_unit = unit
	doc.shift = shift

	existing = []
	if existing_batches:
		if isinstance(existing_batches, str):
			try:
				existing = json.loads(existing_batches) or []
			except Exception:
				existing = [x.strip() for x in existing_batches.split(",") if x.strip()]
		elif isinstance(existing_batches, (list, tuple)):
			existing = list(existing_batches)

	for bn in existing:
		bn = _cstr(bn).strip()
		if bn:
			row = doc.append("items", {})
			row.batch_no = bn

	rd = getdate(run_date)
	comp_id, unit_num = doc._batch_prefix_parts()
	root_5 = f"{comp_id}-{unit_num}{rd.month:02d}{rd.year % 100:02d}"
	fresh_prefix = doc._resolve_series_prefix(root_5)
	csp = _cstr(client_series_prefix).strip()
	if csp and csp.startswith(root_5) and csp == fresh_prefix:
		series_prefix = csp
	else:
		series_prefix = fresh_prefix

	db_start = doc._next_roll_starting(series_prefix)
	next_roll = db_start
	if cint(gsm_shift_prefix) and csp:
		next_roll = _spr_roll_starting_for_gsm_session(
			series_prefix,
			spr_name=None,
			existing_batches=existing,
			client_max_roll=client_max_roll,
		)
	elif cint(session_local):
		mx = 0
		for row in doc.items or []:
			bn = _cstr(getattr(row, "batch_no", "")).strip()
			if bn:
				mx = max(mx, doc._roll_no_from_batch(bn, series_prefix))
		try:
			if client_max_roll is not None and cint(client_max_roll) >= 0:
				mx = max(mx, cint(client_max_roll))
		except Exception:
			pass
		grid_next = (mx + 1) if mx > 0 else 0
		next_roll = max(db_start, grid_next if grid_next > 0 else db_start)
	else:
		try:
			if client_max_roll is not None and cint(client_max_roll) >= 0 and csp == series_prefix:
				next_roll = max(int(next_roll), cint(client_max_roll) + 1)
		except Exception:
			pass
		next_roll = max(int(next_roll), int(db_start))

	used_batches = set(_cstr(x).strip() for x in existing if _cstr(x).strip())
	out = []
	for _i in range(count):
		bn, next_roll = _spr_next_available_batch_no(series_prefix, int(next_roll), used_batches)
		used_batches.add(bn)
		out.append({"batch_no": bn, "roll_no": next_roll, "series_prefix": series_prefix})
		next_roll += 1
	return out


@frappe.whitelist(methods=["GET", "POST"])
def get_order_length_for_pt_line(pp_id, gsm=None, width_inch=None, item_code=None, production_plan_item=None, job_id=None):
	"""Order length (meters per roll) from PP shaft details for a GSM+width line."""
	pp_id = _cstr(pp_id).strip()
	if not pp_id or not frappe.db.exists("Production Plan", pp_id):
		frappe.throw(_("Production Plan not found"))
	target_gsm = cint(gsm) if gsm is not None else 0
	target_w = flt(width_inch) if width_inch is not None else 0.0
	item_code = _cstr(item_code).strip()
	target_job = _cstr(job_id).strip()

	pp = frappe.get_doc("Production Plan", pp_id)
	pp_shafts = pp.get("custom_shaft_details") or pp.get("shaft_details") or []
	meter_keys = _meter_keys()

	for idx, shaft in enumerate(pp_shafts, start=1):
		if target_job:
			shaft_job = _cstr(_pick_value(shaft, ["job_id", "job", "job_no"], str(idx)))
			if shaft_job != target_job:
				continue
		sg = _shaft_gsm(shaft)
		sw = _shaft_width_inch(shaft)
		if target_gsm and sg and sg != target_gsm:
			continue
		if target_w > 0:
			comb = _cstr(_pick_value(shaft, ["combination", "combined_width", "shaft", "shaft_details"], ""))
			widths = _parse_combination_widths_inches(comb) if comb else []
			width_match = abs(sw - target_w) < 0.05
			if widths and not width_match:
				width_match = any(abs(flt(w) - target_w) < 0.05 for w in widths)
			if not width_match:
				continue
		raw_meter = flt(_pick_value(shaft, meter_keys, 0))
		if raw_meter > 0:
			return {"meter_roll_mtrs": raw_meter, "source": "shaft_details"}

	if production_plan_item:
		for row in pp.get("po_items") or []:
			if _cstr(getattr(row, "name", None)) != _cstr(production_plan_item):
				continue
			raw_meter = flt(_pick_value(row, meter_keys, 0))
			if raw_meter > 0:
				return {"meter_roll_mtrs": raw_meter, "source": "po_item"}

	if item_code:
		try:
			g, w = parse_item_code(item_code)
			if not target_gsm and g:
				target_gsm = int(g)
			if not target_w and w:
				target_w = flt(w)
		except Exception:
			pass

	if frappe.db.exists("DocType", "Planning Table"):
		pt_fields = _planning_table_meter_select_fields()
		pt_filters = (
			{"production_plan": pp_id}
			if frappe.db.has_column("Planning Table", "production_plan")
			else {}
		)
		if target_gsm and frappe.db.has_column("Planning Table", "gsm"):
			pt_filters["gsm"] = target_gsm
		if target_w > 0 and frappe.db.has_column("Planning Table", "width_inch"):
			pt_filters["width_inch"] = target_w
		for psi_row in frappe.get_all(
			"Planning Table",
			filters=pt_filters,
			fields=pt_fields,
			limit=50,
		):
			raw_meter = flt(_pick_value(psi_row, meter_keys, 0))
			if raw_meter > 0:
				return {"meter_roll_mtrs": raw_meter, "source": "planning_table"}
		if pt_filters.get("width_inch"):
			relaxed = {
				k: v
				for k, v in pt_filters.items()
				if k != "width_inch"
			}
			for psi_row in frappe.get_all(
				"Planning Table",
				filters=relaxed,
				fields=pt_fields,
				limit=50,
			):
				raw_meter = flt(_pick_value(psi_row, meter_keys, 0))
				if raw_meter > 0:
					return {"meter_roll_mtrs": raw_meter, "source": "planning_table"}

	fallback = flt(
		pp.get("meter__roll")
		or pp.get("custom_meter_roll_mtrs")
		or pp.get("meter_roll_mtrs")
		or pp.get("custom_meter_per_roll")
		or pp.get("meter")
		or 0
	)
	return {"meter_roll_mtrs": fallback or 0, "source": "pp_fallback"}


@frappe.whitelist(methods=["GET", "POST"])
def resolve_work_order_for_roll_line(
	pp_id, gsm=None, width_inch=None, item_code=None, production_plan_item=None, job_id=None
):
	"""Resolve primary Work Order name for a GSM+width roll line."""
	pp_id = _cstr(pp_id).strip()
	if not pp_id:
		return {"work_order": "", "work_orders": [], "production_item": "", "production_item_name": ""}
	job_gsm = cint(gsm) if gsm is not None else None
	if not job_gsm and item_code:
		try:
			g, _w = parse_item_code(_cstr(item_code))
			if g > 0:
				job_gsm = int(g)
		except Exception:
			pass
	jid = _cstr(job_id).strip() or None
	ppi = _cstr(production_plan_item).strip() or None
	if jid:
		ppi = None
	row_index = None
	if jid:
		try:
			row_index = int(jid) - 1
		except Exception:
			row_index = None
	combination = None
	if jid and frappe.db.exists("Production Plan", pp_id):
		for sr in _gsm_pp_shaft_rows(frappe.get_doc("Production Plan", pp_id)):
			if _cstr(sr.get("job")) == jid:
				combination = _cstr(sr.get("combination") or "")
				break
	if not combination and flt(width_inch) > 0:
		combination = _cstr(width_inch)
	wos = _resolve_wos_for_pp_job_row(
		pp_id,
		ppi=ppi,
		job_id=jid,
		row_index=row_index,
		job_gsm=job_gsm,
		combination=combination or None,
	)
	names = [_cstr(w.get("name")) for w in (wos or []) if w.get("name")]
	chosen = (wos or [None])[0] if wos else None
	prod_item = _cstr(getattr(chosen, "get", lambda k, d=None: None)("production_item", None) or "") if chosen else ""
	prod_item_name = _cstr(frappe.db.get_value("Item", prod_item, "item_name") or "") if prod_item else ""
	return {
		"work_order": names[0] if names else "",
		"work_orders": names,
		# Authoritative item for the GSM+width roll line (prevents item/WO mismatch).
		"production_item": prod_item,
		"production_item_name": prod_item_name,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_spr_for_pp(pp_id, prefer_draft=1, unit=None, run_date=None, shift=None):
	"""Read-only: return existing SPR for PP (never creates)."""
	pp_id = _cstr(pp_id).strip()
	if not pp_id:
		return {"spr_name": ""}
	prefer = cint(prefer_draft) != 0
	name = None
	if prefer:
		name = _find_draft_spr_for_pp(pp_id, unit=unit, run_date=run_date, shift=shift)
	if not name:
		name = _find_spr_for_pp(pp_id, prefer_draft=prefer)
	return {"spr_name": name or ""}


@frappe.whitelist(methods=["GET", "POST"])
def get_pt_line_roll_quota_status(
	pp_id,
	production_plan_item=None,
	gsm=None,
	width_inch=None,
	item_code=None,
	run_date=None,
	shift=None,
	unit=None,
):
	"""Roll line quota for one PP line / GSM+width.

	When run_date is set: current_rolls = this shift only; shift_max_rolls = job max minus
	rolls already recorded on other shifts the same day (e.g. Night 0/67 after Day 100/167).
	"""
	pp_id = _cstr(pp_id).strip()
	if not pp_id:
		return {
			"max_rolls": 0,
			"current_rolls": 0,
			"shift_max_rolls": 0,
			"day_rolls_total": 0,
			"other_shifts_rolls": 0,
			"prior_shifts": [],
			"can_add_roll": False,
			"spr_name": "",
			"job_id": "",
		}

	max_rolls = 0
	current_rolls = 0
	job_id = ""
	spr_name = ""

	pp = frappe.get_doc("Production Plan", pp_id)
	pp_shafts = pp.get("custom_shaft_details") or pp.get("shaft_details") or []
	target_gsm = cint(gsm) if gsm is not None else 0
	target_w = flt(width_inch) if width_inch is not None else 0.0
	for idx, shaft in enumerate(pp_shafts, start=1):
		if target_gsm and _shaft_gsm(shaft) != target_gsm:
			continue
		sw = _shaft_width_inch(shaft)
		comb = _cstr(_pick_value(shaft, ["combination", "combined_width", "shaft", "shaft_details"], ""))
		widths = _parse_combination_widths_inches(comb) if comb else []
		width_match = target_w <= 0 or abs(sw - target_w) < 0.05
		if widths and not width_match:
			width_match = any(abs(flt(w) - target_w) < 0.05 for w in widths)
		if not width_match:
			continue
		no_shafts = max(1, cint(_pick_value(shaft, ["no_of_shafts", "no_of_shaft", "no_of_sh"], 1)))
		rolls_per = max(1, cint(_pick_value(shaft, ["no_of_rolls", "rolls_per_shaft"], 1)))
		segs = max(1, _count_combination_segments(comb) if comb else 1)
		if segs <= 1:
			max_rolls = no_shafts * rolls_per
		else:
			# Per width segment: one roll per shaft (not shafts × segs)
			max_rolls = no_shafts * rolls_per
		job_id = str(idx)
		break

	if run_date:
		run_d = getdate(run_date)
		cur_shift = _cstr(shift).strip() if shift else ""
		spr_filters = {
			"production_plan": pp_id,
			"docstatus": ["<", 2],
			"run_date": run_d,
		}
		if unit:
			spr_filters["custom_unit"] = _cstr(unit).strip()
		sprs = frappe.get_all(
			"Shaft Production Run",
			filters=spr_filters,
			fields=["name", "shift"],
			order_by="modified desc",
			limit=50,
		)
		day_rolls_total = 0
		shift_rolls = 0
		prior_by_shift = {}
		for spr_row in sprs:
			spr = frappe.get_doc("Shaft Production Run", spr_row.name)
			job_row = _match_shaft_job_for_line(
				spr,
				pp_id,
				gsm=gsm,
				width_inch=width_inch,
				production_plan_item=production_plan_item,
			)
			if not job_row:
				continue
			jid = _cstr(getattr(job_row, "job_id", None) or getattr(job_row, "job_no", None))
			if not job_id:
				job_id = jid
			mx = cint(_spr_job_max_roll_lines(job_row, spr))
			if mx > 0:
				max_rolls = max(max_rolls, mx)
			cnt = cint(_spr_count_roll_lines_for_job(spr, jid))
			if cnt <= 0:
				continue
			if not spr_name:
				spr_name = spr_row.name
			day_rolls_total += cnt
			spr_shift = _cstr(getattr(spr, "shift", None) or spr_row.get("shift") or "").strip()
			if cur_shift and spr_shift == cur_shift:
				shift_rolls += cnt
			elif spr_shift:
				prior_by_shift[spr_shift] = prior_by_shift.get(spr_shift, 0) + cnt
			else:
				prior_by_shift["Other"] = prior_by_shift.get("Other", 0) + cnt

		other_shifts_rolls = max(0, day_rolls_total - shift_rolls)
		if not cur_shift:
			shift_rolls = day_rolls_total
			other_shifts_rolls = 0
			prior_by_shift = {}
		shift_max_rolls = max(0, max_rolls - other_shifts_rolls) if max_rolls > 0 else 0
		current_rolls = shift_rolls
		prior_shifts = [
			{"shift": k, "rolls": v} for k, v in sorted(prior_by_shift.items(), key=lambda x: x[0])
		]
		can_add = max_rolls <= 0 or day_rolls_total < max_rolls
	else:
		day_rolls_total = 0
		shift_max_rolls = max_rolls
		other_shifts_rolls = 0
		prior_shifts = []
		spr_name = _find_spr_for_pp(pp_id, prefer_draft=True) or ""
		if spr_name:
			spr = frappe.get_doc("Shaft Production Run", spr_name)
			job_row = _match_shaft_job_for_line(
				spr,
				pp_id,
				gsm=gsm,
				width_inch=width_inch,
				production_plan_item=production_plan_item,
			)
			if job_row:
				job_id = _cstr(getattr(job_row, "job_id", None) or getattr(job_row, "job_no", None))
				mx = cint(_spr_job_max_roll_lines(job_row, spr))
				if mx > 0:
					max_rolls = mx
				current_rolls = cint(_spr_count_roll_lines_for_job(spr, job_id))
				day_rolls_total = current_rolls
				shift_max_rolls = max_rolls
		can_add = max_rolls <= 0 or current_rolls < max_rolls

	return {
		"max_rolls": max_rolls,
		"current_rolls": current_rolls,
		"shift_max_rolls": shift_max_rolls,
		"day_rolls_total": day_rolls_total,
		"other_shifts_rolls": other_shifts_rolls,
		"prior_shifts": prior_shifts,
		"can_add_roll": can_add,
		"spr_name": spr_name,
		"job_id": job_id,
	}


@frappe.whitelist(methods=["GET", "POST"])
def ensure_draft_spr_for_pp(pp_id, planning_sheet_item_names, unit=None, run_date=None, shift=None, force_new=0):
	"""Return draft SPR for PP; create via create_item_spr when missing."""
	pp_id = _cstr(pp_id).strip()
	if isinstance(planning_sheet_item_names, str):
		try:
			planning_sheet_item_names = json.loads(planning_sheet_item_names)
		except Exception:
			planning_sheet_item_names = [
				x.strip() for x in planning_sheet_item_names.split(",") if x.strip()
			]
	if not planning_sheet_item_names:
		frappe.throw(_("Planning Table row name(s) required"))

	if not cint(force_new):
		prefix = ""
		if unit and run_date and shift:
			session = _gsm_open_shift_session_doc(run_date, shift, unit)
			prefix = _cstr(getattr(session, "batch_series_prefix", "") if session else "")
		existing = _find_draft_spr_for_pp(
			pp_id, unit=unit, run_date=run_date, shift=shift, series_prefix=prefix
		)
		if existing:
			if unit or run_date or shift:
				spr = frappe.get_doc("Shaft Production Run", existing)
				if _apply_gsm_session_header_to_spr(spr, run_date, shift, unit):
					spr.save(ignore_permissions=True)
			return {"status": "ok", "spr_name": existing, "reused": 1}

	result = create_item_spr(pp_id, planning_sheet_item_names)
	if isinstance(result, dict):
		if result.get("status") == "ok" and result.get("spr_id"):
			spr_name = result["spr_id"]
			if unit and run_date and shift and frappe.db.exists("Shaft Production Run", spr_name):
				spr = frappe.get_doc("Shaft Production Run", spr_name)
				changed = False
				if unit and _cstr(spr.get("custom_unit")) != _cstr(unit):
					spr.custom_unit = unit
					changed = True
				if run_date and str(spr.get("run_date") or "") != str(run_date):
					spr.run_date = run_date
					changed = True
				if shift and _cstr(spr.get("shift")) != _cstr(shift):
					spr.shift = shift
					changed = True
				if changed:
					spr.save(ignore_permissions=True)
			return {
				"status": "ok",
				"spr_name": spr_name,
				"reused": cint(result.get("reused") or 0),
				"message": result.get("message") or "",
			}
		return result
	if result:
		return {"status": "ok", "spr_name": _cstr(result), "reused": 0}
	return {"status": "error", "message": _("Could not create SPR")}


def _spr_doc_fields(*names):
	"""Return field names that exist on Shaft Production Run (site custom fields vary)."""
	out = []
	for n in names:
		if frappe.db.has_column("Shaft Production Run", n):
			out.append(n)
	return out


def _spr_pick_doc_field(doc, *candidates, default=""):
	for key in candidates:
		if frappe.db.has_column("Shaft Production Run", key):
			val = doc.get(key)
			if val is not None and str(val).strip() != "":
				return _cstr(val)
	return _cstr(default)


def _extract_core_width_mm_from_item(item_name: str, item_code: str = "") -> float:
	"""Best-effort mm from paper-core Item name / custom fields."""
	for key in ("custom_core_width_mm", "core_width_mm", "custom_width_mm"):
		if item_code and frappe.db.has_column("Item", key):
			v = frappe.db.get_value("Item", item_code, key)
			if v and flt(v) > 0:
				return flt(v)
	name = _cstr(item_name)
	import re

	m = re.search(r"(\d{3,4})\s*mm", name, re.I)
	if m:
		return flt(m.group(1))
	m = re.search(r'(\d+(?:\.\d+)?)\s*["\u201d]', name)
	if m:
		# Inch label on core item — common stock sizes map to mm presets
		inch = flt(m.group(1))
		preset = {63: 1500, 85: 1600, 90: 1700, 118: 1800, 126: 1900}
		for k, mm in preset.items():
			if abs(inch - k) < 0.6:
				return float(mm)
	return 1600.0


def _fabric_width_to_stock_core_inch(width_inch: float) -> float:
	"""Map fabric roll width to stock core diameter (inches) — SPR desk parity."""
	w = flt(width_inch)
	if w <= 0:
		return 63.0
	for k in (63, 85, 90, 118, 126):
		if abs(w - k) < 0.6:
			return float(k)
	if w < 63:
		return 63.0
	if w < 85:
		return 85.0
	if w < 90:
		return 90.0
	if w < 118:
		return 118.0
	return 126.0


def _core_size_meta_fieldnames() -> dict:
	"""Best-effort Core Size field names (doctype may vary by site)."""
	meta = frappe.get_meta("Core Size") if frappe.db.table_exists("Core Size") else None
	if not meta:
		return {}
	out = {}
	for fn in ("core_inch", "core", "core_inches", "core_width_inch"):
		if meta.has_field(fn):
			out["inch"] = fn
			break
	for fn in ("item_code",):
		if meta.has_field(fn):
			out["item_code"] = fn
			break
	for fn in ("base_weight_kgs", "base_weight", "base_weight_kg"):
		if meta.has_field(fn):
			out["base_weight"] = fn
			break
	return out


def _parse_core_inch_from_name(name: str) -> float:
	import re

	m = re.search(r"(\d+(?:\.\d+)?)", _cstr(name))
	return flt(m.group(1)) if m else 0.0


def _core_size_name_for_inch(core_inch: float) -> str:
	"""Format Core Size document name (e.g. 63\")."""
	ci = flt(core_inch)
	if ci <= 0:
		return ""
	if abs(ci - round(ci)) < 0.01:
		return f'{int(round(ci))}"'
	return f'{ci}"'


def _resolve_core_size_for_fabric_width(width_inch: float) -> dict:
	"""Resolve Core Size master row for a fabric roll width."""
	target_inch = _fabric_width_to_stock_core_inch(width_inch)
	default = {
		"core_size": "",
		"core_inch": target_inch,
		"item_code": "",
		"base_weight_kgs": 0.0,
		"width_mm": _preset_core_mm_for_fabric_width(target_inch),
		"label": _core_size_name_for_inch(target_inch),
	}
	if not frappe.db.table_exists("Core Size"):
		item_code = _resolve_core_item_code_for_mm(default["width_mm"])
		if item_code:
			default["item_code"] = item_code
		return default

	fields = _core_size_meta_fieldnames()
	select_fields = ["name"]
	for key in ("inch", "item_code", "base_weight"):
		fn = fields.get(key)
		if fn and fn not in select_fields:
			select_fields.append(fn)

	candidates = [_core_size_name_for_inch(target_inch)]
	if abs(target_inch - round(target_inch)) >= 0.01:
		candidates.append(f'{target_inch:g}"')

	for name in candidates:
		if name and frappe.db.exists("Core Size", name):
			row = frappe.get_doc("Core Size", name)
			inch = target_inch
			if fields.get("inch"):
				inch = flt(row.get(fields["inch"])) or inch
			if inch <= 0:
				inch = _parse_core_inch_from_name(name) or target_inch
			return {
				"core_size": name,
				"core_inch": inch,
				"item_code": _cstr(row.get(fields.get("item_code") or "item_code") or "").strip(),
				"base_weight_kgs": flt(row.get(fields.get("base_weight") or "base_weight_kgs") or 0),
				"width_mm": _preset_core_mm_for_fabric_width(inch),
				"label": name,
			}

	inch_field = fields.get("inch")
	if inch_field:
		rows = frappe.get_all(
			"Core Size",
			filters={inch_field: target_inch},
			fields=select_fields,
			limit=1,
		)
		if rows:
			row = rows[0]
			name = _cstr(row.get("name")).strip()
			inch = flt(row.get(inch_field)) or target_inch
			return {
				"core_size": name,
				"core_inch": inch,
				"item_code": _cstr(row.get(fields.get("item_code") or "item_code") or "").strip(),
				"base_weight_kgs": flt(row.get(fields.get("base_weight") or "base_weight_kgs") or 0),
				"width_mm": _preset_core_mm_for_fabric_width(inch),
				"label": name or _core_size_name_for_inch(inch),
			}

	item_code = _resolve_core_item_code_for_mm(default["width_mm"])
	if item_code:
		default["item_code"] = item_code
	return default


def _preset_core_mm_for_fabric_width(width_inch: float) -> float:
	"""Map fabric roll width (inches) to stock core mm — SPR desk parity."""
	w = flt(width_inch)
	if w <= 0:
		return 1600.0
	preset = {63: 1500.0, 85: 1600.0, 90: 1700.0, 118: 1800.0, 126: 1900.0}
	for k, mm in preset.items():
		if abs(w - k) < 0.6:
			return mm
	if w < 63:
		return 1500.0
	if w < 85:
		return 1600.0
	if w < 90:
		return 1700.0
	if w < 118:
		return 1800.0
	return 1900.0


def _resolve_core_item_code_for_mm(mm: float) -> str:
	"""Paper-core Item name/code for a preset mm width (SPR Link field on roll lines)."""
	mm = flt(mm)
	if mm <= 0:
		return ""
	for row in get_gsm_core_width_options():
		if abs(flt(row.get("width_mm")) - mm) < 0.01:
			return _cstr(row.get("item_code")).strip()
	# Closest preset
	best = ""
	best_diff = 1e9
	for row in get_gsm_core_width_options():
		wm = flt(row.get("width_mm"))
		if wm <= 0:
			continue
		diff = abs(wm - mm)
		if diff < best_diff:
			best_diff = diff
			best = _cstr(row.get("item_code")).strip()
	return best


def _gsm_core_width_value_for_spr_item(payload: dict) -> str:
	"""Map GSM payload core width to SPR Item custom_core_width_mm (Link or numeric field)."""
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	if not spi_meta.has_field("custom_core_width_mm"):
		return ""
	df = spi_meta.get_field("custom_core_width_mm")
	raw = payload.get("custom_core_width_mm")
	if raw in (None, ""):
		width_inch = flt(payload.get("width_inch") or 0)
		if width_inch > 0:
			return _gsm_resolve_core_link_for_fabric_width(width_inch, "")
		return ""
	if df.fieldtype == "Link":
		link_dt = _cstr(df.options or "Item").strip() or "Item"
		s = _cstr(raw).strip()
		if frappe.db.exists(link_dt, s):
			return s
		width_inch = flt(payload.get("width_inch") or 0)
		if width_inch > 0:
			return _gsm_resolve_core_link_for_fabric_width(width_inch, s)
		# Numeric mm from GSM grid — resolve to paper-core Item (legacy)
		try:
			mm = flt(s)
			if mm > 0:
				code = _resolve_core_item_code_for_mm(mm)
				if code and frappe.db.exists(link_dt, code):
					return code
		except Exception:
			pass
		return ""
	return _cstr(raw).strip() if df.fieldtype in ("Data", "Small Text") else _cstr(flt(raw))


def _normalize_pp_shaft_job_row(shaft) -> frappe._dict:
	"""Align PP shaft detail field names with Shaft Production Run Job for weight helpers."""
	if not shaft:
		return frappe._dict()
	if isinstance(shaft, dict):
		row = frappe._dict(shaft)
	elif hasattr(shaft, "as_dict"):
		row = frappe._dict(shaft.as_dict())
	else:
		row = frappe._dict()
	if not _cstr(row.get("net_weight") or "").strip():
		nw = _pick_value(
			row,
			["net_weight_shaft_kgs", "net_weight_shaft", "custom_net_weight_shaft_kgs", "net_weight"],
			"",
		)
		if nw:
			row.net_weight = nw
	if not flt(row.get("total_weight")):
		tw = _pick_value(row, ["total_weight_kgs", "total_weight", "weight", "planned_qty"], 0)
		if tw:
			row.total_weight = tw
	return row


def _gsm_pp_net_weight_fallback(pp) -> float:
	"""PP-level net / weight-per-roll fallback (SPR create-entry parity)."""
	return flt(
		_pick_value(
			pp,
			[
				"custom_net_weight",
				"net_weight",
				"custom_net_weight_kgs",
				"net_weight_kgs",
				"custom_weight_per_roll",
				"weight_per_roll",
			],
			0,
		)
	)


def _gsm_resolve_pp_shaft_net_weight(pp, shaft_row, shaft_idx: int = 1):
	"""Resolve shaft net weight for GSM Shaft Details — same chain as planned qty."""
	row = _normalize_pp_shaft_job_row(shaft_row)
	nw = flt(row.get("net_weight") or 0)
	if nw > 0:
		return round(nw, 2)

	pp_net = _gsm_pp_net_weight_fallback(pp)
	if pp_net > 0:
		return round(pp_net, 2)

	pp_po_items = pp.get("po_items") or []
	if shaft_idx > 0 and shaft_idx <= len(pp_po_items):
		poi_nw = flt(
			_pick_value(
				pp_po_items[shaft_idx - 1],
				["net_weight", "weight_per_roll", "weight_roll", "stock_qty"],
				0,
			)
		)
		if poi_nw > 0:
			return round(poi_nw, 2)

	comb = _cstr(_pick_value(row, ["combination", "combined_width", "shaft", "shaft_details"], ""))
	segs = max(1, _count_combination_segments(comb))
	weights = _segment_weights_kg(row, segs)
	positive = [flt(w) for w in weights if flt(w) > 0]
	if not positive:
		return 0.0
	if len(positive) == 1 or (len(set(round(w, 2) for w in positive)) == 1):
		return round(positive[0], 2)
	parts = []
	for w in positive:
		parts.append(str(int(w)) if abs(w - int(w)) < 0.01 else f"{w:.2f}".rstrip("0").rstrip("."))
	return "+".join(parts)


def _planned_qty_kg_from_pp_shaft(
	pp_id: str,
	width_inch=None,
	gsm=None,
	production_plan_item=None,
	job_id=None,
) -> float:
	"""Per-roll planned kg from PP shaft net weight (SPR Create Entry parity), not GSM formula.

	Maps width → segment net weight within the selected job (e.g. 33→32kg, 35→50kg).
	When two jobs share the same width, ``job_id`` / ``gsm`` pick the correct job's net weight.
	"""
	pp_id = _cstr(pp_id).strip()
	wx = flt(width_inch)
	if not pp_id or wx <= 0:
		return 0.0
	ppi = _cstr(production_plan_item).strip()
	jid = _cstr(job_id).strip()
	target_gsm = cint(gsm) if gsm is not None and _cstr(gsm) != "" else 0

	spr_name = _find_spr_for_pp(pp_id, prefer_draft=True)
	if spr_name:
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		net_ps, _matched_jid = _spr_net_kg_per_shaft_for_pp_line_width(
			spr,
			wx,
			ppi or None,
			job_id=jid or None,
			gsm=target_gsm or None,
		)
		if net_ps is not None and flt(net_ps) > 0:
			return round(flt(net_ps), 3)

	if not frappe.db.exists("Production Plan", pp_id):
		return 0.0
	pp = frappe.get_doc("Production Plan", pp_id)
	shafts = list(pp.get("custom_shaft_details") or pp.get("shaft_details") or [])

	def _pp_row_job_id(row, idx: int) -> str:
		return _cstr(_pick_value(row, ["job_id", "job", "job_no"], str(idx)))

	ordered = []
	for idx, shaft in enumerate(shafts, start=1):
		row = _normalize_pp_shaft_job_row(shaft)
		ordered.append((idx, row))

	# Prefer exact job, then gsm match, then all
	if jid:
		pref = [(i, r) for i, r in ordered if _spr_job_keys_match(_pp_row_job_id(r, i), jid)]
		if pref:
			ordered = pref
	if target_gsm:
		pref = [(i, r) for i, r in ordered if _shaft_gsm(r) == target_gsm]
		if pref:
			ordered = pref

	for _idx, row in ordered:
		comb = _cstr(_pick_value(row, ["combination", "combined_width", "shaft", "shaft_details"], ""))
		segs = max(1, _count_combination_segments(comb))
		widths = _parse_combination_widths_inches(comb) if comb else []
		weights = _segment_weights_kg(row, segs)
		if segs > 1 and len(widths) >= segs:
			for i in range(segs):
				if abs(flt(widths[i]) - wx) <= 0.75 and i < len(weights) and flt(weights[i]) > 0:
					return round(flt(weights[i]), 3)
			continue
		tw = flt(row.get("total_width"))
		if tw > 0 and abs(tw - wx) <= 0.75 and weights:
			return round(flt(weights[0]), 3)
		if weights and flt(weights[0]) > 0:
			sw = _shaft_width_inch(row)
			if sw <= 0 or abs(sw - wx) <= 0.75:
				return round(flt(weights[0]), 3)
	return 0.0


def _resolve_core_mm_for_fabric_width(width_inch: float) -> float:
	"""Pick paper-core mm from Item master for fabric width (inch label or closest preset)."""
	import re

	w = flt(width_inch)
	target_mm = _preset_core_mm_for_fabric_width(w)
	rows = frappe.db.sql(
		"""
		SELECT name, item_name
		FROM `tabItem`
		WHERE disabled = 0
		  AND (item_name LIKE %s OR item_name LIKE %s OR name LIKE %s)
		ORDER BY item_name
		LIMIT 200
		""",
		('%" PC%', '% PC -%', '%PC%'),
		as_dict=True,
	)
	w_label = str(int(w)) if abs(w - round(w)) < 0.01 else f"{w:.1f}".rstrip("0").rstrip(".")
	for row in rows or []:
		name = _cstr(row.get("item_name") or "")
		if re.match(rf"^{re.escape(w_label)}\s*['\"\u201d″]", name):
			mm = _extract_core_width_mm_from_item(name, row.get("name") or "")
			if mm > 0:
				return mm
	best_mm = target_mm
	best_diff = 999999.0
	for row in rows or []:
		mm = _extract_core_width_mm_from_item(row.get("item_name") or "", row.get("name") or "")
		if mm <= 0:
			continue
		diff = abs(mm - target_mm)
		if diff < best_diff:
			best_diff = diff
			best_mm = mm
	return flt(best_mm) if best_mm > 0 else target_mm


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_roll_row_extras(
	gsm=None,
	width_inch=None,
	length_m=None,
	item_code=None,
	pp_id=None,
	production_plan_item=None,
	job_id=None,
):
	"""Planned qty (PP shaft net kg) + polybag + auto core mm for a GSM roll line."""
	gsm_val = cint(gsm) if gsm is not None else 0
	w_in = flt(width_inch) if width_inch is not None else 0.0
	ln = flt(length_m) if length_m is not None else 0.0
	item_code = _cstr(item_code).strip()
	pp_id = _cstr(pp_id).strip()
	ppi = _cstr(production_plan_item).strip()
	jid = _cstr(job_id).strip()
	if not gsm_val and item_code:
		try:
			g, w = parse_item_code(item_code)
			if g > 0:
				gsm_val = int(g)
			if w > 0 and w_in <= 0:
				w_in = flt(w)
		except Exception:
			pass

	planned_qty = 0.0
	if pp_id and w_in > 0:
		planned_qty = _planned_qty_kg_from_pp_shaft(
			pp_id, w_in, gsm_val, ppi or None, job_id=jid or None
		)
	if planned_qty <= 0 and gsm_val and w_in > 0 and ln > 0:
		planned_qty = compute_mix_roll_planned_qty_kg(gsm_val, w_in, ln)

	core_mm = _resolve_core_mm_for_fabric_width(w_in) if w_in > 0 else 1600.0
	core_info = _resolve_core_size_for_fabric_width(w_in) if w_in > 0 else {}
	core_size = _cstr(core_info.get("core_size")).strip()
	core_item = core_size or _resolve_core_item_code_for_mm(core_mm)
	polybag = 0.0
	if item_code and frappe.db.exists("Item", item_code):
		for key in ("custom_polybag_kgs", "polybag_kgs", "custom_polybag_weight"):
			if frappe.db.has_column("Item", key):
				polybag = flt(frappe.db.get_value("Item", item_code, key) or 0)
				if polybag > 0:
					break
	return {
		"planned_qty": planned_qty,
		"custom_polybag_kgs": polybag,
		"custom_core_width_mm": core_item or "",
		"core_width_mm": core_mm,
		"core_size": core_size,
		"core_inch": flt(core_info.get("core_inch") or 0),
		"core_label": _cstr(core_info.get("label") or core_size).strip(),
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_core_width_options():
	"""Core Size options for GSM grid (falls back to paper-core Items when Core Size absent)."""
	if frappe.db.table_exists("Core Size"):
		fields = _core_size_meta_fieldnames()
		select_fields = ["name"]
		for key in ("inch", "item_code", "base_weight"):
			fn = fields.get(key)
			if fn and fn not in select_fields:
				select_fields.append(fn)
		rows = frappe.get_all("Core Size", fields=select_fields, order_by="name", limit=200)
		out = []
		seen = set()
		for row in rows or []:
			name = _cstr(row.get("name")).strip()
			if not name:
				continue
			inch_field = fields.get("inch")
			inch = flt(row.get(inch_field)) if inch_field else 0.0
			if inch <= 0:
				inch = _parse_core_inch_from_name(name)
			mm = _preset_core_mm_for_fabric_width(inch) if inch > 0 else 1600.0
			out.append(
				{
					"value": name,
					"core_size": name,
					"item_code": _cstr(row.get(fields.get("item_code") or "item_code") or "").strip(),
					"label": name,
					"width_mm": mm,
					"core_inch": inch,
					"base_weight_kgs": flt(row.get(fields.get("base_weight") or "base_weight_kgs") or 0),
				}
			)
			seen.add(name)
		if out:
			out.sort(key=lambda x: (flt(x.get("core_inch")), x.get("label") or ""))
			return out

	rows = frappe.db.sql(
		"""
		SELECT name, item_name
		FROM `tabItem`
		WHERE disabled = 0
		  AND (item_name LIKE %s OR item_name LIKE %s OR name LIKE %s)
		ORDER BY item_name
		LIMIT 200
		""",
		('%" PC%', '% PC -%', '%PC%'),
		as_dict=True,
	)
	out = []
	seen_mm = set()
	for row in rows or []:
		mm = _extract_core_width_mm_from_item(row.get("item_name") or "", row.get("name") or "")
		label = _cstr(row.get("item_name") or row.get("name"))
		out.append({"item_code": row.get("name"), "label": label, "width_mm": mm})
		seen_mm.add(int(mm))
	# Standard fallbacks when Item master has no paper-core rows
	for mm in (1500, 1600, 1700, 1800, 1900):
		if mm not in seen_mm:
			out.append({"item_code": "", "label": f"{mm} mm", "width_mm": mm})
			seen_mm.add(mm)
	out.sort(key=lambda x: (flt(x.get("width_mm")), x.get("label") or ""))
	return out


def _gsm_resolve_core_link_for_fabric_width(width_inch: float, raw_value: str = "") -> str:
	"""Resolve SPR custom_core_width_mm link value from Core Size master or legacy Item."""
	raw = _cstr(raw_value).strip()
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	if not spi_meta.has_field("custom_core_width_mm"):
		return raw
	df = spi_meta.get_field("custom_core_width_mm")
	link_dt = _cstr(df.options or "Item").strip() or "Item"
	if raw and frappe.db.exists(link_dt, raw):
		return raw
	core_info = _resolve_core_size_for_fabric_width(flt(width_inch))
	core_size = _cstr(core_info.get("core_size")).strip()
	if link_dt == "Core Size" and core_size and frappe.db.exists("Core Size", core_size):
		return core_size
	if df.fieldtype == "Link" and link_dt == "Item":
		item_code = _cstr(core_info.get("item_code")).strip()
		if item_code and frappe.db.exists("Item", item_code):
			return item_code
		mm = flt(core_info.get("width_mm") or 0)
		if mm > 0:
			code = _resolve_core_item_code_for_mm(mm)
			if code and frappe.db.exists("Item", code):
				return code
	return raw


def _gsm_core_info_from_link(core_link: str, width_inch: float = 0) -> dict:
	"""Resolve Core Size master row from link name / item code / GSM option value."""
	core_link = _cstr(core_link).strip()
	if core_link:
		for row in get_gsm_core_width_options():
			if core_link in (
				_cstr(row.get("core_size")),
				_cstr(row.get("value")),
				_cstr(row.get("item_code")),
			):
				return {
					"core_size": row.get("core_size") or row.get("value"),
					"core_inch": flt(row.get("core_inch")),
					"base_weight_kgs": flt(row.get("base_weight_kgs")),
				}
		if frappe.db.table_exists("Core Size") and frappe.db.exists("Core Size", core_link):
			row = frappe.get_doc("Core Size", core_link)
			fields = _core_size_meta_fieldnames()
			inch_field = fields.get("inch")
			inch = flt(row.get(inch_field)) if inch_field else _parse_core_inch_from_name(core_link)
			bw_field = fields.get("base_weight") or "base_weight_kgs"
			return {
				"core_size": core_link,
				"core_inch": inch,
				"base_weight_kgs": flt(row.get(bw_field) or 0),
			}
	if flt(width_inch) > 0:
		return _resolve_core_size_for_fabric_width(flt(width_inch))
	return {"core_size": core_link, "core_inch": 0.0, "base_weight_kgs": 0.0}


def _gsm_calc_roll_net_weight_kg(gross_kg, width_inch, core_link, polybag_kgs=0) -> float:
	"""Desk SPR parity: deduct (width/core_inch)*base_weight and polybag from gross."""
	gw = flt(gross_kg)
	wi = flt(width_inch)
	if gw <= 0 or wi <= 0:
		return 0.0
	info = _gsm_core_info_from_link(core_link, wi)
	core_inch = flt(info.get("core_inch"))
	base_bw = flt(info.get("base_weight_kgs"))
	if core_inch > 0 and base_bw > 0:
		core_weight = (wi / core_inch) * base_bw
	else:
		core_weight = 0.0
	poly = flt(polybag_kgs)
	net = gw - core_weight - poly
	if net <= 0:
		net = gw - poly if gw > poly else gw
	return flt(net, 2)


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_shift_submitted_entries(run_date, shift, unit=None):
	"""Submitted SPRs for a shift — GSM admin Shift Entries tab."""
	run_date = getdate(run_date)
	shift = _cstr(shift).strip()
	filters = {"docstatus": 1, "run_date": run_date}
	if shift:
		filters["shift"] = shift
	if unit:
		filters["custom_unit"] = unit

	list_fields = [
		"name",
		"production_plan",
		"run_date",
		"shift",
		"custom_unit",
		"modified",
	]
	list_fields.extend(
		_spr_doc_fields(
			"operator",
			"supervisor",
			"custom_operator",
			"custom_supervisor",
			"custom_shift_operator",
			"custom_shift_supervisor",
		)
	)
	list_fields = list(dict.fromkeys(list_fields))

	sprs = frappe.get_all(
		"Shaft Production Run",
		filters=filters,
		fields=list_fields,
		order_by="modified desc",
		limit=100,
	)
	return [_gsm_build_shift_spr_entry(frappe.get_doc("Shaft Production Run", row.name)) for row in sprs]


def _gsm_roll_batch_series_prefix(batch_no: str) -> str:
	bn = _cstr(batch_no).strip()
	if not bn:
		return ""
	if "/" in bn:
		return bn.rsplit("/", 1)[0].strip()
	return bn


def _gsm_roll_number_from_batch(batch_no: str) -> int:
	"""Roll suffix after ``/`` in batch id (e.g. JS-0305264/2 → 2)."""
	bn = _cstr(batch_no).strip()
	if not bn or "/" not in bn:
		return 0
	try:
		return cint(bn.rsplit("/", 1)[-1].strip())
	except Exception:
		return 0


def _gsm_build_shift_spr_entry(spr) -> dict:
	"""Serialize one SPR for GSM shift views (submitted or draft)."""
	real_rolls = [it for it in (spr.get("items") or []) if _spr_is_real_roll_item_row(it)]
	total_net = sum(flt(getattr(it, "net_weight", 0)) for it in real_rolls)
	total_gross = sum(flt(getattr(it, "gross_weight", 0)) for it in real_rolls)
	order_codes = set()
	for it in real_rolls:
		oc = _cstr(getattr(it, "party_code", None) or getattr(it, "custom_order_code", None))
		if oc:
			order_codes.add(oc)
	if not order_codes:
		oc_hdr = _cstr(spr.get("custom_order_code") or "")
		if oc_hdr:
			order_codes.add(oc_hdr)
	wo_status = []
	seen_wo = set()
	for sj in _spr_job_rows(spr):
		wos_raw = _cstr(getattr(sj, "work_orders", None) or getattr(sj, "work_order", None))
		for part in wos_raw.replace("\n", ",").split(","):
			wn = part.strip()
			if not wn or wn in seen_wo:
				continue
			seen_wo.add(wn)
			st = frappe.db.get_value("Work Order", wn, "status") or ""
			wo_status.append({"name": wn, "status": st})
	roll_rows = []
	for it in real_rolls:
		roll_rows.append(
			{
				"batch_no": _cstr(getattr(it, "batch_no", None)),
				"gsm": getattr(it, "gsm", None) or getattr(it, "custom_fabric_gsm", None),
				"width_inch": getattr(it, "width_inch", None) or getattr(it, "custom_width_inch", None),
				"net_weight": flt(getattr(it, "net_weight", 0)),
				"gross_weight": flt(getattr(it, "gross_weight", 0)),
				"party_code": _cstr(getattr(it, "party_code", None)),
				"work_order": _cstr(getattr(it, "work_order", None)),
			}
		)
	docstatus = cint(spr.docstatus)
	return {
		"spr_name": spr.name,
		"production_plan": spr.get("production_plan"),
		"run_date": str(spr.get("run_date") or ""),
		"shift": spr.get("shift") or "",
		"unit": spr.get("custom_unit") or "",
		"operator": _spr_pick_doc_field(
			spr,
			"operator",
			"custom_operator",
			"custom_shift_operator",
		),
		"supervisor": _spr_pick_doc_field(
			spr,
			"supervisor",
			"custom_supervisor",
			"custom_shift_supervisor",
		),
		"docstatus": docstatus,
		"spr_status": "Submitted" if docstatus == 1 else "Draft",
		"roll_count": len(real_rolls),
		"total_net_kg": round(total_net, 2),
		"total_gross_kg": round(total_gross, 2),
		"order_codes": sorted(order_codes),
		"wo_status": wo_status,
		"rolls": roll_rows,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_shift_consolidated_summary(run_date, shift, unit=None):
	"""Consolidated shift production — submitted + draft SPRs, session status, aggregates."""
	run_date = getdate(run_date)
	shift = _normalize_gsm_shift_label(shift)
	unit = _cstr(unit).strip()

	filters = {"run_date": run_date, "docstatus": ["<", 2]}
	if shift:
		filters["shift"] = shift
	if unit:
		filters["custom_unit"] = unit

	spr_names = frappe.get_all(
		"Shaft Production Run",
		filters=filters,
		pluck="name",
		order_by="modified desc",
		limit=200,
	)
	spr_list = []
	roll_lines = []
	roll_lines_truncated = False
	_max_roll_lines = 500
	submitted_count = draft_count = 0
	total_rolls = 0
	total_net = total_gross = 0.0
	by_order = {}
	by_gsm = {}
	by_batch_series = {}

	for spr_name in spr_names or []:
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		entry = _gsm_build_shift_spr_entry(spr)
		spr_list.append(entry)
		if entry["spr_status"] == "Submitted":
			submitted_count += 1
		else:
			draft_count += 1
		total_rolls += cint(entry.get("roll_count") or 0)
		total_net += flt(entry.get("total_net_kg") or 0)
		total_gross += flt(entry.get("total_gross_kg") or 0)

		order_key = ", ".join(entry.get("order_codes") or []) or "—"
		if order_key not in by_order:
			by_order[order_key] = {
				"order_codes": entry.get("order_codes") or [],
				"spr_name": entry["spr_name"],
				"spr_status": entry["spr_status"],
				"rolls": 0,
				"net_kg": 0.0,
				"gross_kg": 0.0,
				"wo_status": entry.get("wo_status") or [],
			}
		by_order[order_key]["rolls"] += cint(entry.get("roll_count") or 0)
		by_order[order_key]["net_kg"] += flt(entry.get("total_net_kg") or 0)
		by_order[order_key]["gross_kg"] += flt(entry.get("total_gross_kg") or 0)

		for roll in entry.get("rolls") or []:
			gsm_key = _cstr(roll.get("gsm") or "—")
			if gsm_key not in by_gsm:
				by_gsm[gsm_key] = {"gsm": gsm_key, "rolls": 0, "net_kg": 0.0}
			by_gsm[gsm_key]["rolls"] += 1
			by_gsm[gsm_key]["net_kg"] += flt(roll.get("net_weight") or 0)
			prefix = _gsm_roll_batch_series_prefix(roll.get("batch_no"))
			if prefix:
				if prefix not in by_batch_series:
					by_batch_series[prefix] = {"batch_series": prefix, "rolls": 0, "net_kg": 0.0}
				by_batch_series[prefix]["rolls"] += 1
				by_batch_series[prefix]["net_kg"] += flt(roll.get("net_weight") or 0)

		if not roll_lines_truncated:
			for grid_row in _gsm_serialize_spr_roll_lines_for_grid(spr):
				if len(roll_lines) >= _max_roll_lines:
					roll_lines_truncated = True
					break
				roll_lines.append(
					{
						**grid_row,
						"spr_name": entry["spr_name"],
						"spr_status": entry["spr_status"],
						"operator": entry.get("operator") or "",
						"supervisor": entry.get("supervisor") or "",
						"run_date": str(run_date),
						"shift": shift,
						"unit": unit,
					}
				)

	shift_session = None
	if _gsm_shift_session_table_exists() and unit and shift:
		sess_filters = {
			"run_date": run_date,
			"shift": shift,
			"custom_unit": unit,
		}
		open_name = frappe.db.get_value(
			_GSM_SHIFT_SESSION_DOCTYPE,
			{**sess_filters, "status": "Open"},
			"name",
		)
		if open_name:
			shift_session = _serialize_gsm_shift_session(
				frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, open_name)
			)
		else:
			closed = frappe.get_all(
				_GSM_SHIFT_SESSION_DOCTYPE,
				filters={**sess_filters, "status": "Closed"},
				fields=["name"],
				order_by="modified desc",
				limit=1,
			)
			if closed:
				shift_session = _serialize_gsm_shift_session(
					frappe.get_doc(_GSM_SHIFT_SESSION_DOCTYPE, closed[0].name)
				)

	session_status = "Not started"
	if shift_session:
		session_status = shift_session.get("status") or "Closed"

	roll_lines.sort(key=lambda r: _cstr(r.get("batch_no")))

	return {
		"run_date": str(run_date),
		"shift": shift,
		"unit": unit,
		"shift_session": shift_session,
		"session_status": session_status,
		"totals": {
			"spr_count": len(spr_list),
			"submitted_spr_count": submitted_count,
			"draft_spr_count": draft_count,
			"roll_count": total_rolls,
			"net_kg": round(total_net, 2),
			"gross_kg": round(total_gross, 2),
		},
		"by_order": sorted(
			[
				{
					**row,
					"net_kg": round(flt(row["net_kg"]), 2),
					"gross_kg": round(flt(row["gross_kg"]), 2),
				}
				for row in by_order.values()
			],
			key=lambda r: _cstr(next(iter(r.get("order_codes") or []), "")),
		),
		"by_gsm": sorted(
			[{**row, "net_kg": round(flt(row["net_kg"]), 2)} for row in by_gsm.values()],
			key=lambda r: _cstr(r.get("gsm")),
		),
		"by_batch_series": sorted(
			[{**row, "net_kg": round(flt(row["net_kg"]), 2)} for row in by_batch_series.values()],
			key=lambda r: _cstr(r.get("batch_series")),
		),
		"spr_list": spr_list,
		"roll_lines": roll_lines,
		"roll_lines_truncated": roll_lines_truncated,
	}


def _parse_json_arg(val, default=None):
	if val is None:
		return default if default is not None else []
	if isinstance(val, str):
		try:
			return json.loads(val)
		except Exception:
			return default if default is not None else []
	return val


def _gsm_as_selection_list(val) -> list:
	"""Normalize frappe.call JSON / form-dict selections into a list of rows."""
	parsed = _parse_json_arg(val, [])
	if parsed is None:
		return []
	if isinstance(parsed, dict):
		if parsed and all(str(k).isdigit() for k in parsed.keys()):
			return [parsed[k] for k in sorted(parsed.keys(), key=lambda x: int(x))]
		return [parsed]
	if isinstance(parsed, list):
		return parsed
	return [parsed] if parsed else []


def _gsm_coerce_patty_selection(sel) -> dict:
	if sel is None:
		return {}
	if isinstance(sel, str):
		s = sel.strip()
		if not s:
			return {}
		if s[:1] in "{[":
			parsed = _parse_json_arg(s, None)
			if isinstance(parsed, dict):
				return dict(parsed)
			if isinstance(parsed, list) and parsed:
				return _gsm_coerce_patty_selection(parsed[0])
			return {}
		return {"batch_no": s, "name": s}
	if hasattr(sel, "as_dict"):
		sel = sel.as_dict()
	return dict(sel) if isinstance(sel, dict) else {}


def _gsm_is_real_batch_no(val) -> bool:
	"""True for Batch names like JS-0208267W/1 — not Frappe child hashes (d4oa1av77v)."""
	s = _cstr(val).strip()
	if not s:
		return False
	if "/" in s:
		return True
	if s.lower() == s and s.isalnum() and 8 <= len(s) <= 12:
		return False
	try:
		return bool(frappe.db.exists("Batch", s))
	except Exception:
		return False


def _gsm_lookup_patty_stock_doctype(batch_no="", name=""):
	"""Load one Patty Stock row by batch_no or document name."""
	if not frappe.db.exists("DocType", "Patty Stock"):
		return None
	meta = frappe.get_meta("Patty Stock")
	fields = ["name"]
	for fn in (
		"batch_no",
		"item_code",
		"item_name",
		"quality",
		"colour",
		"color",
		"gsm",
		"width_inch",
		"balance_quantity",
		"meter_roll_mtrs",
		"no_of_shafts",
	):
		if meta.has_field(fn):
			fields.append(fn)
	if batch_no and meta.has_field("batch_no"):
		rows = frappe.get_all("Patty Stock", filters={"batch_no": batch_no}, fields=fields, limit=1)
		if rows:
			return rows[0]
	name = _cstr(name).strip()
	if name and frappe.db.exists("Patty Stock", name):
		return frappe.db.get_value("Patty Stock", name, fields, as_dict=True)
	return None


def _gsm_hydrate_patty_selection(sel) -> dict:
	"""Fill available_kg / width / specs from Patty Stock when the client payload omitted them."""
	out = _gsm_coerce_patty_selection(sel)
	batch_no = _cstr(_pick_value(out, ["batch_no", "batch", "source_roll"], "")).strip()
	if not _gsm_is_real_batch_no(batch_no):
		maybe_name = _cstr(out.get("name") or "").strip()
		batch_no = maybe_name if _gsm_is_real_batch_no(maybe_name) else ""
	if batch_no:
		out["batch_no"] = batch_no
		out["source_roll"] = batch_no
	else:
		out.pop("batch_no", None)
		if not _gsm_is_real_batch_no(_cstr(out.get("source_roll") or "")):
			out.pop("source_roll", None)

	qty_keys = (
		"available_kg",
		"available",
		"available_qty",
		"qty",
		"balance_quantity",
		"batch_qty",
		"actual_qty",
		"wastage",
		"wastage_qty",
		"net_wastage",
	)
	qty = flt(_pick_value(out, qty_keys, 0))
	width = flt(
		_pick_value(
			out,
			[
				"width_inch",
				"width",
				"w",
				"custom_width_inch",
				"custom_width",
				"trim_width",
				"patty_width",
			],
			0,
		)
	)
	quality = _cstr(_pick_value(out, ["quality"], ""))
	color = _cstr(_pick_value(out, ["color", "colour"], ""))
	gsm = cint(_pick_value(out, ["gsm"], 0))
	item_code = _cstr(_pick_value(out, ["item_code", "item"], ""))

	if (qty <= 0 or width <= 0 or not quality or not color or gsm <= 0) and (
		batch_no or _cstr(out.get("name") or out.get("patty_stock") or "")
	):
		ps = _gsm_lookup_patty_stock_doctype(batch_no, out.get("name") or out.get("patty_stock"))
		if ps:
			if not item_code:
				item_code = _cstr(ps.get("item_code") or "")
			if width <= 0:
				width = flt(ps.get("width_inch") or 0)
			if not quality:
				quality = _cstr(ps.get("quality") or "")
			if not color:
				color = _cstr(ps.get("colour") or ps.get("color") or "")
			if gsm <= 0:
				gsm = cint(ps.get("gsm") or 0)
			if qty <= 0:
				qty = flt(ps.get("balance_quantity") or 0)
			if not batch_no:
				batch_no = _cstr(ps.get("batch_no") or "")
				if batch_no:
					out["batch_no"] = batch_no
					out["source_roll"] = batch_no

	out["available_kg"] = qty
	out["available"] = qty
	out["available_qty"] = qty
	if width > 0:
		out["width_inch"] = width
		out["width"] = width
	if quality:
		out["quality"] = quality
	if color:
		out["color"] = color
	if gsm > 0:
		out["gsm"] = gsm
	if item_code:
		out["item_code"] = item_code

	if width <= 0 or qty <= 0 or not batch_no:
		for stock in _gsm_patty_stock_rows_cached():
			stock_batch = _cstr(stock.get("batch_no") or stock.get("name") or "").strip()
			if batch_no and stock_batch and stock_batch != batch_no:
				continue
			if not batch_no:
				if gsm and cint(stock.get("gsm") or 0) != gsm:
					continue
				if quality and _cstr(stock.get("quality")).strip().upper() != quality.upper():
					continue
				if color and _cstr(stock.get("color")).strip().upper() != color.upper():
					continue
				stock_w = flt(stock.get("width_inch") or stock.get("width") or 0)
				if width > 0 and stock_w > 0 and abs(stock_w - width) > 0.05:
					continue
			if width <= 0:
				width = flt(stock.get("width_inch") or stock.get("width") or 0)
			if qty <= 0:
				qty = flt(stock.get("available_kg") or stock.get("available") or 0)
			if not batch_no and stock_batch:
				batch_no = stock_batch
				out["batch_no"] = batch_no
				out["source_roll"] = batch_no
			if width > 0 and qty > 0:
				break
		if width > 0:
			out["width_inch"] = width
			out["width"] = width
		out["available_kg"] = qty
		out["available"] = qty
		out["available_qty"] = qty

	return out


def _gsm_patty_stock_rows_cached() -> list:
	rows = getattr(frappe.local, "_gsm_patty_stock_rows", None)
	if rows is not None:
		return rows
	try:
		from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
			get_available_patty_stock,
		)

		raw = get_available_patty_stock() or []
		if isinstance(raw, dict):
			raw = raw.get("stock") or raw.get("rows") or []
		rows = [r for r in raw if isinstance(r, dict)]
	except Exception:
		rows = []
	frappe.local._gsm_patty_stock_rows = rows
	return rows


def _apply_gsm_session_header_to_spr(
	spr, run_date=None, shift=None, unit=None, operator=None, supervisor=None, coordinator=None
):
	"""Set GSM session header fields on draft SPR without touching desk SPR flows."""
	changed = False
	if unit and _cstr(spr.get("custom_unit")) != _cstr(unit):
		spr.custom_unit = unit
		changed = True
	if unit and _gsm_is_lamination_unit(unit) and frappe.db.has_column("Shaft Production Run", "custom_is_lamination"):
		if not cint(spr.get("custom_is_lamination") or 0):
			spr.custom_is_lamination = 1
			changed = True
	if run_date and str(spr.get("run_date") or "") != str(run_date):
		spr.run_date = run_date
		changed = True
	if shift and _cstr(spr.get("shift")) != _cstr(shift):
		spr.shift = shift
		changed = True
	for value, candidates in (
		(operator, ("operator", "custom_operator", "custom_shift_operator")),
		(supervisor, ("supervisor", "custom_supervisor", "custom_shift_supervisor")),
		(coordinator, ("coordinator", "custom_coordinator")),
	):
		val = _cstr(value).strip()
		if not val:
			continue
		for key in candidates:
			if frappe.db.has_column("Shaft Production Run", key) and _cstr(spr.get(key)) != val:
				spr.set(key, val)
				changed = True
				break
	return changed


def _gsm_spr_has_submittable_rolls(spr_name: str) -> bool:
	"""True when draft SPR already has real roll item rows (e.g. bundle packaging saved on server)."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		return False
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	return any(_spr_is_real_roll_item_row(it) for it in (spr.items or []))


def _gsm_pp_shaft_rows(pp) -> list[dict]:
	"""Read-only PP shaft details for GSM Shaft Details popup."""
	meter_keys = _meter_keys()
	out = []
	for idx, shaft in enumerate(pp.get("custom_shaft_details") or pp.get("shaft_details") or [], start=1):
		row = _normalize_pp_shaft_job_row(shaft)
		comb = _cstr(
			_pick_value(row, ["combination", "combined_width", "shaft", "shaft_details"], "")
		)
		meter_roll = flt(_pick_value(row, meter_keys, 0))
		no_of_shafts = _pick_value(
			row,
			["no_of_shafts", "no_of_shaft", "custom_no_of_shafts", "shaft_count", "number_of_shafts"],
			"",
		)
		if not no_of_shafts and comb:
			try:
				seg = _count_combination_segments(comb)
				if seg > 0:
					no_of_shafts = seg
			except Exception:
				pass
		net_weight = _gsm_resolve_pp_shaft_net_weight(pp, shaft, idx)
		display_nw = net_weight if isinstance(net_weight, str) else flt(net_weight)
		out.append(
			{
				"job": _cstr(_pick_value(row, ["job_id", "job", "job_no"], str(idx))),
				"no_of_shafts": no_of_shafts,
				"gsm": _shaft_gsm(row),
				"combination": comb,
				"total_width": flt(_pick_value(row, ["total_width", "combined_width", "width"], 0))
				or _shaft_width_inch(row),
				"meter_roll": meter_roll,
				"net_weight": display_nw,
				"quality": _cstr(_pick_value(row, ["quality", "custom_quality"], "")),
				"color": _cstr(
					_pick_value(row, ["color", "custom_color", "fabric_colour", "colour"], "")
				),
			}
		)
	return out


def _gsm_job_quality_color(pp_id: str, job_id: str, shaft_row: dict | None = None, cache: dict | None = None) -> dict:
	"""Resolve quality + color for one PP job (PP shaft → SPR job/items → WO item code)."""
	cache = cache if cache is not None else {}
	shaft_row = shaft_row or {}
	job_id = _cstr(job_id).strip()
	quality = _cstr(shaft_row.get("quality") or "").strip()
	color = _cstr(shaft_row.get("color") or "").strip()

	if "spr_doc" not in cache:
		spr_name = pp_id if _gsm_is_standalone_spr_key(pp_id) else (
			_find_spr_for_pp(pp_id, prefer_draft=False) or _find_draft_spr_for_pp(pp_id)
		)
		spr_doc = None
		spr_jobs = {}
		if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
			try:
				spr_doc = frappe.get_doc("Shaft Production Run", spr_name)
			except Exception:
				spr_doc = None
		if spr_doc:
			for sj in _spr_job_rows(spr_doc):
				jid = _cstr(getattr(sj, "job_id", None) or getattr(sj, "job_no", None)).strip()
				if jid:
					spr_jobs[jid] = sj
		cache["spr_doc"] = spr_doc
		cache["spr_jobs"] = spr_jobs

	sj = (cache.get("spr_jobs") or {}).get(job_id)
	if sj and not quality:
		quality = _cstr(getattr(sj, "quality", None) or "").strip()

	if (not quality or not color) and cache.get("spr_doc"):
		for it in cache["spr_doc"].items or []:
			if not _spr_is_real_roll_item_row(it):
				continue
			it_job = _cstr(getattr(it, "job", None) or getattr(it, "job_id", None)).strip()
			if job_id and not _spr_job_keys_match(it_job, job_id):
				continue
			if not quality:
				quality = _cstr(getattr(it, "quality", None) or "").strip()
			if not color:
				color = _cstr(getattr(it, "color", None) or getattr(it, "fabric_colour", None) or "").strip()
			if quality and color:
				break

	if not quality or not color:
		wo_key = f"wo::{job_id}"
		if wo_key not in cache:
			try:
				cache[wo_key] = resolve_work_order_for_roll_line(
					pp_id,
					gsm=shaft_row.get("gsm"),
					job_id=job_id or None,
				) or {}
			except Exception:
				cache[wo_key] = {}
		wo = cache.get(wo_key) or {}
		item_code = _cstr(wo.get("production_item") or shaft_row.get("item_code") or "").strip()
		item_name = _cstr(wo.get("production_item_name") or shaft_row.get("item_name") or "").strip()
		if item_code:
			try:
				specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
			except Exception:
				specs = {}
			if not quality:
				quality = _cstr((specs or {}).get("quality") or "").strip()
			if not color:
				color = _cstr((specs or {}).get("color") or "").strip()

	return {"quality": quality, "color": color}


def _gsm_is_standalone_spr_key(pp_id: str) -> bool:
	pp_id = _cstr(pp_id).strip()
	return bool(pp_id) and frappe.db.exists("Shaft Production Run", pp_id) and not frappe.db.exists(
		"Production Plan", pp_id
	)


def _gsm_order_code_for_pp(pp_id: str) -> str:
	if _gsm_is_standalone_spr_key(pp_id):
		fields = ["custom_order_code"]
		if frappe.db.has_column("Shaft Production Run", "custom_party_code"):
			fields.append("custom_party_code")
		row = frappe.db.get_value("Shaft Production Run", pp_id, fields, as_dict=True) or {}
		return _cstr(row.get("custom_order_code") or row.get("custom_party_code") or "")
	if not pp_id or not frappe.db.exists("Production Plan", pp_id):
		return ""
	pp = frappe.get_doc("Production Plan", pp_id)
	if frappe.get_meta("Production Plan").has_field("custom_party_code"):
		return _cstr(pp.get("custom_party_code") or "")
	return _cstr(pp.get("custom_order_code") or "")


def _gsm_combination_label(comb: str) -> str:
	widths = _parse_combination_widths_inches(_cstr(comb))
	if not widths:
		return _cstr(comb).strip()
	parts = []
	for w in widths:
		fw = flt(w)
		parts.append(str(int(fw)) if fw == int(fw) else str(fw))
	return "+".join(parts)


def _gsm_job_limits_from_shaft_row(shaft_row: dict) -> dict:
	comb = _cstr(shaft_row.get("combination") or "")
	segs = max(1, _count_combination_segments(comb))
	widths = _parse_combination_widths_inches(comb) if comb else []
	if not widths:
		tw = flt(shaft_row.get("total_width") or 0)
		if tw > 0:
			widths = [tw]
	no_shafts = max(1, cint(shaft_row.get("no_of_shafts") or 0))
	rolls_per_shaft = max(1, cint(shaft_row.get("no_of_rolls") or 1))
	if segs > 1:
		max_rolls = no_shafts * segs * rolls_per_shaft
	else:
		max_rolls = no_shafts * rolls_per_shaft
	width_caps: dict[float, int] = {}
	width_requirements: dict[float, int] = {}
	for w in widths:
		fw = round(flt(w), 4)
		if fw > 0:
			width_requirements[fw] = width_requirements.get(fw, 0) + 1
	for fw, req in width_requirements.items():
		width_caps[fw] = req * no_shafts
	return {
		"max_shafts": no_shafts,
		"max_rolls": max_rolls,
		"rolls_per_shaft": rolls_per_shaft if segs <= 1 else segs * rolls_per_shaft,
		"width_segments": sorted(width_caps.keys()),
		"width_caps": width_caps,
		"width_requirements": width_requirements,
	}


def _gsm_sprs_for_pp_job_counts(
	pp_id: str, unit: str | None = None, run_date=None, shift: str | None = None
) -> list:
	rows = frappe.get_all(
		"Shaft Production Run",
		filters={"production_plan": pp_id, "docstatus": ["<", 2]},
		fields=["name", "shift", "run_date", "docstatus", "custom_unit"],
		order_by="modified desc",
		limit=100,
	) or []
	unit = _cstr(unit).strip()
	if not unit:
		return rows
	matched = [r for r in rows if _gsm_units_match(r.get("custom_unit"), unit)]
	if matched:
		return matched
	# GSM header unit and SPR custom_unit often disagree (Unit 4 vs UNIT 3).
	# Still use the SPR that belongs to this shift so job cards and Manual Jobs appear.
	run_d = getdate(run_date) if run_date else None
	cur_shift = _normalize_gsm_shift_label(shift) if shift else ""
	if run_d and cur_shift:
		session_rows = [
			r
			for r in rows
			if r.get("run_date")
			and getdate(r.get("run_date")) == run_d
			and _normalize_gsm_shift_label(r.get("shift")) == cur_shift
		]
		if session_rows:
			return session_rows
	return [r for r in rows if cint(r.get("docstatus")) == 0] or rows


def _gsm_job_production_stats(
	spr_docs: list,
	job_id: str,
	width_segments: list,
	rolls_per_shaft: int,
	width_requirements: dict | None = None,
	run_date=None,
	shift: str | None = None,
) -> dict:
	job_key = _cstr(job_id).strip()
	width_segments = [flt(w) for w in (width_segments or []) if flt(w) > 0]
	job_rolls = 0
	submitted_rolls = 0
	today_rolls = 0
	shift_rolls = 0
	rolls_by_shift_today: dict[str, int] = {}
	job_produced_kg = 0.0
	width_counts: dict[float, int] = {w: 0 for w in width_segments}
	submitted_width_counts: dict[float, int] = {w: 0 for w in width_segments}
	spr_names: list[str] = []
	active_spr_names: list[str] = []
	run_d = getdate(run_date) if run_date else None
	cur_shift = _cstr(shift).strip()

	for spr_row in spr_docs or []:
		sn = _cstr(spr_row.get("name") if isinstance(spr_row, dict) else spr_row)
		if not sn:
			continue
		spr = frappe.get_doc("Shaft Production Run", sn)
		if sn not in spr_names:
			spr_names.append(sn)
		spr_run = getdate(spr.run_date) if spr.get("run_date") else None
		spr_shift = _cstr(spr.get("shift") or "").strip()
		is_submitted = cint(spr.docstatus) == 1
		if run_d and spr_run == run_d and cur_shift and spr_shift == cur_shift and cint(spr.docstatus) == 0:
			active_spr_names.append(sn)
		for it in spr.get("items") or []:
			if not _spr_job_keys_match(_cstr(getattr(it, "job", None)), job_key):
				continue
			if not _spr_is_real_roll_item_row(it):
				continue
			if not _spr_roll_has_production(it):
				continue
			job_rolls += 1
			job_produced_kg += flt(getattr(it, "net_weight", None) or 0)
			if is_submitted:
				submitted_rolls += 1
			if run_d and spr_run == run_d:
				today_rolls += 1
				if spr_shift:
					rolls_by_shift_today[spr_shift] = rolls_by_shift_today.get(spr_shift, 0) + 1
				if cur_shift and spr_shift == cur_shift:
					shift_rolls += 1
			w = flt(getattr(it, "width_inch", None) or 0)
			for seg_w in width_segments:
				if abs(w - seg_w) < 0.05:
					width_counts[seg_w] = width_counts.get(seg_w, 0) + 1
					if is_submitted:
						submitted_width_counts[seg_w] = submitted_width_counts.get(seg_w, 0) + 1
					break

	width_requirements = width_requirements or {w: 1 for w in width_segments}
	if width_segments:
		submitted_shafts = min(
			cint(submitted_width_counts.get(w, 0)) // max(1, cint(width_requirements.get(w) or 1))
			for w in width_segments
		)
	else:
		submitted_shafts = submitted_rolls // max(1, cint(rolls_per_shaft))
	if width_segments:
		job_shafts = min(
			cint(width_counts.get(w, 0)) // max(1, cint(width_requirements.get(w) or 1))
			for w in width_segments
		)
	else:
		job_shafts = job_rolls // max(1, cint(rolls_per_shaft))

	segment_stats = [{"width_inch": w, "current": width_counts.get(w, 0)} for w in width_segments]

	return {
		"job_rolls_produced": job_rolls,
		"job_shafts_produced": job_shafts,
		"submitted_rolls": submitted_rolls,
		"submitted_shafts": submitted_shafts,
		"job_produced_kg": job_produced_kg,
		"today_rolls": today_rolls,
		"shift_rolls": shift_rolls,
		"rolls_by_shift_today": rolls_by_shift_today,
		"width_counts": width_counts,
		"segment_stats": segment_stats,
		"spr_names": spr_names,
		"active_spr_names": active_spr_names,
	}


def _gsm_build_job_board_entry(
	pp_id: str,
	shaft_row: dict,
	run_date=None,
	shift: str | None = None,
	unit: str | None = None,
	wo_terminal: bool = False,
	qc_cache: dict | None = None,
) -> dict:
	job_id = _cstr(shaft_row.get("job") or "")
	limits = _gsm_job_limits_from_shaft_row(shaft_row)
	width_segments = limits["width_segments"]
	width_caps = limits["width_caps"]
	width_requirements = limits["width_requirements"]
	max_shafts = limits["max_shafts"]
	max_rolls = limits["max_rolls"]
	rolls_per_shaft = limits["rolls_per_shaft"]

	if _gsm_is_standalone_spr_key(pp_id):
		spr_list = [{"name": pp_id}]
	else:
		spr_list = _gsm_sprs_for_pp_job_counts(pp_id, unit=unit, run_date=run_date, shift=shift)
		extra_spr = _cstr(shaft_row.get("spr_name") or "").strip()
		if extra_spr and extra_spr not in {_cstr(r.get("name")) for r in spr_list}:
			spr_list = list(spr_list) + [{"name": extra_spr}]
	stats = _gsm_job_production_stats(
		spr_list,
		job_id,
		width_segments,
		rolls_per_shaft,
		width_requirements,
		run_date=run_date,
		shift=shift,
	)
	job_rolls = cint(stats["job_rolls_produced"])
	job_shafts = min(max_shafts, cint(stats["job_shafts_produced"]))
	submitted_rolls = cint(stats.get("submitted_rolls") or 0)
	submitted_shafts = min(max_shafts, cint(stats.get("submitted_shafts") or 0))
	rem_shafts = max(0, max_shafts - job_shafts)
	rem_rolls = max(0, max_rolls - job_rolls)
	current_shaft_rolls = job_rolls % max(1, rolls_per_shaft)
	current_shaft_remaining_rolls = (
		max(0, rolls_per_shaft - current_shaft_rolls) if rem_rolls > 0 and current_shaft_rolls else 0
	)

	segments_out = []
	can_add_any = not wo_terminal and rem_rolls > 0
	for seg in stats["segment_stats"]:
		w = flt(seg["width_inch"])
		cur = cint(seg["current"])
		seg_max = cint(width_caps.get(w) or max_shafts)
		seg_can = cur < seg_max and rem_rolls > 0 and not wo_terminal
		segments_out.append(
			{
				"width_inch": w,
				"current": cur,
				"max": seg_max,
				"can_add": seg_can,
			}
		)

	# A job is only "complete" once its rolls are SUBMITTED (docstatus 1) to the
	# full planned quota, or the Work Order is terminal. Draft/planned rolls still
	# count toward can_add limits but must not mark the job Completed.
	roll_limit_reached = rem_rolls <= 0 or job_shafts >= max_shafts
	submitted_complete = submitted_rolls >= max_rolls and max_rolls > 0
	quota_full = wo_terminal or submitted_complete
	comb = _cstr(shaft_row.get("combination") or "")
	net_per_roll = flt(shaft_row.get("net_weight") or 0)
	job_target_kg = round(net_per_roll * max_rolls, 2) if net_per_roll > 0 else 0.0
	job_produced_kg = flt(stats.get("job_produced_kg") or 0)
	job_remaining_kg = max(0.0, round(job_target_kg - job_produced_kg, 2)) if job_target_kg > 0 else 0.0
	order_code = _gsm_order_code_for_pp(pp_id)
	qc = _gsm_job_quality_color(pp_id, job_id, shaft_row, cache=qc_cache)
	return {
		"pp_id": pp_id,
		"order_code": order_code,
		"party_name": "",
		"job_id": job_id,
		"job_key": f"{pp_id}::{job_id}",
		"gsm": cint(shaft_row.get("gsm") or 0),
		"combination": comb,
		"combination_label": _gsm_combination_label(comb),
		"meter_roll": flt(shaft_row.get("meter_roll") or 0),
		"net_weight": flt(shaft_row.get("net_weight") or 0),
		"quality": _cstr(qc.get("quality") or ""),
		"color": _cstr(qc.get("color") or ""),
		"job_target_kg": job_target_kg,
		"job_produced_kg": job_produced_kg,
		"job_remaining_kg": job_remaining_kg,
		"max_shafts": max_shafts,
		"max_rolls": max_rolls,
		"rolls_per_shaft": rolls_per_shaft,
		"job_shafts_produced": job_shafts,
		"job_rolls_produced": job_rolls,
		"submitted_rolls": submitted_rolls,
		"submitted_shafts": submitted_shafts,
		"rem_shafts": rem_shafts,
		"rem_rolls": rem_rolls,
		"current_shaft_rolls": current_shaft_rolls,
		"current_shaft_remaining_rolls": current_shaft_remaining_rolls,
		"today_rolls": cint(stats["today_rolls"]),
		"shift_rolls": cint(stats["shift_rolls"]),
		"rolls_by_shift_today": stats.get("rolls_by_shift_today") or {},
		"width_segments": segments_out,
		"can_add_roll": can_add_any and not roll_limit_reached,
		"roll_limit_reached": roll_limit_reached,
		"quota_full": quota_full,
		"wo_terminal": bool(wo_terminal),
		"spr_names": stats.get("spr_names") or [],
		"active_spr_names": stats.get("active_spr_names") or [],
		"run_date": str(run_date or ""),
		"shift": _cstr(shift or ""),
	}


_GSM_WO_TERMINAL_STATUSES = frozenset(
	{"completed", "stopped", "cancelled", "canceled", "closed", "close"}
)


def _gsm_pp_wo_terminal(pp_id: str) -> bool:
	"""True when the PP has Work Order(s) and none are still open (matches Production Table)."""
	wo_rows = frappe.db.sql(
		"""
		SELECT status, docstatus
		FROM `tabWork Order`
		WHERE production_plan = %(pp)s AND docstatus != 2
		""",
		{"pp": pp_id},
		as_dict=True,
	)
	if not wo_rows:
		return False
	for w in wo_rows:
		if cint(w.get("docstatus") or 0) == 0:
			return False
		if str(w.get("status") or "").strip().lower() not in _GSM_WO_TERMINAL_STATUSES:
			return False
	return True


def _gsm_manual_job_shaft_rows(
	pp_id: str,
	unit: str | None = None,
	run_date=None,
	shift: str | None = None,
	extra_spr_names=None,
) -> list[dict]:
	"""Manual jobs (created via SPR Tools) live only on the SPR, not the Production Plan.

	Surface them as shaft-row dicts so they show up in the GSM job board and add-roll
	wizard, letting the operator record production against a manual job.
	"""
	names: list[str] = []
	seen: set[str] = set()

	def _add(n):
		n = _cstr(n).strip()
		if n and n not in seen:
			seen.add(n)
			names.append(n)

	for r in _gsm_sprs_for_pp_job_counts(pp_id, unit=unit, run_date=run_date, shift=shift):
		_add(r.get("name") if isinstance(r, dict) else r)
	# Manual jobs are order-scoped. Do not hide them when SPR custom_unit disagrees
	# with the GSM header (Unit 4 session vs UNIT 3 on the desk form).
	for n in (
		frappe.get_all(
			"Shaft Production Run",
			filters={"production_plan": pp_id, "docstatus": ["<", 2]},
			pluck="name",
			order_by="modified desc",
			limit=100,
		)
		or []
	):
		_add(n)
	for n in extra_spr_names or []:
		n = _cstr(n).strip()
		if not n:
			continue
		pp_of = _cstr(frappe.db.get_value("Shaft Production Run", n, "production_plan") or "")
		if pp_of == pp_id or n == pp_id:
			_add(n)
	if not names:
		return []
	return _gsm_job_shaft_rows_from_spr_names(names)


def _gsm_job_is_manual_row(row: dict) -> bool:
	if cint(row.get("is_manual")):
		return True
	return _cstr(row.get("job_id")).strip().upper().startswith("MAN-")


def _gsm_job_shaft_rows_from_spr_names(spr_names: list[str]) -> list[dict]:
	spr_names = [_cstr(n).strip() for n in (spr_names or []) if _cstr(n).strip()]
	if not spr_names:
		return []
	meta = frappe.get_meta("Shaft Production Run Job")
	wanted = [
		"job_id",
		"no_of_shafts",
		"no_of_rolls",
		"gsm",
		"quality",
		"color",
		"combination",
		"total_width",
		"work_orders",
		"manual_items",
		"party_code",
		"meter_roll_mtrs",
		"is_manual",
	]
	fields = ["name", "parent"] + [f for f in wanted if meta.has_field(f)]
	rows = frappe.get_all(
		"Shaft Production Run Job",
		filters={"parent": ["in", spr_names], "parenttype": "Shaft Production Run"},
		fields=fields,
		order_by="idx asc",
	)
	out: list[dict] = []
	seen: set[str] = set()
	for r in rows:
		if not _gsm_job_is_manual_row(r):
			continue
		job_id = _cstr(r.get("job_id")).strip()
		if not job_id or job_id in seen:
			continue
		seen.add(job_id)
		work_order = _cstr(r.get("work_orders")).split(",")[0].strip()
		item_code = _cstr(r.get("manual_items")).split(",")[0].strip()
		item_name = frappe.db.get_value("Item", item_code, "item_name") if item_code else ""
		out.append(
			{
				"job": job_id,
				"no_of_shafts": cint(r.get("no_of_shafts") or 1),
				"no_of_rolls": cint(r.get("no_of_rolls") or 1),
				"gsm": cint(r.get("gsm") or 0),
				"quality": _cstr(r.get("quality")),
				"color": _cstr(r.get("color")),
				"combination": _cstr(r.get("combination")),
				"total_width": flt(r.get("total_width") or 0),
				"meter_roll": flt(r.get("meter_roll_mtrs") or 0),
				"net_weight": 0,
				"is_manual": True,
				"spr_name": _cstr(r.get("parent")),
				"work_order": work_order,
				"item_code": item_code,
				"item_name": _cstr(item_name),
			}
		)
	return out


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_pp_job_board(pp_ids=None, run_date=None, shift=None, unit=None, spr_names=None):
	"""GSM sidebar — per PP job shaft+roll progress, remaining, per-width caps."""
	pp_ids = _parse_json_arg(pp_ids, [])
	if isinstance(pp_ids, str):
		pp_ids = [x.strip() for x in pp_ids.split(",") if x.strip()]
	pp_ids = [_cstr(x).strip() for x in (pp_ids or []) if _cstr(x).strip()]
	extra_spr_names = _parse_json_arg(spr_names, [])
	if isinstance(extra_spr_names, str):
		extra_spr_names = [x.strip() for x in extra_spr_names.split(",") if x.strip()]
	extra_spr_names = [_cstr(x).strip() for x in (extra_spr_names or []) if _cstr(x).strip()]
	if run_date and shift and unit:
		for row in _gsm_draft_sprs_for_session(run_date, shift, unit):
			tid = _cstr(row.get("pp_id")).strip()
			if cint(row.get("is_trial")) and tid and tid not in pp_ids:
				pp_ids.append(tid)
	if not pp_ids:
		return {"jobs": [], "by_pp": {}}

	out_jobs = []
	by_pp: dict[str, list] = {}
	for pp_id in pp_ids:
		pp_id = _cstr(pp_id).strip()
		if not pp_id:
			continue
		if _gsm_is_standalone_spr_key(pp_id):
			pp_jobs = []
			qc_cache: dict = {}
			for man_row in _gsm_job_shaft_rows_from_spr_names([pp_id]):
				entry = _gsm_build_job_board_entry(
					pp_id,
					man_row,
					run_date=run_date,
					shift=shift,
					unit=unit,
					wo_terminal=False,
					qc_cache=qc_cache,
				)
				entry["is_trial"] = True
				entry["is_manual"] = True
				entry["work_order"] = _cstr(man_row.get("work_order"))
				entry["item_code"] = _cstr(man_row.get("item_code"))
				entry["item_name"] = _cstr(man_row.get("item_name"))
				if not entry.get("quality"):
					entry["quality"] = _cstr(man_row.get("quality"))
				if not entry.get("color"):
					entry["color"] = _cstr(man_row.get("color"))
				if not entry.get("color") and entry.get("item_code"):
					try:
						specs = _spr_resolve_roll_line_specs_from_item_code(
							entry["item_code"], entry.get("item_name")
						)
						entry["color"] = _cstr((specs or {}).get("color") or "")
						if not entry.get("quality"):
							entry["quality"] = _cstr((specs or {}).get("quality") or "")
					except Exception:
						pass
				pp_jobs.append(entry)
				out_jobs.append(entry)
			by_pp[pp_id] = pp_jobs
			continue
		if not frappe.db.exists("Production Plan", pp_id):
			continue
		wo_terminal = _gsm_pp_wo_terminal(pp_id)
		pp_jobs = []
		qc_cache: dict = {}
		for shaft_row in _gsm_pp_shaft_rows(frappe.get_doc("Production Plan", pp_id)):
			entry = _gsm_build_job_board_entry(
				pp_id,
				shaft_row,
				run_date=run_date,
				shift=shift,
				unit=unit,
				wo_terminal=wo_terminal,
				qc_cache=qc_cache,
			)
			pp_jobs.append(entry)
			out_jobs.append(entry)
		for man_row in _gsm_manual_job_shaft_rows(
			pp_id,
			unit=unit,
			run_date=run_date,
			shift=shift,
			extra_spr_names=extra_spr_names,
		):
			entry = _gsm_build_job_board_entry(
				pp_id,
				man_row,
				run_date=run_date,
				shift=shift,
				unit=unit,
				wo_terminal=False,
				qc_cache=qc_cache,
			)
			entry["is_manual"] = True
			entry["work_order"] = _cstr(man_row.get("work_order"))
			entry["item_code"] = _cstr(man_row.get("item_code"))
			entry["item_name"] = _cstr(man_row.get("item_name"))
			# Prefer shaft/manual quality; fill gaps from item-code specs already resolved above.
			if not entry.get("quality"):
				entry["quality"] = _cstr(man_row.get("quality"))
			if not entry.get("color") and entry.get("item_code"):
				try:
					specs = _spr_resolve_roll_line_specs_from_item_code(
						entry["item_code"], entry.get("item_name")
					)
					entry["color"] = _cstr((specs or {}).get("color") or "")
					if not entry.get("quality"):
						entry["quality"] = _cstr((specs or {}).get("quality") or "")
				except Exception:
					pass
			pp_jobs.append(entry)
			out_jobs.append(entry)
		by_pp[pp_id] = pp_jobs

	return {
		"jobs": out_jobs,
		"by_pp": by_pp,
		"run_date": str(run_date or ""),
		"shift": _cstr(shift or ""),
		"unit": _cstr(unit or ""),
	}


def _gsm_resolve_planning_table_names_for_pp(pp_id: str) -> list[str]:
	"""
	Find Planning Table row names for a Production Plan when the client sent
	no valid lineId (common for lamination order-board selections).

	Prefer lamination FG rows (104/107); otherwise any PT rows linked to the PP.
	"""
	pp_id = _cstr(pp_id).strip()
	if not pp_id or not frappe.db.exists("Production Plan", pp_id):
		return []

	candidates: list[str] = []

	# PT rows that store PP link on common fields
	pp_fields = []
	for fn in (
		"production_plan",
		"custom_production_plan",
		"mr_production_plan",
		"custom_pp_name",
	):
		if frappe.db.has_column("Planning Table", fn):
			pp_fields.append(fn)

	rows = []
	if pp_fields:
		or_filters = [[fn, "=", pp_id] for fn in pp_fields]
		try:
			rows = frappe.get_all(
				"Planning Table",
				or_filters=or_filters,
				fields=["name", "item_code"],
				limit=50,
			)
		except Exception:
			rows = []

	# Also via Production Plan Item → sales_order_item linkage when present
	if not rows and frappe.db.has_column("Planning Table", "sales_order_item"):
		soi_list = frappe.get_all(
			"Production Plan Item",
			filters={"parent": pp_id},
			pluck="sales_order_item",
		)
		soi_list = [x for x in (soi_list or []) if x]
		if soi_list:
			rows = frappe.get_all(
				"Planning Table",
				filters={"sales_order_item": ["in", soi_list]},
				fields=["name", "item_code"],
				limit=50,
			)

	lam_rows = []
	other_rows = []
	for r in rows or []:
		nm = _cstr(r.get("name")).strip()
		if not nm:
			continue
		ic = _cstr(r.get("item_code")).strip()
		try:
			from production_entry.production_planning.scheduler_api import (
				_is_lamination_parent_process,
			)

			is_lam = _is_lamination_parent_process(ic)
		except Exception:
			is_lam = ic.startswith("104") or ic.startswith("107") or "-104" in ic or "-107" in ic
		if is_lam:
			lam_rows.append(nm)
		else:
			other_rows.append(nm)

	candidates = lam_rows or other_rows
	# de-dupe preserve order
	seen = set()
	out = []
	for n in candidates:
		if n not in seen:
			seen.add(n)
			out.append(n)
	return out


@frappe.whitelist(methods=["GET", "POST"])
def create_gsm_sprs_for_session(
	run_date=None,
	shift=None,
	unit=None,
	operator=None,
	supervisor=None,
	coordinator=None,
	entries=None,
	force_new_session=0,
):
	"""GSM Step 1 — create/reuse one draft SPR per PP (same as Production Table create_item_spr)."""
	entries = _parse_json_arg(entries, [])
	if not entries:
		frappe.throw(_("Select at least one planning line"))

	pp_groups: dict[str, list] = {}
	for entry in entries:
		if not isinstance(entry, dict):
			continue
		pp_id = _cstr(entry.get("pp_id") or entry.get("ppId")).strip()
		line_id = _cstr(
			entry.get("lineId")
			or entry.get("planning_table_row")
			or entry.get("planning_line_id")
			or entry.get("itemName")
			or entry.get("psi_name")
			or entry.get("id")
		).strip()
		if not pp_id:
			continue
		# Reject synthetic job placeholders (job-1 / 1 / gsm-job) — resolve real PT rows below
		if line_id and (
			line_id.startswith("job-")
			or line_id in ("gsm-job", "1")
			or not frappe.db.exists("Planning Table", line_id)
		):
			line_id = ""
		pp_groups.setdefault(pp_id, [])
		if line_id and line_id not in pp_groups[pp_id]:
			pp_groups[pp_id].append(line_id)

	if not pp_groups:
		frappe.throw(_("No valid Production Plan rows in selection"))

	# Fill missing Planning Table names from the PP (lamination 104/107 FG rows)
	for pp_id, psi_names in list(pp_groups.items()):
		if psi_names:
			continue
		resolved = _gsm_resolve_planning_table_names_for_pp(pp_id)
		if resolved:
			pp_groups[pp_id] = resolved
		else:
			frappe.log_error(
				f"No Planning Table rows for PP {pp_id}",
				"create_gsm_sprs_for_session",
			)

	sprs_out = []
	for pp_id, psi_names in pp_groups.items():
		if not psi_names:
			sprs_out.append(
				{
					"pp_id": pp_id,
					"status": "error",
					"message": _("No valid Planning Sheet Items found"),
				}
			)
			continue
		result = ensure_draft_spr_for_pp(
			pp_id,
			psi_names,
			unit=unit,
			run_date=run_date,
			shift=shift,
			force_new=cint(force_new_session),
		)
		if not isinstance(result, dict) or result.get("status") != "ok" or not result.get("spr_name"):
			sprs_out.append(
				{
					"pp_id": pp_id,
					"status": "error",
					"message": _cstr(result.get("message") if isinstance(result, dict) else result) or _("Could not create SPR"),
				}
			)
			continue
		spr_name = result["spr_name"]
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if _apply_gsm_session_header_to_spr(spr, run_date, shift, unit, operator, supervisor, coordinator):
			spr.save(ignore_permissions=True)
		label_type = _gsm_label_type_for_pp_spr(pp_id, spr_name)
		sprs_out.append(
			{
				"pp_id": pp_id,
				"status": "ok",
				"spr_name": spr_name,
				"order_code": _cstr(spr.get("custom_order_code") or _gsm_order_code_for_pp(pp_id)),
				"label_type": label_type,
				"reused": cint(result.get("reused") or 0),
				"shaft_job_count": len(_spr_job_rows(spr)),
			}
		)
	return {"status": "ok", "sprs": sprs_out}


def _gsm_is_lamination_unit(unit) -> bool:
	from production_entry.production_planning.planning_doctypes import LAMINATION_UNIT

	u = _cstr(unit).strip().upper()
	return "LAMINATION" in u or u == _cstr(LAMINATION_UNIT).strip().upper()


def _gsm_rm_input_kind(item_code: str, process_code: str = "") -> str:
	"""Classify BOM RM as fabric (100*) vs bopp/other for lamination input UI."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		spr_fg_item_process_code,
	)

	ic = _cstr(item_code)
	pc = _cstr(process_code) or spr_fg_item_process_code(ic)
	icu = ic.upper()
	if pc.startswith("100") or icu.startswith("100") or " FABRIC" in icu:
		return "fabric"
	if "BOPP" in icu or pc in ("101", "105", "106", "108", "109") or pc.startswith("10") and not pc.startswith("100"):
		return "bopp"
	# Non-100 batch RM on 107 plans → treat as BOPP/film input
	return "bopp" if pc and not pc.startswith("100") else "fabric"


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_lamination_order_board(run_date=None, unit=None, lamination_process=None):
	"""Order-centric board for GSM Lamination mode (104 and/or 107)."""
	from production_entry.production_planning.planning_doctypes import LAMINATION_UNIT
	from production_entry.production_planning.scheduler_api import get_lamination_order_table_data

	run_date = getdate(run_date) if run_date else getdate()
	unit = _cstr(unit).strip() or LAMINATION_UNIT
	want_proc = _cstr(lamination_process).strip()
	procs = [want_proc] if want_proc in ("104", "107") else ["104", "107"]

	orders = []
	seen = set()
	for proc in procs:
		try:
			rows = get_lamination_order_table_data(
				date=str(run_date),
				planned_only=1,
				lamination_process=proc,
				board_process_scope="lamination_only",
				board_slug="gsm-production-entry",
			) or []
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"get_gsm_lamination_order_board:{proc}")
			rows = []
		for r in rows:
			if not isinstance(r, dict):
				continue
			pp_id = _cstr(r.get("pp_id") or r.get("production_plan") or r.get("ppId")).strip()
			if not pp_id or pp_id in seen:
				# Still allow same PP once; prefer first
				if pp_id and pp_id in seen:
					continue
			order_code = _cstr(
				r.get("lamination_order_code")
				or r.get("booking_id")
				or r.get("order_code")
				or r.get("party_code")
				or r.get("custom_order_code")
				or _gsm_order_code_for_pp(pp_id)
				or pp_id
			)
			fg_proc = _cstr(r.get("lamination_process") or r.get("process") or proc)
			if fg_proc not in ("104", "107"):
				fg_proc = proc
			key = pp_id or f"{order_code}|{fg_proc}"
			if key in seen:
				continue
			seen.add(key)
			if pp_id:
				seen.add(pp_id)
			target_kg = flt(r.get("qty") or r.get("planned_qty") or r.get("required_qty") or 0)
			produced_kg = flt(r.get("spr_kg") or r.get("achieved_kg") or r.get("actual_production_weight_kgs") or 0)
			psi_name = _cstr(
				r.get("itemName") or r.get("item_name") or r.get("psi_name") or r.get("name") or ""
			).strip()
			# Prefer Planning Table row id only (not Item description names)
			if psi_name and not frappe.db.exists("Planning Table", psi_name):
				psi_name = _cstr(r.get("itemName") or r.get("psi_name") or "").strip()
				if psi_name and not frappe.db.exists("Planning Table", psi_name):
					psi_name = ""
			width_inch = flt(
				r.get("width_inch")
				or r.get("pt_width_inch")
				or r.get("width")
				or 0
			)
			if width_inch <= 0:
				comb = _cstr(r.get("combination") or r.get("combination_label") or "")
				widths = _parse_combination_widths_inches(comb) if comb else []
				if widths:
					width_inch = flt(widths[0])
			orders.append(
				{
					"key": key,
					"pp_id": pp_id,
					"order_code": order_code,
					"customer": _cstr(r.get("customer") or r.get("customer_name") or ""),
					"party_code": _cstr(r.get("party_code") or ""),
					"quality": _cstr(r.get("quality") or ""),
					"color": _cstr(r.get("color") or r.get("colour") or ""),
					"unit": unit,
					"lamination_process": fg_proc,
					"needs_fabric_input": 1,
					"needs_bopp_input": 1 if fg_proc == "107" else 0,
					"target_kg": target_kg,
					"produced_kg": produced_kg,
					"remaining_kg": max(0.0, target_kg - produced_kg),
					"combination": _cstr(r.get("combination") or r.get("combination_label") or ""),
					"fabric_gsm": cint(r.get("fabric_gsm") or r.get("gsm") or 0),
					"lam_gsm": cint(r.get("lam_gsm") or r.get("lamination_gsm") or r.get("custom_lam_gsm") or 0),
					"bopp_gsm": cint(r.get("bopp_gsm") or r.get("custom_bopp_gsm") or 0),
					"pp_docstatus": cint(r.get("pp_docstatus") or 1),
					"planned_date": str(r.get("planned_date") or r.get("date") or run_date),
					# Planning Table row name — required for Create SPR
					"itemName": psi_name,
					"name": psi_name,
					"psi_name": psi_name,
					"width_inch": width_inch,
					"width": width_inch,
				}
			)
	return {"status": "ok", "run_date": str(run_date), "unit": unit, "orders": orders}


@frappe.whitelist(methods=["GET", "POST"])
def gsm_lamination_input_context(spr_name=None, input_kind=None):
	"""RM pick context for GSM lamination inputs, filtered to fabric or bopp."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		spr_doc_is_lamination,
		spr_fg_item_process_code,
		spr_get_fabric_batch_pick_context,
	)

	spr_name = _cstr(spr_name).strip()
	kind = _cstr(input_kind).strip().lower() or "fabric"
	if kind not in ("fabric", "bopp"):
		frappe.throw(_("input_kind must be fabric or bopp"))
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))

	spr = frappe.get_doc("Shaft Production Run", spr_name)
	fg_process = ""
	for it in spr.items or []:
		ic = _cstr(getattr(it, "item_code", None) or "")
		if ic:
			fg_process = spr_fg_item_process_code(ic)
			if fg_process in ("104", "107"):
				break
	if not fg_process:
		pp = _cstr(spr.get("production_plan") or "")
		if pp and frappe.db.exists("Production Plan", pp):
			for w in frappe.get_all(
				"Work Order",
				filters={"production_plan": pp, "docstatus": ["!=", 2]},
				fields=["production_item"],
				limit=20,
			) or []:
				fg_process = spr_fg_item_process_code(_cstr((w or {}).get("production_item") or ""))
				if fg_process in ("104", "107"):
					break

	if kind == "bopp" and fg_process == "104":
		return {
			"status": "ok",
			"spr_name": spr_name,
			"fg_process": fg_process or "104",
			"input_kind": kind,
			"allowed": False,
			"message": _("Process 104 needs fabric inputs only (no BOPP)."),
			"rm_options": [],
			"current_picks": [],
		}

	ctx = spr_get_fabric_batch_pick_context(spr_name) or {}
	rm_options = []
	for ln in ctx.get("lines") or []:
		wo = _cstr(ln.get("work_order") or "")
		for rm in ln.get("raw_materials") or []:
			ic = _cstr(rm.get("item_code") or "")
			pc = _cstr(rm.get("process_code") or "")
			if _gsm_rm_input_kind(ic, pc) != kind:
				continue
			rm_options.append(
				{
					"work_order": wo,
					"item_code": ic,
					"item_name": _cstr(rm.get("item_name") or ""),
					"process_code": pc,
					"required_qty": flt(rm.get("required_qty") or 0),
					"quality": _cstr(rm.get("quality") or ""),
					"colour": _cstr(rm.get("colour") or ""),
					"gsm": flt(rm.get("gsm") or 0),
					"width_inch": flt(rm.get("width_inch") or 0),
					"batches": rm.get("batches") or [],
				}
			)

	cur = []
	for p in ctx.get("current_picks") or []:
		ic = _cstr(p.get("item_code") or "")
		if _gsm_rm_input_kind(ic) != kind:
			continue
		cur.append(p)

	return {
		"status": "ok",
		"spr_name": spr_name,
		"fg_process": fg_process or "",
		"is_lamination": 1 if spr_doc_is_lamination(spr) else cint(spr.get("custom_is_lamination") or 0),
		"input_kind": kind,
		"allowed": True,
		"needs_picks": cint(ctx.get("needs_picks") or 0),
		"rm_options": rm_options,
		"current_picks": cur,
		"all_current_picks": ctx.get("current_picks") or [],
	}


@frappe.whitelist(methods=["GET", "POST"])
def gsm_lamination_prepare_input_rows(spr_name=None, input_kind=None, num_rolls=None):
	"""Ask N → return N blank editable input rows (fabric or bopp) for the operator to fill."""
	kind = _cstr(input_kind).strip().lower() or "fabric"
	n = max(1, cint(num_rolls or 1))
	ctx = gsm_lamination_input_context(spr_name=spr_name, input_kind=kind)
	if not ctx.get("allowed"):
		return {**ctx, "rows": []}
	opts = ctx.get("rm_options") or []
	default_rm = opts[0] if opts else {}
	rows = []
	for i in range(n):
		rows.append(
			{
				"idx": i + 1,
				"input_kind": kind,
				"work_order": _cstr(default_rm.get("work_order") or ""),
				"item_code": _cstr(default_rm.get("item_code") or ""),
				"item_name": _cstr(default_rm.get("item_name") or ""),
				"batch_no": "",
				"qty": 0,
				"batches": default_rm.get("batches") or [],
			}
		)
	return {
		"status": "ok",
		"spr_name": _cstr(spr_name),
		"input_kind": kind,
		"fg_process": ctx.get("fg_process"),
		"rm_options": opts,
		"rows": rows,
		"message": _("Enter batch and qty for each of the {0} {1} input roll(s).").format(n, kind),
	}


@frappe.whitelist(methods=["GET", "POST"])
def gsm_lamination_save_input_picks(spr_name=None, picks_json=None, merge_with_other_kind=1):
	"""Save fabric/bopp input rows onto SPR fabric_batch_picks (merge keeps the other kind)."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		spr_save_fabric_batch_picks,
	)

	spr_name = _cstr(spr_name).strip()
	picks = _parse_json_arg(picks_json, [])
	if not spr_name:
		frappe.throw(_("SPR is required"))
	new_picks = []
	for p in picks or []:
		if not isinstance(p, dict):
			continue
		wo = _cstr(p.get("work_order") or "")
		ic = _cstr(p.get("item_code") or "")
		bn = _cstr(p.get("batch_no") or "")
		q = flt(p.get("qty") or 0)
		if wo and ic and bn and q > 0:
			new_picks.append({"work_order": wo, "item_code": ic, "batch_no": bn, "qty": q})

	if cint(merge_with_other_kind):
		# Keep existing picks that are the opposite kind of this save batch
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		incoming_kinds = {_gsm_rm_input_kind(p["item_code"]) for p in new_picks}
		keep = []
		for r in spr.get("fabric_batch_picks") or []:
			ic = _cstr(getattr(r, "item_code", None) or "")
			k = _gsm_rm_input_kind(ic)
			if k not in incoming_kinds:
				keep.append(
					{
						"work_order": _cstr(getattr(r, "work_order", None) or ""),
						"item_code": ic,
						"batch_no": _cstr(getattr(r, "batch_no", None) or ""),
						"qty": flt(getattr(r, "qty", None) or 0),
					}
				)
		new_picks = keep + new_picks

	return spr_save_fabric_batch_picks(spr_name=spr_name, picks_json=new_picks)


@frappe.whitelist(methods=["GET", "POST"])
def gsm_lamination_add_output_rolls(spr_name=None, job_id=None, rolls_per_combination=None, exact_roll_lines=None):
	"""Add FG output rolls on a lamination SPR (wraps desk append API)."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_job_id,
		_spr_job_rows,
		append_roll_lines_for_job_and_save,
	)

	spr_name = _cstr(spr_name).strip()
	job_id = _cstr(job_id).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if not job_id:
		jobs = _spr_job_rows(spr)
		if not jobs:
			frappe.throw(_("No Available Jobs on this SPR — create SPR from the order first."))
		job_id = _cstr(_spr_job_id(jobs[0]))
	lam_n = cint(rolls_per_combination or 0)
	exact_n = cint(exact_roll_lines or 0)
	if lam_n <= 0 and exact_n <= 0:
		frappe.throw(_("Enter rolls per combination (or exact roll lines)."))
	# Ensure lamination flag for create-entry rules
	if frappe.get_meta("Shaft Production Run").has_field("custom_is_lamination"):
		if not cint(spr.get("custom_is_lamination") or 0):
			frappe.db.set_value("Shaft Production Run", spr_name, "custom_is_lamination", 1, update_modified=False)

	return append_roll_lines_for_job_and_save(
		shaft_production_run=spr_name,
		job_id=job_id,
		lamination_rolls_per_combination=lam_n if lam_n > 0 else None,
		lamination_exact_roll_lines=exact_n if exact_n > 0 else None,
	)


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_pp_shaft_details(pp_ids=None):
	"""GSM Shaft Details popup — read PP shaft table (no SPR required)."""
	pp_ids = _parse_json_arg(pp_ids, [])
	if isinstance(pp_ids, str):
		pp_ids = [x.strip() for x in pp_ids.split(",") if x.strip()]
	if not pp_ids:
		frappe.throw(_("Production Plan id(s) required"))
	out = []
	for pp_id in pp_ids:
		pp_id = _cstr(pp_id).strip()
		if not pp_id or not frappe.db.exists("Production Plan", pp_id):
			out.append({"pp_id": pp_id, "status": "error", "message": _("Production Plan not found")})
			continue
		pp = frappe.get_doc("Production Plan", pp_id)
		out.append(
			{
				"pp_id": pp_id,
				"status": "ok",
				"order_code": _gsm_order_code_for_pp(pp_id),
				"label_type": _gsm_label_type_for_pp_spr(pp_id) or "",
				"shaft_rows": _gsm_pp_shaft_rows(pp),
			}
		)
	return out


def _gsm_tolerance_orders_from_sprs(spr_names: list[str]) -> list[dict]:
	orders = []
	for spr_name in spr_names:
		check = spr_get_tolerance_violations(spr_name)
		if check.get("skipped") or not check.get("violations"):
			continue
		orders.append(
			{
				"spr_name": spr_name,
				"order_code": check.get("order_code") or "",
				"tolerance_percent": check.get("tolerance_percent"),
				"violations": check.get("violations") or [],
			}
		)
	return orders


def _gsm_apply_tolerance_override(spr_name: str, reason: str, approved: int):
	"""Set SPR tolerance override fields — same fields desk submit dialog uses."""
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	meta = frappe.get_meta("Shaft Production Run")
	if not meta.has_field("tolerance_override_approved") or not meta.has_field("tolerance_override_reason"):
		return
	spr.tolerance_override_approved = cint(approved)
	spr.tolerance_override_reason = _cstr(reason).strip()
	spr.save(ignore_permissions=True)


@frappe.whitelist(methods=["GET", "POST"])
def save_gsm_roll_line(spr_name, roll_payload, shift=None):
	"""GSM real-time Save Row — thin wrapper for Vue."""
	return save_gsm_roll_line_to_spr(spr_name, roll_payload, shift=shift)


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_spr_doc(spr_name):
	"""GSM label / SPR Tools load. Desk read permission is not required for operators."""
	_gsm_require_desk_user()
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	d = frappe.get_doc("Shaft Production Run", spr_name).as_dict()
	spec = get_label_template_print_spec(d.get("custom_label"))
	if spec:
		d["_label_template"] = spec
	return d


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_spr_headers(spr_names=None):
	"""docstatus / unit / label for GSM — does not use frappe.client (no SPR Desk permission)."""
	_gsm_require_desk_user()
	names = _parse_json_arg(spr_names, [])
	if isinstance(names, str):
		names = [x.strip() for x in names.split(",") if x.strip()]
	names = [_cstr(x).strip() for x in (names or []) if _cstr(x).strip()]
	fields = ["name", "docstatus"]
	if frappe.db.has_column("Shaft Production Run", "custom_unit"):
		fields.append("custom_unit")
	if frappe.db.has_column("Shaft Production Run", "custom_label"):
		fields.append("custom_label")
	out = []
	for name in names:
		if not frappe.db.exists("Shaft Production Run", name):
			continue
		row = frappe.db.get_value("Shaft Production Run", name, fields, as_dict=True) or {}
		if row.get("name"):
			row["label_type"] = _gsm_label_type_display(row.get("custom_label") or "")
			out.append(row)
	return {"sprs": out}


@frappe.whitelist(methods=["GET", "POST"])
def get_warehouse_bays_for_unit(unit=None):
	"""Bay names for Production Session unit — Warehouse Bay.description like 'UNIT N%'.

	Additive helper only. Returns [] if DocType missing or unit blank.
	Reads with ignore_permissions — Warehouse Bay is often restricted to WMS roles,
	but Production Session needs the list for Unit 1–4 operators.
	"""
	raw = _cstr(unit).strip()
	if not raw:
		return []
	if not frappe.db.exists("DocType", "Warehouse Bay"):
		return []

	# "Unit 1" / "UNIT 1" / "unit-1" → "UNIT 1"
	norm = raw.upper().replace("-", " ").replace("_", " ")
	norm = " ".join(norm.split())
	parts = norm.split()
	if len(parts) >= 2 and parts[0] == "UNIT" and parts[1].isdigit():
		token = f"UNIT {parts[1]}"
	elif parts and parts[0].isdigit():
		token = f"UNIT {parts[0]}"
	elif norm.startswith("UNIT ") and len(norm) > 5:
		token = f"UNIT {parts[1]}" if len(parts) >= 2 and parts[1].isdigit() else norm
	else:
		token = norm

	# Match "UNIT 1 | Rack …" (starts with) or contains "UNIT 1 |"
	like_prefix = f"{token}%"
	like_pipe = f"%{token} |%"
	try:
		rows = frappe.db.sql(
			"""
			select name, bay_name, description
			from `tabWarehouse Bay`
			where ifnull(description, '') like %s
			   or ifnull(description, '') like %s
			   or upper(ifnull(description, '')) like %s
			order by bay_name asc
			limit 500
			""",
			(like_prefix, like_pipe, f"%{token}%"),
			as_dict=1,
		) or []
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_warehouse_bays_for_unit")
		return []

	out = []
	seen = set()
	for r in rows:
		desc = _cstr(r.get("description")).upper()
		# Prefer rows that actually belong to this unit token
		if token not in desc and not desc.startswith(token):
			continue
		bn = _cstr(r.get("bay_name") or r.get("name"))
		if not bn or bn in seen:
			continue
		seen.add(bn)
		out.append(
			{
				"name": _cstr(r.get("name") or bn),
				"bay_name": bn,
				"description": _cstr(r.get("description")),
			}
		)
	return out


@frappe.whitelist(methods=["GET", "POST"])
def delete_gsm_roll_line(spr_name, batch_no=None, row_name=None):
	"""GSM Remove Row — delete one saved roll line from draft SPR."""
	return delete_gsm_roll_line_from_spr(spr_name, batch_no=batch_no, row_name=row_name)


@frappe.whitelist(methods=["GET", "POST"])
def delete_gsm_bundle_packaging(spr_name, bundle_batch_no, child_roll_batches=None):
	"""GSM Remove bundle — child rolls, bundle sticker row, and -B1 summary line."""
	return delete_gsm_bundle_packaging_from_spr(
		spr_name,
		bundle_batch_no=bundle_batch_no,
		child_roll_batches=child_roll_batches,
	)


def _gsm_submittable_pp_to_spr(pp_to_spr: dict, rolls_by_pp: dict) -> dict:
	"""SPRs with grid rolls and/or rolls already saved on the draft SPR."""
	out = {}
	for pp_id, spr_name in (pp_to_spr or {}).items():
		if rolls_by_pp.get(pp_id):
			out[pp_id] = spr_name
		elif _gsm_spr_has_submittable_rolls(spr_name):
			out[pp_id] = spr_name
	return out


@frappe.whitelist(methods=["GET", "POST"])
def submit_gsm_production_entry(
	run_date=None,
	shift=None,
	unit=None,
	operator=None,
	supervisor=None,
	coordinator=None,
	rolls=None,
	session_sprs=None,
	tolerance_overrides=None,
	submit_sprs=True,
):
	"""
	GSM Step 2 — import rolls to draft SPRs, check existing SPR tolerance rules, submit.

	Does not change desk SPR flows. Uses the same tolerance_override_* fields and doc.submit()
	pipeline (manufacturing entries included).
	"""
	rolls = _parse_json_arg(rolls, [])
	session_sprs = _parse_json_arg(session_sprs, [])
	tolerance_overrides = _parse_json_arg(tolerance_overrides, [])
	submit_sprs = cint(submit_sprs)

	if not session_sprs:
		frappe.throw(_("Create SPRs first"))

	pp_to_spr = {}
	for row in session_sprs:
		if not isinstance(row, dict):
			continue
		pp_id = _cstr(row.get("pp_id") or row.get("ppId")).strip()
		spr_name = _cstr(row.get("spr_name")).strip()
		if pp_id and spr_name:
			spr_pp = _cstr(
				frappe.db.get_value("Shaft Production Run", spr_name, "production_plan") or ""
			).strip()
			if spr_pp and spr_pp != pp_id:
				frappe.throw(
					_("SPR {0} belongs to production plan {1}, not {2}").format(
						spr_name, spr_pp, pp_id
					)
				)
			pp_to_spr[pp_id] = spr_name

	if not pp_to_spr:
		frappe.throw(_("No SPR mapping in session"))

	pp_to_spr_all = dict(pp_to_spr)

	# Bundle packaging saves child rolls on SPR immediately; GSM grid may only show bundle summary rows.
	rolls_by_pp: dict[str, list] = {}
	for roll in rolls:
		if not isinstance(roll, dict):
			continue
		if cint(roll.get("is_bundle_row") or 0):
			continue
		pp_id = _cstr(roll.get("pp_id")).strip()
		if not pp_id:
			continue
		rolls_by_pp.setdefault(pp_id, []).append(roll)

	pp_to_spr = _gsm_submittable_pp_to_spr(pp_to_spr, rolls_by_pp)
	if not pp_to_spr and not any(_gsm_spr_has_submittable_rolls(sn) for sn in pp_to_spr_all.values()):
		frappe.throw(_("No roll lines to submit"))

	skipped_empty = [
		{"pp_id": pp_id, "spr_name": spr_name}
		for pp_id, spr_name in pp_to_spr_all.items()
		if pp_id not in pp_to_spr
	]

	override_by_spr = {}
	for ov in tolerance_overrides:
		if not isinstance(ov, dict):
			continue
		sn = _cstr(ov.get("spr_name")).strip()
		if sn:
			override_by_spr[sn] = ov

	imported = []
	import_failed = []
	for pp_id, spr_name in pp_to_spr.items():
		payloads = rolls_by_pp.get(pp_id) or []
		if not payloads:
			if _gsm_spr_has_submittable_rolls(spr_name):
				imported.append(
					{
						"pp_id": pp_id,
						"spr_name": spr_name,
						"added": 0,
						"updated": 0,
						"skipped": "rolls_already_on_spr",
					}
				)
			continue
		try:
			spr = frappe.get_doc("Shaft Production Run", spr_name)
			# Resume can include already-submitted SPRs (docstatus=1).
			# Import is only allowed for draft SPRs (docstatus=0), so skip import for submitted SPRs.
			if cint(spr.docstatus) != 0:
				imported.append(
					{
						"pp_id": pp_id,
						"spr_name": spr_name,
						"added": 0,
						"updated": 0,
						"skipped": "spr_already_submitted",
					}
				)
				continue
			if _apply_gsm_session_header_to_spr(spr, run_date, shift, unit, operator, supervisor, coordinator):
				spr.save(ignore_permissions=True)
			res = import_gsm_roll_lines_to_spr(spr_name, payloads, shift=shift)
			imported.append({"pp_id": pp_id, "spr_name": spr_name, **res})
		except Exception as e:
			import_failed.append({"pp_id": pp_id, "spr_name": spr_name, "error": _cstr(e)})

	if import_failed:
		return {
			"status": "import_failed",
			"imported": imported,
			"failed": import_failed,
		}

	spr_names = list(pp_to_spr.values())
	tolerance_orders = _gsm_tolerance_orders_from_sprs(spr_names)

	if tolerance_orders and submit_sprs:
		missing_override = []
		for order in tolerance_orders:
			sn = order["spr_name"]
			ov = override_by_spr.get(sn) or {}
			reason = _cstr(ov.get("reason") or ov.get("tolerance_override_reason")).strip()
			approved = cint(ov.get("approved") or ov.get("tolerance_override_approved"))
			if not (approved and reason):
				missing_override.append(sn)
		if missing_override:
			return {
				"status": "tolerance_required",
				"imported": imported,
				"orders": tolerance_orders,
				"message": _("Tolerance approval required for one or more orders"),
			}

	if not submit_sprs:
		return {
			"status": "imported",
			"imported": imported,
			"orders": tolerance_orders,
		}

	submitted = []
	submit_failed = []
	for pp_id, spr_name in pp_to_spr.items():
		try:
			ov = override_by_spr.get(spr_name) or {}
			reason = _cstr(ov.get("reason") or ov.get("tolerance_override_reason")).strip()
			approved = cint(ov.get("approved") or ov.get("tolerance_override_approved"))
			if reason and approved:
				_gsm_apply_tolerance_override(spr_name, reason, approved)
			doc = frappe.get_doc("Shaft Production Run", spr_name)
			if cint(doc.docstatus) == 0:
				doc.flags.ignore_permissions = True
				doc.submit()
			submitted.append(
				{
					"pp_id": pp_id,
					"spr_name": spr_name,
					"order_code": _cstr(doc.get("custom_order_code") or ""),
					"roll_count": len(doc.items or []),
				}
			)
		except Exception as e:
			submit_failed.append({"pp_id": pp_id, "spr_name": spr_name, "error": _spr_exc_message(e) or _cstr(e)})

	status = "ok"
	if submit_failed and submitted:
		status = "partial"
	elif submit_failed and not submitted:
		status = "failed"

	total_kg = 0.0
	for row in submitted:
		try:
			doc = frappe.get_doc("Shaft Production Run", row["spr_name"])
			total_kg += sum(flt(getattr(it, "net_weight", 0)) for it in (doc.items or []))
		except Exception:
			pass

	return {
		"status": status,
		"imported": imported,
		"submitted": submitted,
		"failed": submit_failed,
		"skipped_empty": skipped_empty,
		"total_kg": round(total_kg, 2),
	}


@frappe.whitelist(methods=["GET", "POST"])
def gsm_apply_bundle_packaging(
	shaft_production_run,
	job_id,
	width_inch=None,
	no_of_packaging=None,
	whole_gross_kg=None,
	produced_length_mtrs=None,
	pp_id=None,
	width_mix=None,
):
	"""GSM Tools → Bundle packaging (fresh rolls + bundle sticker on SPR)."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		gsm_apply_bundle_packaging as _gsm_bundle,
	)

	return _gsm_bundle(
		shaft_production_run,
		job_id,
		width_inch=width_inch,
		no_of_packaging=no_of_packaging,
		whole_gross_kg=whole_gross_kg,
		produced_length_mtrs=produced_length_mtrs,
		pp_id=pp_id,
		width_mix=width_mix,
	)


# ---------------------------------------------------------------------------
# GSM Wastage / Recycle (SPR child tables — desk Client Script handles inventory)
# ---------------------------------------------------------------------------

_GSM_WASTAGE_CHILD_SPECS = (
	# Live site child DocTypes (Customize Form) — preferred first
	("custom_running_patty_wastage", "Shaft Production Run Wastage"),
	("custom_running_patty_wastage", "Running Patty Wastage Row"),
	("custom_roll_waste", "Roll Waste Row"),
	("custom_recycled_wastage_details", "Recycled Wastage Details"),
	("custom_recycled_wastage_details", "Recycled Wastage Detail Row"),
	("custom_gsm_manual_recycle_details", "GSM Manual Recycle Row"),
)

_GSM_MANUAL_RECYCLE_FIELD = "custom_gsm_manual_recycle_details"
_GSM_MANUAL_RECYCLE_DOCTYPE = "GSM Manual Recycle Row"


def _gsm_resolve_spr_child_field(spr_meta, preferred_fieldname: str, child_doctype: str) -> str | None:
	if spr_meta.has_field(preferred_fieldname):
		return preferred_fieldname
	for df in spr_meta.fields or []:
		if df.fieldtype == "Table" and _cstr(df.options) == child_doctype:
			return df.fieldname
	return None


def _gsm_load_spr_child_rows(spr_doc, preferred_fieldname: str, child_doctype: str) -> tuple[str | None, list]:
	"""Return (resolved_fieldname, rows) with DB fallback when meta/Doc child cache is empty."""
	spr_meta = frappe.get_meta("Shaft Production Run")
	resolved = _gsm_resolve_spr_child_field(spr_meta, preferred_fieldname, child_doctype)

	for fieldname in dict.fromkeys(
		[f for f in (resolved, preferred_fieldname) if f]
		+ [
			df.fieldname
			for df in spr_meta.fields or []
			if df.fieldtype == "Table" and _cstr(df.options) == child_doctype
		]
	):
		rows = list(getattr(spr_doc, fieldname, None) or [])
		if rows:
			return fieldname, rows

	if not frappe.db.table_exists(child_doctype):
		# Live sites may use a renamed child DocType — scan SPR table fields.
		for df in spr_meta.fields or []:
			if df.fieldtype != "Table":
				continue
			if preferred_fieldname and df.fieldname == preferred_fieldname:
				continue
			if child_doctype.lower() not in _cstr(df.options or "").lower() and "patty" not in _cstr(
				df.fieldname or ""
			).lower():
				if "recycl" not in child_doctype.lower() and "roll waste" not in child_doctype.lower():
					continue
			alt_dt = _cstr(df.options)
			if not alt_dt or not frappe.db.table_exists(alt_dt):
				continue
			alt_rows = frappe.get_all(
				alt_dt,
				filters={"parent": spr_doc.name, "parenttype": "Shaft Production Run"},
				fields=["*"],
				order_by="idx asc",
				limit=500,
				ignore_permissions=True,
			)
			if alt_rows:
				parentfield = _cstr(alt_rows[0].get("parentfield") or df.fieldname)
				return parentfield or df.fieldname, [frappe._dict(r) for r in alt_rows]
		return resolved, []

	db_rows = frappe.get_all(
		child_doctype,
		filters={"parent": spr_doc.name, "parenttype": "Shaft Production Run"},
		fields=["*"],
		order_by="idx asc",
		limit=500,
		ignore_permissions=True,
	)
	if not db_rows:
		return resolved, []

	parentfield = _cstr(db_rows[0].get("parentfield") or resolved or preferred_fieldname)
	return parentfield or resolved, [frappe._dict(r) for r in db_rows]

_PATTY_STOCK_METHOD_CANDIDATES = (
	"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_available_patty_stock",
	"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_available_patty_stock",
	"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_patty_stock_for_spr",
)

# Logical child-row keys → possible DocField names on live sites (repo scaffolds differ).
# Live Customize Form (SPR): Shaft Production Run Wastage + Recycled Wastage Details.
_GSM_CHILD_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
	"job_id": ("job_id", "job"),
	"quality": ("quality", "custom_quality"),
	"color": ("color", "fabric_colour", "custom_color"),
	"gsm": ("gsm",),
	"width_inch": (
		"width_inches",
		"width_inch",
		"width",
		"w",
		"custom_width_inch",
		"custom_width",
		"trim_width",
		"patty_width",
	),
	"width": (
		"width",
		"width_inches",
		"width_inch",
		"w",
		"custom_width_inch",
		"custom_width",
		"trim_width",
		"patty_width",
	),
	"meter_per_roll": (
		"meter__roll_mtrs",
		"meter_roll_mtrs",
		"meter__roll",
		"meter_per_roll",
		"meter_roll",
		"meter",
		"produced_length_mtrs",
		"produced_length_mtr",
	),
	"no_of_shafts": ("no_of_shafts", "shafts", "no_of_shaft"),
	# NEVER cross-map wastage ↔ net_wastage: Recycle to Next sets net=0 and must not wipe qty.
	"wastage": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
	"wastage_qty": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
	"net_wastage": ("net_wastage", "net_wastage_kg", "net_wastage_kgs"),
	"one_shaft_gross": ("one_shaft_gross", "one_shaft_wastage", "one_shaft"),
	"recycled": ("recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"),
	"recycled_qty": ("recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"),
	"available": ("available_qty_kgs", "available_qty", "available", "available_kg"),
	"available_qty": ("available_qty_kgs", "available_qty", "available", "available_kg"),
	"available_kg": ("available_qty_kgs", "available_kg", "available", "available_qty"),
	"batch_no": ("batch_no", "batch", "source_roll"),
	"source_roll": ("source_roll", "batch_no", "batch"),
	"source_roll_waste_row": ("source_roll_waste_row", "roll_waste_row", "spr_item_name"),
	"spr_item_name": ("spr_item_name", "source_roll_waste_row"),
	"recycle_to_next": ("recycle_to_next", "custom_recycle_to_next"),
	"calculation_details": ("calculation_details",),
	"item": ("item", "item_code"),
	"item_name": ("item_name",),
}


def _gsm_recycled_child_doctype() -> str:
	meta = frappe.get_meta("Shaft Production Run")
	df = meta.get_field("custom_recycled_wastage_details") if meta else None
	opts = _cstr(getattr(df, "options", None) or "")
	if opts and frappe.db.exists("DocType", opts):
		return opts
	for cand in ("Recycled Wastage Details", "Recycled Wastage Detail Row"):
		if frappe.db.exists("DocType", cand):
			return cand
	return "Recycled Wastage Details"


def _gsm_manual_recycle_field_and_doctype() -> tuple[str, str]:
	"""Parent field + child DocType for GSM View Patty Stock / manual recycle."""
	meta = frappe.get_meta("Shaft Production Run")
	fieldname = _GSM_MANUAL_RECYCLE_FIELD
	child_dt = _GSM_MANUAL_RECYCLE_DOCTYPE
	if meta and meta.has_field(fieldname):
		df = meta.get_field(fieldname)
		child_dt = _cstr(getattr(df, "options", None) or child_dt) or child_dt
		return fieldname, child_dt
	# Fall back to any Table whose options match the manual recycle child.
	for df in (meta.fields if meta else []) or []:
		if df.fieldtype == "Table" and _cstr(df.options) == _GSM_MANUAL_RECYCLE_DOCTYPE:
			return df.fieldname, _GSM_MANUAL_RECYCLE_DOCTYPE
	return fieldname, child_dt


def _gsm_child_writable_fields(child_doctype: str) -> set[str]:
	meta = frappe.get_meta(child_doctype)
	existing = {df.fieldname for df in meta.fields}
	for fn in (
		"job_id",
		"quality",
		"color",
		"gsm",
		"width_inch",
		"width",
		"w",
		"custom_width_inch",
		"custom_width",
		"trim_width",
		"patty_width",
		"meter_per_roll",
		"no_of_shafts",
		"wastage",
		"wastage_qty",
		"net_wastage",
		"recycled",
		"recycled_qty",
		"available",
		"available_qty",
		"batch_no",
		"source_roll",
		"source_roll_waste_row",
		"recycle_to_next",
		"custom_recycle_to_next",
	):
		try:
			if frappe.db.has_column(child_doctype, fn):
				existing.add(fn)
		except Exception:
			pass
	return existing


def _gsm_width_fieldnames(row_or_doctype) -> list[str]:
	"""Every live child field that stores fabric/patty width (not core width)."""
	dt = row_or_doctype
	if not isinstance(dt, str):
		dt = _cstr(getattr(row_or_doctype, "doctype", None) or _gsm_recycled_child_doctype())
	names = []
	seen = set()
	try:
		meta = frappe.get_meta(dt)
	except Exception:
		meta = None
	for df in (meta.fields if meta else []) or []:
		fn = _cstr(getattr(df, "fieldname", "") or "")
		label = _cstr(getattr(df, "label", "") or "").lower()
		if not fn or fn in seen:
			continue
		low = fn.lower()
		if "core" in low:
			continue
		if "width" in low or "width" in label:
			seen.add(fn)
			names.append(fn)
	for fn in _GSM_CHILD_FIELD_ALIASES.get("width_inch", ()):
		if fn not in seen:
			seen.add(fn)
			names.append(fn)
	return names


def _gsm_write_child_row(child_doctype: str, logical: dict) -> dict:
	"""Write child row values using whichever fieldnames exist on the live child DocType."""
	existing = _gsm_child_writable_fields(child_doctype)
	wastage_fields = {fn for fn in ("wastage", "wastage_qty", "wastage_qt") if fn in existing}
	out: dict = {}
	for logical_key, val in (logical or {}).items():
		if val is None:
			continue
		if isinstance(val, str) and not val.strip():
			continue
		if logical_key in ("width_inch", "width", "w") and flt(val) <= 0:
			continue
		aliases = _GSM_CHILD_FIELD_ALIASES.get(logical_key, (logical_key,))
		wrote = False
		for fn in aliases:
			if fn not in existing:
				continue
			# Recycle to Next sets net_wastage=0 — never write that onto wastage qty columns
			if logical_key in ("net_wastage",) and fn in wastage_fields:
				continue
			out[fn] = val
			wrote = True
		if not wrote and logical_key in existing:
			if logical_key in ("net_wastage",) and logical_key in wastage_fields:
				pass
			else:
				out[logical_key] = val
	width_out = flt((logical or {}).get("width_inch") or (logical or {}).get("width") or out.get("width_inch") or 0)
	if width_out > 0:
		for fn in _gsm_width_fieldnames(child_doctype):
			if fn in existing:
				out[fn] = width_out
	return out


def _gsm_pick_positive_qty(row, keys) -> float:
	"""Return the first strictly positive qty. Zero is stored on unused aliases and must not win."""
	if not row:
		return 0.0
	if isinstance(row, dict):
		getter = row.get
	else:
		getter = lambda k, d=None: getattr(row, k, d)
	for k in keys:
		try:
			v = flt(getter(k, None) or 0)
		except Exception:
			continue
		if v > 0:
			return v
	return 0.0


def _gsm_qty_from_spr_item(spr_doc, job_id="", batch_no="", spr_item_name="") -> float:
	"""Net kg of the source production roll (used when recycled/wastage child qty was saved as 0)."""
	items = list(getattr(spr_doc, "items", None) or [])
	if not items:
		return 0.0
	spr_item_name = _cstr(spr_item_name)
	batch_no = _cstr(batch_no)
	job_id = _cstr(job_id)

	def _item_kg(it) -> float:
		return _gsm_pick_positive_qty(
			it,
			("net_weight", "gross_weight", "qty", "fg_completed_qty"),
		)

	if spr_item_name:
		for it in items:
			if _cstr(getattr(it, "name", None)) == spr_item_name:
				kg = _item_kg(it)
				if kg > 0:
					return kg
	if batch_no:
		for it in items:
			if _cstr(getattr(it, "batch_no", None)) == batch_no:
				kg = _item_kg(it)
				if kg > 0:
					return kg
	if job_id:
		for it in items:
			if _cstr(getattr(it, "job", None) or getattr(it, "job_id", None)) != job_id:
				continue
			kg = _item_kg(it)
			if kg > 0:
				return kg
	return 0.0


_GSM_STAMP_SKIP_KEYS = {
	"name",
	"owner",
	"creation",
	"modified",
	"modified_by",
	"parent",
	"parentfield",
	"parenttype",
	"idx",
	"docstatus",
	"doctype",
}
_GSM_QTY_STAMP_KEYS = {
	"available_kg",
	"available",
	"available_qty",
	"wastage",
	"wastage_qty",
	"net_wastage",
	"recycled",
	"recycled_qty",
}
_GSM_QTY_FIELDS = (
	"wastage",
	"wastage_qty",
	"net_wastage",
	"recycled",
	"recycled_qty",
	"available",
	"available_qty",
	"available_kg",
)


def _gsm_stamp_child_values(row, logical: dict) -> None:
	"""Force-set mapped values on an appended child row (covers Custom Field / alias names)."""
	if row is None:
		return
	dt = _cstr(getattr(row, "doctype", None) or _gsm_recycled_child_doctype())
	writable = _gsm_child_writable_fields(dt)
	width_val = flt(
		_pick_value(logical or {}, ["width_inch", "width", "w", "custom_width_inch", "custom_width"], 0)
	)
	qty_val = _gsm_pick_positive_qty(logical or {}, list(_GSM_QTY_STAMP_KEYS))
	for key, val in (logical or {}).items():
		if key in _GSM_STAMP_SKIP_KEYS:
			continue
		if val is None:
			continue
		if isinstance(val, str) and not val.strip():
			continue
		if key in ("width_inch", "width", "w", "custom_width_inch", "custom_width") and flt(val) <= 0:
			continue
		if key in _GSM_QTY_STAMP_KEYS and flt(val) <= 0:
			continue
		if key in ("batch_no", "batch", "source_roll") and not _gsm_is_real_batch_no(val):
			continue
		aliases = _GSM_CHILD_FIELD_ALIASES.get(key, (key,))
		for fn in aliases:
			if fn not in writable:
				continue
			try:
				row.set(fn, val)
			except Exception:
				setattr(row, fn, val)
	if width_val > 0:
		_gsm_stamp_width_on_child(row, width_val)
	if qty_val > 0:
		_gsm_stamp_qty_on_child(row, qty_val)


def _gsm_stamp_qty_on_child(row, qty) -> None:
	qty = flt(qty)
	if row is None or qty <= 0:
		return
	writable = _gsm_child_writable_fields(_cstr(getattr(row, "doctype", None) or ""))
	for fn in _GSM_QTY_FIELDS:
		if writable and fn not in writable:
			continue
		try:
			row.set(fn, qty)
		except Exception:
			try:
				setattr(row, fn, qty)
			except Exception:
				pass


def _gsm_stamp_width_on_child(row, width) -> None:
	width = flt(width)
	if row is None or width <= 0:
		return
	writable = _gsm_child_writable_fields(_cstr(getattr(row, "doctype", None) or ""))
	for fn in _gsm_width_fieldnames(row):
		if writable and fn not in writable:
			try:
				if not frappe.db.has_column(getattr(row, "doctype", None) or "", fn):
					continue
			except Exception:
				continue
		try:
			row.set(fn, width)
		except Exception:
			try:
				setattr(row, fn, width)
			except Exception:
				pass


def _gsm_child_table_columns(child_doctype: str) -> list[dict]:
	meta = frappe.get_meta(child_doctype)
	cols: list[dict] = []
	seen: set[str] = set()
	for df in meta.fields:
		if df.fieldtype in ("Section Break", "Column Break", "Table", "HTML", "Button"):
			continue
		if df.fieldname in seen:
			continue
		if df.in_list_view or df.fieldtype in (
			"Data",
			"Float",
			"Int",
			"Check",
			"Link",
			"Select",
			"Currency",
			"Date",
		):
			seen.add(df.fieldname)
			cols.append({"fieldname": df.fieldname, "label": df.label, "fieldtype": df.fieldtype})
	return cols


def _gsm_child_row_dict(row, columns: list[dict]) -> dict:
	# Prefer `as_dict()` since Frappe child rows may expose values via doc dict.
	src = row.as_dict() if hasattr(row, "as_dict") else {}
	out = {"name": getattr(row, "name", None) or src.get("name")}

	def _get(k: str):
		if isinstance(src, dict) and k in src:
			return src.get(k)
		return getattr(row, k, None)

	# Field fallbacks for "db-only" columns that may have different stored fieldnames
	# on the live site vs the repo scaffolds.
	FALLBACKS = {k: v for k, v in _GSM_CHILD_FIELD_ALIASES.items()}
	qty_or_width = {
		"width_inch",
		"width",
		"w",
		"custom_width_inch",
		"custom_width",
		"trim_width",
		"patty_width",
		"wastage",
		"wastage_qty",
		"recycled",
		"recycled_qty",
		"available",
		"available_qty",
		"available_kg",
		"net_wastage",
	}

	def _empty(fn, val):
		if val is None:
			return True
		if isinstance(val, str) and not str(val).strip():
			return True
		if fn in qty_or_width or "width" in _cstr(fn).lower():
			return flt(val or 0) <= 0
		return False

	for col in columns:
		fn = col["fieldname"]
		val = _get(fn)
		if _empty(fn, val) and fn in FALLBACKS:
			for alt in FALLBACKS[fn]:
				if alt == fn:
					continue
				alt_val = _get(alt)
				if not _empty(alt, alt_val):
					val = alt_val
					break
		if val is not None and hasattr(val, "isoformat"):
			val = str(val)
		out[fn] = val
	if isinstance(src, dict):
		found_w = flt(_pick_value(out, ["width_inch", "width", "w", "custom_width_inch", "custom_width"], 0))
		if found_w <= 0:
			for k, v in src.items():
				if "width" in _cstr(k).lower() and "core" not in _cstr(k).lower() and flt(v or 0) > 0:
					found_w = flt(v)
					break
		if found_w > 0:
			out["width_inch"] = found_w
			out["width"] = found_w
		# Normalize live Customize Form fieldnames → logical keys for GSM dialog cards
		w_qty = flt(_pick_value(out, ["wastage_qty_kgs", "wastage_qty", "wastage", "wastage_qt"], 0))
		if w_qty > 0:
			out["wastage"] = w_qty
			out["wastage_qty"] = w_qty
		mtr = flt(
			_pick_value(
				out,
				["meter__roll_mtrs", "meter_roll_mtrs", "meter__roll", "meter_per_roll", "meter_roll", "meter"],
				0,
			)
		)
		if mtr > 0:
			out["meter_per_roll"] = mtr
		avail = flt(
			_pick_value(out, ["available_qty_kgs", "available_qty", "available", "available_kg"], 0)
		)
		if avail > 0:
			out["available"] = avail
			out["available_qty"] = avail
			out["available_kg"] = avail
		recy = flt(
			_pick_value(out, ["recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"], 0)
		)
		if recy > 0:
			out["recycled"] = recy
			out["recycled_qty"] = recy
	return out


def _gsm_pick_row_val(row_dict: dict, *keys):
	for k in keys:
		if k not in row_dict:
			continue
		v = row_dict.get(k)
		if v is None:
			continue
		if isinstance(v, str) and not v.strip():
			continue
		return v
	return None


def _gsm_enrich_child_row_from_spr(spr_doc, row_dict: dict, child_doctype: str = "") -> dict:
	"""Fill width/meter/batch/recycled aliases from SPR roll lines when live child fields differ."""
	if not isinstance(row_dict, dict):
		return row_dict or {}
	out = dict(row_dict)
	job_id = _cstr(_gsm_pick_row_val(out, "job_id", "job"))
	items = list(getattr(spr_doc, "items", None) or [])
	job_items = [it for it in items if _cstr(getattr(it, "job", "")) == job_id] if job_id else items

	is_patty_wastage = (
		"patty" in child_doctype.lower()
		or (
			"wastage" in child_doctype.lower()
			and "recycl" not in child_doctype.lower()
			and "roll waste" not in child_doctype.lower()
		)
		or "patty" in _cstr(out.get("parentfield") or "").lower()
		or _cstr(out.get("parentfield") or "") == "custom_running_patty_wastage"
	)
	is_recycled = (
		"recycl" in child_doctype.lower()
		or "recycl" in _cstr(out.get("parentfield") or "").lower()
		or _cstr(out.get("parentfield") or "") == "custom_recycled_wastage_details"
	)
	# Patty-stock recycle rows have no job_id — do not copy production-roll width/meter/batch.
	skip_spr_roll_fill = is_patty_wastage or (is_recycled and not job_id)
	if is_recycled:
		kg = _gsm_pick_positive_qty(
			out,
			(
				"wastage_qty_kgs",
				"wastage",
				"wastage_qty",
				"recycled_qty_kgs",
				"recycled",
				"recycled_qty",
				"available_qty_kgs",
				"available_kg",
				"available",
				"net_wastage",
				"net_weight",
			),
		)
		if kg <= 0:
			kg = _gsm_qty_from_spr_item(
				spr_doc,
				job_id=job_id,
				batch_no=_cstr(_gsm_pick_row_val(out, "batch_no", "batch", "source_roll") or ""),
				spr_item_name=_cstr(_gsm_pick_row_val(out, "spr_item_name") or ""),
			)
		if kg > 0:
			if flt(_gsm_pick_row_val(out, "recycled", "recycled_qty") or 0) <= 0:
				out["recycled"] = kg
				out["recycled_qty"] = kg
			if flt(_gsm_pick_row_val(out, "wastage", "wastage_qty") or 0) <= 0:
				out["wastage"] = kg
				out["wastage_qty"] = kg
			out["available_kg"] = out.get("available_kg") or kg
			out["available"] = out.get("available") or kg
	if is_recycled and not job_id:
		bn_now = _cstr(_gsm_pick_row_val(out, "batch_no", "batch", "source_roll") or "")
		if bn_now and not _gsm_is_real_batch_no(bn_now):
			out["batch_no"] = ""
			out["source_roll"] = ""
		filled = _gsm_hydrate_patty_selection(out)
		filled_w = flt(
			filled.get("width_inch")
			or filled.get("width")
			or filled.get("custom_width_inch")
			or 0
		)
		if filled_w > 0:
			out["width_inch"] = filled_w
			out["width"] = filled_w
			out["w"] = filled_w
			out["custom_width_inch"] = filled_w
			out["custom_width"] = filled_w
		kg = flt(filled.get("available_kg") or filled.get("recycled") or filled.get("wastage") or 0)
		if kg > 0:
			if flt(_gsm_pick_row_val(out, "recycled", "recycled_qty") or 0) <= 0:
				out["recycled"] = kg
				out["recycled_qty"] = kg
			if flt(_gsm_pick_row_val(out, "wastage", "wastage_qty") or 0) <= 0:
				out["wastage"] = kg
				out["wastage_qty"] = kg
			out["available_kg"] = kg
			out["available"] = kg
		if filled.get("batch_no") and _gsm_is_real_batch_no(filled.get("batch_no")):
			out["batch_no"] = filled.get("batch_no")
			out["source_roll"] = filled.get("batch_no")
	width = flt(_gsm_pick_row_val(out, "width_inch", "width", "w") or 0)
	# Patty width is unit trim (10/12/14/15), not the production roll width.
	if width <= 0 and not skip_spr_roll_fill:
		for it in job_items:
			width = flt(getattr(it, "width_inch", 0) or 0)
			if width > 0:
				out["width_inch"] = width
				out["width"] = width
				break

	meter = flt(
		_gsm_pick_row_val(
			out,
			"meter_per_roll",
			"meter_roll",
			"meter",
			"produced_length_mtrs",
			"produced_length_mtr",
		)
		or 0
	)
	if meter <= 0 and not skip_spr_roll_fill:
		for it in job_items:
			meter = flt(getattr(it, "meter_roll", 0) or getattr(it, "produced_length_mtrs", 0) or 0)
			if meter > 0:
				out["meter_per_roll"] = meter
				out["meter_roll"] = meter
				break

	batch_no = _cstr(_gsm_pick_row_val(out, "batch_no", "batch", "source_roll", "source_batch") or "")
	if not batch_no and is_recycled and not job_id:
		pass
	elif not batch_no:
		if is_patty_wastage:
			for it in job_items:
				candidate = _cstr(getattr(it, "batch_no", "") or "")
				if candidate and "W/" in candidate:
					batch_no = candidate
					break
			if not batch_no:
				for it in job_items:
					roll_batch = _cstr(getattr(it, "batch_no", "") or "")
					if roll_batch and "/" in roll_batch:
						idx = roll_batch.rfind("/")
						if idx > 0:
							batch_no = f"{roll_batch[:idx]}W{roll_batch[idx:]}"
							break
		else:
			for it in job_items:
				batch_no = _cstr(getattr(it, "batch_no", "") or "")
				if batch_no:
					break
		if batch_no:
			out["batch_no"] = batch_no
			if not out.get("source_roll"):
				out["source_roll"] = batch_no

	if child_doctype == "Roll Waste Row" or "roll_waste" in _cstr(out.get("parentfield") or "").lower():
		rn = cint(_gsm_pick_row_val(out, "roll_number", "roll_no") or 0)
		if rn <= 0 and batch_no:
			rn = _gsm_roll_number_from_batch(batch_no)
		if rn > 0:
			out["roll_number"] = rn

	recycled_qty = flt(_gsm_pick_row_val(out, "recycled_qty", "recycled", "recycled_kg") or 0)
	available_qty = flt(
		_gsm_pick_row_val(out, "available_qty", "available", "available_kg", "wastage_qty", "wastage", "net_wastage")
		or 0
	)
	if "recycled" in child_doctype.lower() or "recycled" in _cstr(out.get("parentfield") or ""):
		if recycled_qty <= 0 and available_qty > 0:
			recycled_qty = available_qty
		if recycled_qty > 0:
			out["recycled"] = recycled_qty
			out["recycled_qty"] = recycled_qty
			if available_qty <= 0:
				out["available_qty"] = recycled_qty
				out["available"] = recycled_qty

	wastage_qty = flt(_gsm_pick_row_val(out, "wastage_qty", "wastage", "wastage_qt", "net_wastage") or 0)
	if wastage_qty > 0:
		out["wastage"] = wastage_qty
		if not out.get("net_wastage"):
			out["net_wastage"] = wastage_qty

	quality = _cstr(_gsm_pick_row_val(out, "quality", "custom_quality") or "")
	color = _cstr(_gsm_pick_row_val(out, "color", "fabric_colour", "custom_color") or "")
	gsm = cint(_gsm_pick_row_val(out, "gsm") or 0)
	item_code = _cstr(_gsm_pick_row_val(out, "item_code") or "")
	item_name = _cstr(_gsm_pick_row_val(out, "item_name") or "")
	for it in job_items:
		if not item_code:
			item_code = _cstr(getattr(it, "item_code", "") or "")
		if not item_name:
			item_name = _cstr(getattr(it, "item_name", "") or "")
		if not quality:
			quality = _cstr(getattr(it, "quality", "") or "")
		if not color:
			color = _cstr(
				getattr(it, "color", None) or getattr(it, "fabric_colour", None) or ""
			)
		if gsm <= 0:
			gsm = cint(getattr(it, "gsm", 0) or 0)
		if quality and color and gsm > 0:
			break
	if item_code and (not quality or not color or gsm <= 0):
		try:
			from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
				_spr_resolve_roll_line_specs_from_item_code,
			)

			specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
			if not quality:
				quality = _cstr(specs.get("quality") or "")
			if not color:
				color = _cstr(specs.get("color") or "")
			if gsm <= 0:
				gsm = cint(specs.get("gsm") or 0)
		except Exception:
			pass
	if quality:
		out["quality"] = quality
	if color:
		out["color"] = color
	if gsm > 0:
		out["gsm"] = gsm
	if item_code and not out.get("item_code"):
		out["item_code"] = item_code
	if item_name and not out.get("item_name"):
		out["item_name"] = item_name

	return out


def _gsm_spr_child_table_payload(spr_doc, fieldname: str, child_doctype: str) -> dict:
	resolved_field, rows = _gsm_load_spr_child_rows(spr_doc, fieldname, child_doctype)
	columns = _gsm_child_table_columns(child_doctype)
	enriched_rows = []
	for r in rows:
		row_dict = _gsm_child_row_dict(r, columns)
		if resolved_field:
			row_dict["parentfield"] = resolved_field
		enriched_rows.append(_gsm_enrich_child_row_from_spr(spr_doc, row_dict, child_doctype))
	if child_doctype == "Roll Waste Row" or "roll waste" in _cstr(child_doctype).lower():
		enriched_rows = _gsm_unique_roll_waste_row_dicts(enriched_rows)
	return {
		"fieldname": resolved_field or fieldname,
		"resolved_fieldname": resolved_field or fieldname,
		"child_doctype": child_doctype,
		"columns": columns,
		"rows": enriched_rows,
	}


def _gsm_map_to_recycled_row(src, from_roll_waste: bool = False, spr_doc=None, child_doctype=None) -> dict:
	if not from_roll_waste:
		src = _gsm_hydrate_patty_selection(src)
	elif not isinstance(src, dict):
		src = src.as_dict() if hasattr(src, "as_dict") else {}

	qty_keys = (
		"available_kg",
		"available",
		"available_qty",
		"wastage",
		"wastage_qty",
		"wastage_qt",
		"net_wastage",
		"net_wastage_kg",
		"net_weight",
		"gross_weight",
		"qty",
		"batch_qty",
		"actual_qty",
		"recycled",
		"recycled_qty",
		"recycled_kg",
	)
	available_qty = _gsm_pick_positive_qty(
		src, ("available_kg", "available", "available_qty", "qty", "batch_qty", "actual_qty")
	)
	wastage_qty = _gsm_pick_positive_qty(
		src, ("wastage", "wastage_qty", "wastage_qt", "net_wastage", "net_wastage_kg", "net_weight", "gross_weight")
	)
	recycled_qty = _gsm_pick_positive_qty(src, ("recycled", "recycled_qty", "recycled_kg"))
	consume_kg = available_qty or wastage_qty or recycled_qty
	if consume_kg <= 0 and spr_doc is not None:
		consume_kg = _gsm_qty_from_spr_item(
			spr_doc,
			job_id=_cstr(_pick_value(src, ["job_id", "job"], "")),
			batch_no=_cstr(_pick_value(src, ["batch_no", "batch", "source_roll"], "")),
			spr_item_name=_cstr(_pick_value(src, ["spr_item_name"], "")),
		)
	if from_roll_waste and consume_kg <= 0:
		consume_kg = _gsm_pick_positive_qty(src, qty_keys)
	if wastage_qty <= 0:
		wastage_qty = consume_kg
	if recycled_qty <= 0:
		recycled_qty = consume_kg

	batch_no = _cstr(_pick_value(src, ["batch_no", "batch", "source_roll"], ""))
	if not _gsm_is_real_batch_no(batch_no):
		maybe_name = _cstr(_pick_value(src, ["name"], ""))
		batch_no = maybe_name if _gsm_is_real_batch_no(maybe_name) else ""
	if not from_roll_waste and consume_kg <= 0:
		frappe.throw(
			_("No available quantity to recycle for batch {0}").format(batch_no or _("selected row"))
		)
	logical = {
		"job_id": _cstr(_pick_value(src, ["job_id", "job"], "")),
		"quality": _cstr(_pick_value(src, ["quality"], "")),
		"color": _cstr(_pick_value(src, ["color"], "")),
		"gsm": cint(_pick_value(src, ["gsm"], 0)),
		"width_inch": flt(
			_pick_value(
				src,
				["width_inch", "width", "w", "custom_width_inch", "custom_width", "trim_width", "patty_width"],
				0,
			)
		),
		"meter_per_roll": flt(
			_pick_value(
				src,
				["meter_per_roll", "meter_roll", "meter", "produced_length_mtrs", "produced_length_mtr"],
				0,
			)
		),
		"no_of_shafts": cint(_pick_value(src, ["no_of_shafts", "shafts", "no_of_shaft"], 0)),
		"wastage": wastage_qty,
		"wastage_qty": wastage_qty,
		"net_wastage": wastage_qty,
		"recycled": recycled_qty,
		"recycled_qty": recycled_qty,
		"available": consume_kg,
		"available_qty": consume_kg,
		"batch_no": batch_no,
		"source_roll": batch_no,
	}
	if from_roll_waste:
		row_name = _cstr(_pick_value(src, ["name"], ""))
		if row_name:
			logical["source_roll_waste_row"] = row_name
	target_dt = _cstr(child_doctype).strip() or _gsm_recycled_child_doctype()
	return _gsm_write_child_row(target_dt, logical)


def _gsm_build_roll_waste_row_from_item(item_row, roll_payload: dict | None = None) -> dict:
	roll_payload = roll_payload if isinstance(roll_payload, dict) else {}
	item_code = _cstr(
		_pick_value(roll_payload, ["item_code"]) or getattr(item_row, "item_code", None) or ""
	)
	item_name = _cstr(
		_pick_value(roll_payload, ["item_name"]) or getattr(item_row, "item_name", None) or ""
	)
	if item_code and not item_name:
		item_name = _cstr(frappe.db.get_value("Item", item_code, "item_name") or "")
	quality = _cstr(
		_pick_value(roll_payload, ["quality"]) or getattr(item_row, "quality", None) or ""
	)
	color = _cstr(_pick_value(roll_payload, ["color"]) or getattr(item_row, "color", None) or "")
	gsm = cint(_pick_value(roll_payload, ["gsm"]) or getattr(item_row, "gsm", None) or 0)
	if item_code and (not quality or not color or gsm <= 0):
		specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
		if not quality:
			quality = _cstr(specs.get("quality") or "")
		if not color:
			color = _cstr(specs.get("color") or "")
		if gsm <= 0:
			gsm = cint(specs.get("gsm") or 0)
	no_shafts = cint(
		_pick_value(roll_payload, ["no_of_shafts", "custom_no_of_shaft"])
		or getattr(item_row, "no_of_shafts", None)
		or getattr(item_row, "custom_no_of_shaft", None)
		or 0
	)
	logical = {
		"job_id": _cstr(
			_pick_value(roll_payload, ["job_id", "job"])
			or getattr(item_row, "job_id", None)
			or getattr(item_row, "job", None)
			or ""
		),
		"item_code": item_code,
		"item_name": item_name,
		"quality": quality,
		"color": color,
		"gsm": gsm,
		"width_inch": flt(
			_pick_value(roll_payload, ["width_inch", "width"])
			or getattr(item_row, "width_inch", None)
			or 0
		),
		"meter_per_roll": flt(
			_pick_value(roll_payload, ["meter_per_roll", "meter_roll", "produced_length_mtrs"])
			or getattr(item_row, "meter_per_roll", None)
			or getattr(item_row, "produced_length_mtrs", None)
			or 0
		),
		"no_of_shafts": no_shafts,
		"wastage": flt(
			_pick_value(roll_payload, ["wastage", "net_weight"])
			or getattr(item_row, "net_weight", None)
			or getattr(item_row, "gross_weight", None)
			or 0
		),
		"batch_no": _cstr(
			_pick_value(roll_payload, ["batch_no"]) or getattr(item_row, "batch_no", None) or ""
		),
		"roll_number": _gsm_roll_number_from_batch(
			_cstr(_pick_value(roll_payload, ["batch_no"]) or getattr(item_row, "batch_no", None) or "")
		),
		"spr_item_name": _cstr(getattr(item_row, "name", "") or ""),
		"source_roll": _cstr(
			_pick_value(roll_payload, ["batch_no", "source_roll"])
			or getattr(item_row, "batch_no", None)
			or ""
		),
	}
	return _gsm_write_child_row("Roll Waste Row", logical)


def _gsm_patty_rows_have_saved_wastage(rows) -> bool:
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_patty_row_wastage_kg,
	)

	for row in rows or []:
		row_dict = row if isinstance(row, dict) else (row.as_dict() if hasattr(row, "as_dict") else {})
		if _spr_patty_row_wastage_kg(row_dict) > 0:
			return True
	return False


def _gsm_patty_preview_payload(spr, base_payload: dict | None = None) -> dict | None:
	"""Read-only patty preview using the desk SPR formula when child rows are empty."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_compute_patty_wastage_by_job,
		_spr_patty_wastage_fieldname,
	)

	field = _spr_patty_wastage_fieldname()
	if not field:
		return None

	try:
		spr.calculate_produced_gsm(missing_only=True)
	except Exception:
		pass

	computed = _spr_compute_patty_wastage_by_job(spr)
	if not computed:
		return None

	saved_flags = {}
	for row in spr.get(field) or []:
		jid = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
		if not jid:
			continue
		saved_flags[jid] = cint(
			getattr(row, "recycle_to_next", None) or getattr(row, "custom_recycle_to_next", None) or 0
		)

	base = base_payload or {}
	columns = base.get("columns") or _gsm_child_table_columns("Running Patty Wastage Row")
	preview_rows = []
	from_jobs_only = False
	for logical in computed.values():
		if flt(logical.get("wastage") or 0) <= 0:
			continue
		if cint(logical.get("_from_jobs_only") or 0):
			from_jobs_only = True
		row_dict = dict(logical)
		row_dict.pop("_from_jobs_only", None)
		jid = _cstr(row_dict.get("job_id") or "")
		flag = saved_flags.get(jid, cint(logical.get("recycle_to_next") or 0))
		row_dict["recycle_to_next"] = flag
		row_dict["parentfield"] = field
		row_dict["name"] = f"preview::{jid}" if jid else ""
		try:
			row_dict = _gsm_write_child_row("Running Patty Wastage Row", row_dict)
			row_dict["parentfield"] = field
			row_dict["name"] = f"preview::{jid}" if jid else ""
			row_dict["recycle_to_next"] = flag
		except Exception:
			pass
		preview_rows.append(_gsm_enrich_child_row_from_spr(spr, row_dict, "Running Patty Wastage Row"))

	if not preview_rows:
		return None

	return {
		"fieldname": field,
		"resolved_fieldname": base.get("resolved_fieldname") or field,
		"child_doctype": "Running Patty Wastage Row",
		"columns": columns,
		"rows": preview_rows,
		"configured": True,
		"source": "gsm_preview_from_spr",
		"from_jobs_only": from_jobs_only,
		"read_only": True,
	}


@frappe.whitelist(methods=["GET", "POST"])
def set_gsm_patty_recycle_to_next(spr_name, row_name=None, job_id=None, recycle_to_next=0):
	"""Toggle recycle_to_next on a *saved* Running Patty row only — no separate wastage calculate.

	Uses existing row one_shaft_gross / wastage_qty_kgs for net_wastage. SPR Client Scripts
	own full Running Patty / Recycle table fills.
	"""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_gsm_publish_session_update,
		_spr_operation_lock,
		_spr_patty_child_doctype,
		_spr_patty_live_field_map,
		_spr_patty_wastage_fieldname,
	)

	spr_name = _cstr(spr_name).strip()
	row_name = _cstr(row_name).strip()
	job_id = _cstr(job_id).strip()
	flag = 1 if cint(recycle_to_next) else 0

	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))

	if row_name.startswith("preview::"):
		frappe.throw(
			_("Cannot toggle Recycle on a preview row. Open Shaft Production Run so wastage scripts fill the table first.")
		)

	child_dt = _spr_patty_child_doctype()
	child_meta = frappe.get_meta(child_dt) if frappe.db.exists("DocType", child_dt) else None
	flag_field = None
	if child_meta:
		for cand in ("recycle_to_next", "custom_recycle_to_next"):
			if child_meta.has_field(cand):
				flag_field = cand
				break
	if not flag_field:
		frappe.throw(_("Recycle to Next is not on Running Patty Wastage. Please migrate."))

	with _spr_operation_lock(spr_name, "write", ttl_sec=60):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot update a submitted Shaft Production Run"))

		field = _spr_patty_wastage_fieldname()
		if not field:
			frappe.throw(_("Running Patty Wastage is not configured on Shaft Production Run"))

		target = None
		for row in spr.get(field) or []:
			if row_name and _cstr(row.name) == row_name:
				target = row
				break
			row_job = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
			if job_id and row_job == job_id:
				target = row
				break

		if target is None:
			frappe.throw(
				_("No saved Running Patty row found. Let Shaft Production Run wastage scripts fill the table first.")
			)

		live = _spr_patty_live_field_map()
		net_fn = live.get("net_wastage") or ("net_wastage" if child_meta and child_meta.has_field("net_wastage") else None)
		one_fn = live.get("one_shaft") or "one_shaft_gross"
		tail = flt(getattr(target, one_fn, None) or getattr(target, "one_shaft_gross", None) or 0)
		if tail <= 0:
			w_fn = live.get("wastage_qty") or "wastage_qty_kgs"
			shafts = max(cint(getattr(target, "no_of_shafts", None) or 1), 1)
			qty = flt(getattr(target, w_fn, None) or getattr(target, "wastage_qty_kgs", None) or 0)
			tail = flt(qty / shafts, 3) if qty > 0 else 0.0

		try:
			target.set(flag_field, flag)
		except Exception:
			setattr(target, flag_field, flag)
		if net_fn:
			try:
				target.set(net_fn, 0.0 if flag else flt(tail or 0))
			except Exception:
				setattr(target, net_fn, 0.0 if flag else flt(tail or 0))

		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		_gsm_publish_session_update(spr)
		return {
			"status": "ok",
			"spr_name": spr_name,
			"row_name": _cstr(getattr(target, "name", "") or ""),
			"job_id": _cstr(getattr(target, "job_id", None) or job_id),
			"recycle_to_next": flag,
		}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_spr_wastage_context(spr_name):
	"""Fetch-only: return saved Running Patty / Recycle / Roll Waste tables from SPR."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))

	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if cint(spr.docstatus) == 0:
		waste_keys = [
			_cstr(getattr(r, "batch_no", None) or getattr(r, "source_roll", None) or "").strip()
			for r in spr.get("custom_roll_waste") or []
		]
		waste_keys = [k for k in waste_keys if k]
		if len(waste_keys) != len(set(waste_keys)):
			from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
				_spr_operation_lock,
			)

			with _spr_operation_lock(spr_name, "write", ttl_sec=60):
				spr = frappe.get_doc("Shaft Production Run", spr_name)
				if _gsm_dedupe_spr_roll_waste(spr):
					spr.flags._spr_incremental_roll_save = True
					spr.save(ignore_permissions=True)

	tables = {}
	seen_fields: set[str] = set()
	for fieldname, child_doctype in _GSM_WASTAGE_CHILD_SPECS:
		if fieldname in seen_fields:
			continue
		resolved_field, rows = _gsm_load_spr_child_rows(spr, fieldname, child_doctype)
		if not resolved_field and not rows:
			continue
		seen_fields.add(fieldname)
		live_child = child_doctype
		try:
			df = frappe.get_meta("Shaft Production Run").get_field(resolved_field or fieldname)
			if df and _cstr(df.options):
				live_child = _cstr(df.options)
		except Exception:
			pass
		payload = _gsm_spr_child_table_payload(spr, fieldname, live_child)
		payload["fieldname"] = fieldname
		payload["resolved_fieldname"] = resolved_field
		payload["configured"] = True
		payload["source"] = "spr_saved_table"
		tables[fieldname] = payload

	for fieldname, child_doctype in _GSM_WASTAGE_CHILD_SPECS:
		if fieldname in tables:
			continue
		tables[fieldname] = {
			"fieldname": fieldname,
			"child_doctype": child_doctype,
			"columns": _gsm_child_table_columns(child_doctype),
			"rows": [],
			"configured": False,
			"source": "spr_saved_table",
		}

	# FETCH ONLY — never substitute a server-side wastage preview.

	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_is_real_roll_item_row,
		_spr_patty_row_wastage_kg,
	)

	real_roll_count = sum(1 for it in (spr.items or []) if _spr_is_real_roll_item_row(it))
	patty_rows_now = (tables.get("custom_running_patty_wastage") or {}).get("rows") or []
	patty_kg = 0.0
	for r in patty_rows_now:
		row_dict = r if isinstance(r, dict) else (r.as_dict() if hasattr(r, "as_dict") else {})
		patty_kg += _spr_patty_row_wastage_kg(row_dict or {})
	recycle_rows = (tables.get("custom_recycled_wastage_details") or {}).get("rows") or []
	warnings = []
	if real_roll_count <= 0 and (patty_kg > 0 or recycle_rows):
		warnings.append(
			_(
				"This SPR has running patty / recycle data but no produced rolls yet. "
				"Save Row on GSM for each roll, then Refresh — otherwise submit will miss rolls."
			)
		)
	elif real_roll_count <= 0 and not patty_rows_now:
		warnings.append(
			_(
				"No produced rolls on this SPR yet. Enter rolls on GSM and click Save Row before relying on wastage."
			)
		)
	elif patty_rows_now and patty_kg <= 0:
		warnings.append(
			_(
				"Running Patty rows are saved but wastage qty is 0. "
				"Open the Shaft Production Run so existing wastage Client Scripts can fill "
				"width_inches / meter_roll_mtrs / wastage_qty_kgs, then Refresh here."
			)
		)

	order_code = _cstr(
		getattr(spr, "custom_order_code", None)
		or getattr(spr, "order_code", None)
		or ""
	)
	if not order_code and spr.get("production_plan"):
		order_code = _gsm_order_code_for_pp(_cstr(spr.get("production_plan")))

	return {
		"spr_name": spr_name,
		"order_code": order_code,
		"real_roll_count": real_roll_count,
		"warnings": warnings,
		"tables": tables,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_available_patty_stock(spr_name):
	"""Patty stock for GSM recycle — Patty Stock DocType first, then SPR wastage preview."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name:
		frappe.throw(_("SPR is required"))

	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		get_available_patty_stock,
	)

	try:
		doctype_stock = get_available_patty_stock(spr_name) or []
	except Exception:
		doctype_stock = []
	if doctype_stock:
		return {
			"stock": doctype_stock,
			"source": "Patty Stock",
			"spr_name": spr_name,
		}

	fallback_stock = _gsm_fallback_patty_stock_from_spr(spr_name)
	if fallback_stock:
		return {
			"stock": fallback_stock,
			"source": "spr_wastage_or_roll_preview",
			"spr_name": spr_name,
		}

	for path in _PATTY_STOCK_METHOD_CANDIDATES:
		try:
			fn = frappe.get_attr(path)
		except Exception:
			continue
		if not callable(fn):
			continue
		try:
			result = fn(spr_name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"GSM patty stock: {path}")
			continue
		if isinstance(result, list):
			return {"stock": result, "source": path, "spr_name": spr_name}
		if isinstance(result, dict):
			if "stock" in result or "message" in result:
				return result
			return {"stock": result.get("rows") or result.get("data") or [], "source": path, **result}

	return {
		"stock": [],
		"source": "fallback-none",
		"message": _("No patty stock available for this SPR."),
	}


def _gsm_fallback_patty_stock_from_spr(spr_name: str) -> list[dict]:
	"""When site RPC is unavailable, derive patty-stock from saved wastage rows or GSM preview."""
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	out: list[dict] = []

	for fieldname in ("custom_running_patty_wastage", "custom_roll_waste"):
		rows = getattr(spr, fieldname, None) or []
		for r in rows:
			d = r.as_dict() if hasattr(r, "as_dict") else {}
			waste_qty = flt(
				_pick_value(
					d,
					["wastage", "wastage_qty", "wastage_qt", "net_wastage", "net_wastage_kg", "net_weight", "available_kg", "available"],
					0,
				)
			)
			recycled_qty = flt(_pick_value(d, ["recycled", "recycled_qty", "recycled_kg"], 0))
			available = max(0.0, waste_qty - recycled_qty) if waste_qty > 0 else waste_qty
			if available <= 0 and waste_qty <= 0:
				continue

			out.append(
				{
					"name": r.name,
					"batch_no": _cstr(_pick_value(d, ["batch_no", "source_roll"], "")),
					"quality": _cstr(_pick_value(d, ["quality"], "")),
					"color": _cstr(_pick_value(d, ["color"], "")),
					"gsm": cint(_pick_value(d, ["gsm"], 0)),
					"width_inch": flt(_pick_value(d, ["width_inch", "width"], 0)),
					"meter_per_roll": flt(_pick_value(d, ["meter_per_roll", "meter_roll", "meter", "produced_length_mtrs", "produced_length_mtr"], 0)),
					"no_of_shafts": cint(_pick_value(d, ["no_of_shafts", "shafts"], 0)),
					"available_kg": available,
				}
			)

	if out:
		return out

	# No preview fallback — only saved SPR Running Patty / Recycle tables.
	return out


def _gsm_roll_waste_row_key(row) -> str:
	if isinstance(row, dict):
		return _cstr(
			row.get("batch_no") or row.get("source_roll") or row.get("name") or ""
		).strip()
	return _cstr(
		getattr(row, "batch_no", None)
		or getattr(row, "source_roll", None)
		or getattr(row, "name", None)
		or ""
	).strip()


def _gsm_unique_roll_waste_row_dicts(rows) -> list:
	seen = set()
	out = []
	for row in rows or []:
		key = _gsm_roll_waste_row_key(row)
		if key and key in seen:
			continue
		if key:
			seen.add(key)
		out.append(row)
	return out


def _gsm_find_existing_roll_waste(spr, batch_no: str = "", row_name: str = ""):
	batch_no = _cstr(batch_no).strip()
	row_name = _cstr(row_name).strip()
	for row in spr.get("custom_roll_waste") or []:
		rb = _cstr(getattr(row, "batch_no", None) or getattr(row, "source_roll", None) or "").strip()
		if batch_no and rb == batch_no:
			return row
		if row_name and _cstr(getattr(row, "spr_item_name", None) or "").strip() == row_name:
			return row
	return None


def _gsm_dedupe_spr_roll_waste(spr) -> int:
	"""Drop extra custom_roll_waste rows that share the same batch / source roll."""
	seen = set()
	extras = []
	for row in list(spr.get("custom_roll_waste") or []):
		key = _cstr(getattr(row, "batch_no", None) or getattr(row, "source_roll", None) or "").strip()
		if not key:
			key = _cstr(getattr(row, "name", None) or "")
		if key and key in seen:
			extras.append(row)
			continue
		if key:
			seen.add(key)
	for row in extras:
		spr.remove(row)
	return len(extras)


def _gsm_roll_waste_response(spr, waste_child, waste_row: dict, removed_names: list, already_marked: bool = False):
	return {
		"status": "ok",
		"already_marked": 1 if already_marked else 0,
		"spr_name": spr.name,
		"batch_no": waste_row.get("batch_no")
		or _cstr(getattr(waste_child, "batch_no", None) or ""),
		"removed_row_name": removed_names[0] if removed_names else "",
		"removed_row_names": removed_names,
		"roll_waste_row_name": _cstr(getattr(waste_child, "name", "")),
		"roll_waste": _gsm_child_row_dict(waste_child, _gsm_child_table_columns("Roll Waste Row")),
		"total_items": len(spr.items or []),
	}


@frappe.whitelist(methods=["GET", "POST"])
def mark_gsm_roll_waste(spr_name, roll_payload=None, batch_no=None, row_name=None):
	"""Mark a production roll as waste — append Roll Waste row and remove SPR items row."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_operation_lock,
		_gsm_publish_session_update,
	)

	spr_name = _cstr(spr_name).strip()
	roll_payload = _parse_json_arg(roll_payload, {})
	batch_no = _cstr(batch_no or roll_payload.get("batch_no") or "").strip()
	row_name = _cstr(
		row_name or roll_payload.get("spr_item_name") or roll_payload.get("row_name") or ""
	).strip()

	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not batch_no and not row_name:
		frappe.throw(_("Batch number or SPR item row name is required"))

	spr_meta = frappe.get_meta("Shaft Production Run")
	if not spr_meta.has_field("custom_roll_waste"):
		frappe.throw(_("Roll Waste table is not configured on Shaft Production Run"))

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot mark roll waste on a submitted Shaft Production Run"))

		matching_items = []
		for row in list(spr.items or []):
			match_batch = batch_no and _cstr(getattr(row, "batch_no", "")).strip() == batch_no
			match_name = row_name and _cstr(getattr(row, "name", "")).strip() == row_name
			if match_batch or match_name:
				matching_items.append(row)

		removed_dupes = _gsm_dedupe_spr_roll_waste(spr)
		existing_waste = _gsm_find_existing_roll_waste(spr, batch_no, row_name)

		removed_names = [_cstr(getattr(it, "name", "")) for it in matching_items]
		for item in matching_items:
			spr.remove(item)

		if existing_waste:
			if matching_items or removed_dupes:
				spr.flags._spr_incremental_roll_save = True
				spr.save(ignore_permissions=True)
				_gsm_publish_session_update(spr)
			waste_dict = _gsm_child_row_dict(existing_waste, _gsm_child_table_columns("Roll Waste Row"))
			return _gsm_roll_waste_response(
				spr, existing_waste, waste_dict, removed_names, already_marked=True
			)

		if not matching_items:
			frappe.throw(_("Roll line not found on SPR"))

		waste_row = _gsm_build_roll_waste_row_from_item(matching_items[0], roll_payload)
		spr.append("custom_roll_waste", waste_row)
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		_gsm_publish_session_update(spr)

		roll_waste_child = (spr.custom_roll_waste or [])[-1]
		return _gsm_roll_waste_response(spr, roll_waste_child, waste_row, removed_names)


@frappe.whitelist(methods=["GET", "POST"])
def consume_gsm_recycled_wastage(spr_name, patty_selections=None, roll_waste_row_names=None):
	"""Append GSM manual recycle rows from View Patty Stock and/or roll waste selections.

	Automated Recycled Wastage Details (custom_recycled_wastage_details) are left unchanged.
	"""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_spr_operation_lock,
	)

	spr_name = _cstr(spr_name).strip()
	patty_selections = _gsm_as_selection_list(patty_selections)
	roll_waste_row_names = _gsm_as_selection_list(roll_waste_row_names)

	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not patty_selections and not roll_waste_row_names:
		frappe.throw(_("Select patty stock and/or roll waste rows to recycle"))

	manual_field, manual_doctype = _gsm_manual_recycle_field_and_doctype()
	spr_meta = frappe.get_meta("Shaft Production Run")
	if not spr_meta.has_field(manual_field):
		frappe.throw(
			_(
				"GSM Manual Recycle Details is not configured on Shaft Production Run. "
				"Run migrate / ensure_spr_gsm_manual_recycle_table."
			)
		)
	if not frappe.db.exists("DocType", manual_doctype):
		frappe.throw(_("DocType {0} is missing").format(manual_doctype))

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot add recycled rows to a submitted Shaft Production Run"))

		added = 0
		for sel in patty_selections:
			row = _gsm_hydrate_patty_selection(sel)
			if not row:
				continue
			values = _gsm_map_to_recycled_row(row, from_roll_waste=False, child_doctype=manual_doctype)
			# Mark as manual patty consume so desk automation never overwrites this table.
			if not _cstr(values.get("job_id") or "").strip():
				values["job_id"] = "Patty"
			child = spr.append(manual_field, values)
			_gsm_stamp_child_values(child, row)
			_gsm_stamp_child_values(child, values)
			_gsm_stamp_width_on_child(
				child,
				flt(
					row.get("width_inch")
					or row.get("width")
					or values.get("width_inch")
					or values.get("width")
					or 0
				),
			)
			_gsm_stamp_qty_on_child(
				child,
				_gsm_pick_positive_qty(
					row,
					("available_kg", "available", "wastage", "wastage_qty", "recycled", "recycled_qty"),
				)
				or _gsm_pick_positive_qty(
					values,
					("wastage", "wastage_qty", "recycled", "recycled_qty", "available_kg"),
				),
			)
			real_bn = _cstr(row.get("batch_no") or values.get("batch_no") or "")
			if _gsm_is_real_batch_no(real_bn):
				try:
					child.set("batch_no", real_bn)
				except Exception:
					child.batch_no = real_bn
				try:
					child.set("source_roll", real_bn)
				except Exception:
					child.source_roll = real_bn
			added += 1

		roll_waste_by_name = {r.name: r for r in (spr.custom_roll_waste or [])}
		roll_waste_to_remove = []
		for rn in roll_waste_row_names:
			rn = _cstr(rn).strip()
			if not rn:
				continue
			rw = roll_waste_by_name.get(rn)
			if not rw:
				continue
			values = _gsm_map_to_recycled_row(
				rw, from_roll_waste=True, spr_doc=spr, child_doctype=manual_doctype
			)
			child = spr.append(manual_field, values)
			_gsm_stamp_child_values(child, values)
			waste_kg = _gsm_pick_positive_qty(
				values,
				("wastage", "wastage_qty", "recycled", "recycled_qty", "available_kg", "net_weight"),
			)
			if waste_kg <= 0:
				waste_kg = _gsm_qty_from_spr_item(
					spr,
					job_id=_cstr(getattr(rw, "job_id", None) or getattr(rw, "job", None) or ""),
					batch_no=_cstr(getattr(rw, "batch_no", None) or getattr(rw, "source_roll", None) or ""),
					spr_item_name=_cstr(getattr(rw, "spr_item_name", None) or ""),
				)
			_gsm_stamp_qty_on_child(child, waste_kg)
			roll_waste_to_remove.append(rw)
			added += 1

		for rw in roll_waste_to_remove:
			spr.remove(rw)

		if not added:
			frappe.throw(_("No recycled rows were added"))

		spr.flags.gsm_recycle_consume = True
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		return {
			"status": "ok",
			"spr_name": spr_name,
			"added": added,
			"manual_field": manual_field,
			"recycled": _gsm_spr_child_table_payload(spr, manual_field, manual_doctype),
		}


def _mix_roll_store_table_exists() -> bool:
	try:
		frappe.db.sql("SELECT 1 FROM `mix_roll_store_data` LIMIT 1")
		return True
	except Exception:
		return False


def _mix_roll_row_key(m: dict) -> str:
	return f"{_cstr(m.get('unit')).strip()}|{_cstr(m.get('color1')).strip().upper()}|{_cstr(m.get('color2')).strip().upper()}"


def _normalize_mix_unit(unit: str) -> str:
	u = _cstr(unit).strip()
	if not u:
		return ""
	try:
		return normalize_planning_unit_for_select(u)
	except Exception:
		return u


def _iter_mix_roll_store_rows():
	if not _mix_roll_store_table_exists():
		return
	for store in frappe.db.sql(
		"SELECT date_key, data, modified FROM `mix_roll_store_data` ORDER BY modified DESC",
		as_dict=True,
	):
		try:
			entries = json.loads(store.data or "[]")
		except Exception:
			entries = []
		if not isinstance(entries, list):
			continue
		for entry in entries:
			if isinstance(entry, dict):
				yield store.date_key, entry


def _find_mix_roll_store_row(date_key: str, mix_id: str | None = None, mix_row_key: str | None = None):
	date_key = _cstr(date_key).strip()
	mix_id = _cstr(mix_id).strip()
	mix_row_key = _cstr(mix_row_key).strip()
	if not date_key:
		return None, None
	rows = frappe.db.sql(
		"SELECT data FROM `mix_roll_store_data` WHERE date_key = %s", date_key
	)
	if not rows or not rows[0][0]:
		return None, None
	try:
		entries = json.loads(rows[0][0])
	except Exception:
		return None, None
	for entry in entries or []:
		if not isinstance(entry, dict):
			continue
		if mix_id and _cstr(entry.get("mix_id")) == mix_id:
			return entry, entries
		if mix_row_key and _mix_roll_row_key(entry) == mix_row_key:
			return entry, entries
	return None, None


def _sync_mix_roll_spr_name_in_store(date_key: str, mix_entry: dict, spr_name: str):
	"""Write spr_name back to mix_roll_store_data for one row."""
	date_key = _cstr(date_key).strip()
	spr_name = _cstr(spr_name).strip()
	if not date_key or not spr_name:
		return
	rows = frappe.db.sql("SELECT data FROM `mix_roll_store_data` WHERE date_key = %s", date_key)
	if not rows or not rows[0][0]:
		return
	try:
		entries = json.loads(rows[0][0])
	except Exception:
		return
	updated = False
	target_id = _cstr(mix_entry.get("mix_id")).strip()
	target_key = _mix_roll_row_key(mix_entry)
	for entry in entries or []:
		if not isinstance(entry, dict):
			continue
		match = False
		if target_id and _cstr(entry.get("mix_id")) == target_id:
			match = True
		elif not target_id and _mix_roll_row_key(entry) == target_key:
			match = True
		if match:
			entry["spr_name"] = spr_name
			updated = True
			break
	if updated:
		frappe.db.sql(
			"UPDATE `mix_roll_store_data` SET data = %s, modified = NOW() WHERE date_key = %s",
			(json.dumps(entries, ensure_ascii=False), date_key),
		)
		frappe.db.commit()


def _serialize_mix_roll_candidate(date_key: str, entry: dict) -> dict:
	spr_name = _cstr(entry.get("spr_name")).strip()
	spr_status = None
	spr_run_date = ""
	spr_shift = ""
	spr_docstatus = None
	if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
		res = frappe.db.get_value(
			"Shaft Production Run",
			spr_name,
			["docstatus", "run_date", "shift", "custom_unit"],
			as_dict=True,
		)
		if res:
			spr_docstatus = cint(res.docstatus)
			spr_run_date = _cstr(res.run_date)
			spr_shift = _cstr(res.shift)
			spr_status = "Submitted" if spr_docstatus == 1 else "Draft"
	spr_roll_count = 0
	if spr_name and frappe.db.exists("Shaft Production Run Item"):
		try:
			spr_roll_count = cint(
				frappe.db.count("Shaft Production Run Item", {"parent": spr_name})
			)
		except Exception:
			spr_roll_count = 0
	return {
		**entry,
		"date_key": date_key,
		"planning_date_key": date_key,
		"mix_row_key": _mix_roll_row_key(entry),
		"spr_name": spr_name or "",
		"spr_status": spr_status,
		"spr_docstatus": spr_docstatus,
		"spr_run_date": spr_run_date,
		"spr_shift": spr_shift,
		"spr_roll_count": spr_roll_count,
		"label": _cstr(entry.get("mixName") or entry.get("mix_name") or "Mix Roll"),
		"color_transition": f"{_cstr(entry.get('color1'))} → {_cstr(entry.get('color2'))}",
	}


def _mix_date_key_months(date_key: str) -> set:
	"""Return the set of 'YYYY-MM' month labels a Color Chart mix date_key covers.

	Keys look like `day-YYYY-MM-DD`, `week-YYYY-Www` or `month-YYYY-MM` (optionally
	suffixed with `-<PLAN>`). Legacy keys `YYYY-Www` and `YYYY-MM-DD` are also
	supported. Weeks may straddle two calendar months, so all months the week
	touches are returned. Unparseable keys return an empty set.
	"""
	import re as _re
	from datetime import date as _date, timedelta as _timedelta

	key = _cstr(date_key).strip()
	months: set = set()
	if not key:
		return months

	def _add_week_months(year: int, week: int) -> None:
		try:
			monday = _date.fromisocalendar(year, week, 1)
			for i in range(7):
				d = monday + _timedelta(days=i)
				months.add(f"{d.year:04d}-{d.month:02d}")
		except Exception:
			pass

	m = _re.match(r"^month-(\d{4})-(\d{1,2})", key)
	if m:
		months.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}")
		return months
	m = _re.match(r"^day-(\d{4})-(\d{1,2})-(\d{1,2})", key)
	if m:
		months.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}")
		return months
	m = _re.match(r"^week-(\d{4})-W(\d{1,2})", key, _re.IGNORECASE)
	if m:
		_add_week_months(int(m.group(1)), int(m.group(2)))
		return months
	m = _re.match(r"^(\d{4})-W(\d{1,2})", key, _re.IGNORECASE)
	if m:
		_add_week_months(int(m.group(1)), int(m.group(2)))
		return months
	m = _re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", key)
	if m:
		months.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}")
	return months


def _list_mix_store_date_keys() -> list:
	if not _mix_roll_store_table_exists():
		return []
	rows = frappe.db.sql("SELECT date_key FROM `mix_roll_store_data`")
	return [_cstr(r[0]).strip() for r in rows if r and _cstr(r[0]).strip()]


def _mix_date_key_iso_weeks(date_key: str) -> set:
	"""ISO (year, week) tuples covered by a Color Chart mix date_key."""
	import calendar
	import re as _re
	from datetime import date as _date, timedelta as _timedelta

	key = _cstr(date_key).strip()
	weeks: set = set()
	if not key:
		return weeks
	m = _re.match(r"^week-(\d{4})-W(\d{1,2})", key, _re.IGNORECASE)
	if m:
		weeks.add((int(m.group(1)), int(m.group(2))))
		return weeks
	m = _re.match(r"^day-(\d{4})-(\d{1,2})-(\d{1,2})", key)
	if m:
		try:
			d = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
			iso = d.isocalendar()
			weeks.add((iso[0], iso[1]))
		except Exception:
			pass
		return weeks
	m = _re.match(r"^month-(\d{4})-(\d{1,2})", key)
	if m:
		year, month = int(m.group(1)), int(m.group(2))
		try:
			start = _date(year, month, 1)
			end = _date(year, month, calendar.monthrange(year, month)[1])
		except Exception:
			return weeks
		d = start
		while d <= end:
			iso = d.isocalendar()
			weeks.add((iso[0], iso[1]))
			d += _timedelta(days=1)
		return weeks
	m = _re.match(r"^(\d{4})-W(\d{1,2})", key, _re.IGNORECASE)
	if m:
		weeks.add((int(m.group(1)), int(m.group(2))))
	return weeks


def _mix_date_key_base_and_plan(date_key: str) -> tuple:
	import re as _re

	key = _cstr(date_key).strip()
	for pat in (
		r"^(day-\d{4}-\d{1,2}-\d{1,2})(?:-(.+))?$",
		r"^(week-\d{4}-W\d{1,2})(?:-(.+))?$",
		r"^(month-\d{4}-\d{1,2})(?:-(.+))?$",
	):
		m = _re.match(pat, key, _re.IGNORECASE)
		if m:
			return m.group(1), _cstr(m.group(2)).strip()
	return key, ""


def _mix_date_key_kind(date_key: str) -> str:
	base, _plan = _mix_date_key_base_and_plan(date_key)
	if base.lower().startswith("month-"):
		return "month"
	if base.lower().startswith("week-"):
		return "week"
	return "day"


def _mix_date_key_day_stamp(date_key: str) -> str:
	"""YYYY-MM-DD for a day-* key, else empty."""
	import re as _re

	m = _re.match(r"^day-(\d{4})-(\d{1,2})-(\d{1,2})", _cstr(date_key).strip())
	if not m:
		return ""
	return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _related_mix_store_keys(date_key: str) -> list:
	"""Existing mix stores Color Chart should show together with date_key.

	GSM Daily saves `day-YYYY-MM-DD`. Color Chart weekly PLAN 2 loads
	`week-YYYY-Www-PLAN 2`. Unsuffixed GSM day rows are included; other plans are not.
	"""
	date_key = _cstr(date_key).strip()
	weeks = _mix_date_key_iso_weeks(date_key)
	kind = _mix_date_key_kind(date_key)
	day_stamp = _mix_date_key_day_stamp(date_key)
	_view_base, view_plan = _mix_date_key_base_and_plan(date_key)
	out = []
	seen = set()
	if date_key:
		out.append(date_key)
		seen.add(date_key)
	if not weeks:
		return out
	for k in _list_mix_store_date_keys():
		if k in seen:
			continue
		kw = _mix_date_key_iso_weeks(k)
		if not (kw & weeks):
			continue
		kkind = _mix_date_key_kind(k)
		_k_base, k_plan = _mix_date_key_base_and_plan(k)
		if kind in ("day", "week") and kkind == "month":
			continue
		if kind == "day" and kkind == "day" and _mix_date_key_day_stamp(k) != day_stamp:
			continue
		if k_plan and view_plan and k_plan != view_plan:
			continue
		if k_plan and not view_plan:
			continue
		out.append(k)
		seen.add(k)
	return out


def _copy_mix_row_to_related_stores(entry: dict, primary_date_key: str, planned_date=None):
	"""Append a GSM-created mix onto same-plan week/month stores only (never other plans)."""
	if not isinstance(entry, dict):
		return
	mix_id = _cstr(entry.get("mix_id")).strip()
	if not mix_id:
		return
	anchor = _cstr(primary_date_key).strip()
	if planned_date:
		try:
			d = getdate(planned_date)
			_base, plan = _mix_date_key_base_and_plan(anchor)
			day_key = f"day-{d.year:04d}-{d.month:02d}-{d.day:02d}"
			anchor = f"{day_key}-{plan}" if plan else day_key
		except Exception:
			pass
	weeks = _mix_date_key_iso_weeks(anchor)
	day_stamp = _mix_date_key_day_stamp(anchor)
	_primary_base, primary_plan = _mix_date_key_base_and_plan(primary_date_key)
	existing = set(_list_mix_store_date_keys())
	for date_key in existing:
		if date_key == primary_date_key:
			continue
		if not (_mix_date_key_iso_weeks(date_key) & weeks):
			continue
		kind = _mix_date_key_kind(date_key)
		if kind == "day" and _mix_date_key_day_stamp(date_key) != day_stamp:
			continue
		_k_base, k_plan = _mix_date_key_base_and_plan(date_key)
		if (k_plan or "") != (primary_plan or ""):
			continue
		entries = _load_mix_store_entries(date_key)
		if any(_cstr(e.get("mix_id")).strip() == mix_id for e in entries if isinstance(e, dict)):
			continue
		entries.append(dict(entry))
		save_mix_roll_data(date_key, entries)


def _purge_mix_id_from_other_plan_stores(mix_id: str, keep_date_key: str):
	"""Remove a mix_id from stores belonging to other Color Chart plans."""
	mix_id = _cstr(mix_id).strip()
	keep_date_key = _cstr(keep_date_key).strip()
	if not mix_id or not keep_date_key:
		return
	_keep_base, keep_plan = _mix_date_key_base_and_plan(keep_date_key)
	for date_key in _list_mix_store_date_keys():
		if date_key == keep_date_key:
			continue
		_k_base, k_plan = _mix_date_key_base_and_plan(date_key)
		if (k_plan or "") == (keep_plan or ""):
			continue
		entries = _load_mix_store_entries(date_key)
		next_entries = [
			e
			for e in entries
			if not (isinstance(e, dict) and _cstr(e.get("mix_id")).strip() == mix_id)
		]
		if len(next_entries) != len(entries):
			save_mix_roll_data(date_key, next_entries)


def _locked_color_chart_plan_names() -> list:
	from production_entry.production_planning.scheduler_api import get_persisted_plans

	plans = get_persisted_plans("color_chart") or []
	out = []
	for p in plans:
		if not isinstance(p, dict):
			continue
		name = _cstr(p.get("name")).strip() or "Default"
		if cint(p.get("locked")):
			out.append(name)
	return out


def _plan_unit_colours_near_date(plan_name: str, unit: str, ref_date) -> set:
	"""Distinct colours on Planning sheets for plan + unit around ref_date."""
	plan_name = _cstr(plan_name).strip() or "Default"
	unit = _normalize_mix_unit(unit)
	if not unit or not frappe.db.table_exists("Planning Table"):
		return set()
	try:
		d = getdate(ref_date)
	except Exception:
		d = getdate()
	start = frappe.utils.add_days(d, -14)
	end = frappe.utils.add_days(d, 14)
	has_ps_cpd = frappe.db.has_column("Planning sheet", "custom_planned_date")
	date_expr = (
		"COALESCE(NULLIF(i.planned_date,''), NULLIF(p.custom_planned_date,''), p.ordered_date)"
		if has_ps_cpd and frappe.db.has_column("Planning Table", "planned_date")
		else (
			"COALESCE(p.custom_planned_date, p.ordered_date)"
			if has_ps_cpd
			else "p.ordered_date"
		)
	)
	if plan_name == "Default":
		plan_sql = "(p.custom_plan_name IS NULL OR p.custom_plan_name = '' OR p.custom_plan_name = 'Default')"
		params = (unit, start, end)
	else:
		plan_sql = "p.custom_plan_name = %s"
		params = (unit, plan_name, start, end)
	try:
		rows = frappe.db.sql(
			f"""
			SELECT DISTINCT UPPER(TRIM(i.color))
			FROM `tabPlanning Table` i
			JOIN `tabPlanning sheet` p ON i.parent = p.name
			WHERE i.unit = %s
			  AND {plan_sql}
			  AND i.color IS NOT NULL AND TRIM(i.color) != ''
			  AND {date_expr} BETWEEN %s AND %s
			""",
			params,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "plan_unit_colours_near_date")
		return set()
	return {(_cstr(r[0]).strip().upper()) for r in (rows or []) if r and r[0]}


def _resolve_mix_target_plan(unit, color1, color2, planned_date=None, run_date=None) -> str:
	"""Pick the locked Color Chart plan where from→to colours belong.

	Does not fan out across Default/open plans. Prefers locked plans that already
	hold this transition or have both colours on the unit's color chart.
	"""
	unit_n = _normalize_mix_unit(unit)
	c1 = _cstr(color1).strip().upper()
	c2 = _cstr(color2).strip().upper()
	locked = _locked_color_chart_plan_names()
	ref = planned_date or run_date
	try:
		ref_date = getdate(ref) if ref else getdate()
	except Exception:
		ref_date = getdate()

	# 1) Locked plan store already has this unit + color1→color2
	match_key = _mix_roll_match_key(unit_n, c1, c2)
	for date_key, entry in _iter_mix_roll_store_rows():
		if not isinstance(entry, dict) or entry.get("isRecycle"):
			continue
		if _normalize_mix_unit(entry.get("unit")) != unit_n:
			continue
		if _mix_roll_match_key(entry.get("unit"), entry.get("color1"), entry.get("color2")) != match_key:
			continue
		_base, plan = _mix_date_key_base_and_plan(date_key)
		plan = _cstr(entry.get("_plan_name") or plan).strip() or "Default"
		if locked and plan in locked:
			return plan
		if not locked and plan and plan != "Default":
			return plan

	# 2) Score locked plans by colours present on Planning sheets
	if locked:
		best = ""
		best_score = -1
		for plan in locked:
			colors = _plan_unit_colours_near_date(plan, unit_n, ref_date)
			score = 0
			if c1 in colors and c2 in colors:
				score = 3
			elif c1 in colors or c2 in colors:
				score = 1
			if score > best_score:
				best_score = score
				best = plan
		if best and best_score > 0:
			return best
		# Locked plans exist but colours not found — still pin to first non-Default locked
		for plan in locked:
			if plan != "Default":
				return plan
		return locked[0]

	return "Default"


def _gsm_browse_scope_months(
	planned_date=None,
	view_scope=None,
	filter_week=None,
	filter_month=None,
	run_date=None,
) -> set:
	"""Calendar months covered by GSM browse filters (Planned Date / week / month)."""
	import re as _re

	months: set = set()
	scope = _cstr(view_scope or "daily").strip().lower()
	if scope == "monthly" and filter_month:
		m = _re.match(r"^(\d{4})-(\d{1,2})", _cstr(filter_month).strip())
		if m:
			months.add(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}")
	elif scope == "weekly" and filter_week:
		week_key = _cstr(filter_week).strip()
		if week_key and not week_key.lower().startswith("week-"):
			week_key = f"week-{week_key}"
		months |= _mix_date_key_months(week_key)
	elif planned_date:
		d = getdate(planned_date)
		months.add(f"{d.year:04d}-{d.month:02d}")
	elif run_date:
		d = getdate(run_date)
		months.add(f"{d.year:04d}-{d.month:02d}")
	return months


def _gsm_mix_store_date_key(
	planned_date=None,
	view_scope=None,
	filter_week=None,
	filter_month=None,
	run_date=None,
	plan_name=None,
) -> str:
	"""Color Chart mix_roll_store_data key matching the GSM browse filters (+ optional plan)."""
	import re as _re

	scope = _cstr(view_scope or "daily").strip().lower()
	if scope == "monthly":
		fm = _cstr(filter_month).strip()
		m = _re.match(r"^(\d{4})-(\d{1,2})", fm) if fm else None
		if m:
			base = f"month-{int(m.group(1)):04d}-{int(m.group(2)):02d}"
		else:
			d = getdate(planned_date or run_date) if (planned_date or run_date) else getdate()
			base = f"month-{d.year:04d}-{d.month:02d}"
	elif scope == "weekly":
		fw = _cstr(filter_week).strip()
		if fw:
			if fw.lower().startswith("week-"):
				base = fw
			else:
				base = f"week-{fw}"
		else:
			d = getdate(planned_date or run_date) if (planned_date or run_date) else getdate()
			base = f"day-{d.year:04d}-{d.month:02d}-{d.day:02d}"
	else:
		d = getdate(planned_date or run_date) if (planned_date or run_date) else getdate()
		base = f"day-{d.year:04d}-{d.month:02d}-{d.day:02d}"
	plan = _cstr(plan_name).strip()
	if plan and plan != "Default":
		return f"{base}-{plan}"
	return base


def _load_mix_store_entries(date_key: str) -> list:
	date_key = _cstr(date_key).strip()
	if not date_key or not _mix_roll_store_table_exists():
		return []
	rows = frappe.db.sql("SELECT data FROM `mix_roll_store_data` WHERE date_key = %s", date_key)
	if not rows or not rows[0][0]:
		return []
	try:
		entries = json.loads(rows[0][0])
	except Exception:
		return []
	return entries if isinstance(entries, list) else []


def _mix_roll_match_key(unit, color1, color2) -> str:
	return (
		f"{_normalize_mix_unit(unit)}|"
		f"{_cstr(color1).strip().upper()}|"
		f"{_cstr(color2).strip().upper()}"
	)


def _find_gsm_operator_mix_match(
	target_unit: str, mix_match_key: str, scope_months: set, preferred_plan: str | None = None
):
	"""Locate a Color Chart mix row for this unit + from/to colour in the browse month.

	Returns (date_key, mix_id, action):
	- update: row exists and Color Chart would still show CREATE ITEMS (no item_code)
	- new_on_key: row exists and already has items (UPDATE ITEMS) — caller appends a new row
	- (None, None, None): not on the plan
	"""
	preferred_plan = _cstr(preferred_plan).strip()
	preferred_hit = None
	created_hit = None
	for date_key, entry in _iter_mix_roll_store_rows():
		if not isinstance(entry, dict) or entry.get("isRecycle"):
			continue
		if _normalize_mix_unit(entry.get("unit")) != target_unit:
			continue
		if _mix_roll_match_key(entry.get("unit"), entry.get("color1"), entry.get("color2")) != mix_match_key:
			continue
		key_months = _mix_date_key_months(date_key)
		if not key_months or not (key_months & scope_months):
			continue
		_base, key_plan = _mix_date_key_base_and_plan(date_key)
		entry_plan = _cstr(entry.get("_plan_name") or key_plan).strip() or "Default"
		has_item = bool(_cstr(entry.get("item_code")).strip())
		submitted = bool(entry.get("_submitted"))
		mix_id = _cstr(entry.get("mix_id")).strip()
		hit = (date_key, mix_id, "update" if (not has_item and not submitted) else "new_on_key")
		if preferred_plan and entry_plan == preferred_plan:
			if hit[2] == "update":
				return date_key, mix_id, "update"
			if preferred_hit is None:
				preferred_hit = (date_key, mix_id, "new_on_key")
			continue
		if not has_item and not submitted:
			if preferred_plan:
				# Keep searching for preferred plan; fall back later
				if created_hit is None:
					created_hit = (date_key, mix_id, "update")
			else:
				return date_key, mix_id, "update"
		elif created_hit is None:
			created_hit = (date_key, mix_id, "new_on_key")
	if preferred_hit:
		return preferred_hit
	if created_hit:
		return created_hit
	return None, None, None


def _new_gsm_mix_store_row(payload: dict) -> dict:
	return {
		"unit": payload["unit"],
		"color1": payload["color1"],
		"color2": payload["color2"],
		"mixName": payload["mix_name"],
		"quality": payload["quality"],
		"clType": payload["cl_type"],
		"gsm": payload["gsm"],
		"shaft": payload["shaft"],
		"no_of_shaft": payload["no_of_shaft"],
		"kg": payload["kg"],
		"item_code": "",
		"item_name": "",
		"isRecycle": False,
		"_prevMixName": "",
		"_isManual": True,
		"_fromGsmEntry": True,
		"_plan_name": _cstr(payload.get("plan_name")).strip() or "Default",
		"mix_id": f"mix-{frappe.generate_hash(length=8)}",
	}


def _apply_gsm_mix_fields(entry: dict, payload: dict):
	entry["unit"] = payload["unit"]
	entry["color1"] = payload["color1"]
	entry["color2"] = payload["color2"]
	entry["mixName"] = payload["mix_name"]
	entry["quality"] = payload["quality"]
	entry["clType"] = payload["cl_type"]
	entry["gsm"] = payload["gsm"]
	entry["shaft"] = payload["shaft"]
	entry["no_of_shaft"] = payload["no_of_shaft"]
	entry["kg"] = payload["kg"]
	entry["_isManual"] = True
	entry["_fromGsmEntry"] = True
	if payload.get("plan_name"):
		entry["_plan_name"] = _cstr(payload.get("plan_name")).strip() or "Default"


def _create_mix_items_for_store_row(entry: dict):
	"""CREATE ITEMS equivalent so the GSM Mix Rolls card can appear."""
	quality = _cstr(entry.get("quality")).strip()
	cl_type = _cstr(entry.get("clType") or entry.get("cl_type")).strip()
	gsm = entry.get("gsm")
	shaft = _cstr(entry.get("shaft")).strip()
	unit = _cstr(entry.get("unit")).strip()
	if not quality or not cl_type or not gsm or not shaft:
		frappe.throw(_("Quality, Color, GSM and Shaft Details are required to create mix items."))
	validate_mix_shaft_width(unit, shaft)
	details = create_mix_item(quality, cl_type, gsm, shaft, unit=unit)
	if not details:
		frappe.throw(_("Could not create mix items."))
	if not isinstance(details, list):
		details = [details]
	entry["item_code"] = ", ".join(_cstr(d.get("item_code")) for d in details if d)
	entry["item_name"] = " | ".join(_cstr(d.get("item_name")) for d in details if d)
	return details


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_mix_roll_form_options(
	unit=None,
	planned_date=None,
	view_scope=None,
	filter_week=None,
	filter_month=None,
	run_date=None,
):
	"""Unit shaft hint + colours already used on Color Chart mix rolls for this unit."""
	target_unit = _normalize_mix_unit(unit)
	max_in = get_mix_roll_unit_max_shaft_inches(target_unit) if target_unit else 0
	colors = set()
	if target_unit:
		for _dk, entry in _iter_mix_roll_store_rows():
			if not isinstance(entry, dict):
				continue
			if _normalize_mix_unit(entry.get("unit")) != target_unit:
				continue
			for key in ("color1", "color2"):
				c = _cstr(entry.get(key)).strip().upper()
				if c and c != "NO COLOR":
					colors.add(c)
	hint_n = int(max_in) if max_in and max_in == int(max_in) else max_in
	return {
		"unit": target_unit,
		"max_shaft_inches": max_in,
		"shaft_hint": f'Max {hint_n}" total' if max_in else "",
		"colors": sorted(colors),
		"qualities": ["Virgin Mix", "Eco Mix", "Deluxe Mix"],
		"cl_types": ["Color Mix", "Beige Mix", "White Mix", "Black Mix"],
		"date_key": _gsm_mix_store_date_key(
			planned_date=planned_date,
			view_scope=view_scope,
			filter_week=filter_week,
			filter_month=filter_month,
			run_date=run_date,
		),
	}


@frappe.whitelist(methods=["GET", "POST"])
def upsert_gsm_mix_roll_from_entry(
	unit,
	color1,
	color2,
	mix_name,
	quality,
	cl_type,
	gsm,
	shaft,
	no_of_shaft=1,
	kg=0,
	planned_date=None,
	view_scope=None,
	filter_week=None,
	filter_month=None,
	run_date=None,
):
	"""Reverse Color Chart → GSM: operator adds a mix roll on GSM, persist to Color Chart store.

	Lookup is unit + from colour + to colour in the GSM browse month:
	- not on plan → new Color Chart row
	- on plan with CREATE ITEMS (no item_code) → fill that row
	- on plan with UPDATE ITEMS (item_code already set) → new Color Chart row
	Then CREATE ITEMS so the Mix Rolls card appears for production entry.
	"""
	target_unit = _normalize_mix_unit(unit)
	color1 = _cstr(color1).strip().upper()
	color2 = _cstr(color2).strip().upper()
	mix_name = _cstr(mix_name).strip()
	quality = _cstr(quality).strip()
	cl_type = _cstr(cl_type).strip()
	shaft = _cstr(shaft).strip()
	gsm_val = flt(gsm)
	no_of_shaft = max(cint(no_of_shaft) or 1, 1)
	kg_val = flt(kg)
	if not target_unit:
		frappe.throw(_("Unit is required"))
	if not color1 or not color2:
		frappe.throw(_("From colour and To colour are required"))
	if not mix_name:
		frappe.throw(_("Mix Name is required"))
	if not quality:
		frappe.throw(_("Quality is required"))
	if not cl_type:
		frappe.throw(_("Color (mix type) is required"))
	if gsm_val <= 0:
		frappe.throw(_("GSM is required"))
	if not shaft:
		frappe.throw(_("Shaft Details are required"))
	validate_mix_shaft_width(target_unit, shaft)

	scope_months = _gsm_browse_scope_months(
		planned_date=planned_date,
		view_scope=view_scope,
		filter_week=filter_week,
		filter_month=filter_month,
		run_date=run_date,
	)
	if not scope_months:
		ref = getdate(run_date) if run_date else getdate()
		scope_months = {f"{ref.year:04d}-{ref.month:02d}"}

	payload = {
		"unit": target_unit,
		"color1": color1,
		"color2": color2,
		"mix_name": mix_name,
		"quality": quality,
		"cl_type": cl_type,
		"gsm": gsm_val if gsm_val == int(gsm_val) else gsm_val,
		"shaft": shaft,
		"no_of_shaft": no_of_shaft,
		"kg": kg_val,
	}
	if payload["gsm"] == int(payload["gsm"]):
		payload["gsm"] = int(payload["gsm"])

	target_plan = _resolve_mix_target_plan(
		target_unit,
		color1,
		color2,
		planned_date=planned_date,
		run_date=run_date,
	)
	payload["plan_name"] = target_plan

	mix_match_key = _mix_roll_match_key(target_unit, color1, color2)
	found_key, found_id, action = _find_gsm_operator_mix_match(
		target_unit, mix_match_key, scope_months, preferred_plan=target_plan
	)

	# Prefer writing onto the resolved plan key (unless updating an existing CREATE ITEMS row)
	preferred_key = _gsm_mix_store_date_key(
		planned_date=planned_date,
		view_scope=view_scope,
		filter_week=filter_week,
		filter_month=filter_month,
		run_date=run_date,
		plan_name=target_plan,
	)

	if action == "update" and found_key:
		date_key = found_key
		entries = _load_mix_store_entries(date_key)
		entry = None
		for row in entries:
			if not isinstance(row, dict) or row.get("isRecycle"):
				continue
			if found_id and _cstr(row.get("mix_id")).strip() == found_id:
				if _cstr(row.get("item_code")).strip() or row.get("_submitted"):
					continue
				entry = row
				break
			if (
				not found_id
				and _mix_roll_match_key(row.get("unit"), row.get("color1"), row.get("color2"))
				== mix_match_key
				and not _cstr(row.get("item_code")).strip()
				and not row.get("_submitted")
			):
				entry = row
				break
		if entry is None:
			date_key = preferred_key
			entries = _load_mix_store_entries(date_key)
			entry = _new_gsm_mix_store_row(payload)
			entries.append(entry)
			action = "created"
		else:
			_apply_gsm_mix_fields(entry, payload)
			action = "updated"
	else:
		date_key = preferred_key
		# If a same-plan unfinished row was found under another scope key, still create on preferred
		entries = _load_mix_store_entries(date_key)
		entry = _new_gsm_mix_store_row(payload)
		entries.append(entry)
		action = "created"

	_create_mix_items_for_store_row(entry)
	entry["_plan_name"] = target_plan
	save_mix_roll_data(date_key, entries)
	_copy_mix_row_to_related_stores(entry, date_key, planned_date=planned_date or run_date)
	_purge_mix_id_from_other_plan_stores(_cstr(entry.get("mix_id")), date_key)

	candidate = _serialize_mix_roll_candidate(date_key, entry)
	overlap = sorted(_mix_date_key_months(date_key) & scope_months)
	candidate["planning_month"] = overlap[0] if overlap else (sorted(scope_months)[0] if scope_months else "")
	candidate["plan_name"] = target_plan
	return {
		"status": "ok",
		"action": action,
		"date_key": date_key,
		"plan_name": target_plan,
		"mix": candidate,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_mix_rolls_for_unit(
	unit,
	include_submitted=0,
	run_date=None,
	planned_date=None,
	view_scope=None,
	filter_week=None,
	filter_month=None,
):
	"""List planner-ready mix rows for GSM operator, scoped to browse month + unit.

	Mix rolls match the GSM browse filters (Planned Date / week / month), not the
	production run_date month. Rows require planner item + shaft/width on Color Chart.
	"""
	target_unit = _normalize_mix_unit(unit)
	if not target_unit:
		frappe.throw(_("Unit is required"))

	scope_months = _gsm_browse_scope_months(
		planned_date=planned_date,
		view_scope=view_scope,
		filter_week=filter_week,
		filter_month=filter_month,
		run_date=run_date,
	)
	if not scope_months:
		ref = getdate(run_date) if run_date else getdate()
		scope_months = {f"{ref.year:04d}-{ref.month:02d}"}
	target_month = sorted(scope_months)[0]

	out = []
	seen = set()
	for date_key, entry in _iter_mix_roll_store_rows():
		row_unit = _normalize_mix_unit(entry.get("unit"))
		if row_unit != target_unit:
			continue
		if not _cstr(entry.get("item_code")).strip():
			continue
		# Planning team enters width in shaft details — hide rows without it.
		if not _cstr(entry.get("shaft")).strip():
			continue
		# Show only browse scope month(s), based on the planned (Color Chart) date key.
		key_months = _mix_date_key_months(date_key)
		if not key_months or not (key_months & scope_months):
			continue
		if cint(include_submitted) == 0 and entry.get("_submitted"):
			continue
		mix_id = _cstr(entry.get("mix_id")).strip()
		dedupe = mix_id or f"{date_key}::{_mix_roll_row_key(entry)}"
		if dedupe in seen:
			continue
		seen.add(dedupe)
		candidate = _serialize_mix_roll_candidate(date_key, entry)
		overlap = sorted(key_months & scope_months)
		candidate["planning_month"] = overlap[0] if overlap else target_month
		out.append(candidate)

	out.sort(
		key=lambda r: (
			_cstr(r.get("planning_date_key")),
			_cstr(r.get("label")),
			_cstr(r.get("mix_row_key")),
		)
	)
	return {"unit": target_unit, "month": target_month, "months": sorted(scope_months), "mix_rolls": out}


@frappe.whitelist(methods=["GET", "POST"])
def activate_gsm_mix_roll_for_session(
	date_key,
	mix_id=None,
	mix_row_key=None,
	run_date=None,
	shift=None,
	unit=None,
):
	"""Operator selects a mix row — create draft SPR or remap existing draft to this shift."""
	date_key = _cstr(date_key).strip()
	run_date = getdate(run_date) if run_date else getdate()
	shift = _normalize_gsm_shift_label(shift) or get_current_shift()
	unit = _normalize_mix_unit(unit)
	if not date_key or not unit:
		frappe.throw(_("Mix row date key and unit are required"))

	mix_entry, _all_entries = _find_mix_roll_store_row(date_key, mix_id=mix_id, mix_row_key=mix_row_key)
	if not mix_entry:
		frappe.throw(_("Mix roll row not found on Color Chart store"))
	if not _cstr(mix_entry.get("item_code")).strip():
		frappe.throw(_("Items not created yet — planner must CREATE ITEMS on Color Chart first"))
	if mix_entry.get("_submitted"):
		frappe.throw(_("This mix roll is already submitted"))

	spr_name = _cstr(mix_entry.get("spr_name")).strip()
	if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) == 1:
			frappe.throw(_("Mix roll SPR {0} is already submitted").format(spr_name))
		if cint(spr.docstatus) == 2:
			spr_name = ""
		else:
			spr.run_date = run_date
			spr.shift = shift
			spr.custom_unit = unit
			if not _cstr(spr.custom_order_code).strip():
				spr.custom_order_code = _cstr(mix_entry.get("mixName") or "")
			spr.save(ignore_permissions=True)
			frappe.db.commit()

	if not spr_name:
		payload = dict(mix_entry)
		payload["cl_type"] = payload.get("cl_type") or payload.get("clType")
		spr_name = create_mix_spr(
			date_key,
			[payload],
			run_date=str(run_date),
			shift=shift,
			unit=unit,
		)
		_sync_mix_roll_spr_name_in_store(date_key, mix_entry, spr_name)

	spr = frappe.get_doc("Shaft Production Run", spr_name)
	rolls = _gsm_serialize_spr_roll_lines_for_grid(spr)
	return {
		"status": "ok",
		"spr_name": spr_name,
		"is_mix_roll": 1,
		"mix": _serialize_mix_roll_candidate(date_key, mix_entry),
		"roll_lines": rolls,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_gsm_mix_roll_spr_rolls(spr_name):
	"""Load mix-roll SPR roll lines for GSM grid."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if not spr_doc_is_mix_roll(spr):
		frappe.throw(_("Not a mix roll Shaft Production Run"))
	return {
		"spr_name": spr_name,
		"docstatus": cint(spr.docstatus),
		"custom_unit": _cstr(spr.get("custom_unit") or ""),
		"order_code": _cstr(spr.get("custom_order_code") or ""),
		"roll_lines": _gsm_serialize_spr_roll_lines_for_grid(spr),
	}


@frappe.whitelist(methods=["GET", "POST"])
def add_gsm_mix_roll_line(spr_name, item_code=None, width_inch=None, batch_no=None, gsm=None):
	"""Add one empty mix roll line on draft mix SPR (uses build_spr_roll_result_lines_for_job)."""
	from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
		_build_mix_roll_result_lines_for_job,
		_gsm_serialize_item_row_for_grid,
		_spr_operation_lock,
	)

	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if not spr_doc_is_mix_roll(spr):
			frappe.throw(_("Only mix roll SPRs can use this action"))
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot add roll lines to a submitted SPR"))

		job_row = (_spr_job_rows(spr) or [None])[0]
		if not job_row:
			frappe.throw(_("Mix roll SPR has no shaft job row"))

		current = len([it for it in (spr.items or []) if _spr_is_real_roll_item_row(it)])
		new_lines = _build_mix_roll_result_lines_for_job(
			spr, job_row, exact_roll_lines=1, roll_start_index=current
		)
		if not new_lines:
			frappe.throw(_("Could not build mix roll line"))

		line = new_lines[0]
		if item_code:
			line["item_code"] = _cstr(item_code).strip()
		if width_inch:
			line["width_inch"] = flt(width_inch)
		if gsm:
			line["gsm"] = cint(gsm)
		if batch_no:
			line["batch_no"] = _cstr(batch_no).strip()
		line["meter_roll"] = 0
		line["party_code"] = _cstr(spr.get("custom_order_code") or "")

		spr.append("items", line)
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)

		added = spr.items[-1]
		return {
			"status": "ok",
			"spr_name": spr_name,
			"roll_line": _gsm_serialize_item_row_for_grid(added, ""),
			"row_name": _cstr(getattr(added, "name", "")),
		}


@frappe.whitelist(methods=["GET", "POST"])
def submit_gsm_mix_roll_spr(spr_name):
	"""Submit mix-roll SPR — Material Receipt path (no WO / Manufacture)."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if not spr_doc_is_mix_roll(spr):
		frappe.throw(_("Use fabric submit for non-mix SPRs"))
	already = cint(spr.docstatus) == 1
	if not already:
		if cint(spr.docstatus) != 0:
			frappe.throw(_("SPR cannot be submitted from status {0}").format(spr.docstatus))
		spr.flags.ignore_permissions = True
		spr.submit()
	_mark_mix_roll_store_submitted_for_spr(spr_name)
	return {
		"status": "ok",
		"spr_name": spr_name,
		"docstatus": 1,
		"already_submitted": 1 if already else 0,
	}


def _mark_mix_roll_store_submitted_for_spr(spr_name: str) -> None:
	"""Flag Color Chart mix_roll_store_data rows that point at this SPR as submitted."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not _mix_roll_store_table_exists():
		return
	rows = frappe.db.sql("SELECT date_key, data FROM `mix_roll_store_data`")
	for date_key, raw in rows or []:
		try:
			entries = json.loads(raw) if raw else []
		except Exception:
			continue
		if not isinstance(entries, list):
			continue
		updated = False
		for entry in entries:
			if not isinstance(entry, dict):
				continue
			if _cstr(entry.get("spr_name")).strip() != spr_name:
				continue
			entry["_submitted"] = 1
			entry["spr_name"] = spr_name
			updated = True
		if updated:
			frappe.db.sql(
				"UPDATE `mix_roll_store_data` SET data = %s, modified = NOW() WHERE date_key = %s",
				(json.dumps(entries, ensure_ascii=False), date_key),
			)
	frappe.db.commit()
