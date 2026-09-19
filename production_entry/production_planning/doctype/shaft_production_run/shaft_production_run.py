import json
import math
import re
from collections import OrderedDict, defaultdict
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_link_to_form, getdate, nowtime, today

from production_entry.production_planning.doctype.planning_sheet.planning_sheet import (
	extract_quality_and_color,
)
from production_entry.production_planning.scheduler_api import (
	_bom_rm_stock_qty_map_for_fg,
)
from production_entry.production_planning.planning_doctypes import (
	BOX_BAG_UNIT_L1,
	BOX_BAG_UNIT_L2,
	BOX_BAG_UNIT_L4_SCREEN,
	BOX_BAG_UNASSIGNED_UNIT,
	LAMINATION_UNIT,
	PRINTED_BOPP_FILM_UNIT,
	PRINTING_UNASSIGNED_UNIT,
	PRINTING_UNIT_2_COLOUR,
	PRINTING_UNIT_4_COLOUR,
	PRINTING_UNIT_TT,
	REWINDING_UNASSIGNED_UNIT,
	REWINDING_UNIT_L3,
	REWINDING_UNIT_L4,
	REWINDING_UNIT_L5,
	SHEET_CUTTING_UNIT,
	SLITTING_UNIT,
	SLITTING_UNIT_VTP,
	SLITTING_UNASSIGNED_UNIT,
	W_CUT_D_CUT_UNIT_JVE_L1,
	W_CUT_D_CUT_UNIT_JVE_L2,
	W_CUT_D_CUT_UNIT_JVE_L3,
	W_CUT_D_CUT_UNIT_L1,
	W_CUT_D_CUT_UNIT_L2,
	W_CUT_D_CUT_UNIT_L3,
	W_CUT_D_CUT_ALL_UNITS,
	normalize_planning_unit_for_select,
	get_mix_roll_unit_max_shaft_inches,
	validate_mix_shaft_width,
	resolve_mix_roll_company_and_fg_warehouse,
	ensure_planning_line_unit_docfield_options,
)

# Unique (company_id, 2-digit unit_no) per workstation — never reuse across units.
SPR_BATCH_UNIT_MAP = {
	"Unit 1": ("JS", "01"),
	"Unit 2": ("JS", "02"),
	"Unit 3": ("JS", "03"),
	"Unit 4": ("TS", "04"),
	LAMINATION_UNIT: ("TS", "05"),
	SLITTING_UNIT: ("JV", "06"),
	SLITTING_UNIT_VTP: ("VTP", "23"),
	REWINDING_UNIT_L3: ("TS", "07"),
	REWINDING_UNIT_L4: ("JS", "08"),
	REWINDING_UNIT_L5: ("JS", "09"),
	SHEET_CUTTING_UNIT: ("JV", "10"),
	PRINTING_UNIT_2_COLOUR: ("JV", "11"),
	PRINTING_UNIT_TT: ("TT", "12"),
	PRINTING_UNIT_4_COLOUR: ("JV", "13"),
	PRINTED_BOPP_FILM_UNIT: ("VR", "14"),
	BOX_BAG_UNIT_L2: ("VTP", "15"),
	BOX_BAG_UNIT_L1: ("VTP", "16"),
	BOX_BAG_UNIT_L4_SCREEN: ("VTP", "24"),
	W_CUT_D_CUT_UNIT_JVE_L1: ("JVE", "17"),
	W_CUT_D_CUT_UNIT_JVE_L2: ("JVE", "18"),
	W_CUT_D_CUT_UNIT_JVE_L3: ("JVE", "19"),
	W_CUT_D_CUT_UNIT_L1: ("TTT", "20"),
	W_CUT_D_CUT_UNIT_L2: ("TTT", "21"),
	W_CUT_D_CUT_UNIT_L3: ("TTT", "22"),
}


def spr_batch_prefix_for_unit(unit_value: str):
	"""Resolve batch company + unit digits for a workstation name. Returns None if not configured."""
	u = normalize_planning_unit_for_select(_cstr(unit_value))
	if not u or u == "UNASSIGNED":
		return None
	if u in SPR_BATCH_UNIT_MAP:
		return SPR_BATCH_UNIT_MAP[u]
	m = re.search(r"(?i)\bunit\s*(\d+)\b", _cstr(unit_value))
	if m:
		num = int(m.group(1))
		if num == 4:
			return ("TS", "04")
		if 1 <= num <= 3:
			return ("JS", f"{num:02d}")
	return None


# FG Work Order processes that require manual batch-tracked RM lines in SPR batch picker.
SPR_FG_FABRIC_PICK_PROCESSES = ("102", "103", "104", "105", "106", "107", "108", "109", "251", "252", "253", "254", "255")


def spr_fg_item_process_code(item_code: str) -> str:
	"""3-digit process code from FG item_code.

	- Design-first printing / sheet: ``002-105…``, ``DES-252…`` (process after first hyphen).
	- Legacy width suffix: ``102…-1600`` (short numeric tail → process from head digit stream).
	"""
	raw = (item_code or "").strip().upper()
	if not raw:
		return ""
	if " - " in raw:
		raw = raw.split(" - ")[-1].strip()
	if "-" in raw:
		parts = raw.split("-", 1)
		head = parts[0].strip()
		tail = parts[1].strip() if len(parts) > 1 else ""
		if raw.count("-") == 1 and tail.isdigit() and len(tail) <= 4:
			head_digits = "".join([ch for ch in head if ch.isdigit()])
			if len(head_digits) >= 3:
				return head_digits[:3]
		tail_digits = "".join([ch for ch in tail if ch.isdigit()])
		if len(tail_digits) >= 3:
			return tail_digits[:3]
	digits = "".join([ch for ch in raw if ch.isdigit()])
	return digits[:3] if len(digits) >= 3 else ""


def spr_fg_parent_needs_fabric_batch_pick(production_item: str) -> bool:
	"""True when WO FG needs manual batch-tracked RM allocation for this SPR (102–109 incl. 108, 251–255 incl. 254, design-first)."""
	proc = spr_fg_item_process_code(production_item)
	return bool(proc) and proc in SPR_FG_FABRIC_PICK_PROCESSES


# FG process → (immediate BOM child process, fabric process) for desk batch-pick dialog.
_SPR_BOM_STACK_BY_FG_PROCESS = {
	"102": (None, "100"),
	"103": (None, "100"),
	"104": (None, "100"),
	"105": (None, "100"),
	"106": (None, "100"),
	"107": (None, "100"),
	"108": (None, "100"),
	"109": (None, "100"),
	"110": (None, "100"),
	"251": (None, "100"),
	"252": ("105", "100"),
	"253": ("104", "100"),
	"254": ("106", "100"),
	"255": (None, "100"),
	"200": (None, "100"),
	"201": ("105", "100"),
	"202": ("104", "100"),
	"211": (None, "100"),
	"212": ("105", "100"),
	"213": ("104", "100"),
	"216": ("107", "100"),
	"217": ("107", "100"),
	"225": ("106", "104"),
	"226": ("106", "104"),
	"221": ("103", "100"),
	"222": ("107", "100"),
	"223": ("107", "100"),
	"224": ("104", "103"),
	"231": ("107", "104"),
	"232": ("231", "107"),
	"233": ("107", "104"),
	"241": ("106", "104"),
	"242": ("106", "104"),
}

# Box bag + W-CUT + D-CUT finished-goods process codes (221/224, 211–217, 200–203, BOPP bag, etc.).
ALL_BAG_FG_PROCESS_CODES = frozenset({
	"200", "201", "202", "203",
	"211", "212", "213", "214", "216", "217",
	"221", "222", "223", "224", "231", "232", "233", "241", "242", "225", "226",
})


def _spr_resolve_bag_fg_process_code(item_code: str) -> str:
	"""Resolve FG process for bag items (design-first 6000-511-221…, W/D-CUT, BOPP bag)."""
	ic = _cstr(item_code).strip()
	if not ic:
		return ""
	try:
		from production_entry.production_planning.scheduler_api import (
			W_CUT_D_CUT_FG_PROCESS_CODES,
			_item_process_prefix,
		)

		try:
			from production_entry.production_planning.box_bag_api import (
				_parse_box_bag_item_code,
				_parse_dcut_bag_item_code,
			)

			p_dc = _parse_dcut_bag_item_code(ic) or {}
			proc_dc = _cstr(p_dc.get("process") or "").strip()
			if proc_dc in W_CUT_D_CUT_FG_PROCESS_CODES:
				return proc_dc
			p_bb = _parse_box_bag_item_code(ic) or {}
			proc_bb = _cstr(p_bb.get("process") or "").strip()
			if proc_bb in ALL_BAG_FG_PROCESS_CODES:
				return proc_bb
		except Exception:
			pass
		try:
			from production_entry.production_planning.bopp_bag_api import _parse_bopp_bag_item_code

			p_bopp = _parse_bopp_bag_item_code(ic) or {}
			proc_bopp = _cstr(p_bopp.get("process") or "").strip()
			if proc_bopp in ALL_BAG_FG_PROCESS_CODES:
				return proc_bopp
		except Exception:
			pass
		proc = _cstr(_item_process_prefix(ic)).strip()
		if proc in ALL_BAG_FG_PROCESS_CODES:
			return proc
	except Exception:
		pass
	proc = spr_fg_item_process_code(ic)
	return proc if proc in ALL_BAG_FG_PROCESS_CODES else ""


def spr_bag_fg_needs_rm_batch_pick(production_item: str) -> bool:
	"""Bag FG SPR (box bag, W-CUT, D-CUT, BOPP bag) needs Select RM batches dialog."""
	return bool(_spr_resolve_bag_fg_process_code(production_item))


def spr_fg_needs_rm_batch_pick(production_item: str, is_bag_spr: bool = False) -> bool:
	if is_bag_spr:
		return spr_bag_fg_needs_rm_batch_pick(production_item)
	return spr_fg_parent_needs_fabric_batch_pick(production_item)


def _spr_item_has_batch_no(item_code: str) -> bool:
	try:
		return bool(cint(frappe.db.get_value("Item", item_code, "has_batch_no") or 0))
	except Exception:
		return False


def _spr_rm_needs_manual_batch_pick(item_code: str) -> bool:
	"""True for any batch-tracked BOM RM on eligible FG WO (105 on 252 WO, not only 100*)."""
	ic = _cstr(item_code)
	if not ic:
		return False
	return _spr_item_has_batch_no(ic)


def _spr_bom_stack_for_fg_item(production_item: str) -> list[dict]:
	"""BOM chain rows for the fabric-batch dialog (FG → child → fabric)."""
	proc = _spr_resolve_bag_fg_process_code(production_item) or spr_fg_item_process_code(production_item)
	if not proc:
		return []
	child_proc, fabric_proc = _SPR_BOM_STACK_BY_FG_PROCESS.get(proc, (None, "100"))
	out = [{"process": proc, "label": _("FG ({0})").format(proc), "role": "fg", "item_code": _cstr(production_item)}]
	if child_proc:
		out.append(
			{
				"process": child_proc,
				"label": _("BOM child ({0})").format(child_proc),
				"role": "child",
				"item_code": "",
			}
		)
	if fabric_proc:
		out.append(
			{
				"process": fabric_proc,
				"label": _("Fabric ({0})").format(fabric_proc),
				"role": "fabric",
				"item_code": "",
			}
		)
	return out


def batch_shift_value(shift: str | None) -> str:
	if not shift:
		return ""
	s = shift.lower()
	if "night" in s:
		return "Night"
	if "day" in s:
		return "Day"
	return shift


def _cstr(v) -> str:
	return str(v).strip() if v is not None else ""


def _spr_exc_message(exc=None) -> str:
	"""Frappe often stores the real throw text in message_log, not on the exception."""
	parts = []
	if exc is not None:
		text = _cstr(exc)
		if text:
			parts.append(text)
		for a in getattr(exc, "args", ()) or ():
			s = _cstr(a)
			if s and s not in parts:
				parts.append(s)
		title = _cstr(getattr(exc, "title", None))
		if title and title not in parts:
			parts.append(title)
		typename = type(exc).__name__
		if typename and typename not in ("Exception", "BaseException") and typename not in " ".join(parts):
			parts.append(typename)
	try:
		for m in frappe.get_message_log() or []:
			if isinstance(m, dict):
				s = _cstr(m.get("message") or m.get("title"))
			else:
				s = _cstr(m)
			s = re.sub(r"<[^>]+>", " ", s)
			s = re.sub(r"\s+", " ", s).strip()
			if s and s not in parts:
				parts.append(s)
	except Exception:
		pass
	return "\n".join(p for p in parts if p).strip()


def _spr_mark_stock_entry_system_flags(se) -> None:
	if not se:
		return
	se.flags.ignore_permissions = True
	se.flags.ignore_mandatory = True
	se.flags.ignore_validate = True
	se.flags.ignore_links = True
	se.flags.ignore_duplicate_for_work_order = True


@contextmanager
def _spr_as_administrator():
	"""Post SPR stock docs without the logged-in user's Stock Entry / warehouse limits."""
	user = frappe.session.user
	perm_backup = frappe.flags.get("ignore_permissions")
	switched = False
	try:
		frappe.flags.ignore_permissions = True
		if user and user != "Administrator":
			frappe.set_user("Administrator")
			switched = True
		yield
	finally:
		if switched:
			try:
				frappe.set_user(user)
			except Exception:
				pass
		frappe.flags.ignore_permissions = perm_backup


def _spr_db_savepoint_name(prefix: str, key: str = "") -> str:
	"""MariaDB savepoint identifiers cannot contain hyphens (e.g. MFG-WO-2026-00917)."""
	safe_key = re.sub(r"[^0-9a-zA-Z_]", "_", _cstr(key))
	name = f"{prefix}_{safe_key}" if safe_key else _cstr(prefix)
	return name[:64]


def _compact_unit_key(value) -> str:
	return re.sub(r"[^A-Z0-9]", "", _cstr(value).upper())


def _units_equivalent(a, b) -> bool:
	"""True when two unit labels are the same workstation (Unit 2 vs UNIT 2)."""
	ka, kb = _compact_unit_key(a), _compact_unit_key(b)
	return bool(ka and kb and ka == kb)


def _unit_value_for_doctype_field(unit_value, doctype: str, fieldname: str, meta=None) -> str:
	"""Return a unit value that validates against the current DocType field."""
	raw = _cstr(unit_value)
	if not raw:
		return ""
	normalized = normalize_planning_unit_for_select(raw)
	if not normalized or normalized == "UNASSIGNED":
		return ""

	try:
		field_meta = meta or frappe.get_meta(doctype)
		df = field_meta.get_field(fieldname)
	except Exception:
		df = None
	if not df or df.fieldtype != "Select":
		return normalized

	options = [_cstr(opt) for opt in _cstr(df.options).splitlines() if _cstr(opt)]
	if not options:
		return normalized
	if raw in options:
		return raw
	if normalized in options:
		return normalized

	alias_map = {
		"Unit 1": ["UNIT 1", "Unit 1"],
		"Unit 2": ["UNIT 2", "Unit 2"],
		"Unit 3": ["UNIT 3", "Unit 3"],
		"Unit 4": ["UNIT 4", "Unit 4"],
		LAMINATION_UNIT: ["Lamination Unit", LAMINATION_UNIT],
		SLITTING_UNIT: ["Slitting Unit", SLITTING_UNIT],
		REWINDING_UNASSIGNED_UNIT: ["Unassigned rewinding machine", REWINDING_UNASSIGNED_UNIT],
		PRINTING_UNASSIGNED_UNIT: ["Unassigned printing machine", PRINTING_UNASSIGNED_UNIT],
		SHEET_CUTTING_UNIT: [SHEET_CUTTING_UNIT],
		REWINDING_UNIT_L3: [REWINDING_UNIT_L3],
		REWINDING_UNIT_L4: [REWINDING_UNIT_L4],
		REWINDING_UNIT_L5: [REWINDING_UNIT_L5],
		PRINTED_BOPP_FILM_UNIT: [PRINTED_BOPP_FILM_UNIT],
		PRINTING_UNIT_2_COLOUR: [PRINTING_UNIT_2_COLOUR, "JVE - PRINTING MACHINE 2 COLOUR"],
		PRINTING_UNIT_4_COLOUR: [PRINTING_UNIT_4_COLOUR, "JVE - PRINTING MACHINE 4 COLOUR"],
		PRINTING_UNIT_TT: [PRINTING_UNIT_TT, "TT - PRINTING MACHINE COLOUR 1200MM"],
	}
	candidates = [raw, normalized] + alias_map.get(normalized, [])
	option_by_key = {_compact_unit_key(opt): opt for opt in options}
	for candidate in candidates:
		candidate_key = _compact_unit_key(candidate)
		if candidate_key in option_by_key:
			return option_by_key[candidate_key]

	# Older live sites can still have stale Select options. Widen the in-memory
	# meta so validation accepts the canonical Workstation name during migration.
	if normalized not in options:
		df.options = (_cstr(df.options) + "\n" + normalized).strip()
	return normalized


def _spr_unit_value_for_current_field(unit_value) -> str:
	"""Return a unit value that validates against the site's current SPR custom_unit field."""
	return _unit_value_for_doctype_field(unit_value, "Shaft Production Run", "custom_unit")


def _spr_bundle_source_batch_prefix(batch_no) -> str:
	raw = _cstr(batch_no)
	if not raw:
		return ""
	m = re.match(r"^(?P<prefix>.+)-B\d+(?:-\d+-\d+)?$", raw)
	return _cstr(m.group("prefix")) if m else raw


def _spr_next_bundle_batch_no(spr, source_prefix: str) -> str:
	source_prefix = _cstr(source_prefix)
	if not source_prefix:
		return ""
	max_no = 0
	for row in spr.bundle_stickers or []:
		bn = _cstr(getattr(row, "batch_no", ""))
		m = re.match(rf"^{re.escape(source_prefix)}-B(\d+)(?:-\d+-\d+)?$", bn)
		if m:
			max_no = max(max_no, cint(m.group(1)))
	return f"{source_prefix}-B{max_no + 1}"


def _spr_is_bundle_summary_batch(batch_no: str) -> bool:
	"""True for GSM/SPR summary batches like JS-0307262-B1 (not real roll suffix /N)."""
	return bool(re.search(r"-B\d+(?:-\d+-\d+)?$", _cstr(batch_no)))


def _spr_is_real_roll_item_row(item_row) -> bool:
	bn = _cstr(getattr(item_row, "batch_no", "") or "")
	return bool(bn) and not _spr_is_bundle_summary_batch(bn)


def _spr_roll_has_production(item_row) -> bool:
	"""True when the operator has entered production, not just a planned SPR slot.

	Planned desk lines often copy job net weight onto the item. Count only
	produced length or gross weight — the fields GSM Save Row always writes.
	"""
	if flt(getattr(item_row, "gross_weight", None) or 0) > 0:
		return True
	for key in ("custom_gross_weight", "custom_gross_weight_kgs"):
		if flt(getattr(item_row, key, None) or 0) > 0:
			return True
	if flt(getattr(item_row, "produced_length_mtrs", None) or 0) > 0:
		return True
	if flt(getattr(item_row, "custom_produced_length_mtrs", None) or 0) > 0:
		return True
	return False


def _gsm_payload_is_bundle_summary(payload: dict) -> bool:
	if not isinstance(payload, dict):
		return False
	if cint(payload.get("is_bundle_row") or 0):
		return True
	return _spr_is_bundle_summary_batch(_cstr(payload.get("batch_no")))


def _spr_row_get(spr_row, key: str):
	if spr_row is None:
		return None
	if isinstance(spr_row, dict):
		return spr_row.get(key)
	return spr_row.get(key)


def _batch_field_net_weight_kgs(batch_meta) -> str | None:
	for df in batch_meta.fields:
		lab = (df.label or "").lower()
		if "net" in lab and "weight" in lab:
			return df.fieldname
	for fn in ("custom_net_weight_kgs", "custom_net_weight", "net_weight_kgs"):
		if batch_meta.has_field(fn):
			return fn
	return None


def _batch_field_gross_weight_kgs(batch_meta) -> str | None:
	for df in batch_meta.fields:
		lab = (df.label or "").lower()
		if "gross" in lab and "weight" in lab:
			return df.fieldname
	for fn in ("custom_gross_weight_kgs", "custom_gross_weight", "gross_weight_kgs"):
		if batch_meta.has_field(fn):
			return fn
	return None


def _batch_field_length_mtrs(batch_meta) -> str | None:
	for df in batch_meta.fields:
		lab = (df.label or "").lower()
		if "planned qty" in lab:
			continue
		if "length" in lab and ("mtr" in lab or "meter" in lab or "mtrs" in lab):
			return df.fieldname
		if "ordered" in lab and "length" in lab:
			return df.fieldname
	for fn in ("custom_length_mtrs", "length_mtrs", "custom_meter_roll", "meter_roll"):
		if batch_meta.has_field(fn):
			return fn
	return None


def _spr_length_meters(spr_row) -> float | None:
	"""Length in m for Batch + reporting: prefers Produced Length (Mtrs), then Meter/Roll."""
	if spr_row is None:
		return None
	for key in ("produced_length_mtrs", "custom_produced_length_mtrs"):
		v = _spr_row_get(spr_row, key)
		if v is not None and flt(v) > 0:
			return flt(v)
	try:
		spi_meta = frappe.get_meta("Shaft Production Run Item")
		for df in spi_meta.fields:
			if df.fieldtype not in ("Float", "Int", "Currency"):
				continue
			lab = (df.label or "").lower()
			if "produced" in lab and "length" in lab:
				v = _spr_row_get(spr_row, df.fieldname)
				if v is not None and flt(v) > 0:
					return flt(v)
	except Exception:
		pass
	for key in ("meter_roll", "ordered_length", "custom_ordered_length"):
		v = _spr_row_get(spr_row, key)
		if v is not None and flt(v) > 0:
			return flt(v)
	return None


def _spr_produced_length_meters(spr_row) -> float:
	"""Produced-only length in meters (no ordered/meter_roll fallback)."""
	if spr_row is None:
		return 0.0
	for key in (
		"produced_length_mtrs",
		"custom_produced_length_mtrs",
		"produced_length",
		"custom_produced_length",
	):
		v = _spr_row_get(spr_row, key)
		if v is not None and flt(v) > 0:
			return flt(v)
	return 0.0


def _spr_first_roll_item_code(doc) -> str:
	for it in doc.get("items") or []:
		ic = _cstr(getattr(it, "item_code", None) or "")
		if ic:
			return ic
	return ""


def _pp_has_lamination_work_order(pp_name: str) -> bool:
	"""True when the production plan has FG work orders for process 104 or 107 lamination."""
	if not pp_name or not frappe.db.exists("Production Plan", pp_name):
		return False
	try:
		for w in frappe.get_all(
			"Work Order",
			filters={"production_plan": pp_name, "docstatus": ["!=", 2]},
			fields=["production_item"],
			limit=50,
		):
			pi = _cstr((w or {}).get("production_item") or "")
			if spr_fg_item_process_code(pi) in ("104", "107"):
				return True
	except Exception:
		pass
	return False


def spr_doc_is_mix_roll(doc) -> bool:
	"""Mix roll SPR from Color Chart — no Production Plan / Work Order."""
	return bool(doc and cint(getattr(doc, "is_mix_roll", 0) or 0))


def spr_doc_is_lamination(doc) -> bool:
	"""Lamination SPR: Is Lamination ticked and plan / roll lines are process 104 or 107."""
	if not doc or not cint(getattr(doc, "custom_is_lamination", 0) or 0):
		return False
	ic = _spr_first_roll_item_code(doc)
	if ic and spr_fg_item_process_code(ic) in ("104", "107"):
		return True
	pp = _cstr(getattr(doc, "production_plan", None) or "")
	return _pp_has_lamination_work_order(pp)


def _fabric_gsm_from_item_name(item_name: str) -> int:
	"""Parse Fabric GSM from item name by finding the F-<number> pattern (e.g. 'F-60' or 'F - 60' ΓåÆ 60)."""
	if not item_name:
		return 0
	m = re.search(r'\bF\s*-\s*(\d+)\b', item_name, re.IGNORECASE)
	if m:
		try:
			return int(m.group(1))
		except Exception:
			pass
	return 0


# Lamination GSM suffix map (same as scheduler_api._LAM_GSM_SUFFIX_MAP): A=10 … F=13; B1 legacy 13.
_LAM_GSM_SUFFIX_MAP: dict[str, int] = {
	"A": 10,
	"B": 12,
	"B1": 13,
	"C": 15,
	"D": 20,
	"E": 30,
	"F": 13,
}


def _lam_gsm_from_item(item_name: str, item_code: str) -> int:
	"""Parse Lamination GSM from item name 'L-15 GSM' pattern, with -C suffix fallback.

	Item name pattern: 'L- 15 GSM' or 'L-15GSM'  ΓåÆ 15
	Item code suffix:  '1041030010750890-C' ΓåÆ suffix 'C' ΓåÆ 15 via _LAM_GSM_SUFFIX_MAP
	"""
	# Primary: parse 'L-<N> GSM' or 'L- <N> GSM' from item name
	if item_name:
		m = re.search(r'\bL-\s*(\d+)\s*GSM\b', item_name, re.IGNORECASE)
		if m:
			try:
				return int(m.group(1))
			except Exception:
				pass
	# Fallback: suffix after last '-' in item code (e.g. '-C' ΓåÆ 'C' ΓåÆ 15)
	if item_code:
		parts = str(item_code).strip().upper().split('-')
		if len(parts) >= 2:
			suffix = parts[-1].strip()
			if suffix in _LAM_GSM_SUFFIX_MAP:
				return _LAM_GSM_SUFFIX_MAP[suffix]
	return 0


def _bopp_gsm_from_item(item_code: str, item_name: str = "") -> int:
	"""Parse BOPP GSM from lamination / slitting item codes (107, 108, 109, encoded tails)."""
	ic = _cstr(item_code)
	if not ic:
		return 0
	try:
		from production_entry.production_planning.scheduler_api import (
			_item_process_prefix,
			_parse_107_item_code,
			_parse_108_item_code,
			_parse_110_item_code,
		)

		proc = _item_process_prefix(ic)
		if proc == "107":
			return cint((_parse_107_item_code(ic) or {}).get("bopp_gsm") or 0)
		if proc in ("108", "109"):
			return cint((_parse_108_item_code(ic) or {}).get("bopp_gsm") or 0)
		if proc == "110":
			return cint((_parse_110_item_code(ic) or {}).get("bopp_gsm") or 0)
		if proc in ("104", "107"):
			parsed = _parse_107_item_code(ic) or {}
			if cint(parsed.get("bopp_gsm") or 0) > 0:
				return cint(parsed.get("bopp_gsm") or 0)
	except Exception:
		pass
	try:
		from production_entry.production_planning.box_bag_api import _parse_dcut_bag_item_code

		parsed = _parse_dcut_bag_item_code(ic) or {}
		if cint(parsed.get("bopp_gsm") or 0) > 0:
			return cint(parsed.get("bopp_gsm") or 0)
	except Exception:
		pass
	return 0


def _fabric_gsm_from_planning_for_pp(pp_name: str) -> int:
	"""Fabric (100ΓÇª) GSM from Planning Table child row on same sheet as 104 lamination line."""
	if not pp_name or not frappe.db.exists("DocType", "Planning Table"):
		return 0
	pt_cols = set(frappe.db.get_table_columns("Planning Table") or [])
	if "custom_production_plan" not in pt_cols:
		return 0
	so_l = "sales_order_item" if "sales_order_item" in pt_cols else None
	so_cust = "custom_sales_order_item" if "custom_sales_order_item" in pt_cols else None
	fab_so = "so_item" if "so_item" in pt_cols else None
	if not fab_so:
		return 0
	params: list = [pp_name]
	if so_l and so_cust:
		join_sql = (
			f"(IFNULL(fab.{fab_so},'') = IFNULL(lam.{so_l},'') OR IFNULL(fab.{fab_so},'') = IFNULL(lam.{so_cust},''))"
		)
	elif so_l:
		join_sql = f"IFNULL(fab.{fab_so},'') = IFNULL(lam.{so_l},'')"
	elif so_cust:
		join_sql = f"IFNULL(fab.{fab_so},'') = IFNULL(lam.{so_cust},'')"
	else:
		return 0
	gcol = "gsm" if "gsm" in pt_cols else None
	if not gcol:
		return 0
	row = frappe.db.sql(
		f"""
		SELECT IFNULL(fab.{gcol}, 0) AS g
		FROM `tabPlanning Table` lam
		INNER JOIN `tabPlanning sheet` ps ON ps.name = lam.parent
		LEFT JOIN `tabPlanning Table` fab ON fab.parent = lam.parent
			AND fab.item_code LIKE '100%%'
			AND ({join_sql})
		WHERE IFNULL(lam.custom_production_plan, '') = %s
		  AND lam.item_code LIKE '104%%'
		ORDER BY lam.idx ASC, fab.idx ASC
		LIMIT 1
		""",
		tuple(params),
		as_dict=True,
	)
	if row and row[0].get("g") is not None:
		return int(flt(row[0].get("g")))
	return 0


# Company RM store for manufacture transfers. Unit-based lookup can return Jayashree
# RM on a Thusma SPR/WO; ERPNext then throws "warehouse does not belong to company".
SPR_COMPANY_RM_WAREHOUSE = {
	"Jayashree Spun Bond - 1ZT": "Raw Materials - JSB-1ZT",
	"Thusma SMS Nonwovens Private Limited - 1Z0": "Raw Materials Warehouse - TSNPL",
}
SPR_COMPANY_WIP_WAREHOUSE = {
	"Thusma SMS Nonwovens Private Limited - 1Z0": "Work In Progress Warehouse - TSNPL",
}


def _spr_warehouse_company(warehouse: str) -> str:
	wh = _cstr(warehouse).strip()
	if not wh or not frappe.db.exists("Warehouse", wh):
		return ""
	return _cstr(frappe.db.get_value("Warehouse", wh, "company"))


def _spr_wh_belongs_to_company(warehouse: str, company: str) -> bool:
	company = _cstr(company).strip()
	wh = _cstr(warehouse).strip()
	if not wh or not company:
		return False
	return _spr_warehouse_company(wh) == company


def _spr_company_wip_warehouse(company: str) -> str:
	company = _cstr(company).strip()
	if not company:
		return ""
	mapped = SPR_COMPANY_WIP_WAREHOUSE.get(company)
	if mapped and frappe.db.exists("Warehouse", mapped) and _spr_wh_belongs_to_company(mapped, company):
		return mapped
	from production_entry.production_planning.spr_unit_warehouses import _company_wip_warehouse

	return _company_wip_warehouse(company)


def _spr_ok_rm_wh(warehouse: str, wip_wh: str = "", company: str = "") -> str:
	"""Return warehouse only if it is a non-WIP store that belongs to company."""
	wh = _cstr(warehouse).strip()
	wip_wh = _cstr(wip_wh).strip()
	company = _cstr(company).strip()
	if not wh or wh == wip_wh:
		return ""
	if company and not _spr_wh_belongs_to_company(wh, company):
		return ""
	return wh


def _spr_company_rm_warehouse(company: str, wip_wh: str = "") -> str:
	"""Best-effort raw-material warehouse for a company (lamination PP/LD, etc.)."""
	company = _cstr(company).strip()
	wip_wh = _cstr(wip_wh).strip()
	if not company:
		return ""
	mapped = SPR_COMPANY_RM_WAREHOUSE.get(company)
	if mapped and frappe.db.exists("Warehouse", mapped):
		if _spr_wh_belongs_to_company(mapped, company) and mapped != wip_wh:
			return mapped
	patterns = (
		"%Raw Material%",
		"%Raw Materials%",
		"%RM Warehouse%",
		"%Stores%",
		"%Store%",
		"%Material%",
	)
	for pattern in patterns:
		for is_group in (0, 1):
			wh = frappe.db.get_value(
				"Warehouse",
				{"company": company, "name": ["like", pattern], "is_group": is_group},
				"name",
				order_by="modified desc",
			)
			wh = _cstr(wh).strip()
			if wh and wh != wip_wh and "work in progress" not in wh.lower():
				if is_group:
					leaf = frappe.db.get_value(
						"Warehouse",
						{"company": company, "parent_warehouse": wh, "is_group": 0},
						"name",
						order_by="modified desc",
					)
					leaf = _cstr(leaf).strip()
					if leaf and leaf != wip_wh:
						return leaf
				else:
					return wh
	rows = frappe.db.sql(
		"""
		SELECT name
		FROM `tabWarehouse`
		WHERE company = %s
		  AND IFNULL(is_group, 0) = 0
		  AND IFNULL(name, '') != ''
		  AND IFNULL(name, '') != %s
		  AND LOWER(IFNULL(name, '')) NOT LIKE %s
		ORDER BY modified DESC
		LIMIT 20
		""",
		(company, wip_wh, "%work in progress%"),
		as_dict=True,
	)
	for row in rows or []:
		wh = _cstr(row.get("name")).strip()
		if wh and wh != wip_wh:
			return wh
	return ""


def _spr_wo_rm_source_warehouse(wo_doc, item_code: str, wip_wh: str = "") -> str:
	"""Per-item RM source warehouse from WO required_items, BOM, prior MTFM, or company RM store."""
	item_code = _cstr(item_code).strip()
	wip_wh = _cstr(wip_wh).strip()
	wo_name = _cstr(getattr(wo_doc, "name", None)).strip()
	company = _cstr(getattr(wo_doc, "company", None)).strip()

	for req in getattr(wo_doc, "required_items", None) or []:
		if _cstr(getattr(req, "item_code", None)).strip() == item_code:
			src = _spr_ok_rm_wh(getattr(req, "source_warehouse", None), wip_wh, company)
			if src:
				return src
	header_src = _spr_ok_rm_wh(getattr(wo_doc, "source_warehouse", None), wip_wh, company)
	if header_src:
		return header_src

	if wo_name and item_code:
		prev = frappe.db.sql(
			"""
			SELECT sed.s_warehouse
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			WHERE IFNULL(se.docstatus, 0) < 2
			  AND IFNULL(se.work_order, '') = %s
			  AND IFNULL(se.purpose, '') = 'Material Transfer for Manufacture'
			  AND IFNULL(sed.item_code, '') = %s
			  AND IFNULL(sed.s_warehouse, '') != ''
			ORDER BY se.modified DESC
			LIMIT 1
			""",
			(wo_name, item_code),
			as_dict=True,
		)
		if prev:
			src = _spr_ok_rm_wh(prev[0].get("s_warehouse"), wip_wh, company)
			if src:
				return src

	bom_no = _cstr(getattr(wo_doc, "bom_no", None)).strip()
	if bom_no and item_code:
		bom_wh = frappe.db.get_value(
			"BOM Item",
			{"parent": bom_no, "item_code": item_code},
			"source_warehouse",
		)
		bom_wh = _spr_ok_rm_wh(bom_wh, wip_wh, company)
		if bom_wh:
			return bom_wh

	if company and item_code:
		item_wh = frappe.db.get_value(
			"Item Default",
			{"parent": item_code, "company": company},
			"default_warehouse",
		)
		item_wh = _spr_ok_rm_wh(item_wh, wip_wh, company)
		if item_wh:
			return item_wh

	co_rm = _spr_company_rm_warehouse(company, wip_wh)
	if co_rm:
		return co_rm

	try:
		default_wh = _spr_ok_rm_wh(
			frappe.db.get_single_value("Stock Settings", "default_warehouse"),
			wip_wh,
			company,
		)
		if default_wh:
			return default_wh
	except Exception:
		pass
	return header_src or ""


def _spr_wo_rm_still_needed_map(wo_doc) -> dict[str, float]:
	"""RM qty still to transfer to WIP from WO required_items (stock UOM, typically Kg)."""
	out = defaultdict(float)
	for req in getattr(wo_doc, "required_items", None) or []:
		ic = _cstr(getattr(req, "item_code", None)).strip()
		if not ic:
			continue
		need = flt(getattr(req, "required_qty", 0)) - flt(getattr(req, "transferred_qty", 0))
		if need > 1e-9:
			out[ic] += need
	return dict(out)


def _spr_rm_stock_qty_precision() -> int:
	"""Decimal places for BOM RM Kg on Stock Entry lines (matches MTFM / site ledger)."""
	try:
		p = int(frappe.conf.get("spr_rm_stock_qty_precision") or 3)
	except (TypeError, ValueError):
		p = 3
	return max(0, min(p, 6))


def _spr_rm_wip_shortage_tolerance(required_qty: float) -> float:
	"""Ignore float/rounding gaps when comparing WIP Bin vs BOM RM need (Kg)."""
	try:
		base = flt(frappe.conf.get("spr_rm_wip_shortage_tolerance_kg") or 0.05)
	except (TypeError, ValueError):
		base = 0.05
	req = flt(required_qty)
	if req <= 0:
		return base
	return max(base, req * 0.002)


def _spr_wip_topup_bump_qty(shortage_qty: float) -> float:
	"""Round WIP gaps up to stock precision so extra production RM still posts (e.g. 0.0004 -> 0.001 Kg)."""
	qty = flt(shortage_qty)
	if qty <= 0:
		return 0.0
	min_qty = 10 ** (-_spr_rm_stock_qty_precision())
	if qty < min_qty:
		qty = min_qty
	return _spr_round_rm_stock_qty(qty)


def _spr_wo_rm_transfer_remaining(wo_doc, item_code: str) -> float:
	"""Qty still to transfer to WIP for one RM item on the WO."""
	item_code = _cstr(item_code).strip()
	if not wo_doc or not item_code:
		return 0.0
	for req in getattr(wo_doc, "required_items", None) or []:
		if _cstr(getattr(req, "item_code", None)).strip() != item_code:
			continue
		required = flt(getattr(req, "required_qty", 0))
		still = required - flt(getattr(req, "transferred_qty", 0))
		if still <= _spr_rm_wip_shortage_tolerance(required):
			return 0.0
		min_qty = 10 ** (-_spr_rm_stock_qty_precision())
		if still <= min_qty + 1e-12:
			return 0.0
		return still
	return 0.0


def _spr_round_rm_stock_qty(qty: float) -> float:
	return flt(qty, _spr_rm_stock_qty_precision())


def _spr_floor_rm_stock_qty(qty: float) -> float:
	"""Floor to site RM precision so transfer qty never exceeds bin actual."""
	prec = _spr_rm_stock_qty_precision()
	factor = 10**prec
	return math.floor(flt(qty) * factor + 1e-12) / factor


def _spr_rm_available_qty(item_code: str, warehouse: str) -> float:
	"""Actual qty in warehouse Bin for MTFM transfers.

	Use actual_qty (not actual - reserved) because reserved_qty includes the
	same Work Order we are transferring for.  ERPNext validates the real SLE
	balance on submit, so capping to actual_qty is safe and prevents the
	'Maximum transferable quantity is 0.0 Kg' false-block.
	"""
	item_code = _cstr(item_code).strip()
	warehouse = _cstr(warehouse).strip()
	if not item_code or not warehouse:
		return 0.0
	actual = flt(
		frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty") or 0
	)
	if actual > 0:
		return actual
	try:
		from erpnext.stock.utils import get_stock_balance

		bal = flt(get_stock_balance(item_code, warehouse))
		if bal > 0:
			return bal
	except Exception:
		pass
	return max(actual, 0.0)


def _spr_batches_in_warehouse(item_code: str, warehouse: str) -> list[dict]:
	"""Batches with positive qty in one warehouse (classic SLE.batch_no + v15 bundles)."""
	item_code = _cstr(item_code).strip()
	warehouse = _cstr(warehouse).strip()
	if not item_code or not warehouse:
		return []
	acc: dict[str, float] = {}
	for r in frappe.db.sql(
		"""
		SELECT batch_no, SUM(actual_qty) AS qty
		FROM `tabStock Ledger Entry`
		WHERE IFNULL(is_cancelled, 0) = 0
		  AND IFNULL(item_code, '') = %s
		  AND IFNULL(warehouse, '') = %s
		  AND IFNULL(batch_no, '') != ''
		GROUP BY batch_no
		HAVING SUM(actual_qty) > 0
		""",
		(item_code, warehouse),
		as_dict=True,
	):
		bn = _cstr(r.get("batch_no"))
		q = flt(r.get("qty") or 0)
		if bn and q > 0:
			acc[bn] = acc.get(bn, 0.0) + q
	if frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
		try:
			sb_entry_dt = "Serial and Batch Entry"
			if frappe.db.exists("DocType", sb_entry_dt):
				sb_meta = frappe.get_meta(sb_entry_dt)
				batch_field = next(
					(fn for fn in ("batch_no", "batch", "batch_id") if sb_meta.has_field(fn)), ""
				)
				qty_field = next((fn for fn in ("qty", "quantity") if sb_meta.has_field(fn)), "")
				if batch_field and qty_field:
					for r in frappe.db.sql(
						f"""
						SELECT
							sbe.`{batch_field}` AS batch_no,
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
						  AND IFNULL(sle.warehouse, '') = %s
						  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
						  AND IFNULL(sbe.`{batch_field}`, '') != ''
						GROUP BY sbe.`{batch_field}`
						HAVING SUM(
							CASE
								WHEN IFNULL(sle.actual_qty, 0) < 0
									THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
								ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
							END
						) > 0
						""",
						(item_code, warehouse),
						as_dict=True,
					) or []:
						bn = _cstr(r.get("batch_no"))
						q = flt(r.get("qty") or 0)
						if bn and q > 0:
							acc[bn] = max(acc.get(bn, 0.0), q)
		except Exception:
			pass
	out = [{"batch_no": bn, "qty": flt(q)} for bn, q in acc.items() if bn and flt(q) > 0]
	out.sort(key=lambda x: flt(x.get("qty") or 0), reverse=True)
	return out


def _spr_enable_serial_batch_fields_on_se(se) -> None:
	"""ERPNext v15: only enable serial/batch fields on lines that carry a batch_no."""
	if not se:
		return
	se_meta = frappe.get_meta("Stock Entry")
	line_meta = frappe.get_meta("Stock Entry Detail")
	has_line_field = line_meta.has_field("use_serial_batch_fields")
	has_header_field = se_meta.has_field("use_serial_batch_fields")
	any_batched_line = False
	for d in se.items or []:
		if not d.item_code:
			continue
		has_batch_item = cint(frappe.db.get_value("Item", d.item_code, "has_batch_no") or 0)
		bn = _cstr(d.get("batch_no")).strip()
		use_batch = bool(has_batch_item and bn)
		if has_line_field:
			d.use_serial_batch_fields = 1 if use_batch else 0
		if use_batch:
			any_batched_line = True
	if has_header_field:
		se.use_serial_batch_fields = 1 if any_batched_line else 0


def _spr_expand_outbound_lines_by_batch(se) -> None:
	"""Split outbound STE lines across batches when one batch cannot cover the full qty."""
	if not se or not se.items:
		return
	new_lines: list = []
	for d in list(se.items or []):
		if not d.item_code or not d.get("s_warehouse"):
			new_lines.append(d)
			continue
		has_batch = cint(frappe.db.get_value("Item", d.item_code, "has_batch_no") or 0)
		need = flt(d.get("transfer_qty") or d.get("qty"))
		if not has_batch or need <= 0:
			new_lines.append(d)
			continue
		batches = _spr_batches_in_warehouse(d.item_code, d.s_warehouse)
		if not batches:
			d.batch_no = ""
			new_lines.append(d)
			continue
		total_batch_qty = sum(flt(br.get("qty") or 0) for br in batches)
		total_wh_qty = _spr_rm_available_qty(d.item_code, d.s_warehouse)
		if total_wh_qty + 1e-9 >= need and total_batch_qty + 1e-9 < need:
			d.batch_no = ""
			new_lines.append(d)
			continue
		remaining = need
		first = True
		for br in batches:
			if remaining <= 0:
				break
			bn = _cstr(br.get("batch_no"))
			bq = flt(br.get("qty") or 0)
			if not bn or bq <= 0:
				continue
			take = min(remaining, bq)
			take = _spr_floor_rm_stock_qty(take)
			if take <= 0:
				continue
			if first:
				d.batch_no = bn
				d.qty = take
				d.transfer_qty = take
				new_lines.append(d)
				first = False
			else:
				new_lines.append(
					{
						"item_code": d.item_code,
						"s_warehouse": d.s_warehouse,
						"t_warehouse": d.t_warehouse,
						"uom": d.uom,
						"stock_uom": d.stock_uom,
						"conversion_factor": flt(d.conversion_factor) or 1.0,
						"batch_no": bn,
						"qty": take,
						"transfer_qty": take,
						"work_order": d.get("work_order"),
						"expense_account": d.get("expense_account"),
						"cost_center": d.get("cost_center"),
					}
				)
			remaining -= take
		if first:
			d.batch_no = ""
			new_lines.append(d)
	se.items = []
	for row in new_lines:
		if isinstance(row, dict):
			se.append("items", row)
		else:
			se.items.append(row)


def _spr_batch_qty_from_sle_and_bundle(item_code: str, warehouse: str, batch_no: str) -> float:
	"""Warehouse qty for a batch: classic SLE.batch_no plus bundle-only SLEs.

	v15 Material Transfer for Manufacture often posts the outbound/inbound SLE with an
	empty batch_no and a Serial and Batch Bundle. Summing SLE.batch_no alone then still
	shows the pre-transfer qty at the source warehouse (FG) and duplicates it at WIP.
	"""
	classic = flt(
		frappe.db.sql(
			"""
			SELECT COALESCE(SUM(actual_qty), 0)
			FROM `tabStock Ledger Entry`
			WHERE item_code = %s AND warehouse = %s AND batch_no = %s
			  AND IFNULL(is_cancelled, 0) = 0
			""",
			(item_code, warehouse, batch_no),
		)[0][0]
		or 0
	)
	bundle = 0.0
	if frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle") and frappe.db.exists(
		"DocType", "Serial and Batch Entry"
	):
		try:
			sb_meta = frappe.get_meta("Serial and Batch Entry")
			batch_field = next(
				(fn for fn in ("batch_no", "batch", "batch_id") if sb_meta.has_field(fn)),
				"",
			)
			qty_field = next((fn for fn in ("qty", "quantity") if sb_meta.has_field(fn)), "")
			if batch_field and qty_field:
				bundle = flt(
					frappe.db.sql(
						f"""
						SELECT COALESCE(SUM(
							CASE
								WHEN IFNULL(sle.actual_qty, 0) < 0
									THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
								ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
							END
						), 0)
						FROM `tabStock Ledger Entry` sle
						INNER JOIN `tabSerial and Batch Entry` sbe
							ON sbe.parent = sle.serial_and_batch_bundle
						WHERE sle.item_code = %s
						  AND sle.warehouse = %s
						  AND IFNULL(sle.is_cancelled, 0) = 0
						  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
						  AND IFNULL(sle.batch_no, '') = ''
						  AND IFNULL(sbe.`{batch_field}`, '') = %s
						""",
						(item_code, warehouse, batch_no),
					)[0][0]
					or 0
				)
		except Exception:
			bundle = 0.0
	return classic + bundle


def _spr_batch_available_qty(item_code: str, warehouse: str, batch_no: str) -> float:
	"""Batch qty in this warehouse, including v15 serial/batch-bundle transfers."""
	item_code = _cstr(item_code).strip()
	warehouse = _cstr(warehouse).strip()
	batch_no = _cstr(batch_no).strip()
	if not item_code or not warehouse or not batch_no:
		return 0.0
	return _spr_batch_qty_from_sle_and_bundle(item_code, warehouse, batch_no)


def _spr_parse_max_transferable_kg(exc_msg: str) -> float:
	"""Parse ERPNext 'Maximum transferable quantity is X Kg' from submit validation."""
	msg = _cstr(exc_msg)
	m = re.search(
		r"Maximum transferable quantity is\s+([\d.]+)",
		msg,
		flags=re.IGNORECASE,
	)
	return flt(m.group(1)) if m else 0.0


def _spr_cap_qty_to_rm_available(
	qty: float, item_code: str, warehouse: str, batch_no: str | None = None
) -> float:
	"""Cap MTFM qty to RM/bin or batch stock — never round above available (7.554 vs 7.553997)."""
	qty = flt(qty)
	if qty <= 0:
		return 0.0
	batch_no = _cstr(batch_no).strip() if batch_no else ""
	if batch_no:
		avl = _spr_batch_available_qty(item_code, warehouse, batch_no)
	else:
		avl = _spr_rm_available_qty(item_code, warehouse)
	if avl <= 0:
		return 0.0
	capped = min(qty, avl)
	floored = _spr_floor_rm_stock_qty(capped)
	if floored > avl + 1e-9:
		floored = _spr_floor_rm_stock_qty(avl)
	if floored > avl + 1e-9:
		floored = avl
	return max(floored, 0.0)


def _spr_wo_rm_fully_transferred(wo_doc) -> bool:
	"""True when every WO required_item shows RM fully moved to WIP (within tolerance)."""
	if not wo_doc:
		return False
	for req in getattr(wo_doc, "required_items", None) or []:
		ic = _cstr(getattr(req, "item_code", None)).strip()
		if not ic:
			continue
		req_qty = flt(getattr(req, "required_qty", 0))
		still = _spr_wo_rm_transfer_remaining(wo_doc, ic)
		if still > _spr_rm_wip_shortage_tolerance(req_qty):
			return False
	return True


def _spr_find_rm_warehouse_with_stock(
	company: str,
	item_code: str,
	wip_wh: str,
	preferred_wh: str = "",
	need_qty: float = 0,
) -> tuple[str, float]:
	"""Pick a non-WIP warehouse with stock for item; prefer preferred_wh then company RM."""
	item_code = _cstr(item_code).strip()
	wip_wh = _cstr(wip_wh).strip()
	company = _cstr(company).strip()
	need_qty = flt(need_qty)
	if not item_code:
		return "", 0.0
	candidates: list[str] = []
	for wh in (_cstr(preferred_wh).strip(),):
		if wh and wh != wip_wh and wh not in candidates:
			candidates.append(wh)
	if company:
		co_rm = _spr_company_rm_warehouse(company, wip_wh)
		if co_rm and co_rm not in candidates:
			candidates.append(co_rm)
	if company:
		for row in frappe.db.sql(
			"""
			SELECT b.warehouse, b.actual_qty
			FROM `tabBin` b
			INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
			WHERE b.item_code = %s
			  AND w.company = %s
			  AND IFNULL(w.is_group, 0) = 0
			  AND IFNULL(b.actual_qty, 0) > 0.0001
			  AND b.warehouse != %s
			ORDER BY b.actual_qty DESC
			""",
			(item_code, company, wip_wh),
			as_dict=True,
		):
			wh = _cstr(row.get("warehouse")).strip()
			if wh and wh not in candidates:
				candidates.append(wh)
	best_wh, best_qty = "", 0.0
	for wh in candidates:
		avl = _spr_rm_available_qty(item_code, wh)
		if avl > best_qty:
			best_wh, best_qty = wh, avl
		if need_qty > 0 and avl + _spr_rm_wip_shortage_tolerance(need_qty) >= need_qty:
			return wh, avl
	return best_wh, best_qty


def _spr_finalize_mtfm_line_qty(sed, source_wh: str, requested_qty: float) -> float:
	"""Assign batch (if item is batched) then cap line qty to true transferable stock."""
	if not sed or not sed.item_code or not source_wh:
		return 0.0
	_spr_assign_batch_for_mtfm_line(sed, source_wh, flt(requested_qty))
	qty = _spr_cap_qty_to_rm_available(
		flt(requested_qty),
		sed.item_code,
		source_wh,
		sed.get("batch_no"),
	)
	if qty > 0:
		sed.qty = qty
		sed.transfer_qty = qty
	return qty


def _spr_prepare_mtfm_stock_entry_for_submit(se, skip_batch: bool = False) -> None:
	"""Batch split + serial/batch fields + cap lines before MTFM insert/submit."""
	if not se:
		return
	if skip_batch:
		line_meta = frappe.get_meta("Stock Entry Detail")
		se_meta = frappe.get_meta("Stock Entry")
		for d in se.items or []:
			if d.get("s_warehouse"):
				d.batch_no = ""
			if line_meta.has_field("use_serial_batch_fields"):
				d.use_serial_batch_fields = 0
		if se_meta.has_field("use_serial_batch_fields"):
			se.use_serial_batch_fields = 0
		_spr_cap_stock_entry_lines_to_max_transferable(se)
		return
	_spr_enable_serial_batch_fields_on_se(se)
	_spr_expand_outbound_lines_by_batch(se)
	_spr_enable_serial_batch_fields_on_se(se)
	_spr_cap_stock_entry_lines_to_max_transferable(se)


def _spr_cap_stock_entry_lines_to_max_transferable(se) -> None:
	"""Cap each outbound line on a Stock Entry to available source-warehouse / batch qty."""
	if not se:
		return
	for d in se.items or []:
		if not d.item_code or not d.get("s_warehouse"):
			continue
		_spr_finalize_mtfm_line_qty(
			d,
			d.s_warehouse,
			flt(d.get("transfer_qty") or d.get("qty")),
		)


class _SprWipTopupRetry(Exception):
	"""Signal manufacture chunk should rebuild after auto WIP top-up transfer."""


def _spr_resolve_expense_account(item_code: str, company: str, warehouse: str | None = None) -> str:
	"""Resolve expense_account for Stock Entry lines (perpetual inventory sites)."""
	item_code = _cstr(item_code).strip()
	company = _cstr(company).strip()
	if not item_code or not company:
		return ""
	acc = frappe.db.get_value(
		"Item Default",
		{"parent": item_code, "company": company},
		"expense_account",
	)
	if acc:
		return _cstr(acc)
	try:
		from erpnext.stock.get_item_details import get_item_details

		row = get_item_details(
			{
				"item_code": item_code,
				"company": company,
				"warehouse": warehouse or "",
				"qty": 1,
				"doctype": "Stock Entry",
			}
		)
		acc = (row or {}).get("expense_account")
		if acc:
			return _cstr(acc)
	except Exception:
		pass
	item_meta = frappe.get_meta("Item")
	if item_meta.has_field("expense_account"):
		acc = frappe.db.get_value("Item", item_code, "expense_account")
		if acc:
			return _cstr(acc)
	company_meta = frappe.get_meta("Company")
	for fld in ("stock_adjustment_account", "default_expense_account"):
		if company_meta.has_field(fld):
			acc = frappe.db.get_value("Company", company, fld)
			if acc:
				return _cstr(acc)
	return ""


def _spr_apply_stock_entry_item_accounts(se) -> None:
	"""Fill expense_account / cost_center on Stock Entry item lines before insert/submit."""
	company = _cstr(getattr(se, "company", None)).strip()
	if not company or not se:
		return
	line_meta = frappe.get_meta("Stock Entry Detail")
	has_expense = line_meta.has_field("expense_account")
	has_cc = line_meta.has_field("cost_center")
	if not has_expense and not has_cc:
		return
	default_cc = frappe.db.get_value("Company", company, "cost_center") if has_cc else None
	for d in se.items or []:
		if not d.item_code:
			continue
		wh = _cstr(d.get("s_warehouse") or d.get("t_warehouse") or getattr(se, "wip_warehouse", None))
		if has_expense and not _cstr(d.get("expense_account")):
			acc = _spr_resolve_expense_account(d.item_code, company, wh)
			if acc:
				d.expense_account = acc
		if has_cc and not _cstr(d.get("cost_center")) and default_cc:
			d.cost_center = default_cc


def _spr_apply_bag_rm_qty_from_bom(se, bom_no: str, fg_qty: float) -> None:
	"""Replace RM line qty on Stock Entry with BOM×FG using Meter→Kg divide (matches WO/PP)."""
	fg_qty = flt(fg_qty)
	if fg_qty <= 0 or not se:
		return
	rm_map, _multi = _bom_rm_stock_qty_map_for_fg(_cstr(bom_no), fg_qty)
	if not rm_map:
		return
	for d in se.items or []:
		if not d.item_code or d.get("t_warehouse"):
			continue
		needed = _spr_round_rm_stock_qty(rm_map.get(_cstr(d.item_code)))
		if needed <= 0:
			continue
		stock_uom = frappe.db.get_value("Item", d.item_code, "stock_uom") or d.stock_uom or "Kg"
		d.uom = stock_uom
		d.stock_uom = stock_uom
		d.conversion_factor = 1.0
		d.transfer_qty = needed
		d.qty = needed


def _batch_fields_from_spr_row(batch_meta, spr_row, is_bag_spr: bool = False) -> dict:
	"""Map Roll Production Result line to Batch fields (Net/Gross Weight Kgs, Length Mtrs, CBM)."""
	if not spr_row:
		return {}
	out = {}
	fn_n = _batch_field_net_weight_kgs(batch_meta)
	fn_g = _batch_field_gross_weight_kgs(batch_meta)
	fn_l = _batch_field_length_mtrs(batch_meta)
	if fn_n is not None and _spr_row_get(spr_row, "net_weight") is not None:
		out[fn_n] = flt(_spr_row_get(spr_row, "net_weight"))
	if fn_g is not None and _spr_row_get(spr_row, "gross_weight") is not None:
		out[fn_g] = flt(_spr_row_get(spr_row, "gross_weight"))
	ln = _spr_length_meters(spr_row)
	if fn_l is not None and ln is not None:
		out[fn_l] = flt(ln)
	# Explicit mapping required: SPR produced_length_mtrs -> Batch custom_meter.
	meter_from_spr = _spr_row_get(spr_row, "produced_length_mtrs")
	if batch_meta.has_field("custom_meter") and meter_from_spr not in (None, ""):
		out["custom_meter"] = flt(meter_from_spr)
	if batch_meta.has_field("custom_cbm") and _spr_row_get(spr_row, "custom_cbm") is not None:
		out["custom_cbm"] = flt(_spr_row_get(spr_row, "custom_cbm"))
	# Additive: mirror SPR Item bay → Batch.custom_bay (field already on Batch from WMS)
	if batch_meta.has_field("custom_bay"):
		bay = _cstr(_spr_row_get(spr_row, "custom_bay"))
		if bay:
			out["custom_bay"] = bay

	def _set_first_batch_field(candidates: tuple[str, ...], value, label_tokens: tuple[str, ...] = ()):
		if value in (None, ""):
			return
		for fn in candidates:
			if batch_meta.has_field(fn):
				out[fn] = value
				return
		if label_tokens:
			for df in (batch_meta.fields or []):
				lab = (df.label or "").lower()
				if all(t in lab for t in label_tokens):
					out[df.fieldname] = value
					return

	# Batch.custom_party_code_text ΓåÉ Roll Production line.party_code (explicit; fallback legacy line text)
	party_for_batch_party_field = _cstr(_spr_row_get(spr_row, "party_code")) or _cstr(
		_spr_row_get(spr_row, "custom_party_code_text")
	)
	_set_first_batch_field(("custom_party_code_text",), party_for_batch_party_field, ("party", "code"))

	order_code = (
		_cstr(_spr_row_get(spr_row, "custom_order_code"))
		or _cstr(_spr_row_get(spr_row, "order_code"))
		or _cstr(_spr_row_get(spr_row, "custom_party_code_text"))
		or _cstr(_spr_row_get(spr_row, "party_code"))
	)
	work_order = _cstr(_spr_row_get(spr_row, "work_order") or _spr_row_get(spr_row, "wo_id"))
	roll_numbers = _spr_row_get(spr_row, "roll_numbers")
	roll_no = _spr_row_get(spr_row, "roll_no") or roll_numbers

	_set_first_batch_field(
		("custom_order_code", "order_code", "party_code"),
		order_code,
		("order", "code"),
	)
	_set_first_batch_field(
		("work_order", "custom_work_order", "wo_no", "custom_wo_no"),
		work_order,
		("work", "order"),
	)
	_set_first_batch_field(
		("roll_no", "custom_roll_no", "roll_number", "custom_roll_number"),
		roll_no,
		("roll",),
	)
	_set_first_batch_field(
		("custom_roll_numbers", "roll_numbers", "custom_source_roll_numbers", "source_roll_numbers"),
		roll_numbers,
	)
	if is_bag_spr:
		pcs = flt(_spr_row_get(spr_row, "custom_achieved_bag_pcs"))
		if pcs > 0:
			_set_first_batch_field(("custom_produced_bagpcs",), pcs, ("produced", "bag"))
			if batch_meta.has_field("custom_produced_bagpcs"):
				ft = batch_meta.get_field("custom_produced_bagpcs").fieldtype
				out["custom_produced_bagpcs"] = _cstr(int(pcs)) if ft == "Data" else pcs
		bag_sz = _cstr(_spr_row_get(spr_row, "custom_bag_size"))
		if bag_sz:
			_set_first_batch_field(("custom_bag_size", "bag_size"), bag_sz, ("bag", "size"))
		for val, cands, tokens in (
			(_spr_row_get(spr_row, "quality"), ("custom_quality", "quality"), ("quality",)),
			(_spr_row_get(spr_row, "color"), ("custom_color", "color"), ("color",)),
			(_spr_row_get(spr_row, "gsm"), ("custom_gsm", "gsm"), ("gsm",)),
			(flt(_spr_row_get(spr_row, "width_inch") or 0), ("custom_width_inch", "width_inch"), ("width",)),
		):
			if val not in (None, "", 0):
				_set_first_batch_field(cands, val, tokens)
	return out


def _production_plan_custom_shaft_child_doctype() -> str | None:
	"""Child table Doctype behind Production Plan.custom_shaft_details (e.g. Shaft Plan Detail)."""
	try:
		pp_meta = frappe.get_meta("Production Plan")
	except Exception:
		return None
	for fname in ("custom_shaft_details", "custom_shaft_detail"):
		if not pp_meta.has_field(fname):
			continue
		f = pp_meta.get_field(fname)
		if f.fieldtype == "Table" and f.options:
			return f.options
	return None


def _looks_like_frappe_row_name(s: str) -> bool:
	"""Heuristic: autogenerated child row names are often long alphanumeric strings."""
	t = _cstr(s)
	if len(t) < 9:
		return False
	return t.isalnum()


def normalize_label_template_link(value: str) -> str:
	"""Map legacy Select / free-text label values to a Label Template link name."""
	v = _cstr(value).strip()
	if not v:
		return ""
	if not frappe.db.exists("DocType", "Label Template"):
		return v
	if frappe.db.exists("Label Template", v):
		return v
	lower = v.lower()
	for name in frappe.get_all("Label Template", pluck="name"):
		if _cstr(name).lower() == lower:
			return name
	lt_meta = frappe.get_meta("Label Template")
	for fn in ("label_name", "template_name", "label"):
		if not lt_meta.has_field(fn):
			continue
		for row in frappe.get_all("Label Template", fields=["name", fn]):
			if _cstr(row.get(fn)).lower() == lower:
				return row.name
	return v


_LABEL_TEMPLATE_FLAG_MAP = (
	("show_company", ("show_company", "company", "company_name", "header_company"), ("company name", "company")),
	("show_email", ("show_email", "email", "company_email"), ("company email", "email")),
	("show_customer", ("show_customer", "customer", "customer_name"), ("customer name", "customer")),
	("show_quality", ("show_quality", "quality"), ("quality",)),
	("show_order_code", ("show_order_code", "order_code", "party_code"), ("order code", "party code")),
	("show_gsm", ("show_gsm", "gsm"), ("gsm",)),
	("show_color", ("show_color", "color", "colour"), ("color", "colour")),
	("show_length", ("show_length", "length"), ("length",)),
	("show_width", ("show_width", "width"), ("width",)),
	("show_gw", ("show_gw", "cross_weight", "gross_weight", "show_cross_weight", "show_gross_weight"), ("cross weight", "gross weight")),
	("show_nw", ("show_nw", "net_weight", "show_net_weight"), ("net weight",)),
	("show_batch", ("show_batch", "batch_no", "batch", "show_batch_no"), ("batch no", "batch")),
	("show_barcode", ("show_barcode", "barcode"), ("barcode",)),
)

_LABEL_TEMPLATE_SIZE_FIELDS = (
	"label_size",
	"size",
	"sticker_size",
	"print_size",
	"label_dimension",
	"dimensions",
	"paper_size",
	"width_height",
)


def _label_template_parse_size(raw) -> dict:
	text = _cstr(raw)
	m = re.search(
		r"(\d+(?:\.\d+)?)\s*(?:in(?:ch(?:es)?)?|[\"”])?\s*[x×*]\s*(\d+(?:\.\d+)?)",
		text,
		re.I,
	)
	if m:
		return {
			"width_in": flt(m.group(1)) or 4,
			"height_in": flt(m.group(2)) or 4,
			"raw": text or "4x4",
		}
	return {"width_in": 4.0, "height_in": 4.0, "raw": text or "4x4"}


def _label_template_check_value(doc, meta, fieldnames, labels):
	"""Return 0/1 if a Check field matches, else None."""
	wanted_fn = {str(x).strip().lower() for x in (fieldnames or []) if x}
	wanted_lb = {str(x).strip().lower() for x in (labels or []) if x}
	for df in meta.fields or []:
		if _cstr(getattr(df, "fieldtype", "")).lower() != "check":
			continue
		fn = _cstr(getattr(df, "fieldname", "")).lower()
		fn_bare = fn[7:] if fn.startswith("custom_") else fn
		lb = _cstr(getattr(df, "label", "")).lower()
		if fn in wanted_fn or fn_bare in wanted_fn or lb in wanted_lb:
			return 1 if cint(doc.get(df.fieldname)) else 0
	return None


def build_label_template_print_spec(name=None) -> dict | None:
	"""Normalize Label Template checkboxes + size for sticker print."""
	key = normalize_label_template_link(name)
	if not key or not frappe.db.exists("DocType", "Label Template"):
		return None
	if not frappe.db.exists("Label Template", key):
		return None
	# db.get_value skips DocPerm — GSM operators often cannot open Label Template.
	doc = frappe.db.get_value("Label Template", key, "*", as_dict=True)
	if not doc:
		return None
	meta = frappe.get_meta("Label Template")
	size_raw = ""
	for fn in _LABEL_TEMPLATE_SIZE_FIELDS:
		if meta.has_field(fn):
			size_raw = _cstr(doc.get(fn))
			if size_raw:
				break
	if not size_raw:
		for df in meta.fields or []:
			lb = _cstr(getattr(df, "label", "")).lower()
			if "size" in lb or "dimension" in lb:
				size_raw = _cstr(doc.get(df.fieldname))
				if size_raw:
					break
	size = _label_template_parse_size(size_raw)
	compact = flt(size["height_in"]) <= 2.5
	fields = {
		"show_company": 0 if compact else 1,
		"show_email": 0 if compact else 1,
		"show_customer": 0,
		"show_quality": 0 if compact else 1,
		"show_order_code": 0 if compact else 1,
		"show_gsm": 1,
		"show_color": 1,
		"show_length": 1,
		"show_width": 1,
		"show_gw": 0 if compact else 1,
		"show_nw": 1,
		"show_batch": 1,
		"show_barcode": 1,
		"width_in": size["width_in"],
		"height_in": size["height_in"],
	}
	for flag, fieldnames, labels in _LABEL_TEMPLATE_FLAG_MAP:
		hit = _label_template_check_value(doc, meta, fieldnames, labels)
		if hit is not None:
			fields[flag] = hit
	display = key
	for fn in ("label_name", "template_name", "label"):
		if meta.has_field(fn):
			disp = _cstr(doc.get(fn))
			if disp:
				display = disp
				break
	return {
		"name": key,
		"display_name": display,
		"from_template": 1,
		"size_raw": size["raw"],
		"width_in": size["width_in"],
		"height_in": size["height_in"],
		"fields": fields,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_label_template_print_spec(name=None):
	"""GSM / SPR operators may lack Label Template DocPerm — ignore permissions."""
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Please log in to continue."), frappe.AuthenticationError)
	try:
		return build_label_template_print_spec(name) or {}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_label_template_print_spec")
		return {}


def resolve_label_from_pp_doc(pp_doc) -> str:
	"""Label / label type from Production Plan header or first shaft detail row (sites use different field names)."""
	if not pp_doc:
		return ""
	try:
		meta = frappe.get_meta("Production Plan")
		for fn in (
			"custom_label",
			"label",
			"label_type",
			"custom_label_type",
			"type_of_label",
			"custom_type_of_label",
			"custom_print_type",
			"print_type",
		):
			if meta.has_field(fn):
				v = _cstr(pp_doc.get(fn))
				if v:
					return normalize_label_template_link(v)
		for tbl in ("custom_shaft_details", "shaft_details"):
			if not meta.has_field(tbl):
				continue
			rows = pp_doc.get(tbl) or []
			if not rows:
				continue
			r0 = rows[0]
			for fn in ("custom_label", "label", "label_type", "type_of_label", "custom_label_type"):
				try:
					raw = r0.get(fn) if isinstance(r0, dict) else getattr(r0, fn, None)
				except Exception:
					raw = None
				v = _cstr(raw)
				if v:
					return normalize_label_template_link(v)
	except Exception:
		pass
	return ""


def resolve_label_from_planning_sheet_doc(sheet_doc) -> str:
	"""Fallback label when PP header has no label field populated."""
	if not sheet_doc:
		return ""
	try:
		meta = frappe.get_meta("Planning sheet")
		for fn in (
			"custom_label",
			"label",
			"label_type",
			"custom_label_type",
			"type_of_label",
			"custom_type_of_label",
		):
			if meta.has_field(fn):
				v = _cstr(sheet_doc.get(fn))
				if v:
					return normalize_label_template_link(v)
	except Exception:
		pass
	return ""


def _production_plan_total_planned_qty(production_plan: str) -> float:
	"""Resolve planned KG from Production Plan fields, then fall back to linked Work Orders sum."""
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return 0.0

	try:
		wo_qty = flt(
			frappe.db.sql(
				"""
				SELECT IFNULL(SUM(wo.qty), 0)
				FROM `tabWork Order` wo
				WHERE wo.production_plan = %(pp)s
				  AND wo.docstatus < 2
				""",
				{"pp": production_plan},
			)[0][0]
		)
		frappe.logger().info(f"[_production_plan_total_planned_qty] {production_plan}: WO qty sum = {wo_qty}")
		if wo_qty > 0:
			return wo_qty
	except Exception as e:
		frappe.logger().error(f"[_production_plan_total_planned_qty] Error fetching WO sum for {production_plan}: {e}")
		pass

	# Sum Production Plan Item rows (ERPNext / custom PPs)
	try:
		pp = frappe.get_doc("Production Plan", production_plan)
		pp_meta = frappe.get_meta("Production Plan")
		for tbl in ("po_items", "prod_order_items", "items", "production_plan_item", "custom_production_plan_items"):
			if not pp_meta.has_field(tbl):
				continue
			s = 0.0
			for row in pp.get(tbl) or []:
				if isinstance(row, dict):
					pq = row.get("planned_qty") or row.get("qty")
				else:
					pq = getattr(row, "planned_qty", None) or getattr(row, "qty", None)
				s += flt(pq)
			if s > 0:
				frappe.logger().info(f"[_production_plan_total_planned_qty] {production_plan}: sum {tbl} = {s}")
				return s
	except Exception as e:
		frappe.logger().error(f"[_production_plan_total_planned_qty] PP item sum error {production_plan}: {e}")

	# Final fallback to direct PP scalar fields
	try:
		pp = frappe.get_doc("Production Plan", production_plan)
		pp_meta = frappe.get_meta("Production Plan")
		for fn in (
			"custom_total_planned_qty",
			"total_planned_qty",
			"custom_total_weight_kgs",
			"total_weight_kgs",
			"planned_qty",
			"qty",
		):
			if pp_meta.has_field(fn):
				v = flt(pp.get(fn))
				if v > 0:
					return v
	except Exception:
		pass

	return 0.0


def _production_plan_total_planned_pcs(production_plan: str) -> float:
	"""Sum planned bag/sheet PCS from PP bundle rows (box-bag / bundle calc)."""
	pp_doc = _get_pp_doc(production_plan)
	if not pp_doc:
		return 0.0
	total = 0.0
	for src in _read_pp_bundle_calculation_rows(production_plan):
		tpb = flt(src.get("total_pcs_per_bundle") or 0)
		if tpb <= 0:
			n_boxes = flt(src.get("no_of_boxes") or 0)
			pcs = cint(src.get("pcs_per_packet") or 0)
			pkts = cint(src.get("pkts_per_bundle") or 0)
			if n_boxes > 0 and pcs > 0:
				tpb = flt(n_boxes * pcs)
			elif pkts > 0 and pcs > 0:
				tpb = flt(pkts * pcs)
		if tpb > 0:
			total += tpb
	if total > 0:
		return flt(total, 0)
	try:
		wo_qty = flt(
			frappe.db.sql(
				"""
				SELECT IFNULL(SUM(wo.qty), 0)
				FROM `tabWork Order` wo
				WHERE wo.production_plan = %(pp)s
				  AND wo.docstatus < 2
				""",
				{"pp": production_plan},
			)[0][0]
		)
		if wo_qty > 0:
			return flt(wo_qty, 0)
	except Exception:
		pass
	return 0.0


def _effective_weight_kg_for_produced_gsm(row) -> float:
	"""Prefer net weight; if not entered yet, use gross (same rule as desk JS spr_update_produced_gsm)."""
	nw = flt(getattr(row, "net_weight", None))
	if nw > 0:
		return nw
	return flt(getattr(row, "gross_weight", None))


def compute_produced_gsm(weight_kg, width_inch, length_m) -> float:
	"""Roll line GSM from actuals: (weight_kg * 10000) / (width_inch * length_m * 0.254)."""
	wgt = flt(weight_kg)
	w = flt(width_inch)
	ln = flt(length_m)
	den = w * ln * 0.254
	if den <= 0:
		return 0.0
	return round((wgt * 10000.0) / den, 2)


def compute_mix_roll_planned_qty_kg(gsm, width_inch, length_m) -> float:
	"""Mix-roll line planned qty (kg): gsm * width_inch * length_m * 0.0254 / 1000."""
	g, w, ln = flt(gsm), flt(width_inch), flt(length_m)
	if g <= 0 or w <= 0 or ln <= 0:
		return 0.0
	return round(g * w * ln * 0.0254 / 1000, 2)


def _spr_mix_roll_planned_length_m(row) -> float:
	"""Length for mix-roll planned qty: meter_roll / ordered length only (not produced)."""
	for key in (
		"meter_roll",
		"meter_roll_mtrs",
		"custom_meter_roll_mtrs",
		"ordered_length",
		"ordered_length_mtrs",
		"custom_ordered_length",
	):
		v = _spr_row_get(row, key)
		if v is not None and flt(v) > 0:
			return flt(v)
	return 0.0


def _work_order_names_for_pp_job(production_plan: str, m: dict, idx: int) -> str:
	"""Comma-separated WO names for this shaft row (same rules as _get_work_orders_for_spr_job)."""
	
	# Extract GSM if available in job row
	job_gsm = None
	try:
		if m.get("gsm"):
			job_gsm = int(flt(m.get("gsm")))
	except Exception:
		pass

	wos = _resolve_wos_for_pp_job_row(
		production_plan,
		ppi=m.get("production_plan_item"),
		job_id=m.get("job_id"),
		row_index=idx,
		combination=m.get("combination"),
		job_gsm=job_gsm,
	)
	return ", ".join(w["name"] for w in wos) if wos else ""


def _build_shaft_jobs_from_custom_shaft_details(production_plan: str) -> list[dict] | None:
	"""
	Load Available Jobs from Production Plan.custom_shaft_details (Table field on PP).
	Maps rows to Shaft Production Run Job; prefers human-readable Job column for job_id.
	"""
	child_dt = _production_plan_custom_shaft_child_doctype()
	if not child_dt or not frappe.db.exists("DocType", child_dt):
		return None
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return None
	pp_rows = frappe.db.sql(
		f"""
		SELECT * FROM `tab{child_dt}`
		WHERE parent = %(p)s
		ORDER BY idx ASC
		""",
		{"p": production_plan},
		as_dict=True,
	)
	if not pp_rows:
		return None
	job_meta = frappe.get_meta("Shaft Production Run Job")
	out: list[dict] = []
	for idx, r in enumerate(pp_rows):
		m: dict = {}
		# Shaft Plan Detail: Job is field s_no (Int); also accept job / job_no / job_id
		readable = None
		if r.get("s_no") is not None and _cstr(r.get("s_no")) != "":
			try:
				readable = str(int(flt(r["s_no"])))
			except (TypeError, ValueError):
				readable = _cstr(r.get("s_no"))
		if not readable:
			readable = (
				_cstr(r.get("job"))
				if r.get("job") is not None and _cstr(r.get("job")) != ""
				else None
			)
		if not readable:
			readable = r.get("job_no") or r.get("job_id")
		if not readable or _cstr(readable) == "":
			readable = str(idx + 1)
		m["job_id"] = _cstr(readable)

		ppi = r.get("production_plan_item") or r.get("against_production_plan_item")
		if not ppi and _looks_like_frappe_row_name(_cstr(r.get("name", ""))):
			ppi = r.get("name")
		if ppi and job_meta.has_field("production_plan_item"):
			m["production_plan_item"] = _cstr(ppi)

		comb = r.get("shaft_combination") or r.get("combination")
		if comb is not None and job_meta.has_field("combination"):
			m["combination"] = comb

		# Map Shaft Plan Detail fieldnames: combined_width, meter__roll, no_of_shaft, total_weight_kgs
		field_aliases = {
			"gsm": ("gsm", "custom_gsm"),
			"quality": ("quality", "custom_quality"),
			"notes": ("notes", "custom_notes"),
			"total_width": (
				"combined_width",
				"total_width",
				"total_width_inches",
				"custom_total_width",
			),
			"meter_roll_mtrs": (
				"meter__roll",
				"meter_roll_mtrs",
				"meter_per_roll",
				"custom_meter_roll_mtrs",
			),
			"no_of_shafts": ("no_of_shafts", "no_of_shaft", "custom_no_of_shafts"),
			"no_of_rolls": ("no_of_rolls", "roll_count_per_shaft", "custom_no_of_rolls"),
			"net_weight": ("net_weight", "net_weight_per_shaft", "custom_net_weight_per_shaft"),
			"total_weight": ("total_weight_kgs", "total_weight", "custom_total_weight"),
			"custom_total_achieved_weight": ("custom_total_achieved_weight",),
			"party_code": ("party_code", "order_code", "custom_party_code"),
			"work_orders": ("work_orders", "custom_work_orders"),
		}
		for target, aliases in field_aliases.items():
			if not job_meta.has_field(target):
				continue
			for a in aliases:
				if a in r and r[a] is not None and _cstr(r[a]) != "":
					val = r[a]
					if target == "net_weight" and not isinstance(val, str):
						val = str(val)
					m[target] = val
					break

		if m.get("gsm") is not None and m.get("gsm") != "":
			try:
				m["gsm"] = int(flt(str(m["gsm"]).strip().split()[0]))
			except Exception:
				pass

		skip_copy = {
			"name",
			"owner",
			"creation",
			"modified",
			"modified_by",
			"docstatus",
			"idx",
			"parent",
			"parentfield",
			"parenttype",
			"doctype",
		}
		skip_alias = {
			"job",
			"job_no",
			"job_id",
			"s_no",
			"name",
			"production_plan_item",
			"against_production_plan_item",
			"shaft_combination",
			"combination",
			"combined_width",
			"meter__roll",
			"no_of_shaft",
			"total_weight_kgs",
		}
		for fn, v in r.items():
			if fn in skip_copy or fn in skip_alias or v is None:
				continue
			if not job_meta.has_field(fn):
				continue
			if fn not in m:
				if fn == "net_weight" and not isinstance(v, str):
					m[fn] = str(v)
				else:
					m[fn] = v

		jn = m.get("job_id")
		job_gsm = None
		if m.get("gsm") is not None and m.get("gsm") != "":
			try:
				job_gsm = int(flt(str(m.get("gsm")).strip().split()[0]))
			except Exception:
				try:
					job_gsm = int(flt(m.get("gsm")))
				except Exception:
					pass
		wos_res = _resolve_wos_for_pp_job_row(
			production_plan,
			ppi=m.get("production_plan_item"),
			job_id=_cstr(jn) if jn else None,
			row_index=idx,
			combination=m.get("combination"),
			job_gsm=job_gsm,
		)
		if jn and (not m.get("total_weight")) and wos_res:
			tw = sum(flt(w.get("planned_qty")) for w in wos_res)
			if flt(tw) > 0:
				m["total_weight"] = flt(tw)
		if job_meta.has_field("work_orders"):
			m["work_orders"] = ", ".join(w["name"] for w in wos_res) if wos_res else ""
		_fill_party_code_from_resolved_wos(m, job_meta, wos_res)

		out.append(m)
	return out or None


def _ordered_production_plan_items(production_plan: str) -> list[str]:
	"""Distinct Work Order production_plan_item values in stable order (for ordinal WO fallback)."""
	rows = frappe.db.sql(
		"""
		SELECT wo.production_plan_item AS ppi, MIN(wo.creation) AS mc
		FROM `tabWork Order` wo
		WHERE wo.production_plan = %(pp)s
		  AND wo.docstatus < 2
		  AND IFNULL(wo.production_plan_item, '') != ''
		GROUP BY wo.production_plan_item
		ORDER BY mc ASC, ppi ASC
		""",
		{"pp": production_plan},
		as_dict=True,
	)
	return [_cstr(r.ppi) for r in rows]


def _spr_job_row_index(spr_doc, job_row) -> int | None:
	rows = list(_spr_job_rows(spr_doc))
	for i, r in enumerate(rows):
		if getattr(r, "name", None) and getattr(job_row, "name", None) and r.name == job_row.name:
			return i
	jid = _cstr(_spr_job_id(job_row))
	for i, r in enumerate(rows):
		if _cstr(_spr_job_id(r)) == jid:
			return i
	return None


def _get_all_work_orders_for_production_plan(pp_name: str) -> list:
	"""All Work Orders for a Production Plan (fallback when job id does not match production_plan_item)."""
	if not pp_name:
		return []
	return frappe.db.sql(
		"""
		SELECT wo.name, wo.production_item, wo.qty as planned_qty, wo.produced_qty, wo.status
		FROM `tabWork Order` wo
		WHERE wo.production_plan = %(pp)s
		  AND wo.docstatus != 2
		ORDER BY wo.creation ASC, wo.name ASC
		""",
		{"pp": pp_name},
		as_dict=True,
	)


def _parse_combination_widths_inches(combination) -> list[float]:
	"""Numeric widths per '+' segment, aligned with _count_combination_segments (e.g. 39\" + 24\" → [39, 24])."""
	if not combination:
		return []
	text = _cstr(combination)
	# Normalize fancy inch quotes so 7.8" / 7.8″ / 7.8' all parse the same.
	text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2033", '"').replace("′", "'")
	parts = [p.strip() for p in re.split(r"\+", text) if p.strip()]
	out: list[float] = []
	for part in parts:
		m = re.search(r"(\d+(?:\.\d+)?)", part.replace(",", ""))
		if m:
			out.append(flt(m.group(1)))
	return out


def _match_work_orders_to_combination_segments(
	pp_name: str,
	combination: str,
	ppi: str | None = None,
	job_gsm: int | None = None,
	job_id: str | None = None,
) -> list | None:
	"""
	For multi-width combinations (e.g. ``46\"+42\"+38\"`` or ``63\"+63\"``),
	match each segment to a Work Order using ``(GSM, width)`` from the WO's item code.

	PPI-scoped queries often return only **one** WO per ``production_plan_item``, while a
	single shaft job row lists **several different widths** — each width is usually its own
	WO on the plan. When ``distinct_widths >= 2`` and we have fewer WOs than combination
	segments, we load **all** work orders on the Production Plan so each width can match.

	Same-width repeats (e.g. ``42+42+42``) deduplicate to one WO.
	"""
	comb = _cstr(combination)
	if not comb:
		return None
	segs = _count_combination_segments(comb)
	if segs < 2:
		return None
	widths = _parse_combination_widths_inches(comb)
	if len(widths) < segs:
		return None
	widths = widths[:segs]
	all_wos: list = []
	if ppi:
		all_wos = list(get_work_orders_for_job(pp_name, _cstr(ppi)) or [])
	if not all_wos and job_id:
		all_wos = list(get_work_orders_for_job(pp_name, _cstr(job_id)) or [])

	# If we have fewer WOs than segments, expand to PP-wide candidates for width matching.
	if len(all_wos) < segs:
		all_wos = list(_get_all_work_orders_for_production_plan(pp_name) or [])
	elif not all_wos:
		all_wos = list(_get_all_work_orders_for_production_plan(pp_name) or [])

	if not all_wos:
		return None
	
	# Γ£à Build (GSM, WIDTH) ΓåÆ WO map using ITEM CODE parsing
	gsm_width_to_wo_map = {}
	for wo in all_wos:
		wo_name = _cstr(wo.get("name"))
		try:
			production_item = wo.get("production_item")
			if not production_item:
				continue
			# Parse item code to extract GSM and WIDTH
			parsed_gsm, parsed_width = parse_item_code(_cstr(production_item))
			if parsed_gsm > 0 and parsed_width > 0:
				key = (parsed_gsm, parsed_width)
				gsm_width_to_wo_map[key] = wo
				frappe.logger().info(f"[COMBO] WO {wo_name} = ({parsed_gsm}, {parsed_width}\") (PPI {ppi})")
		except Exception as e:
			frappe.logger().warning(f"[COMBO ERROR] Could not parse WO {wo_name}: {str(e)}")
	
	if not job_gsm:
		frappe.logger().warning(f"[COMBO] No job_gsm provided, cannot match")
		return None
	
	out: list = []
	tol = 1.25
	for target_w in widths:
		# Match by (GSM, WIDTH) tuple
		key = (job_gsm, target_w)
		if key in gsm_width_to_wo_map:
			best = gsm_width_to_wo_map[key]
			out.append(best)
			frappe.logger().info(f"[COMBO] Segment ({job_gsm}, {target_w}\") ΓåÆ WO {best.get('name')}")
		else:
			# Fallback: find by width closest to target
			best = None
			best_d = 999.0
			for (w_gsm, w_width), wo in gsm_width_to_wo_map.items():
				if abs(w_width - target_w) < best_d:
					best_d = abs(w_width - target_w)
					best = wo
			
			if best and best_d <= tol:
				out.append(best)
				frappe.logger().warning(f"[COMBO] No exact match for ({job_gsm}, {target_w}\"), fallback")
			else:
				frappe.logger().warning(f"[COMBO] No WO found for ({job_gsm}, {target_w}\")")
				return None
	
	# Γ£à DEDUPLICATE: For "63+63", return only UNIQUE WOs
	seen = set()
	unique_out = []
	for wo in out:
		wo_name = _cstr(wo.get("name"))
		if wo_name not in seen:
			seen.add(wo_name)
			unique_out.append(wo)
			frappe.logger().info(f"[COMBO DEDUP] Keep WO {wo_name}")
		else:
			frappe.logger().info(f"[COMBO DEDUP] Skip duplicate WO {wo_name}")
	
	return unique_out if len(unique_out) > 0 else None


def _wo_has_gsm(wo: dict, target_gsm: int) -> bool:
	"""Check if a WO's item has matching GSM."""
	try:
		prod_item = wo.get("production_item")
		if prod_item:
			gsm, width = parse_item_code(_cstr(prod_item))
			return gsm == target_gsm
	except Exception:
		pass
	return False


def _pick_one_wo_by_gsm(wos: list, job_gsm: int | None) -> list:
	"""Narrow a WO list to one row; prefer GSM match on production_item when several WOs share a PPI."""
	if not wos:
		return []
	if len(wos) == 1:
		return wos
	if job_gsm and job_gsm > 0:
		for wo in wos:
			try:
				prod_item = wo.get("production_item")
				if prod_item:
					gsm, _width = parse_item_code(_cstr(prod_item))
					if gsm == job_gsm:
						return [wo]
			except Exception:
				pass
	return [wos[0]]


def _pick_wos_by_gsm_and_width(wos: list, job_gsm: int | None, combination: str | None = None) -> list:
	"""Pick WO(s) by width (+ GSM when available). Supports single-width and multi-width combinations."""
	if not wos:
		return []
	widths = _parse_combination_widths_inches(combination) if combination else []
	if widths:
		tol = 0.75
		out: list = []
		seen = set()
		for tw in widths:
			best = None
			# 1) Prefer exact GSM + width
			if job_gsm and job_gsm > 0:
				for wo in wos:
					try:
						prod_item = wo.get("production_item")
						if not prod_item:
							continue
						gsm, ww = parse_item_code(_cstr(prod_item))
						if gsm == int(job_gsm) and abs(flt(ww) - flt(tw)) <= tol:
							best = wo
							break
					except Exception:
						pass
			# 2) Width-only fallback (if no GSM or no exact GSM+width)
			if best is None:
				for wo in wos:
					try:
						prod_item = wo.get("production_item")
						if not prod_item:
							continue
						_gsm, ww = parse_item_code(_cstr(prod_item))
						if abs(flt(ww) - flt(tw)) <= tol:
							best = wo
							break
					except Exception:
						pass
			if best:
				nm = _cstr(best.get("name"))
				if nm and nm not in seen:
					seen.add(nm)
					out.append(best)
		if out:
			return out
	return _pick_one_wo_by_gsm(wos, job_gsm)


def _resolve_wos_for_pp_job_row(
	pp_name: str,
	*,
	ppi: str | None = None,
	job_id: str | None = None,
	row_index: int | None = None,
	combination: str | None = None,
	job_gsm: int | None = None,
) -> list:
	"""Resolve Work Orders for one Available Jobs row.

	Multi-segment combinations (e.g. ``46\"+42\"+38\"``) return one WO per segment via
	``(GSM, width)`` on the WO item code when possible. Same-width segments share one WO.

	If ``production_plan_item`` does not match any Work Order link, we fall back to ``job_id``,
	then row order, then any WO on the plan (so the grid does not stay blank when PPI is stale).
	"""
	comb = _cstr(combination).strip() if combination else ""
	widths = _parse_combination_widths_inches(comb) if comb else []
	# Width-aware resolver for both single-width and multi-width rows.
	# This prevents picking same WO for different widths (e.g. 120" vs 63"+63").
	if comb and widths and job_gsm:
		candidates = _get_all_work_orders_for_production_plan(pp_name)
		if candidates:
			picked = _pick_wos_by_gsm_and_width(candidates, job_gsm, comb)
			if picked:
				return picked

	if comb and _count_combination_segments(comb) >= 2:
		jg = job_gsm
		if not jg:
			for ref in (ppi, job_id):
				if not ref:
					continue
				wl = get_work_orders_for_job(pp_name, _cstr(ref))
				if wl and wl[0].get("production_item"):
					try:
						g, _w = parse_item_code(_cstr(wl[0].get("production_item")))
						if g > 0:
							jg = int(g)
							break
					except Exception:
						pass
		if jg:
			matched = _match_work_orders_to_combination_segments(
				pp_name,
				comb,
				ppi=_cstr(ppi) if ppi else None,
				job_gsm=int(jg),
				job_id=_cstr(job_id) if job_id else None,
			)
			if matched:
				return matched

	if ppi:
		wos = get_work_orders_for_job(pp_name, _cstr(ppi))
		if wos:
			return _pick_wos_by_gsm_and_width(wos, job_gsm, comb)

	if job_id:
		wos = get_work_orders_for_job(pp_name, _cstr(job_id))
		if wos:
			return _pick_wos_by_gsm_and_width(wos, job_gsm, comb)

	if row_index is not None:
		ord_ppi = _ordered_production_plan_items(pp_name)
		if ord_ppi and 0 <= row_index < len(ord_ppi):
			wos = get_work_orders_for_job(pp_name, ord_ppi[row_index])
			if wos:
				return _pick_wos_by_gsm_and_width(wos, job_gsm, comb)

	all_wos = _get_all_work_orders_for_production_plan(pp_name)
	return _pick_wos_by_gsm_and_width(all_wos, job_gsm, comb) if all_wos else []


def _get_work_orders_for_spr_job(pp_name: str, spr_doc, job_row):
	"""Resolve Work Orders for a job row.

	Priority:
	1) Explicit Available Jobs.work_orders (manual jobs must stay pinned to these WOs)
	2) production_plan_item / job_id / row index heuristics for auto jobs
	"""
	# Manual jobs can coexist with auto jobs on same PP/WO dimensions; never re-resolve away
	# from explicitly selected WO(s) in the row.
	explicit_wos = _cstr(getattr(job_row, "work_orders", None) or "")
	if explicit_wos:
		names: list[str] = []
		seen = set()
		for raw in explicit_wos.replace("\n", ",").split(","):
			wo_name = _cstr(raw).strip()
			if not wo_name or wo_name in seen:
				continue
			if frappe.db.exists("Work Order", wo_name):
				names.append(wo_name)
				seen.add(wo_name)
		if names:
			wo_rows = (
				frappe.get_all(
					"Work Order",
					filters={"name": ["in", names], "docstatus": ["!=", 2]},
					fields=["name", "production_item", "qty", "produced_qty", "status"],
				)
				or []
			)
			by_name = {_cstr(r.get("name")): r for r in wo_rows}
			ordered = []
			for nm in names:
				row = by_name.get(nm)
				if not row:
					continue
				ordered.append(
					{
						"name": row.get("name"),
						"production_item": row.get("production_item"),
						"planned_qty": flt(row.get("qty")),
						"produced_qty": flt(row.get("produced_qty")),
						"status": row.get("status"),
					}
				)
			if ordered:
				return ordered
	meta = frappe.get_meta("Shaft Production Run Job")
	ppi = None
	if meta.has_field("production_plan_item"):
		ppi = getattr(job_row, "production_plan_item", None)
	jid = _spr_job_id(job_row)
	idx = _spr_job_row_index(spr_doc, job_row)
	comb = getattr(job_row, "combination", None) if meta.has_field("combination") else None
	# Γ£à Extract GSM from job_row for (GSM, WIDTH) matching
	job_gsm = None
	if meta.has_field("gsm"):
		try:
			gsm_val = getattr(job_row, "gsm", None)
			if gsm_val:
				job_gsm = int(flt(gsm_val))
		except Exception:
			pass
	return _resolve_wos_for_pp_job_row(pp_name, ppi=ppi, job_id=jid, row_index=idx, combination=comb, job_gsm=job_gsm)


def _build_shaft_jobs_from_pp_details(production_plan: str) -> list[dict] | None:
	"""One row per Production Plan Shaft Detail line, field-aligned with Shaft Production Run Job."""
	if not production_plan or not frappe.db.exists("DocType", "Production Plan Shaft Detail"):
		return None
	if not frappe.db.exists("Production Plan", production_plan):
		return None
	pp_rows = frappe.db.sql(
		"""
		SELECT * FROM `tabProduction Plan Shaft Detail`
		WHERE parent = %(p)s
		ORDER BY idx ASC
		""",
		{"p": production_plan},
		as_dict=True,
	)
	if not pp_rows:
		return None
	job_meta = frappe.get_meta("Shaft Production Run Job")
	out: list[dict] = []
	for idx, r in enumerate(pp_rows):
		m: dict = {}
		jn = r.get("job_no") or r.get("job_id")
		if not jn:
			continue
		m["job_id"] = _cstr(jn)
		if job_meta.has_field("production_plan_item"):
			# Use explicit PP-item link only when row already carries a real child-row name.
			ppi = _cstr(r.get("production_plan_item") or r.get("against_production_plan_item"))
			if ppi and frappe.db.exists("Production Plan Item", ppi):
				m["production_plan_item"] = ppi
		if r.get("shaft_combination") is not None and job_meta.has_field("combination"):
			m["combination"] = r.get("shaft_combination")
		for fn in (
			"gsm",
			"quality",
			"combination",
			"notes",
			"total_width",
			"meter_roll_mtrs",
			"net_weight",
			"total_weight",
			"custom_total_achieved_weight",
			"no_of_shafts",
			"party_code",
			"work_orders",
		):
			if fn in r and r[fn] is not None and job_meta.has_field(fn):
				m[fn] = r[fn]
		job_gsm = None
		if m.get("gsm"):
			try:
				job_gsm = int(flt(m.get("gsm")))
			except Exception:
				pass
		wos_res = _resolve_wos_for_pp_job_row(
			production_plan,
			ppi=m.get("production_plan_item"),
			job_id=_cstr(jn),
			row_index=idx,
			combination=m.get("combination"),
			job_gsm=job_gsm,
		)
		if (not m.get("total_weight")) and jn and wos_res:
			tw = sum(flt(w.get("planned_qty")) for w in wos_res)
			if flt(tw) > 0:
				m["total_weight"] = flt(tw)
		if job_meta.has_field("work_orders") and not m.get("work_orders"):
			m["work_orders"] = ", ".join(w["name"] for w in wos_res) if wos_res else ""
		_fill_party_code_from_resolved_wos(m, job_meta, wos_res)
		out.append(m)
	return out or None


def _spr_net_weight_tolerance_percent() -> float:
	"""Allowed deviation of roll net (or gross) vs planned_qty (%). Set `spr_net_weight_tolerance_percent` in site_config."""
	pc = frappe.conf.get("spr_net_weight_tolerance_percent")
	if pc is not None:
		return max(flt(pc), 0.0)
	return 5.0


def _spr_effective_roll_weight_kg_for_tolerance(row) -> float:
	nw = flt(_spr_row_get(row, "net_weight"))
	if nw > 0:
		return nw
	return flt(_spr_row_get(row, "gross_weight"))


def _spr_collect_roll_planned_tolerance_violations(doc) -> list[tuple]:
	spi = frappe.get_meta("Shaft Production Run Item")
	if not spi.has_field("planned_qty"):
		return []
	tol = _spr_net_weight_tolerance_percent()
	if tol <= 0:
		return []
	out: list[tuple] = []
	for row in doc.items or []:
		pq = flt(_spr_row_get(row, "planned_qty"))
		if pq <= 0:
			continue
		act = _spr_effective_roll_weight_kg_for_tolerance(row)
		if act <= 0:
			continue
		dev_pct = abs(act - pq) / pq * 100.0
		if dev_pct > tol + 1e-9:
			jb = _cstr(_spr_row_get(row, "job"))
			rn = _spr_row_get(row, "roll_no")
			out.append((jb, rn, pq, act, dev_pct))
	return out


def _spr_unique_text_values(values: list) -> list[str]:
	seen_upper = set()
	out = []
	for v in values:
		s = _cstr(v).strip()
		if not s:
			continue
		key = s.upper()
		if key in seen_upper:
			continue
		seen_upper.add(key)
		out.append(key)
	return out


def _spr_unique_gsm_display_values(gsms: list) -> list[str]:
	seen = set()
	ordered = []
	for g in gsms:
		val = flt(g)
		if val <= 0:
			continue
		key = round(val, 2)
		if key in seen:
			continue
		seen.add(key)
		if abs(key - int(key)) < 0.001:
			disp = str(int(key))
		else:
			disp = f"{key:.2f}".rstrip("0").rstrip(".")
		ordered.append((key, disp))
	ordered.sort(key=lambda x: x[0])
	return [disp for _, disp in ordered]


def _spr_format_gsm_summary(gsms: list) -> str:
	parts = _spr_unique_gsm_display_values(gsms)
	if not parts:
		return ""
	return ", ".join(parts) + " gsm"


def _spr_collect_gsm_from_row(row) -> float:
	for key in ("gsm", "produced_gsm", "custom_fabric_gsm", "custom_lam_gsm", "custom_bopp_gsm"):
		g = flt(_spr_row_get(row, key) or 0)
		if g > 0:
			return g
	return 0.0


def compute_spr_attribute_summaries(spr_doc) -> dict:
	"""Build list-view Color / Quality / GSM text from shaft jobs and roll lines."""
	colors, qualities, gsms = [], [], []

	for row in spr_doc.get("shaft_jobs") or []:
		q = _cstr(_spr_row_get(row, "quality")).strip()
		if q:
			qualities.append(q)
		g = flt(_spr_row_get(row, "gsm") or 0)
		if g > 0:
			gsms.append(g)

	for row in spr_doc.get("items") or []:
		c = _cstr(_spr_row_get(row, "color")).strip()
		q = _cstr(_spr_row_get(row, "quality")).strip()
		g = _spr_collect_gsm_from_row(row)
		if not c or not q or g <= 0:
			ic = _cstr(_spr_row_get(row, "item_code")).strip()
			if ic:
				specs = _spr_resolve_roll_line_specs_from_item_code(
					ic, _cstr(_spr_row_get(row, "item_name"))
				)
				if not c:
					c = _cstr(specs.get("color")).strip()
				if not q:
					q = _cstr(specs.get("quality")).strip()
				if g <= 0:
					g = flt(specs.get("gsm") or 0)
		if c:
			colors.append(c)
		if q:
			qualities.append(q)
		if g > 0:
			gsms.append(g)

	return {
		"custom_color_summary": ", ".join(_spr_unique_text_values(colors)),
		"custom_quality_summary": ", ".join(_spr_unique_text_values(qualities)),
		"custom_gsm_summary": _spr_format_gsm_summary(gsms),
	}


def sync_spr_attribute_summaries_to_doc(spr_doc) -> None:
	if not spr_doc.meta.has_field("custom_color_summary"):
		return
	for fn, val in compute_spr_attribute_summaries(spr_doc).items():
		if spr_doc.meta.has_field(fn):
			spr_doc.set(fn, val)


class ShaftProductionRun(Document):
	def before_save(self):
		self._spr_prune_recycled_when_waste_removed()
		self._spr_backfill_zero_patty_rows()

	def _spr_backfill_zero_patty_rows(self) -> None:
		"""Server owns Running Patty values — refill rows posted with zeros.

		Desk Client Scripts can send width / meter / wastage as 0 when the browser
		cannot resolve job meter. Refill from the same compute the GSM wastage
		dialog uses so the SPR never stores an empty wastage row.
		"""
		if cint(self.docstatus) not in (0, 1):
			return
		field = _spr_patty_wastage_fieldname()
		if not field:
			return
		rows = list(self.get(field) or [])
		if not rows:
			return

		def _row_is_complete(row) -> bool:
			rd = _spr_patty_row_dict(row)
			if _spr_patty_row_wastage_kg(rd) <= 0:
				return False
			if flt(rd.get("width_inch") or rd.get("width") or 0) <= 0:
				return False
			meter = flt(
				rd.get("meter_per_roll")
				or rd.get("meter__roll")
				or rd.get("meter_roll")
				or rd.get("meter")
				or 0
			)
			return meter > 0

		pending = [row for row in rows if not _row_is_complete(row)]
		if not pending:
			return

		try:
			computed = _spr_compute_patty_wastage_by_job(self, include_unproduced_jobs=True) or {}
		except Exception:
			return
		if not computed:
			return

		live = _spr_patty_live_field_map()
		flag_field = live.get("recycle_to_next") or "recycle_to_next"
		skip_keys = {"name", "parent", "parenttype", "parentfield", "doctype", "idx", flag_field}
		computed_values = list(computed.values())
		for row in pending:
			jid = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
			logical = None
			if jid:
				for cj, cval in computed.items():
					if _spr_job_keys_match(_cstr(cj), jid):
						logical = cval
						break
			if logical is None and len(computed_values) == 1 and len(rows) == 1:
				logical = computed_values[0]
			if not logical:
				continue
			flag = cint(
				getattr(row, flag_field, None)
				or getattr(row, "recycle_to_next", None)
				or getattr(row, "custom_recycle_to_next", None)
				or 0
			)
			values = _spr_write_patty_child_row(_spr_apply_patty_recycle_net(dict(logical), flag))
			for key, val in (values or {}).items():
				if key in skip_keys:
					continue
				try:
					row.set(key, val)
				except Exception:
					setattr(row, key, val)

	def _spr_prune_recycled_when_waste_removed(self):
		"""When roll-waste / patty-waste child rows are deleted on desk, drop linked recycled rows.

		Do not drop recycled rows created in the same save (GSM Consume Selected moves
		waste onto Recycled Wastage Details and removes the waste source together).
		"""
		if getattr(self.flags, "gsm_recycle_consume", False):
			return
		if self.is_new() or not self.name:
			return
		try:
			old = self.get_doc_before_save()
		except Exception:
			return
		if not old:
			return

		recycled_field = "custom_recycled_wastage_details"
		if not frappe.get_meta("Shaft Production Run").has_field(recycled_field):
			return
		if not (getattr(old, recycled_field, None) or getattr(self, recycled_field, None)):
			return

		recycled_meta = frappe.get_meta("Recycled Wastage Detail Row")
		link_fields = [
			f
			for f in ("source_roll_waste_row", "roll_waste_row", "spr_item_name")
			if recycled_meta.has_field(f)
		]

		def _removed_rows(fieldname: str):
			old_names = {r.name for r in (getattr(old, fieldname, None) or [])}
			new_names = {r.name for r in (getattr(self, fieldname, None) or [])}
			removed = old_names - new_names
			if not removed:
				return [], set()
			old_by_name = {r.name: r for r in (getattr(old, fieldname, None) or [])}
			batches: set[str] = set()
			for rn in removed:
				row = old_by_name.get(rn)
				if not row:
					continue
				b = _cstr(getattr(row, "batch_no", "") or getattr(row, "source_roll", "")).strip()
				if b:
					batches.add(b)
			return removed, batches

		removed_roll_waste, roll_batches = _removed_rows("custom_roll_waste")
		removed_patty, patty_batches = _removed_rows("custom_running_patty_wastage")
		if not removed_roll_waste and not removed_patty:
			return

		old_recycled_names = {
			_cstr(getattr(r, "name", "") or "")
			for r in (getattr(old, recycled_field, None) or [])
			if _cstr(getattr(r, "name", "") or "")
		}

		drop_batches = roll_batches | patty_batches
		keep = []
		for row in getattr(self, recycled_field, None) or []:
			row_name = _cstr(getattr(row, "name", "") or "")
			if not row_name or row_name not in old_recycled_names:
				keep.append(row)
				continue
			drop = False
			for lf in link_fields:
				val = _cstr(getattr(row, lf, "") or "").strip()
				if val and val in removed_roll_waste:
					drop = True
					break
			if not drop and drop_batches:
				row_batch = _cstr(
					getattr(row, "batch_no", "") or getattr(row, "source_roll", "") or ""
				).strip()
				if row_batch and row_batch in drop_batches:
					drop = True
			if not drop:
				keep.append(row)

		if len(keep) != len(getattr(self, recycled_field, None) or []):
			self.set(recycled_field, keep)

	def before_validate(self):
		self.sync_company_from_source()
		self.normalize_custom_unit()

	def _spr_is_submit_action(self) -> bool:
		return getattr(self, "_action", None) == "submit"

	def _spr_rows_already_have_produced_gsm(self) -> bool:
		if not frappe.get_meta("Shaft Production Run Item").has_field("produced_gsm"):
			return True
		for row in self.items or []:
			if flt(getattr(row, "produced_gsm", 0)) > 0:
				continue
			if _effective_weight_kg_for_produced_gsm(row) > 0:
				return False
		return True

	def _spr_all_planned_rows_already_manufactured(self, planned_wo_posts: list) -> bool:
		for plan in planned_wo_posts or []:
			wo_doc = plan.get("wo_doc")
			rows = plan.get("rows") or []
			if not wo_doc or not rows:
				continue
			if self._spr_rows_needing_manufacture(wo_doc, rows, extra_se_names=None):
				return False
		return bool(planned_wo_posts)

	def validate(self):
		self.sync_company_from_source()
		self.normalize_custom_unit()
		submitting = self._spr_is_submit_action()
		incremental = cint(getattr(self.flags, "_spr_incremental_roll_save", 0))
		item_count = len(self.items or [])
		self._validate_no_duplicate_roll_batches()
		if self.name:
			self._validate_batch_numbers_not_on_other_sprs()

		# One-roll Create Entry save: skip full-grid validate (server already assigned batch).
		if incremental and item_count > 15 and not submitting:
			self.calculate_produced_gsm(missing_only=True)
			self._spr_recalc_total_produced_weight_header()
			return

		# Large draft saves: avoid reprocessing every roll line on each Save click.
		large_draft = item_count > 50 and not submitting and not incremental
		if large_draft:
			self.sync_shaft_job_work_orders_from_plan()
			self._spr_round_item_net_weights()
			self.calculate_produced_gsm(missing_only=True)
			self.recalculate_job_achieved_weights()
			self.recalculate_job_achieved_meters()
			self.generate_batch_numbers()
			self._spr_recalc_total_produced_weight_header()
			self._spr_recalc_bag_pcs_headers()
			return

		self.sync_shaft_job_work_orders_from_plan()
		self._spr_round_item_net_weights()
		self._spr_stamp_bag_sizes_on_roll_lines()
		self._spr_stamp_sheet_sizes_on_roll_lines()
		self._spr_recalc_mix_roll_planned_qty()
		if submitting:
			self.flags._spr_force_roll_summaries = True
		if submitting:
			self.calculate_produced_gsm(missing_only=False)
		elif incremental or (submitting and self._spr_rows_already_have_produced_gsm()) or (
			not submitting and item_count > 15
		):
			self.calculate_produced_gsm(missing_only=True)
		else:
			self.calculate_produced_gsm(missing_only=False)
		self.recalculate_job_achieved_weights()
		self.recalculate_job_achieved_meters()
		self.generate_batch_numbers()
		if cint(getattr(self, "custom_is_sheet_cutting", 0)) or cint(getattr(self, "custom_is_box_bag", 0)):
			self._spr_stamp_bag_sizes_on_bundle_rows()
			sync_bundle_total_produced_sheets_for_doc(self)
			sync_bundle_total_produced_bag_pcs_for_doc(self)
			sync_bundle_total_achieved_weight_for_doc(self)
			sync_bundle_consumed_meter_header(self)
		self._spr_recalc_total_produced_weight_header()
		self._spr_recalc_bag_pcs_headers()
		if item_count <= 15 or cint(getattr(self.flags, "_spr_force_roll_summaries", 0)):
			self.sync_roll_attribute_summaries()
		if submitting:
			self._validate_production_submit_readiness()

	def submit(self):
		"""Full SPR submit as Administrator so user roles cannot block Manufacture / Patty stock."""
		with _spr_as_administrator():
			self.flags.ignore_permissions = True
			return super().submit()

	def _spr_sync_patty_on_gsm_roll_save(self, submitting: bool = False, incremental: bool = True) -> None:
		"""Disabled — Running Patty / Recycle are owned by SPR Client+Server scripts.

		GSM only fetches the saved SPR tables (no separate server wastage calculate).
		"""
		return


	def _validate_production_submit_readiness(self):
		"""Hard gates before manufacture posting — batch numbers, WO mapping, no cross-SPR batch reuse."""
		self._validate_no_duplicate_roll_batches()
		self._validate_produced_rows_have_batch_numbers()
		self._validate_batch_numbers_not_on_other_sprs()
		if not spr_doc_is_mix_roll(self):
			self._validate_no_pending_wo_width_rows()

	def _validate_produced_rows_have_batch_numbers(self):
		"""Every produced roll line on a batch-managed FG item must have batch_no before submit."""
		missing: list[str] = []
		for row in self.items or []:
			if flt(self._row_fg_qty(row)) <= 0:
				continue
			ic = _cstr(getattr(row, "item_code", None)).strip()
			if ic and not _spr_item_has_batch_no(ic):
				continue
			bn = _cstr(getattr(row, "batch_no", None)).strip()
			if bn:
				continue
			roll = getattr(row, "roll_no", None)
			idx = cint(getattr(row, "idx", 0) or 0)
			missing.append(_("{0} (row {1})").format(roll if roll not in (None, "") else _("no roll"), idx))
		if missing:
			frappe.throw(
				_(
					"Produced roll line(s) missing batch number: {0}. "
					"Save the document or use Create Entry to assign batches before submit."
				).format(", ".join(missing[:12])),
				title=_("Missing batch numbers"),
			)

	def _validate_batch_numbers_not_on_other_sprs(self):
		"""A roll batch (full string e.g. JS-0206261/1) must not exist on another SPR."""
		if not self.name:
			return
		batches = list(
			{
				_cstr(getattr(r, "batch_no", None)).strip()
				for r in (self.items or [])
				if _cstr(getattr(r, "batch_no", None)).strip()
			}
		)
		if not batches:
			return
		conflicts = frappe.db.sql(
			"""
			SELECT spi.batch_no, spi.parent, spi.idx
			FROM `tabShaft Production Run Item` spi
			WHERE spi.batch_no IN %(batches)s
			  AND spi.parent != %(spr)s
			ORDER BY spi.batch_no ASC, spi.parent ASC
			LIMIT 8
			""",
			{"batches": tuple(batches), "spr": self.name},
			as_dict=True,
		)
		if not conflicts:
			return
		lines = []
		seen_pairs: set[tuple[str, str]] = set()
		for c in conflicts:
			bn = _cstr(c.batch_no)
			parent = _cstr(c.parent)
			pair = (bn, parent)
			if pair in seen_pairs:
				continue
			seen_pairs.add(pair)
			spr_link = get_link_to_form("Shaft Production Run", parent)
			lines.append(
				_("Batch <b>{0}</b> is already on {1} (row {2})").format(
					bn, spr_link, cint(c.idx)
				)
			)
		frappe.throw(
			_(
				"Each roll batch must be unique across all Shaft Production Runs.<br><br>{0}"
				"<br><br>Open the linked SPR above, fix or remove that batch line, then save/submit this SPR again."
			).format("<br>".join(lines)),
			title=_("Batch already used on another SPR"),
		)

	def _validate_no_duplicate_roll_batches(self):
		"""Reject duplicate batch_no on the same SPR — prevents qty mismatch on submit."""
		seen: dict[str, int] = {}
		for row in self.items or []:
			bn = _cstr(getattr(row, "batch_no", None) or row.get("batch_no")).strip()
			if not bn:
				continue
			idx = cint(getattr(row, "idx", 0) or row.get("idx") or 0)
			if bn in seen:
				frappe.throw(
					_(
						"Duplicate roll batch <b>{0}</b> on rows {1} and {2}. "
						"Delete the duplicate line, refresh the page, then save again."
					).format(bn, seen[bn], idx),
					title=_("Duplicate batch not allowed"),
				)
			seen[bn] = idx

	def sync_roll_attribute_summaries(self):
		sync_spr_attribute_summaries_to_doc(self)

	def sync_company_from_source(self):
		"""Show the manufacturing company on SPR from PP first, then linked WO."""
		if not self.meta.has_field("company"):
			return
		company = ""
		pp = _cstr(self.get("production_plan"))
		if pp and frappe.db.exists("Production Plan", pp):
			company = _cstr(frappe.db.get_value("Production Plan", pp, "company"))
		if not company:
			for table_name in ("items", "shaft_jobs"):
				for row in self.get(table_name) or []:
					wo_name = _cstr(_spr_row_get(row, "work_order") or _spr_row_get(row, "wo_id"))
					if not wo_name:
						wo_name = _cstr(getattr(row, "work_orders", None) or "").split(",")[0].strip()
					if wo_name and frappe.db.exists("Work Order", wo_name):
						company = _cstr(frappe.db.get_value("Work Order", wo_name, "company"))
						if company:
							break
				if company:
					break
		if company:
			self.company = company

	def normalize_custom_unit(self):
		unit_value = _cstr(self.get("custom_unit"))
		if not unit_value:
			return
		resolved = _spr_unit_value_for_current_field(unit_value)
		if resolved and not _units_equivalent(unit_value, resolved):
			try:
				df = self.meta.get_field("custom_unit")
				options = [_cstr(opt) for opt in _cstr(getattr(df, "options", "")).splitlines() if _cstr(opt)]
				if df and df.fieldtype == "Select" and resolved not in options:
					df.options = (_cstr(df.options) + "\n" + resolved).strip()
			except Exception:
				pass
			self.custom_unit = resolved

	def on_update(self):
		try:
			frappe.publish_realtime("shaft_production_run_updated", {"name": self.name})
		except Exception:
			pass

	def _spr_stamp_bag_sizes_on_roll_lines(self):
		"""Fill custom_bag_size from FG item code when missing on bag SPR roll lines."""
		if not spr_doc_is_bag_spr(self):
			return
		spi_meta = frappe.get_meta("Shaft Production Run Item")
		if not spi_meta.has_field("custom_bag_size"):
			return
		for row in self.items or []:
			if _cstr(getattr(row, "custom_bag_size", None)).strip():
				continue
			ic = _cstr(getattr(row, "item_code", None)).strip()
			if not ic or not _is_bag_bundle_fg_code(ic):
				continue
			sz = _spr_bag_size_from_item_code(ic)
			if sz:
				row.custom_bag_size = sz

	def _spr_stamp_sheet_sizes_on_roll_lines(self):
		"""Fill custom_sheet_size from FG item code when missing on sheet-cutting SPR roll lines."""
		if spr_doc_is_bag_spr(self):
			return
		if not cint(getattr(self, "custom_is_sheet_cutting", 0)):
			return
		spi_meta = frappe.get_meta("Shaft Production Run Item")
		if not spi_meta.has_field("custom_sheet_size"):
			return
		for row in self.items or []:
			if _cstr(getattr(row, "custom_sheet_size", None)).strip():
				continue
			ic = _cstr(getattr(row, "item_code", None)).strip()
			if not ic:
				continue
			sz = _spr_sheet_size_from_item_code(ic)
			if not sz:
				specs = _spr_resolve_roll_line_specs_from_item_code(ic)
				sz = _cstr(specs.get("sheet_size") or "").strip()
			if sz:
				row.custom_sheet_size = sz

	def _spr_stamp_bag_sizes_on_bundle_rows(self):
		"""Fill bundle_calculation.bag_size from FG item code when missing on bag SPR."""
		if not cint(getattr(self, "custom_is_box_bag", 0)):
			return
		for row in self.bundle_calculation or []:
			if _cstr(getattr(row, "bag_size", None) or getattr(row, "sheet_cutting_size", None)).strip():
				continue
			ic = _cstr(getattr(row, "item_code", None)).strip()
			if not ic:
				continue
			sz = _bundle_row_bag_size(row, ic, for_bag_fg=True)
			if sz:
				row.bag_size = sz
				if hasattr(row, "sheet_cutting_size"):
					row.sheet_cutting_size = sz

	def _spr_recalc_bag_pcs_headers(self):
		"""Bag SPR header: planned PCS from PP, achieved PCS = sum of bundle Total Produced Bag PCS."""
		if not cint(getattr(self, "custom_is_box_bag", 0)):
			return
		meta = frappe.get_meta("Shaft Production Run")
		pp = _cstr(self.get("production_plan"))
		if meta.has_field("custom_total_planned_pcs"):
			planned = 0.0
			if pp:
				planned = _production_plan_total_planned_pcs(pp)
			if planned <= 0 and getattr(self, "bundle_calculation", None):
				planned = sum(flt(getattr(br, "total_pcs_per_bundle", 0) or 0) for br in (self.bundle_calculation or []))
			self.custom_total_planned_pcs = flt(planned, 0)
		if meta.has_field("custom_total_achieved_pcs") and getattr(self, "bundle_calculation", None):
			self.custom_total_achieved_pcs = flt(
				sum(flt(getattr(br, "total_produced_bag_pcs", 0) or 0) for br in (self.bundle_calculation or [])),
				0,
			)

	def _spr_round_item_net_weights(self):
		"""Keep roll net weight at 2 decimal kg (matches child DocType precision and manual totals)."""
		item_meta = frappe.get_meta("Shaft Production Run Item")
		if not item_meta.has_field("net_weight"):
			return
		for row in self.items or []:
			raw = getattr(row, "net_weight", None)
			if raw in (None, ""):
				continue
			cur = flt(raw)
			rnd = flt(cur, 2)
			if abs(cur - rnd) > 1e-9:
				row.net_weight = rnd

	def _spr_recalc_total_produced_weight_header(self):
		"""Header totals: bag = sum bundle bag PCS; sheet cutting = bundle achieved kg; else roll net_weight."""
		meta = frappe.get_meta("Shaft Production Run")
		if not meta.has_field("total_produced_weight"):
			return
		if cint(getattr(self, "custom_is_box_bag", 0)) and getattr(self, "bundle_calculation", None):
			total = sum(flt(getattr(br, "total_produced_bag_pcs", 0) or 0) for br in (self.bundle_calculation or []))
			self.total_produced_weight = flt(total, 0)
		elif cint(getattr(self, "custom_is_sheet_cutting", 0)) and getattr(self, "bundle_calculation", None):
			total = sum(flt(getattr(br, "total_achieved_weight", None), 2) for br in (self.bundle_calculation or []))
			self.total_produced_weight = flt(total, 2)
		else:
			total = sum(flt(getattr(r, "net_weight", None), 2) for r in (self.items or []))
			self.total_produced_weight = flt(total, 2)
		if meta.has_field("custom_total_produced_weight"):
			self.custom_total_produced_weight = flt(self.total_produced_weight, 2 if not cint(getattr(self, "custom_is_box_bag", 0)) else 0)
		if meta.has_field("custom_no_of_rolls_created"):
			real_rolls = sum(1 for r in (self.items or []) if _spr_is_real_roll_item_row(r))
			if real_rolls > 0:
				self.custom_no_of_rolls_created = real_rolls

	def _spr_needs_job_work_order_resync(self) -> bool:
		"""True when PP-driven WO list on jobs should be recomputed (saves DB work on routine saves)."""
		if self.is_new():
			return True
		try:
			if self.has_value_changed("production_plan"):
				return True
		except Exception:
			pass
		meta = frappe.get_meta("Shaft Production Run Job")
		if not meta.has_field("work_orders"):
			return False
		for row in self.shaft_jobs or []:
			if cint(getattr(row, "is_manual", 0)):
				continue
			if not (getattr(row, "work_orders", None) or "").strip():
				return True
		return False

	def before_submit(self):
		self.flags._spr_force_roll_summaries = True
		self.sync_roll_attribute_summaries()
		# Persist wastage + core before submit so GSM path matches desk Client Scripts.
		# Zero-qty rows (shafts set / recycle checked but qty=0) are auto-repaired.
		try:
			persist_spr_patty_and_core(
				self, only_if_empty=False, save_if_draft=False, refresh_zero_rows=True
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR persist patty/core before_submit:{self.name}")
		self._validate_production_submit_readiness()
		if spr_doc_is_mix_roll(self):
			self.create_mix_roll_material_receipts()
			return
		if (
			not spr_doc_is_lamination(self)
			and not cint(getattr(self, "custom_is_box_bag", 0))
			and not cint(getattr(self, "custom_is_sheet_cutting", 0))
		):
			self._validate_roll_weight_tolerance()
		# Create/submit Manufacture entries before final submit so shortage handling can block
		# submission and still persist a draft transfer link. Run as Administrator so user
		# Stock Entry / Serial and Batch Bundle / warehouse roles do not block SPR submit.
		self.flags._spr_allow_manufacture_posting = True
		try:
			with _spr_as_administrator():
				self.create_manufacturing_stock_entries()
		finally:
			self.flags._spr_allow_manufacture_posting = False

	def _fg_rows_missing_work_order(self) -> list[dict]:
		"""Produced rows that still do not have WO mapping (causes partial submit)."""
		out = []
		for row in self.items or []:
			qty = flt(row.get("net_weight") or row.get("gross_weight") or 0)
			if qty <= 0:
				continue
			wo = _cstr(row.get("work_order") or row.get("wo_id"))
			if wo:
				continue
			out.append(
				{
					"roll_no": row.get("roll_no"),
					"item_code": _cstr(row.get("item_code")),
					"width_inch": flt(row.get("width_inch") or 0),
					"qty": qty,
				}
			)
		return out

	def _spr_backfill_missing_roll_work_orders(self) -> None:
		"""Fill blank roll-line Work Orders from Available Jobs / item width before submit."""
		spi_meta = frappe.get_meta("Shaft Production Run Item")
		jobs = list(self.shaft_jobs or [])
		for row in self.items or []:
			if flt(self._row_fg_qty(row)) <= 0:
				continue
			if _cstr(row.get("work_order") or row.get("wo_id")).strip():
				continue
			width = _spr_roll_effective_width_inch(row)
			job_id = _cstr(row.get("job") or row.get("job_id")).strip()
			sj = None
			if job_id:
				for j in jobs:
					if _spr_job_id(j) == job_id or _cstr(getattr(j, "job_id", None)).strip() == job_id:
						sj = j
						break
			wo = _spr_bundle_wo_for_width(self, sj, width) if sj else None
			if not wo and sj:
				comb = getattr(sj, "combination", None) or ""
				total = sum(_parse_combination_widths_inches(comb)) or flt(getattr(sj, "total_width", 0) or 0)
				if width > 0 and total > 0 and abs(width - total) <= 0.75:
					raw_wos = [
						p.strip()
						for p in _cstr(getattr(sj, "work_orders", None) or "").replace("\n", ",").split(",")
						if p.strip()
					]
					if len(raw_wos) == 1:
						wo = {"name": raw_wos[0]}
			if not wo and width > 0:
				ic = _cstr(row.get("item_code")).strip()
				if ic:
					_g, w_item = parse_item_code(ic)
					if w_item:
						width = flt(w_item)
				for j in jobs:
					found = _spr_bundle_wo_for_width(self, j, width)
					if found:
						wo = found
						break
			won = _cstr((wo or {}).get("name")).strip()
			if not won:
				continue
			if spi_meta.has_field("work_order"):
				row.work_order = won
			if spi_meta.has_field("wo_id"):
				row.wo_id = won

	def _validate_no_pending_wo_width_rows(self):
		"""Block submit with a clear list when produced widths are pending WO mapping."""
		self._spr_backfill_missing_roll_work_orders()
		missing = self._fg_rows_missing_work_order()
		if not missing:
			return
		by_width = defaultdict(list)
		for r in missing:
			w = flt(r.get("width_inch") or 0)
			key = f'{w:.1f}"' if w > 0 else "Unknown width"
			by_width[key].append(r)
		lines = []
		for w in sorted(by_width.keys()):
			rows = by_width[w]
			rolls = [str(x.get("roll_no")) for x in rows if x.get("roll_no") not in (None, "")]
			roll_txt = f" | rolls: {', '.join(rolls[:8])}" if rolls else ""
			lines.append(_("{0}: {1} row(s){2}").format(w, len(rows), roll_txt))
		frappe.throw(
			_(
				"Pending WO mapping found for produced widths. Fix Work Order on these roll lines before submit:\n\n{0}"
			).format("\n".join(lines)),
			title=_("Pending WO widths"),
		)

	def sync_shaft_job_work_orders_from_plan(self):
		"""Fill Available Jobs.work_orders from Production Plan (comma-separated; multi-width combos get one WO per width)."""
		meta = frappe.get_meta("Shaft Production Run Job")
		if not meta.has_field("work_orders"):
			return
		pp = self.get("production_plan")
		if not pp:
			return
		if not self._spr_needs_job_work_order_resync():
			return
		for row in self.shaft_jobs or []:
			if cint(getattr(row, "is_manual", 0)):
				continue
			idx = _spr_job_row_index(self, row)
			if idx is None:
				idx = 0
			# Γ£à Extract GSM from row for (GSM, WIDTH) matching
			job_gsm = None
			if meta.has_field("gsm"):
				try:
					gsm_val = getattr(row, "gsm", None)
					if gsm_val:
						job_gsm = int(flt(gsm_val))
				except Exception:
					pass
			m = {
				"job_id": _spr_job_id(row),
				"production_plan_item": getattr(row, "production_plan_item", None),
				"combination": getattr(row, "combination", None),
			}
			wos = _resolve_wos_for_pp_job_row(
				pp,
				ppi=m.get("production_plan_item"),
				job_id=_cstr(m.get("job_id")),
				row_index=idx,
				combination=m.get("combination"),
				job_gsm=job_gsm,
			)
			if wos:
				row.work_orders = ", ".join(w["name"] for w in wos)
				if meta.has_field("party_code"):
					pc_existing = getattr(row, "party_code", None)
					if pc_existing is None or not str(pc_existing).strip():
						try:
							wo_doc = frappe.get_doc("Work Order", wos[0]["name"])
							pc = get_order_code(wo_doc)
							if pc:
								row.party_code = pc
						except Exception:
							pass

	def sync_roll_line_net_weights_from_planned(self):
		"""No longer used on save: net weight must come from operators or site scripts, not planned qty."""
		pass

	def _validate_roll_weight_tolerance(self):
		meta = frappe.get_meta("Shaft Production Run")
		if not meta.has_field("tolerance_override_approved") or not meta.has_field("tolerance_override_reason"):
			return
		violations = _spr_collect_roll_planned_tolerance_violations(self)
		if not violations:
			if cint(self.get("tolerance_override_approved")):
				self.tolerance_override_approved = 0
				self.tolerance_override_reason = ""
			return
		reason = (self.get("tolerance_override_reason") or "").strip()
		if cint(self.get("tolerance_override_approved")) and reason:
			return
		tol = _spr_net_weight_tolerance_percent()
		parts = []
		for jb, rn, pq, act, dp in violations[:8]:
			parts.append(
				_("job {0} roll {1}: planned {2} vs {3} kg ({4:.2f}%)").format(
					jb or "—", rn if rn is not None else "—", flt(pq, 3), flt(act, 3), dp
				)
			)
		detail = "; ".join(parts)
		frappe.throw(
			_(
				"Net/gross weight differs from planned qty by more than {0}%. {1} "
				"Use Submit from desk to open the approval dialog, or set Tolerance override with a reason."
			).format(tol, detail),
			title=_("Tolerance approval required"),
		)

	def recalculate_job_achieved_weights(self):
		"""Per job: sum net_weight on roll lines (kg only — never ordered/planned meters)."""
		meta = frappe.get_meta("Shaft Production Run Job")
		if not meta.has_field("custom_total_achieved_weight"):
			return
		sums: dict[str, float] = {}
		for it in self.items or []:
			jid = _cstr(getattr(it, "job", None))
			if not jid:
				continue
			sums[jid] = sums.get(jid, 0.0) + flt(it.net_weight, 2)
		for row in self.shaft_jobs or []:
			jid = _cstr(_spr_job_id(row))
			row.custom_total_achieved_weight = flt(sums.get(jid, 0.0), 2)

	def recalculate_job_achieved_meters(self):
		"""Per job + SPR header: sum produced_length_mtrs only (no meter_roll / ordered length)."""
		if cint(getattr(self, "custom_is_sheet_cutting", 0)) or cint(getattr(self, "custom_is_box_bag", 0)):
			return
		meta_job = frappe.get_meta("Shaft Production Run Job")
		meta_spr = frappe.get_meta("Shaft Production Run")
		has_job_m = meta_job.has_field("custom_total_achieved_meter")
		has_hdr_m = meta_spr.has_field("custom_total_achieved_meter")
		if not has_job_m and not has_hdr_m:
			return
		per_job: dict[str, float] = {}
		total_all = 0.0
		for it in self.items or []:
			m = _spr_produced_length_meters(it)
			total_all += m
			jid = _cstr(getattr(it, "job", None))
			if jid and has_job_m:
				per_job[jid] = per_job.get(jid, 0.0) + m
		if has_job_m:
			for row in self.shaft_jobs or []:
				jid = _cstr(_spr_job_id(row))
				row.custom_total_achieved_meter = flt(per_job.get(jid, 0.0), 2)
		if has_hdr_m:
			self.custom_total_achieved_meter = flt(total_all, 2)

	def _spr_recalc_mix_roll_planned_qty(self):
		"""Mix-roll line planned qty from gsm × width × meter/roll (not color-chart split)."""
		if not spr_doc_is_mix_roll(self):
			return
		for row in self.items or []:
			ic = _cstr(getattr(row, "item_code", None))
			gsm_val = flt(getattr(row, "gsm", None) or 0)
			if gsm_val <= 0 and ic:
				parsed_gsm, _pw = parse_item_code(ic)
				gsm_val = flt(parsed_gsm)
			w_in = flt(getattr(row, "width_inch", None) or 0)
			if w_in <= 0 and ic:
				_pg, parsed_w = parse_item_code(ic)
				w_in = flt(parsed_w)
			length_m = _spr_mix_roll_planned_length_m(row)
			pq = compute_mix_roll_planned_qty_kg(gsm_val, w_in, length_m)
			if pq > 0:
				row.planned_qty = pq

	def calculate_produced_gsm(self, missing_only: bool = False):
		"""Set produced_gsm on each roll line from effective weight (net, else gross), width, length (m)."""
		meta = frappe.get_meta("Shaft Production Run Item")
		if not meta.has_field("produced_gsm"):
			return
		unit_lam = _cstr(getattr(self, "custom_unit", None)).strip() == LAMINATION_UNIT
		lam = spr_doc_is_lamination(self) or unit_lam
		is_mix = spr_doc_is_mix_roll(self)
		is_bb = cint(getattr(self, "custom_is_box_bag", 0))
		for row in self.items or []:
			if missing_only and flt(getattr(row, "produced_gsm", 0)) > 0:
				continue
			if lam or is_mix:
				ln = 0.0
				for key in ("produced_length_mtrs", "custom_produced_length_mtrs"):
					v = _spr_row_get(row, key)
					if v is not None and flt(v) > 0:
						ln = flt(v)
						break
				if is_mix and ln <= 0:
					row.produced_gsm = 0
					continue
			else:
				ln_m = _spr_length_meters(row)
				if ln_m is None or flt(ln_m) <= 0:
					ln = flt(getattr(row, "meter_roll", None))
				else:
					ln = flt(ln_m)
			wgt = _effective_weight_kg_for_produced_gsm(row)
			w_in = flt(getattr(row, "width_inch", None))
			if is_bb and w_in <= 0:
				ic = _cstr(getattr(row, "item_code", None))
				if ic:
					w_in = flt(_spr_resolve_roll_line_specs_from_item_code(ic).get("width_inch") or 0)
			row.produced_gsm = compute_produced_gsm(wgt, w_in, flt(ln))

	def on_submit(self):
		self.sync_batch_custom_fields()
		self.update_work_order_statuses()
		try:
			from production_entry.production_planning.scheduler_api import sync_spr_planning_table_links

			sync_spr_planning_table_links(self.name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR on_submit planning link:{self.name}")

	def on_cancel(self):
		self.cancel_manufacturing_stock_entries()

	def on_trash(self):
		"""Remove stale row links so deleted SPR is not shown as Continue Entry on Production Table."""
		try:
			if frappe.db.exists("DocType", "Planning Table") and frappe.db.has_column("Planning Table", "spr_name"):
				# spr_name is stored as CSV of SPR ids; remove this id from any row that references it.
				rows = frappe.get_all(
					"Planning Table",
					filters={"spr_name": ["like", f"%{self.name}%"]},
					fields=["name", "spr_name"],
					limit_page_length=0,
				) or []
				for r in rows:
					raw = str(r.get("spr_name") or "").strip()
					if not raw:
						continue
					parts = [p.strip() for p in raw.replace(";", ",").split(",") if p and p.strip()]
					filtered = [p for p in parts if p != self.name]
					new_val = ", ".join(filtered)
					if new_val != raw:
						frappe.db.set_value("Planning Table", r["name"], "spr_name", new_val, update_modified=False)
				# Back-compat: rows that stored a single id only.
				frappe.db.sql(
					"""
					UPDATE `tabPlanning Table`
					SET spr_name = ''
					WHERE IFNULL(spr_name, '') = %s
					""",
					(self.name,),
				)
				frappe.db.commit()
		except Exception:
			frappe.log_error(frappe.get_traceback(), "SPR on_trash cleanup: Planning Table spr_name")

		# Also remove this SPR from Production Plan link list to avoid stale PP-level references elsewhere.
		try:
			if not frappe.db.has_column("Production Plan", "custom_shaft_production_run_id"):
				return
			rows = frappe.db.sql(
				"""
				SELECT name, custom_shaft_production_run_id
				FROM `tabProduction Plan`
				WHERE IFNULL(custom_shaft_production_run_id, '') != ''
				""",
				as_dict=True,
			)
			for r in rows or []:
				raw = str(r.get("custom_shaft_production_run_id") or "")
				parts = [p.strip() for p in raw.split(",") if p and p.strip()]
				filtered = [p for p in parts if p != self.name]
				if filtered != parts:
					frappe.db.set_value("Production Plan", r["name"], "custom_shaft_production_run_id", ", ".join(filtered))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "SPR on_trash cleanup: Production Plan links")

	def _batch_prefix_parts(self):
		"""Return (company_identifier, unit_number_2digit) for the batch format — one unique pair per workstation."""
		u_raw = (self.get("custom_unit") or "").strip()
		if not u_raw:
			frappe.throw(
				_("Set Unit on this Shaft Production Run before roll batch numbers can be assigned."),
				title=_("Unit required"),
			)
		parts = spr_batch_prefix_for_unit(u_raw)
		if not parts:
			frappe.throw(
				_("No batch number format is configured for unit «{0}». Choose a supported workstation or contact admin.").format(
					u_raw
				),
				title=_("Unsupported unit"),
			)
		return parts

	def _unit_digit(self) -> str:
		"""Legacy single-character unit code — kept for backward compatibility."""
		_, unit_num = self._batch_prefix_parts()
		return str(int(unit_num))  # '01' → '1', '10' → '10'

	def generate_batch_numbers(self):
		"""Assign batch_no on each roll line when draft is saved.

		New format: ``{ID}-{UU}{MM}{YY}{S}/{N}``
		  ID = company identifier (JS / TS / JV / PY)
		  UU = 2-digit unit number
		  MM = month, YY = year, S = shift series, N = roll number
		Example: JS-01052601/1
		``tabBatch`` rows are created/updated on **submit** via ``sync_batch_custom_fields``, not on every save.
		"""
		if not self.run_date or not self.get("custom_unit") or not self.shift:
			return
		rows = [r for r in (self.items or []) if r.item_code]
		if not rows:
			return
		# Heavy series queries only when at least one line still needs batch_no (routine saves with full grid skip this).
		if not any(not getattr(r, "batch_no", None) for r in rows):
			return
		parts = spr_batch_prefix_for_unit(self.get("custom_unit"))
		if not parts:
			# Unsupported / unassigned unit — skip roll batch assignment on save (submit may still require Shift + unit).
			return
		rd = getdate(self.run_date)
		comp_id, unit_num = parts
		root_5 = f"{comp_id}-{unit_num}{rd.month:02d}{rd.year % 100:02d}"
		series_prefix = self._resolve_series_prefix(root_5)
		next_roll = self._next_roll_starting(series_prefix)
		item_meta = frappe.get_meta("Shaft Production Run Item")
		used_batches = _spr_used_batch_numbers_on_spr(self.name, self.items)
		for row in rows:
			bn = _cstr(getattr(row, "batch_no", None)).strip()
			if bn:
				used_batches.add(bn)
				continue
			bn, next_roll = _spr_next_available_batch_no(
				series_prefix, next_roll, used_batches, exclude_spr=self.name
			)
			row.batch_no = bn
			used_batches.add(bn)
			if item_meta.has_field("roll_no"):
				rf = item_meta.get_field("roll_no")
				row.roll_no = int(next_roll) if rf and rf.fieldtype == "Int" else str(next_roll)
			if item_meta.has_field("custom_shift"):
				row.custom_shift = batch_shift_value(self.shift)
			next_roll += 1

	def _spr_series_prefix_from_batches(self, root_5: str, batch_nos) -> str | None:
		"""First batch prefix on this root (part before ``/``), if any."""
		for bn in batch_nos or []:
			bn = _cstr(bn)
			if not bn or "/" not in bn:
				continue
			pref = bn.split("/", 1)[0].strip()
			if pref.startswith(root_5) and len(pref) >= len(root_5):
				return pref
		return None

	def _current_batch_shift_label(self) -> str:
		return batch_shift_value(self.shift)

	def _spr_batch_nos_for_current_shift(self) -> list[str]:
		"""Batch numbers on this doc that belong to the current shift only."""
		cur = self._current_batch_shift_label()
		item_meta = frappe.get_meta("Shaft Production Run Item")
		has_shift = item_meta.has_field("custom_shift")
		out: list[str] = []
		for row in self.items or []:
			bn = _cstr(getattr(row, "batch_no", None)).strip()
			if not bn:
				continue
			if has_shift and cur:
				row_shift = batch_shift_value(getattr(row, "custom_shift", None))
				if not row_shift or row_shift != cur:
					continue
			out.append(bn)
		return out

	def _resolve_series_prefix(self, root_5: str) -> str:
		"""Reuse series for same run_date + shift + unit when batches already exist.

		Within one SPR + shift: keep the same prefix (e.g. JS-01062674) and only
		increment the roll suffix (/1, /2, …).  A new shift digit (…74 → …75) is
		allocated only when no batch exists yet for this shift on this date/unit.
		"""
		# 1) Rows already on this document (in-memory during save / preview)
		on_doc = self._spr_series_prefix_from_batches(root_5, self._spr_batch_nos_for_current_shift())
		if on_doc:
			return on_doc

		# 2) Rows saved on this SPR in DB — same shift only (day vs night get different S digit)
		if self.name:
			shift_val = self._current_batch_shift_label()
			if shift_val and frappe.db.has_column("Shaft Production Run Item", "custom_shift"):
				own_rows = frappe.db.sql(
					"""
					SELECT spi.batch_no
					FROM `tabShaft Production Run Item` spi
					WHERE spi.parent = %(cur)s
					  AND IFNULL(spi.batch_no, '') != ''
					  AND spi.batch_no LIKE CONCAT(%(root)s, '%%')
					  AND spi.custom_shift = %(shift_val)s
					ORDER BY spi.idx ASC
					LIMIT 50
					""",
					{"cur": self.name, "root": root_5, "shift_val": shift_val},
				)
			else:
				own_rows = frappe.db.sql(
					"""
					SELECT spi.batch_no
					FROM `tabShaft Production Run Item` spi
					WHERE spi.parent = %(cur)s
					  AND IFNULL(spi.batch_no, '') != ''
					  AND spi.batch_no LIKE CONCAT(%(root)s, '%%')
					ORDER BY spi.idx ASC
					LIMIT 50
					""",
					{"cur": self.name, "root": root_5},
				)
			on_doc = self._spr_series_prefix_from_batches(root_5, [r[0] for r in own_rows or []])
			if on_doc:
				return on_doc

		# 3) Other SPRs on same run_date + shift + unit
		existing = frappe.db.sql(
			"""
			SELECT spi.batch_no
			FROM `tabShaft Production Run Item` spi
			INNER JOIN `tabShaft Production Run` spr ON spr.name = spi.parent
			WHERE spr.run_date = %(rd)s
			  AND spr.shift = %(sh)s
			  AND spr.custom_unit = %(un)s
			  AND spr.name != %(cur)s
			  AND IFNULL(spi.batch_no, '') != ''
			  AND spi.batch_no LIKE CONCAT(%(root)s, '%%')
			ORDER BY spr.modified DESC
			LIMIT 20
			""",
			{
				"rd": self.run_date,
				"sh": self.shift,
				"un": self.custom_unit,
				"cur": self.name or "",
				"root": root_5,
			},
		)
		for (bn,) in existing or []:
			if bn and "/" in bn:
				pref = bn.split("/")[0].strip()
				if pref.startswith(root_5) and len(pref) >= len(root_5):
					return pref

		next_s = self._next_shift_suffix_num(root_5)
		return f"{root_5}{next_s}"

	def _spr_max_roll_suffix_for_prefix(self, series_prefix: str) -> int:
		"""Fast MAX(roll suffix) for a batch series prefix (replaces full-table row loops)."""
		series_prefix = _cstr(series_prefix).strip()
		if not series_prefix:
			return 0
		pat = f"{series_prefix}/%"
		mx = 0
		queries = (
			("Batch", "batch_id"),
			("Shaft Production Run Item", "batch_no"),
			("Roll Waste Row", "batch_no"),
		)
		for table, col in queries:
			try:
				row = frappe.db.sql(
					f"""
					SELECT MAX(CAST(SUBSTRING_INDEX(`{col}`, '/', -1) AS UNSIGNED)) AS mx
					FROM `tab{table}`
					WHERE `{col}` LIKE %(pat)s
					""",
					{"pat": pat},
					as_dict=True,
				)
				if row and row[0].get("mx") is not None:
					mx = max(mx, cint(row[0].mx))
			except Exception:
				for (val,) in frappe.db.sql(
					f"SELECT `{col}` FROM `tab{table}` WHERE `{col}` LIKE %(pat)s LIMIT 500",
					{"pat": pat},
				) or []:
					mx = max(mx, self._roll_no_from_batch(val, series_prefix))
		return mx

	def _spr_max_shift_suffix_for_root(self, root_5: str) -> int:
		"""Fast MAX(shift series digit) after root_5 in batch prefix (before ``/``)."""
		root_5 = _cstr(root_5).strip()
		if not root_5:
			return 0
		pat = f"{root_5}%"
		root_len = len(root_5)
		mx = 0
		for table, col in (("Batch", "batch_id"), ("Shaft Production Run Item", "batch_no")):
			try:
				rows = frappe.db.sql(
					f"""
					SELECT `{col}` AS val
					FROM `tab{table}`
					WHERE `{col}` LIKE %(pat)s AND `{col}` LIKE '%%/%%'
					LIMIT 800
					""",
					{"pat": pat},
				)
				for (val,) in rows or []:
					mx = max(mx, self._suffix_after_root(val, root_5))
			except Exception:
				pass
		return mx

	def _next_shift_suffix_num(self, root_5: str) -> int:
		"""Pick next S digit(s) after scanning Batch + SPR items for this month/unit/year root."""
		max_s = self._spr_max_shift_suffix_for_root(root_5)
		return max_s + 1 if max_s >= 0 else 1

	def _suffix_after_root(self, batch_id: str, root_5: str) -> int:
		if not batch_id or "/" not in batch_id:
			return 0
		pref = batch_id.split("/", 1)[0].strip()
		if not pref.startswith(root_5):
			return 0
		s_part = pref[len(root_5) :]
		try:
			return int(s_part) if s_part else 0
		except ValueError:
			return 0

	def _next_roll_starting(self, series_prefix: str) -> int:
		mx = self._spr_max_roll_suffix_for_prefix(series_prefix)
		return (mx + 1) if mx > 0 else 1

	def _roll_no_from_batch(self, batch_id: str, series_prefix: str) -> int:
		if not batch_id or "/" not in batch_id:
			return 0
		pref, roll = batch_id.split("/", 1)
		if pref.strip() != series_prefix:
			return 0
		try:
			return int(roll.strip())
		except ValueError:
			return 0

	def sync_batch_custom_fields(self):
		batch_meta = frappe.get_meta("Batch")
		is_bag = spr_doc_is_bag_spr(self)
		company = _spr_company_from_doc(self)
		rows = [r for r in (self.items or []) if _cstr(r.get("batch_no")).strip()]
		if not rows:
			return

		batch_ids = list({_cstr(r.batch_no).strip() for r in rows})
		by_name: dict[str, dict] = {}
		by_item_batch: dict[tuple[str, str], str] = {}
		if batch_ids:
			for b in frappe.db.sql(
				"""
				SELECT name, batch_id, item
				FROM `tabBatch`
				WHERE name IN %(ids)s OR batch_id IN %(ids)s
				""",
				{"ids": tuple(batch_ids)},
				as_dict=True,
			):
				by_name[_cstr(b.name)] = b
				if b.batch_id and b.item:
					by_item_batch[(_cstr(b.item), _cstr(b.batch_id))] = _cstr(b.name)

		for row in rows:
			bn = _cstr(row.batch_no).strip()
			ic = _cstr(row.get("item_code")).strip()
			batch_name = bn if bn in by_name else by_item_batch.get((ic, bn), "")
			if not batch_name and ic:
				try:
					batch_name = self._get_batch_link_name_for_stock_entry(bn, ic, company, row)
					if batch_name:
						by_name[batch_name] = {"name": batch_name}
				except Exception:
					frappe.log_error(frappe.get_traceback(), f"SPR Batch auto-create:{self.name}")
					continue
			if not batch_name:
				continue
			if not frappe.db.exists("Batch", batch_name):
				continue
			data = dict(_batch_fields_from_spr_row(batch_meta, row, is_bag_spr=is_bag))
			if batch_meta.has_field("custom_gross_weight") and row.get("gross_weight") is not None:
				data["custom_gross_weight"] = flt(row.gross_weight)
			if batch_meta.has_field("custom_cbm") and row.get("custom_cbm") is not None:
				data["custom_cbm"] = flt(row.custom_cbm)
			if batch_meta.has_field("custom_diameter") and row.get("custom_diameter") is not None:
				data["custom_diameter"] = flt(row.custom_diameter)
			if batch_meta.has_field("custom_shift") and row.get("custom_shift"):
				data["custom_shift"] = row.custom_shift
			if batch_meta.has_field("custom_party_code_text") and row.get("custom_party_code_text"):
				data["custom_party_code_text"] = row.custom_party_code_text
			# Additive: bay from SPR roll line → Batch (only set when present)
			if batch_meta.has_field("custom_bay"):
				bay = _cstr(row.get("custom_bay"))
				if bay:
					data["custom_bay"] = bay
			if not data:
				continue
			try:
				frappe.db.set_value("Batch", batch_name, data, update_modified=False)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "SPR Batch sync skipped")

	def _row_fg_qty(self, row) -> float:
		if spr_doc_is_bag_spr(self):
			pcs = flt(_spr_row_get(row, "custom_achieved_bag_pcs"))
			if pcs > 0:
				return pcs
		qty = flt(_spr_row_get(row, "net_weight"))
		if qty <= 0:
			qty = flt(_spr_row_get(row, "gross_weight"))
		if qty <= 0 and cint(getattr(self, "custom_is_box_bag", 0)):
			qty = flt(_spr_row_get(row, "custom_achieved_bag_pcs"))
		if qty <= 0 and cint(getattr(self, "custom_is_sheet_cutting", 0)):
			qty = flt(_spr_row_get(row, "custom_total_produced_sheets"))
		return qty

	def _spr_bag_fg_posting_qty_for_wo(self, wo_doc, spr_rows: list | None = None) -> float:
		"""Is Bag: FG qty in PCS from bundle/header — never meters or roll weight."""
		if not spr_doc_is_bag_spr(self) or not wo_doc:
			return 0.0
		wo_id = _cstr(getattr(wo_doc, "name", None))
		total = 0.0
		for br in self.bundle_calculation or []:
			if _cstr(getattr(br, "work_order", None)) == wo_id:
				total += flt(getattr(br, "total_produced_bag_pcs", 0) or 0)
		if total > 1e-9:
			return flt(total, 0)
		meta = frappe.get_meta("Shaft Production Run")
		if meta.has_field("custom_total_achieved_pcs"):
			hdr = flt(getattr(self, "custom_total_achieved_pcs", 0) or 0)
			if hdr > 1e-9:
				return flt(hdr, 0)
		rows = spr_rows if spr_rows is not None else [
			r for r in (self.items or [])
			if _cstr(r.get("work_order") or r.get("wo_id")) == wo_id
		]
		return flt(sum(flt(_spr_row_get(r, "custom_achieved_bag_pcs")) for r in rows), 0)

	def _spr_validate_bag_fg_qty_for_wo(self, wo_doc, fg_pcs: float) -> None:
		"""Is Bag: ensure posting PCS does not exceed WO remaining qty."""
		if not spr_doc_is_bag_spr(self) or fg_pcs <= 0:
			return
		remaining, allowed, already, _over = self._wo_allowed_remaining_qty(wo_doc)
		if fg_pcs > remaining + 1e-9:
			frappe.throw(
				_(
					"Bag SPR produced {0} PCS for WO {1}, but WO allows only {2} PCS remaining "
					"(WO qty {3}, already produced {4}). Adjust Achieved Bag PCS before submit."
				).format(
					flt(fg_pcs, 0),
					wo_doc.name,
					flt(remaining, 0),
					flt(getattr(wo_doc, "qty", 0), 0),
					flt(already, 0),
				),
				title=_("Bag PCS exceeds WO qty"),
			)

	def _set_stock_entry_spr_link(self, se):
		"""Link Manufacture / transfer entries back to this SPR for traceability and recovery tools."""
		meta = frappe.get_meta("Stock Entry")
		if meta.has_field("shaft_production_run"):
			se.shaft_production_run = self.name
		if frappe.db.has_column("Stock Entry", "custom_spr_reference") and meta.has_field("custom_spr_reference"):
			se.set("custom_spr_reference", self.name)

	def _persist_stock_entry_spr_reference_db(self, se_name: str | None):
		"""If DB has custom_spr_reference column, persist link even when not on in-memory doc meta."""
		if not se_name or not frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			return
		try:
			frappe.db.set_value("Stock Entry", se_name, "custom_spr_reference", self.name, update_modified=False)
		except Exception:
			pass

	def _apply_order_code_to_submitted_stock_entry(self, se_name: str):
		"""Copy SPR header order code onto Stock Entry when the site has a matching custom field."""
		if not se_name:
			return
		oc = self._resolve_spr_order_code()
		if not oc:
			return
		meta = frappe.get_meta("Stock Entry")
		for fn in ("order_code", "custom_order_code", "custom_party_code", "party_code"):
			if meta.has_field(fn):
				try:
					frappe.db.set_value("Stock Entry", se_name, fn, oc, update_modified=False)
				except Exception:
					pass

	def _resolve_spr_order_code(self) -> str:
		for key in ("custom_order_code", "order_code", "custom_party_code", "party_code"):
			s = _cstr(self.get(key))
			if s:
				return s
		for row in self.items or []:
			for key in ("custom_order_code", "order_code", "custom_party_code_text", "party_code"):
				s = _cstr(row.get(key))
				if s:
					return s
		return ""

	def _resolve_spr_unit_value(self, wo_doc=None) -> str:
		"""Pick unit for Stock Entry: SPR header first, then WO."""
		for v in (
			self.get("custom_unit"),
			self.get("unit"),
			getattr(wo_doc, "custom_unit", None) if wo_doc else None,
			getattr(wo_doc, "unit", None) if wo_doc else None,
		):
			s = _cstr(v)
			if s:
				return s
		return ""

	def _set_stock_entry_unit(self, se, wo_doc=None):
		meta = frappe.get_meta("Stock Entry")
		unit_value = self._resolve_spr_unit_value(wo_doc)
		if not unit_value:
			return
		for fn in ("unit", "custom_unit", "workstation", "custom_workstation"):
			if not meta.has_field(fn):
				continue
			current = _cstr(se.get(fn))
			resolved = _unit_value_for_doctype_field(unit_value, "Stock Entry", fn, meta=meta)
			if not resolved:
				continue
			if current and _units_equivalent(current, resolved):
				df = meta.get_field(fn)
				opts = (
					[_cstr(o) for o in _cstr(getattr(df, "options", "")).splitlines() if _cstr(o)]
					if df
					else []
				)
				if not opts or current in opts:
					continue
			se.set(fn, resolved)

	def _apply_unit_to_submitted_stock_entry(self, se_name: str, wo_doc=None):
		if not se_name:
			return
		unit_value = self._resolve_spr_unit_value(wo_doc)
		if not unit_value:
			return
		meta = frappe.get_meta("Stock Entry")
		for fn in ("unit", "custom_unit", "workstation", "custom_workstation"):
			if meta.has_field(fn):
				resolved = _unit_value_for_doctype_field(unit_value, "Stock Entry", fn, meta=meta)
				if not resolved:
					continue
				try:
					current = _cstr(frappe.db.get_value("Stock Entry", se_name, fn) or "")
					if current and _units_equivalent(current, resolved):
						continue
					if cint(frappe.db.get_value("Stock Entry", se_name, "docstatus") or 0) == 1 and current:
						continue
					frappe.db.set_value("Stock Entry", se_name, fn, resolved, update_modified=False)
				except Exception:
					pass

	def _spr_roll_net_weight_for_batch(self, batch_no: str) -> float:
		"""FG qty from SPR roll line for a batch id."""
		bn = _cstr(batch_no).strip()
		if not bn:
			return 0.0
		for row in self.items or []:
			if _cstr(row.get("batch_no")).strip() == bn:
				return flt(self._row_fg_qty(row))
		return 0.0

	def _refresh_batch_qty_for_codes(self, batch_codes: list[str]):
		"""Force-refresh Batch.batch_qty for given batch ids from stock ledger."""
		codes = sorted({_cstr(x).strip() for x in (batch_codes or []) if _cstr(x).strip()})
		if not codes:
			return
		qty_by_batch: dict[str, float] = {}
		try:
			for r in frappe.db.sql(
				"""
				SELECT IFNULL(batch_no, '') AS batch_no, IFNULL(SUM(actual_qty), 0) AS qty
				FROM `tabStock Ledger Entry`
				WHERE IFNULL(is_cancelled, 0) = 0
				  AND IFNULL(batch_no, '') IN %(codes)s
				GROUP BY IFNULL(batch_no, '')
				""",
				{"codes": tuple(codes)},
				as_dict=True,
			):
				bn = _cstr(r.get("batch_no")).strip()
				if bn:
					qty_by_batch[bn] = flt(r.get("qty"))
		except Exception:
			qty_by_batch = {}

		has_batch_qty = frappe.db.has_column("Batch", "batch_qty")
		has_batch_status = frappe.db.has_column("Batch", "status")
		for bn in codes:
			if not frappe.db.exists("Batch", bn):
				continue
			try:
				qty = flt(qty_by_batch.get(bn, 0))
				if abs(qty) <= 1e-9 and frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
					try:
						sb_bundle_dt = "Serial and Batch Bundle"
						sb_entry_dt = "Serial and Batch Entry"
						if frappe.db.exists("DocType", sb_bundle_dt) and frappe.db.exists("DocType", sb_entry_dt):
							sb_entry_meta = frappe.get_meta(sb_entry_dt)
							batch_field = next(
								(
									fn
									for fn in ("batch_no", "batch", "batch_id")
									if sb_entry_meta.has_field(fn)
								),
								"",
							)
							qty_field = next(
								(
									fn
									for fn in ("qty", "quantity")
									if sb_entry_meta.has_field(fn)
								),
								"",
							)
							if batch_field and qty_field:
								qty = flt(
									frappe.db.sql(
										f"""
										SELECT IFNULL(SUM(
											CASE
												WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
												ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
											END
										), 0)
										FROM `tabStock Ledger Entry` sle
										INNER JOIN `tabSerial and Batch Entry` sbe
											ON sbe.parent = sle.serial_and_batch_bundle
										WHERE IFNULL(sle.is_cancelled, 0) = 0
										  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
										  AND IFNULL(sbe.`{batch_field}`, '') = %s
										""",
										(bn,),
									)[0][0]
									or 0
								)
					except Exception:
						pass
				if has_batch_qty:
					frappe.db.sql(
						"UPDATE `tabBatch` SET batch_qty = %s WHERE name = %s",
						(qty, bn),
					)
				if has_batch_status:
					status = "Empty" if abs(qty) <= 1e-9 else "Active"
					frappe.db.set_value("Batch", bn, "status", status, update_modified=False)
			except Exception:
				pass

	def _get_existing_submitted_manufacture_entries_for_spr(self) -> list[str]:
		"""Submitted Manufacture entries already linked to this SPR."""
		names = [x.strip() for x in _cstr(self.get("manufacturing_entries")).split(",") if x and x.strip()]
		if names:
			return sorted({_cstr(x).strip() for x in names if _cstr(x).strip()})
		spr_clause, params = self._spr_stock_entry_spr_link_clause("se")
		if spr_clause != "1=0":
			names = frappe.db.sql_list(
				f"""
				SELECT se.name
				FROM `tabStock Entry` se
				WHERE {spr_clause}
				  AND IFNULL(se.purpose, '') = 'Manufacture'
				  AND IFNULL(se.docstatus, 0) = 1
				""",
				params,
			)
			return sorted({_cstr(x).strip() for x in (names or []) if _cstr(x).strip()})
		wo_ids = sorted(
			{
				_cstr(r.get("work_order") or r.get("wo_id"))
				for r in (self.items or [])
				if _cstr(r.get("work_order") or r.get("wo_id"))
			}
		)
		if not wo_ids:
			return []
		params = {"wo_ids": tuple(wo_ids)}
		where = "IFNULL(se.work_order, '') IN %(wo_ids)s"
		oc = self._resolve_spr_order_code()
		if oc:
			meta = frappe.get_meta("Stock Entry")
			for fn in ("order_code", "custom_order_code", "custom_party_code", "party_code"):
				if meta.has_field(fn):
					params["spr_order_code"] = oc
					where += f" AND IFNULL(se.{fn}, '') = %(spr_order_code)s"
					break
		names = frappe.db.sql_list(
			f"""
			SELECT se.name
			FROM `tabStock Entry` se
			WHERE {where}
			  AND IFNULL(se.purpose, '') = 'Manufacture'
			  AND IFNULL(se.docstatus, 0) = 1
			""",
			params,
		)
		return sorted({_cstr(x).strip() for x in (names or []) if _cstr(x).strip()})

	def _filter_shortages_by_wo_transfer_remaining(
		self, wo_doc, shortages: list[tuple[str, str, float, float, float]]
	) -> list[tuple[str, str, float, float, float]]:
		"""Drop/cap RM shortages when Work Order required_items already show full transfer."""
		if not wo_doc or not shortages:
			return []
		wo_doc = self._reload_work_order_doc(wo_doc)
		out = []
		for item_code, wh, req, avl, short_qty in shortages:
			ic = _cstr(item_code).strip()
			if not ic:
				continue
			still = _spr_wo_rm_transfer_remaining(wo_doc, ic)
			tol = _spr_rm_wip_shortage_tolerance(flt(short_qty))
			if still <= tol:
				continue
			capped = min(_spr_round_rm_stock_qty(flt(short_qty)), _spr_round_rm_stock_qty(still))
			if capped > tol:
				out.append((ic, wh, flt(req), flt(avl), capped))
		return out

	def _spr_prune_wip_topup_when_rm_transferred(
		self, wo_doc, shortages: list[tuple[str, str, float, float, float]]
	) -> list[tuple[str, str, float, float, float]]:
		"""Keep extra-production / stock-precision WIP gaps even when WO already shows RM transferred."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		if not wo_doc or not shortages:
			return []
		out = []
		min_qty = 10 ** (-_spr_rm_stock_qty_precision())
		for item_code, wh, req, avl, short_qty in shortages:
			ic = _cstr(item_code).strip()
			if not ic:
				continue
			if _spr_round_rm_stock_qty(short_qty) < min_qty:
				continue
			out.append((item_code, wh, req, avl, short_qty))
		return out

	def _spr_filter_preflight_shortage_events(self, shortage_events) -> list:
		"""Drop WIP top-up blocks when WO RM is already fully transferred (cap at manufacture)."""
		filtered = []
		for event in shortage_events or []:
			if not event.get("wip_topup"):
				filtered.append(event)
				continue
			wo_doc = self._reload_work_order_doc(event.get("wo_doc"))
			pruned = self._spr_prune_wip_topup_when_rm_transferred(
				wo_doc, event.get("shortages") or []
			)
			if pruned:
				filtered.append({**event, "shortages": pruned})
		return filtered

	def _filter_shortage_events_by_wo_transfer(self, shortage_events) -> list:
		"""Remove shortage lines that WO already transferred — prevents duplicate MTFM per item."""
		filtered = []
		for event in shortage_events or []:
			if event.get("wip_topup"):
				wo_doc = self._reload_work_order_doc(event.get("wo_doc"))
				pruned = self._spr_prune_wip_topup_when_rm_transferred(
					wo_doc, event.get("shortages") or []
				)
				if pruned:
					filtered.append({**event, "shortages": pruned})
				continue
			wo_doc = self._reload_work_order_doc(event.get("wo_doc"))
			shortages = self._filter_shortages_by_wo_transfer_remaining(
				wo_doc, event.get("shortages") or []
			)
			if not shortages:
				continue
			filtered.append(
				{
					"wo_id": _cstr(event.get("wo_id")),
					"wo_doc": wo_doc,
					"chunk_total_qty": flt(event.get("chunk_total_qty")),
					"shortages": shortages,
				}
			)
		return filtered

	def _spr_cap_manufacture_rm_lines_to_wip_available(self, se, wo_doc) -> bool:
		"""Cap consume lines for rounding/batch drift when WO RM is already transferred."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
		if not se or not wip_wh or not _spr_wo_rm_fully_transferred(wo_doc):
			return False
		changed = False
		for d in se.items or []:
			if not d.item_code or d.get("t_warehouse"):
				continue
			if _cstr(d.get("s_warehouse")) != wip_wh:
				continue
			ic = _cstr(d.item_code).strip()
			req = flt(d.get("transfer_qty") or d.get("qty"))
			if req <= 0:
				continue
			wh_avl = _spr_floor_rm_stock_qty(_spr_rm_available_qty(ic, wip_wh))
			batch_no = _cstr(d.get("batch_no")).strip()
			if batch_no:
				avl = _spr_floor_rm_stock_qty(_spr_batch_available_qty(ic, wip_wh, batch_no))
			else:
				avl = wh_avl
			if req <= avl + 1e-12:
				continue
			gap = req - avl
			tol = _spr_rm_wip_shortage_tolerance(req)
			if gap <= tol + 1e-12:
				cap = _spr_floor_rm_stock_qty(avl)
				if cap <= 0:
					continue
				cf = flt(d.get("conversion_factor") or 1) or 1
				d.transfer_qty = cap
				d.qty = flt(cap / cf, 6)
				changed = True
				continue
			# Assigned lot is short but WIP warehouse already has stock — drop the lot so FIFO can split.
			if batch_no and req <= wh_avl + tol + 1e-12:
				d.batch_no = ""
				if hasattr(d, "serial_and_batch_bundle"):
					d.serial_and_batch_bundle = None
				changed = True
		return changed

	def _spr_wip_topup_max_kg(self) -> float:
		"""Optional per-item cap for WIP top-up (0 = unlimited)."""
		try:
			val = frappe.conf.get("spr_wip_topup_max_kg")
			if val is None:
				return 0.0
			return max(0.0, flt(val))
		except (TypeError, ValueError):
			return 0.0

	def _spr_wip_topup_short_by_item_from_exception(self, wo_doc, exc) -> dict:
		"""Parse manufacture NegativeStockError into RM->WIP top-up qty when WO transfer is already complete."""
		parsed = self._rm_shortages_from_exception(exc)
		if not parsed:
			return {}
		wo_doc = self._reload_work_order_doc(wo_doc)
		max_topup = self._spr_wip_topup_max_kg()
		out: dict[str, float] = {}
		for ic, _wh, _req, _avl, sq in parsed:
			code = _cstr(ic).strip()
			qty = _spr_wip_topup_bump_qty(sq)
			if not code or qty <= 0:
				continue
			if max_topup > 0 and qty > max_topup:
				qty = max_topup
			out[code] = out.get(code, 0.0) + qty
		return out

	def _spr_wip_topup_from_manufacture_se(self, se, wo_doc) -> dict:
		"""Derive WIP top-up qty from failed Manufacture STE when WO RM transfer is already complete."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		if not se or not wo_doc:
			return {}
		out: dict[str, float] = {}
		for d in se.items or []:
			if not d.item_code or d.get("t_warehouse"):
				continue
			ic = _cstr(d.item_code).strip()
			required = _spr_round_rm_stock_qty(d.get("qty") or d.get("transfer_qty"))
			wh = _cstr(d.get("s_warehouse"))
			if not ic or required <= 0 or not wh:
				continue
			available = _spr_round_rm_stock_qty(
				frappe.db.get_value("Bin", {"item_code": ic, "warehouse": wh}, "actual_qty") or 0
			)
			gap = _spr_wip_topup_bump_qty(required - available)
			if gap > 0:
				out[ic] = out.get(ic, 0.0) + gap
		return out

	def _spr_try_wip_topup_transfer_and_retry_manufacture(
		self, wo_doc, exc, allow_wip_topup_retry: bool, mfg_submit_savepoint: str, mfg_se=None
	) -> None:
		"""Create one RM->WIP MTFM for parsed WIP shortage, auto-submit, then retry Manufacture."""
		if not allow_wip_topup_retry:
			return
		topup = self._spr_wip_topup_short_by_item_from_exception(wo_doc, exc)
		if not topup and mfg_se:
			topup = self._spr_wip_topup_from_manufacture_se(mfg_se, wo_doc)
		if not topup:
			return
		fg_hint = max(flt(sum(flt(v) for v in topup.values())), 1.0)
		transfer = self._create_wip_topup_mtfm_for_manufacture(wo_doc, topup, chunk_total_qty=fg_hint)
		if not transfer:
			return
		if cint(frappe.db.get_value("Stock Entry", transfer, "docstatus")) != 1:
			skip_cap_flag_backup = frappe.flags.get("spr_skip_wo_transfer_qty_validation")
			try:
				se_doc = frappe.get_doc("Stock Entry", transfer)
				frappe.flags.spr_skip_wo_transfer_qty_validation = True
				se_doc.flags.ignore_validate_work_order = True
				se_doc.flags.ignore_permissions = True
				se_doc.flags.ignore_validate = True
				se_doc.submit()
			except Exception:
				return
			finally:
				frappe.flags.spr_skip_wo_transfer_qty_validation = skip_cap_flag_backup
		if cint(frappe.db.get_value("Stock Entry", transfer, "docstatus")) == 1:
			self._force_sync_stock_bins_after_transfer(transfer, wo_doc)
			frappe.db.savepoint(mfg_submit_savepoint)
			raise _SprWipTopupRetry()

	def _force_sync_stock_bins_after_transfer(self, se_name: str, wo_doc) -> None:
		"""Force immediate stock bin synchronization after MTFM submit to avoid stale reads."""
		try:
			wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
			if not wip_wh:
				return
			# Get items from the submitted Stock Entry
			items = frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": se_name, "t_warehouse": wip_wh},
				fields=["item_code"],
			)
			if not items:
				return
			# Force recompute bin actual_qty from stock ledger for each item
			from erpnext.stock.utils import update_bin_qty
			for row in items:
				ic = _cstr(row.get("item_code"))
				if not ic:
					continue
				# update_bin_qty recalculates actual_qty from Stock Ledger Entry
				update_bin_qty(ic, wip_wh)
			# Clear any query cache
			frappe.clear_cache(doctype="Bin")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "SPR force bin sync")

	def _force_sync_bins_for_stock_entry(self, se_name: str) -> None:
		"""Force immediate bin sync for all items in a Stock Entry (source and target warehouses)."""
		try:
			from erpnext.stock.utils import update_bin_qty
			items = frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": se_name},
				fields=["item_code", "s_warehouse", "t_warehouse"],
			)
			seen = set()
			for row in items:
				ic = _cstr(row.get("item_code"))
				if not ic:
					continue
				for wh in (row.get("s_warehouse"), row.get("t_warehouse")):
					wh = _cstr(wh)
					if wh and (ic, wh) not in seen:
						seen.add((ic, wh))
						update_bin_qty(ic, wh)
			frappe.clear_cache(doctype="Bin")
		except Exception:
			frappe.log_error(frappe.get_traceback(), "SPR force bin sync (SE)")

	def _create_wip_topup_mtfm_for_manufacture(self, wo_doc, short_by_item: dict, chunk_total_qty: float = 0) -> str:
		"""One RM->WIP transfer when Manufacture cannot consume WIP (extra production or ledger gap)."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		if not wo_doc or not short_by_item:
			return ""
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None)) or ""
		if not wip_wh:
			return ""
		short_by_item = {
			_cstr(k).strip(): _spr_wip_topup_bump_qty(v)
			for k, v in (short_by_item or {}).items()
			if _cstr(k).strip() and flt(v) > 0
		}
		if not short_by_item:
			return ""
		se, _wip_b = self._new_mtfm_stock_entry_shell(
			wo_doc,
			flt(chunk_total_qty) or max(sum(flt(v) for v in short_by_item.values()), 1.0),
			today(),
			nowtime(),
			short_by_item=short_by_item,
		)
		se.from_bom = 0
		if not self._append_mtfm_shortage_lines(
			se, wo_doc, short_by_item, wip_wh, ignore_wo_transfer=True
		):
			return ""
		return self._spr_insert_shortage_transfer_draft(se)

	def _spr_apply_stock_entry_item_accounts(self, se) -> None:
		_spr_apply_stock_entry_item_accounts(se)

	def _spr_wo_wip_covers_required(self, wo_doc) -> bool:
		"""True when WIP bins cover every WO required_item (within rounding tolerance)."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
		if not wo_doc or not wip_wh:
			return False
		any_rm = False
		for req in getattr(wo_doc, "required_items", None) or []:
			ic = _cstr(getattr(req, "item_code", None)).strip()
			required = flt(getattr(req, "required_qty", 0))
			if not ic or required <= 0:
				continue
			any_rm = True
			wip_qty = flt(
				frappe.db.get_value("Bin", {"item_code": ic, "warehouse": wip_wh}, "actual_qty") or 0
			)
			if wip_qty + _spr_rm_wip_shortage_tolerance(required) + 1e-9 < required:
				return False
		return any_rm

	def _throw_wip_stock_wo_transfer_mismatch(self, wo_doc, original_exc=None):
		"""WO shows RM transferred but WIP bin cannot satisfy Manufacture — do not create more MTFM."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		wo_id = _cstr(getattr(wo_doc, "name", None))
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
		prec = _spr_rm_stock_qty_precision()
		lines = []
		wip_covers = self._spr_wo_wip_covers_required(wo_doc)
		for req in getattr(wo_doc, "required_items", None) or []:
			ic = _cstr(getattr(req, "item_code", None)).strip()
			if not ic:
				continue
			transferred = flt(getattr(req, "transferred_qty", 0))
			required = flt(getattr(req, "required_qty", 0))
			wip_qty = flt(
				frappe.db.get_value("Bin", {"item_code": ic, "warehouse": wip_wh}, "actual_qty") or 0
			)
			lines.append(
				_("{0}: WO transferred {1} / required {2} Kg; WIP bin {3} Kg").format(
					ic, flt(transferred, prec), flt(required, prec), flt(wip_qty, prec)
				)
			)
		err_text = original_exc if isinstance(original_exc, str) else _spr_exc_message(original_exc)
		err_tail = _("\n\nERPNext error:\n{0}").format(err_text) if err_text else ""
		if wip_covers:
			frappe.throw(
				_(
					"Work Order {0} is already submitted and WIP has enough stock. "
					"No extra RM transfer is required. Only Shaft Production Run {1} is still draft — "
					"Manufacture Stock Entry could not be posted while submitting it."
					"{2}"
				).format(wo_id, self.name, err_tail),
				title=_("SPR Manufacture failed"),
			)
		frappe.throw(
			_(
				"Work Order {0} already shows raw materials transferred, but Work In Progress "
				"warehouse does not have enough stock to complete Manufacture.\n\n"
				"{1}\n\n"
				"An automatic RM → WIP transfer could not be created or submitted. "
				"Check RM store stock for the shortage item(s), cancel duplicate MTFM entries "
				"if any, then submit SPR again."
				"{2}"
			).format(wo_id, "\n".join(lines[:15]), err_tail),
			title=_("WIP stock mismatch"),
		)

	def _rm_shortages_for_se(self, se, wo_doc=None) -> list[tuple[str, str, float, float, float]]:
		"""Return RM shortages as (item_code, s_warehouse, required, available, shortage)."""
		if wo_doc:
			wo_doc = self._reload_work_order_doc(wo_doc)
		out = []
		for d in se.items or []:
			if not d.item_code or d.get("t_warehouse"):
				continue
			required = _spr_round_rm_stock_qty(d.get("transfer_qty") or d.get("qty"))
			if required <= 0:
				continue
			wh = _cstr(d.get("s_warehouse"))
			available = _spr_round_rm_stock_qty(_spr_rm_available_qty(d.item_code, wh))
			tol = _spr_rm_wip_shortage_tolerance(required)
			shortage = required - available
			if shortage <= tol:
				continue
			if wo_doc and _spr_wo_rm_transfer_remaining(wo_doc, d.item_code) <= tol:
				# MTFM already submitted — WIP Bin may differ from BOM preview by fractions of a gram.
				continue
			out.append((_cstr(d.item_code), wh, required, available, shortage))
		if wo_doc:
			return self._filter_shortages_by_wo_transfer_remaining(wo_doc, out)
		return out

	def _spr_wip_topup_shortages_for_se(self, se, wo_doc) -> list[tuple[str, str, float, float, float]]:
		"""WIP bin shortfall when WO already shows RM transferred — needs company RM -> WIP top-up."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
		if not se or not wip_wh:
			return []
		company = _cstr(getattr(wo_doc, "company", None))
		out = []
		for d in se.items or []:
			if not d.item_code or d.get("t_warehouse"):
				continue
			if _cstr(d.get("s_warehouse")) != wip_wh:
				continue
			ic = _cstr(d.item_code).strip()
			required = _spr_round_rm_stock_qty(d.get("transfer_qty") or d.get("qty"))
			if required <= 0:
				continue
			available = _spr_round_rm_stock_qty(_spr_rm_available_qty(ic, wip_wh))
			shortage = _spr_round_rm_stock_qty(required - available)
			if shortage <= 0:
				continue
			bump = _spr_wip_topup_bump_qty(shortage)
			item_src = self._resolve_rm_source_warehouse_for_transfer(wo_doc, ic, wip_wh)
			rm_wh, rm_avl = _spr_find_rm_warehouse_with_stock(company, ic, wip_wh, item_src, bump)
			if _spr_round_rm_stock_qty(rm_avl) < bump:
				continue
			if not rm_wh:
				rm_wh = item_src or _spr_company_rm_warehouse(company, wip_wh) or _("RM warehouse")
			out.append((ic, rm_wh, required, available, bump))
		return out

	def _best_available_batch_for_rm(self, item_code: str, warehouse: str, required_qty: float) -> str:
		"""Pick a batch with available qty in source warehouse for RM consumption.

		Covers both classic SLE.batch_no and v15 Serial-and-Batch-Bundle based entries.
		"""
		if not item_code or not warehouse:
			return ""
		rows = frappe.db.sql(
			"""
			SELECT batch_no, SUM(actual_qty) as qty
			FROM `tabStock Ledger Entry`
			WHERE IFNULL(is_cancelled, 0) = 0
			  AND IFNULL(item_code, '') = %s
			  AND IFNULL(warehouse, '') = %s
			  AND IFNULL(batch_no, '') != ''
			GROUP BY batch_no
			HAVING SUM(actual_qty) > 0
			ORDER BY SUM(actual_qty) DESC, MAX(posting_date) DESC, MAX(posting_time) DESC
			""",
			(item_code, warehouse),
			as_dict=True,
		)
		# v15 path: batch may live in Serial and Batch Entry (bundle), with empty SLE.batch_no.
		if (not rows) and frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
			try:
				sb_entry_dt = "Serial and Batch Entry"
				if frappe.db.exists("DocType", sb_entry_dt):
					sb_entry_meta = frappe.get_meta(sb_entry_dt)
					batch_field = next(
						(fn for fn in ("batch_no", "batch", "batch_id") if sb_entry_meta.has_field(fn)),
						"",
					)
					qty_field = next(
						(fn for fn in ("qty", "quantity") if sb_entry_meta.has_field(fn)),
						"",
					)
					if batch_field and qty_field:
						rows = frappe.db.sql(
							f"""
							SELECT
								sbe.`{batch_field}` AS batch_no,
								SUM(
									CASE
										WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
										ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
									END
								) AS qty
							FROM `tabStock Ledger Entry` sle
							INNER JOIN `tabSerial and Batch Entry` sbe
								ON sbe.parent = sle.serial_and_batch_bundle
							WHERE IFNULL(sle.is_cancelled, 0) = 0
							  AND IFNULL(sle.item_code, '') = %s
							  AND IFNULL(sle.warehouse, '') = %s
							  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
							  AND IFNULL(sbe.`{batch_field}`, '') != ''
							GROUP BY sbe.`{batch_field}`
							HAVING SUM(
								CASE
									WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
									ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
								END
							) > 0
							ORDER BY qty DESC
							""",
							(item_code, warehouse),
							as_dict=True,
						)
			except Exception:
				rows = rows or []
		if not rows:
			return ""
		need = flt(required_qty or 0)
		for r in rows:
			if flt(r.get("qty") or 0) + 1e-9 >= need:
				return _cstr(r.get("batch_no"))
		# Important: do NOT pick a partial batch for a full-consumption row.
		# Picking an under-qty batch causes negative stock on submit.
		return ""

	def _available_batches_for_rm(self, item_code: str, warehouse: str) -> list[dict]:
		"""Return available batches with qty for RM consumption, sorted by qty desc.

		Covers both classic SLE.batch_no and bundle-based batch entries.
		"""
		if not item_code or not warehouse:
			return []
		acc: dict[str, float] = {}
		# Path 1: classic batch_no on SLE
		for r in frappe.db.sql(
			"""
			SELECT batch_no, SUM(actual_qty) as qty
			FROM `tabStock Ledger Entry`
			WHERE IFNULL(is_cancelled, 0) = 0
			  AND IFNULL(item_code, '') = %s
			  AND IFNULL(warehouse, '') = %s
			  AND IFNULL(batch_no, '') != ''
			GROUP BY batch_no
			HAVING SUM(actual_qty) > 0
			""",
			(item_code, warehouse),
			as_dict=True,
		):
			bn = _cstr(r.get("batch_no"))
			q = flt(r.get("qty") or 0)
			if bn and q > 0:
				acc[bn] = acc.get(bn, 0.0) + q

		# Path 2: serial-and-batch bundle (v15)
		if frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
			try:
				sb_entry_dt = "Serial and Batch Entry"
				if frappe.db.exists("DocType", sb_entry_dt):
					sb_entry_meta = frappe.get_meta(sb_entry_dt)
					batch_field = next(
						(fn for fn in ("batch_no", "batch", "batch_id") if sb_entry_meta.has_field(fn)),
						"",
					)
					qty_field = next(
						(fn for fn in ("qty", "quantity") if sb_entry_meta.has_field(fn)),
						"",
					)
					if batch_field and qty_field:
						rows = frappe.db.sql(
							f"""
							SELECT
								sbe.`{batch_field}` AS batch_no,
								SUM(
									CASE
										WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
										ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
									END
								) AS qty
							FROM `tabStock Ledger Entry` sle
							INNER JOIN `tabSerial and Batch Entry` sbe
								ON sbe.parent = sle.serial_and_batch_bundle
							WHERE IFNULL(sle.is_cancelled, 0) = 0
							  AND IFNULL(sle.item_code, '') = %s
							  AND IFNULL(sle.warehouse, '') = %s
							  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
							  AND IFNULL(sbe.`{batch_field}`, '') != ''
							GROUP BY sbe.`{batch_field}`
							HAVING SUM(
								CASE
									WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.`{qty_field}`, 0))
									ELSE ABS(IFNULL(sbe.`{qty_field}`, 0))
								END
							) > 0
							""",
							(item_code, warehouse),
							as_dict=True,
						)
						for r in rows or []:
							bn = _cstr(r.get("batch_no"))
							q = flt(r.get("qty") or 0)
							if bn and q > 0:
								acc[bn] = max(acc.get(bn, 0.0), q)
			except Exception:
				pass

		out = []
		for bn, _stale in acc.items():
			if not bn:
				continue
			live = _spr_batch_available_qty(item_code, warehouse, bn)
			if live > 0.0001:
				out.append({"batch_no": bn, "qty": live})
		out.sort(key=lambda x: flt(x.get("qty") or 0), reverse=True)
		return out

	def _available_batches_for_rm_all_wh(self, item_code: str) -> list[dict]:
		"""Like _available_batches_for_rm but queries ALL warehouses.

		Returns list of dicts with keys: batch_no, qty, warehouse.
		Used for the fabric-batch pick dialog so the user can see every
		available batch regardless of whether it is already in WIP.
		"""
		if not item_code:
			return []
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
				sb_entry_dt = "Serial and Batch Entry"
				if frappe.db.exists("DocType", sb_entry_dt):
					sb_meta = frappe.get_meta(sb_entry_dt)
					batch_field = next(
						(fn for fn in ("batch_no", "batch", "batch_id") if sb_meta.has_field(fn)), ""
					)
					qty_field = next(
						(fn for fn in ("qty", "quantity") if sb_meta.has_field(fn)), ""
					)
					if batch_field and qty_field:
						rows = frappe.db.sql(
							f"""
							SELECT
								sbe.`{batch_field}` AS batch_no,
								sle.warehouse,
								SUM(
									CASE
										WHEN IFNULL(sle.actual_qty,0) < 0
											THEN -ABS(IFNULL(sbe.`{qty_field}`,0))
										ELSE ABS(IFNULL(sbe.`{qty_field}`,0))
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
									WHEN IFNULL(sle.actual_qty,0) < 0
										THEN -ABS(IFNULL(sbe.`{qty_field}`,0))
									ELSE ABS(IFNULL(sbe.`{qty_field}`,0))
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

		# Re-read live warehouse qty. Classic SLE.batch_no sums ignore bundle transfers,
		# which made the same batch appear at FG and WIP with identical kg after MTFM.
		out = []
		for (bn, wh), _stale in acc.items():
			if not bn or not wh:
				continue
			q = _spr_batch_available_qty(item_code, wh, bn)
			if q > 0.0001:
				out.append({"batch_no": bn, "qty": q, "warehouse": wh})
		out.sort(key=lambda x: (0 if "progress" in _cstr(x.get("warehouse")).lower() else 1, -flt(x.get("qty") or 0)))
		return out

	def _mtfm_batch_nos_for_wo_item(self, wo_name: str, item_code: str) -> set[str]:
		"""Batch numbers transferred to WIP for this WO via submitted Material Transfer for Manufacture."""
		wo_name = _cstr(wo_name).strip()
		item_code = _cstr(item_code).strip()
		out: set[str] = set()
		if not wo_name or not item_code:
			return out
		for r in frappe.db.sql(
			"""
			SELECT DISTINCT IFNULL(sed.batch_no, '') AS batch_no
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			WHERE se.docstatus = 1
			  AND IFNULL(se.work_order, '') = %s
			  AND IFNULL(se.purpose, '') = 'Material Transfer for Manufacture'
			  AND IFNULL(sed.item_code, '') = %s
			  AND IFNULL(sed.batch_no, '') != ''
			""",
			(wo_name, item_code),
			as_dict=True,
		):
			bn = _cstr(r.get("batch_no")).strip()
			if bn:
				out.add(bn)
		if frappe.db.exists("DocType", "Serial and Batch Entry"):
			try:
				sb_meta = frappe.get_meta("Serial and Batch Entry")
				batch_field = next(
					(fn for fn in ("batch_no", "batch", "batch_id") if sb_meta.has_field(fn)),
					"",
				)
				if batch_field:
					for r in frappe.db.sql(
						f"""
						SELECT DISTINCT IFNULL(sbe.`{batch_field}`, '') AS batch_no
						FROM `tabStock Entry` se
						INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
						INNER JOIN `tabSerial and Batch Entry` sbe
							ON sbe.parent = IFNULL(sed.serial_and_batch_bundle, '')
						WHERE se.docstatus = 1
						  AND IFNULL(se.work_order, '') = %s
						  AND IFNULL(se.purpose, '') = 'Material Transfer for Manufacture'
						  AND IFNULL(sed.item_code, '') = %s
						  AND IFNULL(sbe.`{batch_field}`, '') != ''
						""",
						(wo_name, item_code),
						as_dict=True,
					):
						bn = _cstr(r.get("batch_no")).strip()
						if bn:
							out.add(bn)
			except Exception:
				pass
		return out

	def _spr_is_wip_warehouse(self, warehouse: str, wip_warehouse: str = "") -> bool:
		wh = _cstr(warehouse).strip()
		if not wh:
			return False
		target = _cstr(wip_warehouse).strip()
		if target and wh == target:
			return True
		wu = wh.upper()
		return "WORK IN PROGRESS" in wu or wu.startswith("WIP ") or wu.startswith("WIP-")

	def _available_batches_for_wo_transfer(self, wo_doc, item_code: str) -> list[dict]:
		"""Only WIP batches already transferred for this WO (MTFM), with current stock qty."""
		wo_name = _cstr(getattr(wo_doc, "name", None)).strip()
		allowed = self._mtfm_batch_nos_for_wo_item(wo_name, item_code)
		if not allowed:
			return []
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None) or "")
		all_batches = self._available_batches_for_rm_all_wh(item_code)
		return [
			b
			for b in all_batches
			if _cstr(b.get("batch_no")).strip() in allowed
			and self._spr_is_wip_warehouse(_cstr(b.get("warehouse")), wip_wh)
		]

	def _spr_fabric_picks_field_exists(self) -> bool:
		try:
			return bool(frappe.get_meta("Shaft Production Run").has_field("fabric_batch_picks"))
		except Exception:
			return False

	def _spr_init_manual_fabric_batch_pools(self, planned_wo_posts) -> None:
		"""Validate `fabric_batch_picks` vs BOM batch-tracked RM need and build mutable pools for Manufacture SE."""
		self.flags._spr_manual_batch_pools = {}
		if not self._spr_fabric_picks_field_exists():
			return
		pools: dict[str, dict[str, list]] = {}
		for plan in planned_wo_posts or []:
			wo_id = _cstr(plan.get("wo_id"))
			wo_doc = plan.get("wo_doc")
			total_qty = flt(plan.get("total_qty") or 0)
			if not wo_id or not wo_doc or total_qty <= 0:
				continue
			pi = _cstr(getattr(wo_doc, "production_item", None) or "")
			if not spr_fg_needs_rm_batch_pick(pi, spr_doc_is_bag_spr(self)):
				continue
			expected = self._build_expected_rm_map_for_qty(wo_doc, total_qty)
			batch_rm = {
				ic: q
				for ic, q in (expected or {}).items()
				if _spr_rm_needs_manual_batch_pick(ic) and flt(q) > 1e-9
			}
			if not batch_rm:
				continue
			picks = [
				r
				for r in (self.get("fabric_batch_picks") or [])
				if _cstr(getattr(r, "work_order", None) or "") == wo_id
			]
			if not picks:
				if spr_doc_is_bag_spr(self):
					frappe.throw(
						_(
							"Bag SPR — Work Order {0} (item {1}) needs raw-material batches before Submit. "
							"Open **Tools → SPR — Select RM batches**, allocate fabric/BOM batches, Save picks, then Submit again."
						).format(wo_id, pi or "—"),
						title=_("RM batches required"),
					)
				frappe.throw(
					_(
						"This SPR manufactures Work Order {0} (parent item {1}). "
						"Use **Select RM batches** on the toolbar and allocate each batch-tracked BOM item before Submit."
					).format(wo_id, pi or "—"),
					title=_("RM batches required"),
				)
			by_item: dict[str, list] = defaultdict(list)
			for p in picks:
				ic = _cstr(getattr(p, "item_code", None))
				bn = _cstr(getattr(p, "batch_no", None))
				q = flt(getattr(p, "qty", None))
				if not ic or not bn or q <= 0:
					continue
				if not _spr_rm_needs_manual_batch_pick(ic):
					frappe.throw(
						_("Batch pick lines must be for batch-tracked BOM items (got {0}).").format(ic)
					)
				if frappe.db.exists("Batch", bn):
					batch_item = frappe.db.get_value("Batch", bn, "item")
					if batch_item and _cstr(batch_item) != ic:
						frappe.throw(
							_("Batch {0} belongs to item {1}, not {2}. Fix SPR {3}.").format(bn, batch_item, ic, self.name)
						)
				by_item[ic].append({"batch_no": bn, "qty": q})
			for ic, req in batch_rm.items():
				got = sum(flt(x["qty"]) for x in by_item.get(ic, []))
				if got + 1e-6 < flt(req):
					frappe.throw(
						_(
							"Work Order {0}: RM {1} needs {2} Kg in batch picks but only {3} Kg is set. "
							"Open **Select RM batches** and increase quantities."
						).format(wo_id, ic, flt(req, 3), flt(got, 3)),
						title=_("Insufficient RM batch quantity"),
					)
			pools[wo_id] = {
				k: [{"batch_no": x["batch_no"], "qty": flt(x["qty"])} for x in v] for k, v in by_item.items()
			}
		self.flags._spr_manual_batch_pools = pools

	def _spr_rm_row_uses_manual_pool(self, wo_id: str | None, item_code: str) -> bool:
		if not wo_id:
			return False
		pools = getattr(self.flags, "_spr_manual_batch_pools", None) or {}
		if wo_id not in pools:
			return False
		ic = _cstr(item_code)
		if not _spr_rm_needs_manual_batch_pick(ic):
			return False
		return ic in (pools.get(wo_id) or {})

	def _spr_take_from_manual_pool(self, wo_id: str, item_code: str, required: float):
		ch = list((getattr(self.flags, "_spr_manual_batch_pools", None) or {}).get(wo_id, {}).get(item_code, []) or [])
		remaining = flt(required)
		allocs: list[tuple[str, float]] = []
		idx = 0
		while idx < len(ch) and remaining > 1e-9:
			entry = ch[idx]
			av = flt(entry.get("qty"))
			if av <= 1e-9:
				idx += 1
				continue
			take = min(av, remaining)
			allocs.append((_cstr(entry.get("batch_no")), flt(take)))
			entry["qty"] = flt(av - take)
			remaining = flt(remaining - take)
			if entry["qty"] <= 1e-9:
				idx += 1
		return allocs, remaining

	def _spr_apply_manual_rm_batch_splits(self, se, rm_row, allocs: list[tuple[str, float]], source_wh: str):
		if not allocs:
			return
		cf = flt(rm_row.get("conversion_factor") or 1) or 1
		first_bn, first_qty = allocs[0]
		rm_row.batch_no = first_bn
		rm_row.s_warehouse = source_wh
		rm_row.transfer_qty = flt(first_qty, 6)
		rm_row.qty = flt(first_qty / cf, 6)
		for bn, tq in allocs[1:]:
			new_row = se.append("items", {})
			base = rm_row.as_dict()
			for k, v in base.items():
				if k in {"name", "parent", "parenttype", "parentfield", "idx", "owner", "creation", "modified", "modified_by", "docstatus"}:
					continue
				new_row.set(k, v)
			new_row.batch_no = bn
			new_row.s_warehouse = source_wh
			new_row.transfer_qty = flt(tq, 6)
			new_row.qty = flt(tq / cf, 6)

	def _assign_rm_batches_for_stock_entry(self, se, wo_id: str | None = None):
		"""Assign batch_no for batch-tracked RM lines before submit.

		For Work Orders on FG processes 102–109, 251, 252 (incl. design-first codes), 100* fabric lines consume
		batches from operator picks (`fabric_batch_picks`) in order instead of auto FIFO by quantity.
		"""
		for d in list(se.items or []):
			if not d.item_code or d.get("t_warehouse"):
				continue
			if _cstr(d.get("batch_no")):
				continue
			if not cint(frappe.db.get_value("Item", d.item_code, "has_batch_no") or 0):
				continue
			wh = _cstr(d.get("s_warehouse"))
			required = flt(d.get("transfer_qty") or d.get("qty") or 0)
			if self._spr_rm_row_uses_manual_pool(wo_id, d.item_code):
				allocs, leftover = self._spr_take_from_manual_pool(_cstr(wo_id), _cstr(d.item_code), required)
				if leftover > 1e-6 or not allocs:
					frappe.throw(
						_(
							"Manual RM batch pool for WO {0}, item {1}, is exhausted or short by {2} Kg "
							"(required for this Manufacture line: {3} Kg). Re-open **Select RM batches**."
						).format(wo_id or "—", _cstr(d.item_code), flt(leftover, 3), flt(required, 3)),
						title=_("Fabric batch pool exhausted"),
					)
				self._spr_apply_manual_rm_batch_splits(se, d, allocs, wh)
				continue

			candidates = self._available_batches_for_rm(_cstr(d.item_code), wh)
			source_wh = wh
			if not candidates:
				# Fallback: if WIP has no batches yet, consume directly from from_warehouse with valid batches.
				fallback_wh = _cstr(se.get("from_warehouse"))
				if fallback_wh and fallback_wh != wh:
					candidates = self._available_batches_for_rm(_cstr(d.item_code), fallback_wh)
					if candidates:
						source_wh = fallback_wh

			remaining = flt(required)
			allocs: list[tuple[str, float]] = []
			for c in candidates:
				if remaining <= 1e-9:
					break
				q = flt(c.get("qty") or 0)
				if q <= 1e-9:
					continue
				take = min(q, remaining)
				if take > 1e-9:
					allocs.append((_cstr(c.get("batch_no")), flt(take)))
					remaining = flt(remaining - take)

			# 0.001 Kg WO/BOM rounding must not block Manufacture when lots already cover the line.
			if allocs and remaining <= max(1e-6, _spr_rm_wip_shortage_tolerance(required)):
				# Split one RM row across many batches when needed.
				cf = flt(d.get("conversion_factor") or 1) or 1
				first_bn, first_qty = allocs[0]
				d.batch_no = first_bn
				d.s_warehouse = source_wh
				d.transfer_qty = flt(first_qty, 6)
				d.qty = flt(first_qty / cf, 6)
				for bn, tq in allocs[1:]:
					new_row = se.append("items", {})
					base = d.as_dict()
					for k, v in base.items():
						if k in {"name", "parent", "parenttype", "parentfield", "idx", "owner", "creation", "modified", "modified_by", "docstatus"}:
							continue
						new_row.set(k, v)
					new_row.batch_no = bn
					new_row.s_warehouse = source_wh
					new_row.transfer_qty = flt(tq, 6)
					new_row.qty = flt(tq / cf, 6)
				continue

			avail_wh = flt(
				frappe.db.sql(
					"""
					SELECT IFNULL(SUM(actual_qty), 0)
					FROM `tabStock Ledger Entry`
					WHERE IFNULL(is_cancelled, 0) = 0
					  AND IFNULL(item_code, '') = %s
					  AND IFNULL(warehouse, '') = %s
					  AND IFNULL(batch_no, '') != ''
					""",
					(_cstr(d.item_code), wh),
				)[0][0]
				or 0
			)
			frappe.throw(
				_(
					"Batch is mandatory for raw material {0} in {1}, but no batch has enough quantity "
					"(required: {2}, available in batches: {3}). Transfer more stock or split issue batches."
				).format(
					_cstr(d.item_code), wh or "—", flt(required, 3), flt(avail_wh, 3)
				),
				title=_("Missing RM Batch"),
			)

	def _spr_merge_wos_from_shaft_jobs(self, wo_groups: dict[str, list]) -> None:
		"""Include WOs from Available Jobs so fabric can be picked before roll rows are linked or weighed."""
		for job in self.get("shaft_jobs") or []:
			raw = _cstr(getattr(job, "work_orders", None) or "")
			if not raw:
				continue
			for part in raw.replace("\n", ",").split(","):
				wo = part.strip()
				if not wo or not frappe.db.exists("Work Order", wo):
					continue
				wo_groups.setdefault(_cstr(wo), [])

	def _spr_fabric_pick_preview_fg_qty(self, rows: list, wo_doc) -> float:
		"""FG kg for BOM preview: actual roll weights, else planned_qty, else WO remaining / qty (min 1)."""
		qty = sum(self._row_fg_qty(r) for r in rows)
		if qty > 1e-9:
			return qty
		wo_id = _cstr(getattr(wo_doc, "name", None))
		if spr_doc_is_bag_spr(self) and wo_id:
			for br in self.bundle_calculation or []:
				if _cstr(getattr(br, "work_order", None)) != wo_id:
					continue
				planned = flt(getattr(br, "total_pcs_per_bundle", 0) or 0)
				if planned > 1e-9:
					return planned
				n_boxes = cint(getattr(br, "no_of_boxes", 0) or 0)
				pcs = cint(getattr(br, "pcs_per_packet", 0) or 0)
				if n_boxes > 0 and pcs > 0:
					return flt(n_boxes * pcs)
			achieved = sum(
				flt(_spr_row_get(r, "custom_achieved_bag_pcs"))
				for r in (self.items or [])
				if _cstr(r.get("work_order") or r.get("wo_id")) == wo_id
			)
			if achieved > 1e-9:
				return achieved
		if cint(getattr(self, "custom_is_box_bag", 0)):
			planned_bags = sum(flt(_spr_row_get(r, "custom_planned_bag_pcs")) for r in rows)
			if planned_bags > 1e-9:
				return planned_bags
		if cint(getattr(self, "custom_is_sheet_cutting", 0)):
			planned_sheets = sum(flt(_spr_row_get(r, "custom_planned_sheets_pcs")) for r in rows)
			if planned_sheets > 1e-9:
				return planned_sheets
		planned = sum(flt(_spr_row_get(r, "planned_qty")) for r in rows)
		if planned > 1e-9:
			return planned
		remaining, _, _, _ = self._wo_allowed_remaining_qty(wo_doc)
		if remaining > 1e-9:
			return remaining
		base = flt(getattr(wo_doc, "qty", 0))
		if base > 1e-9:
			return base
		return 1.0

	def _spr_wo_groups_for_batch_pick(self) -> dict[str, list]:
		"""Work Orders → roll lines for RM batch dialog (items + bundle_calculation + shaft jobs)."""
		wo_groups: dict[str, list] = {}
		for row in self.items or []:
			wo_name = _cstr(row.get("work_order") or row.get("wo_id"))
			if wo_name:
				wo_groups.setdefault(wo_name, []).append(row)
		for br in self.bundle_calculation or []:
			wo_name = _cstr(getattr(br, "work_order", None))
			if not wo_name or wo_name in wo_groups:
				continue
			linked = [
				r
				for r in (self.items or [])
				if _cstr(r.get("work_order") or r.get("wo_id")) == wo_name
			]
			wo_groups[wo_name] = linked or [{"work_order": wo_name, "wo_id": wo_name}]
		self._spr_merge_wos_from_shaft_jobs(wo_groups)
		if spr_doc_is_bag_spr(self):
			for br in self.bundle_calculation or []:
				wo_name = _cstr(getattr(br, "work_order", None))
				if not wo_name or wo_name in wo_groups:
					continue
				wo_groups[wo_name] = [{"work_order": wo_name, "wo_id": wo_name}]
			pp = _cstr(self.get("production_plan"))
			if pp:
				for wo in frappe.get_all(
					"Work Order",
					filters={"production_plan": pp, "docstatus": ["<", 2]},
					fields=["name", "production_item"],
				):
					wo_name = _cstr(wo.get("name"))
					pi = _cstr(wo.get("production_item"))
					if not wo_name or wo_name in wo_groups:
						continue
					if spr_bag_fg_needs_rm_batch_pick(pi):
						wo_groups[wo_name] = wo_groups.get(wo_name) or [{"work_order": wo_name, "wo_id": wo_name}]
		return wo_groups

	def _spr_build_fabric_batch_pick_context_dict(self) -> dict:
		"""API payload for the desk RM-batch dialog (102–109, 251–255 WOs + batch-tracked BOM RM + WIP batches)."""
		is_bag_spr = spr_doc_is_bag_spr(self)
		out: dict = {
			"needs_picks": False,
			"lines": [],
			"current_picks": [],
			"spr": self.name,
			"is_bag_spr": bool(is_bag_spr),
		}
		if cint(self.docstatus) != 0:
			return out
		if not self._spr_fabric_picks_field_exists():
			return out
		wo_groups = self._spr_wo_groups_for_batch_pick()
		lines = []
		for wo_id, rows in wo_groups.items():
			if not frappe.db.exists("Work Order", wo_id):
				continue
			wo_doc = frappe.get_doc("Work Order", wo_id)
			pi = _cstr(getattr(wo_doc, "production_item", None) or "")
			if not spr_fg_needs_rm_batch_pick(pi, is_bag_spr):
				continue
			total_qty = self._spr_fabric_pick_preview_fg_qty(rows, wo_doc)
			if total_qty <= 0:
				continue
			expected = self._build_expected_rm_map_for_qty(wo_doc, total_qty)
			wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None) or "")
			fg_process = _spr_resolve_bag_fg_process_code(pi) or spr_fg_item_process_code(pi)
			bom_stack = _spr_bom_stack_for_fg_item(pi)
			raw_list = []
			for ic, req in sorted((expected or {}).items()):
				ic_s = _cstr(ic)
				if not _spr_rm_needs_manual_batch_pick(ic_s):
					continue
				if flt(req) <= 1e-9:
					continue
				batches = self._available_batches_for_wo_transfer(wo_doc, ic_s)
				item_name = frappe.db.get_value("Item", ic_s, "item_name") or ""
				rm_proc = spr_fg_item_process_code(ic_s)
				qual, col = extract_quality_and_color(item_name, ic_s)
				try:
					gsm, width_inch = parse_item_code(ic_s)
				except Exception:
					gsm, width_inch = 0.0, 0.0
				if flt(gsm) <= 0:
					gsm = float(_fabric_gsm_from_item_name(item_name))
				raw_list.append(
					{
						"item_code": ic_s,
						"item_name": item_name,
						"process_code": rm_proc,
						"required_qty": flt(req, 3),
						"quality": qual or "",
						"colour": col or "",
						"gsm": flt(gsm, 2),
						"width_inch": flt(width_inch, 2),
						"batches": batches,
					}
				)
			if raw_list:
				lines.append(
					{
						"work_order": wo_id,
						"fg_item": pi,
						"fg_process": fg_process,
						"bom_stack": bom_stack,
						"wip_warehouse": wip_wh,
						"total_fg_kg": flt(total_qty, 3),
						"raw_materials": raw_list,
					}
				)
		cur = []
		for r in self.get("fabric_batch_picks") or []:
			cur.append(
				{
					"work_order": _cstr(getattr(r, "work_order", None)),
					"item_code": _cstr(getattr(r, "item_code", None)),
					"batch_no": _cstr(getattr(r, "batch_no", None)),
					"qty": flt(getattr(r, "qty", None)),
				}
			)
		out["current_picks"] = cur
		out["needs_picks"] = bool(lines)
		out["lines"] = lines
		return out

	def _rm_shortages_from_exception(self, exc) -> list[tuple[str, str, float, float, float]]:
		"""Best-effort parse of ERPNext insufficient-stock message into shortage tuples."""
		msg = _cstr(exc)
		if not msg:
			return []
		# Example:
		# "0.994 units of Item MB - 1001222: ... needed in Warehouse Work In Progress - ... to complete this transaction."
		# or HTML-rich equivalent with <strong> / links.
		msg_plain = re.sub(r"<[^>]+>", " ", msg)
		msg_plain = re.sub(r"\s+", " ", msg_plain).strip()
		m = re.search(
			r"([0-9]+(?:\.[0-9]+)?)\s+units?\s+of\s+Item\s+([^:]+):.*?Warehouse\s+(.+?)\s+to\s+complete",
			msg_plain,
			flags=re.IGNORECASE,
		)
		if not m:
			return []
		short_qty = flt(m.group(1))
		item_code = _cstr(m.group(2)).strip()
		wh = _cstr(m.group(3)).strip()
		if not item_code or not wh or short_qty <= 0:
			return []
		# required/available unknown from exception text; provide safe fallback for draft transfer creation path.
		return [(item_code, wh, short_qty, 0.0, short_qty)]

	def _spr_insert_shortage_transfer_draft(self, se) -> str:
		"""Insert MTFM for shortages; auto-submit when stock is available."""
		self._spr_last_mtfm_error = ""
		name = None
		skip_cap_flag_backup = frappe.flags.get("spr_skip_wo_transfer_qty_validation")
		try:
			_spr_prepare_mtfm_stock_entry_for_submit(se)
			self._spr_apply_stock_entry_item_accounts(se)
			se.flags.ignore_mandatory = True
			se.flags.ignore_permissions = True
			frappe.flags.spr_skip_wo_transfer_qty_validation = True
			se.flags.ignore_validate_work_order = True
			se.insert(ignore_permissions=True)
			name = _cstr(se.name)
			self._persist_stock_entry_spr_reference_db(name)
			try:
				frappe.db.commit()
			except Exception:
				pass
			try:
				se.flags.ignore_permissions = True
				se.flags.ignore_validate = True
				se.submit()
			except Exception as submit_exc:
				submit_msg = _cstr(submit_exc)
				if "DocType" in submit_msg and "not found" in submit_msg:
					frappe.log_error(frappe.get_traceback(), f"SPR MTFM DocType-not-found:{self.name}")
					self._spr_last_mtfm_error = submit_msg
					return name
				# Retry once after filling accounts (expense_account is mandatory on some sites).
				if "Expense Account" in submit_msg:
					se.reload()
					self._spr_apply_stock_entry_item_accounts(se)
					se.flags.ignore_permissions = True
					se.flags.ignore_validate = True
					se.save()
					se.flags.ignore_validate = True
					se.submit()
				elif "Maximum transferable" in submit_msg or "Cannot transfer" in submit_msg:
					se.reload()
					plain_msg = re.sub(r"<[^>]+>", "", submit_msg)
					max_kg = _spr_parse_max_transferable_kg(plain_msg)
					if max_kg <= 0:
						se.work_order = None
						se.purpose = "Material Transfer"
						for d in se.items or []:
							if d.get("s_warehouse"):
								d.batch_no = ""
								if frappe.get_meta("Stock Entry Detail").has_field("use_serial_batch_fields"):
									d.use_serial_batch_fields = 0
						_spr_prepare_mtfm_stock_entry_for_submit(se, skip_batch=True)
						self._spr_apply_stock_entry_item_accounts(se)
						se.flags.ignore_permissions = True
						se.flags.ignore_validate = True
						se.save()
						kept = [
							d
							for d in (se.items or [])
							if flt(d.get("transfer_qty") or d.get("qty"))
							> _spr_rm_wip_shortage_tolerance(flt(d.get("transfer_qty") or d.get("qty")))
						]
						se.items = kept
						if not kept:
							try:
								frappe.delete_doc("Stock Entry", se.name, force=1)
							except Exception:
								pass
							self._spr_last_mtfm_error = plain_msg
							return ""
						se.flags.ignore_validate = True
						try:
							se.submit()
						except Exception as inner_exc:
							self._spr_last_mtfm_error = _cstr(inner_exc)
							return name
					else:
						row_m = re.search(r"Row #(\d+)", plain_msg, flags=re.IGNORECASE)
						row_idx = int(row_m.group(1)) - 1 if row_m else 0
						for i, d in enumerate(se.items or []):
							if not d.item_code or not d.get("s_warehouse"):
								continue
							req = flt(d.get("transfer_qty") or d.get("qty"))
							if i == row_idx:
								req = min(req, max_kg)
							_spr_finalize_mtfm_line_qty(d, d.s_warehouse, req)
						# Drop zero-qty lines — submit partial transfer when RM has some stock.
						kept = []
						for d in se.items or []:
							tol = _spr_rm_wip_shortage_tolerance(
								flt(d.get("transfer_qty") or d.get("qty"))
							)
							if flt(d.get("transfer_qty") or d.get("qty")) > tol:
								kept.append(d)
						se.items = kept
						if not kept:
							try:
								frappe.delete_doc("Stock Entry", se.name, force=1)
							except Exception:
								pass
							self._spr_last_mtfm_error = plain_msg
							return ""
						self._spr_apply_stock_entry_item_accounts(se)
						se.flags.ignore_permissions = True
						se.flags.ignore_validate = True
						se.save()
						se.flags.ignore_validate = True
						se.submit()
				else:
					raise
			# Commit immediately so draft/submitted entry survives frappe.throw rollback during SPR before_submit.
			try:
				frappe.db.commit()
			except Exception:
				pass
			# Force bin sync after commit to ensure stock is immediately queryable
			if name and frappe.db.exists("Stock Entry", name):
				try:
					self._force_sync_bins_for_stock_entry(name)
				except Exception:
					pass
				return name
		except Exception as exc:
			self._spr_last_mtfm_error = _cstr(exc)
			if "Expense Account" in _cstr(exc):
				frappe.throw(
					_(
						"Material Transfer could not be submitted: expense account is missing on one or more "
						"raw materials. Open each Item → Defaults and set Expense Account for company {0}, "
						"or set Company → Stock Adjustment Account, then submit SPR again."
					).format(_cstr(getattr(se, "company", None))),
					title=_("Expense Account required"),
				)
			frappe.log_error(
				frappe.get_traceback(),
				f"SPR shortage draft insert failed:{self.name}",
			)
			# Draft was already inserted — commit and return its name
			# so user gets a clickable link to manually submit it.
			if name and frappe.db.exists("Stock Entry", name):
				try:
					frappe.db.commit()
				except Exception:
					pass
				return name
		finally:
			frappe.flags.spr_skip_wo_transfer_qty_validation = skip_cap_flag_backup

		return ""

	def _transfer_for_manufacture_type_name(self) -> str:
		"""Resolve a valid Stock Entry Type for 'Material Transfer for Manufacture' purpose."""
		if frappe.db.exists("Stock Entry Type", "Material Transfer for Manufacture"):
			p = _cstr(frappe.db.get_value("Stock Entry Type", "Material Transfer for Manufacture", "purpose"))
			if p == "Material Transfer for Manufacture":
				return "Material Transfer for Manufacture"
		name = frappe.db.get_value("Stock Entry Type", {"purpose": "Material Transfer for Manufacture"}, "name")
		return _cstr(name) if name else "Material Transfer for Manufacture"

	def _reload_work_order_doc(self, wo_doc):
		wo_name = _cstr(getattr(wo_doc, "name", None)).strip()
		if not wo_name:
			return wo_doc
		try:
			return frappe.get_doc("Work Order", wo_name)
		except Exception:
			return wo_doc

	def _spr_company_warehouse_ctx(self) -> dict:
		"""Strict company RM (source) and WIP (target) warehouses for this SPR."""
		from production_entry.production_planning.spr_unit_warehouses import (
			_company_rm_warehouse,
			_company_wip_warehouse,
			resolve_spr_unit_manufacturing_warehouses,
		)

		company = _spr_company_from_doc(self)
		unit = _cstr(getattr(self, "custom_unit", None) or getattr(self, "unit", None))
		wh_ctx: dict = {}
		if unit:
			try:
				wh_ctx = resolve_spr_unit_manufacturing_warehouses(unit) or {}
			except Exception:
				wh_ctx = {}
		if not company:
			company = _cstr(wh_ctx.get("company"))
		wip_wh = _cstr(wh_ctx.get("wip_warehouse"))
		source_wh = _cstr(wh_ctx.get("source_warehouse"))
		if company:
			if not wip_wh or not _spr_wh_belongs_to_company(wip_wh, company):
				wip_wh = _spr_company_wip_warehouse(company) or _company_wip_warehouse(company)
			if not source_wh or not _spr_wh_belongs_to_company(source_wh, company):
				source_wh = _company_rm_warehouse(company, wip_wh=wip_wh) or _spr_company_rm_warehouse(
					company, wip_wh
				)
		return {
			"company": company,
			"wip_warehouse": wip_wh,
			"source_warehouse": source_wh,
		}

	def _resolve_rm_source_warehouse_for_transfer(self, wo_doc, item_code: str, wip_wh: str) -> str:
		"""RM store for MTFM lines (never WIP) — prefer SPR company RM warehouse."""
		item_code = _cstr(item_code).strip()
		wip_wh = _cstr(wip_wh).strip()
		ctx = self._spr_company_warehouse_ctx()
		company = _cstr(getattr(wo_doc, "company", None)).strip() or _cstr(ctx.get("company"))
		strict_rm = _spr_ok_rm_wh(ctx.get("source_warehouse"), wip_wh, company)
		if strict_rm:
			return strict_rm
		wo_doc = self._reload_work_order_doc(wo_doc)
		raw_source_wh = _spr_ok_rm_wh(getattr(wo_doc, "source_warehouse", None), wip_wh, company)
		src = _spr_wo_rm_source_warehouse(wo_doc, item_code, wip_wh)
		if src:
			return src
		if raw_source_wh:
			return raw_source_wh
		co_rm = _spr_company_rm_warehouse(company, wip_wh)
		if co_rm:
			return co_rm
		return ""

	def _append_mtfm_shortage_lines(
		self, se, wo_doc, short_by_item: dict, wip_wh: str, ignore_wo_transfer: bool = False
	) -> int:
		"""Append RM->WIP lines for shortage qty; returns count of lines added."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		ctx = self._spr_company_warehouse_ctx()
		wip_wh = _cstr(ctx.get("wip_warehouse")).strip() or _cstr(wip_wh).strip()
		is_bag = spr_doc_is_bag_spr(self)
		added = 0
		for item_code, short_qty in sorted((short_by_item or {}).items()):
			ic = _cstr(item_code).strip()
			if ignore_wo_transfer:
				qty = _spr_wip_topup_bump_qty(short_qty)
			else:
				qty = _spr_round_rm_stock_qty(short_qty)
				tol = _spr_rm_wip_shortage_tolerance(qty)
				if not ic or qty <= tol:
					continue
			if not ic or qty <= 0:
				continue
			if not ignore_wo_transfer:
				tol = _spr_rm_wip_shortage_tolerance(qty)
				still = _spr_wo_rm_transfer_remaining(wo_doc, ic)
				if still > tol:
					qty = min(qty, _spr_round_rm_stock_qty(still))
				elif is_bag and still <= tol:
					continue
			item_src = self._resolve_rm_source_warehouse_for_transfer(wo_doc, ic, wip_wh)
			company = _cstr(getattr(se, "company", None)).strip() or _cstr(ctx.get("company"))
			alt_wh, alt_avl = _spr_find_rm_warehouse_with_stock(company, ic, wip_wh, item_src, qty)
			if alt_wh and alt_avl > 0:
				item_src = alt_wh
			if not item_src:
				frappe.log_error(
					_("No RM source warehouse for {0} on WO {1}").format(ic, getattr(wo_doc, "name", "")),
					f"SPR MTFM draft no source:{self.name}",
				)
				continue
			stock_uom = frappe.db.get_value("Item", ic, "stock_uom") or "Nos"
			line = {
				"item_code": ic,
				"s_warehouse": item_src,
				"t_warehouse": wip_wh,
				"uom": stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": 1.0,
				"qty": qty,
				"transfer_qty": qty,
				"work_order": wo_doc.name,
			}
			company = _cstr(getattr(se, "company", None)).strip()
			exp_acc = _spr_resolve_expense_account(ic, company, item_src) if company else ""
			if exp_acc:
				line["expense_account"] = exp_acc
			cc = frappe.db.get_value("Company", company, "cost_center") if company else None
			if cc:
				line["cost_center"] = cc
			se.append("items", line)
			if se.items:
				qty = _spr_finalize_mtfm_line_qty(se.items[-1], item_src, qty)
			tol = _spr_rm_wip_shortage_tolerance(qty)
			if qty <= tol:
				se.items.pop()
				frappe.log_error(
					_(
						"No RM stock for {0} in {1} (need {2} Kg for WIP transfer). "
						"Transfer raw material to RM warehouse first."
					).format(ic, item_src, _spr_round_rm_stock_qty(short_qty)),
					f"SPR MTFM RM shortage:{self.name}",
				)
				continue
			added += 1
		return added

	def _mtfm_fg_qty_for_shortage_transfer(
		self, wo_doc, short_by_item: dict, chunk_total_qty: float
	) -> float:
		"""FG qty for extra-production / WIP top-up MTFM — must cover the RM shortage, not WO remaining."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		chunk_total_qty = flt(chunk_total_qty)
		wo_base = flt(getattr(wo_doc, "qty", 0))

		fg_from_short = 0.0
		bom_no = _cstr(getattr(wo_doc, "bom_no", None))
		if bom_no and short_by_item and wo_base > 0:
			full_rm_map, _multi = _bom_rm_stock_qty_map_for_fg(bom_no, wo_base)
			for ic, short_qty in (short_by_item or {}).items():
				full_need = flt(full_rm_map.get(_cstr(ic)))
				if full_need > 0 and flt(short_qty) > 0:
					fg_from_short = max(fg_from_short, (flt(short_qty) / full_need) * wo_base)

		target = max(chunk_total_qty, flt(fg_from_short) * 1.05, 1.0)
		return max(flt(target), 0.001)

	def _prune_short_by_item_to_wo_transfer_remaining(self, wo_doc, short_by_item: dict) -> dict:
		"""Keep only RM lines that still need transfer on the WO (prevents duplicate MTFM per item)."""
		wo_doc = self._reload_work_order_doc(wo_doc)
		out: dict[str, float] = {}
		for ic, qty in (short_by_item or {}).items():
			code = _cstr(ic).strip()
			if not code:
				continue
			still = _spr_wo_rm_transfer_remaining(wo_doc, code)
			tol = _spr_rm_wip_shortage_tolerance(flt(qty))
			if still <= tol:
				continue
			need = min(_spr_round_rm_stock_qty(flt(qty)), _spr_round_rm_stock_qty(still))
			if need > tol:
				out[code] = need
		return out

	def _spr_mtfm_stock_entry_filters(self) -> dict:
		filters = {"purpose": "Material Transfer for Manufacture"}
		meta = frappe.get_meta("Stock Entry")
		if meta.has_field("shaft_production_run"):
			filters["shaft_production_run"] = self.name
		elif frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			filters["custom_spr_reference"] = self.name
		else:
			return {}
		return filters

	def _find_recent_spr_wo_mtfm_transfer(self, wo_name: str, docstatus=None) -> str:
		"""Latest MTFM for this SPR + WO (draft or submitted) — avoids duplicate transfers on retry."""
		wo_name = _cstr(wo_name).strip()
		base = self._spr_mtfm_stock_entry_filters()
		if not wo_name or not base:
			return ""
		filters = dict(base)
		filters["work_order"] = wo_name
		if docstatus is not None:
			filters["docstatus"] = docstatus
		names = frappe.get_all(
			"Stock Entry",
			filters=filters,
			pluck="name",
			order_by="modified desc",
			limit=1,
		)
		return _cstr(names[0]) if names else ""

	def _shortage_events_still_blocking(self, shortage_events) -> list:
		"""Re-check WIP RM availability after MTFM — skip throw when transfers already satisfied need."""
		blocking = []
		for event in shortage_events or []:
			wo_doc = self._reload_work_order_doc(event.get("wo_doc"))
			if not wo_doc:
				continue
			chunk_total_qty = flt(event.get("chunk_total_qty"))
			if chunk_total_qty <= 0:
				continue
			preview_se = self._build_shortage_preview_for_chunk(wo_doc, chunk_total_qty)
			self._spr_cap_manufacture_rm_lines_to_wip_available(preview_se, wo_doc)
			shortages = self._rm_shortages_for_se(preview_se, wo_doc)
			wip_topup = self._spr_wip_topup_shortages_for_se(preview_se, wo_doc)
			wip_topup = self._spr_prune_wip_topup_when_rm_transferred(wo_doc, wip_topup)
			if shortages:
				blocking.append(
					{
						"wo_id": _cstr(event.get("wo_id")),
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total_qty,
						"shortages": shortages,
					}
				)
			if wip_topup:
				blocking.append(
					{
						"wo_id": _cstr(event.get("wo_id")),
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total_qty,
						"shortages": wip_topup,
						"wip_topup": True,
					}
				)
		return blocking

	def _new_mtfm_stock_entry_shell(
		self,
		wo_doc,
		chunk_total_qty: float,
		transfer_posting_date=None,
		transfer_posting_time=None,
		short_by_item=None,
	):
		wo_doc = self._reload_work_order_doc(wo_doc)
		ctx = self._spr_company_warehouse_ctx()
		company = _cstr(ctx.get("company")) or _cstr(getattr(wo_doc, "company", None))
		raw_source_wh = _cstr(ctx.get("source_warehouse")) or _cstr(getattr(wo_doc, "source_warehouse", None)) or ""
		wip_wh = _cstr(ctx.get("wip_warehouse")) or _cstr(getattr(wo_doc, "wip_warehouse", None)) or ""
		is_bag = spr_doc_is_bag_spr(self)
		se = frappe.new_doc("Stock Entry")
		se.company = company or wo_doc.company
		se.posting_date = transfer_posting_date or today()
		se.posting_time = transfer_posting_time or nowtime()
		se.set_posting_time = 1
		se.purpose = "Material Transfer for Manufacture"
		se.stock_entry_type = self._transfer_for_manufacture_type_name()
		se.work_order = wo_doc.name
		se.production_item = wo_doc.production_item
		if short_by_item:
			se.fg_completed_qty = self._mtfm_fg_qty_for_shortage_transfer(
				wo_doc, short_by_item, chunk_total_qty
			)
		else:
			se.fg_completed_qty = flt(chunk_total_qty) if flt(chunk_total_qty) > 0 else 1.0
		se.from_warehouse = None if is_bag else (raw_source_wh or None)
		se.wip_warehouse = wip_wh
		se.to_warehouse = wip_wh
		self._set_stock_entry_spr_link(se)
		_spr_enable_serial_batch_fields_on_se(se)
		return se, wip_wh

	def _create_wip_shortage_transfer_draft(self, wo_doc, chunk_total_qty: float, shortages: list[tuple[str, str, float, float, float]]) -> str:
		"""Create a draft Material Transfer for Manufacture for shortage items only."""
		if not wo_doc or not shortages:
			return ""
		wo_doc = self._reload_work_order_doc(wo_doc)
		# Use current day/time for shortage transfers to avoid backdated ledger insufficiency on old run dates.
		transfer_posting_date = today()
		transfer_posting_time = nowtime()
		wo_id = _cstr(getattr(wo_doc, "name", None))
		# Reuse existing draft for same WO + SPR to avoid duplicate drafts on retry.
		existing = self._find_open_wip_shortage_transfer_draft(wo_id)
		if existing:
			return existing
		wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None)) or ""
		if not wip_wh:
			return ""
		short_by_item = defaultdict(float)
		for item_code, _wh, _req, _avl, short_qty in shortages:
			if item_code and flt(short_qty) > 0:
				short_by_item[_cstr(item_code)] += flt(short_qty)
		if not short_by_item:
			return ""

		for ic in list(short_by_item.keys()):
			qty = _spr_round_rm_stock_qty(short_by_item.get(ic))
			if qty <= 0:
				short_by_item.pop(ic, None)
				continue
			short_by_item[ic] = qty
		original_short = dict(short_by_item)
		pruned = self._prune_short_by_item_to_wo_transfer_remaining(wo_doc, dict(short_by_item))
		merged = dict(pruned)
		for ic, qty in original_short.items():
			if ic in merged:
				continue
			if flt(qty) > 0:
				merged[ic] = _spr_wip_topup_bump_qty(qty)
		short_by_item = merged
		ignore_wo_transfer = len(merged) > len(pruned)
		if not short_by_item:
			return self._find_recent_spr_wo_mtfm_transfer(wo_id) or ""

		# One manual MTFM with all shortage RM lines (never BOM-driven — avoids per-item STE loops).
		se, _wip_b = self._new_mtfm_stock_entry_shell(
			wo_doc,
			chunk_total_qty,
			transfer_posting_date,
			transfer_posting_time,
			short_by_item=short_by_item,
		)
		se.from_bom = 0
		if not self._append_mtfm_shortage_lines(
			se, wo_doc, dict(short_by_item), wip_wh, ignore_wo_transfer=ignore_wo_transfer
		):
			return self._find_recent_spr_wo_mtfm_transfer(wo_id) or ""
		return self._spr_insert_shortage_transfer_draft(se)

	def _find_open_spr_shortage_transfer_draft(self) -> str:
		"""Find latest combined draft MTFM for this SPR."""
		filters = {
			"docstatus": 0,
			"purpose": "Material Transfer for Manufacture",
		}
		meta = frappe.get_meta("Stock Entry")
		if meta.has_field("shaft_production_run"):
			filters["shaft_production_run"] = self.name
		elif frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			filters["custom_spr_reference"] = self.name
		else:
			return ""
		names = frappe.get_all("Stock Entry", filters=filters, pluck="name", order_by="modified desc", limit=1)
		return _cstr(names[0]) if names else ""

	def _create_combined_spr_shortage_transfer_draft(
		self, shortage_events, ignore_wo_transfer_prune: bool = False
	) -> str:
		"""One draft Material Transfer for Manufacture with all RM shortages (PP, LD, fabric, etc.)."""
		if not shortage_events:
			return ""
		existing = self._find_open_spr_shortage_transfer_draft()
		if existing:
			return existing

		short_map: dict[str, float] = defaultdict(float)
		item_meta: dict[str, tuple] = {}
		item_wo: dict[str, object] = {}
		item_wip_topup: dict[str, bool] = {}
		primary_wo_doc = None
		chunk_max = 0.0

		for event in shortage_events or []:
			wo_doc = event.get("wo_doc")
			is_wip_topup = bool(event.get("wip_topup"))
			if wo_doc and not primary_wo_doc:
				primary_wo_doc = self._reload_work_order_doc(wo_doc)
			chunk_max = max(chunk_max, flt(event.get("chunk_total_qty")))
			for item_code, wh, req, avl, short_qty in event.get("shortages") or []:
				ic = _cstr(item_code).strip()
				if not ic or not wo_doc or flt(short_qty) <= 0:
					continue
				short_map[ic] += flt(short_qty)
				item_wo[ic] = self._reload_work_order_doc(wo_doc)
				item_wip_topup[ic] = item_wip_topup.get(ic) or is_wip_topup
				if ic not in item_meta:
					item_meta[ic] = (_cstr(wh), flt(req), flt(avl))

		if not short_map or not primary_wo_doc:
			return ""

		if ignore_wo_transfer_prune:
			pruned_map = {_cstr(ic): _spr_round_rm_stock_qty(qty) for ic, qty in short_map.items()}
		else:
			pruned_map: dict[str, float] = {}
			item_wo_pruned: dict[str, object] = {}
			for ic, qty in short_map.items():
				wo_for_ic = item_wo.get(ic) or primary_wo_doc
				wo_ref = self._reload_work_order_doc(wo_for_ic)
				if item_wip_topup.get(ic):
					bumped = _spr_wip_topup_bump_qty(flt(qty))
					if bumped > _spr_rm_wip_shortage_tolerance(bumped):
						pruned_map[ic] = bumped
						item_wo_pruned[ic] = wo_ref
					continue
				pruned = self._prune_short_by_item_to_wo_transfer_remaining(wo_ref, {ic: flt(qty)})
				if pruned.get(ic):
					pruned_map[ic] = pruned[ic]
					item_wo_pruned[ic] = wo_ref
			if not pruned_map:
				wo_ids = sorted(
					{
						_cstr(getattr(item_wo.get(ic) or primary_wo_doc, "name", None))
						for ic in short_map.keys()
					}
				)
				for wo_id in wo_ids:
					if wo_id:
						found = self._find_recent_spr_wo_mtfm_transfer(wo_id)
						if found:
							return found
				return ""
			item_wo = item_wo_pruned

		short_map = pruned_map

		# Combined manual STE: all shortage RM lines in one entry (PP + LD + fabric, etc.).
		try:
			se, wip_wh = self._new_mtfm_stock_entry_shell(
				primary_wo_doc,
				chunk_max if chunk_max > 0 else 1.0,
				short_by_item=short_map,
			)
			se.from_bom = 0
			per_wo_short: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
			for ic, qty in short_map.items():
				wo_for_ic = item_wo.get(ic) or primary_wo_doc
				wo_key = _cstr(getattr(wo_for_ic, "name", None)).strip() or "__primary__"
				per_wo_short[wo_key][ic] += flt(qty)
			added_total = 0
			for _wo_key, ic_map in per_wo_short.items():
				sample_ic = next(iter(ic_map.keys()), "")
				wo_ref = item_wo.get(sample_ic) or primary_wo_doc
				added_total += self._append_mtfm_shortage_lines(
					se,
					wo_ref,
					dict(ic_map),
					wip_wh,
					ignore_wo_transfer=bool(
						ignore_wo_transfer_prune or item_wip_topup.get(sample_ic)
					),
				)
			if added_total > 0:
				name = self._spr_insert_shortage_transfer_draft(se)
				if name:
					return name
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR combined MTFM draft:{self.name}")

		agg_shortages = [
			(ic, item_meta[ic][0], item_meta[ic][1], item_meta[ic][2], short_map[ic])
			for ic in sorted(short_map.keys())
		]
		return self._create_wip_shortage_transfer_draft(
			primary_wo_doc,
			chunk_max if chunk_max > 0 else 1.0,
			agg_shortages,
		)

	def _find_open_wip_shortage_transfer_draft(self, wo_name: str) -> str:
		"""Find latest draft transfer-for-manufacture for this WO and SPR."""
		wo_name = _cstr(wo_name)
		if not wo_name:
			return ""
		filters = {
			"docstatus": 0,
			"work_order": wo_name,
			"purpose": "Material Transfer for Manufacture",
		}
		meta = frappe.get_meta("Stock Entry")
		if meta.has_field("shaft_production_run"):
			filters["shaft_production_run"] = self.name
		elif frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			filters["custom_spr_reference"] = self.name
		names = frappe.get_all("Stock Entry", filters=filters, pluck="name", order_by="modified desc", limit=1)
		return _cstr(names[0]) if names else ""

	def _find_open_manufacture_draft_for_wo(self, wo_id: str) -> str:
		"""Find latest draft Manufacture Stock Entry for this WO + SPR (avoid duplicate STE on retry)."""
		wo_id = _cstr(wo_id)
		if not wo_id:
			return ""
		filters: dict = {"docstatus": 0, "purpose": "Manufacture"}
		meta = frappe.get_meta("Stock Entry")
		has_spr_link = False
		if meta.has_field("shaft_production_run"):
			filters["shaft_production_run"] = self.name
			has_spr_link = True
		elif frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			filters["custom_spr_reference"] = self.name
			has_spr_link = True
		if has_spr_link:
			filters["work_order"] = ["in", [wo_id, ""]]
		else:
			filters["work_order"] = wo_id
		names = frappe.get_all(
			"Stock Entry", filters=filters, pluck="name", order_by="modified desc", limit=1
		)
		if names:
			return _cstr(names[0])
		if has_spr_link:
			names = frappe.get_all(
				"Stock Entry",
				filters={"docstatus": 0, "purpose": "Manufacture", "work_order": wo_id},
				pluck="name",
				order_by="modified desc",
				limit=1,
			)
			return _cstr(names[0]) if names else ""
		return ""

	def _spr_submit_all_draft_manufactures_for_spr(self) -> list[str]:
		"""Submit open Manufacture drafts linked to this SPR instead of creating duplicates."""
		spr_clause, params = self._spr_stock_entry_spr_link_clause("se")
		if spr_clause == "1=0":
			return []
		names = frappe.db.sql_list(
			f"""
			SELECT se.name
			FROM `tabStock Entry` se
			WHERE {spr_clause}
			  AND IFNULL(se.purpose, '') = 'Manufacture'
			  AND IFNULL(se.docstatus, 0) = 0
			ORDER BY se.modified DESC
			""",
			params,
		) or []
		submitted: list[str] = []
		for se_name in names:
			se_name = _cstr(se_name).strip()
			if not se_name:
				continue
			try:
				se = frappe.get_doc("Stock Entry", se_name)
				if cint(se.docstatus) != 0 or _cstr(se.purpose) != "Manufacture":
					continue
				wo_id = _cstr(se.work_order).strip()
				self._set_stock_entry_spr_link(se)
				self._spr_ensure_manufacture_fg_bundles(se_name)
				_spr_mark_stock_entry_system_flags(se)
				se.submit()
				if wo_id:
					frappe.db.set_value("Stock Entry", se.name, "work_order", wo_id, update_modified=False)
				submitted.append(se.name)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"SPR submit draft manufacture:{self.name}:{se_name}",
				)
		return submitted

	def _build_shortage_preview_for_chunk(self, wo_doc, chunk_total_qty: float):
		"""Build a transient Manufacture entry for shortage pre-check without insert/submit."""
		posting_date = today()
		posting_time = nowtime()
		se = frappe.new_doc("Stock Entry")
		se.flags.ignore_duplicate_for_work_order = True
		se.company = wo_doc.company
		se.posting_date = posting_date
		se.posting_time = posting_time
		se.set_posting_time = 1
		se.stock_entry_type = self._manufacture_stock_entry_type_name()
		se.purpose = "Manufacture"
		se.work_order = None
		se.production_item = wo_doc.production_item
		se.fg_completed_qty = flt(chunk_total_qty)
		se.from_bom = 1
		se.bom_no = wo_doc.bom_no
		se.use_multi_level_bom = wo_doc.use_multi_level_bom
		se.wip_warehouse = wo_doc.wip_warehouse
		se.to_warehouse = wo_doc.fg_warehouse
		self._set_stock_entry_spr_link(se)
		self._set_stock_entry_unit(se, wo_doc)
		se.get_items()
		if spr_doc_is_bag_spr(self):
			_spr_apply_bag_rm_qty_from_bom(se, wo_doc.bom_no, se.fg_completed_qty)
		wip_warehouse = _cstr(getattr(wo_doc, "wip_warehouse", None))
		for item in se.items or []:
			if item.item_code and not item.get("t_warehouse"):
				item.s_warehouse = wip_warehouse
		return se

	def _raise_shortage_with_transfer(self, wo_id: str, wo_doc, chunk_total_qty: float, shortages):
		"""Create draft transfer, then throw a clear actionable shortage message."""
		transfer_name = ""
		transfer_err = ""
		try:
			transfer_name = self._create_wip_shortage_transfer_draft(wo_doc, chunk_total_qty, shortages)
			# Keep draft + submit atomic inside one request transaction.
		except Exception:
			transfer_err = _cstr(frappe.get_traceback())
			transfer_name = ""
		prec = _spr_rm_stock_qty_precision()
		lines = "\n".join(
			[
				_("{0} @ {1}: required {2}, available {3}, shortage {4}").format(
					it, wh or "—", flt(req, prec), flt(avl, prec), flt(sh, prec)
				)
				for it, wh, req, avl, sh in shortages[:20]
			]
		)
		next_steps = _(
			"1) Submit shortage transfer (each RM source warehouse -> WIP).\n"
			"2) Return to SPR and submit again."
		)
		if transfer_name:
			verify_line = (
				"2) Verify each line: source = item WO Required Items warehouse, target = WIP, qty in Kg matches WO.\n"
				if spr_doc_is_bag_spr(self)
				else "2) Verify source warehouse = Raw Materials and target warehouse = WIP, then submit.\n"
			)
			next_steps = _(
				'1) Open draft transfer: <a href="/app/stock-entry/{0}" target="_blank">{0}</a> '
				'(/app/stock-entry/{0})\n'
				"{1}"
				'3) Return to SPR: <a href="/app/shaft-production-run/{2}" target="_blank">{2}</a> and submit again.'
			).format(transfer_name, verify_line, self.name)
		elif transfer_err:
			next_steps = _(
				"Could not auto-create draft transfer on this site. "
				"Create 'Material Transfer for Manufacture' manually (Raw Materials -> WIP), submit it, then submit SPR again."
			)
		frappe.throw(
			_("Insufficient WIP stock for WO {0}.\n\n{1}\n\n{2}").format(wo_id, lines, next_steps),
			title=_("Insufficient stock"),
		)

	def _consolidated_shortage_lines(self, shortage_events) -> str:
		"""One line per item across all WOs (RM -> WIP)."""
		ctx = self._spr_company_warehouse_ctx()
		rm_wh = _cstr(ctx.get("source_warehouse")) or _("RM warehouse")
		wip_wh = _cstr(ctx.get("wip_warehouse")) or _("WIP warehouse")
		prec = _spr_rm_stock_qty_precision()
		merged: dict[str, dict] = {}
		for event in shortage_events or []:
			for item_code, _wh, req, avl, short_qty in event.get("shortages") or []:
				ic = _cstr(item_code).strip()
				if not ic:
					continue
				row = merged.setdefault(ic, {"req": 0.0, "avl": 0.0, "short": 0.0})
				row["req"] += flt(req)
				row["avl"] += flt(avl)
				row["short"] += flt(short_qty)
		if not merged:
			return ""
		lines = []
		for ic, row in sorted(merged.items()):
			lines.append(
				_(
					"{0}: move {1} from {2} to {3} (required {4}, available {5}, shortage {6})"
				).format(
					ic,
					flt(row["short"], prec),
					rm_wh,
					wip_wh,
					flt(row["req"], prec),
					flt(row["avl"], prec),
					flt(row["short"], prec),
				)
			)
		return "\n".join(lines)

	def _spr_no_rm_stock_lines_for_shortage_events(self, shortage_events) -> str:
		"""Items with WIP shortage but zero RM warehouse stock — operator must receive RM first."""
		prec = _spr_rm_stock_qty_precision()
		lines = []
		seen: set[str] = set()
		for event in shortage_events or []:
			wo_doc = self._reload_work_order_doc(event.get("wo_doc"))
			if not wo_doc:
				continue
			company = _cstr(getattr(wo_doc, "company", None))
			wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
			for item_code, wh, _req, _avl, short_qty in event.get("shortages") or []:
				ic = _cstr(item_code).strip()
				if not ic or ic in seen:
					continue
				need = _spr_wip_topup_bump_qty(short_qty)
				tol = _spr_rm_wip_shortage_tolerance(need)
				item_src = self._resolve_rm_source_warehouse_for_transfer(wo_doc, ic, wip_wh)
				rm_wh, rm_avl = _spr_find_rm_warehouse_with_stock(company, ic, wip_wh, item_src, need)
				if rm_avl + tol >= need:
					continue
				seen.add(ic)
				lines.append(
					_("{0}: need {1} Kg in RM store ({2}) — available {3} Kg").format(
						ic,
						flt(need, prec),
						rm_wh or wh or _("RM warehouse"),
						flt(rm_avl, prec),
					)
				)
		return "\n".join(lines[:20])

	def _raise_shortage_with_transfer_batch(
		self, shortage_events, ignore_wo_transfer_prune: bool = False
	):
		"""Create one Stock Entry per Work Order for shortages."""
		if not shortage_events:
			return

		if isinstance(shortage_events, list):
			if not ignore_wo_transfer_prune:
				shortage_events = self._filter_shortage_events_by_wo_transfer(shortage_events)
			
			grouped = {}
			for e in (shortage_events or []):
				wo = e.get("wo_id") or e.get("work_order")
				if not wo:
					continue
				if wo not in grouped:
					grouped[wo] = []
				grouped[wo].append(e)
			shortage_events = grouped

		if not shortage_events:
			return

		errors = []
		submitted_ses = []
		for wo_name, events in shortage_events.items():
			added_items = []
			try:
				wo = frappe.get_doc("Work Order", wo_name)
				
				try:
					se_type = self._transfer_for_manufacture_type_name()
				except Exception:
					se_type = "Material Transfer for Manufacture"
				
				se = frappe.new_doc("Stock Entry")
				se.purpose = "Material Transfer for Manufacture"
				se.stock_entry_type = se_type
				se.work_order = wo_name
				se.company = wo.company
				ctx = self._spr_company_warehouse_ctx()
				wo_company = _cstr(wo.company)
				wip_wh = _cstr(wo.wip_warehouse)
				if not _spr_wh_belongs_to_company(wip_wh, wo_company):
					wip_wh = _cstr(ctx.get("wip_warehouse")) or _spr_company_wip_warehouse(wo_company)
				source_wh = _cstr(ctx.get("source_warehouse"))
				if not _spr_wh_belongs_to_company(source_wh, wo_company):
					source_wh = ""
				se.from_warehouse = source_wh
				se.to_warehouse = wip_wh
				se.from_bom = 0
				
				chunk_total = sum(flt(e.get("chunk_total_qty")) for e in events)
				se.fg_completed_qty = chunk_total if chunk_total > 0 else 1.0
				
				for event in events:
					shortages = event.get("shortages") or []
					for it, wh, req, avl, need in shortages:
						if not it or flt(need) <= 0:
							continue
						still = _spr_wo_rm_transfer_remaining(wo, it)
						if still > 0:
							need = max(flt(need), flt(still))
						need = _spr_wip_topup_bump_qty(need)
						if flt(need) <= 0:
							continue
						stock_uom = frappe.db.get_value("Item", it, "stock_uom") or "Nos"
						item_src = self._resolve_rm_source_warehouse_for_transfer(wo, it, wip_wh)
						if not item_src:
							item_src = source_wh
						if not item_src:
							continue
						if not se.from_warehouse:
							se.from_warehouse = item_src
						se.append("items", {
							"item_code": it,
							"qty": need,
							"s_warehouse": item_src,
							"t_warehouse": wip_wh or wo.wip_warehouse,
							"uom": stock_uom,
							"stock_uom": stock_uom,
							"conversion_factor": 1.0,
							"transfer_qty": need,
						})
						added_items.append(it)
				
				if added_items:
					skip_cap_flag_backup = frappe.flags.get("spr_skip_wo_transfer_qty_validation")
					try:
						frappe.flags.spr_skip_wo_transfer_qty_validation = True
						se.flags.ignore_validate_work_order = True
						se.flags.ignore_permissions = True
						se.flags.ignore_mandatory = True
						se.insert(ignore_permissions=True)
						se.flags.ignore_permissions = True
						se.flags.ignore_validate = True
						se.submit()
						submitted_ses.append(f"<a href='/app/stock-entry/{se.name}' target='_blank'><b>{se.name}</b></a> for WO {wo_name}")
					except Exception as e:
						errors.append(f"Failed to create Stock Entry for Work Order {wo_name}: {e}")
					finally:
						frappe.flags.spr_skip_wo_transfer_qty_validation = skip_cap_flag_backup
			except Exception as e:
				error_msg = f"Failed to create Stock Entry for Work Order {wo_name}: {str(e)}"
				item_codes = ", ".join(list(set([str(i) for i in added_items])))
				errors.append(f"{error_msg} (Items: {item_codes})")

		if errors:
			frappe.throw("\n\n".join(errors), title=_("Shortage Transfer Failed"))

		if submitted_ses:
			try:
				frappe.db.commit()
			except Exception:
				pass
			msg = _("Shortages automatically handled via Material Transfers:\n\n{0}").format("\n".join(["- " + d for d in submitted_ses]))
			frappe.msgprint(msg, alert=True, indicator="green")
			return

		frappe.throw(
			_(
				"Raw material shortage detected for Work Order(s) but no Material Transfer for "
				"Manufacture could be created. Check RM stock in the Work Order source warehouse, "
				"complete pending transfers, then submit SPR again."
			),
			title=_("RM shortage — transfer required"),
		)

	def _manufacture_stock_entry_type_name(self) -> str:
		"""Resolve a valid Stock Entry Type name for Manufacture purpose."""
		# Prefer exact standard type label only when its mapped purpose is truly Manufacture.
		if frappe.db.exists("Stock Entry Type", "Manufacture"):
			p = _cstr(frappe.db.get_value("Stock Entry Type", "Manufacture", "purpose"))
			if p == "Manufacture":
				return "Manufacture"
		name = frappe.db.get_value("Stock Entry Type", {"purpose": "Manufacture"}, "name")
		if name:
			return _cstr(name)
		# Mandatory field on this site: fail fast with explicit setup message.
		frappe.throw(
			_(
				"Cannot create SPR Manufacture entry because no Stock Entry Type is mapped to purpose "
				"'Manufacture'. Please configure one in Stock Entry Type master."
			),
			title=_("Missing Manufacture Stock Entry Type"),
		)
		return ""

	def _stock_entry_type_name_for_purpose(self, purpose: str) -> str:
		"""Resolve a valid Stock Entry Type name for the given purpose."""
		purpose = _cstr(purpose)
		if not purpose:
			return ""
		if frappe.db.exists("Stock Entry Type", purpose):
			p = _cstr(frappe.db.get_value("Stock Entry Type", purpose, "purpose"))
			if p == purpose:
				return purpose
		name = frappe.db.get_value("Stock Entry Type", {"purpose": purpose}, "name")
		if name:
			return _cstr(name)
		frappe.throw(
			_(
				"Cannot create Stock Entry because no Stock Entry Type is mapped to purpose '{0}'. "
				"Please configure one in Stock Entry Type master."
			).format(purpose),
			title=_("Missing Stock Entry Type"),
		)
		return ""

	def _strip_finished_goods_from_stock_entry(self, se):
		"""Remove FG rows from BOM-generated Stock Entry and return them as templates.
		Catches FG items both via is_finished_item flag and via production_item match
		(ERPNext v15 may not set is_finished_item until validate() runs)."""
		items = se.get("items") or []
		production_item = _cstr(getattr(se, "production_item", None)).strip()
		fg_templates = []
		for i in range(len(items) - 1, -1, -1):
			item = items[i]
			is_fg = bool(getattr(item, "is_finished_item", 0))
			if not is_fg and production_item:
				# Fallback: treat as FG when item_code matches production_item AND
				# there is no source warehouse (FG lines go TO warehouse only).
				ic = _cstr(item.get("item_code")).strip()
				sw = _cstr(item.get("s_warehouse")).strip()
				is_fg = (ic == production_item and not sw)
			if is_fg:
				try:
					fg_templates.append(items[i].as_dict(no_default_fields=False))
				except Exception:
					fg_templates.append(dict(items[i].as_dict()))
				se.items.pop(i)
		fg_templates.reverse()
		return fg_templates

	def _get_batch_link_name_for_stock_entry(
		self,
		batch_id: str,
		item_code: str,
		company: str | None,
		spr_row=None,
	) -> str:
		"""
		Return tabBatch.name for Stock Entry Detail `batch_no` (Link).
		Frappe's savedocs validates every Link: the value must exist as Batch.name before insert().
		ERPNext Batch.autoname sets name = batch_id when batch_id is provided; use ERPNext make_batch when available.
		``spr_row`` fills Batch mandatory custom fields (net/gross weight, length) from the roll line.
		"""
		bid = _cstr(batch_id)
		if not bid:
			return ""

		batch_meta = frappe.get_meta("Batch")
		is_bag = spr_doc_is_bag_spr(self)
		roll_batch_data = _batch_fields_from_spr_row(batch_meta, spr_row, is_bag_spr=is_bag)

		def _existing_name() -> str | None:
			if frappe.db.exists("Batch", bid):
				it = frappe.db.get_value("Batch", bid, "item")
				if it == item_code:
					return bid
			nm = frappe.db.get_value(
				"Batch",
				{"item": item_code, "batch_id": bid},
				"name",
			)
			if nm:
				return nm
			return frappe.db.get_value("Batch", {"batch_id": bid, "item": item_code}, "name")

		found = _existing_name()
		if found:
			if roll_batch_data:
				try:
					frappe.db.set_value("Batch", found, roll_batch_data)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "SPR Batch roll fields on existing Batch")
			return found

		try:
			from erpnext.stock.doctype.batch.batch import make_batch

			kw = frappe._dict(
				item=item_code,
				batch_id=bid,
				manufacturing_date=self.run_date or today(),
			)
			if company:
				if batch_meta.has_field("company"):
					kw.company = company
			for fk, fv in roll_batch_data.items():
				kw[fk] = fv
			mb = make_batch(kw)
			if mb:
				return mb
		except ImportError:
			pass
		except Exception:
			found = _existing_name()
			if found:
				return found

		b = frappe.new_doc("Batch")
		b.batch_id = bid
		b.item = item_code
		if batch_meta.has_field("company") and company:
			b.company = company
		if batch_meta.has_field("manufacturing_date"):
			b.manufacturing_date = self.run_date or today()
		for fk, fv in roll_batch_data.items():
			b.set(fk, fv)
		try:
			b.insert(ignore_permissions=True)
			return b.name
		except Exception:
			found = _existing_name()
			if found:
				return found
			raise

	def _bundle_roll_numbers_from_text(self, raw_value) -> list[str]:
		"""Parse Bundle Stickers.roll_numbers into stable roll number strings."""
		out = []
		seen = set()
		for part in re.split(r"[,;\s]+", _cstr(raw_value)):
			val = part.strip()
			if not val:
				continue
			try:
				val = str(cint(val)) if str(cint(val)) == val or val.isdigit() else val
			except Exception:
				pass
			if val not in seen:
				seen.add(val)
				out.append(val)
		return out

	def _roll_batch_prefix_and_no(self, row) -> tuple[str, str]:
		bn = _cstr(row.get("batch_no"))
		rn = _cstr(row.get("roll_no"))
		prefix = ""
		batch_roll = ""
		if "/" in bn:
			prefix, batch_roll = [x.strip() for x in bn.rsplit("/", 1)]
		elif bn:
			prefix = bn
		return prefix, rn or batch_roll

	def _bundle_batch_id(self, sticker_row, roll_numbers: list[str]) -> str:
		raw_batch_no = _cstr(sticker_row.get("batch_no"))
		if re.match(r"^.+-B\d+(?:-\d+-\d+)?$", raw_batch_no):
			return raw_batch_no
		prefix = _spr_bundle_source_batch_prefix(raw_batch_no)
		if not prefix:
			return ""
		return f"{prefix}-B{cint(sticker_row.get('idx') or 0) or 1}"

	def _spr_submit_uses_bundle_packaging(self) -> bool:
		"""When True, Manufacture FG uses Bundle Stickers (combined) then unpacked rolls; when False, one FG per roll."""
		if frappe.db.has_column("Shaft Production Run", "custom_use_bundle_packaging_on_submit"):
			return cint(self.get("custom_use_bundle_packaging_on_submit") or 0) == 1
		return False

	def _bundle_fg_plans(self) -> list[dict]:
		"""Build bundle FG rows from Bundle Stickers and map them to source roll rows."""
		if not self._spr_submit_uses_bundle_packaging():
			return []
		plans = []
		used_roll_names = {}
		rows = list(self.items or [])
		row_by_key = {}
		for row in rows:
			prefix, roll_no = self._roll_batch_prefix_and_no(row)
			if prefix and roll_no:
				row_by_key[(prefix, roll_no)] = row

		for sticker in self.bundle_stickers or []:
			prefix = _spr_bundle_source_batch_prefix(sticker.get("batch_no"))
			roll_numbers = self._bundle_roll_numbers_from_text(sticker.get("roll_numbers"))
			if not prefix or not roll_numbers:
				continue

			source_rows = []
			missing = []
			for rn in roll_numbers:
				row = row_by_key.get((prefix, rn))
				if not row:
					missing.append(rn)
					continue
				source_rows.append(row)
			if missing:
				frappe.throw(
					_("Bundle Sticker row {0}: roll number(s) {1} not found for batch {2}.").format(
						cint(sticker.get("idx") or 0) or "?", ", ".join(missing), prefix
					),
					title=_("Bundle roll mismatch"),
				)

			for row in source_rows:
				row_name = _cstr(row.get("name"))
				if row_name in used_roll_names:
					frappe.throw(
						_("Roll {0} is included in more than one bundle sticker row ({1} and {2}).").format(
							row.get("roll_no") or row.get("batch_no"),
							used_roll_names[row_name],
							sticker.get("idx"),
						),
						title=_("Duplicate bundled roll"),
					)
				used_roll_names[row_name] = sticker.get("idx")

			source_net = flt(sum(self._row_fg_qty(r) for r in source_rows), 2)
			source_gross = flt(sum(flt(r.get("gross_weight")) for r in source_rows), 2)
			bundle_net = flt(sticker.get("sticker_bundle_weight") or source_net, 2)
			source_order_code = ""
			for r in source_rows:
				source_order_code = (
					_cstr(r.get("custom_order_code"))
					or _cstr(r.get("order_code"))
					or _cstr(r.get("custom_party_code_text"))
					or _cstr(r.get("party_code"))
				)
				if source_order_code:
					break
			if abs(bundle_net - source_net) > 0.05:
				frappe.throw(
					_("Bundle Sticker row {0}: bundle net {1} Kg does not match selected roll net {2} Kg.").format(
						cint(sticker.get("idx") or 0) or "?", bundle_net, source_net
					),
					title=_("Bundle weight mismatch"),
				)

			# Combo stickers (e.g. 30"+33") may span different WO/items — one FG plan per WO/item.
			grouped = OrderedDict()
			for r in source_rows:
				wo_key = _cstr(r.get("work_order") or r.get("wo_id"))
				ic_key = _cstr(r.get("item_code"))
				grouped.setdefault((wo_key, ic_key), []).append(r)

			sticker_gross = flt(sticker.get("sticker_bundle_gross_weight_kg") or source_gross, 2)
			produced_len = flt(
				sticker.get("produced_length_mtrs")
				or sticker.get("custom_produced_length_mtrs")
				or 0
			)
			multi_item = len(grouped) > 1
			for (work_order, item_code), group_rows in grouped.items():
				group_roll_nos = []
				for r in group_rows:
					rn = cint(r.get("roll_no") or 0)
					if rn > 0:
						group_roll_nos.append(str(rn))
					else:
						bn = _cstr(r.get("batch_no"))
						if bn and "-" in bn:
							group_roll_nos.append(bn.rsplit("-", 1)[-1])
				group_net = flt(sum(self._row_fg_qty(r) for r in group_rows), 2)
				group_gross = flt(sum(flt(r.get("gross_weight")) for r in group_rows), 2)
				plans.append(
					{
						"source_rows": group_rows,
						"source_names": {_cstr(r.get("name")) for r in group_rows},
						"first_idx": min([cint(r.get("idx") or 0) for r in group_rows] or [0]),
						"work_order": work_order,
						"item_code": item_code,
						"batch_no": self._bundle_batch_id(
							sticker, group_roll_nos if multi_item else roll_numbers
						),
						"net_weight": group_net if multi_item else bundle_net,
						"gross_weight": group_gross if multi_item else sticker_gross,
						"produced_length_mtrs": produced_len,
						"roll_numbers": ", ".join(group_roll_nos if multi_item else roll_numbers),
						"order_code": source_order_code,
						"party_code": source_order_code,
						"custom_party_code_text": source_order_code,
						"bundle_sticker_idx": sticker.get("idx"),
					}
				)
		return plans

	def _fg_posting_units_for_rows(self, spr_rows: list, wo_doc) -> list:
		"""Return FG posting units: bundles first (when enabled), then individual roll lines."""
		chunk_names = {_cstr(r.get("name")) for r in spr_rows or []}
		units = []
		covered = set()
		use_bundles = self._spr_submit_uses_bundle_packaging()
		for plan in self._bundle_fg_plans() if use_bundles else []:
			if _cstr(plan.get("work_order")) != _cstr(getattr(wo_doc, "name", "")):
				continue
			src = set(plan.get("source_names") or set())
			overlap = src & chunk_names
			if not overlap:
				continue
			if overlap != src:
				frappe.throw(
					_("Bundle Sticker row {0} was split across Stock Entry chunks. Reduce bundle size or increase WO overproduction allowance.").format(
						plan.get("bundle_sticker_idx") or "?"
					),
					title=_("Bundle split blocked"),
				)
			covered.update(src)
			units.append({"sort_idx": plan.get("first_idx") or 0, "row": plan, "is_bundle": True})

		for row in spr_rows or []:
			if _cstr(row.get("name")) in covered:
				continue
			units.append({"sort_idx": cint(row.get("idx") or 0), "row": row, "is_bundle": False})
		# Bundle FG lines first, then unpacked rolls (same sort_idx group).
		units.sort(key=lambda x: (cint(x.get("sort_idx") or 0), 0 if x.get("is_bundle") else 1))
		return [u["row"] for u in units]

	def _fg_posting_qty_for_rows(self, spr_rows: list, wo_doc) -> float:
		if spr_doc_is_bag_spr(self):
			bag_total = self._spr_bag_fg_posting_qty_for_wo(wo_doc, spr_rows)
			if bag_total > 0:
				return bag_total
		return sum(flt(self._row_fg_qty(unit)) for unit in self._fg_posting_units_for_rows(spr_rows, wo_doc))

	def _append_manufacture_fg_from_spr_rolls(self, se, wo_doc, spr_rows: list, fg_templates=None):
		"""Append FG rows: normal rolls individually, packed rolls as one Bundle Stickers row."""
		item_code = wo_doc.production_item
		has_batch = cint(frappe.db.get_value("Item", item_code, "has_batch_no"))
		stock_uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Kg"
		item_name = frappe.db.get_value("Item", item_code, "item_name")
		fg_templates = list(fg_templates or [])
		base_template = None
		for tpl in fg_templates:
			if _cstr(tpl.get("item_code")) == _cstr(item_code):
				base_template = tpl
				break
		if base_template is None and fg_templates:
			base_template = fg_templates[0]

		for spr in self._fg_posting_units_for_rows(spr_rows, wo_doc):
			qty = self._row_fg_qty(spr)
			if qty <= 0:
				continue
			bn_raw = spr.get("batch_no")
			bn = _cstr(bn_raw) if bn_raw is not None else ""
			if has_batch:
				if not bn:
					frappe.throw(
						_("Batch No is required on each roll line for batch-tracked item {0}").format(
							item_code
						),
						title=_("Missing Batch"),
					)
				se_batch = self._get_batch_link_name_for_stock_entry(
					bn, item_code, wo_doc.company, spr
				)
				if not se_batch:
					frappe.throw(
						_("Could not resolve Batch master for batch id {0}").format(bn),
						title=_("Batch"),
					)
			else:
				se_batch = ""

			row = {}
			if base_template:
				row.update(
					{
						k: v
						for k, v in base_template.items()
						if k
						not in {
							"name",
							"parent",
							"parenttype",
							"parentfield",
							"idx",
							"owner",
							"creation",
							"modified",
							"modified_by",
							"docstatus",
						}
					}
				)
			row.update(
				{
					"item_code": item_code,
					"item_name": item_name,
					"qty": qty,
					"transfer_qty": qty,
					"uom": row.get("uom") or stock_uom,
					"stock_uom": row.get("stock_uom") or stock_uom,
					"conversion_factor": flt(row.get("conversion_factor") or 1),
					"t_warehouse": wo_doc.fg_warehouse,
					"s_warehouse": "",
					"is_finished_item": 1,
				}
			)
			if has_batch and se_batch:
				row["batch_no"] = se_batch
				line_meta = frappe.get_meta("Stock Entry Detail")
				if line_meta.has_field("use_serial_batch_fields"):
					row["use_serial_batch_fields"] = 1
			elif "batch_no" in row:
				row["batch_no"] = ""
			se.append("items", row)
		_spr_enable_serial_batch_fields_on_se(se)

	def _spr_backfill_manufacture_fg_batches(self, se_name: str, wo_doc, spr_rows: list) -> None:
		"""Patch batch_no onto submitted Manufacture FG lines + SLE when ERPNext dropped them."""
		if not se_name or not frappe.db.exists("Stock Entry", se_name):
			return
		se_doc = frappe.get_doc("Stock Entry", se_name)
		item_code = _cstr(se_doc.get("production_item") or getattr(wo_doc, "production_item", None))
		
		# If the item cannot have a batch, skip batch generation entirely to prevent ERPNext ValidationError
		if item_code and not cint(frappe.db.get_value("Item", item_code, "has_batch_no")):
			return

		company = _cstr(se_doc.get("company") or getattr(wo_doc, "company", None))
		fg_lines = [d for d in (se_doc.items or []) if cint(d.get("is_finished_item")) == 1]
		if not fg_lines:
			return

		roll_by_qty: dict[str, list] = {}
		for row in spr_rows or []:
			bn = _cstr(getattr(row, "batch_no", "")).strip()
			if not bn:
				continue
			qty_key = f"{flt(self._row_fg_qty(row), 6)}"
			roll_by_qty.setdefault(qty_key, []).append(row)

		# Pre-seed used_batches with every batch already assigned to a real FG line so
		# any ghost (BOM-generated) FG line — which has no batch_no yet — cannot steal
		# one of those batches and create a duplicate SE detail row.
		used_batches: set = {
			_cstr(fg.get("batch_no")).strip()
			for fg in fg_lines
			if _cstr(fg.get("batch_no")).strip()
		}
		activated_batches: set = set()
		for fg in fg_lines:
			existing_bn = _cstr(fg.get("batch_no")).strip()
			if existing_bn:
				if existing_bn in activated_batches:
					continue
				activated_batches.add(existing_bn)
				_spr_activate_batch_from_manufacture(
					existing_bn, fg, se_doc, fallback_qty=flt(fg.get("qty"))
				)
				continue
			fg_qty = flt(fg.get("qty"))
			candidates = roll_by_qty.get(f"{flt(fg_qty, 6)}") or []
			matched_row = None
			for row in candidates:
				bn = _cstr(getattr(row, "batch_no", "")).strip()
				if bn and bn not in used_batches:
					matched_row = row
					break
			if not matched_row:
				for row in spr_rows or []:
					bn = _cstr(getattr(row, "batch_no", "")).strip()
					if bn and bn not in used_batches:
						matched_row = row
						break
			
			if not matched_row:
				continue
			bn_raw = _cstr(getattr(matched_row, "batch_no", "")).strip()
			used_batches.add(bn_raw)
			batch_link = self._get_batch_link_name_for_stock_entry(
				bn_raw, item_code, company, matched_row
			)
			if not batch_link:
				continue
			try:
				updates = {"batch_no": batch_link}
				line_meta = frappe.get_meta("Stock Entry Detail")
				if line_meta.has_field("use_serial_batch_fields"):
					updates["use_serial_batch_fields"] = 1
				frappe.db.set_value("Stock Entry Detail", fg.name, updates, update_modified=False)
				fg.batch_no = batch_link
				_spr_activate_batch_from_manufacture(
					batch_link, fg, se_doc, fallback_qty=fg_qty or self._row_fg_qty(matched_row)
				)
			except Exception:
				frappe.log_error(frappe.get_traceback(), f"SPR manufacture FG batch backfill:{se_name}")

	def _wo_submitted_manufacture_fg_qty(self, wo_id: str) -> float:
		"""Submitted Manufacture FG qty already posted against this WO."""
		if not wo_id:
			return 0.0
		return flt(
			frappe.db.sql(
				"""
				SELECT IFNULL(SUM(fg_completed_qty), 0)
				FROM `tabStock Entry`
				WHERE work_order = %s
				  AND IFNULL(purpose, '') = 'Manufacture'
				  AND docstatus = 1
				""",
				wo_id,
			)[0][0]
		)

	def _wo_overproduction_percent(self) -> float:
		"""Work Order overproduction allowance from Manufacturing Settings (safe fallback = 0)."""
		try:
			p = frappe.db.get_single_value("Manufacturing Settings", "overproduction_percentage_for_work_order")
			return max(flt(p), 0.0)
		except Exception:
			return 0.0

	def _wo_allowed_remaining_qty(self, wo_doc) -> tuple[float, float, float, float]:
		"""Return remaining allowed qty, allowed total, produced so far, overproduction %."""
		base_qty = flt(getattr(wo_doc, "qty", 0))
		over_pct = self._wo_overproduction_percent()
		allowed_total = base_qty * (1.0 + (over_pct / 100.0))
		already = max(flt(getattr(wo_doc, "produced_qty", 0)), self._wo_submitted_manufacture_fg_qty(wo_doc.name))
		remaining = max(allowed_total - already, 0.0)
		return remaining, allowed_total, already, over_pct

	def _wo_allowed_entry_qty(self, wo_doc) -> tuple[float, float]:
		"""Per-entry allowed FG qty from WO qty + Manufacturing Settings overproduction %."""
		base_qty = flt(getattr(wo_doc, "qty", 0))
		over_pct = self._wo_overproduction_percent()
		allowed = base_qty * (1.0 + (over_pct / 100.0))
		return flt(allowed), flt(over_pct)

	def _split_rows_by_qty_limit(self, rows: list, qty_limit: float) -> list[list]:
		"""Split SPR rows into chunks where sum(_row_fg_qty) <= qty_limit."""
		if qty_limit <= 0:
			return [rows]
		chunks: list[list] = []
		cur: list = []
		cur_total = 0.0
		for r in rows:
			q = flt(self._row_fg_qty(r))
			if q <= 0:
				continue
			# Single row bigger than per-entry limit cannot be split safely.
			if q > qty_limit + 1e-9:
				wo_id = _cstr(r.get("work_order") or r.get("wo_id"))
				frappe.throw(
					_(
						"Single roll row for WO {0} has qty {1} Kg, above per-entry allowed {2} Kg. "
						"Adjust roll qty/WO qty or overproduction %."
					).format(wo_id or "—", flt(q, 3), flt(qty_limit, 3)),
					title=_("WO quantity exceeded"),
				)
			if cur and (cur_total + q) > qty_limit + 1e-9:
				chunks.append(cur)
				cur = []
				cur_total = 0.0
			cur.append(r)
			cur_total += q
		if cur:
			chunks.append(cur)
		return chunks

	def _build_expected_rm_map_for_qty(self, wo_doc, fg_qty: float) -> dict[str, float]:
		"""Expected RM consumption map for a WO at given FG qty (item_code -> transfer_qty).

		Must use the **same** Stock Entry inputs as ``create_manufacturing_stock_entries`` before
		``get_items()``: ``work_order`` is left blank so ERPNext builds RM from BOM × ``fg_completed_qty``
		identically to submitted Manufacture entries. Setting ``work_order`` here would use a different
		validation/backflush path and skew split-entry variance checks. This doc is never inserted.
		"""
		fg_qty = flt(fg_qty)
		if fg_qty <= 0:
			return {}
		se = frappe.new_doc("Stock Entry")
		se.company = wo_doc.company
		se.posting_date = today()
		se.posting_time = nowtime()
		se.set_posting_time = 1
		se.stock_entry_type = self._manufacture_stock_entry_type_name()
		se.purpose = "Manufacture"
		# Match submitted SPR Manufacture entries: WO linked only after submit, not during get_items().
		se.work_order = None
		se.production_item = wo_doc.production_item
		se.fg_completed_qty = fg_qty
		se.from_bom = 1
		se.bom_no = wo_doc.bom_no
		se.use_multi_level_bom = wo_doc.use_multi_level_bom
		se.wip_warehouse = wo_doc.wip_warehouse
		se.to_warehouse = wo_doc.fg_warehouse
		se.get_items()
		if spr_doc_is_bag_spr(self):
			_spr_apply_bag_rm_qty_from_bom(se, wo_doc.bom_no, fg_qty)
		rm = defaultdict(float)
		for d in se.items or []:
			if d.item_code and not d.get("t_warehouse"):
				rm[d.item_code] += flt(d.get("transfer_qty") or d.get("qty"))
		return dict(rm)

	def _collect_rm_map_from_se(self, se) -> dict[str, float]:
		"""Actual RM map from generated Stock Entry items (item_code -> transfer_qty)."""
		rm = defaultdict(float)
		for d in se.items or []:
			if d.item_code and not d.get("t_warehouse"):
				rm[d.item_code] += flt(d.get("transfer_qty") or d.get("qty"))
		return dict(rm)

	def _merge_rm_maps(self, target: dict[str, float], delta: dict[str, float]) -> dict[str, float]:
		for item_code, qty in (delta or {}).items():
			target[item_code] = flt(target.get(item_code)) + flt(qty)
		return target

	def _validate_rm_split_variance(
		self,
		wo_id: str,
		fg_total_qty: float,
		expected_rm: dict[str, float],
		actual_rm: dict[str, float],
		wo_doc=None,
	):
		"""Ensure split-entry RM consumption matches BOM-expected RM for this FG qty (same path as phantom SE)."""
		under_consume: dict[str, float] = {}
		issues = []
		for item_code in sorted(set(expected_rm or {}) | set(actual_rm or {})):
			exp = flt((expected_rm or {}).get(item_code))
			act = flt((actual_rm or {}).get(item_code))
			diff = abs(act - exp)
			threshold = max(0.01, abs(exp) * 0.001)  # 0.1% or 0.01 qty floor
			if diff <= threshold + 1e-9:
				continue
			if act < exp - threshold:
				under_consume[_cstr(item_code).strip()] = _spr_wip_topup_bump_qty(exp - act)
			else:
				issues.append((item_code, exp, act, act - exp, threshold))
		if under_consume and wo_doc:
			shortage_events = [
				{
					"wo_id": wo_id,
					"wo_doc": wo_doc,
					"chunk_total_qty": fg_total_qty,
					"shortages": [
						(
							ic,
							self._resolve_rm_source_warehouse_for_transfer(
								wo_doc, ic, _cstr(getattr(wo_doc, "wip_warehouse", None))
							)
							or _spr_company_rm_warehouse(
								_cstr(getattr(wo_doc, "company", None)),
								_cstr(getattr(wo_doc, "wip_warehouse", None)),
							)
							or _("RM warehouse"),
							flt((expected_rm or {}).get(ic)),
							flt((actual_rm or {}).get(ic)),
							qty,
						)
						for ic, qty in under_consume.items()
						if ic and qty > 0
					],
					"wip_topup": True,
				}
			]
			self._raise_shortage_with_transfer_batch(shortage_events, ignore_wo_transfer_prune=True)
		if not issues:
			return
		details = "\n".join(
			[
				_("{0}: expected {1}, actual {2}, delta {3}, tolerance {4}").format(
					it, flt(exp, 6), flt(act, 6), flt(delta, 6), flt(tol, 6)
				)
				for it, exp, act, delta, tol in issues[:20]
			]
		)
		frappe.throw(
			_(
				"RM consumption mismatch for WO {0} (FG qty {1}) after split Manufacture entries. "
				"Submission aborted for safety.\n\n{2}"
			).format(wo_id, flt(fg_total_qty, 3), details),
			title=_("RM split variance"),
		)

	def _validate_fg_roll_coverage_for_wo(self, wo_doc, spr_rows: list, se_names: list[str]):
		"""Guard: every produced SPR FG roll must be represented in submitted Manufacture entries.
		Only raises when actual FG posted is LESS than expected (missed rolls).
		Excess FG (e.g. BOM FG line surviving alongside SPR rolls) is silently ignored."""
		if not wo_doc or not se_names:
			return
		# Leftover rolls may already have warehouse stock (auto Manufacture / Material Receipt).
		if not self._spr_rows_needing_manufacture(wo_doc, spr_rows, extra_se_names=se_names):
			return
		item_code = _cstr(getattr(wo_doc, "production_item", None))
		if not item_code:
			return
		has_batch = cint(frappe.db.get_value("Item", item_code, "has_batch_no"))
		expected_total = 0.0
		expected_by_batch = defaultdict(float)
		for spr in self._fg_posting_units_for_rows(spr_rows, wo_doc):
			qty = flt(self._row_fg_qty(spr))
			if qty <= 0:
				continue
			expected_total += qty
			if has_batch:
				bn_raw = _cstr(spr.get("batch_no"))
				if not bn_raw:
					continue
				se_batch = self._get_batch_link_name_for_stock_entry(bn_raw, item_code, wo_doc.company, spr)
				if se_batch:
					expected_by_batch[_cstr(se_batch)] += qty
		rows = frappe.db.sql(
			"""
			SELECT IFNULL(sed.batch_no, '') AS batch_no, IFNULL(sed.qty, 0) AS qty
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent
			WHERE sed.parent IN %(parents)s
			  AND IFNULL(se.docstatus, 0) = 1
			  AND IFNULL(se.purpose, '') = 'Manufacture'
			  AND IFNULL(sed.is_finished_item, 0) = 1
			  AND sed.item_code = %(item_code)s
			""",
			{"parents": tuple(se_names), "item_code": item_code},
			as_dict=True,
		) or []
		actual_total = 0.0
		actual_by_batch = defaultdict(float)
		for r in rows:
			q = flt(r.get("qty"))
			actual_total += q
			b = _cstr(r.get("batch_no"))
			if b:
				actual_by_batch[b] += q
		# Only block when rolls are MISSED (actual < expected).
		# When actual > expected the BOM-generated FG line survived alongside SPR rolls —
		# that is a duplicate posting issue logged separately; do not stop the submit here.
		if actual_total < expected_total - 1e-6:
			frappe.throw(
				_(
					"WO {0}: SPR roll total {1} Kg but only {2} Kg was posted to Manufacture entries. "
					"Some rolls may have been missed — please check and retry."
				).format(wo_doc.name, flt(expected_total, 3), flt(actual_total, 3)),
				title=_("FG roll coverage mismatch"),
			)
		if actual_total > expected_total + 1e-6:
			frappe.log_error(
				f"SPR {self.name} WO {wo_doc.name}: Manufacture FG total {flt(actual_total,3)} Kg "
				f"> SPR roll total {flt(expected_total,3)} Kg (BOM FG line may have survived alongside "
				f"SPR roll lines in SEs: {se_names}). Submit continues.",
				"SPR FG overshoot"
			)
		if has_batch:
			missing_batches = []   # batches where actual < expected (truly missed)
			excess_batches  = []   # batches where actual > expected (ghost duplication)
			for b, exp in expected_by_batch.items():
				act = flt(actual_by_batch.get(b))
				if act < exp - 1e-6:
					missing_batches.append((b, exp, act))
				elif act > exp + 1e-6:
					excess_batches.append((b, exp, act))
			if excess_batches:
				frappe.log_error(
					"SPR {0} WO {1}: batch quantities are higher than expected (BOM ghost FG line "
					"may have been back-filled with an SPR batch). Batches: {2}".format(
						self.name, wo_doc.name,
						"; ".join(
							"Batch {0}: expected {1} got {2}".format(b, flt(e,3), flt(a,3))
							for b, e, a in excess_batches[:10]
						),
					),
					"SPR batch overshoot",
				)
			if missing_batches:
				details = "\n".join(
					[_("Batch {0}: expected {1} Kg, posted {2} Kg").format(b, flt(e, 3), flt(a, 3)) for b, e, a in missing_batches[:20]]
				)
				frappe.throw(
					_(
						"WO {0}: some produced roll batches were not fully posted to Manufacture entries.\n\n{1}"
					).format(wo_doc.name, details),
					title=_("Missing FG roll batches"),
				)

	def _sync_work_order_produced_qty_from_submitted_manufacture(self, wo_id: str):
		"""Recompute WO produced_qty from submitted Manufacture entries."""
		if not wo_id:
			return
		total = flt(
			frappe.db.sql(
				"""
				SELECT IFNULL(SUM(fg_completed_qty), 0)
				FROM `tabStock Entry`
				WHERE work_order = %s
				  AND IFNULL(purpose, '') = 'Manufacture'
				  AND docstatus = 1
				""",
				wo_id,
			)[0][0]
		)
		frappe.db.set_value("Work Order", wo_id, "produced_qty", total, update_modified=False)

	def _sync_work_order_required_item_progress(self, wo_id: str):
		"""Recompute WO required-items consumed/transferred qty from submitted Stock Entries."""
		if not wo_id or not frappe.db.exists("Work Order", wo_id):
			return
		wo_meta = frappe.get_meta("Work Order")
		if not wo_meta.has_field("required_items"):
			return
		req_df = wo_meta.get_field("required_items")
		req_dt = _cstr(getattr(req_df, "options", None))
		if not req_dt:
			return
		req_meta = frappe.get_meta(req_dt)
		if not req_meta.has_field("item_code"):
			return

		consumed_map = {}
		try:
			rows = frappe.db.sql(
				"""
				SELECT sed.item_code, IFNULL(SUM(IFNULL(sed.transfer_qty, sed.qty)), 0) AS qty
				FROM `tabStock Entry` se
				INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE se.work_order = %(wo)s
				  AND se.docstatus = 1
				  AND IFNULL(se.purpose, '') IN ('Manufacture', 'Material Consumption for Manufacture')
				  AND IFNULL(sed.s_warehouse, '') != ''
				  AND IFNULL(sed.t_warehouse, '') = ''
				GROUP BY sed.item_code
				""",
				{"wo": wo_id},
				as_dict=True,
			) or []
			consumed_map = {_cstr(r.item_code): flt(r.qty) for r in rows if _cstr(r.item_code)}
		except Exception:
			consumed_map = {}

		transferred_map = {}
		try:
			rows = frappe.db.sql(
				"""
				SELECT sed.item_code, IFNULL(SUM(IFNULL(sed.transfer_qty, sed.qty)), 0) AS qty
				FROM `tabStock Entry` se
				INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE se.work_order = %(wo)s
				  AND se.docstatus = 1
				  AND IFNULL(se.purpose, '') = 'Material Transfer for Manufacture'
				  AND IFNULL(sed.s_warehouse, '') != ''
				  AND IFNULL(sed.t_warehouse, '') != ''
				GROUP BY sed.item_code
				""",
				{"wo": wo_id},
				as_dict=True,
			) or []
			transferred_map = {_cstr(r.item_code): flt(r.qty) for r in rows if _cstr(r.item_code)}
		except Exception:
			transferred_map = {}

		wo_doc = frappe.get_doc("Work Order", wo_id)
		for row in wo_doc.get("required_items") or []:
			item_code = _cstr(getattr(row, "item_code", None))
			if not item_code:
				continue
			if req_meta.has_field("consumed_qty"):
				next_consumed = flt(consumed_map.get(item_code, 0))
				if abs(flt(getattr(row, "consumed_qty", 0)) - next_consumed) > 1e-9:
					frappe.db.set_value(req_dt, row.name, "consumed_qty", next_consumed, update_modified=False)
			if req_meta.has_field("transferred_qty"):
				next_transferred = flt(transferred_map.get(item_code, 0))
				if abs(flt(getattr(row, "transferred_qty", 0)) - next_transferred) > 1e-9:
					frappe.db.set_value(req_dt, row.name, "transferred_qty", next_transferred, update_modified=False)

	def _sync_production_plan_progress_from_work_orders(self, production_plan: str):
		"""Sync Production Plan item/header produced qty from Work Order produced_qty."""
		pp = _cstr(production_plan)
		if not pp or not frappe.db.exists("Production Plan", pp):
			return
		pp_meta = frappe.get_meta("Production Plan")
		ppi_meta = frappe.get_meta("Production Plan Item")
		ppi_has_produced = ppi_meta.has_field("produced_qty")
		pp_rows = frappe.get_all(
			"Production Plan Item",
			filters={"parent": pp, "parenttype": "Production Plan", "parentfield": "po_items"},
			fields=["name", "item_code", "produced_qty"] if ppi_has_produced else ["name", "item_code"],
		) or []
		if not pp_rows:
			return
		wo_rows = frappe.get_all(
			"Work Order",
			filters={"production_plan": pp, "docstatus": ["<", 2]},
			fields=["name", "production_plan_item", "production_item", "produced_qty"],
		) or []
		produced_by_ppi = defaultdict(float)
		produced_by_item = defaultdict(float)
		for wo in wo_rows:
			produced = flt(wo.get("produced_qty"))
			ppi = _cstr(wo.get("production_plan_item"))
			item_code = _cstr(wo.get("production_item"))
			if ppi:
				produced_by_ppi[ppi] += produced
			elif item_code:
				produced_by_item[item_code] += produced
		pp_item_code_count = defaultdict(int)
		for pr in pp_rows:
			pp_item_code_count[_cstr(pr.get("item_code"))] += 1
		total_produced = 0.0
		for pr in pp_rows:
			row_name = _cstr(pr.get("name"))
			item_code = _cstr(pr.get("item_code"))
			next_val = produced_by_ppi.get(row_name)
			if next_val is None and item_code and pp_item_code_count.get(item_code) == 1:
				next_val = produced_by_item.get(item_code, 0.0)
			if next_val is None:
				next_val = 0.0
			next_val = flt(next_val, 3)
			total_produced += next_val
			if ppi_has_produced:
				cur_val = flt(pr.get("produced_qty"))
				if abs(cur_val - next_val) > 1e-9:
					frappe.db.set_value("Production Plan Item", row_name, "produced_qty", next_val, update_modified=False)
		for f in ("produced_qty", "total_produced_weight", "custom_total_produced_weight"):
			if pp_meta.has_field(f):
				cur = flt(frappe.db.get_value("Production Plan", pp, f) or 0)
				if abs(cur - total_produced) > 1e-9:
					frappe.db.set_value("Production Plan", pp, f, total_produced, update_modified=False)

	def _spr_stock_entry_spr_link_clause(self, se_alias: str = "se") -> tuple[str, dict]:
		"""SQL WHERE fragment matching Stock Entry rows linked to this SPR (any link field)."""
		parts: list[str] = []
		params: dict = {"spr": self.name}
		meta = frappe.get_meta("Stock Entry")
		if frappe.db.has_column("Stock Entry", "custom_spr_reference"):
			parts.append(f"IFNULL({se_alias}.custom_spr_reference, '') = %(spr)s")
		if meta.has_field("shaft_production_run"):
			parts.append(f"IFNULL({se_alias}.shaft_production_run, '') = %(spr)s")
		if parts:
			return "(" + " OR ".join(parts) + ")", params
		# Fallback: use manufacturing_entries on this SPR (do not call
		# _get_existing_submitted_manufacture_entries_for_spr — that method
		# delegates here and would recurse infinitely).
		names = [
			x.strip()
			for x in _cstr(self.get("manufacturing_entries")).split(",")
			if x and x.strip()
		]
		if names:
			params["spr_se_names"] = tuple(names)
			return f"{se_alias}.name IN %(spr_se_names)s", params
		return "1=0", params

	def _spr_manufacture_se_where_clause(
		self,
		se_alias: str,
		wo_id: str,
		extra_se_names: list | tuple | None = None,
	) -> tuple[str, dict]:
		"""WHERE fragment for submitted Manufacture Stock Entry rows for this SPR + WO."""
		spr_clause, params = self._spr_stock_entry_spr_link_clause(se_alias)
		params["wo"] = _cstr(wo_id)
		extra = [x for x in (extra_se_names or []) if _cstr(x).strip()]
		if extra:
			params["extra_se"] = tuple(extra)
			wo_clause = (
				f"(IFNULL({se_alias}.work_order, '') = %(wo)s OR {se_alias}.name IN %(extra_se)s)"
			)
		else:
			wo_clause = f"IFNULL({se_alias}.work_order, '') = %(wo)s"
		if spr_clause == "1=0":
			link_clause = wo_clause
			oc = self._resolve_spr_order_code()
			if oc:
				meta = frappe.get_meta("Stock Entry")
				for fn in ("order_code", "custom_order_code", "custom_party_code", "party_code"):
					if meta.has_field(fn):
						params["spr_order_code"] = oc
						link_clause = (
							f"({wo_clause}) AND IFNULL({se_alias}.{fn}, '') = %(spr_order_code)s"
						)
						break
		else:
			link_clause = f"({spr_clause}) AND {wo_clause}"
		return link_clause, params

	def _validate_all_planned_wos_manufactured(self, planned_wo_posts: list, created_entries_by_wo: dict) -> None:
		"""Ensure every planned WO has manufacture posting for all its SPR rows."""
		missing = []
		hints: list[str] = []
		backfill_errors = getattr(self.flags, "_spr_mfg_backfill_errors", None) or {}
		for plan in planned_wo_posts or []:
			wo_id = _cstr(plan.get("wo_id"))
			wo_doc = plan.get("wo_doc")
			rows = plan.get("rows") or []
			if not wo_id or not wo_doc:
				continue
			extra_se = created_entries_by_wo.get(wo_id) or []
			need_rows = self._spr_rows_needing_manufacture(wo_doc, rows, extra_se_names=extra_se)
			if need_rows:
				need_batches = [
					_cstr(r.get("batch_no")).strip()
					for r in need_rows
					if _cstr(r.get("batch_no")).strip()
				]
				detail = f"{wo_id} ({len(need_rows)} roll(s))"
				if need_batches:
					detail += ": " + ", ".join(need_batches[:8])
				missing.append(detail)
				if extra_se:
					hints.append(
						_("WO {0}: Manufacture entry {1} was created but {2} roll batch(es) were not recognised.").format(
							wo_id, ", ".join(extra_se[:3]), len(need_rows)
						)
					)
			elif not created_entries_by_wo.get(wo_id):
				# Rolls may already be posted from a prior submit attempt (no SPR link on Stock Entry).
				if self._spr_rows_needing_manufacture(wo_doc, rows, extra_se_names=None):
					missing.append(wo_id)
			if backfill_errors.get(wo_id):
				hints.append(f"{wo_id}: {_cstr(backfill_errors[wo_id])[:500]}")
		if missing:
			msg = _(
				"SPR submit blocked: Manufacture Stock Entry was not created for Work Order(s): {0}. "
				"All SPR rows mapped to a WO must post to Manufacture — check raw-material stock in "
				"the Work Order WIP warehouse, complete any Material Transfer for Manufacture, then retry."
			).format(", ".join(missing))
			if hints:
				msg += "\n\n" + "\n".join(hints[:8])
			frappe.throw(msg, title=_("Missing Manufacture entries"))

	def _spr_manufacture_total_fg_qty_for_wo(
		self, wo_id: str, item_code: str, extra_se_names: list | tuple | None = None
	) -> float:
		"""Total FG qty on draft/submitted Manufacture entries for this SPR + WO."""
		wo_id = _cstr(wo_id)
		item_code = _cstr(item_code)
		if not wo_id or not item_code:
			return 0.0
		link_clause, params = self._spr_manufacture_se_where_clause("se", wo_id, extra_se_names)
		params["item_code"] = item_code
		return flt(
			frappe.db.sql(
				f"""
				SELECT IFNULL(SUM(sed.qty), 0)
				FROM `tabStock Entry` se
				INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE {link_clause}
				  AND IFNULL(se.purpose, '') = 'Manufacture'
				  AND IFNULL(se.docstatus, 0) < 2
				  AND IFNULL(sed.is_finished_item, 0) = 1
				  AND IFNULL(sed.item_code, '') = %(item_code)s
				""",
				params,
			)[0][0]
			or 0
		)

	def _spr_manufacture_batch_qty_map_for_wo(
		self, wo_id: str, extra_se_names: list | tuple | None = None
	) -> dict[str, float]:
		"""Sum draft/submitted Manufacture FG qty per batch for this SPR + WO (no duplicates)."""
		wo_id = _cstr(wo_id)
		if not wo_id:
			return {}
		out: dict[str, float] = defaultdict(float)
		link_clause, params = self._spr_manufacture_se_where_clause("se", wo_id, extra_se_names)
		rows = frappe.db.sql(
			f"""
			SELECT IFNULL(sed.batch_no, '') AS batch_no, IFNULL(SUM(sed.qty), 0) AS qty
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			WHERE {link_clause}
			  AND IFNULL(se.purpose, '') = 'Manufacture'
			  AND IFNULL(se.docstatus, 0) < 2
			  AND IFNULL(sed.is_finished_item, 0) = 1
			  AND IFNULL(sed.batch_no, '') != ''
			GROUP BY IFNULL(sed.batch_no, '')
			""",
			params,
			as_dict=True,
		) or []
		for r in rows:
			bn = _cstr(r.get("batch_no")).strip()
			if not bn:
				continue
			qty = flt(r.get("qty"))
			out[bn] += qty
			try:
				batch_id = frappe.db.get_value("Batch", bn, "batch_id")
				if batch_id and _cstr(batch_id) != bn:
					out[_cstr(batch_id)] += qty
			except Exception:
				pass
		# Manufacture FG for this WO even when the Stock Entry is missing the SPR link.
		unlinked = frappe.db.sql(
			"""
			SELECT IFNULL(sed.batch_no, '') AS batch_no, IFNULL(SUM(sed.qty), 0) AS qty
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
			WHERE IFNULL(se.work_order, '') = %(wo)s
			  AND IFNULL(se.purpose, '') = 'Manufacture'
			  AND IFNULL(se.docstatus, 0) < 2
			  AND IFNULL(sed.is_finished_item, 0) = 1
			  AND IFNULL(sed.batch_no, '') != ''
			GROUP BY IFNULL(sed.batch_no, '')
			""",
			{"wo": wo_id},
			as_dict=True,
		) or []
		for r in unlinked:
			bn = _cstr(r.get("batch_no")).strip()
			if not bn:
				continue
			qty = flt(r.get("qty"))
			out[bn] = max(flt(out.get(bn)), qty)
			try:
				batch_id = frappe.db.get_value("Batch", bn, "batch_id")
				if batch_id and _cstr(batch_id) != bn:
					out[_cstr(batch_id)] = max(flt(out.get(_cstr(batch_id))), qty)
			except Exception:
				pass
		return dict(out)

	def _spr_roll_has_active_manufacture_stock(self, row, wo_doc) -> bool:
		"""True when this roll already has Manufacture FG or warehouse stock — do not post again."""
		rq = flt(self._row_fg_qty(row))
		if rq <= 0 or not wo_doc:
			return False
		bn_raw = _cstr(row.get("batch_no")).strip()
		if not bn_raw:
			return False
		item_code = _cstr(getattr(wo_doc, "production_item", None) or row.get("item_code") or "")
		try:
			b_link = _cstr(
				self._get_batch_link_name_for_stock_entry(
					bn_raw, item_code, getattr(wo_doc, "company", None), row
				)
			)
		except Exception:
			b_link = bn_raw
		key = b_link or bn_raw
		wo_id = _cstr(getattr(wo_doc, "name", None))
		posted = self._spr_manufacture_batch_qty_map_for_wo(wo_id, extra_se_names=None)
		if max(flt(posted.get(key)), flt(posted.get(bn_raw))) + 1e-6 >= rq:
			return True
		fg_wh = _cstr(getattr(wo_doc, "fg_warehouse", None))
		sle_qty = _spr_batch_sle_qty(key, item_code, fg_wh)
		if sle_qty + 1e-6 < rq:
			sle_qty = _spr_batch_sle_qty(key, item_code, "")
		if sle_qty + 1e-6 < rq:
			sle_qty = _spr_batch_sle_qty(key, "", "")
		return sle_qty + 1e-6 >= rq

	def _spr_rows_needing_manufacture(
		self, wo_doc, rows: list, extra_se_names: list | tuple | None = None
	) -> list:
		"""SPR rows whose batch/qty is not yet fully posted on a submitted Manufacture entry."""
		if not wo_doc or not rows:
			return []
		wo_id = _cstr(getattr(wo_doc, "name", None))
		item_code = _cstr(getattr(wo_doc, "production_item", None))
		posted = self._spr_manufacture_batch_qty_map_for_wo(wo_id, extra_se_names=extra_se_names)
		need: list = []
		allocated = dict(posted)
		for r in rows:
			rq = flt(self._row_fg_qty(r))
			if rq <= 0:
				continue
			if self._spr_roll_has_active_manufacture_stock(r, wo_doc):
				continue
			bn_raw = _cstr(r.get("batch_no")).strip()
			b_link = ""
			if bn_raw and item_code:
				try:
					b_link = _cstr(
						self._get_batch_link_name_for_stock_entry(
							bn_raw, item_code, wo_doc.company, r
						)
					)
				except Exception:
					b_link = bn_raw
			if b_link or bn_raw:
				avail = max(flt(allocated.get(b_link, 0)), flt(allocated.get(bn_raw, 0)))
				if avail + 1e-6 >= rq:
					key = b_link if flt(allocated.get(b_link, 0)) >= flt(allocated.get(bn_raw, 0)) else bn_raw
					if not key:
						key = b_link or bn_raw
					allocated[key] = avail - rq
					continue
				need.append(r)
			else:
				need.append(r)
		if need:
			expected_total = flt(
				sum(flt(self._row_fg_qty(r)) for r in rows if flt(self._row_fg_qty(r)) > 0)
			)
			posted_total = self._spr_manufacture_total_fg_qty_for_wo(
				wo_id, item_code, extra_se_names=extra_se_names
			)
			if posted_total + 1e-6 >= expected_total:
				return []
		return need

	def _spr_run_backfill_manufacture_for_wo(
		self,
		wo_id: str,
		wo_doc,
		missing_rows: list,
		created_entries: list,
		created_entries_by_wo: dict,
		savepoint_name: str = "spr_mfg_backfill",
		raise_on_shortage: bool = False,
	) -> str | None:
		"""Transfer RM if needed, then submit one Manufacture entry for missing rows only."""
		if not missing_rows or not wo_doc:
			return None
		wo_doc = self._reload_work_order_doc(wo_doc)
		chunk_total = self._fg_posting_qty_for_rows(missing_rows, wo_doc)
		if chunk_total <= 0:
			return None

		for attempt in range(2):
			shortage_events: list = []
			preview_se = self._build_shortage_preview_for_chunk(wo_doc, chunk_total)
			self._spr_cap_manufacture_rm_lines_to_wip_available(preview_se, wo_doc)
			shortages = self._rm_shortages_for_se(preview_se, wo_doc)
			if shortages:
				shortage_events.append(
					{
						"wo_id": wo_id,
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total,
						"shortages": shortages,
					}
				)
			wip_topup = self._spr_wip_topup_shortages_for_se(preview_se, wo_doc)
			if wip_topup:
				shortage_events.append(
					{
						"wo_id": wo_id,
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total,
						"shortages": wip_topup,
						"wip_topup": True,
					}
				)
			if shortage_events:
				shortage_events = self._spr_filter_preflight_shortage_events(shortage_events)
				try:
					self._raise_shortage_with_transfer_batch(shortage_events)
					wo_doc = self._reload_work_order_doc(wo_doc)
				except Exception:
					if raise_on_shortage:
						raise
					if attempt == 0:
						frappe.log_error(
							frappe.get_traceback(),
							f"SPR backfill transfer:{self.name}:{wo_id}",
						)
					break

			frappe.db.savepoint(savepoint_name)
			actual_rm_map: dict = {}
			try:
				done = self._spr_run_manufacture_chunk_attempt(
					wo_id=wo_id,
					wo_doc=wo_doc,
					chunk_rows=missing_rows,
					chunk_total_qty=chunk_total,
					chunk_idx=1,
					chunk_count=1,
					mfg_submit_savepoint=savepoint_name,
					planned_wo_posts=[],
					actual_rm_map=actual_rm_map,
					created_entries=created_entries,
					created_entries_by_wo=created_entries_by_wo,
					allow_wip_topup_retry=(attempt == 0),
				)
				return _cstr(done.get("se_name"))
			except _SprWipTopupRetry:
				wo_doc = self._reload_work_order_doc(wo_doc)
				continue
			except Exception as exc:
				try:
					frappe.db.rollback(save_point=savepoint_name)
				except Exception:
					pass
				if raise_on_shortage:
					raise
				if not hasattr(self.flags, "_spr_mfg_backfill_errors"):
					self.flags._spr_mfg_backfill_errors = {}
				self.flags._spr_mfg_backfill_errors[wo_id] = _cstr(exc)
				frappe.log_error(
					frappe.get_traceback(),
					f"SPR backfill manufacture:{self.name}:{wo_id}",
				)
				break
		return None

	def _spr_submit_backfill_missing_manufactures(
		self,
		planned_wo_posts: list,
		created_entries: list,
		created_entries_by_wo: dict,
	) -> None:
		"""After main manufacture loop, post any rows still missing a Manufacture entry."""
		for plan in planned_wo_posts or []:
			wo_id = _cstr(plan.get("wo_id"))
			wo_doc = plan.get("wo_doc")
			rows = plan.get("rows") or []
			if not wo_id or not wo_doc:
				continue
			existing_this_pass = created_entries_by_wo.get(wo_id) or []
			missing_rows = self._spr_rows_needing_manufacture(
				wo_doc, rows, extra_se_names=existing_this_pass
			)
			if not missing_rows:
				continue
			# Leftover rolls (often 1 extra-production row after the first chunk) still need
			# their own Manufacture entry. extra_se_names keeps already-posted batches out.
			self._spr_run_backfill_manufacture_for_wo(
				wo_id,
				wo_doc,
				missing_rows,
				created_entries,
				created_entries_by_wo,
				savepoint_name=_spr_db_savepoint_name("spr_mfg_backfill", wo_id),
				raise_on_shortage=False,
			)

	def _spr_auto_post_leftover_roll_stock(
		self,
		planned_wo_posts: list,
		created_entries: list,
		created_entries_by_wo: dict,
	) -> None:
		"""On SPR submit, auto-create Stock Entry for leftover rolls (MTFM + Manufacture, then FG receipt)."""
		for plan in planned_wo_posts or []:
			wo_id = _cstr(plan.get("wo_id"))
			wo_doc = plan.get("wo_doc")
			rows = plan.get("rows") or []
			if not wo_id or not wo_doc:
				continue
			wo_doc = self._reload_work_order_doc(wo_doc)
			existing = created_entries_by_wo.get(wo_id) or []
			missing_rows = self._spr_rows_needing_manufacture(wo_doc, rows, extra_se_names=existing)
			if not missing_rows:
				continue

			chunk_total = self._fg_posting_qty_for_rows(missing_rows, wo_doc)
			preview_se = self._build_shortage_preview_for_chunk(wo_doc, chunk_total) if chunk_total > 0 else None
			shortage_events = []
			if preview_se:
				self._spr_cap_manufacture_rm_lines_to_wip_available(preview_se, wo_doc)
				shortages = self._rm_shortages_for_se(preview_se, wo_doc)
				if shortages:
					shortage_events.append(
						{
							"wo_id": wo_id,
							"wo_doc": wo_doc,
							"chunk_total_qty": chunk_total,
							"shortages": shortages,
						}
					)
				wip_topup = self._spr_wip_topup_shortages_for_se(preview_se, wo_doc)
				if wip_topup:
					shortage_events.append(
						{
							"wo_id": wo_id,
							"wo_doc": wo_doc,
							"chunk_total_qty": chunk_total,
							"shortages": wip_topup,
							"wip_topup": True,
						}
					)
			if shortage_events:
				shortage_events = self._spr_filter_preflight_shortage_events(shortage_events)
				try:
					self._raise_shortage_with_transfer_batch(shortage_events, ignore_wo_transfer_prune=True)
					wo_doc = self._reload_work_order_doc(wo_doc)
				except Exception:
					frappe.log_error(
						frappe.get_traceback(),
						f"SPR leftover auto-transfer:{self.name}:{wo_id}",
					)

			self._spr_run_backfill_manufacture_for_wo(
				wo_id,
				wo_doc,
				missing_rows,
				created_entries,
				created_entries_by_wo,
				savepoint_name=_spr_db_savepoint_name("spr_leftover_mfg", wo_id),
				raise_on_shortage=False,
			)
			wo_doc = self._reload_work_order_doc(wo_doc)
			existing = created_entries_by_wo.get(wo_id) or []
			still_missing = self._spr_rows_needing_manufacture(wo_doc, rows, extra_se_names=existing)
			if still_missing:
				receipt_name = self._spr_auto_material_receipt_leftover_rolls(wo_doc, still_missing)
				if receipt_name:
					frappe.msgprint(
						_("WO {0}: leftover roll(s) posted via Stock Entry {1}.").format(
							wo_id, receipt_name
						),
						alert=True,
					)

	def _spr_auto_material_receipt_leftover_rolls(self, wo_doc, missing_rows) -> str:
		"""Submit a Material Receipt so leftover FG batches have warehouse stock when Manufacture cannot post."""
		if not wo_doc or not missing_rows:
			return ""
		fg_wh = _cstr(getattr(wo_doc, "fg_warehouse", None))
		if not fg_wh:
			return ""
		company = _cstr(getattr(wo_doc, "company", None))
		receipt = frappe.new_doc("Stock Entry")
		receipt.company = company
		receipt.posting_date = today()
		receipt.posting_time = nowtime()
		receipt.set_posting_time = 1
		receipt.stock_entry_type = self._stock_entry_type_name_for_purpose("Material Receipt")
		receipt.purpose = "Material Receipt"
		receipt.remarks = _("Auto leftover FG receipt for {0}").format(self.name)
		self._set_stock_entry_spr_link(receipt)
		self._set_stock_entry_unit(receipt, wo_doc)

		added = 0
		batch_codes = []
		for row in missing_rows:
			qty = flt(self._row_fg_qty(row))
			if qty <= 0:
				continue
			item_code = _cstr(row.get("item_code") or getattr(wo_doc, "production_item", None)).strip()
			bn_raw = _cstr(row.get("batch_no")).strip()
			if not item_code or not bn_raw:
				continue
			try:
				batch_no = _cstr(
					self._get_batch_link_name_for_stock_entry(bn_raw, item_code, company, row)
				) or bn_raw
			except Exception:
				batch_no = bn_raw
			line = {
				"item_code": item_code,
				"qty": qty,
				"transfer_qty": qty,
				"uom": frappe.db.get_value("Item", item_code, "stock_uom") or "Kg",
				"stock_uom": frappe.db.get_value("Item", item_code, "stock_uom") or "Kg",
				"conversion_factor": 1,
				"t_warehouse": fg_wh,
				"batch_no": batch_no,
				"allow_zero_valuation_rate": 1,
			}
			receipt.append("items", line)
			batch_codes.append(batch_no)
			added += 1
		if not added:
			return ""
		try:
			receipt.flags.ignore_permissions = True
			receipt.flags.ignore_mandatory = True
			receipt.insert()
			self._persist_stock_entry_spr_reference_db(receipt.name)
			self._apply_order_code_to_submitted_stock_entry(receipt.name)
			receipt.flags.ignore_validate = True
			receipt.submit()
			self._refresh_batch_qty_for_codes(batch_codes)
			return _cstr(receipt.name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"SPR leftover FG receipt:{self.name}:{getattr(wo_doc, 'name', '')}",
			)
			return ""

	def _spr_sync_backfill_missing_manufacture(self) -> list[str]:
		"""Create/submit missing Manufacture entries for SPR rolls with auto RM transfer. Never throws.

		Repairs serial/batch bundles on existing Manufacture entries, then posts leftover rolls
		that are not yet on those entries (no duplicate batches).
		"""
		submitted_drafts = self._spr_submit_all_draft_manufactures_for_spr() or []
		existing_submitted = self._get_existing_submitted_manufacture_entries_for_spr()
		all_existing = sorted(set(existing_submitted + submitted_drafts))
		if all_existing:
			for se_name in all_existing:
				try:
					self._spr_ensure_manufacture_fg_bundles(se_name)
				except Exception:
					frappe.log_error(
						frappe.get_traceback(),
						f"SPR sync bundle repair:{self.name}:{se_name}",
					)

		wo_rows: dict[str, list] = defaultdict(list)
		for r in self.items or []:
			wo = _cstr(r.get("work_order") or r.get("wo_id"))
			if wo and flt(self._row_fg_qty(r)) > 0:
				wo_rows[wo].append(r)
		if not wo_rows:
			if all_existing:
				merged = [
					x.strip()
					for x in _cstr(self.get("manufacturing_entries")).split(",")
					if x and x.strip()
				]
				merged = sorted(set(merged + all_existing))
				if merged:
					self.db_set("manufacturing_entries", ", ".join(merged))
			return []

		created_se: list[str] = list(all_existing)
		created_entries_by_wo: dict[str, list] = defaultdict(list)
		for se_name in all_existing:
			wo = _cstr(frappe.db.get_value("Stock Entry", se_name, "work_order") or "")
			if wo:
				created_entries_by_wo[wo].append(se_name)

		self.flags._spr_allow_manufacture_posting = True
		try:
			for wo_id, rows in wo_rows.items():
				if not frappe.db.exists("Work Order", wo_id):
					continue
				wo_doc = frappe.get_doc("Work Order", wo_id)
				missing_rows = self._spr_rows_needing_manufacture(
					wo_doc, rows, extra_se_names=created_entries_by_wo.get(wo_id) or []
				)
				if not missing_rows:
					continue
				se_name = self._spr_run_backfill_manufacture_for_wo(
					wo_id,
					wo_doc,
					missing_rows,
					created_se,
					created_entries_by_wo,
					savepoint_name=_spr_db_savepoint_name("spr_sync_mfg", wo_id),
				)
				if se_name and se_name not in created_se:
					created_se.append(se_name)
		finally:
			self.flags._spr_allow_manufacture_posting = False

		if created_se:
			existing = [
				x.strip()
				for x in _cstr(self.get("manufacturing_entries")).split(",")
				if x and x.strip()
			]
			merged = sorted(set(existing + created_se))
			self.db_set("manufacturing_entries", ", ".join(merged))
		existing_set = set(all_existing)
		return [x for x in created_se if x not in existing_set]

	def _spr_remove_bom_fg_duplicates_from_se_db(self, se, production_item: str, spr_rows: list | None = None) -> int:
		"""After SE insert + reload, delete BOM-generated ghost FG lines from DB and in-memory se.items.

		Removes: (1) FG lines with empty batch_no when batched SPR roll lines exist;
		(2) duplicate FG lines sharing the same batch_no (keep lowest idx);
		(3) extra unbatched FG lines when batched line count already matches SPR roll count.
		Returns the number of rows deleted."""
		se_name = _cstr(getattr(se, "name", None) or se).strip()
		if not se_name or not production_item:
			return 0
		try:
			fg_rows = frappe.db.sql(
				"""SELECT name, idx, IFNULL(batch_no, '') AS batch_no, IFNULL(qty, 0) AS qty
				FROM `tabStock Entry Detail`
				WHERE parent = %s AND is_finished_item = 1 AND item_code = %s
				ORDER BY idx ASC""",
				(se_name, production_item),
				as_dict=True,
			) or []
			if not fg_rows:
				return 0

			expected_count = len(
				[r for r in (spr_rows or []) if flt(self._row_fg_qty(r)) > 0]
			) or len(fg_rows)
			to_delete: set[str] = set()
			seen_batch: set[str] = set()
			batch_first_row: dict[str, dict] = {}
			batched_kept = 0

			for row in fg_rows:
				bn = _cstr(row.get("batch_no")).strip()
				if bn:
					if bn in seen_batch:
						first = batch_first_row.get(bn)
						if first:
							merged_qty = flt(first.get("qty")) + flt(row.get("qty"))
							frappe.db.set_value(
								"Stock Entry Detail",
								first["name"],
								"qty",
								merged_qty,
								update_modified=False,
							)
							first["qty"] = merged_qty
						to_delete.add(row["name"])
					else:
						seen_batch.add(bn)
						batch_first_row[bn] = row
						batched_kept += 1
					continue
				# Empty batch: ghost when SPR already has batched roll lines.
				if batched_kept >= expected_count:
					to_delete.add(row["name"])
				elif seen_batch:
					to_delete.add(row["name"])

			# Drop any remaining unbatched extras beyond expected roll count.
			unbatched = [r for r in fg_rows if not _cstr(r.get("batch_no")).strip() and r["name"] not in to_delete]
			while unbatched and (len(fg_rows) - len(to_delete)) > expected_count:
				to_delete.add(unbatched.pop(0)["name"])

			if not to_delete:
				return 0

			frappe.db.sql(
				"DELETE FROM `tabStock Entry Detail` WHERE name IN %s",
				(tuple(to_delete),),
			)
			count = len(to_delete)
			frappe.log_error(
				f"SPR: removed {count} BOM-FG ghost/duplicate line(s) from SE {se_name} "
				f"(item {production_item}, expected {expected_count} roll lines).",
				"SPR BOM FG duplicate removed",
			)
			if hasattr(se, "items"):
				se.items = [
					d for d in (se.items or [])
					if _cstr(getattr(d, "name", None)) not in to_delete
				]
			return count
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR BOM FG dedup:{se_name}")
			return 0

	def _spr_run_manufacture_chunk_attempt(
		self,
		wo_id,
		wo_doc,
		chunk_rows,
		chunk_total_qty,
		chunk_idx,
		chunk_count,
		mfg_submit_savepoint,
		planned_wo_posts,
		actual_rm_map,
		created_entries,
		created_entries_by_wo,
		allow_wip_topup_retry: bool = True,
	):
		"""Build + submit one Manufacture Stock Entry; auto WIP top-up + retry when allowed."""
		pending_rows = self._spr_rows_needing_manufacture(
			wo_doc, chunk_rows, extra_se_names=created_entries_by_wo.get(wo_id) or []
		)
		if not pending_rows:
			return {"actual_rm_map": actual_rm_map, "se_name": None}
		chunk_rows = pending_rows
		chunk_total_qty = self._fg_posting_qty_for_rows(chunk_rows, wo_doc)
		if chunk_total_qty <= 0:
			return {"actual_rm_map": actual_rm_map, "se_name": None}

		draft_se_name = self._find_open_manufacture_draft_for_wo(wo_id)
		if draft_se_name:
			try:
				draft_se = frappe.get_doc("Stock Entry", draft_se_name)
				if cint(draft_se.docstatus) == 0 and _cstr(draft_se.purpose) == "Manufacture":
					self._set_stock_entry_spr_link(draft_se)
					self._set_stock_entry_unit(draft_se, wo_doc)
					self._spr_ensure_manufacture_fg_bundles(draft_se_name)
					_spr_mark_stock_entry_system_flags(draft_se)
					draft_se.submit()
					self._spr_backfill_manufacture_fg_batches(draft_se.name, wo_doc, chunk_rows)
					self._spr_ensure_manufacture_fg_bundles(draft_se.name)
					frappe.db.set_value("Stock Entry", draft_se.name, "work_order", wo_id, update_modified=False)
					self._apply_unit_to_submitted_stock_entry(draft_se.name, wo_doc)
					self._apply_order_code_to_submitted_stock_entry(draft_se.name)
					self._sync_work_order_produced_qty_from_submitted_manufacture(wo_id)
					self._sync_work_order_required_item_progress(wo_id)
					self._sync_production_plan_progress_from_work_orders(
						_cstr(getattr(wo_doc, "production_plan", None))
					)
					created_entries.append(draft_se.name)
					created_entries_by_wo[wo_id].append(draft_se.name)
					frappe.msgprint(
						_("WO {0}: Submitted existing draft Manufacture entry {1} ({2} Kg).").format(
							wo_id, draft_se.name, flt(chunk_total_qty, 3)
						),
						alert=True,
					)
					actual_rm_map = self._merge_rm_maps(
						actual_rm_map, self._collect_rm_map_from_se(draft_se)
					)
					return {"actual_rm_map": actual_rm_map, "se_name": draft_se.name}
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"SPR submit draft manufacture retry:{self.name}:{draft_se_name}",
				)

		se = frappe.new_doc("Stock Entry")
		se.flags.ignore_duplicate_for_work_order = True
		se.company = wo_doc.company
		se.posting_date = today()
		se.posting_time = nowtime()
		se.set_posting_time = 1
		se.stock_entry_type = self._manufacture_stock_entry_type_name()
		se.purpose = "Manufacture"
		se.work_order = None
		se.production_item = wo_doc.production_item
		se.fg_completed_qty = chunk_total_qty
		se.from_bom = 1
		se.bom_no = wo_doc.bom_no
		se.use_multi_level_bom = wo_doc.use_multi_level_bom
		se.wip_warehouse = wo_doc.wip_warehouse
		se.to_warehouse = wo_doc.fg_warehouse
		self._set_stock_entry_spr_link(se)
		self._set_stock_entry_unit(se, wo_doc)
		se.get_items()
		if spr_doc_is_bag_spr(self):
			_spr_apply_bag_rm_qty_from_bom(se, wo_doc.bom_no, chunk_total_qty)
		wip_warehouse = wo_doc.wip_warehouse
		for item in se.items or []:
			if not item.item_code:
				continue
			if not item.get("t_warehouse"):
				item.s_warehouse = wip_warehouse
				if item.s_warehouse != wip_warehouse:
					frappe.throw(
						_("Raw material {0} source warehouse is {1}, not {2}. ABORT.").format(
							item.item_code, item.s_warehouse, wip_warehouse
						),
						title=_("Warehouse Mismatch"),
					)
			elif item.t_warehouse != wo_doc.fg_warehouse:
				item.t_warehouse = wo_doc.fg_warehouse
		fg_templates = self._strip_finished_goods_from_stock_entry(se)
		self._append_manufacture_fg_from_spr_rolls(se, wo_doc, chunk_rows, fg_templates)
		self._spr_cap_manufacture_rm_lines_to_wip_available(se, wo_doc)
		self._assign_rm_batches_for_stock_entry(se, wo_id)
		self._spr_cap_manufacture_rm_lines_to_wip_available(se, wo_doc)
		_spr_enable_serial_batch_fields_on_se(se)
		self._spr_apply_stock_entry_item_accounts(se)
		se.stock_entry_type = self._manufacture_stock_entry_type_name()
		se.purpose = "Manufacture"
		_spr_mark_stock_entry_system_flags(se)
		se.insert(ignore_permissions=True)
		self._persist_stock_entry_spr_reference_db(se.name)
		if _cstr(se.purpose) != "Manufacture":
			frappe.throw(
				_("Stock Entry {0} resolved to purpose {1}; expected Manufacture.").format(
					se.name, _cstr(se.purpose) or "—"
				),
				title=_("Invalid Stock Entry purpose"),
			)
		se.reload()
		# ERPNext validate() may re-add the BOM FG line during insert; strip it again so
		# we don't end up with both a BOM FG line and the per-roll SPR FG lines.
		self._spr_remove_bom_fg_duplicates_from_se_db(se, wo_doc.production_item, chunk_rows)
		# Insert/reload can restore BOM RM qty and drop flags — recap 0.001 Kg drift before submit.
		if self._spr_cap_manufacture_rm_lines_to_wip_available(se, wo_doc):
			_spr_mark_stock_entry_system_flags(se)
			se.save()
		self._set_stock_entry_spr_link(se)
		self._set_stock_entry_unit(se, wo_doc)
		if _cstr(se.purpose) != "Manufacture":
			frappe.throw(
				_("Stock Entry {0} changed to purpose {1} after insert; expected Manufacture.").format(
					se.name, _cstr(se.purpose) or "—"
				),
				title=_("Invalid Stock Entry purpose"),
			)
		shortages: list = []
		_submit_exc: Exception | None = None
		_submit_exc_msg = ""
		try:
			_spr_mark_stock_entry_system_flags(se)
			try:
				mod = frappe.db.get_value("Stock Entry", se.name, "modified")
				if mod:
					se.modified = mod
			except Exception:
				pass
			se.submit()
			self._spr_backfill_manufacture_fg_batches(se.name, wo_doc, chunk_rows)
			self._spr_ensure_manufacture_fg_bundles(se.name)
		except Exception as e:
			_submit_exc = e  # persist before Python 3.12 deletes the except-clause variable
			_submit_exc_msg = _spr_exc_message(e)
			shortages = self._rm_shortages_for_se(se, wo_doc)
			if not shortages:
				shortages = self._rm_shortages_from_exception(e)
				if shortages:
					shortages = self._filter_shortages_by_wo_transfer_remaining(wo_doc, shortages)
		# WIP already has stock and WO is submitted — retry Manufacture, do not create RM transfers.
		if _submit_exc is not None and self._spr_wo_wip_covers_required(wo_doc):
			try:
				se_name = _cstr(getattr(se, "name", None))
				if se_name and frappe.db.exists("Stock Entry", se_name):
					se = frappe.get_doc("Stock Entry", se_name)
					if cint(se.docstatus) == 0:
						self._spr_cap_manufacture_rm_lines_to_wip_available(se, wo_doc)
						_spr_mark_stock_entry_system_flags(se)
						se.submit()
						self._spr_backfill_manufacture_fg_batches(se.name, wo_doc, chunk_rows)
						self._spr_ensure_manufacture_fg_bundles(se.name)
					_submit_exc = None
					_submit_exc_msg = ""
			except Exception as retry_exc:
				_submit_exc = retry_exc
				_submit_exc_msg = _spr_exc_message(retry_exc) or _submit_exc_msg
			if _submit_exc is not None:
				try:
					frappe.db.rollback(save_point=mfg_submit_savepoint)
				except Exception:
					pass
				self._throw_wip_stock_wo_transfer_mismatch(wo_doc, _submit_exc_msg)
		elif _submit_exc is not None:
			try:
				frappe.db.rollback(save_point=mfg_submit_savepoint)
			except Exception:
				pass
		if _submit_exc is None:
			# SUCCESS PATH — run all post-submit bookkeeping then return.
			frappe.db.set_value("Stock Entry", se.name, "work_order", wo_id, update_modified=False)
			self._apply_unit_to_submitted_stock_entry(se.name, wo_doc)
			self._apply_order_code_to_submitted_stock_entry(se.name)
			self._sync_work_order_produced_qty_from_submitted_manufacture(wo_id)
			self._sync_work_order_required_item_progress(wo_id)
			self._sync_production_plan_progress_from_work_orders(_cstr(getattr(wo_doc, "production_plan", None)))
			created_entries.append(se.name)
			created_entries_by_wo[wo_id].append(se.name)
			frappe.msgprint(
				_("WO {0}: Created {1}/{2} Manufacture entry {3} ({4} Kg).").format(
					wo_id, chunk_idx, chunk_count, se.name, flt(chunk_total_qty, 3)
				),
				alert=True,
			)
			actual_rm_map = self._merge_rm_maps(actual_rm_map, self._collect_rm_map_from_se(se))
			return {"actual_rm_map": actual_rm_map, "se_name": se.name}
		if shortages:
			submit_shortage_events = [
				{
					"wo_id": wo_id,
					"wo_doc": wo_doc,
					"chunk_total_qty": chunk_total_qty,
					"shortages": shortages,
				}
			]
			for p2 in planned_wo_posts:
				wo_id2 = p2["wo_id"]
				wo_doc2 = p2["wo_doc"]
				for chunk_rows2 in p2["row_chunks"]:
					chunk_total_qty2 = sum(self._row_fg_qty(r2) for r2 in chunk_rows2)
					if chunk_total_qty2 <= 0:
						continue
					preview_se2 = self._build_shortage_preview_for_chunk(wo_doc2, chunk_total_qty2)
					shortages2 = self._rm_shortages_for_se(preview_se2, wo_doc2)
					if shortages2:
						submit_shortage_events.append(
							{
								"wo_id": wo_id2,
								"wo_doc": wo_doc2,
								"chunk_total_qty": chunk_total_qty2,
								"shortages": shortages2,
							}
						)
					wip_topup2 = self._spr_wip_topup_shortages_for_se(preview_se2, wo_doc2)
					if wip_topup2:
						submit_shortage_events.append(
							{
								"wo_id": wo_id2,
								"wo_doc": wo_doc2,
								"chunk_total_qty": chunk_total_qty2,
								"shortages": wip_topup2,
								"wip_topup": True,
							}
						)
			self._raise_shortage_with_transfer_batch(submit_shortage_events)
			# Auto-transfers committed — reset savepoint and retry this WO's manufacture entry.
			if allow_wip_topup_retry:
				frappe.db.savepoint(mfg_submit_savepoint)
				raise _SprWipTopupRetry()
			# Second attempt still failing — fall through to mismatch error.
		# WO already shows RM transferred — auto RM->WIP transfer then retry Manufacture once.
		try:
			self._spr_try_wip_topup_transfer_and_retry_manufacture(
				wo_doc, _submit_exc, allow_wip_topup_retry, mfg_submit_savepoint, mfg_se=se
			)
		except _SprWipTopupRetry:
			raise
		# WIP top-up could not auto-submit — raise transfer / no-stock message (never cap below BOM).
		wip_topup = self._spr_wip_topup_shortages_for_se(se, wo_doc)
		if not wip_topup:
			wip_topup = self._spr_wip_topup_from_manufacture_se(se, wo_doc)
			if wip_topup:
				company = _cstr(getattr(wo_doc, "company", None))
				wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
				wip_topup = [
					(
						ic,
						self._resolve_rm_source_warehouse_for_transfer(wo_doc, ic, wip_wh)
						or _spr_company_rm_warehouse(company, wip_wh)
						or _("RM warehouse"),
						flt(qty),
						0.0,
						flt(qty),
					)
					for ic, qty in wip_topup.items()
					if ic and flt(qty) > 0
				]
		if wip_topup:
			self._raise_shortage_with_transfer_batch(
				[
					{
						"wo_id": wo_id,
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total_qty,
						"shortages": wip_topup,
						"wip_topup": True,
					}
				],
				ignore_wo_transfer_prune=True,
			)
			if allow_wip_topup_retry:
				frappe.db.savepoint(mfg_submit_savepoint)
				raise _SprWipTopupRetry()
		parsed_wip = self._rm_shortages_from_exception(_submit_exc)
		if parsed_wip:
			self._raise_shortage_with_transfer_batch(
				[
					{
						"wo_id": wo_id,
						"wo_doc": wo_doc,
						"chunk_total_qty": chunk_total_qty,
						"shortages": parsed_wip,
					}
				],
				ignore_wo_transfer_prune=True,
			)
			if allow_wip_topup_retry:
				frappe.db.savepoint(mfg_submit_savepoint)
				raise _SprWipTopupRetry()
		self._throw_wip_stock_wo_transfer_mismatch(wo_doc, _submit_exc_msg or _submit_exc)

	def create_manufacturing_stock_entries(self):
		"""Create submitted Manufacture Stock Entries from Roll Production Results (per WO / chunk).

		Operator flow (enforced in this method):

		1. **Before any Manufacture insert**: for every WO chunk, build a preview Manufacture entry and
		   check WIP raw-material stock. If anything is short, create a draft *Material Transfer for
		   Manufacture* (Raw Materials ΓåÆ WIP), ``commit`` it so it survives rollback, then throw with links.
		   After the operator submits that transfer, SPR submit can proceed.

		2. **Create / submit** Manufacture entries: each Stock Entry lists **one finished-good row per
		   roll/batch** (same item, different ``batch_no`` / qty) plus BOM raw materials — like a single
		   document with multiple FG lines (see standard Manufacture layout). Multiple Stock Entries are
		   created only when total FG for the WO exceeds the per-entry overproduction limit. Link WO after
		   submit, then sync each WO ``produced_qty`` and required-items ``consumed_qty`` /
		   ``transferred_qty`` from submitted Stock Entries, and sync Production Plan produced totals.

		3. **Guards**: no produced row without WO; FG roll coverage vs SPR rows must match so no silent
		   partial posting.

		Does not change Work Order ``qty`` (Qty To Manufacture). ERPNext caps Manufacture FG by WO qty
		plus Manufacturing Settings overproduction; if roll totals exceed that cap, raise overproduction
		percent or WO qty in ERPNext.
		"""
		if not cint(getattr(self.flags, "_spr_allow_manufacture_posting", 0)):
			frappe.throw(
				_(
					"Manufacture entries are allowed only during SPR submit. "
					"Click Submit on SPR after resolving any shortage transfer(s)."
				),
				title=_("Submit required"),
			)
		self._validate_production_submit_readiness()
		wo_groups = {}
		for row in self.items or []:
			wo_name = row.get("work_order") or row.get("wo_id")
			if not wo_name:
				continue
			wo_groups.setdefault(wo_name, []).append(row)
		if not wo_groups:
			positive_rows = [r for r in (self.items or []) if self._row_fg_qty(r) > 0]
			if positive_rows:
				frappe.throw(
					_(
						"SPR has produced rows, but none are linked to a Work Order. "
						"Map Work Order in Roll Production Results and submit again."
					),
					title=_("Missing Work Order mapping"),
				)
			frappe.throw(
				_(
					"Cannot submit SPR without any Work Order-linked production rows. "
					"Create Entry and ensure each row has Work Order plus produced weight "
					"(or achieved bag PCS for bag runs)."
				),
				title=_("No manufacturing rows"),
			)

		created_entries = []
		created_entries_by_wo = defaultdict(list)
		planned_wo_posts = []

		# Phase 1: validate all WO groups first (no Stock Entry insert/submit here).
		for wo_id, rows in wo_groups.items():
			wo_doc = frappe.get_doc("Work Order", wo_id)
			total_qty = self._fg_posting_qty_for_rows(rows, wo_doc)
			wo_item = _cstr(getattr(wo_doc, "production_item", None))
			if spr_doc_is_bag_spr(self):
				self._spr_validate_bag_fg_qty_for_wo(wo_doc, total_qty)
	
			# Ensure the produced item is batch managed
			if wo_item and not cint(frappe.db.get_value("Item", wo_item, "has_batch_no")):
				frappe.throw(
					_(
						"Item {0} for Work Order {1} is not batch-managed. "
						"Please enable 'Has Batch No' in the Item master before submitting the Shaft Production Run."
					).format(wo_item, wo_id),
					title=_("Item Not Batch Managed")
				)
	
			# Hard safety: one WO must not receive rows of other finished items.
			mismatch_items = sorted(
				{
					_cstr(r.get("item_code"))
					for r in rows
					if _cstr(r.get("item_code")) and _cstr(r.get("item_code")) != wo_item
				}
			)
			if mismatch_items:
				frappe.throw(
					_(
						"Work Order {0} produces item {1}, but this SPR has roll lines mapped to this WO with "
						"different item(s): {2}. Correct Available Jobs → Work Orders mapping before submit."
					).format(wo_id, wo_item or "—", ", ".join(mismatch_items)),
					title=_("Wrong WO mapping"),
				)
	
			if total_qty <= 0:
				skip_msg = (
					_("Skipping WO {0} — achieved bag PCS is 0").format(wo_id)
					if spr_doc_is_bag_spr(self)
					else _("Skipping WO {0} — net/gross weight is 0").format(wo_id)
				)
				frappe.msgprint(skip_msg, alert=True)
				continue
	
			allowed_entry_qty, over_pct = self._wo_allowed_entry_qty(wo_doc)
			row_chunks = self._split_rows_by_qty_limit(rows, allowed_entry_qty) or [rows]
			expected_rm_map = self._build_expected_rm_map_for_qty(wo_doc, total_qty)
			if len(row_chunks) > 1:
				frappe.msgprint(
					_(
						"WO {0}: SPR quantity {1} Kg exceeds per-entry limit {2} Kg "
						"(overproduction {3}%). Creating {4} Manufacture entries."
					).format(
						wo_id,
						flt(total_qty, 3),
						flt(allowed_entry_qty, 3),
						flt(over_pct, 3),
						len(row_chunks),
					),
					alert=False,
				)
	
			if not wo_doc.wip_warehouse:
				frappe.throw(
					_("Work Order {0} has no WIP warehouse set. Raw materials cannot be fetched.").format(wo_id),
					title=_("Missing WIP Warehouse")
				)
	
			planned_wo_posts.append(
				{
					"wo_id": wo_id,
					"wo_doc": wo_doc,
					"rows": rows,
					"total_qty": total_qty,
					"row_chunks": row_chunks,
					"expected_rm_map": expected_rm_map,
				}
			)

		if not planned_wo_posts:
			frappe.throw(
				_(
					"SPR submit blocked: no Work Order has producible quantity. "
					"Check roll weights / bag PCS and Work Order mapping."
				),
				title=_("No manufacturing rows"),
			)

		# Fast path: rolls already posted to submitted Manufacture — skip preview/build/submit loop.
		if self._spr_all_planned_rows_already_manufactured(planned_wo_posts):
			existing_submitted = self._get_existing_submitted_manufacture_entries_for_spr()
			if existing_submitted:
				self.db_set("manufacturing_entries", ", ".join(existing_submitted))
				self._sync_production_plan_progress_from_work_orders(_cstr(self.get("production_plan")))
				self._refresh_batch_qty_for_codes(
					[_cstr(r.get("batch_no")) for r in (self.items or []) if _cstr(r.get("batch_no"))]
				)
				created_entries_by_wo = self._spr_group_manufacture_entries_by_wo(existing_submitted, wo_groups)
				self._spr_show_submit_summary(wo_groups, created_entries_by_wo)
				frappe.msgprint(
					_("Manufacture already posted for all rolls — linked entries: {0}").format(
						", ".join(existing_submitted[:20])
					),
					alert=True,
				)
				return

		# Phase 2: after ALL WO groups are validated, create/submit Manufacture entries once.
		# Preflight shortage check first so submit cannot partially create entries for only some WOs.
		shortage_events = []
		for plan in planned_wo_posts:
			wo_id = plan["wo_id"]
			wo_doc = plan["wo_doc"]
			for chunk_rows in plan["row_chunks"]:
				chunk_total_qty = self._fg_posting_qty_for_rows(chunk_rows, wo_doc)
				if chunk_total_qty <= 0:
					continue
				if not self._spr_rows_needing_manufacture(wo_doc, chunk_rows, extra_se_names=None):
					continue
				preview_se = self._build_shortage_preview_for_chunk(wo_doc, chunk_total_qty)
				self._spr_cap_manufacture_rm_lines_to_wip_available(preview_se, wo_doc)
				shortages = self._rm_shortages_for_se(preview_se, wo_doc)
				if shortages:
					shortage_events.append(
						{
							"wo_id": wo_id,
							"wo_doc": wo_doc,
							"chunk_total_qty": chunk_total_qty,
							"shortages": shortages,
						}
					)
				wip_topup = self._spr_wip_topup_shortages_for_se(preview_se, wo_doc)
				if wip_topup:
					shortage_events.append(
						{
							"wo_id": wo_id,
							"wo_doc": wo_doc,
							"chunk_total_qty": chunk_total_qty,
							"shortages": wip_topup,
							"wip_topup": True,
						}
					)
		if shortage_events:
			shortage_events = self._spr_filter_preflight_shortage_events(shortage_events)
		if shortage_events:
			self._raise_shortage_with_transfer_batch(shortage_events)

		self._spr_init_manual_fabric_batch_pools(planned_wo_posts)

		for draft_se in self._spr_submit_all_draft_manufactures_for_spr():
			if draft_se not in created_entries:
				created_entries.append(draft_se)
			wo = _cstr(frappe.db.get_value("Stock Entry", draft_se, "work_order"))
			if wo:
				created_entries_by_wo[wo].append(draft_se)

		# Create/submit Manufacture entries after preflight passes for all WO chunks.
		# Savepoint ensures we can roll back partial Manufacture submits if any later WO fails.
		mfg_submit_savepoint = "spr_mfg_submit"
		frappe.db.savepoint(mfg_submit_savepoint)
		for plan in planned_wo_posts:
			wo_id = plan["wo_id"]
			wo_doc = plan["wo_doc"]
			row_chunks = plan["row_chunks"]
			total_qty = plan["total_qty"]
			expected_rm_map = plan["expected_rm_map"]
			actual_rm_map = {}
			for idx, chunk_rows in enumerate(row_chunks, start=1):
				chunk_total_qty = self._fg_posting_qty_for_rows(chunk_rows, wo_doc)
				if chunk_total_qty <= 0:
					continue
				if not self._spr_rows_needing_manufacture(wo_doc, chunk_rows, extra_se_names=None):
					continue
				for _spr_mfg_try in range(2):
					try:
						chunk_done = self._spr_run_manufacture_chunk_attempt(
							wo_id=wo_id,
							wo_doc=wo_doc,
							chunk_rows=chunk_rows,
							chunk_total_qty=chunk_total_qty,
							chunk_idx=idx,
							chunk_count=len(row_chunks),
							mfg_submit_savepoint=mfg_submit_savepoint,
							planned_wo_posts=planned_wo_posts,
							actual_rm_map=actual_rm_map,
							created_entries=created_entries,
							created_entries_by_wo=created_entries_by_wo,
							allow_wip_topup_retry=(_spr_mfg_try == 0),
						)
						actual_rm_map = chunk_done["actual_rm_map"]
						break
					except _SprWipTopupRetry:
						if _spr_mfg_try == 0:
							continue
						raise
			if actual_rm_map or created_entries_by_wo.get(wo_id):
				self._validate_rm_split_variance(
					wo_id, total_qty, expected_rm_map, actual_rm_map, wo_doc=wo_doc
				)

		self._spr_submit_backfill_missing_manufactures(
			planned_wo_posts, created_entries, created_entries_by_wo
		)
		self._spr_auto_post_leftover_roll_stock(
			planned_wo_posts, created_entries, created_entries_by_wo
		)
		for plan in planned_wo_posts:
			self._validate_fg_roll_coverage_for_wo(
				plan.get("wo_doc"),
				plan.get("rows") or [],
				created_entries_by_wo.get(plan.get("wo_id"), []),
			)
		self._validate_all_planned_wos_manufactured(planned_wo_posts, created_entries_by_wo)

		if created_entries:
			self.db_set("manufacturing_entries", ", ".join(created_entries))
			self._sync_production_plan_progress_from_work_orders(_cstr(self.get("production_plan")))
			self._refresh_batch_qty_for_codes([_cstr(r.get("batch_no")) for r in (self.items or []) if _cstr(r.get("batch_no"))])
			self._spr_activate_all_roll_batches_after_manufacture(created_entries_by_wo)
			self._spr_show_submit_summary(wo_groups, created_entries_by_wo)
		else:
			# Recovery-safe path: if old bug already posted Manufacture entries for this SPR, reuse them.
			existing_submitted = self._get_existing_submitted_manufacture_entries_for_spr()
			if existing_submitted:
				self.db_set("manufacturing_entries", ", ".join(existing_submitted))
				self._sync_production_plan_progress_from_work_orders(_cstr(self.get("production_plan")))
				self._refresh_batch_qty_for_codes([_cstr(r.get("batch_no")) for r in (self.items or []) if _cstr(r.get("batch_no"))])
				created_entries_by_wo = self._spr_group_manufacture_entries_by_wo(existing_submitted, wo_groups)
				self._spr_show_submit_summary(wo_groups, created_entries_by_wo)
				frappe.msgprint(
					_("No new Manufacture entry needed; reusing existing submitted entries: {0}").format(
						", ".join(existing_submitted[:20])
					),
					alert=True,
				)
			else:
				frappe.throw(
					_(
						"SPR submit blocked: no Manufacture Stock Entry was created. "
						"Check Work Order mapping and produced quantity "
						"(weight for roll runs, achieved bag PCS for bag runs), then retry."
					),
					title=_("No stock entry created"),
				)

	def _spr_ensure_manufacture_fg_bundles(self, se_name: str) -> None:
		"""Ensure manufacture FG lines have batch + Serial and Batch Bundle (draft or submitted)."""
		if not se_name or not frappe.db.exists("Stock Entry", se_name):
			return
		se_doc = frappe.get_doc("Stock Entry", se_name)
		if _cstr(se_doc.get("purpose")) != "Manufacture" or cint(se_doc.get("docstatus")) >= 2:
			return
		for fg in se_doc.items or []:
			if cint(fg.get("is_finished_item")) != 1:
				continue
			bn = _cstr(fg.get("batch_no")).strip()
			if not bn:
				continue
			fg_wh = _cstr(fg.get("t_warehouse") or se_doc.get("to_warehouse")).strip()
			fg_qty = flt(fg.get("transfer_qty") or fg.get("qty"))
			_spr_activate_batch_from_manufacture(bn, fg, se_doc, fallback_qty=fg_qty)
			_spr_ensure_fg_serial_batch_bundle(se_doc, fg, bn, fg_qty, fg_wh)

	def _spr_activate_all_roll_batches_after_manufacture(self, created_entries_by_wo: dict) -> None:
		"""Activate SPR roll batches only from submitted Manufacture FG lines (real SLE)."""
		for wo_id, ses in (created_entries_by_wo or {}).items():
			for se_name in ses or []:
				if not frappe.db.exists("Stock Entry", se_name):
					continue
				se_doc = frappe.get_doc("Stock Entry", se_name)
				for fg in se_doc.items or []:
					if cint(fg.get("is_finished_item")) != 1:
						continue
					bn = _cstr(fg.get("batch_no")).strip()
					if bn:
						_spr_activate_batch_from_manufacture(
							bn, fg, se_doc, fallback_qty=flt(fg.get("qty"))
						)
						_spr_ensure_fg_serial_batch_bundle(
							se_doc,
							fg,
							bn,
							flt(fg.get("transfer_qty") or fg.get("qty")),
							_cstr(fg.get("t_warehouse") or se_doc.get("to_warehouse")),
						)

	def _spr_build_submit_summary_html(self, wo_groups: dict, created_entries_by_wo: dict) -> str:
		"""Build post-submit summary HTML (all WOs, manufacture links, batch counts)."""
		quality = _cstr(
			getattr(self, "custom_quality_summary", None)
			or getattr(self, "custom_quality", None)
			or getattr(self, "quality", None)
			or ""
		).strip()
		color = _cstr(
			getattr(self, "custom_color_summary", None)
			or getattr(self, "custom_color", None)
			or getattr(self, "color", None)
			or ""
		).strip()
		gsm_val = _cstr(
			getattr(self, "custom_gsm_summary", None)
			or getattr(self, "custom_gsm", None)
			or getattr(self, "gsm", None)
			or ""
		).strip()

		header_chips = []
		if quality:
			header_chips.append(f"<span style='background:#e3f2fd;padding:2px 8px;border-radius:12px;font-size:12px;'>{frappe.utils.escape_html(quality)}</span>")
		if color:
			header_chips.append(f"<span style='background:#fce4ec;padding:2px 8px;border-radius:12px;font-size:12px;'>{frappe.utils.escape_html(color)}</span>")
		if gsm_val:
			header_chips.append(f"<span style='background:#e8f5e9;padding:2px 8px;border-radius:12px;font-size:12px;'>{frappe.utils.escape_html(gsm_val)} GSM</span>")

		rows_html = ""
		total_rolls = 0
		total_batches = 0
		all_active = True

		for wo_id, rows in wo_groups.items():
			ses = created_entries_by_wo.get(wo_id) or []
			roll_count = len(rows)
			total_rolls += roll_count

			# Count FG lines with batch_no (activated batches) across this WO's SEs
			batch_count = 0
			if ses:
				try:
					res = frappe.db.sql(
						"""
						SELECT COUNT(*) FROM `tabStock Entry Detail`
						WHERE parent IN %s AND is_finished_item = 1
						  AND IFNULL(batch_no, '') != ''
						""",
						[tuple(ses)],
					)
					batch_count = cint((res or [[0]])[0][0])
				except Exception:
					batch_count = 0
			total_batches += batch_count

			batch_ok = ses and batch_count >= roll_count
			if not batch_ok:
				all_active = False
			batch_badge_color = "#27ae60" if batch_ok else "#e74c3c"
			batch_label = f"{batch_count} / {roll_count}"

			if ses:
				from urllib.parse import quote as _url_quote
				_se_link_parts = []
				for se in ses:
					_se_url = "/app/stock-entry/" + _url_quote(se, safe="")
					_se_label = frappe.utils.escape_html(se)
					_se_link_parts.append(
						f"<a href='{_se_url}' target='_blank' style='color:#1a73e8;font-size:11px;'>{_se_label}</a>"
					)
				se_links = " ".join(_se_link_parts)
			else:
				se_links = "<span style='color:#e74c3c;font-style:italic;'>Not created</span>"
				all_active = False

			from urllib.parse import quote as _url_quote
			wo_url = "/app/work-order/" + _url_quote(_cstr(wo_id), safe="")
			rows_html += (
				f"<tr style='border-bottom:1px solid #f0f0f0;'>"
				f"<td style='padding:8px 10px;font-size:12px;'>"
				f"<a href='{wo_url}' target='_blank' "
				f"style='color:#333;font-weight:600;'>{frappe.utils.escape_html(wo_id)}</a></td>"
				f"<td style='padding:8px 10px;text-align:center;font-size:13px;font-weight:600;'>{roll_count}</td>"
				f"<td style='padding:8px 10px;text-align:center;'>"
				f"<span style='background:{batch_badge_color};color:white;padding:2px 8px;"
				f"border-radius:10px;font-size:12px;font-weight:600;'>{batch_label}</span></td>"
				f"<td style='padding:8px 10px;font-size:11px;'>{se_links}</td>"
				f"</tr>"
			)

		status_bg = "#e8f5e9" if all_active else "#fff3e0"
		status_color = "#2e7d32" if all_active else "#e65100"
		status_text = "All batches activated" if all_active else "Some batches may be inactive — please verify"

		html = (
			f"<div style='font-family:var(--font-stack);'>"
			f"<div style='background:{status_bg};border-radius:6px;padding:8px 12px;"
			f"margin-bottom:12px;display:flex;align-items:center;gap:8px;'>"
			f"<span style='font-size:16px;'>{'✅' if all_active else '⚠️'}</span>"
			f"<span style='color:{status_color};font-weight:600;font-size:13px;'>{status_text}</span>"
			f"</div>"
			f"<div style='margin-bottom:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;'>"
			f"<span style='font-size:13px;font-weight:700;color:#333;'>{frappe.utils.escape_html(self.name)}</span>"
			f"{''.join(header_chips)}"
			f"</div>"
			f"<table style='width:100%;border-collapse:collapse;background:#fff;"
			f"border:1px solid #e0e0e0;border-radius:6px;overflow:hidden;'>"
			f"<thead><tr style='background:#f5f5f5;'>"
			f"<th style='padding:8px 10px;text-align:left;font-size:12px;color:#555;'>Work Order</th>"
			f"<th style='padding:8px 10px;text-align:center;font-size:12px;color:#555;'>Rolls</th>"
			f"<th style='padding:8px 10px;text-align:center;font-size:12px;color:#555;'>Batches Active</th>"
			f"<th style='padding:8px 10px;text-align:left;font-size:12px;color:#555;'>Manufacture Entry</th>"
			f"</tr></thead>"
			f"<tbody>{rows_html}</tbody>"
			f"<tfoot><tr style='background:#fafafa;border-top:2px solid #e0e0e0;'>"
			f"<td style='padding:8px 10px;font-size:12px;font-weight:700;'>Total</td>"
			f"<td style='padding:8px 10px;text-align:center;font-size:13px;font-weight:700;'>{total_rolls}</td>"
			f"<td style='padding:8px 10px;text-align:center;font-size:13px;font-weight:700;color:{status_color};'>"
			f"{total_batches} / {total_rolls}</td>"
			f"<td></td>"
			f"</tr></tfoot>"
			f"</table>"
			f"</div>"
		)
		return html

	def _spr_show_submit_summary(self, wo_groups: dict, created_entries_by_wo: dict) -> None:
		"""Store summary HTML for client display after submit (avoid server dialog during save)."""
		html = self._spr_build_submit_summary_html(wo_groups, created_entries_by_wo)
		try:
			frappe.response["spr_submit_summary"] = html
		except Exception:
			pass

	def _spr_group_manufacture_entries_by_wo(self, se_names: list[str], wo_groups: dict) -> dict:
		"""Map submitted Manufacture Stock Entry names to Work Orders for summary display."""
		created_by_wo: dict = defaultdict(list)
		for se in se_names or []:
			wo = _cstr(frappe.db.get_value("Stock Entry", se, "work_order"))
			if wo:
				created_by_wo[wo].append(se)
				continue
			pi = _cstr(
				frappe.db.get_value(
					"Stock Entry Detail",
					{"parent": se, "is_finished_item": 1},
					"item_code",
					order_by="idx asc",
				)
			)
			for w, rows in (wo_groups or {}).items():
				if rows and _cstr(rows[0].get("item_code")) == pi:
					created_by_wo[w].append(se)
					break
		return dict(created_by_wo)

	def create_mix_roll_material_receipts(self):
		"""Post mix roll FG via Material Receipt (no Work Order / Manufacture)."""
		if not spr_doc_is_mix_roll(self):
			frappe.throw(_("This method is only for mix roll Shaft Production Runs."))

		unit = _cstr(self.get("custom_unit") or self.get("unit"))
		company, fg_wh = resolve_mix_roll_company_and_fg_warehouse(unit)
		if self.get("company") and frappe.db.exists("Company", self.company):
			company = _cstr(self.company)
		if not company:
			frappe.throw(_("Company is required for mix roll stock posting."))
		if not fg_wh or not frappe.db.exists("Warehouse", fg_wh):
			frappe.throw(_("Finished goods warehouse not found for {0}.").format(unit or "unit"))

		lines = []
		for row in self.items or []:
			qty = flt(row.get("net_weight") or row.get("gross_weight") or 0)
			if qty <= 0:
				continue
			item_code = _cstr(row.get("item_code"))
			batch_id = _cstr(row.get("batch_no"))
			if not item_code:
				frappe.throw(_("Mix roll line missing item code (roll {0}).").format(row.get("roll_no") or "?"))
			if not batch_id:
				frappe.throw(_("Mix roll line missing batch (item {0}).").format(item_code))
			lines.append((row, item_code, batch_id, qty))

		if not lines:
			frappe.throw(
				_("Cannot submit mix roll SPR without produced roll weights on Roll Production Results."),
				title=_("No produced quantity"),
			)

		se = frappe.new_doc("Stock Entry")
		se.company = company
		se.posting_date = self.run_date or today()
		se.posting_time = nowtime()
		se.set_posting_time = 1
		se.stock_entry_type = self._stock_entry_type_name_for_purpose("Material Receipt")
		se.purpose = "Material Receipt"
		se.remarks = _("Mix roll production from {0}").format(self.name)
		self._set_stock_entry_spr_link(se)
		self._set_stock_entry_unit(se)
		se_meta = frappe.get_meta("Stock Entry")
		if se_meta.has_field("custom_is_mix_roll"):
			se.custom_is_mix_roll = 1

		for spr_row, item_code, batch_id, qty in lines:
			batch_link = self._get_batch_link_name_for_stock_entry(batch_id, item_code, company, spr_row)
			uom = _cstr(spr_row.get("uom")) or frappe.db.get_value("Item", item_code, "stock_uom") or "Kg"
			se.append(
				"items",
				{
					"item_code": item_code,
					"item_name": spr_row.get("item_name") or frappe.db.get_value("Item", item_code, "item_name"),
					"qty": qty,
					"transfer_qty": qty,
					"t_warehouse": fg_wh,
					"uom": uom,
					"stock_uom": uom,
					"conversion_factor": 1,
					"batch_no": batch_link or batch_id,
					"is_finished_item": 1,
				},
			)

		se.insert(ignore_permissions=True)
		_spr_mark_stock_entry_system_flags(se)
		se.submit()
		self._persist_stock_entry_spr_reference_db(se.name)
		self._apply_order_code_to_submitted_stock_entry(se.name)
		self.db_set("manufacturing_entries", se.name)
		self._refresh_batch_qty_for_codes([_cstr(r.get("batch_no")) for r, *_ in lines])
		frappe.msgprint(
			_("Mix roll Material Receipt created: {0} → {1}").format(se.name, fg_wh),
			alert=True,
		)

	def update_work_order_statuses(self):
		wo_ids = list(
			{
				(row.get("work_order") or row.get("wo_id"))
				for row in (self.items or [])
				if row.get("work_order") or row.get("wo_id")
			}
		)
		for wo_id in wo_ids:
			wo_doc = frappe.get_doc("Work Order", wo_id)
			total_produced = frappe.db.sql(
				"""
				SELECT IFNULL(SUM(fg_completed_qty), 0)
				FROM `tabStock Entry`
				WHERE work_order = %s
				  AND IFNULL(purpose, '') = 'Manufacture'
				  AND docstatus = 1
				""",
				wo_id,
			)[0][0]

			if flt(total_produced) >= flt(wo_doc.qty):
				wo_doc.db_set("status", "Completed")
				frappe.msgprint(_("Work Order {0} marked as Completed").format(wo_id), alert=True)

	def cancel_manufacturing_stock_entries(self):
		names = []
		if self.manufacturing_entries:
			names = [x.strip() for x in self.manufacturing_entries.split(",") if x.strip()]
		meta_se = frappe.get_meta("Stock Entry")
		if not names and meta_se.has_field("shaft_production_run"):
			conds = [
				"shaft_production_run = %s",
				"IFNULL(purpose, '') = 'Manufacture'",
				"docstatus = 1",
			]
			params = [self.name]
			if meta_se.has_field("roll_production_entry"):
				conds.append("IFNULL(roll_production_entry, '') = ''")
			names = frappe.db.sql_list(
				f"SELECT name FROM `tabStock Entry` WHERE {' AND '.join(conds)}",
				params,
			)
		for name in names:
			if not frappe.db.exists("Stock Entry", name):
				continue
			if frappe.db.get_value("Stock Entry", name, "docstatus") != 1:
				continue
			se = frappe.get_doc("Stock Entry", name)
			se.cancel()
			frappe.msgprint(_("Cancelled Manufacturing Entry {0}").format(name), alert=True)
		self.db_set("manufacturing_entries", "")


_SLITTING_PP_UNITS = (SLITTING_UNIT, SLITTING_UNIT_VTP, SLITTING_UNASSIGNED_UNIT)
_REWINDING_PP_UNITS = (REWINDING_UNIT_L3, REWINDING_UNIT_L4, REWINDING_UNIT_L5, REWINDING_UNASSIGNED_UNIT)
_PRINTING_PP_UNITS = (
	PRINTING_UNIT_2_COLOUR,
	PRINTING_UNIT_4_COLOUR,
	PRINTING_UNIT_TT,
	PRINTING_UNASSIGNED_UNIT,
)
_BAG_BOARD_PP_UNITS = (
	BOX_BAG_UNIT_L1,
	BOX_BAG_UNIT_L2,
	BOX_BAG_UNIT_L4_SCREEN,
	BOX_BAG_UNASSIGNED_UNIT,
) + tuple(W_CUT_D_CUT_ALL_UNITS)


def _pp_is_bag_board_unit_from_doc(pp_doc) -> bool:
	"""True when PP workstation is a Box Bag or W/D-CUT bag-board machine."""
	u = _spr_unit_value_for_current_field(pp_doc.get("custom_unit") if pp_doc else None)
	return u in _BAG_BOARD_PP_UNITS


def _spr_pp_process_flags(pp) -> dict:
	"""Map PP unit → SPR process checkboxes (unit wins over item/bundle heuristics)."""
	spr_meta = frappe.get_meta("Shaft Production Run")
	pp_unit = _spr_unit_value_for_current_field(pp.get("custom_unit"))
	flags = {}

	def _set_flag(fieldname: str, on: bool) -> None:
		if spr_meta.has_field(fieldname):
			flags[fieldname] = 1 if on else 0

	_set_flag("custom_is_box_bag", _pp_is_bag_board_unit_from_doc(pp))
	_set_flag("custom_is_sheet_cutting", pp_unit == SHEET_CUTTING_UNIT)
	_set_flag("custom_is_slitting", pp_unit in _SLITTING_PP_UNITS)
	_set_flag("custom_is_lamination", pp_unit == LAMINATION_UNIT)
	_set_flag("custom_is_rewinding", pp_unit in _REWINDING_PP_UNITS)
	_set_flag("custom_is_printing", pp_unit in _PRINTING_PP_UNITS)
	_set_flag("custom_is_bopp_film", pp_unit == PRINTED_BOPP_FILM_UNIT)
	return flags


@frappe.whitelist()
def get_production_plan_details(production_plan):
	"""Fill header fields from Production Plan."""
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return {}
	pp = frappe.get_doc("Production Plan", production_plan)
	pp_meta = frappe.get_meta("Production Plan")
	pp_unit = _spr_unit_value_for_current_field(pp.get("custom_unit"))
	out = {
		"company": pp.get("company"),
		"customer": pp.get("customer"),
		"custom_unit": pp_unit,
	}
	# custom_order_code comes from PP's custom_party_code
	if pp_meta.has_field("custom_party_code"):
		out["custom_order_code"] = pp.get("custom_party_code") or ""
	
	label_value = resolve_label_from_pp_doc(pp)
	if label_value:
		out["custom_label"] = label_value
	
	# Calculate custom_total_planned_qty from WO sum
	out["custom_total_planned_qty"] = _production_plan_total_planned_qty(production_plan)
	if frappe.get_meta("Shaft Production Run").has_field("custom_total_planned_pcs"):
		out["custom_total_planned_pcs"] = _production_plan_total_planned_pcs(production_plan)
	process_flags = _spr_pp_process_flags(pp)
	out.update(process_flags)
	is_sc = bool(cint(process_flags.get("custom_is_sheet_cutting", 0)))
	is_bb = bool(cint(process_flags.get("custom_is_box_bag", 0)))
	out["is_sheet_cutting"] = is_sc
	if is_sc or is_bb:
		out["bundle_rows"] = get_bundle_calculation_rows_for_production_plan(
			production_plan,
			out.get("custom_order_code"),
			0,
		)
	
	frappe.logger().info(f"[get_production_plan_details] PP {production_plan}: custom_order_code={out.get('custom_order_code')}, custom_label={out.get('custom_label')}, custom_total_planned_qty={out.get('custom_total_planned_qty')}")
	if pp.get("sales_order"):
		so = frappe.db.get_value(
			"Sales Order", pp.sales_order, ["customer", "transaction_date"], as_dict=True
		)
		if so:
			out["customer"] = out["customer"] or so.customer
	return out


PP_BUNDLE_CALC_FIELD = "custom_bundle_calculation"
BUNDLE_CALC_DOCTYPE = "Bundle Calculation"
SHEET_CUTTING_PROCESS_CODES = frozenset({"251", "252", "253", "254", "255"})


def _spr_item_process_prefix(item_code: str) -> str:
	"""Process prefix for FG items (design-first bag codes e.g. 6000-511-221…)."""
	try:
		from production_entry.production_planning.scheduler_api import _item_process_prefix

		return _cstr(_item_process_prefix(item_code)).strip()
	except Exception:
		return spr_fg_item_process_code(item_code)


def _is_sheet_cutting_fg_code(item_code: str) -> bool:
	return _spr_item_process_prefix(item_code) in SHEET_CUTTING_PROCESS_CODES


def _is_box_bag_fg_code(item_code: str) -> bool:
	try:
		from production_entry.production_planning.scheduler_api import BOX_BAG_PROCESS_CODES

		return _spr_item_process_prefix(item_code) in BOX_BAG_PROCESS_CODES
	except Exception:
		return False


def _is_wcut_dcut_fg_code(item_code: str) -> bool:
	try:
		from production_entry.production_planning.scheduler_api import W_CUT_D_CUT_FG_PROCESS_CODES

		return _spr_item_process_prefix(item_code) in W_CUT_D_CUT_FG_PROCESS_CODES
	except Exception:
		return False


def _is_bag_bundle_fg_code(item_code: str) -> bool:
	return bool(_spr_resolve_bag_fg_process_code(item_code))


def _spr_bag_size_from_item_code(item_code: str) -> str:
	"""Bag size label (inches) from FG item code — Bag Series master, same as Planning Sheet."""
	ic = _cstr(item_code).strip()
	if not ic:
		return ""
	try:
		from production_entry.production_planning.box_bag_api import resolve_bag_size_from_item_code

		return _cstr(resolve_bag_size_from_item_code(ic)).strip()
	except Exception:
		return ""


def _spr_sheet_size_from_item_code(item_code: str) -> str:
	"""Sheet size label from FG item code — Sheet Cutting Series master."""
	ic = _cstr(item_code).strip()
	if not ic:
		return ""
	try:
		from production_entry.production_planning.scheduler_api import _sheet_size_for_item_code

		sz, _ = _sheet_size_for_item_code(ic)
		return _cstr(sz).strip()
	except Exception:
		return ""


def _spr_resolve_roll_line_specs_from_item_code(item_code: str, item_name: str = None) -> dict:
	"""Quality, colour, GSM, sheet size, width from FG item code (251–255) for SPR roll lines."""
	ic = _cstr(item_code).strip()
	out = {"quality": "", "color": "", "gsm": 0, "sheet_size": "", "bag_size": "", "width_inch": 0.0}
	if not ic:
		return out
	if not item_name:
		item_name = _cstr(frappe.db.get_value("Item", ic, "item_name") or "")
	try:
		from production_entry.production_planning.scheduler_api import (
			_LAMINATION_QUALITY_BY_CODE,
			_combined_bopp_line_gsm,
			_get_color_by_code,
			_item_process_prefix,
			_lamination_process_from_item_code,
			_parse_253_item_code,
			_parse_254_item_code,
			_parse_255_item_code,
			_parse_sheet_cutting_item_code,
			_sheet_size_for_item_code,
			resolve_quality_color_gsm_from_item_code,
		)

		q, c, g = resolve_quality_color_gsm_from_item_code(ic, item_name)
		out["quality"] = _cstr(q).strip().upper()
		out["color"] = _cstr(c).strip().upper()
		out["gsm"] = cint(g or 0)
		sz, w = _sheet_size_for_item_code(ic)
		out["sheet_size"] = _cstr(sz).strip()
		out["width_inch"] = flt(w or 0)
		if _is_bag_bundle_fg_code(ic):
			try:
				from production_entry.production_planning.box_bag_api import (
					_parse_box_bag_item_code,
					_parse_dcut_bag_item_code,
					resolve_bag_size_from_item_code,
				)

				p221 = _parse_box_bag_item_code(ic) or _parse_dcut_bag_item_code(ic) or {}
				if p221:
					if out["gsm"] <= 0:
						out["gsm"] = cint(p221.get("total_gsm") or 0)
					if not out["quality"]:
						qc = _cstr(p221.get("quality_code") or "").strip().upper()
						out["quality"] = _cstr(
							p221.get("quality_name") or _LAMINATION_QUALITY_BY_CODE.get(qc, "") or ""
						).strip().upper()
					if not out["color"]:
						cc = _cstr(p221.get("colour_code") or "").strip()
						if cc:
							try:
								out["color"] = _cstr(_get_color_by_code(cc) or "").strip().upper()
							except Exception:
								out["color"] = ""
				bag_sz = _cstr(resolve_bag_size_from_item_code(ic)).strip()
				if bag_sz:
					out["bag_size"] = bag_sz
			except Exception:
				frappe.log_error(frappe.get_traceback(), "_spr_resolve_roll_line_specs:box_bag")
		if out["quality"] and out["color"] and out["gsm"] > 0:
			return out
		pp = _item_process_prefix(ic)
		lam = _lamination_process_from_item_code(ic)
		p = {}
		if lam == "255" or pp == "255":
			p = _parse_255_item_code(ic) or {}
		elif pp == "253":
			p = _parse_253_item_code(ic) or {}
		elif pp == "254":
			p = _parse_254_item_code(ic) or {}
		elif pp in ("251", "252"):
			p = _parse_sheet_cutting_item_code(ic) or {}
		if p:
			if not out["quality"]:
				qc = _cstr(p.get("quality_code") or "").strip().upper()
				out["quality"] = _cstr(p.get("quality_name") or _LAMINATION_QUALITY_BY_CODE.get(qc, "") or "").strip().upper()
			if not out["color"]:
				cc = _cstr(p.get("colour_code") or "").strip()
				if cc:
					try:
						out["color"] = _cstr(_get_color_by_code(cc) or "").strip().upper()
					except Exception:
						out["color"] = ""
			if out["gsm"] <= 0:
				out["gsm"] = cint(_combined_bopp_line_gsm(p) or 0) or cint(p.get("gsm") or p.get("fabric_gsm") or 0)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "_spr_resolve_roll_line_specs_from_item_code")
	if out["gsm"] <= 0:
		out["gsm"] = _sheet_cutting_parse_gsm(ic)
	if not out["quality"] or not out["color"]:
		q2, c2 = extract_quality_and_color(item_name or "", item_code=ic)
		if not out["quality"]:
			out["quality"] = _cstr(q2).strip().upper()
	if not out["color"]:
		out["color"] = _cstr(c2).strip().upper()
	if flt(out["width_inch"]) <= 0:
		out["width_inch"] = _spr_nominal_roll_width_inch(ic, item_name)
	return out


def _sheet_cutting_parse_gsm(item_code: str) -> int:
	ic = _cstr(item_code)
	if not ic:
		return 0
	try:
		from production_entry.production_planning.scheduler_api import (
			_parse_253_item_code,
			_parse_254_item_code,
			_parse_255_item_code,
			_parse_sheet_cutting_item_code,
			_item_process_prefix,
		)

		pp = _item_process_prefix(ic)
		if pp == "253":
			return cint((_parse_253_item_code(ic) or {}).get("gsm") or 0)
		if pp == "254":
			return cint((_parse_254_item_code(ic) or {}).get("gsm") or 0)
		if pp == "255":
			p = _parse_255_item_code(ic) or {}
			comb = cint(p.get("fabric_gsm") or 0) + cint(p.get("bopp_gsm") or 0) + cint(p.get("lam_gsm") or 0)
			return comb or cint(p.get("fabric_gsm") or 0)
		p = _parse_sheet_cutting_item_code(ic) or {}
		return cint(p.get("gsm") or 0)
	except Exception:
		pass
	gsm, _w = parse_item_code(ic)
	return cint(gsm or 0)


def _resolve_wo_for_pp_item_code(production_plan: str, item_code: str) -> dict:
	"""Best-effort WO for a sheet-cutting FG on this Production Plan."""
	pp = _cstr(production_plan)
	ic = _cstr(item_code)
	if not pp or not ic:
		return {}
	rows = frappe.get_all(
		"Work Order",
		filters={"production_plan": pp, "production_item": ic, "docstatus": ["<", 2]},
		fields=["name", "production_item", "qty", "production_plan_item"],
		order_by="creation asc",
		limit=1,
	) or []
	if rows:
		return rows[0]
	proc = spr_fg_item_process_code(ic)
	if not proc:
		return {}
	for wo in frappe.get_all(
		"Work Order",
		filters={"production_plan": pp, "docstatus": ["<", 2]},
		fields=["name", "production_item", "qty", "production_plan_item"],
		order_by="creation asc",
	) or []:
		if spr_fg_item_process_code(wo.get("production_item")) == proc:
			return wo
	return {}


def _bundle_child_field(row, default=None, *names):
	"""Read a field from a PP/SPR Bundle Calculation child row (supports box-bag aliases).

	When several field names are listed, numeric zero is skipped so e.g. pcs_per_packet=0
	on a box-bag PP row does not block pcs_per_box=200.
	"""
	if not names:
		return default
	skip_zero = len(names) > 1
	for n in names:
		try:
			v = row.get(n) if isinstance(row, dict) else getattr(row, n, None)
		except Exception:
			v = None
		if v in (None, ""):
			continue
		if skip_zero:
			try:
				if flt(v) == 0:
					continue
			except Exception:
				pass
		return v
	return default


def _bundle_optional_field(row, default=None, *names):
	"""Read PP/SPR bundle field; return default when missing (do not coerce empty to zero)."""
	if not names:
		return default
	for n in names:
		try:
			v = row.get(n) if isinstance(row, dict) else getattr(row, n, None)
		except Exception:
			v = None
		if v not in (None, ""):
			return v
	return default


def _bundle_row_bag_size(row, item_code: str, *, for_bag_fg: bool = False) -> str:
	if for_bag_fg:
		field_names = (
			"bag_size",
			"custom_bag_size",
			"sheet_cutting_size",
			"sheet_size",
			"custom_sheet_size",
		)
	else:
		field_names = (
			"sheet_cutting_size",
			"sheet_size",
			"bag_size",
			"custom_bag_size",
			"custom_sheet_size",
		)
	sz = _cstr(_bundle_child_field(row, "", *field_names))
	if sz:
		return sz
	if item_code:
		try:
			from production_entry.production_planning.box_bag_api import resolve_bag_size_from_item_code

			if for_bag_fg:
				sz = _cstr(resolve_bag_size_from_item_code(item_code)).strip()
				if sz:
					return sz
		except Exception:
			pass
		return _cstr(_spr_resolve_roll_line_specs_from_item_code(item_code).get("sheet_size") or "")
	return ""


def _pp_bundle_calc_child_table_names() -> list[str]:
	"""All Production Plan child tables that use Bundle Calculation doctype."""
	names = []
	try:
		meta = frappe.get_meta("Production Plan")
		for df in meta.fields:
			if df.fieldtype == "Table" and df.options == BUNDLE_CALC_DOCTYPE:
				if df.fieldname not in names:
					names.append(df.fieldname)
	except Exception:
		pass
	for fn in (PP_BUNDLE_CALC_FIELD, "bundle_calculation", "custom_bundle_calculation_table"):
		if fn not in names:
			names.append(fn)
	return names


def _pp_is_box_bag_unit(pp_doc) -> bool:
	u = _spr_unit_value_for_current_field(pp_doc.get("custom_unit") if pp_doc else None)
	return u in (BOX_BAG_UNIT_L1, BOX_BAG_UNIT_L2, BOX_BAG_UNIT_L4_SCREEN, BOX_BAG_UNASSIGNED_UNIT)


def _pp_is_wcut_dcut_unit(pp_doc) -> bool:
	u = _spr_unit_value_for_current_field(pp_doc.get("custom_unit") if pp_doc else None)
	return u in W_CUT_D_CUT_ALL_UNITS


def spr_doc_is_bag_spr(doc) -> bool:
	"""True only when Is Bag is explicitly checked on the SPR — drives PCS qty + RM batch pick."""
	return bool(cint(getattr(doc, "custom_is_box_bag", 0)))


def _first_pp_bundle_calc_row(pp_doc):
	for tbl in _pp_bundle_calc_child_table_names():
		rows = pp_doc.get(tbl) or []
		if rows:
			return rows[0]
	return None


def _resolve_pp_bundle_item_code(row, pp_doc=None, default_item_code=None) -> str:
	ic = _cstr(
		_bundle_child_field(row, "", "item_code", "production_item", "finished_item", "item")
	).strip()
	if ic:
		return ic
	ic = _cstr(default_item_code).strip()
	if ic:
		return ic
	if not pp_doc:
		return ""
	for poi in pp_doc.get("po_items") or []:
		pic = _cstr(poi.get("item_code")).strip()
		if not pic:
			continue
		if _pp_is_box_bag_unit(pp_doc) or _is_bag_bundle_fg_code(pic) or _is_sheet_cutting_fg_code(pic):
			return pic
	for wo in frappe.get_all(
		"Work Order",
		filters={"production_plan": pp_doc.name, "docstatus": ["<", 2]},
		fields=["production_item"],
		order_by="creation asc",
		limit=5,
	):
		pic = _cstr(wo.get("production_item")).strip()
		if pic and (_pp_is_box_bag_unit(pp_doc) or _is_bag_bundle_fg_code(pic) or _is_sheet_cutting_fg_code(pic)):
			return pic
	return ""


def _normalize_pp_bundle_src_row(row, default_item_code=None, pp_doc=None) -> dict:
	"""Map PP bundle row to SPR bundle_calculation (sheet cutting + box bag field names)."""
	ic = _resolve_pp_bundle_item_code(row, pp_doc=pp_doc, default_item_code=default_item_code)
	if not ic:
		return {}
	is_bag = _is_bag_bundle_fg_code(ic) or bool(pp_doc and _pp_is_box_bag_unit(pp_doc))
	if is_bag:
		pcs = cint(
			_bundle_child_field(
				row,
				0,
				"pcs_per_box",
				"custom_pcs_per_box",
				"pcs_per_packet",
				"pcs_per_pack",
				"custom_pcs_per_packet",
			)
			or 0
		)
		pkts = cint(_bundle_child_field(row, 0, "pkts_per_bundle", "packets_per_bundle") or 0)
	else:
		pcs = cint(
			_bundle_child_field(
				row,
				0,
				"pcs_per_packet",
				"pcs_per_box",
				"pcs_per_pack",
				"custom_pcs_per_box",
				"custom_pcs_per_packet",
			)
			or 0
		)
		pkts = cint(_bundle_child_field(row, 0, "pkts_per_bundle", "packets_per_bundle") or 0)
		if pkts <= 0:
			pkts = 1
	n_boxes = flt(_bundle_child_field(row, 0, "no_of_boxes", "custom_no_of_boxes") or 0)
	n_bundles = flt(_bundle_child_field(row, 0, "no_of_bundles", "custom_no_of_bundles") or 0)
	if is_bag and n_boxes > 0:
		if n_bundles <= 0:
			n_bundles = n_boxes
	elif n_bundles <= 0 and n_boxes > 0:
		n_bundles = n_boxes
	if is_bag:
		# On box-bag PP, "Total Planned Pcs" is pcs per box (e.g. 200), not order total.
		pp_pcs_per_box = flt(
			_bundle_child_field(
				row,
				0,
				"total_planned_pcs",
				"custom_total_planned_pcs",
				"planned_bag_pcs",
				"custom_planned_bag_pcs",
			)
			or 0
		)
		if pcs <= 0 and pp_pcs_per_box > 0:
			pcs = cint(pp_pcs_per_box)
		if n_boxes > 0 and pcs > 0:
			tpb = flt(n_boxes * pcs)
		elif pcs > 0:
			tpb = flt(pcs)
		else:
			tpb = flt(
				_bundle_child_field(row, 0, "total_pcs_per_bundle", "total_planned_qty") or 0
			)
	else:
		tpb = flt(
			_bundle_child_field(
				row,
				0,
				"total_pcs_per_bundle",
				"total_planned_pcs",
				"total_planned_qty",
				"custom_total_planned_pcs",
			)
			or 0
		)
		if tpb <= 0 and pkts > 0 and pcs > 0:
			tpb = flt(pkts * pcs)
		if tpb <= 0 and n_boxes > 0 and pcs > 0:
			tpb = flt(n_boxes * pcs)
	bag_sz = _bundle_row_bag_size(row, ic, for_bag_fg=True) if is_bag else ""
	sheet_sz = "" if is_bag else _bundle_row_bag_size(row, ic, for_bag_fg=False)
	if is_bag and bag_sz:
		sheet_sz = bag_sz
	out = {
		"item_code": ic,
		"bag_size": bag_sz,
		"sheet_cutting_size": sheet_sz,
		"no_of_bundles": n_bundles,
		"no_of_boxes": n_boxes,
		"pkts_per_bundle": pkts,
		"pcs_per_packet": pcs,
		"total_pcs_per_bundle": tpb,
	}
	for spr_field, aliases in (
		("total_consumed_meter", ("total_consumed_meter", "consumed_meter", "custom_total_consumed_meter")),
		("total_achieved_weight", ("total_achieved_weight", "custom_total_achieved_weight")),
		("total_produced_sheets", ("total_produced_sheets", "custom_total_produced_sheets")),
		("total_produced_bag_pcs", ("total_produced_bag_pcs", "custom_total_produced_bag_pcs", "achieved_bag_pcs")),
	):
		v = _bundle_optional_field(row, None, *aliases)
		if v not in (None, ""):
			prec = 2 if spr_field in ("total_consumed_meter", "total_achieved_weight") else 0
			out[spr_field] = flt(v, prec)
	return out


def _production_plan_uses_bundle_calculation(pp) -> bool:
	"""True when PP has sheet-cutting or box-bag bundle calculation lines."""
	if not pp:
		return False
	pp_unit = _spr_unit_value_for_current_field(pp.get("custom_unit"))
	if pp_unit == SHEET_CUTTING_UNIT:
		return True
	if pp_unit in (BOX_BAG_UNIT_L1, BOX_BAG_UNIT_L2, BOX_BAG_UNIT_L4_SCREEN, BOX_BAG_UNASSIGNED_UNIT):
		return True
	for row in pp.get(PP_BUNDLE_CALC_FIELD) or []:
		ic = _cstr(_bundle_child_field(row, "", "item_code", "production_item", "finished_item", "item"))
		if not ic:
			continue
		if _is_sheet_cutting_fg_code(ic) or _is_bag_bundle_fg_code(ic):
			return True
	for poi in pp.get("po_items") or []:
		ic = _cstr(poi.get("item_code"))
		if _is_sheet_cutting_fg_code(ic) or _is_bag_bundle_fg_code(ic):
			return True
	return False


def _pp_row_has_bundle_fields(row) -> bool:
	if _bundle_child_field(row, 0, "no_of_boxes", "custom_no_of_boxes", "no_of_bundles"):
		return True
	if _bundle_child_field(row, 0, "pcs_per_box", "custom_pcs_per_box", "pcs_per_packet", "pcs_per_pack"):
		return True
	if _bundle_child_field(
		row, 0, "total_planned_pcs", "custom_total_planned_pcs", "total_pcs_per_bundle", "total_planned_qty"
	):
		return True
	return bool(_cstr(_bundle_child_field(row, "", "item_code", "production_item", "finished_item", "item")).strip())


def _get_pp_doc(pp_or_name):
	if pp_or_name is None:
		return None
	if hasattr(pp_or_name, "get") and getattr(pp_or_name, "doctype", None) == "Production Plan":
		return pp_or_name
	name = _cstr(pp_or_name).strip()
	if not name or not frappe.db.exists("Production Plan", name):
		return None
	return frappe.get_doc("Production Plan", name)


def _pp_length_per_roll_for_item(pp_doc_or_name, item_code: str, production_plan_item: str = None) -> float:
	"""Length / Roll from PP Assembly Items (or po_items) for ordered length on SPR roll lines."""
	pp_doc = _get_pp_doc(pp_doc_or_name)
	if not pp_doc:
		return 0.0
	ic = _cstr(item_code).strip()
	ppi = _cstr(production_plan_item).strip()
	length_fields = (
		"length_per_roll",
		"custom_length_per_roll",
		"length__roll",
		"meter__roll",
		"custom_length_roll",
		"length_roll",
		"custom_meter_per_roll",
		"meter_per_roll",
		"planned_length",
		"custom_planned_length",
	)
	table_keys = (
		PP_BUNDLE_CALC_FIELD,
		"assembly_items",
		"custom_assembly_items",
		"sub_assembly_items",
		"custom_sub_assembly_items",
		"po_items",
		"prod_order_items",
	)
	for tk in table_keys:
		for row in pp_doc.get(tk) or []:
			row_ppi = _cstr(getattr(row, "name", None) or (row.get("name") if isinstance(row, dict) else "")).strip()
			if ppi and row_ppi != ppi:
				continue
			row_ic = _cstr(
				getattr(row, "item_code", None)
				or (row.get("item_code") if isinstance(row, dict) else None)
				or getattr(row, "production_item", None)
				or (row.get("production_item") if isinstance(row, dict) else None)
			).strip()
			if ic and row_ic and row_ic != ic and not ppi:
				continue
			for fn in length_fields:
				if hasattr(row, fn):
					v = flt(getattr(row, fn, 0) or 0)
				else:
					v = flt(row.get(fn) or 0) if isinstance(row, dict) else 0
				if v > 0:
					return v
	return 0.0


def _iter_pp_bundle_source_rows(pp_doc):
	"""Yield PP rows that carry bundle / box fields (child table + assembly + po_items)."""
	seen = set()
	is_bb = _pp_is_box_bag_unit(pp_doc)

	def _yield_row(row, key_ic: str = ""):
		ic = key_ic or _resolve_pp_bundle_item_code(row, pp_doc=pp_doc)
		key = (ic or "__no_item__", _cstr(getattr(row, "name", None)))
		if key in seen:
			return
		seen.add(key)
		yield row

	for tbl in _pp_bundle_calc_child_table_names():
		for row in pp_doc.get(tbl) or []:
			if not _pp_row_has_bundle_fields(row):
				continue
			yield from _yield_row(row)

	pp_meta = frappe.get_meta("Production Plan")
	skip_tables = frozenset(
		{
			"po_items",
			"mr_items",
			"prod_order_items",
			"material_request_plan_items",
			"sub_assembly_items",
		}
		| frozenset(_pp_bundle_calc_child_table_names())
	)
	for df in pp_meta.fields:
		if df.fieldtype != "Table" or df.fieldname in skip_tables:
			continue
		for row in pp_doc.get(df.fieldname) or []:
			if not _pp_row_has_bundle_fields(row):
				continue
			yield from _yield_row(row)

	for poi in pp_doc.get("po_items") or []:
		ic = _cstr(poi.get("item_code")).strip()
		if not ic:
			continue
		if not is_bb and not _is_bag_bundle_fg_code(ic) and not _is_sheet_cutting_fg_code(ic):
			continue
		key = (ic, _cstr(poi.name))
		if key in seen:
			continue
		seen.add(key)
		yield poi


def _fallback_bundle_rows_from_pp(pp_doc) -> list[dict]:
	"""When bundle child rows lack item_code or normalize empty, merge PP bundle qty with po_items / WO."""
	merged_src = _first_pp_bundle_calc_row(pp_doc)
	out = []
	seen_ic = set()
	is_bb = _pp_is_box_bag_unit(pp_doc)

	def _append(ic: str, src_row):
		if not ic or ic in seen_ic:
			return
		seen_ic.add(ic)
		norm = _normalize_pp_bundle_src_row(src_row, default_item_code=ic, pp_doc=pp_doc)
		if norm:
			out.append(norm)

	for poi in pp_doc.get("po_items") or []:
		ic = _cstr(poi.get("item_code")).strip()
		if not ic:
			continue
		if not is_bb and not _is_bag_bundle_fg_code(ic) and not _is_sheet_cutting_fg_code(ic):
			continue
		_append(ic, merged_src or poi)

	if out:
		return out

	for wo in frappe.get_all(
		"Work Order",
		filters={"production_plan": pp_doc.name, "docstatus": ["<", 2]},
		fields=["production_item"],
		order_by="creation asc",
	):
		ic = _cstr(wo.get("production_item")).strip()
		if not ic:
			continue
		if not is_bb and not _is_bag_bundle_fg_code(ic) and not _is_sheet_cutting_fg_code(ic):
			continue
		_append(ic, merged_src or {})

	if not out and merged_src:
		norm = _normalize_pp_bundle_src_row(merged_src, pp_doc=pp_doc)
		if norm:
			out.append(norm)

	return out


def _read_pp_bundle_calculation_rows(production_plan: str) -> list[dict]:
	"""Rows from PP bundle table + assembly/po_items fallback."""
	pp = _cstr(production_plan).strip()
	if not pp or not frappe.db.exists("Production Plan", pp):
		return []
	pp_doc = frappe.get_doc("Production Plan", pp)
	out = []
	seen_ic = set()
	for row in _iter_pp_bundle_source_rows(pp_doc):
		norm = _normalize_pp_bundle_src_row(row, pp_doc=pp_doc)
		if not norm:
			continue
		ic = norm.get("item_code") or ""
		if ic and ic in seen_ic:
			continue
		if ic:
			seen_ic.add(ic)
		out.append(norm)
	if not out:
		out = _fallback_bundle_rows_from_pp(pp_doc)
	return out


@frappe.whitelist()
def get_bundle_calculation_rows_for_production_plan(production_plan, order_code=None, order_meter=None):
	"""Copy PP bundle calculation rows for SPR (sheet cutting)."""
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return []
	oc = _cstr(order_code)
	if not oc:
		pp = frappe.get_doc("Production Plan", production_plan)
		oc = _cstr(pp.get("custom_party_code") or pp.get("party_code") or "")
	out = []
	for src in _read_pp_bundle_calculation_rows(production_plan):
		ic = src.get("item_code") or ""
		wo = _resolve_wo_for_pp_item_code(production_plan, ic)
		pkts = cint(src.get("pkts_per_bundle") or 0)
		pcs = cint(src.get("pcs_per_packet") or 0)
		n_boxes = flt(src.get("no_of_boxes") or 0)
		tpb = flt(src.get("total_pcs_per_bundle") or 0)
		if _is_bag_bundle_fg_code(ic) and n_boxes > 0 and pcs > 0:
			tpb = flt(n_boxes * pcs)
		elif tpb <= 0 and pkts > 0 and pcs > 0:
			tpb = flt(pkts * pcs)
		elif tpb <= 0 and n_boxes > 0 and pcs > 0:
			tpb = flt(n_boxes * pcs)
		row = dict(src)
		row["total_pcs_per_bundle"] = tpb
		if _is_bag_bundle_fg_code(ic) or _pp_is_box_bag_unit(frappe.get_doc("Production Plan", production_plan)):
			bz = _cstr(row.get("bag_size") or row.get("sheet_cutting_size") or "")
			if not bz and ic:
				bz = _bundle_row_bag_size(src, ic, for_bag_fg=True)
			if bz:
				row["bag_size"] = bz
				row["sheet_cutting_size"] = bz
		row["work_order"] = wo.get("name") if wo else ""
		row["order_code"] = oc
		row["job"] = _cstr(wo.get("production_plan_item") or "") if wo else ""
		for prod_field in (
			"total_consumed_meter",
			"total_achieved_weight",
			"total_produced_sheets",
			"total_produced_bag_pcs",
		):
			if prod_field not in row or row.get(prod_field) in (None, ""):
				row.pop(prod_field, None)
		out.append(row)
	return out


def populate_spr_bundle_calculation_from_pp(spr_doc, production_plan, order_code=None, order_meter=None):
	"""Replace SPR bundle_calculation child rows from PP."""
	if not spr_doc or not hasattr(spr_doc, "bundle_calculation"):
		return
	rows = get_bundle_calculation_rows_for_production_plan(production_plan, order_code, order_meter)
	spr_doc.set("bundle_calculation", [])
	for r in rows:
		spr_doc.append("bundle_calculation", r)
	return rows


@frappe.whitelist()
def spr_refresh_bundle_calculation_from_pp(shaft_production_run=None):
	"""Desk: reload bundle_calculation from linked Production Plan (box bag / sheet cutting)."""
	name = _cstr(shaft_production_run).strip()
	if not name or not frappe.db.exists("Shaft Production Run", name):
		frappe.throw(_("Shaft Production Run not found"))
	doc = frappe.get_doc("Shaft Production Run", name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("Only draft Shaft Production Run can be refreshed"))
	pp = get_pp_from_spr(name)
	if not pp:
		frappe.throw(_("Production Plan not linked"))
	populate_spr_bundle_calculation_from_pp(doc, pp, doc.get("custom_order_code"), 0)
	doc.flags.ignore_permissions = True
	doc.save()
	return {"status": "ok", "rows": len(doc.get("bundle_calculation") or [])}


def _spr_bundle_job_tag(bundle_row, bundle_index: int, bundle_row_idx=None) -> str:
	"""Stable link between bundle_calculation row and roll lines (job field)."""
	row_id = _cstr(getattr(bundle_row, "name", None) or "")
	if not row_id and bundle_row_idx is not None:
		row_id = f"idx{cint(bundle_row_idx)}"
	return f"{row_id}::{cint(bundle_index)}"


def _spr_bundle_job_prefix(bundle_row, bundle_row_idx=None) -> str:
	row_id = _cstr(getattr(bundle_row, "name", None) or "")
	if not row_id and bundle_row_idx is not None:
		row_id = f"idx{cint(bundle_row_idx)}"
	return f"{row_id}::" if row_id else ""


def _spr_planned_pcs_per_bundle(bundle_row) -> float:
	pkts = cint(getattr(bundle_row, "pkts_per_bundle", 0) or 0)
	pcs = cint(getattr(bundle_row, "pcs_per_packet", 0) or 0)
	tpb = flt(getattr(bundle_row, "total_pcs_per_bundle", 0) or 0)
	if tpb <= 0 and pkts > 0 and pcs > 0:
		tpb = flt(pkts * pcs)
	n_boxes = flt(getattr(bundle_row, "no_of_boxes", 0) or 0)
	if tpb <= 0 and n_boxes > 0 and pcs > 0:
		tpb = flt(n_boxes * pcs)
	return flt(tpb)


def _spr_planned_pcs_per_bundle_entry(bundle_row, bundle_index: int, n_entries: int, is_box_bag: bool = False) -> float:
	"""Per roll line: box / W-CUT bag = pcs per box (e.g. 200), not order total ÷ boxes."""
	n_entries = cint(n_entries) or 1
	if is_box_bag:
		pcs = cint(getattr(bundle_row, "pcs_per_packet", 0) or 0)
		if pcs > 0:
			return flt(pcs)
		tpb = flt(getattr(bundle_row, "total_pcs_per_bundle", 0) or 0)
		n_boxes = cint(getattr(bundle_row, "no_of_boxes", 0) or 0)
		if tpb > 0 and n_boxes > 1:
			per_box = flt(tpb) / flt(n_boxes)
			if per_box > 0:
				return flt(per_box)
		if tpb > 0:
			return flt(tpb)
		return 0.0
	return _spr_planned_pcs_per_bundle(bundle_row)


def _spr_bundle_row_by_key(spr_doc, bundle_row_name=None, bundle_row_idx=None):
	rows = getattr(spr_doc, "bundle_calculation", None) or []
	if bundle_row_name:
		for r in rows:
			if r.name == bundle_row_name:
				return r
	if bundle_row_idx is not None:
		idx = cint(bundle_row_idx)
		if 0 <= idx < len(rows):
			return rows[idx]
	return None


def _spr_item_line_from_bundle(
	pp_name,
	bundle_row,
	bundle_index: int,
	wo: dict,
	order_code: str,
	bundle_row_idx=None,
	is_box_bag: bool = False,
	n_entries: int = 1,
):
	"""Build one Roll Production Result line for sheet-cutting / box-bag bundle Create Entry."""
	item_code = _cstr(getattr(bundle_row, "item_code", None) or wo.get("production_item"))
	if not item_code:
		frappe.throw(_("Item Code is missing on the bundle row and Work Order"))
	item_name = frappe.db.get_value("Item", item_code, "item_name") or ""
	specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
	quality = specs.get("quality") or ""
	color = specs.get("color") or ""
	gsm = cint(specs.get("gsm") or 0)
	sz_from_item = specs.get("sheet_size") or ""
	w_from_item = flt(specs.get("width_inch") or 0)
	pcs_per_bundle = _spr_planned_pcs_per_bundle_entry(
		bundle_row, bundle_index, n_entries, is_box_bag=is_box_bag
	)
	ordered_len = _pp_length_per_roll_for_item(
		pp_name,
		item_code,
		_cstr(wo.get("production_plan_item") or getattr(bundle_row, "job", None)),
	)
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	row = {
		"work_order": wo.get("name"),
		"item_code": item_code,
		"item_name": item_name,
		"quality": quality,
		"gsm": gsm,
		"planned_qty": 0,
		"job": _spr_bundle_job_tag(bundle_row, bundle_index, bundle_row_idx),
		"batch_no": "",
		"party_code": order_code or get_order_code(frappe.get_doc("Work Order", wo["name"])),
		"uom": _item_stock_uom_for_spr(item_code),
		"roll_no": 0,
		"meter_roll": flt(ordered_len) if ordered_len > 0 else 0,
		"produced_length_mtrs": 0,
		"net_weight": 0,
		"gross_weight": 0,
		"width_inch": w_from_item if w_from_item > 0 else 0,
		"color": color,
	}
	if is_box_bag:
		bag_sz = _cstr(
			getattr(bundle_row, "bag_size", None)
			or getattr(bundle_row, "sheet_cutting_size", None)
			or specs.get("bag_size")
			or sz_from_item
		)
		if not bag_sz and item_code:
			bag_sz = _spr_bag_size_from_item_code(item_code)
		if spi_meta.has_field("custom_bag_size") and bag_sz:
			row["custom_bag_size"] = bag_sz
		if w_from_item > 0 and spi_meta.has_field("width_inch"):
			row["width_inch"] = flt(w_from_item)
	elif spi_meta.has_field("custom_sheet_size"):
		sz = _cstr(specs.get("sheet_size") or "").strip()
		if not sz:
			sz = _spr_sheet_size_from_item_code(item_code)
		if not sz:
			sz = _cstr(getattr(bundle_row, "sheet_cutting_size", None))
		row["custom_sheet_size"] = sz or None
		if w_from_item > 0 and spi_meta.has_field("width_inch"):
			row["width_inch"] = flt(w_from_item)
	if spi_meta.has_field("custom_planned_sheets_pcs") and not is_box_bag:
		row["custom_planned_sheets_pcs"] = pcs_per_bundle
	elif spi_meta.has_field("planned_qty") and not is_box_bag:
		row["planned_qty"] = pcs_per_bundle
	if spi_meta.has_field("custom_planned_bag_pcs"):
		row["custom_planned_bag_pcs"] = pcs_per_bundle
	if spi_meta.has_field("custom_total_produced_sheets"):
		row["custom_total_produced_sheets"] = 0
	if spi_meta.has_field("custom_achieved_bag_pcs"):
		row["custom_achieved_bag_pcs"] = 0
	return row


@frappe.whitelist()
def build_spr_bundle_result_lines_for_row(
	shaft_production_run,
	bundle_row_name=None,
	bundle_row_idx=None,
):
	"""Append Roll Production Result lines for one bundle row (no_of_bundles lines)."""
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		frappe.throw(_("Save Shaft Production Run first"))
	spr_doc = frappe.get_doc("Shaft Production Run", shaft_production_run)
	if cint(spr_doc.docstatus) != 0:
		frappe.throw(_("Cannot add roll lines to a submitted Shaft Production Run"))
	if not (cint(getattr(spr_doc, "custom_is_sheet_cutting", 0)) or cint(getattr(spr_doc, "custom_is_box_bag", 0))):
		frappe.throw(_("Bundle Create Entry is only for sheet-cutting / bag SPR"))
	pp_name = get_pp_from_spr(shaft_production_run)
	if not pp_name:
		frappe.throw(_("Production Plan not found on this Shaft Production Run"))
	bundle_row = _spr_bundle_row_by_key(spr_doc, bundle_row_name, bundle_row_idx)
	if not bundle_row:
		frappe.throw(_("Bundle Calculation row not found"))
	n_bundles = cint(getattr(bundle_row, "no_of_bundles", 0) or 0)
	if cint(getattr(spr_doc, "custom_is_box_bag", 0)) and n_bundles < 1:
		n_bundles = cint(getattr(bundle_row, "no_of_boxes", 0) or 0)
	if n_bundles < 1:
		frappe.throw(_("No of Bundles / No of Boxes must be at least 1"))
	ic = _cstr(getattr(bundle_row, "item_code", None))
	wo = _resolve_wo_for_pp_item_code(pp_name, ic)
	if not wo:
		frappe.throw(_("No Work Order found for item {0} on Production Plan {1}").format(ic, pp_name))
	order_code = _cstr(getattr(spr_doc, "custom_order_code", None) or getattr(bundle_row, "order_code", None))
	resolved_idx = bundle_row_idx
	rows = list(getattr(spr_doc, "bundle_calculation", None) or [])
	if resolved_idx is None and bundle_row_name:
		for i, r in enumerate(rows):
			if r.name == bundle_row_name:
				resolved_idx = i
				break
	if resolved_idx is None:
		try:
			resolved_idx = rows.index(bundle_row)
		except ValueError:
			resolved_idx = max(cint(getattr(bundle_row, "idx", 0)) - 1, 0)
	is_bb = cint(getattr(spr_doc, "custom_is_box_bag", 0))
	lines = []
	for i in range(n_bundles):
		lines.append(
			_spr_item_line_from_bundle(
				pp_name,
				bundle_row,
				i + 1,
				wo,
				order_code,
				bundle_row_idx=resolved_idx,
				is_box_bag=bool(is_bb),
				n_entries=n_bundles,
			)
		)
	return lines


def _spr_sum_produced_sheets_for_bundle_row(spr_doc, bundle_row, bundle_row_idx=None) -> float:
	"""Sum Produced Sheets (PCS) from roll lines linked to one bundle_calculation row."""
	prefix = _spr_bundle_job_prefix(bundle_row, bundle_row_idx)
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	pcs_field = (
		"custom_total_produced_sheets"
		if spi_meta.has_field("custom_total_produced_sheets")
		else None
	)
	if not pcs_field:
		return 0.0
	total = 0.0
	prefix_hits = 0
	ic = _cstr(getattr(bundle_row, "item_code", None))
	wo = _cstr(getattr(bundle_row, "work_order", None))
	n_bundles = cint(getattr(bundle_row, "no_of_bundles", 0) or 0)
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		matched = bool(prefix and job.startswith(prefix))
		if matched:
			prefix_hits += 1
			total += flt(getattr(it, pcs_field, 0) or 0)
	if prefix_hits:
		return flt(total, 0)
	# Legacy rows: job was "1", "2", … before bundle-row tags were introduced.
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		if _cstr(getattr(it, "item_code", None)) != ic or _cstr(getattr(it, "work_order", None)) != wo:
			continue
		try:
			jn = int(job)
		except (TypeError, ValueError):
			continue
		if n_bundles > 0 and not (1 <= jn <= n_bundles):
			continue
		total += flt(getattr(it, pcs_field, 0) or 0)
	return flt(total, 0)


def _spr_sum_net_weight_for_bundle_row(spr_doc, bundle_row, bundle_row_idx=None) -> float:
	"""Sum net_weight (Kg) on roll lines linked to one bundle_calculation row."""
	prefix = _spr_bundle_job_prefix(bundle_row, bundle_row_idx)
	total = 0.0
	prefix_hits = 0
	ic = _cstr(getattr(bundle_row, "item_code", None))
	wo = _cstr(getattr(bundle_row, "work_order", None))
	n_bundles = cint(getattr(bundle_row, "no_of_bundles", 0) or 0)
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		if prefix and job.startswith(prefix):
			prefix_hits += 1
			total += flt(getattr(it, "net_weight", 0) or 0, 2)
	if prefix_hits:
		return flt(total, 2)
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		if _cstr(getattr(it, "item_code", None)) != ic or _cstr(getattr(it, "work_order", None)) != wo:
			continue
		try:
			jn = int(job)
		except (TypeError, ValueError):
			continue
		if n_bundles > 0 and not (1 <= jn <= n_bundles):
			continue
		total += flt(getattr(it, "net_weight", 0) or 0, 2)
	return flt(total, 2)


def sync_bundle_total_produced_sheets_for_doc(spr_doc) -> None:
	"""Update bundle_calculation.total_produced_sheets from roll line sums."""
	if not spr_doc or not hasattr(spr_doc, "bundle_calculation"):
		return
	rows = list(spr_doc.get("bundle_calculation") or [])
	for idx, br in enumerate(rows):
		br.total_produced_sheets = _spr_sum_produced_sheets_for_bundle_row(spr_doc, br, bundle_row_idx=idx)


def _spr_sum_produced_bag_pcs_for_bundle_row(spr_doc, bundle_row, bundle_row_idx=None) -> float:
	"""Sum Achieved Bag PCS from roll lines linked to one bundle_calculation row."""
	prefix = _spr_bundle_job_prefix(bundle_row, bundle_row_idx)
	total = 0.0
	prefix_hits = 0
	ic = _cstr(getattr(bundle_row, "item_code", None))
	wo = _cstr(getattr(bundle_row, "work_order", None))
	n_bundles = cint(getattr(bundle_row, "no_of_bundles", 0) or 0)
	n_boxes = cint(getattr(bundle_row, "no_of_boxes", 0) or 0)
	n_rows = n_bundles if n_bundles > 0 else n_boxes
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		matched = bool(prefix and job.startswith(prefix))
		if matched:
			prefix_hits += 1
			total += flt(getattr(it, "custom_achieved_bag_pcs", 0) or 0)
	if prefix_hits:
		return flt(total, 0)
	for it in spr_doc.get("items") or []:
		job = _cstr(getattr(it, "job", None))
		if _cstr(getattr(it, "item_code", None)) != ic or _cstr(getattr(it, "work_order", None)) != wo:
			continue
		try:
			jn = int(job)
		except (TypeError, ValueError):
			continue
		if n_rows > 0 and not (1 <= jn <= n_rows):
			continue
		total += flt(getattr(it, "custom_achieved_bag_pcs", 0) or 0)
	return flt(total, 0)


def sync_bundle_total_produced_bag_pcs_for_doc(spr_doc) -> None:
	"""Update bundle_calculation.total_produced_bag_pcs from roll line achieved bag pcs sums."""
	if not spr_doc or not hasattr(spr_doc, "bundle_calculation"):
		return
	rows = list(spr_doc.get("bundle_calculation") or [])
	for idx, br in enumerate(rows):
		if hasattr(br, "total_produced_bag_pcs"):
			br.total_produced_bag_pcs = _spr_sum_produced_bag_pcs_for_bundle_row(spr_doc, br, bundle_row_idx=idx)


def sync_bundle_total_achieved_weight_for_doc(spr_doc) -> None:
	"""Update bundle_calculation.total_achieved_weight from roll line net_weight sums."""
	if not spr_doc or not hasattr(spr_doc, "bundle_calculation"):
		return
	rows = list(spr_doc.get("bundle_calculation") or [])
	for idx, br in enumerate(rows):
		br.total_achieved_weight = _spr_sum_net_weight_for_bundle_row(spr_doc, br, bundle_row_idx=idx)


def sync_bundle_consumed_meter_header(spr_doc) -> None:
	"""Sheet cutting: SPR header consumed meters = sum of bundle Consumed Mtrs."""
	if not spr_doc or not (cint(getattr(spr_doc, "custom_is_sheet_cutting", 0)) or cint(getattr(spr_doc, "custom_is_box_bag", 0))):
		return
	meta = frappe.get_meta("Shaft Production Run")
	if not meta.has_field("custom_total_achieved_meter"):
		return
	total = sum(
		flt(getattr(br, "total_consumed_meter", 0) or 0, 2) for br in (spr_doc.get("bundle_calculation") or [])
	)
	spr_doc.custom_total_achieved_meter = flt(total, 2)


def _expand_spr_name_tokens_csv(spr_csv: str) -> list[str]:
	out = []
	for part in _cstr(spr_csv).split(","):
		p = part.strip()
		if p and p not in out:
			out.append(p)
	return out


def _sheet_cutting_spr_metrics(spr_names, pp_id=None):
	"""Aggregate sheet PCS and meters for order table / reporting."""
	metrics = {
		"total_planned_sheet_pcs": 0.0,
		"total_produced_sheet_pcs": 0.0,
		"produced_meter": 0.0,
		"achieved_kg": 0.0,
	}
	spr_list = []
	for nm in spr_names or []:
		spr_list.extend(_expand_spr_name_tokens_csv(nm))
	spr_list = [s for s in spr_list if s and frappe.db.exists("Shaft Production Run", s)]
	if not spr_list and pp_id and frappe.db.has_column("Production Plan", "custom_shaft_production_run_id"):
		pass
	if frappe.db.exists("DocType", BUNDLE_CALC_DOCTYPE):
		for spr_name in spr_list:
			try:
				spr = frappe.get_doc("Shaft Production Run", spr_name)
			except Exception:
				continue
			is_sub = cint(spr.docstatus) == 1
			for br in getattr(spr, "bundle_calculation", None) or []:
				metrics["total_planned_sheet_pcs"] += _spr_planned_pcs_per_bundle(br)
				if is_sub:
					metrics["total_produced_sheet_pcs"] += flt(getattr(br, "total_produced_sheets", 0) or 0)
					metrics["produced_meter"] += flt(getattr(br, "total_consumed_meter", 0) or 0)
	elif pp_id:
		for src in _read_pp_bundle_calculation_rows(pp_id):
			pkts = cint(src.get("pkts_per_bundle") or 0)
			pcs = cint(src.get("pcs_per_packet") or 0)
			tpb = flt(src.get("total_pcs_per_bundle") or 0)
			if tpb <= 0 and pkts > 0 and pcs > 0:
				tpb = flt(pkts * pcs)
			metrics["total_planned_sheet_pcs"] += tpb
	spr_cols = set(frappe.db.get_table_columns("Shaft Production Run Item") or [])
	weight_expr = "IFNULL(sri.net_weight, 0)" if "net_weight" in spr_cols else "0"
	len_expr = "IFNULL(sri.produced_length_mtrs, 0)" if "produced_length_mtrs" in spr_cols else "0"
	pcs_expr = "IFNULL(sri.custom_total_produced_sheets, 0)" if "custom_total_produced_sheets" in spr_cols else "0"
	if spr_list:
		placeholders = ",".join(["%s"] * len(spr_list))
		for r in frappe.db.sql(
			f"""
			SELECT
				sri.parent,
				SUM({weight_expr}) AS kgs,
				SUM({len_expr}) AS len_m,
				SUM({pcs_expr}) AS pcs_sum,
				MAX(spr.docstatus) AS spr_docstatus
			FROM `tabShaft Production Run Item` sri
			INNER JOIN `tabShaft Production Run` spr ON spr.name = sri.parent
			WHERE sri.parent IN ({placeholders})
			GROUP BY sri.parent
			""",
			tuple(spr_list),
			as_dict=True,
		) or []:
			if cint(r.get("spr_docstatus")) != 1:
				continue
			metrics["achieved_kg"] += flt(r.get("kgs"))
			if metrics["produced_meter"] <= 0:
				metrics["produced_meter"] += flt(r.get("len_m"))
			if metrics["total_produced_sheet_pcs"] <= 0:
				metrics["total_produced_sheet_pcs"] += flt(r.get("pcs_sum"))
	return metrics


@frappe.whitelist()
def get_production_plan_wo_summary(production_plan):
	"""Work Orders linked to this PP: status, order qty, pending qty — for desk popup after PP is set."""
	if not production_plan or not frappe.db.exists("Production Plan", production_plan):
		return []
	return frappe.db.sql(
		"""
		SELECT
			wo.name AS work_order,
			wo.status,
			IFNULL(wo.qty, 0) AS order_qty,
			GREATEST(0, IFNULL(wo.qty, 0) - IFNULL(wo.produced_qty, 0)) AS pending_qty
		FROM `tabWork Order` wo
		WHERE wo.production_plan = %(pp)s
		  AND wo.docstatus < 2
		ORDER BY wo.name
		""",
		{"pp": production_plan},
		as_dict=True,
	)


def _count_combination_segments(combination) -> int:
	"""How many width slots in a combination string like '48\\" + 37\\" + ...'."""
	if not combination:
		return 1
	parts = [p.strip() for p in re.split(r"\+", str(combination)) if p.strip()]
	return max(1, len(parts))


def _parse_net_weight_kg_parts(net_weight) -> list[float]:
	"""Parse '89.61 + 6.2' style net weight from shaft job into kg numbers."""
	if not net_weight:
		return []
	s = str(net_weight).strip()
	if not s:
		return []
	out: list[float] = []
	for part in re.split(r"\s*\+\s*", s):
		part = part.strip()
		if not part:
			continue
		m = re.search(r"(\d+(?:\.\d+)?)", part.replace(",", ""))
		if m:
			out.append(flt(m.group(1)))
	return out


def _segment_weights_kg(job_row, segs: int) -> list[float]:
	"""Per-combination-segment kg; falls back to equal split of total target weight."""
	if segs < 1:
		segs = 1
	parts = _parse_net_weight_kg_parts(getattr(job_row, "net_weight", None))
	tw = flt(getattr(job_row, "total_weight", 0) or 0)
	if len(parts) >= segs:
		return [flt(x) for x in parts[:segs]]
	if parts:
		avg = sum(parts) / len(parts)
		while len(parts) < segs:
			parts.append(avg)
		return [flt(x) for x in parts[:segs]]
	if tw > 0:
		return [tw / segs] * segs
	return [0.0] * segs


def _planned_qty_for_roll_line(job_row, roll_index: int, segs: int) -> float:
	"""Planned qty = net weight (kg) for this combination segment (e.g. 48\"ΓåÆ89.61, 37\"ΓåÆ69.08)."""
	if segs < 1:
		segs = 1
	seg_weights = _segment_weights_kg(job_row, segs)
	seg_i = roll_index % segs
	seg_kg = seg_weights[seg_i] if seg_i < len(seg_weights) else 0.0
	return round(flt(seg_kg), 3)


def _planned_kg_for_spr_result_roll(job_row, roll_index: int, n_rolls: int, segs: int) -> float:
	"""Planned kg for one Produced Rolls line: per physical roll, not full job total on every line.

	When ``net_weight`` lists one value per roll, use that. When it lists one value per combination
	segment, cycle segments as rolls repeat (multi-shaft). Otherwise split ``total_weight`` evenly
	across ``n_rolls`` (e.g. 97.08 kg / 2 rolls ΓåÆ 48.54 each).
	"""
	if n_rolls < 1:
		n_rolls = 1
	if segs < 1:
		segs = 1
	tw = flt(getattr(job_row, "total_weight", 0) or 0)
	parts = _parse_net_weight_kg_parts(getattr(job_row, "net_weight", None))

	if len(parts) == n_rolls:
		return round(flt(parts[roll_index]), 3)

	if parts and segs > 1 and len(parts) >= segs:
		return round(flt(parts[roll_index % segs]), 3)

	if tw > 0:
		return round(tw / n_rolls, 3)
	return 0.0


def _spr_operation_lock_key(spr_name: str, operation: str = "write") -> str:
	return f"spr_lock:{_cstr(spr_name)}:{_cstr(operation)}"


def _spr_acquire_cache_lock(key: str, ttl_sec: int = 120) -> bool:
	"""Best-effort distributed lock using Frappe cache (Frappe 16 compatible)."""
	cache = frappe.cache()
	owner = _cstr(frappe.session.user or "system")
	ttl_sec = max(cint(ttl_sec), 30)
	redis_key = cache.make_key(key)

	def _lock_held() -> bool:
		try:
			val = cache.get(redis_key)
			if val is None:
				return False
			if isinstance(val, bytes):
				return bool(val)
			return bool(_cstr(val))
		except Exception:
			try:
				cache.delete_value(key)
				cache.delete_value(redis_key)
			except Exception:
				pass
			return False

	if _lock_held():
		return False

	try:
		acquired = cache.set(redis_key, owner, nx=True, ex=ttl_sec)
		if acquired is not None:
			return bool(acquired)
	except Exception:
		pass

	if _lock_held():
		return False
	return True


def _spr_release_cache_lock(key: str) -> None:
	try:
		cache = frappe.cache()
		cache.delete_value(cache.make_key(key))
	except Exception:
		pass
	try:
		frappe.cache().delete_value(key)
	except Exception:
		pass


@contextmanager
def _spr_operation_lock(spr_name: str, operation: str = "write", ttl_sec: int = 120):
	"""Block concurrent Create Entry / batch assignment on the same SPR."""
	if not spr_name:
		yield
		return
	key = _spr_operation_lock_key(spr_name, operation)
	acquired = _spr_acquire_cache_lock(key, ttl_sec=ttl_sec)
	if not acquired:
		frappe.throw(
			_(
				"Create Entry or Save is already running for {0}. "
				"Wait for it to finish and refresh — do not click again or reload."
			).format(spr_name),
			title=_("Please wait"),
		)
	try:
		yield
	finally:
		if acquired:
			_spr_release_cache_lock(key)


def _spr_used_batch_numbers_on_spr(spr_name: str | None, in_memory_items=None) -> set[str]:
	"""All batch_no values on this SPR (DB + in-memory rows).

	Includes Roll Waste rows — wasted rolls keep their batch number, so the
	next-batch preview must not re-issue it (GSM: mark waste then Add Roll).
	"""
	used: set[str] = set()
	if spr_name:
		for bn in frappe.db.sql_list(
			"""
			SELECT DISTINCT batch_no
			FROM `tabShaft Production Run Item`
			WHERE parent = %s AND IFNULL(batch_no, '') != ''
			""",
			spr_name,
		):
			bn = _cstr(bn).strip()
			if bn:
				used.add(bn)
		try:
			for bn, src in frappe.db.sql(
				"""
				SELECT batch_no, source_roll
				FROM `tabRoll Waste Row`
				WHERE parent = %s
				""",
				spr_name,
			):
				for val in (bn, src):
					val = _cstr(val).strip()
					if val:
						used.add(val)
		except Exception:
			pass
	for row in in_memory_items or []:
		bn = _cstr(getattr(row, "batch_no", None) or (row.get("batch_no") if isinstance(row, dict) else "")).strip()
		if bn:
			used.add(bn)
	return used


def _spr_batch_exists_on_other_spr(batch_no: str, exclude_spr: str | None = None) -> bool:
	"""True when batch_no is already saved on another Shaft Production Run."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no:
		return False
	if exclude_spr:
		return bool(
			frappe.db.sql(
				"""
				SELECT name
				FROM `tabShaft Production Run Item`
				WHERE batch_no = %s AND parent != %s
				LIMIT 1
				""",
				(batch_no, exclude_spr),
			)
		)
	return bool(frappe.db.exists("Shaft Production Run Item", {"batch_no": batch_no}))


def _spr_batch_exists_globally(batch_no: str, exclude_spr: str | None = None) -> bool:
	"""True when batch_no exists on another SPR row, a roll waste row, or in Batch master."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no:
		return False
	if _spr_batch_exists_on_other_spr(batch_no, exclude_spr):
		return True
	try:
		if exclude_spr:
			if frappe.db.sql(
				"""
				SELECT name
				FROM `tabRoll Waste Row`
				WHERE batch_no = %s AND parent != %s
				LIMIT 1
				""",
				(batch_no, exclude_spr),
			):
				return True
		elif frappe.db.exists("Roll Waste Row", {"batch_no": batch_no}):
			return True
	except Exception:
		pass
	return bool(frappe.db.exists("Batch", {"batch_id": batch_no}))


def _spr_next_available_batch_no(
	series_prefix: str,
	next_roll: int,
	used_batches: set[str],
	exclude_spr: str | None = None,
) -> tuple[str, int]:
	"""Next batch string for series that is not used on this SPR or in Batch master."""
	while True:
		candidate = f"{series_prefix}/{next_roll}"
		if candidate not in used_batches and not _spr_batch_exists_globally(candidate, exclude_spr):
			return candidate, next_roll
		next_roll += 1


def _spr_roll_suffix_from_batch(batch_no: str, series_prefix: str) -> int:
	batch_no = _cstr(batch_no).strip()
	series_prefix = _cstr(series_prefix).strip()
	if not batch_no or not series_prefix or "/" not in batch_no:
		return 0
	pref, roll = batch_no.split("/", 1)
	if pref.strip() != series_prefix:
		return 0
	try:
		return int(roll.strip())
	except ValueError:
		return 0


def _spr_parse_batch_list(existing_batches) -> list[str]:
	if not existing_batches:
		return []
	if isinstance(existing_batches, str):
		try:
			parsed = json.loads(existing_batches) or []
		except Exception:
			parsed = [x.strip() for x in existing_batches.split(",") if x.strip()]
	elif isinstance(existing_batches, (list, tuple)):
		parsed = list(existing_batches)
	else:
		parsed = []
	out = []
	for bn in parsed:
		bn = _cstr(bn).strip()
		if bn:
			out.append(bn)
	return out


def _spr_series_max_suffix(series_prefix: str) -> int:
	"""Highest /N already used for this GSM shift series (all SPRs, mix included)."""
	series_prefix = _cstr(series_prefix).strip()
	if not series_prefix:
		return 0
	doc = frappe.new_doc("Shaft Production Run")
	return cint(doc._spr_max_roll_suffix_for_prefix(series_prefix))


def _spr_roll_starting_for_gsm_session(
	series_prefix: str,
	spr_name: str | None = None,
	existing_batches=None,
	client_max_roll=None,
) -> int:
	"""Next roll suffix for the GSM shift series (fabric SPR + mix SPR + grid)."""
	series_prefix = _cstr(series_prefix).strip()
	mx = _spr_series_max_suffix(series_prefix)
	if spr_name:
		for bn in _spr_used_batch_numbers_on_spr(spr_name):
			mx = max(mx, _spr_roll_suffix_from_batch(bn, series_prefix))
	for bn in _spr_parse_batch_list(existing_batches):
		mx = max(mx, _spr_roll_suffix_from_batch(bn, series_prefix))
	try:
		if client_max_roll is not None and cint(client_max_roll) >= 0:
			mx = max(mx, cint(client_max_roll))
	except Exception:
		pass
	return (mx + 1) if mx > 0 else 1


@frappe.whitelist()
def get_next_spr_batch_numbers(
	shaft_production_run,
	count,
	client_max_roll=None,
	run_date=None,
	custom_unit=None,
	shift=None,
	client_series_prefix=None,
	existing_batches=None,
	gsm_shift_prefix=None,
):
	"""
	Preview batch/roll numbers for new rows (e.g. after Create Entry) without submitting SPR.
	Requires run_date, custom_unit, shift. Optional client_max_roll = highest roll index already on the form.

	Pass run_date, custom_unit, shift from the desk form when the document is saved but header
	fields were just edited and not yet re-saved — otherwise get_doc() would see stale DB values.
	"""
	count = cint(count)
	if count < 1:
		return []
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		frappe.throw(_("Save the Shaft Production Run first"))
	with _spr_operation_lock(shaft_production_run, "write", ttl_sec=60):
		return _get_next_spr_batch_numbers_unlocked(
			shaft_production_run=shaft_production_run,
			count=count,
			client_max_roll=client_max_roll,
			run_date=run_date,
			custom_unit=custom_unit,
			shift=shift,
			client_series_prefix=client_series_prefix,
			existing_batches=existing_batches,
			gsm_shift_prefix=gsm_shift_prefix,
		)


def _get_next_spr_batch_numbers_unlocked(
	shaft_production_run,
	count,
	client_max_roll=None,
	run_date=None,
	custom_unit=None,
	shift=None,
	client_series_prefix=None,
	existing_batches=None,
	gsm_shift_prefix=None,
):
	doc = frappe.get_doc("Shaft Production Run", shaft_production_run)
	if run_date not in (None, ""):
		doc.run_date = run_date
	if custom_unit not in (None, "") and str(custom_unit).strip():
		doc.custom_unit = custom_unit
	if shift not in (None, "") and str(shift).strip():
		doc.shift = shift
	rd_val = doc.run_date
	cu = _cstr(doc.get("custom_unit"))
	sh = _cstr(doc.shift)
	if not rd_val or not cu or not sh:
		frappe.throw(_("Set Run Date, Unit, and Shift to assign batch numbers."))
	rd = getdate(rd_val)
	comp_id, unit_num = doc._batch_prefix_parts()
	root_5 = f"{comp_id}-{unit_num}{rd.month:02d}{rd.year % 100:02d}"
	fresh_prefix = doc._resolve_series_prefix(root_5)
	csp = _cstr(client_series_prefix).strip()
	if csp and csp.startswith(root_5) and csp == fresh_prefix:
		series_prefix = csp
	else:
		series_prefix = fresh_prefix
	if cint(gsm_shift_prefix) and csp:
		series_prefix = csp
		next_roll = _spr_roll_starting_for_gsm_session(
			series_prefix,
			spr_name=shaft_production_run,
			existing_batches=existing_batches,
			client_max_roll=client_max_roll,
		)
	else:
		next_roll = doc._next_roll_starting(series_prefix)
		try:
			if client_max_roll is not None and cint(client_max_roll) >= 0 and csp == series_prefix:
				next_roll = max(int(next_roll), cint(client_max_roll) + 1)
		except Exception:
			pass
	item_meta = frappe.get_meta("Shaft Production Run Item")
	used_batches = _spr_used_batch_numbers_on_spr(shaft_production_run, doc.items)
	if existing_batches:
		if isinstance(existing_batches, str):
			try:
				extra = json.loads(existing_batches) or []
			except Exception:
				extra = [x.strip() for x in existing_batches.split(",") if x.strip()]
		elif isinstance(existing_batches, (list, tuple)):
			extra = list(existing_batches)
		else:
			extra = []
		for bn in extra:
			bn = _cstr(bn).strip()
			if bn:
				used_batches.add(bn)
	out = []
	for _i in range(count):
		bn, next_roll = _spr_next_available_batch_no(
			series_prefix, next_roll, used_batches, exclude_spr=shaft_production_run
		)
		used_batches.add(bn)
		rf = item_meta.get_field("roll_no")
		rn = int(next_roll) if rf and rf.fieldtype == "Int" else str(next_roll)
		out.append({"batch_no": bn, "roll_no": rn})
		next_roll += 1
	return out


def _spr_parse_manual_item_codes(sj) -> list[str]:
	"""Parse manual_items from shaft job (comma list or JSON array)."""
	mi = _cstr(getattr(sj, "manual_items", None) or "").strip()
	if not mi:
		return []
	if mi.startswith("["):
		try:
			parsed = json.loads(mi)
			if isinstance(parsed, list):
				return [_cstr(x) for x in parsed if _cstr(x)]
		except Exception:
			pass
	return [_cstr(x) for x in mi.replace("\n", ",").split(",") if _cstr(x)]


def _spr_item_line_from_manual_item(job_row, job_id, item_code, planned_qty, width_inch=None, meter_roll=None):
	item_code = _cstr(item_code)
	item_name = frappe.db.get_value("Item", item_code, "item_name") or ""
	specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
	_gsm, parsed_width = parse_item_code(item_code)
	if width_inch is not None and flt(width_inch) > 0:
		parsed_width = flt(width_inch)
	gsm = cint(specs.get("gsm") or 0) or (int(flt(_gsm)) if _gsm else 0)
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	row: dict = {
		"item_code": item_code,
		"item_name": item_name,
		"quality": specs.get("quality") or getattr(job_row, "quality", None) or "",
		"gsm": gsm or getattr(job_row, "gsm", None) or 0,
		"planned_qty": flt(planned_qty),
		"job": job_id,
		"batch_no": "",
		"party_code": _cstr(getattr(job_row, "party_code", None) or ""),
		"uom": _item_stock_uom_for_spr(item_code),
		"roll_no": 0,
		"meter_roll": flt(meter_roll) if meter_roll else 0,
		"net_weight": 0,
		"gross_weight": 0,
		"width_inch": parsed_width,
		"color": specs.get("color") or getattr(job_row, "color", None) or "",
	}
	if spi_meta.has_field("custom_fabric_gsm"):
		fab_gsm = _fabric_gsm_from_item_name(item_name) or _fabric_gsm_from_item_name(item_code)
		if fab_gsm > 0:
			row["custom_fabric_gsm"] = fab_gsm
	return row


def _spr_job_max_roll_lines(job_row, spr_doc=None) -> int:
	"""Max Roll Production Result lines allowed for one Available Jobs row."""
	comb = getattr(job_row, "combination", None) or ""
	segs = _count_combination_segments(comb)
	no_shafts = max(1, cint(getattr(job_row, "no_of_shafts", 0) or 0))
	rolls_per_shaft = max(1, cint(getattr(job_row, "no_of_rolls", 0) or 0))

	if spr_doc and spr_doc_is_mix_roll(spr_doc):
		widths = _parse_combination_widths_inches(comb)
		item_codes = _spr_parse_manual_item_codes(job_row)
		n_items = len(item_codes) if item_codes else 0
		if widths:
			return max(1, max(len(widths), n_items) * rolls_per_shaft)
		return max(1, max(no_shafts, n_items) * rolls_per_shaft)

	if segs <= 1:
		return max(1, no_shafts * rolls_per_shaft)
	return max(1, no_shafts * segs * rolls_per_shaft)


def _spr_count_roll_lines_for_job(spr_doc, job_id, produced_only: bool = False) -> int:
	job_key = _cstr(job_id).strip()
	if not job_key:
		return 0
	cnt = 0
	for it in spr_doc.get("items") or []:
		if not _spr_job_keys_match(_cstr(getattr(it, "job", None)), job_key):
			continue
		if not _spr_is_real_roll_item_row(it):
			continue
		if produced_only and not _spr_roll_has_production(it):
			continue
		cnt += 1
	return cnt


def _spr_count_roll_lines_for_job_width(spr_doc, job_id, width_inch, produced_only: bool = False) -> int:
	"""Count real roll lines for one job + width segment (within 0.05 inch)."""
	job_key = _cstr(job_id).strip()
	target_w = flt(width_inch)
	if not job_key or target_w <= 0:
		return 0
	cnt = 0
	for it in spr_doc.get("items") or []:
		if not _spr_job_keys_match(_cstr(getattr(it, "job", None)), job_key):
			continue
		if not _spr_is_real_roll_item_row(it):
			continue
		if produced_only and not _spr_roll_has_production(it):
			continue
		w = flt(getattr(it, "width_inch", None) or 0)
		if abs(w - target_w) < 0.05:
			cnt += 1
	return cnt


def _gsm_job_roll_limits_from_job_row(job_row) -> dict:
	"""GSM job max rolls + per-width caps (matches get_gsm_pp_job_board)."""
	comb = _cstr(getattr(job_row, "combination", None) or "")
	widths = _parse_combination_widths_inches(comb) if comb else []
	if not widths:
		tw = flt(getattr(job_row, "total_width", None) or getattr(job_row, "width", None) or 0)
		if tw > 0:
			widths = [tw]
	no_shafts = max(1, cint(getattr(job_row, "no_of_shafts", 0) or 0))
	rolls_per_shaft = max(1, len(widths)) if len(widths) > 1 else 1
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
		"max_rolls": max_rolls,
		"max_shafts": no_shafts,
		"rolls_per_shaft": rolls_per_shaft,
		"width_caps": width_caps,
		"width_requirements": width_requirements,
	}


def _gsm_job_row_for_quota(spr, pp_id: str, job_id: str):
	job_key = _cstr(job_id).strip()
	if not job_key:
		return None
	for sj in _spr_job_rows(spr):
		if _spr_job_keys_match(_spr_job_id(sj), job_key):
			return sj
	if pp_id and frappe.db.exists("Production Plan", pp_id):
		pp = frappe.get_doc("Production Plan", pp_id)
		for idx, shaft in enumerate(pp.get("custom_shaft_details") or pp.get("shaft_details") or [], start=1):
			row_job = _cstr(
				_spr_row_get(shaft, "job_id")
				or _spr_row_get(shaft, "job")
				or _spr_row_get(shaft, "job_no")
				or str(idx)
			)
			if _spr_job_keys_match(row_job, job_key):
				return shaft
	return None


def _gsm_spr_names_for_pp_job_counts(pp_id: str, unit: str | None = None) -> list[str]:
	pp_id = _cstr(pp_id).strip()
	if not pp_id:
		# Empty PP must not scan the unit: Frappe can drop blank Link filters and then
		# job "1" on mix SPR is counted against every fabric SPR in the same warehouse.
		return []
	filters = {"production_plan": pp_id, "docstatus": ["<", 2]}
	if unit:
		filters["custom_unit"] = _cstr(unit).strip()
	rows = frappe.get_all(
		"Shaft Production Run",
		filters=filters,
		fields=["name"],
		order_by="modified desc",
		limit=200,
	)
	return [_cstr(r.get("name")) for r in rows if _cstr(r.get("name"))]


def _gsm_count_job_rolls_all_sprs(
	pp_id: str, job_id: str, unit: str | None = None, produced_only: bool = True
) -> int:
	job_key = _cstr(job_id).strip()
	if not job_key:
		return 0
	total = 0
	for sn in _gsm_spr_names_for_pp_job_counts(pp_id, unit=unit):
		spr = frappe.get_doc("Shaft Production Run", sn)
		total += _spr_count_roll_lines_for_job(spr, job_key, produced_only=produced_only)
	return total


def _gsm_count_job_width_rolls_all_sprs(
	pp_id: str, job_id: str, width_inch, unit: str | None = None, produced_only: bool = True
) -> int:
	job_key = _cstr(job_id).strip()
	if not job_key:
		return 0
	total = 0
	for sn in _gsm_spr_names_for_pp_job_counts(pp_id, unit=unit):
		spr = frappe.get_doc("Shaft Production Run", sn)
		total += _spr_count_roll_lines_for_job_width(spr, job_key, width_inch, produced_only=produced_only)
	return total


def _gsm_batch_series_prefix(batch_no: str) -> str:
	bn = _cstr(batch_no).strip()
	if "/" in bn:
		return bn.rsplit("/", 1)[0].strip()
	return bn


def _spr_item_counts_toward_gsm_job_quota(item_row, series_prefix: str = "") -> bool:
	"""Count produced rolls, plus this shift's GSM batches even before weight is entered."""
	if not _spr_is_real_roll_item_row(item_row):
		return False
	if _spr_roll_has_production(item_row):
		return True
	prefix = _cstr(series_prefix).strip()
	if not prefix:
		return False
	bn = _cstr(getattr(item_row, "batch_no", "") or "").strip()
	return bn == prefix or bn.startswith(prefix + "/")


def _spr_count_gsm_quota_rolls_for_job(spr_doc, job_id, series_prefix: str = "") -> int:
	job_key = _cstr(job_id).strip()
	if not job_key:
		return 0
	cnt = 0
	for it in spr_doc.get("items") or []:
		if not _spr_job_keys_match(_cstr(getattr(it, "job", None)), job_key):
			continue
		if _spr_item_counts_toward_gsm_job_quota(it, series_prefix):
			cnt += 1
	return cnt


def _spr_count_gsm_quota_rolls_for_job_width(spr_doc, job_id, width_inch, series_prefix: str = "") -> int:
	job_key = _cstr(job_id).strip()
	target_w = flt(width_inch)
	if not job_key or target_w <= 0:
		return 0
	cnt = 0
	for it in spr_doc.get("items") or []:
		if not _spr_job_keys_match(_cstr(getattr(it, "job", None)), job_key):
			continue
		if not _spr_item_counts_toward_gsm_job_quota(it, series_prefix):
			continue
		w = flt(getattr(it, "width_inch", None) or 0)
		if abs(w - target_w) < 0.05:
			cnt += 1
	return cnt


def _gsm_count_job_rolls_for_gsm_quota(spr, pp_id: str, job_id: str, batch_no: str) -> int:
	"""Produced rolls on this unit + this shift's GSM batches on the current SPR."""
	prefix = _gsm_batch_series_prefix(batch_no)
	job_key = _cstr(job_id).strip()
	current_name = _cstr(getattr(spr, "name", "") or "")
	unit = _cstr(spr.get("custom_unit") or "").strip()
	total = _spr_count_gsm_quota_rolls_for_job(spr, job_key, prefix)
	if not pp_id or not unit:
		return total
	for sn in _gsm_spr_names_for_pp_job_counts(pp_id, unit=unit):
		if sn == current_name:
			continue
		other = frappe.get_doc("Shaft Production Run", sn)
		total += _spr_count_roll_lines_for_job(other, job_key, produced_only=True)
	return total


def _gsm_count_job_width_rolls_for_gsm_quota(spr, pp_id: str, job_id: str, width_inch, batch_no: str) -> int:
	prefix = _gsm_batch_series_prefix(batch_no)
	job_key = _cstr(job_id).strip()
	current_name = _cstr(getattr(spr, "name", "") or "")
	unit = _cstr(spr.get("custom_unit") or "").strip()
	total = _spr_count_gsm_quota_rolls_for_job_width(spr, job_key, width_inch, prefix)
	if not pp_id or not unit:
		return total
	for sn in _gsm_spr_names_for_pp_job_counts(pp_id, unit=unit):
		if sn == current_name:
			continue
		other = frappe.get_doc("Shaft Production Run", sn)
		total += _spr_count_roll_lines_for_job_width(other, job_key, width_inch, produced_only=True)
	return total


def _gsm_enforce_job_roll_quota_on_add(
	spr,
	pp_id: str,
	job_id: str,
	width_inch,
	batch_no: str,
) -> None:
	"""Block new GSM roll lines past PP job max rolls / per-width caps."""
	if _gsm_find_item_row_by_batch(spr, batch_no):
		return
	# Mix SPRs have no Production Plan. Job id is always "1", so a unit-wide
	# job-1 count picks up fabric rolls (e.g. six 21" lines) and blocks the first mix save.
	if spr_doc_is_mix_roll(spr):
		job_row = _gsm_job_row_for_quota(spr, "", job_id)
		if not job_row:
			return
		max_rolls = _spr_job_max_roll_lines(job_row, spr)
		if max_rolls <= 0:
			return
		current = _spr_count_gsm_quota_rolls_for_job(spr, job_id, _gsm_batch_series_prefix(batch_no))
		if current >= max_rolls:
			_spr_throw_roll_quota_exceeded(job_id, max_rolls, current)
		return
	job_row = _gsm_job_row_for_quota(spr, pp_id, job_id)
	if not job_row:
		return
	limits = _gsm_job_roll_limits_from_job_row(job_row)
	max_rolls = cint(limits.get("max_rolls") or 0)
	if max_rolls <= 0:
		return
	current_rolls = _gsm_count_job_rolls_for_gsm_quota(spr, pp_id, job_id, batch_no)
	if current_rolls >= max_rolls:
		_spr_throw_roll_quota_exceeded(job_id, max_rolls, current_rolls)
	target_w = flt(width_inch)
	if target_w <= 0:
		return
	width_caps = limits.get("width_caps") or {}
	for fw, cap in width_caps.items():
		if abs(target_w - flt(fw)) >= 0.05:
			continue
		cur = _gsm_count_job_width_rolls_for_gsm_quota(spr, pp_id, job_id, fw, batch_no)
		if cint(cap) > 0 and cur >= cint(cap):
			frappe.throw(
				_(
					"Maximum {0} roll lines allowed at width {1}\" for job {2} ({3} already created). "
					"Use Manual Job for additional production."
				).format(cap, fw, job_id, cur),
				title=_("Width roll limit reached"),
			)
		break


def _spr_throw_roll_quota_exceeded(job_id, max_rolls: int, current_rolls: int) -> None:
	frappe.throw(
		_(
			"Maximum {0} roll lines allowed for job {1} ({2} already created). "
			"Use Manual Job for additional production."
		).format(max_rolls, job_id, current_rolls),
		title=_("Roll line limit reached"),
	)


def _build_mix_roll_result_lines_for_job(
	spr_doc, job_row, exact_roll_lines=None, roll_start_index=None
):
	"""Build roll lines for mix-roll SPR from manual_items (no Work Order)."""
	job_id = _spr_job_id(job_row)
	item_codes = _spr_parse_manual_item_codes(job_row)
	if not item_codes:
		frappe.throw(_("Mix roll job {0} has no manual items.").format(job_id))

	comb = getattr(job_row, "combination", None) or ""
	widths = _parse_combination_widths_inches(comb)
	no_shafts = max(1, cint(getattr(job_row, "no_of_shafts", 0) or 0))
	rolls_per_shaft = max(1, cint(getattr(job_row, "no_of_rolls", 0) or 0))
	exact_n = cint(exact_roll_lines or 0)
	start_idx = max(0, cint(roll_start_index or 0))
	max_job_rolls = _spr_job_max_roll_lines(job_row, spr_doc)

	if exact_n > 0:
		if spr_doc_is_mix_roll(spr_doc):
			n_rolls = max(1, exact_n)
			planned_total_rolls = max(max_job_rolls, 1)
		else:
			current = _spr_count_roll_lines_for_job(spr_doc, job_id)
			if current + exact_n > max_job_rolls:
				_spr_throw_roll_quota_exceeded(job_id, max_job_rolls, current)
			n_rolls = max(1, exact_n)
			planned_total_rolls = max_job_rolls
	elif widths:
		n_rolls = max(len(widths), len(item_codes)) * rolls_per_shaft
		planned_total_rolls = n_rolls
		start_idx = 0
	else:
		n_rolls = max(no_shafts, len(item_codes)) * rolls_per_shaft
		planned_total_rolls = n_rolls
		start_idx = 0

	total_weight = flt(getattr(job_row, "total_weight", None) or 0)
	fallback_planned_each = flt(total_weight / planned_total_rolls) if total_weight > 0 else 0

	meter_roll_job = None
	mr_attr = getattr(job_row, "meter_roll_mtrs", None)
	if mr_attr not in (None, "", 0):
		meter_roll_job = flt(mr_attr)

	spi_meta = frappe.get_meta("Shaft Production Run Item")
	segs = max(1, len(widths)) if widths else max(1, _count_combination_segments(comb))
	lines = []
	for i in range(n_rolls):
		idx = start_idx + i
		item_code = item_codes[idx % len(item_codes)]
		width_inch = widths[idx % len(widths)] if widths else None
		row = _spr_item_line_from_manual_item(
			job_row,
			job_id,
			item_code,
			0,
			width_inch=width_inch,
			meter_roll=meter_roll_job,
		)
		gsm_val = flt(row.get("gsm") or 0)
		w_in = flt(row.get("width_inch") or 0)
		length_m = meter_roll_job or _spr_mix_roll_planned_length_m(row)
		planned_each = compute_mix_roll_planned_qty_kg(gsm_val, w_in, length_m)
		if planned_each <= 0:
			planned_each = fallback_planned_each
		row["planned_qty"] = planned_each
		row["roll_no"] = idx + 1
		if spi_meta.has_field("custom_no_of_shaft"):
			row["custom_no_of_shaft"] = _spr_shaft_no_for_roll_index(
				idx, no_shafts, rolls_per_shaft, segs=segs
			)
		lines.append(row)
	return lines


@frappe.whitelist()
def build_spr_roll_result_lines_for_job(
	shaft_production_run,
	job_id,
	lamination_rolls_per_combination=None,
	lamination_exact_roll_lines=None,
	exact_roll_lines=None,
	roll_start_index=None,
):
	"""
	Build Roll Production Result (SPR Item) lines for one job.
	
	Γ£à CORRECT: Extract GSM and WIDTH from Item Name, then match exactly.
	Combination "33+63" cycles rolls through widths [33, 63, 33, 63, ...]
	Each roll matched to correct WO by (GSM, WIDTH) tuple lookup.

	Lamination (104 + Is Lamination): pass ``lamination_rolls_per_combination`` = rolls per combination
	segment; total lines = segments × that number (shaft × roll formula is not used).
	"""
	if not job_id:
		frappe.throw(_("Job ID is required"))
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		frappe.throw(_("Save Shaft Production Run first"))
	spr_doc = frappe.get_doc("Shaft Production Run", shaft_production_run)
	if cint(spr_doc.docstatus) != 0:
		frappe.throw(_("Cannot add roll lines to a submitted Shaft Production Run"))
	job_row = None
	for j in _spr_job_rows(spr_doc):
		if _spr_job_keys_match(_spr_job_id(j), job_id):
			job_row = j
			break
	if not job_row:
		frappe.throw(_("Job {0} not found in Available Jobs").format(job_id))

	has_pinned_wos = bool(_cstr(getattr(job_row, "work_orders", None) or "").strip())
	if not has_pinned_wos and (
		spr_doc_is_mix_roll(spr_doc)
		or (
			_spr_is_manual_shaft_job(job_row)
			and _spr_parse_manual_item_codes(job_row)
			and not _cstr(getattr(spr_doc, "production_plan", None))
		)
	):
		return _build_mix_roll_result_lines_for_job(
			spr_doc,
			job_row,
			exact_roll_lines=exact_roll_lines,
			roll_start_index=roll_start_index,
		)

	pp_name = get_pp_from_spr(shaft_production_run)
	if not pp_name and has_pinned_wos:
		pp_name = None
	elif not pp_name or not frappe.db.exists("Production Plan", pp_name):
		if has_pinned_wos:
			pp_name = None
		else:
			frappe.throw(_("Production Plan not found on this Shaft Production Run"))

	no_shafts = int(flt(getattr(job_row, "no_of_shafts", 0) or 0))
	if no_shafts < 1:
		no_shafts = 1
	comb = getattr(job_row, "combination", None) or ""
	segs = _count_combination_segments(comb)
	rolls_per_shaft = cint(getattr(job_row, "no_of_rolls", 0) or 0)
	if rolls_per_shaft < 1:
		rolls_per_shaft = 1

	lam_exact_n = cint(lamination_exact_roll_lines or 0)
	lam_n = cint(lamination_rolls_per_combination or 0)
	exact_n = cint(exact_roll_lines or 0)
	if exact_n > 0:
		n_rolls = max(1, exact_n)
	elif lam_exact_n > 0:
		if not spr_doc_is_lamination(spr_doc):
			frappe.throw(
				_("Exact roll-line add mode is only for lamination: tick Is Lamination and use a 104 or 107 production plan.")
			)
		n_rolls = max(1, lam_exact_n)
	elif lam_n > 0:
		if not spr_doc_is_lamination(spr_doc):
			frappe.throw(
				_("Rolls-per-combination mode is only for lamination: tick Is Lamination and use a 104 or 107 production plan.")
			)
		n_rolls = max(1, segs * lam_n)
	elif spr_doc_is_lamination(spr_doc):
		frappe.throw(
			_("For lamination runs, enter **Number of rolls per combination** when clicking Create Entry (e.g. 10 rolls × 2 combinations = 20 lines).")
		)
	elif segs <= 1:
		n_rolls = max(1, no_shafts * rolls_per_shaft)
	else:
		n_rolls = max(1, no_shafts * segs * rolls_per_shaft)

	start_idx = max(0, cint(roll_start_index or 0))
	max_job_rolls = _spr_job_max_roll_lines(job_row, spr_doc)
	use_quota_append = exact_n > 0 and not spr_doc_is_lamination(spr_doc) and not lam_exact_n and lam_n <= 0
	if use_quota_append:
		current = _spr_count_roll_lines_for_job(spr_doc, job_id)
		if current + exact_n > max_job_rolls:
			_spr_throw_roll_quota_exceeded(job_id, max_job_rolls, current)
		planned_total_rolls = max_job_rolls
		roll_indices = range(start_idx, start_idx + exact_n)
	else:
		planned_total_rolls = n_rolls
		roll_indices = range(n_rolls)
		start_idx = 0

	shaft_combination = get_shaft_combination(pp_name, job_id)
	if getattr(job_row, "combination", None) and not shaft_combination:
		shaft_combination = job_row.combination

	wo_list = _get_work_orders_for_spr_job(pp_name, spr_doc, job_row)
	if not wo_list:
		frappe.throw(_("No Work Orders for job {0}").format(job_id))

	# Extract job GSM
	job_gsm = None
	if getattr(job_row, "gsm", None) not in (None, 0, "0"):
		try:
			job_gsm = int(flt(job_row.gsm))
		except Exception:
			pass
	
	individual_widths = _parse_combination_widths_inches(comb)

	gsm_width_to_wo = _build_gsm_width_to_wo_map_from_item_names(wo_list)

	meter_roll_job = None
	mr_attr = getattr(job_row, "meter_roll_mtrs", None)
	if mr_attr not in (None, "", 0):
		meter_roll_job = flt(mr_attr)

	spi_meta = frappe.get_meta("Shaft Production Run Item")
	fabric_gsm = _fabric_gsm_from_planning_for_pp(pp_name) if spr_doc_is_lamination(spr_doc) else 0

	lines = []
	for roll_i, idx in enumerate(roll_indices):
		individual_width = None
		if individual_widths:
			individual_width = individual_widths[idx % len(individual_widths)]

		wo = None
		if job_gsm is not None and individual_width is not None:
			iw = round(flt(individual_width), 1)
			key = (job_gsm, iw)
			if key in gsm_width_to_wo:
				wo = gsm_width_to_wo[key]
			else:
				for (g, w), wobj in gsm_width_to_wo.items():
					if int(g) == int(job_gsm) and abs(flt(w) - iw) <= 0.75:
						wo = wobj
						break
			if wo:
				frappe.logger().info(
					f"[SPR] Roll {idx + 1}: GSM {job_gsm} + Width {iw}\" ΓåÆ WO {wo['name']}"
				)

		if wo is None:
			wo = wo_list[0]
			frappe.logger().warning(
				f"[SPR WARNING] No exact match for GSM {job_gsm}, Width {individual_width}, using {wo['name']}"
			)

		if spr_doc_is_lamination(spr_doc):
			planned_qty = 0.0
		else:
			planned_qty = _planned_kg_for_spr_result_roll(job_row, idx, planned_total_rolls, segs)
		row = _spr_item_line_from_wo(pp_name, job_id, shaft_combination, planned_qty, wo)
		if not _cstr(row.get("party_code")) and _cstr(getattr(job_row, "party_code", None)):
			row["party_code"] = _cstr(job_row.party_code)
		if job_gsm is not None:
			row["gsm"] = job_gsm
		if individual_width is not None and flt(individual_width) > 0:
			row["width_inch"] = flt(individual_width)
		if meter_roll_job is not None and meter_roll_job > 0:
			row["meter_roll"] = meter_roll_job
			# Lamination add-flow: removed auto-fill of produced_length_mtrs based on user request

		# Fabric GSM: prefer Planning Table join result; fallback to parsing F-<N> from item name.
		eff_fabric_gsm = fabric_gsm
		if eff_fabric_gsm <= 0 and spr_doc_is_lamination(spr_doc):
			item_nm = row.get("item_name") or ""
			item_cd = row.get("item_code") or ""
			eff_fabric_gsm = _fabric_gsm_from_item_name(item_nm) or _fabric_gsm_from_item_name(item_cd)
		if eff_fabric_gsm > 0 and spi_meta.has_field("custom_fabric_gsm"):
			row["custom_fabric_gsm"] = int(eff_fabric_gsm)

		row["roll_no"] = idx + 1
		if spi_meta.has_field("custom_no_of_shaft"):
			row["custom_no_of_shaft"] = _spr_shaft_no_for_roll_index(
				idx, no_shafts, rolls_per_shaft, segs=segs
			)
		lines.append(row)
	return lines


def _spr_shaft_no_for_roll_index(
	idx: int, no_shafts: int, rolls_per_shaft: int, segs: int = 1
) -> int:
	"""1-based shaft number for a roll at zero-based index idx within a job."""
	no_shafts = max(1, cint(no_shafts or 0))
	rolls_per_shaft = max(1, cint(rolls_per_shaft or 0))
	segs = max(1, cint(segs or 1))
	effective_rolls = rolls_per_shaft * segs if segs > 1 else rolls_per_shaft
	return min(no_shafts, idx // effective_rolls + 1)


def _spr_job_shaft_limits(job_row) -> tuple[int, int, int]:
	"""Return (no_shafts, rolls_per_shaft, segs) for a shaft_jobs row."""
	no_shafts = max(1, cint(getattr(job_row, "no_of_shafts", 0) or getattr(job_row, "no_of_shaft", 0) or 0))
	rolls_per_shaft = max(1, cint(getattr(job_row, "no_of_rolls", 0) or 0))
	comb = _cstr(getattr(job_row, "combination", None) or "")
	segs = max(1, _count_combination_segments(comb))
	return no_shafts, rolls_per_shaft, segs


def _gsm_roll_sort_key_for_shaft(item_row) -> int:
	rn = cint(getattr(item_row, "roll_no", 0) or 0)
	if rn > 0:
		return rn
	return _gsm_roll_suffix_from_batch_no(_cstr(getattr(item_row, "batch_no", "")))


def _gsm_sorted_job_roll_items(spr, job_id: str) -> list:
	"""Real roll item rows for one job, sorted by roll_no / batch suffix."""
	job_id = _cstr(job_id).strip()
	rows = []
	for it in spr.items or []:
		if not _spr_is_real_roll_item_row(it):
			continue
		if _cstr(getattr(it, "job", "")).strip() != job_id:
			continue
		rows.append(it)
	rows.sort(key=_gsm_roll_sort_key_for_shaft)
	return rows


def _gsm_roll_index_for_item(spr, job_id: str, item_row) -> int:
	"""Zero-based roll index for an item row within its job."""
	sorted_rows = _gsm_sorted_job_roll_items(spr, job_id)
	target = _cstr(getattr(item_row, "name", "")).strip()
	batch = _cstr(getattr(item_row, "batch_no", "")).strip()
	for idx, row in enumerate(sorted_rows):
		if target and _cstr(getattr(row, "name", "")).strip() == target:
			return idx
		if batch and _cstr(getattr(row, "batch_no", "")).strip() == batch:
			return idx
	payload_idx = cint(getattr(item_row, "roll_no", 0) or 0)
	if payload_idx > 0:
		return max(0, payload_idx - 1)
	suffix = _gsm_roll_suffix_from_batch_no(batch)
	if suffix > 0:
		return max(0, suffix - 1)
	return len(sorted_rows)


def _gsm_resolve_shaft_no_for_roll(spr, job_id: str, item_row=None, payload: dict | None = None) -> int:
	"""Compute 1-based shaft number for a GSM/SPR roll line."""
	job_id = _cstr(job_id).strip()
	if not job_id:
		return 0
	job_row = _spr_shaft_job_for_roll(spr, job_id)
	if not job_row:
		return 0
	no_shafts, rolls_per_shaft, segs = _spr_job_shaft_limits(job_row)
	idx = 0
	if item_row is not None:
		idx = _gsm_roll_index_for_item(spr, job_id, item_row)
	elif isinstance(payload, dict):
		pseudo = frappe._dict(
			name=_cstr(payload.get("spr_item_name") or ""),
			batch_no=_cstr(payload.get("batch_no") or ""),
			roll_no=cint(payload.get("roll_no") or 0),
		)
		idx = _gsm_roll_index_for_item(spr, job_id, pseudo)
		if idx <= 0 and payload.get("roll_no"):
			idx = max(0, cint(payload.get("roll_no")) - 1)
		elif idx <= 0:
			idx = len(_gsm_sorted_job_roll_items(spr, job_id))
	return _spr_shaft_no_for_roll_index(idx, no_shafts, rolls_per_shaft, segs)


def _backfill_spr_roll_shaft_numbers_for_doc(spr, save: bool = True) -> dict:
	"""Fix custom_no_of_shaft=0 on draft SPR roll lines using job roll order."""
	if cint(getattr(spr, "docstatus", 0)) != 0:
		return {"status": "skipped", "reason": "submitted", "rows_fixed": 0, "details": []}
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	if not spi_meta.has_field("custom_no_of_shaft"):
		return {"status": "skipped", "reason": "no_field", "rows_fixed": 0, "details": []}

	details = []
	rows_fixed = 0
	jobs_seen = set()
	for it in spr.items or []:
		if not _spr_is_real_roll_item_row(it):
			continue
		job_id = _cstr(getattr(it, "job", "")).strip()
		if not job_id:
			continue
		current = cint(getattr(it, "custom_no_of_shaft", 0) or 0)
		if current > 0:
			continue
		jobs_seen.add(job_id)

	for job_id in jobs_seen:
		for idx, it in enumerate(_gsm_sorted_job_roll_items(spr, job_id)):
			current = cint(getattr(it, "custom_no_of_shaft", 0) or 0)
			if current > 0:
				continue
			job_row = _spr_shaft_job_for_roll(spr, job_id)
			if not job_row:
				continue
			no_shafts, rolls_per_shaft, segs = _spr_job_shaft_limits(job_row)
			new_shaft = _spr_shaft_no_for_roll_index(idx, no_shafts, rolls_per_shaft, segs)
			if new_shaft <= 0:
				continue
			it.custom_no_of_shaft = new_shaft
			rows_fixed += 1
			details.append(
				{
					"batch_no": _cstr(getattr(it, "batch_no", "")),
					"job": job_id,
					"old_shaft": current,
					"new_shaft": new_shaft,
					"row_name": _cstr(getattr(it, "name", "")),
				}
			)

	if rows_fixed and save:
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)

	return {
		"status": "ok",
		"spr_name": spr.name,
		"rows_fixed": rows_fixed,
		"details": details,
	}


@frappe.whitelist()
def backfill_spr_roll_shaft_numbers(spr_name=None):
	"""Manual / GSM button — recompute No. of Shaft on draft SPR roll lines."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	return _backfill_spr_roll_shaft_numbers_for_doc(spr, save=True)


def _spr_max_roll_suffix_for_job(spr_doc, job_id: str) -> int:
	"""Highest roll suffix already on this job's lines (batch /N or roll_no)."""
	job_id = _cstr(job_id)
	mx = 0
	for row in spr_doc.items or []:
		if _cstr(getattr(row, "job", None)) != job_id:
			continue
		bn = _cstr(getattr(row, "batch_no", "")).strip()
		if "/" in bn:
			try:
				mx = max(mx, cint(bn.rsplit("/", 1)[-1]))
			except Exception:
				pass
		rn = cint(getattr(row, "roll_no", 0) or 0)
		if rn > mx:
			mx = rn
	return mx


def _spr_existing_series_prefix_for_job(spr_doc, job_id: str) -> str:
	"""Batch prefix (before /) from an existing roll line on this job."""
	job_id = _cstr(job_id)
	for row in spr_doc.items or []:
		if _cstr(getattr(row, "job", None)) != job_id:
			continue
		bn = _cstr(getattr(row, "batch_no", "")).strip()
		if "/" in bn:
			return bn.split("/", 1)[0].strip()
	return ""


def _gsm_shaft_width_inch(shaft_row) -> float:
	w = flt(_spr_row_get(shaft_row, "total_width") or _spr_row_get(shaft_row, "combined_width") or 0)
	if w > 0:
		return w
	comb = _cstr(
		_spr_row_get(shaft_row, "combination")
		or _spr_row_get(shaft_row, "combined_width")
		or _spr_row_get(shaft_row, "shaft")
		or _spr_row_get(shaft_row, "shaft_details")
	)
	if comb and "+" not in comb:
		try:
			return flt(comb)
		except Exception:
			pass
	widths = _parse_combination_widths_inches(comb) if comb else []
	return flt(widths[0]) if widths else 0.0


def _gsm_shaft_gsm(shaft_row) -> int:
	try:
		return int(flt(_spr_row_get(shaft_row, "gsm") or 0))
	except Exception:
		return 0


def _gsm_resolve_job_id_for_roll(spr, pp_id: str, payload: dict) -> str:
	"""Resolve shaft job id for a GSM roll line — honors explicit job_id from operator."""
	explicit_job = _cstr(payload.get("job_id") or payload.get("job")).strip()
	if explicit_job:
		if _spr_shaft_job_for_roll(spr, explicit_job):
			return explicit_job
		if pp_id and frappe.db.exists("Production Plan", pp_id):
			pp = frappe.get_doc("Production Plan", pp_id)
			pp_shafts = pp.get("custom_shaft_details") or pp.get("shaft_details") or []
			for idx, shaft in enumerate(pp_shafts, start=1):
				if _spr_job_keys_match(str(idx), explicit_job):
					return explicit_job

	ppi = _cstr(payload.get("planning_table_row") or payload.get("production_plan_item")).strip()
	target_gsm = cint(payload.get("gsm") or 0)
	target_w = flt(payload.get("width_inch") or 0)

	for sj in _spr_job_rows(spr):
		if ppi and _cstr(getattr(sj, "production_plan_item", None)) == ppi:
			return _cstr(_spr_job_id(sj))
		jg = cint(getattr(sj, "gsm", 0) or 0)
		if target_gsm and jg and jg != target_gsm:
			continue
		comb = _cstr(getattr(sj, "combination", None))
		if target_w > 0:
			widths = _parse_combination_widths_inches(comb) if comb else []
			if widths:
				if any(abs(flt(w) - target_w) < 0.05 for w in widths):
					return _cstr(_spr_job_id(sj))
			elif abs(_gsm_shaft_width_inch(sj) - target_w) < 0.05:
				return _cstr(_spr_job_id(sj))
			elif comb and abs(flt(comb) - target_w) < 0.05:
				return _cstr(_spr_job_id(sj))

	if not pp_id or not frappe.db.exists("Production Plan", pp_id):
		return ""
	pp = frappe.get_doc("Production Plan", pp_id)
	pp_shafts = pp.get("custom_shaft_details") or pp.get("shaft_details") or []
	for idx, shaft in enumerate(pp_shafts, start=1):
		if target_gsm and _gsm_shaft_gsm(shaft) != target_gsm:
			continue
		if target_w > 0:
			comb = _cstr(
				_spr_row_get(shaft, "combination")
				or _spr_row_get(shaft, "combined_width")
				or _spr_row_get(shaft, "shaft")
				or _spr_row_get(shaft, "shaft_details")
			)
			widths = _parse_combination_widths_inches(comb) if comb else []
			width_match = abs(_gsm_shaft_width_inch(shaft) - target_w) < 0.05
			if widths and not width_match:
				width_match = any(abs(flt(w) - target_w) < 0.05 for w in widths)
			if not width_match:
				continue
		for sj in _spr_job_rows(spr):
			jid = _cstr(_spr_job_id(sj))
			if jid == str(idx):
				return jid
	return ""


def _gsm_find_item_row_by_batch(spr, batch_no: str):
	bn = _cstr(batch_no).strip()
	if not bn:
		return None
	for row in spr.items or []:
		if _cstr(getattr(row, "batch_no", "")).strip() == bn:
			return row
	return None


def _gsm_find_reusable_item_row(spr, job_id, width_inch, batch_no: str = ""):
	"""Reuse an unproduced planned slot for this job instead of appending past quota.

	Prefer a planned row whose roll_no matches the new batch suffix so FIFO batch
	order stays aligned with SPR idx / display #.
	"""
	job_key = _cstr(job_id).strip()
	if not job_key:
		return None
	target_w = flt(width_inch)
	want_suffix = 0
	bn = _cstr(batch_no).strip()
	if "/" in bn:
		try:
			want_suffix = cint(bn.rsplit("/", 1)[-1])
		except Exception:
			want_suffix = 0
	width_matches = []
	any_matches = []
	suffix_match = None
	for row in spr.get("items") or []:
		if not _spr_job_keys_match(_cstr(getattr(row, "job", None)), job_key):
			continue
		if _spr_is_bundle_summary_batch(_cstr(getattr(row, "batch_no", "") or "")):
			continue
		if _spr_roll_has_production(row):
			continue
		any_matches.append(row)
		w = flt(getattr(row, "width_inch", None) or 0)
		width_ok = target_w <= 0 or w <= 0 or abs(w - target_w) < 0.05
		if width_ok:
			width_matches.append(row)
		if want_suffix > 0 and suffix_match is None:
			rn = cint(getattr(row, "roll_no", None) or 0)
			row_suffix = 0
			existing_bn = _cstr(getattr(row, "batch_no", "") or "")
			if "/" in existing_bn:
				try:
					row_suffix = cint(existing_bn.rsplit("/", 1)[-1])
				except Exception:
					row_suffix = 0
			if rn == want_suffix or row_suffix == want_suffix:
				suffix_match = row
	if suffix_match is not None:
		return suffix_match
	if width_matches:
		return width_matches[0]
	return any_matches[0] if any_matches else None


def _gsm_spi_field_aliases() -> dict:
	"""Canonical GSM keys → SPR Item field names (first match on site wins)."""
	return {
		"custom_diameter_inches": (
			"custom_diameter_inches",
			"custom_diameter",
			"diameter",
		),
		"custom_cbm_cubic_meters": (
			"custom_cbm_cubic_meters",
			"custom_cbm",
			"cbm",
		),
		"custom_polybag_kgs": ("custom_polybag_kgs", "polybag_kgs", "custom_polybag_weight"),
		"custom_core_width_mm": ("custom_core_width_mm", "core_width"),
	}


def _gsm_spi_resolve_field(meta, canonical: str) -> str:
	for name in _gsm_spi_field_aliases().get(canonical, (canonical,)):
		if meta.has_field(name):
			return name
	return ""


def _gsm_spi_get_float(row, meta, canonical: str) -> float:
	for name in _gsm_spi_field_aliases().get(canonical, (canonical,)):
		if not meta.has_field(name):
			continue
		val = getattr(row, name, None)
		if val not in (None, ""):
			return flt(val)
	return 0.0


def _gsm_spi_set_float_from_payload(row, meta, payload: dict, canonical: str) -> None:
	field = _gsm_spi_resolve_field(meta, canonical)
	if not field:
		return
	aliases = _gsm_spi_field_aliases().get(canonical, (canonical,))
	val = None
	for key in aliases:
		if payload.get(key) not in (None, ""):
			val = payload.get(key)
			break
	if val is None or val == "":
		return
	row.set(field, flt(val))


def _gsm_resolve_core_width_for_item_row(payload: dict) -> str:
	"""SPR Item custom_core_width_mm — Core Size name or paper-core Item link."""
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	if not spi_meta.has_field("custom_core_width_mm"):
		return ""
	df = spi_meta.get_field("custom_core_width_mm")
	raw = payload.get("custom_core_width_mm")
	width_inch = flt(payload.get("width_inch") or 0)
	if raw in (None, ""):
		if width_inch > 0:
			from production_entry.production_planning.unified_production_entry_api import (
				_gsm_resolve_core_link_for_fabric_width,
			)

			return _gsm_resolve_core_link_for_fabric_width(width_inch, "")
		return ""
	if df.fieldtype != "Link":
		return _cstr(raw).strip()
	link_dt = _cstr(df.options or "Item").strip() or "Item"
	s = _cstr(raw).strip()
	if frappe.db.exists(link_dt, s):
		return s
	if width_inch > 0:
		from production_entry.production_planning.unified_production_entry_api import (
			_gsm_resolve_core_link_for_fabric_width,
		)

		return _gsm_resolve_core_link_for_fabric_width(width_inch, s)
	try:
		mm = flt(s)
	except Exception:
		mm = 0.0
	if mm <= 0:
		return ""
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
	best_code = ""
	best_diff = 1e9
	for row in rows or []:
		name = _cstr(row.get("item_name") or "")
		code = _cstr(row.get("name") or "")
		item_mm = 0.0
		import re

		m = re.search(r"(\d{3,4})\s*mm", name, re.I)
		if m:
			item_mm = flt(m.group(1))
		if item_mm <= 0:
			continue
		diff = abs(item_mm - mm)
		if diff < best_diff:
			best_diff = diff
			best_code = code
	if best_code and frappe.db.exists(link_dt, best_code):
		return best_code
	return ""


def _gsm_apply_payload_to_item_row(row, payload: dict, job_id: str, shift=None, spr=None):
	"""Map GSM Production Entry roll payload onto an SPR Item row (additive fields only)."""
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	if not _cstr(payload.get("party_code") or "").strip() and spr is not None:
		pp_id = _cstr(spr.get("production_plan")).strip()
		fallback_pc = _cstr(
			spr.get("custom_order_code")
			or spr.get("order_code")
			or ""
		)
		if not fallback_pc and pp_id:
			try:
				from production_entry.production_planning.unified_production_entry_api import (
					_gsm_order_code_for_pp,
				)

				fallback_pc = _gsm_order_code_for_pp(pp_id)
			except Exception:
				fallback_pc = ""
		if fallback_pc:
			payload = {**payload, "party_code": fallback_pc}
	payload = dict(payload or {})
	specs = _gsm_resolve_item_row_display_specs({**payload, **{k: getattr(row, k, None) for k in ("item_code", "item_name", "quality", "color", "gsm")}})
	if not _cstr(payload.get("quality") or "").strip():
		payload["quality"] = specs.get("quality") or ""
	if not _cstr(payload.get("color") or "").strip():
		payload["color"] = specs.get("color") or ""
	if cint(payload.get("gsm") or 0) <= 0 and cint(specs.get("gsm") or 0) > 0:
		payload["gsm"] = specs.get("gsm")
	field_map = {
		"work_order": "work_order",
		"item_code": "item_code",
		"item_name": "item_name",
		"quality": "quality",
		"color": "color",
		"gsm": "gsm",
		"batch_no": "batch_no",
		"roll_no": "roll_no",
		"width_inch": "width_inch",
		"meter_roll": "meter_roll",
		"produced_length_mtrs": "produced_length_mtrs",
		"produced_gsm": "produced_gsm",
		"net_weight": "net_weight",
		"gross_weight": "gross_weight",
		"planned_qty": "planned_qty",
		"party_code": "party_code",
		"uom": "uom",
	}
	for src, dst in field_map.items():
		if not spi_meta.has_field(dst):
			continue
		val = payload.get(src)
		if val is None or val == "":
			continue
		if dst in ("net_weight", "gross_weight", "planned_qty", "produced_gsm", "width_inch", "meter_roll"):
			row.set(dst, flt(val))
		elif dst == "roll_no":
			row.set(dst, cint(val) if val not in (None, "") else 0)
		elif dst == "gsm":
			row.set(dst, cint(val))
		elif dst == "produced_length_mtrs":
			row.set(dst, cint(round(flt(val))))
		else:
			row.set(dst, _cstr(val))

	if spi_meta.has_field("job") and job_id:
		row.job = job_id

	# Safety: if GSM payload includes a Work Order, force item_code/item_name to match
	# that Work Order's `production_item`. This prevents item/WO mismatch on submit
	# when the client had stale planning-line item selection.
	wo_name = _cstr(payload.get("work_order") or "").strip()
	if wo_name and frappe.db.exists("Work Order", wo_name):
		prod_item = _cstr(frappe.db.get_value("Work Order", wo_name, "production_item") or "").strip()
		if prod_item:
			if spi_meta.has_field("item_code"):
				row.item_code = prod_item
			if spi_meta.has_field("item_name"):
				row.item_name = _cstr(frappe.db.get_value("Item", prod_item, "item_name") or "")

	optional_map = {
		"custom_shift": "custom_shift",
		"custom_no_of_shaft": "custom_no_of_shaft",
		"no_of_shaft": "custom_no_of_shaft",
	}
	for src, dst in optional_map.items():
		if not spi_meta.has_field(dst):
			continue
		val = payload.get(src)
		if val is None or val == "":
			if dst == "custom_shift" and shift:
				row.set(dst, _cstr(shift))
			continue
		if dst == "custom_no_of_shaft":
			row.set(dst, cint(val) if val not in (None, "") else 0)
		else:
			row.set(dst, _cstr(val))
	_gsm_spi_set_float_from_payload(row, spi_meta, payload, "custom_polybag_kgs")
	_gsm_spi_set_float_from_payload(row, spi_meta, payload, "custom_diameter_inches")
	_gsm_spi_set_float_from_payload(row, spi_meta, payload, "custom_cbm_cubic_meters")
	# Additive: Bay (Warehouse Bay link) — only when field exists
	if spi_meta.has_field("custom_bay"):
		bay_val = payload.get("custom_bay")
		if bay_val is not None:
			row.set("custom_bay", _cstr(bay_val))
	if shift and spi_meta.has_field("custom_shift") and not _cstr(getattr(row, "custom_shift", "")):
		row.custom_shift = _cstr(shift)

	if spi_meta.has_field("custom_core_width_mm"):
		core_link = _gsm_resolve_core_width_for_item_row(payload)
		if core_link:
			row.custom_core_width_mm = core_link

	if spi_meta.has_field("row_locked"):
		row.row_locked = cint(payload.get("row_locked") or 0)
	if spi_meta.has_field("row_ready_for_print"):
		row.row_ready_for_print = cint(payload.get("row_ready_for_print") or payload.get("row_locked") or 0)

	pl_m = payload.get("produced_length_mtrs")
	if pl_m not in (None, "") and spi_meta.has_field("custom_produced_length_mtrs"):
		row.custom_produced_length_mtrs = cint(round(flt(pl_m)))

	is_bundle = cint(payload.get("is_bundle_row") or 0)
	preserve_net = is_bundle and flt(getattr(row, "net_weight", 0) or payload.get("net_weight")) > 0
	if not preserve_net and spi_meta.has_field("net_weight"):
		gw = flt(payload.get("gross_weight") or getattr(row, "gross_weight", 0))
		wi = flt(payload.get("width_inch") or getattr(row, "width_inch", 0))
		core_link = _cstr(getattr(row, "custom_core_width_mm", "") or payload.get("custom_core_width_mm"))
		poly = flt(payload.get("custom_polybag_kgs") or getattr(row, "custom_polybag_kgs", 0))
		if gw > 0 and wi > 0:
			from production_entry.production_planning.unified_production_entry_api import (
				_gsm_calc_roll_net_weight_kg,
			)

			row.net_weight = _gsm_calc_roll_net_weight_kg(gw, wi, core_link, poly)

	if spi_meta.has_field("custom_no_of_shaft"):
		current_shaft = cint(getattr(row, "custom_no_of_shaft", 0) or 0)
		if current_shaft <= 0 and job_id:
			resolved = _gsm_resolve_shaft_no_for_roll(spr, job_id, item_row=row, payload=payload)
			if resolved > 0:
				row.custom_no_of_shaft = resolved


def _gsm_validate_roll_for_spr(spr, spr_pp_id: str, payload: dict) -> None:
	"""Ensure GSM roll payload maps to the correct SPR production plan and work order."""
	if spr_doc_is_mix_roll(spr):
		return
	spr_pp = _cstr(spr_pp_id or spr.get("production_plan")).strip()
	payload_pp = _cstr(payload.get("pp_id")).strip()
	if payload_pp and spr_pp and payload_pp != spr_pp:
		frappe.throw(
			_("Roll production plan {0} does not match SPR {1} (production plan {2})").format(
				payload_pp, spr.name, spr_pp
			)
		)
	wo_name = _cstr(payload.get("work_order")).strip()
	if not wo_name or not frappe.db.exists("Work Order", wo_name):
		return
	wo_pp = _cstr(frappe.db.get_value("Work Order", wo_name, "production_plan") or "").strip()
	if not wo_pp and frappe.db.has_column("Work Order", "custom_production_plan"):
		wo_pp = _cstr(frappe.db.get_value("Work Order", wo_name, "custom_production_plan") or "").strip()
	if wo_pp and spr_pp and wo_pp != spr_pp:
		frappe.throw(
			_("Work Order {0} belongs to production plan {1}, not SPR plan {2}").format(
				wo_name, wo_pp, spr_pp
			)
		)


def _gsm_upsert_roll_line_on_spr(spr, pp_id: str, payload: dict, shift=None) -> dict:
	"""Insert or update one GSM roll line on a draft SPR (batch_no dedup)."""
	if _gsm_payload_is_bundle_summary(payload):
		return {
			"action": "skipped",
			"batch_no": _cstr(payload.get("batch_no")),
			"reason": "bundle_summary",
		}
	batch_no = _cstr(payload.get("batch_no")).strip()
	if not batch_no:
		frappe.throw(_("Batch number is required for GSM roll import"))
	if cint(payload.get("is_wasted")):
		return {
			"action": "skipped",
			"batch_no": batch_no,
			"reason": "already_wasted",
		}
	for waste in spr.get("custom_roll_waste") or []:
		wb = _cstr(getattr(waste, "batch_no", None) or getattr(waste, "source_roll", None) or "").strip()
		if wb and wb == batch_no:
			return {
				"action": "skipped",
				"batch_no": batch_no,
				"reason": "already_wasted",
			}

	_gsm_validate_roll_for_spr(spr, pp_id, payload)

	job_id = _gsm_resolve_job_id_for_roll(spr, pp_id, payload)
	existing = _gsm_find_item_row_by_batch(spr, batch_no)
	if not existing:
		existing = _gsm_find_reusable_item_row(spr, job_id, payload.get("width_inch"), batch_no)
	action = "updated" if existing else "added"
	if not existing:
		_gsm_enforce_job_roll_quota_on_add(
			spr,
			pp_id,
			job_id,
			payload.get("width_inch"),
			batch_no,
		)
	if existing:
		row = existing
	else:
		row = spr.append("items", {})
		row.batch_no = batch_no

	_gsm_apply_payload_to_item_row(row, payload, job_id, shift=shift, spr=spr)
	pp_id_out = _cstr(spr.get("production_plan")).strip()
	roll_line = _gsm_serialize_item_row_for_grid(row, pp_id_out)
	return {
		"action": action,
		"batch_no": batch_no,
		"job": job_id,
		"row_name": _cstr(getattr(row, "name", "")),
		"roll_line": roll_line,
	}


def _gsm_spr_skips_tolerance(doc) -> bool:
	return bool(
		cint(getattr(doc, "custom_is_box_bag", 0))
		or cint(getattr(doc, "custom_is_sheet_cutting", 0))
	)


def _gsm_format_tolerance_violations(doc) -> list[dict]:
	out = []
	for jb, rn, pq, act, dp in _spr_collect_roll_planned_tolerance_violations(doc):
		out.append(
			{
				"job": jb or "—",
				"roll_no": rn if rn is not None and rn != "" else "—",
				"planned": flt(pq, 3),
				"actual": flt(act, 3),
				"dev_pct": flt(dp, 2),
			}
		)
	return out


@frappe.whitelist()
def spr_get_tolerance_violations(spr_name):
	"""GSM / desk helper — expose existing SPR tolerance check without changing submit flow."""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if _gsm_spr_skips_tolerance(spr):
		return {
			"spr_name": spr_name,
			"order_code": _cstr(spr.get("custom_order_code") or ""),
			"skipped": True,
			"tolerance_percent": _spr_net_weight_tolerance_percent(),
			"violations": [],
		}
	return {
		"spr_name": spr_name,
		"order_code": _cstr(spr.get("custom_order_code") or ""),
		"skipped": False,
		"tolerance_percent": _spr_net_weight_tolerance_percent(),
		"violations": _gsm_format_tolerance_violations(spr),
		"override_approved": cint(spr.get("tolerance_override_approved") or 0),
		"override_reason": _cstr(spr.get("tolerance_override_reason") or ""),
	}


def _gsm_parse_bundle_sticker_roll_numbers(raw_value) -> list[str]:
	out = []
	seen = set()
	for part in re.split(r"[,;\s]+", _cstr(raw_value)):
		val = part.strip()
		if not val:
			continue
		if val.isdigit():
			val = str(cint(val))
		if val not in seen:
			seen.add(val)
			out.append(val)
	return out


def _gsm_bundle_child_items_for_sticker(spr, sticker) -> list:
	"""Match SPR item rows packed into one bundle sticker."""
	job_id = _cstr(getattr(sticker, "job_id", ""))
	roll_nums = _gsm_parse_bundle_sticker_roll_numbers(getattr(sticker, "roll_numbers", ""))
	children = []
	for it in spr.items or []:
		if not _spr_is_real_roll_item_row(it):
			continue
		if job_id and _cstr(getattr(it, "job", "")) != job_id:
			continue
		bn = _cstr(getattr(it, "batch_no", ""))
		rn = _cstr(getattr(it, "roll_no", ""))
		suffix = bn.rsplit("/", 1)[-1].strip() if "/" in bn else rn
		if roll_nums and (bn in roll_nums or rn in roll_nums or suffix in roll_nums):
			children.append(it)
	pack = cint(getattr(sticker, "rolls_per_bundle", 0))
	if not children and pack > 0 and job_id:
		comb = _cstr(getattr(sticker, "combination", ""))
		m = re.match(r"(\d+)\s*\*\s*([\d.]+)", comb)
		seg_w = flt(m.group(2)) if m else 0.0
		candidates = [
			it
			for it in (spr.items or [])
			if _spr_is_real_roll_item_row(it)
			and _cstr(getattr(it, "job", "")) == job_id
			and (seg_w <= 0 or abs(flt(getattr(it, "width_inch", 0)) - seg_w) < 0.05)
		]
		children = candidates[:pack]
	return children


def _gsm_segment_width_from_bundle_sticker(sticker, pack_count: int) -> float:
	comb = _cstr(getattr(sticker, "combination", ""))
	m = re.match(r"(\d+)\s*\*\s*([\d.]+)", comb)
	if m:
		return flt(m.group(2))
	sw = flt(getattr(sticker, "sticker_width", 0))
	pc = max(1, cint(pack_count))
	if sw > 0:
		return round(sw / pc, 4)
	return 0.0


def _gsm_resolve_item_row_display_specs(row_like) -> dict:
	"""Fill quality, color, gsm from item row fields or item code parsing."""
	quality = _cstr(getattr(row_like, "quality", None) or (row_like.get("quality") if isinstance(row_like, dict) else "") or "")
	color = _cstr(
		getattr(row_like, "color", None)
		or getattr(row_like, "fabric_colour", None)
		or (row_like.get("color") if isinstance(row_like, dict) else "")
		or (row_like.get("fabric_colour") if isinstance(row_like, dict) else "")
		or ""
	)
	gsm = cint(getattr(row_like, "gsm", 0) or (row_like.get("gsm") if isinstance(row_like, dict) else 0) or 0)
	item_code = _cstr(getattr(row_like, "item_code", None) or (row_like.get("item_code") if isinstance(row_like, dict) else "") or "")
	item_name = _cstr(getattr(row_like, "item_name", None) or (row_like.get("item_name") if isinstance(row_like, dict) else "") or "")
	if item_code and not item_name:
		item_name = _cstr(frappe.db.get_value("Item", item_code, "item_name") or "")
	if item_code and (not quality or not color or gsm <= 0):
		specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
		if not quality:
			quality = _cstr(specs.get("quality") or "")
		if not color:
			color = _cstr(specs.get("color") or "")
		if gsm <= 0:
			gsm = cint(specs.get("gsm") or 0)
	return {"quality": quality, "color": color, "gsm": gsm, "item_code": item_code, "item_name": item_name}


def _gsm_serialize_item_row_for_grid(it, pp_id: str) -> dict:
	specs = _gsm_resolve_item_row_display_specs(it)
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	core_field = _gsm_spi_resolve_field(spi_meta, "custom_core_width_mm") or "custom_core_width_mm"
	poly_field = _gsm_spi_resolve_field(spi_meta, "custom_polybag_kgs") or "custom_polybag_kgs"
	return {
		"pp_id": pp_id,
		"party_code": _cstr(getattr(it, "party_code", None) or getattr(it, "custom_order_code", None)),
		"item_code": specs["item_code"] or _cstr(getattr(it, "item_code", None)),
		"item_name": specs["item_name"] or _cstr(getattr(it, "item_name", None)),
		"quality": specs["quality"],
		"color": specs["color"],
		"gsm": specs["gsm"] or cint(getattr(it, "gsm", 0) or 0),
		"batch_no": _cstr(getattr(it, "batch_no", None)),
		"roll_no": cint(getattr(it, "roll_no", 0) or 0),
		"width_inch": flt(getattr(it, "width_inch", 0) or 0),
		"meter_roll": flt(getattr(it, "meter_roll", 0) or 0),
		"produced_length_mtrs": flt(getattr(it, "produced_length_mtrs", 0) or 0),
		"produced_gsm": flt(getattr(it, "produced_gsm", 0) or 0),
		"net_weight": flt(getattr(it, "net_weight", 0) or 0),
		"gross_weight": flt(getattr(it, "gross_weight", 0) or 0),
		"planned_qty": flt(getattr(it, "planned_qty", 0) or 0),
		"work_order": _cstr(getattr(it, "work_order", None)),
		"uom": _cstr(getattr(it, "uom", None) or "Kg"),
		"custom_core_width_mm": _cstr(getattr(it, core_field, None)),
		"custom_polybag_kgs": flt(getattr(it, poly_field, 0) or 0),
		"custom_diameter_inches": _gsm_spi_get_float(it, spi_meta, "custom_diameter_inches"),
		"custom_cbm_cubic_meters": _gsm_spi_get_float(it, spi_meta, "custom_cbm_cubic_meters"),
		"custom_diameter": _gsm_spi_get_float(it, spi_meta, "custom_diameter_inches"),
		"custom_cbm": _gsm_spi_get_float(it, spi_meta, "custom_cbm_cubic_meters"),
		"custom_bay": _cstr(getattr(it, "custom_bay", None)) if spi_meta.has_field("custom_bay") else "",
		"job_id": _cstr(getattr(it, "job", None)),
		"custom_no_of_shaft": cint(getattr(it, "custom_no_of_shaft", 0) or 0),
		"spr_item_name": _cstr(getattr(it, "name", None)),
		"row_modified": _cstr(getattr(it, "modified", None) or getattr(it, "creation", None) or ""),
		"child_idx": cint(getattr(it, "idx", 0) or 0),
		"is_bundle_row": 0,
		"row_locked": 1 if _spr_roll_has_production(it) else 0,
		"row_ready_for_print": 1 if _spr_roll_has_production(it) else 0,
	}


def _gsm_serialize_bundle_sticker_for_grid(spr, sticker, pp_id: str) -> dict:
	children = _gsm_bundle_child_items_for_sticker(spr, sticker)
	pack_count = cint(getattr(sticker, "rolls_per_bundle", 0)) or len(children)
	comb = _cstr(getattr(sticker, "combination", ""))
	seg_w = _gsm_segment_width_from_bundle_sticker(sticker, pack_count)
	if not seg_w and children:
		seg_w = flt(getattr(children[0], "width_inch", 0))
	first = children[0] if children else None
	first_specs = _gsm_resolve_item_row_display_specs(first) if first else {"quality": "", "color": "", "gsm": 0}
	# Multi-width stickers use "2 * 30 + 2 * 32 Inches" — prefer that as label.
	is_multi_comb = "+" in comb
	if is_multi_comb and comb:
		width_label = f"{comb} ({pack_count} rolls)" if pack_count else comb
	elif seg_w and pack_count:
		width_label = f'{_bp_format_width_label_static(seg_w)}" ({pack_count} rolls)'
	else:
		width_label = comb or ""
	# Prefer sticker sum of child nets (set at apply); do not recompute from one width.
	bundle_net = flt(getattr(sticker, "sticker_bundle_weight", 0) or 0)
	if bundle_net <= 0 and children:
		bundle_net = round(sum(flt(getattr(c, "net_weight", 0) or 0) for c in children), 2)
	return {
		"pp_id": pp_id,
		"party_code": _cstr(
			getattr(first, "party_code", None) if first else spr.get("custom_order_code") or ""
		),
		"item_code": _cstr(getattr(first, "item_code", None) if first else ""),
		"item_name": _cstr(getattr(first, "item_name", None) if first else ""),
		"quality": first_specs["quality"] or _cstr(getattr(first, "quality", None) if first else ""),
		"color": first_specs["color"] or _cstr(getattr(first, "color", None) if first else ""),
		"gsm": first_specs["gsm"] or cint(getattr(first, "gsm", 0) if first else 0),
		"width_inch": 0 if is_multi_comb else seg_w,
		"width_label": width_label,
		"pack_count": pack_count,
		"segment_width": 0 if is_multi_comb else seg_w,
		"batch_no": _cstr(getattr(sticker, "batch_no", "")),
		"roll_no": "",
		"roll_numbers": _cstr(getattr(sticker, "roll_numbers", "")),
		"combination": comb,
		"meter_roll": flt(getattr(first, "meter_roll", 0) if first else 0),
		"produced_length_mtrs": flt(getattr(sticker, "produced_length_mtrs", 0) or 0),
		"produced_gsm": flt(getattr(first, "produced_gsm", 0) if first else 0),
		"net_weight": bundle_net,
		"gross_weight": flt(getattr(sticker, "sticker_bundle_gross_weight_kg", 0) or 0),
		"planned_qty": flt(getattr(first, "planned_qty", 0) if first else 0),
		"work_order": _cstr(getattr(first, "work_order", None) if first else ""),
		"uom": _cstr(getattr(first, "uom", None) if first else "Kg"),
		"custom_core_width_mm": _cstr(getattr(first, "custom_core_width_mm", None) if first else ""),
		"job_id": _cstr(getattr(sticker, "job_id", "")),
		"child_roll_batches": [
			_cstr(getattr(c, "batch_no", "")) for c in children if _cstr(getattr(c, "batch_no", ""))
		],
		"child_spr_item_names": [_cstr(getattr(c, "name", "")) for c in children if _cstr(getattr(c, "name", ""))],
		"is_bundle_row": 1,
		"row_locked": 1,
		"row_ready_for_print": 1,
	}


def _gsm_serialize_spr_roll_lines_for_grid(spr) -> list[dict]:
	"""Map draft SPR items + bundle stickers to GSM Production Entry grid rows."""
	pp_id = _cstr(spr.get("production_plan")).strip()
	bundled_batches = set()
	lines = []
	for sticker in spr.bundle_stickers or []:
		bundle_row = _gsm_serialize_bundle_sticker_for_grid(spr, sticker, pp_id)
		for bn in bundle_row.get("child_roll_batches") or []:
			bundled_batches.add(bn)
		if bundle_row.get("batch_no") or bundle_row.get("child_roll_batches"):
			lines.append(bundle_row)
	for it in spr.items or []:
		if not _spr_is_real_roll_item_row(it):
			continue
		bn = _cstr(getattr(it, "batch_no", ""))
		if bn and bn in bundled_batches:
			continue
		lines.append(_gsm_serialize_item_row_for_grid(it, pp_id))
	return lines


def _gsm_roll_suffix_from_batch_no(batch_no: str) -> int:
	bn = _cstr(batch_no).strip()
	if not bn or "/" not in bn:
		return 0
	try:
		return cint(bn.rsplit("/", 1)[-1].strip())
	except Exception:
		return 0


def _gsm_serialize_roll_waste_for_grid(spr, waste_row, pp_id: str) -> dict:
	"""Map SPR Roll Waste child row to GSM grid row (read-only strike-through)."""
	specs = _gsm_resolve_item_row_display_specs(waste_row)
	item_code = specs["item_code"]
	item_name = specs["item_name"]
	quality = specs["quality"]
	color = specs["color"]
	gsm = specs["gsm"] or cint(getattr(waste_row, "gsm", 0) or 0)
	wastage = flt(getattr(waste_row, "wastage", 0) or 0)
	batch_no = _cstr(getattr(waste_row, "batch_no", None) or "")
	roll_no = cint(getattr(waste_row, "roll_number", 0) or 0)
	if roll_no <= 0:
		roll_no = _gsm_roll_suffix_from_batch_no(batch_no)
	return {
		"pp_id": pp_id,
		"party_code": _cstr(spr.get("custom_order_code") or ""),
		"item_code": item_code,
		"item_name": item_name,
		"quality": quality,
		"color": color,
		"gsm": gsm,
		"batch_no": batch_no,
		"roll_no": roll_no,
		"width_inch": flt(getattr(waste_row, "width_inch", 0) or 0),
		"meter_roll": flt(getattr(waste_row, "meter_per_roll", 0) or 0),
		"produced_length_mtrs": flt(getattr(waste_row, "meter_per_roll", 0) or 0),
		"produced_gsm": gsm,
		"net_weight": wastage,
		"gross_weight": wastage,
		"planned_qty": 0,
		"work_order": "",
		"uom": "Kg",
		"job_id": _cstr(getattr(waste_row, "job_id", None) or ""),
		"spr_item_name": _cstr(getattr(waste_row, "spr_item_name", None) or ""),
		"roll_waste_row_name": _cstr(getattr(waste_row, "name", None) or ""),
		"spr_name": spr.name,
		"is_bundle_row": 0,
		"is_wasted": 1,
		"row_locked": 1,
		"row_readonly": 1,
		"row_ready_for_print": 0,
	}


def _gsm_patty_stock_from_batch_doc(batch_doc: dict, item_code: str, available_kg: float) -> dict:
	batch_doc = batch_doc or {}
	item_code = _cstr(item_code or batch_doc.get("item") or batch_doc.get("item_code") or "").strip()
	item_name = ""
	if item_code:
		item_name = _cstr(frappe.db.get_value("Item", item_code, "item_name") or "")
	width = flt(
		batch_doc.get("custom_width_inch")
		or batch_doc.get("width_inch")
		or batch_doc.get("custom_width")
		or batch_doc.get("width")
		or 0
	)
	if width <= 0 and item_name:
		import re

		m = re.search(r"(\d{2,3})\s*mm", item_name, re.I)
		if m:
			width = round(flt(m.group(1)) / 25.4, 2)
	quality = _cstr(batch_doc.get("custom_quality") or batch_doc.get("quality") or "")
	color = _cstr(batch_doc.get("custom_color") or batch_doc.get("color") or "")
	gsm = cint(batch_doc.get("custom_gsm") or batch_doc.get("gsm") or 0)
	if item_code and (not quality or not color or gsm <= 0 or width <= 0):
		specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
		if not quality:
			quality = _cstr(specs.get("quality") or "")
		if not color:
			color = _cstr(specs.get("color") or "")
		if gsm <= 0:
			gsm = cint(specs.get("gsm") or 0)
		if width <= 0:
			width = flt(specs.get("width_inch") or 0)
	if width <= 0 and item_code:
		_pg, parsed_w = parse_item_code(item_code)
		if parsed_w > 0:
			width = flt(parsed_w)
	return {
		"name": _cstr(batch_doc.get("name") or ""),
		"batch_no": _cstr(batch_doc.get("name") or ""),
		"item_code": item_code,
		"item_name": item_name,
		"quality": quality,
		"color": color,
		"gsm": gsm,
		"width_inch": width,
		"available_kg": flt(available_kg or 0),
	}


def _gsm_batch_available_kg(batch_no: str, item_code: str = "") -> float:
	batch_no = _cstr(batch_no).strip()
	if not batch_no:
		return 0.0
	batch_meta = frappe.get_meta("Batch")
	if batch_meta.has_field("batch_qty"):
		qty = flt(frappe.db.get_value("Batch", batch_no, "batch_qty") or 0)
		if qty > 0:
			return qty
	filters = {"batch_no": batch_no, "is_cancelled": 0}
	if item_code:
		filters["item_code"] = item_code
	rows = frappe.db.sql(
		"""
		SELECT SUM(actual_qty) AS qty
		FROM `tabStock Ledger Entry`
		WHERE IFNULL(is_cancelled, 0) = 0
		  AND batch_no = %(batch_no)s
		  {item_clause}
		""".format(item_clause="AND item_code = %(item_code)s" if item_code else ""),
		{"batch_no": batch_no, "item_code": item_code},
		as_dict=True,
	)
	return flt((rows[0] or {}).get("qty") or 0) if rows else 0.0


def _patty_stock_doctype_row(row) -> dict:
	"""Map Patty Stock DocType fields to the View Patty Stock dialog payload."""
	row = row or {}
	avail = flt(row.get("balance_quantity") or row.get("available_kg") or 0)
	color = _cstr(row.get("colour") or row.get("color") or "")
	return {
		"name": _cstr(row.get("name") or ""),
		"patty_stock": _cstr(row.get("name") or ""),
		"batch_no": _cstr(row.get("batch_no") or ""),
		"item_code": _cstr(row.get("item_code") or ""),
		"item_name": _cstr(row.get("item_name") or ""),
		"quality": _cstr(row.get("quality") or ""),
		"color": color,
		"colour": color,
		"gsm": cint(row.get("gsm") or 0),
		"width_inch": flt(row.get("width_inch") or 0),
		"available_kg": avail,
		"meter_per_roll": flt(row.get("meter_roll_mtrs") or 0),
		"no_of_shafts": _cstr(row.get("no_of_shafts") or ""),
	}


@frappe.whitelist()
def get_available_patty_stock(spr_name=None):
	"""Available rows from the Patty Stock DocType (not Batch)."""
	_ = _cstr(spr_name).strip()
	if not frappe.db.exists("DocType", "Patty Stock"):
		frappe.throw(_("Patty Stock DocType is not installed."))

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

	filters = []
	if meta.has_field("balance_quantity"):
		filters.append(["balance_quantity", ">", 0])

	rows = (
		frappe.get_all(
			"Patty Stock",
			fields=fields,
			filters=filters,
			limit_page_length=2000,
			order_by="modified desc",
		)
		or []
	)
	out = []
	for row in rows:
		mapped = _patty_stock_doctype_row(row)
		if flt(mapped.get("available_kg") or 0) <= 0:
			continue
		out.append(mapped)
	return out


@frappe.whitelist()
def spr_get_available_patty_stock(spr_name=None):
	return get_available_patty_stock(spr_name)


@frappe.whitelist()
def get_patty_stock_for_spr(spr_name=None):
	return get_available_patty_stock(spr_name)


def _gsm_publish_session_update(spr) -> None:
	"""Notify GSM clients on same site to refresh session grid."""
	try:
		frappe.publish_realtime(
			"gsm_production_entry_updated",
			{
				"run_date": str(spr.run_date or ""),
				"shift": _cstr(spr.shift),
				"unit": _cstr(spr.custom_unit),
				"spr_name": spr.name,
				"roll_count": len(spr.items or []),
				"modified": str(spr.modified or ""),
			},
		)
	except Exception:
		pass


def _spr_patty_wastage_fieldname() -> str | None:
	meta = frappe.get_meta("Shaft Production Run")
	for cand in ("custom_running_patty_wastage", "running_patty_wastage"):
		if meta.has_field(cand):
			return cand
	for df in meta.fields or []:
		if df.fieldtype != "Table":
			continue
		label = _cstr(df.label or "").lower()
		fname = _cstr(df.fieldname or "").lower()
		options = _cstr(df.options or "")
		if "patty" in fname or "patty" in label or "Patty" in options:
			return df.fieldname
	return None


def _spr_meta_find_field(meta, include_any, exclude_any=None):
	"""Find first child field whose fieldname/label contains any include token."""
	exclude_any = [e.lower() for e in (exclude_any or [])]
	includes = [i.lower() for i in (include_any or []) if i]
	for df in meta.fields or []:
		blob = f"{df.fieldname} {df.label or ''}".lower()
		if any(ex in blob for ex in exclude_any):
			continue
		if any(inc in blob for inc in includes):
			return df.fieldname
	return None


def _spr_patty_child_doctype() -> str:
	"""Live child DocType for Running Patty (Customize Form: Shaft Production Run Wastage)."""
	field = _spr_patty_wastage_fieldname()
	if field:
		df = frappe.get_meta("Shaft Production Run").get_field(field)
		opts = _cstr(getattr(df, "options", None) or "")
		if opts and frappe.db.exists("DocType", opts):
			return opts
	for cand in ("Shaft Production Run Wastage", "Running Patty Wastage Row"):
		if frappe.db.exists("DocType", cand):
			return cand
	return "Shaft Production Run Wastage"


def _spr_recycled_child_doctype() -> str:
	"""Live child DocType for Recycled Wastage Details."""
	field = _spr_recycled_wastage_fieldname()
	if field:
		df = frappe.get_meta("Shaft Production Run").get_field(field)
		opts = _cstr(getattr(df, "options", None) or "")
		if opts and frappe.db.exists("DocType", opts):
			return opts
	for cand in ("Recycled Wastage Details", "Recycled Wastage Detail Row"):
		if frappe.db.exists("DocType", cand):
			return cand
	return "Recycled Wastage Details"


def _spr_patty_live_field_map() -> dict:
	"""Discover live Running Patty fieldnames (site: Shaft Production Run Wastage)."""
	child_dt = _spr_patty_child_doctype()
	if not frappe.db.exists("DocType", child_dt):
		return {}
	meta = frappe.get_meta(child_dt)
	existing = {df.fieldname for df in meta.fields}
	mapping = {
		"job_id": _spr_meta_find_field(meta, ["job_id", "job"], ["recycle"]) or ("job_id" if "job_id" in existing else None),
		"quality": _spr_meta_find_field(meta, ["quality"]),
		"color": _spr_meta_find_field(meta, ["color", "colour"]),
		"gsm": _spr_meta_find_field(meta, ["gsm"]),
		"width": _spr_meta_find_field(meta, ["width_inches", "width_inch", "width"], ["core", "machine"]),
		"meter": _spr_meta_find_field(
			meta, ["meter__roll_mtrs", "meter_roll_mtrs", "meter__roll", "meter_per_roll", "meter"], ["wastage", "shaft", "recycle"]
		)
		or _spr_meta_find_field(meta, ["roll"], ["shaft", "wastage", "no_of", "recycle"]),
		"shafts": _spr_meta_find_field(meta, ["shaft"], ["one_shaft", "wastage"]),
		"wastage_qty": _spr_meta_find_field(meta, ["wastage_qty_kgs", "wastage_qty", "wastage_qt"])
		or _spr_meta_find_field(meta, ["wastage"], ["net", "recycle", "one_shaft", "process"]),
		"recycled_qty": _spr_meta_find_field(meta, ["recycled_qty", "recycled"], ["recycle_to", "next", "detail", "kgs"]),
		"net_wastage": _spr_meta_find_field(meta, ["net_wastage", "net wastage"]),
		"one_shaft": _spr_meta_find_field(meta, ["one_shaft", "one shaft"]),
		"recycle_to_next": _spr_meta_find_field(meta, ["recycle_to_next", "recycle to next"]),
		"batch_no": _spr_meta_find_field(meta, ["batch"]),
	}
	return {k: v for k, v in mapping.items() if v}


def _spr_write_patty_child_row(logical: dict) -> dict:
	"""Map logical patty keys onto live child fieldnames (dynamic discovery + aliases).

	IMPORTANT: do not cross-map net_wastage ↔ wastage_qty. Desk sets net_wastage=0 when
	Recycle to Next is checked; writing that 0 onto wastage_qty zeroes every qty column.
	Live site fields: width_inches, meter_roll_mtrs, wastage_qty_kgs.
	"""
	child_dt = _spr_patty_child_doctype()
	if not frappe.db.exists("DocType", child_dt):
		return {k: v for k, v in (logical or {}).items() if not _cstr(k).startswith("_")}

	live = _spr_patty_live_field_map()
	meta = frappe.get_meta(child_dt)
	existing = {df.fieldname for df in meta.fields}

	# logical key -> preferred live field (discovered) then static aliases
	static_aliases = {
		"job_id": ("job_id", "job"),
		"quality": ("quality", "custom_quality"),
		"color": ("color", "fabric_colour", "custom_color"),
		"gsm": ("gsm",),
		"width_inch": ("width_inches", "width_inch", "width", "w"),
		"width": ("width_inches", "width", "width_inch", "w"),
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
		"wastage": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
		"wastage_qty": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
		"one_shaft_gross": ("one_shaft_gross", "one_shaft_wastage", "one_shaft", "one_shaft_weight"),
		"net_wastage": ("net_wastage", "net_wastage_kg", "net_wastage_kgs"),
		"recycled": ("recycled_qty", "recycled", "recycled_kg"),
		"recycled_qty": ("recycled_qty", "recycled", "recycled_kg"),
		"recycle_to_next": ("recycle_to_next", "custom_recycle_to_next"),
		"batch_no": ("batch_no", "source_roll"),
		"item_code": ("item_code", "item"),
		"item_name": ("item_name",),
		"item": ("item", "item_code"),
		"party_code": ("party_code", "order_code"),
		"order_code": ("order_code", "party_code"),
	}
	live_for_logical = {
		"job_id": live.get("job_id"),
		"quality": live.get("quality"),
		"color": live.get("color"),
		"gsm": live.get("gsm"),
		"width_inch": live.get("width"),
		"width": live.get("width"),
		"meter_per_roll": live.get("meter"),
		"no_of_shafts": live.get("shafts"),
		"wastage": live.get("wastage_qty"),
		"wastage_qty": live.get("wastage_qty"),
		"one_shaft_gross": live.get("one_shaft"),
		"net_wastage": live.get("net_wastage"),
		"recycled": live.get("recycled_qty"),
		"recycled_qty": live.get("recycled_qty"),
		"recycle_to_next": live.get("recycle_to_next"),
		"batch_no": live.get("batch_no"),
	}

	ordered_keys = []
	seen = set()
	for key in (
		"job_id",
		"quality",
		"color",
		"gsm",
		"width_inch",
		"width",
		"meter_per_roll",
		"no_of_shafts",
		"one_shaft_gross",
		"wastage_qty",
		"wastage",
		"recycled_qty",
		"recycled",
		"net_wastage",
		"recycle_to_next",
		"batch_no",
		"item_code",
		"item_name",
		"item",
		"party_code",
		"order_code",
	):
		if key in (logical or {}) and key not in seen:
			ordered_keys.append(key)
			seen.add(key)
	for key in logical or {}:
		if key.startswith("_") or key in seen:
			continue
		ordered_keys.append(key)
		seen.add(key)

	out: dict = {}
	wastage_fn = live.get("wastage_qty")
	net_fn = live.get("net_wastage")
	# Fields that must never receive net_wastage (especially 0 on Recycle to Next)
	wastage_blocklist = {
		fn
		for fn in (
			wastage_fn,
			"wastage",
			"wastage_qty",
			"wastage_qt",
			"wastage_qty_kgs",
		)
		if fn
	}
	# Synonym groups: write every live field that exists so grid columns never stay 0
	# while a sibling alias was updated.
	synonym_groups = {
		"width_inch": ("width_inches", "width_inch", "width", "w", "custom_width_inch", "custom_width", "patty_width"),
		"width": ("width_inches", "width", "width_inch", "w", "custom_width_inch", "custom_width", "patty_width"),
		"meter_per_roll": ("meter_roll_mtrs", "meter_per_roll", "meter__roll", "meter_roll", "meter"),
		"wastage": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
		"wastage_qty": ("wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"),
		"recycled": ("recycled_qty", "recycled", "recycled_kg"),
		"recycled_qty": ("recycled_qty", "recycled", "recycled_kg"),
		"one_shaft_gross": ("one_shaft_gross", "one_shaft_wastage", "one_shaft", "one_shaft_weight"),
		"no_of_shafts": ("no_of_shafts", "shafts", "no_of_shaft"),
		"net_wastage": ("net_wastage", "net_wastage_kg", "net_wastage_kgs"),
	}
	net_collides = bool(net_fn and wastage_fn and net_fn == wastage_fn)
	for key in ordered_keys:
		val = (logical or {}).get(key)
		if val is None:
			continue
		if isinstance(val, str) and not val.strip():
			continue
		if key in ("net_wastage",) and net_collides:
			continue

		targets: list[str] = []
		live_fn = live_for_logical.get(key)
		if live_fn and live_fn in existing:
			targets.append(live_fn)
		for fn in synonym_groups.get(key, ()) + static_aliases.get(key, (key,)):
			if not fn or fn not in existing:
				continue
			if fn not in targets:
				targets.append(fn)
		if key in existing and key not in targets:
			targets.append(key)

		for fn in targets:
			if key in ("net_wastage",) and fn in wastage_blocklist:
				continue
			if key in ("wastage", "wastage_qty") and net_fn and fn == net_fn and fn not in wastage_blocklist:
				# Don't write wastage qty onto the net field when they differ
				if fn == net_fn and wastage_fn and fn != wastage_fn:
					continue
			out[fn] = val

	# Always force canonical JSON fields so Width / Meter / Wastage never stay 0
	# when live discovery mapped to a different (empty) column name.
	canonical_forced = {
		"width_inch": flt((logical or {}).get("width_inch") or (logical or {}).get("width") or 0),
		"meter_per_roll": flt((logical or {}).get("meter_per_roll") or 0),
		"wastage": flt((logical or {}).get("wastage") or (logical or {}).get("wastage_qty") or 0),
		"no_of_shafts": cint((logical or {}).get("no_of_shafts") or 0),
		"gsm": cint((logical or {}).get("gsm") or 0),
		"quality": _cstr((logical or {}).get("quality") or ""),
		"color": _cstr((logical or {}).get("color") or ""),
		"job_id": _cstr((logical or {}).get("job_id") or ""),
		"recycle_to_next": cint((logical or {}).get("recycle_to_next") or 0),
	}
	for fn, val in canonical_forced.items():
		if fn not in existing:
			continue
		if isinstance(val, str):
			if val.strip():
				out[fn] = val
		elif flt(val) != 0 or fn in ("recycle_to_next", "wastage", "meter_per_roll", "width_inch"):
			# Always write wastage/width/meter even when 0 so stale values clear;
			# prefer non-zero overwrites when available.
			if flt(val) > 0 or fn == "recycle_to_next" or out.get(fn) in (None, "", 0, 0.0):
				out[fn] = val
	# Mirror wastage onto wastage_qty when both exist
	if "wastage_qty" in existing and "wastage" in out and flt(out.get("wastage") or 0) > 0:
		out["wastage_qty"] = out["wastage"]
	if "meter__roll" in existing and flt(out.get("meter_per_roll") or 0) > 0:
		out["meter__roll"] = out["meter_per_roll"]
	if "width" in existing and flt(out.get("width_inch") or 0) > 0:
		out["width"] = out["width_inch"]
	return out


def _spr_patch_patty_row_preserve(target, values: dict, flag: int, flag_field: str, live: dict | None = None):
	"""Apply mapped values onto an existing patty row without blanking dimensions / wastage qty."""
	live = live or _spr_patty_live_field_map()
	net_fn = live.get("net_wastage")
	wastage_fn = live.get("wastage_qty")
	protect = {
		fn
		for fn in (
			live.get("width"),
			live.get("meter"),
			live.get("gsm"),
			live.get("shafts"),
			live.get("quality"),
			live.get("color"),
			live.get("one_shaft"),
			live.get("job_id"),
			wastage_fn,
			live.get("recycled_qty"),
		)
		if fn
	}
	# Net may be set to 0 intentionally when recycle is checked
	if net_fn:
		protect.discard(net_fn)

	for k, v in (values or {}).items():
		if k == flag_field:
			continue
		# Never write net_wastage=0 onto wastage_qty
		if wastage_fn and k == wastage_fn and net_fn != wastage_fn:
			if flt(v) == 0 and cint(flag) == 1:
				# Recycle path must not zero wastage qty
				cur = getattr(target, k, None)
				if flt(cur) > 0:
					continue
				# Prefer skipping zero write entirely for wastage on recycle
				continue
		if k in protect:
			if isinstance(v, str):
				if not v.strip():
					cur = _cstr(getattr(target, k, None))
					if cur:
						continue
			elif flt(v) == 0:
				cur = getattr(target, k, None)
				if flt(cur) > 0:
					continue
		try:
			target.set(k, v)
		except Exception:
			setattr(target, k, v)
	try:
		target.set(flag_field, flag)
	except Exception:
		setattr(target, flag_field, flag)


def _spr_patty_row_dict(row) -> dict:
	return row.as_dict() if hasattr(row, "as_dict") else dict(row or {})


def _spr_patty_row_wastage_kg(row_dict: dict) -> float:
	for key in (
		"wastage_qty_kgs",
		"wastage",
		"wastage_qty",
		"wastage_qt",
		"net_wastage",
		"net_wastage_kg",
		"net_wastage_kgs",
	):
		val = row_dict.get(key)
		if val is not None and flt(val) > 0:
			return flt(val)
	return 0.0


def _spr_existing_patty_rows(spr, field: str) -> list[dict]:
	rows = []
	for row in getattr(spr, field, None) or []:
		rows.append(_spr_patty_row_dict(row))
	if rows:
		return rows
	child_dt = _spr_patty_child_doctype()
	if not frappe.db.table_exists(child_dt):
		return []
	return frappe.get_all(
		child_dt,
		filters={"parent": spr.name, "parenttype": "Shaft Production Run"},
		fields=["*"],
		order_by="idx asc",
		limit=500,
	)


def _spr_patty_unit_text(spr) -> str:
	return _cstr(spr.get("custom_unit") or spr.get("unit") or "").upper()


def _spr_patty_is_valid_unit(spr) -> bool:
	unit = _spr_patty_unit_text(spr)
	return any(u in unit for u in ("UNIT 1", "UNIT 2", "UNIT 3", "UNIT 4"))


def _spr_patty_row_keys(row) -> list[str]:
	if row is None:
		return []
	if isinstance(row, dict):
		return list(row.keys())
	if hasattr(row, "as_dict"):
		try:
			return list((row.as_dict() or {}).keys())
		except Exception:
			pass
	return []


def _spr_patty_unit_base_width_inch(spr, gsm=0) -> float:
	"""Desk wastage_automation.js: Unit 1/2 = 10\", Unit 3 = 12\", Unit 4 = 15/14 by GSM."""
	unit = _spr_patty_unit_text(spr)
	if "UNIT 3" in unit:
		return 12.0
	if "UNIT 4" in unit:
		return 15.0 if flt(gsm) < 80 else 14.0
	if "UNIT 1" in unit or "UNIT 2" in unit:
		return 10.0
	return 0.0


def _spr_patty_machine_width_inch(spr) -> float:
	doc = spr.as_dict() if hasattr(spr, "as_dict") else {}
	for key, val in (doc or {}).items():
		if isinstance(val, (list, dict, tuple)):
			continue
		kl = _cstr(key).lower()
		if ("machine" in kl and "width" in kl) or kl in (
			"total_machine_width",
			"custom_machine_width",
			"machine_width",
		):
			try:
				w = flt(val or 0)
			except Exception:
				continue
			if w > 0:
				return w
	return 63.0


def _spr_patty_parse_combo_inches(raw) -> float:
	text = _cstr(raw)
	if not text:
		return 0.0
	total = 0.0
	for token in re.split(r"[+,]", text):
		digits = re.sub(r"[^\d.]", "", token or "")
		n = flt(digits or 0)
		if n > 0:
			total += n
	return total


def _spr_patty_combination_total_inch(job_row, item_row=None) -> float:
	"""Same field discovery as desk wastage_automation.js — never use production roll width_inch."""
	combination_total = 0.0
	if job_row:
		combo_val = None
		for key in _spr_patty_row_keys(job_row):
			kl = _cstr(key).lower()
			if "combin" in kl or "combo" in kl or kl == "combination_inches":
				combo_val = _spr_row_get(job_row, key)
				break
		combination_total = _spr_patty_parse_combo_inches(combo_val)
		# Leftover uses combination inches only — not production/sticker width_inch.
		if combination_total <= 0:
			for key in _spr_patty_row_keys(job_row):
				kl = _cstr(key).lower()
				if kl in ("combined_width", "total_width", "width"):
					combination_total = flt(_spr_row_get(job_row, key) or 0)
					if combination_total > 0:
						break
	if combination_total <= 0 and item_row:
		combination_total = flt(
			getattr(item_row, "width", 0)
			or getattr(item_row, "job_width", 0)
			or getattr(item_row, "custom_width", 0)
			or 0
		)
	return combination_total


def _spr_patty_trim_width_inch(spr, job_row, gsm, item_row=None) -> float:
	"""Unit trim width + leftover (machine width − combination), same as desk client script."""
	width = _spr_patty_unit_base_width_inch(spr, gsm)
	machine = _spr_patty_machine_width_inch(spr)
	combo = _spr_patty_combination_total_inch(job_row, item_row)
	extra = max(0.0, machine - combo) if machine > 0 and combo > 0 else 0.0
	return flt(width + extra)


def _spr_patty_job_row(spr, jid):
	found = _spr_shaft_job_for_roll(spr, jid)
	if found:
		return found
	want = _cstr(jid)
	if not want:
		return None
	for sj in spr.shaft_jobs or []:
		cands = (
			_cstr(_spr_job_id(sj)),
			_cstr(getattr(sj, "job", None)),
			_cstr(getattr(sj, "idx", None)),
			_cstr(getattr(sj, "name", None)),
			_cstr(getattr(sj, "work_order", None)),
		)
		if want in cands:
			return sj
	return None


def _spr_patty_tail_weight_kg(gsm, width_inch, meter) -> float:
	"""One-shaft patty tail: (GSM × width_inch × meter × 0.0254) / 1000."""
	g, w, m = flt(gsm), flt(width_inch), flt(meter)
	if g <= 0 or w <= 0 or m <= 0:
		return 0.0
	return flt((g * w * m * 0.0254) / 1000.0, 3)


def _spr_row_first_positive(row, keys) -> float:
	if row is None:
		return 0.0
	for key in keys:
		try:
			val = flt(_spr_row_get(row, key) if not isinstance(row, dict) else row.get(key) or 0)
		except Exception:
			val = 0.0
		if val > 0:
			return val
	# fuzzy: any key containing fragments
	for key in _spr_patty_row_keys(row):
		kl = _cstr(key).lower()
		for frag in keys:
			fl = _cstr(frag).lower()
			if fl and fl in kl:
				try:
					val = flt(_spr_row_get(row, key) if not isinstance(row, dict) else row.get(key) or 0)
				except Exception:
					val = 0.0
				if val > 0:
					return val
	return 0.0


def _spr_compute_patty_wastage_by_job(spr, *, include_unproduced_jobs: bool | None = None) -> dict[str, dict]:
	"""Running patty wastage — same concept as desk wastage_automation.js.

	By default, once any produced rolls exist, only jobs that have produced rolls get a
	wastage row (2 rolls on 1 job → 1 row, not one row per Available Job).
	When there are no produced rolls yet, Available Jobs are included for GSM preview.
	Pass include_unproduced_jobs=True to force all Available Jobs (desk repair / tools).
	"""
	if not _spr_patty_is_valid_unit(spr):
		return {}

	job_rolls: dict[str, list] = {}
	for it in spr.items or []:
		if not _spr_is_real_roll_item_row(it):
			continue
		# Only count rolls the operator actually produced (gross / produced length),
		# not empty planned Create Entry slots that already have a batch_no.
		if not _spr_roll_has_production(it):
			continue
		jid = _cstr(
			getattr(it, "job", None)
			or getattr(it, "job_id", None)
			or getattr(it, "custom_job", None)
			or getattr(it, "work_order", None)
		)
		if not jid:
			continue
		job_rolls.setdefault(jid, []).append(it)

	has_produced_rolls = bool(job_rolls)
	# Default: preview-from-jobs only when SPR has no produced rolls yet.
	if include_unproduced_jobs is None:
		include_unproduced_jobs = not has_produced_rolls

	if include_unproduced_jobs:
		for sj in spr.shaft_jobs or []:
			jid = _cstr(_spr_job_id(sj) or getattr(sj, "job", None) or "")
			if not jid:
				continue
			job_rolls.setdefault(jid, job_rolls.get(jid) or [])

	if not job_rolls:
		return {}

	out: dict[str, dict] = {}
	for jid, rolls in job_rolls.items():
		item_row = rolls[0] if rolls else None
		job_row = _spr_patty_job_row(spr, jid)

		specs = (
			_gsm_resolve_item_row_display_specs(item_row)
			if item_row is not None
			else {"quality": "", "color": "", "gsm": 0, "item_code": "", "item_name": ""}
		)
		if job_row and (not specs.get("quality") or not specs.get("color") or not specs.get("gsm")):
			job_specs = {
				"quality": _cstr(_spr_row_get(job_row, "quality") or ""),
				"color": _cstr(_spr_row_get(job_row, "color") or _spr_row_get(job_row, "colour") or ""),
				"gsm": cint(_spr_row_get(job_row, "gsm") or 0),
			}
			if not specs.get("quality"):
				specs["quality"] = job_specs["quality"]
			if not specs.get("color"):
				specs["color"] = job_specs["color"]
			if not specs.get("gsm"):
				specs["gsm"] = job_specs["gsm"]

		gsm = cint(
			(_spr_row_get(job_row, "gsm") if job_row else 0)
			or specs.get("gsm")
			or (getattr(item_row, "gsm", 0) if item_row is not None else 0)
			or 0
		)
		if gsm <= 0 and item_row is not None:
			ic = _cstr(getattr(item_row, "item_code", "") or "")
			if ic:
				gsm = cint(_spr_resolve_roll_line_specs_from_item_code(ic).get("gsm") or 0)

		meter = 0.0
		if job_row:
			meter = _spr_row_first_positive(
				job_row,
				(
					"meter__roll",
					"meter_per_roll",
					"meter_roll",
					"meter",
					"custom_meter_roll",
					"custom_meter_per_roll",
					"produced_length_mtrs",
				),
			)
		if meter <= 0 and rolls:
			# Prefer ordered/produced length on any roll of this job
			for r in rolls:
				meter = _spr_row_first_positive(
					r,
					(
						"meter_roll",
						"meter__roll",
						"meter_per_roll",
						"produced_length_mtrs",
						"custom_produced_length_mtrs",
						"length",
						"custom_meter_roll",
						"ordered_length_mtrs",
					),
				)
				if meter > 0:
					break

		shafts = 0
		if job_row:
			shafts = cint(
				_spr_row_first_positive(
					job_row, ("no_of_shafts", "no_of_shaft", "shafts", "custom_no_of_shafts")
				)
				or 0
			)
		if shafts <= 0 and rolls:
			shafts = max(
				cint(getattr(r, "custom_no_of_shaft", 0) or getattr(r, "no_of_shaft", 0) or 0)
				for r in rolls
			) or 1
		elif shafts <= 0:
			shafts = 1

		width = _spr_patty_trim_width_inch(spr, job_row, gsm, item_row)
		if width <= 0:
			# Unit base alone (10/12/14/15) — desk still uses this when combo missing
			width = _spr_patty_unit_base_width_inch(spr, gsm)
		if width <= 0 and item_row is not None:
			ic = _cstr(getattr(item_row, "item_code", "") or "")
			if len(ic) >= 16:
				mm = flt(ic[12:16])
				if mm > 0:
					width = mm / 25.4
			if width <= 0:
				width = flt(getattr(item_row, "width_inch", 0) or getattr(item_row, "width", 0) or 0)

		tail = _spr_patty_tail_weight_kg(gsm, width, meter)
		if tail <= 0:
			continue

		party_code = _cstr(
			(getattr(rolls[0], "party_code", None) if rolls else None)
			or (getattr(job_row, "party_code", None) if job_row else None)
			or ""
		)
		wastage_qty = flt(shafts * tail, 3)
		# Default unchecked — recycled only after Recycle to Next is applied
		recycled_qty = 0.0
		out[jid] = {
			"job_id": jid,
			"quality": _cstr(specs.get("quality") or ""),
			"color": _cstr(specs.get("color") or ""),
			"gsm": gsm,
			"width_inch": width,
			"width": width,
			"meter_per_roll": meter,
			"no_of_shafts": shafts,
			"one_shaft_gross": tail,
			"wastage": wastage_qty,
			"wastage_qty": wastage_qty,
			"net_wastage": tail,
			"recycled": recycled_qty,
			"recycled_qty": recycled_qty,
			"recycle_to_next": 0,
			"order_code": party_code,
			"party_code": party_code,
			"batch_no": _cstr(getattr(item_row, "batch_no", None) or "") if item_row is not None else "",
			"item_code": _cstr(getattr(item_row, "item_code", None) or "") if item_row is not None else "",
			"item_name": _cstr(getattr(item_row, "item_name", None) or "") if item_row is not None else "",
			"item": _cstr(getattr(item_row, "item_code", None) or "") if item_row is not None else "",
			"_from_jobs_only": 0 if rolls else 1,
		}
	return out


def _spr_apply_patty_recycle_net(logical: dict, recycle_to_next: int = 0) -> dict:
	"""Apply Recycle to Next.

	- Checked: net_wastage = 0 and recycled_qty = (shafts-1) × tail (stock recycle active).
	- Unchecked: net_wastage = one tail; recycled_qty = 0 (no recycle until ticked).
	Always keeps wastage_qty / width / meter / one_shaft_gross populated.
	"""
	out = dict(logical or {})
	flag = 1 if cint(recycle_to_next) else 0
	out["recycle_to_next"] = flag
	shafts = max(cint(out.get("no_of_shafts") or 1), 1)
	tail = flt(
		out.get("one_shaft_gross")
		or (
			(flt(out.get("wastage_qty") or out.get("wastage") or 0) / shafts)
			if flt(out.get("wastage_qty") or out.get("wastage") or 0) > 0
			else 0
		)
		or (out.get("net_wastage") if flt(out.get("net_wastage") or 0) > 0 else 0)
		or 0
	)
	if tail > 0:
		out["one_shaft_gross"] = tail
		wastage_qty = flt(shafts * tail, 3)
		out["wastage"] = wastage_qty
		out["wastage_qty"] = wastage_qty
		# Recycled qty only when operator ticks Recycle to Next
		recycled_qty = flt(((shafts - 1) * tail) if flag and shafts > 1 else 0.0, 3)
		out["recycled"] = recycled_qty
		out["recycled_qty"] = recycled_qty
	else:
		out["recycled"] = 0.0
		out["recycled_qty"] = 0.0
	# Never put 0 into wastage_qty — only net_wastage goes to 0 when recycling.
	out["net_wastage"] = 0.0 if flag else flt(tail or 0)
	return out


def _spr_patty_rows_are_effectively_empty(spr, field: str) -> bool:
	"""True when table is missing or every row has zero wastage qty (broken GSM write)."""
	rows = spr.get(field) or []
	if not rows:
		return True
	for row in rows:
		row_dict = row.as_dict() if hasattr(row, "as_dict") else dict(row or {})
		if _spr_patty_row_wastage_kg(row_dict) > 0:
			return False
		# Also treat one_shaft_gross / meter as evidence of a real row
		if flt(row_dict.get("one_shaft_gross") or row_dict.get("one_shaft_wastage") or 0) > 0:
			return False
		if flt(
			row_dict.get("meter_per_roll")
			or row_dict.get("meter__roll")
			or row_dict.get("meter_roll")
			or row_dict.get("meter")
			or 0
		) > 0 and cint(row_dict.get("no_of_shafts") or row_dict.get("shafts") or 0) > 0:
			# shafts+meter but zero qty → broken; allow refresh
			continue
	return True


def _spr_recycled_wastage_fieldname() -> str | None:
	meta = frappe.get_meta("Shaft Production Run")
	if meta.has_field("custom_recycled_wastage_details"):
		return "custom_recycled_wastage_details"
	for df in meta.fields or []:
		if df.fieldtype != "Table":
			continue
		fn = _cstr(df.fieldname).lower()
		if "manual" in fn or "gsm_manual" in fn:
			continue
		if "recycled_wastage" in fn or fn == "recycled_wastage_details":
			return df.fieldname
	return None


def _spr_write_recycled_child_row(logical: dict) -> dict:
	"""Map recycled wastage detail keys onto live child fields.

	Live site (Recycled Wastage Details): width, meter__roll, available_qty_kgs, recycled_qty_kgs.
	"""
	child_dt = _spr_recycled_child_doctype()
	if not frappe.db.exists("DocType", child_dt):
		return {k: v for k, v in (logical or {}).items() if not _cstr(k).startswith("_")}
	meta = frappe.get_meta(child_dt)
	existing = {df.fieldname for df in meta.fields}
	# Prefer label/name discovery for Available / Recycled columns
	avail_f = _spr_meta_find_field(meta, ["available_qty_kgs", "available"])
	recy_f = _spr_meta_find_field(meta, ["recycled_qty_kgs", "recycled"], ["recycle_to", "detail", "wastage"])
	meter_f = _spr_meta_find_field(meta, ["meter__roll", "meter_roll_mtrs", "meter"], ["wastage", "shaft"]) or _spr_meta_find_field(
		meta, ["roll"], ["shaft", "no_of", "wastage"]
	)
	width_f = _spr_meta_find_field(meta, ["width"], ["core"])
	shaft_f = _spr_meta_find_field(meta, ["shaft"], ["one_shaft", "wastage"])
	aliases = {
		"job_id": ("job_id", "job"),
		"quality": ("quality", "custom_quality"),
		"color": ("color", "fabric_colour", "custom_color", "colour"),
		"gsm": ("gsm",),
		"width_inch": (width_f, "width", "width_inches", "width_inch", "w", "patty_width") if width_f else ("width", "width_inches", "width_inch", "w", "patty_width"),
		"width": (width_f, "width", "width_inches", "width_inch", "w") if width_f else ("width", "width_inches", "width_inch", "w"),
		"meter_per_roll": (meter_f, "meter__roll", "meter_roll_mtrs", "meter_per_roll", "meter_roll", "meter")
		if meter_f
		else ("meter__roll", "meter_roll_mtrs", "meter_per_roll", "meter_roll", "meter"),
		"no_of_shafts": (shaft_f, "no_of_shafts", "shafts", "no_of_shaft")
		if shaft_f
		else ("no_of_shafts", "shafts", "no_of_shaft"),
		"available": (avail_f, "available_qty_kgs", "available_qty", "available", "available_kg")
		if avail_f
		else ("available_qty_kgs", "available_qty", "available", "available_kg"),
		"available_qty": (avail_f, "available_qty_kgs", "available_qty", "available", "available_kg")
		if avail_f
		else ("available_qty_kgs", "available_qty", "available", "available_kg"),
		"recycled": (recy_f, "recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg")
		if recy_f
		else ("recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"),
		"recycled_qty": (recy_f, "recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg")
		if recy_f
		else ("recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"),
		"wastage": ("wastage_qty_kgs", "wastage", "wastage_qty"),
		"wastage_qty": ("wastage_qty_kgs", "wastage_qty", "wastage"),
		"calculation_details": ("calculation_details",),
	}
	out: dict = {}
	for key, val in (logical or {}).items():
		if key.startswith("_") or val is None:
			continue
		if isinstance(val, str) and not val.strip():
			continue
		wrote = False
		for fn in aliases.get(key, (key,)):
			if not fn:
				continue
			if fn in existing:
				out[fn] = val
				wrote = True
				# Multi-write qty synonyms so Available Qty (Kgs) and Recycled Qty (Kgs) both fill
				if key in ("available", "available_qty", "recycled", "recycled_qty"):
					continue
				break
		if not wrote and key in existing:
			out[key] = val
	return out


def _spr_sync_recycled_wastage_from_patty(spr) -> int:
	"""Rebuild auto Recycled Wastage Details from Running Patty (desk update_recycled_table).

	Preserves manual / Patty-stock rows (job_id == 'Patty'). Does not touch GSM Manual Recycle.
	Fills missing width/meter/qty from live compute when the patty row was previously zeroed.
	"""
	patty_field = _spr_patty_wastage_fieldname()
	rec_field = _spr_recycled_wastage_fieldname()
	if not patty_field or not rec_field:
		return 0

	existing = list(spr.get(rec_field) or [])
	keep = []
	for row in existing:
		jid = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "").lower()
		if jid == "patty":
			keep.append(row)

	computed = {}
	try:
		computed = _spr_compute_patty_wastage_by_job(spr) or {}
	except Exception:
		computed = {}

	added = 0
	for w_row in spr.get(patty_field) or []:
		w = w_row.as_dict() if hasattr(w_row, "as_dict") else dict(w_row or {})
		# Only create Recycled Wastage Details when Recycle to Next is ticked
		if not cint(w.get("recycle_to_next") or w.get("custom_recycle_to_next") or 0):
			continue
		jid = _cstr(w.get("job_id") or w.get("job") or "")
		comp = computed.get(jid) or {}
		shafts = cint(w.get("no_of_shafts") or w.get("shafts") or w.get("no_of_shaft") or comp.get("no_of_shafts") or 1)
		tail = flt(w.get("one_shaft_gross") or w.get("one_shaft_wastage") or w.get("one_shaft") or 0)
		if tail <= 0:
			tail = flt(comp.get("one_shaft_gross") or 0)
		if tail <= 0:
			qty = _spr_patty_row_wastage_kg(w) or flt(comp.get("wastage_qty") or comp.get("wastage") or 0)
			tail = flt(qty / shafts, 3) if shafts > 0 and qty > 0 else 0.0
		recycled_shafts = (shafts - 1) if shafts > 1 else 0
		total_available = flt(recycled_shafts * tail, 3)
		if total_available <= 0:
			continue
		width = flt(w.get("width_inch") or w.get("width") or 0) or flt(comp.get("width_inch") or comp.get("width") or 0)
		meter = flt(
			w.get("meter_per_roll") or w.get("meter__roll") or w.get("meter_roll") or w.get("meter") or 0
		) or flt(comp.get("meter_per_roll") or 0)
		wastage_qty = flt(w.get("wastage_qty") or w.get("wastage") or 0) or flt(
			comp.get("wastage_qty") or comp.get("wastage") or (shafts * tail)
		)
		logical = {
			"job_id": jid,
			"quality": _cstr(w.get("quality") or comp.get("quality") or ""),
			"color": _cstr(w.get("color") or w.get("colour") or comp.get("color") or ""),
			"gsm": cint(w.get("gsm") or comp.get("gsm") or 0),
			"width_inch": width,
			"width": width,
			"meter_per_roll": meter,
			"no_of_shafts": shafts,
			"available": total_available,
			"available_qty": total_available,
			"recycled": total_available,
			"recycled_qty": total_available,
			"wastage": wastage_qty,
			"wastage_qty": wastage_qty,
			"calculation_details": f"{recycled_shafts} shaft(s) × {tail:.3f} Kg = {total_available:.3f} Kg recycled",
		}
		values = _spr_write_recycled_child_row(logical)
		if not values:
			continue
		keep.append(values)
		added += 1

	spr.set(rec_field, keep)
	return added


def _spr_core_details_fieldname() -> str | None:
	meta = frappe.get_meta("Shaft Production Run")
	if meta.has_field("custom_core_details"):
		return "custom_core_details"
	for df in meta.fields or []:
		if df.fieldtype != "Table":
			continue
		opts = _cstr(df.options or "")
		label = _cstr(df.label or "").lower()
		if opts == "Shaft Core Detail" or "core detail" in label:
			return df.fieldname
	return None


def _spr_map_core_item_for_inch(inch: float) -> tuple[str, str]:
	"""Desk spr_site_core_automation.js stock core item map."""
	w = flt(inch)
	if w <= 63:
		return "PC - 1005307", '63"'
	if w <= 85:
		return "PC - 1005158", '85"'
	if w <= 90:
		return "PC - 1005308", '90"'
	if w <= 118:
		return "PC - 1005161", '118"'
	return "PC - 1005309", '126"'


def _spr_selected_core_inch_from_row(row, width_inch: float) -> float:
	"""Prefer explicit core width link / value on the roll line."""
	raw = _cstr(
		getattr(row, "custom_core_width_mm", None)
		or getattr(row, "core_item_code", None)
		or getattr(row, "core_item_name", None)
		or ""
	)
	import re

	m = re.search(r"(\d+(?:\.\d+)?)", raw)
	if m:
		val = flt(m.group(1))
		# mm values are typically >= 1000; inch labels are 63–126
		if val >= 200:
			from production_entry.production_planning.unified_production_entry_api import (
				_fabric_width_to_stock_core_inch,
			)

			# Map mm presets back roughly via fabric width helper on inches of fabric
			return _fabric_width_to_stock_core_inch(width_inch) if width_inch > 0 else 63.0
		if val > 0:
			return val
	from production_entry.production_planning.unified_production_entry_api import (
		_fabric_width_to_stock_core_inch,
	)

	return _fabric_width_to_stock_core_inch(width_inch) if width_inch > 0 else 0.0


def _spr_cores_per_shaft(unit_text: str, core_inch: float) -> int:
	"""Unit 1/4 → 1. Unit 2/3 → 2 if core ≤63", else 1."""
	u = _cstr(unit_text).upper()
	if "UNIT 1" in u or "UNIT 4" in u:
		return 1
	if "UNIT 2" in u or "UNIT 3" in u:
		return 2 if flt(core_inch) <= 63 else 1
	return 1


def _spr_roll_shaft_key(row) -> str:
	for key in ("custom_no_of_shaft", "no_of_shaft", "custom_shaft_no", "shaft_no"):
		val = getattr(row, key, None)
		if val is not None and _cstr(val) != "":
			return _cstr(val)
	return f"row:{_cstr(getattr(row, 'name', None) or getattr(row, 'idx', None) or '')}"


def _spr_compute_core_details_from_items(spr) -> list[dict]:
	"""Aggregate custom_core_details like desk calculate_aggregate_totals (fabric units).

	Quantity Nos = unique shafts × cores_per_shaft (not one core per roll row).
	"""
	unit = _spr_patty_unit_text(spr)
	unit_u = unit.upper()
	if any(x in unit_u for x in ("JVE", "SHEET CUTTING", "BAG")):
		return []
	if any(x in unit_u for x in ("REWINDING",)):
		return []
	if not any(u in unit_u for u in ("UNIT 1", "UNIT 2", "UNIT 3", "UNIT 4")):
		# Still allow when unit blank but rolls look like fabric
		if not (spr.items or []):
			return []

	totals: dict[str, dict] = {}
	for row in spr.items or []:
		if not _spr_is_real_roll_item_row(row):
			continue
		width = flt(getattr(row, "width_inch", None) or 0)
		if width <= 0:
			ic = _cstr(getattr(row, "item_code", "") or "")
			if len(ic) == 16:
				mm = flt(ic[12:16])
				if mm > 0:
					width = mm / 25.4
		if width <= 0:
			continue
		selected_inch = _spr_selected_core_inch_from_row(row, width)
		map_inch = selected_inch or width
		ic, name = _spr_map_core_item_for_inch(map_inch)
		# Prefer explicit core item on the row when present
		explicit = _cstr(getattr(row, "core_item_code", None) or "")
		if explicit.startswith("PC"):
			ic = explicit
			name = _cstr(getattr(row, "core_item_name", None) or name)
		bucket = totals.setdefault(
			ic,
			{
				"core_item": ic,
				"item_name": name,
				"core_nos": 0,
				"quantity_kgs": 0.0,
				"wastage_quantity_kgs": 0.0,
				"_shafts": set(),
			},
		)
		sk = _spr_roll_shaft_key(row)
		if sk not in bucket["_shafts"]:
			bucket["_shafts"].add(sk)
			bucket["core_nos"] += _spr_cores_per_shaft(unit, selected_inch or map_inch)
		shaft_core_kgs = flt(getattr(row, "gross_weight", None) or 0) - flt(getattr(row, "net_weight", None) or 0)
		if shaft_core_kgs < 0:
			shaft_core_kgs = 0.0
		# Proportional used/wastage when fabric width < selected core inch (desk parity)
		base_weight = shaft_core_kgs
		if selected_inch > 0 and width > 0 and width < selected_inch and shaft_core_kgs > 0:
			bucket["quantity_kgs"] += shaft_core_kgs
		else:
			bucket["quantity_kgs"] += shaft_core_kgs if shaft_core_kgs > 0 else base_weight

	out = []
	for row in totals.values():
		if cint(row.get("core_nos") or 0) <= 0:
			continue
		out.append(
			{
				"core_item": row["core_item"],
				"item_name": row["item_name"],
				"core_nos": cint(row["core_nos"]),
				"quantity_kgs": flt(row["quantity_kgs"], 3),
				"wastage_quantity_kgs": flt(row.get("wastage_quantity_kgs") or 0, 3),
				"uom": "Kg",
				"conversion_factor": 1,
			}
		)
	return out


def _spr_write_core_child_row(logical: dict) -> dict:
	child_dt = "Shaft Core Detail"
	if not frappe.db.exists("DocType", child_dt):
		return logical
	meta = frappe.get_meta(child_dt)
	existing = {df.fieldname for df in meta.fields}
	out: dict = {}
	aliases = {
		"core_item": ("core_item", "item_code"),
		"item_name": ("item_name",),
		"core_nos": ("core_nos", "quantity_nos", "qty_nos"),
		"quantity_kgs": ("quantity_kgs", "consumed_quantity", "qty"),
		"wastage_quantity_kgs": ("wastage_quantity_kgs", "wastage_kgs"),
		"uom": ("uom",),
		"conversion_factor": ("conversion_factor",),
	}
	for key, val in (logical or {}).items():
		if val is None:
			continue
		wrote = False
		for fn in aliases.get(key, (key,)):
			if fn in existing:
				out[fn] = val
				wrote = True
		if not wrote and key in existing:
			out[key] = val
	return out


def _spr_table_row_count(spr, fieldname: str) -> int:
	if not fieldname:
		return 0
	rows = getattr(spr, fieldname, None) or []
	if rows:
		return len(rows)
	df = frappe.get_meta("Shaft Production Run").get_field(fieldname)
	child_dt = _cstr(getattr(df, "options", None) or "") if df else ""
	if not child_dt or not frappe.db.table_exists(child_dt):
		return 0
	return cint(
		frappe.db.count(child_dt, {"parent": spr.name, "parenttype": "Shaft Production Run", "parentfield": fieldname})
	)


def persist_spr_patty_and_core(
	spr, *, only_if_empty: bool = True, save_if_draft: bool = True, refresh_zero_rows: bool = False
) -> dict:
	"""Write Running Patty Wastage + Core Details onto SPR (draft save or submitted db_insert).

	Submitted docs use child.db_insert only — no parent.save() — so desk forms do not go dirty.
	During before_submit pass save_if_draft=False so rows ride on the submit write.

	When refresh_zero_rows=True (or only_if_empty and rows exist with all-zero qty), rewrite
	patty from jobs/rolls so Recycle to Next / GSM does not leave shafts=N with qty=0.
	Also sync auto Recycled Wastage Details from patty rows.
	"""
	if isinstance(spr, str):
		spr = frappe.get_doc("Shaft Production Run", spr)
	result = {"spr_name": spr.name, "patty_added": 0, "core_added": 0, "recycled_synced": 0, "skipped": []}

	try:
		spr.calculate_produced_gsm(missing_only=True)
	except Exception:
		pass

	submitted = cint(spr.docstatus) == 1
	patty_field = _spr_patty_wastage_fieldname()
	core_field = _spr_core_details_fieldname()

	# --- Patty wastage ---
	if patty_field:
		existing_patty = _spr_table_row_count(spr, patty_field)
		zero_broken = refresh_zero_rows or (
			only_if_empty and existing_patty > 0 and _spr_patty_rows_are_effectively_empty(spr, patty_field)
		)
		if only_if_empty and existing_patty > 0 and not zero_broken:
			result["skipped"].append("patty_already_present")
		else:
			# Preserve recycle_to_next flags before rewrite
			saved_flags = {}
			for row in spr.get(patty_field) or []:
				jid = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
				if not jid:
					continue
				saved_flags[jid] = cint(
					getattr(row, "recycle_to_next", None) or getattr(row, "custom_recycle_to_next", None) or 0
				)

			computed = _spr_compute_patty_wastage_by_job(spr) or {}
			if not computed:
				result["skipped"].append("patty_no_computed")
				result["compute_debug"] = {
					"unit": _cstr(spr.get("custom_unit") or spr.get("unit") or ""),
					"jobs": len(spr.shaft_jobs or []),
					"items": len([it for it in (spr.items or []) if _spr_is_real_roll_item_row(it)]),
				}
			else:
				existing_rows = list(spr.get(patty_field) or [])
				# Match computed → existing by job_id, then leftover by position
				matched_existing = set()
				computed_list = list(computed.values())
				assignments = []  # (logical, existing_row|None)
				for logical in computed_list:
					jid = _cstr(logical.get("job_id") or "")
					found = None
					if jid:
						for row in existing_rows:
							rn = _cstr(getattr(row, "name", None) or "")
							if rn in matched_existing:
								continue
							row_job = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
							if row_job and _spr_job_keys_match(row_job, jid):
								found = row
								matched_existing.add(rn)
								break
					assignments.append((logical, found))
				# Position-match remaining existing zero rows to unmatched computed
				unmatched_idx = 0
				for i, (logical, found) in enumerate(assignments):
					if found is not None:
						continue
					while unmatched_idx < len(existing_rows):
						row = existing_rows[unmatched_idx]
						unmatched_idx += 1
						rn = _cstr(getattr(row, "name", None) or "")
						if rn in matched_existing:
							continue
						assignments[i] = (logical, row)
						matched_existing.add(rn)
						break

				idx = 0
				for logical, existing in assignments:
					if flt(logical.get("wastage") or logical.get("wastage_qty") or 0) <= 0:
						continue
					jid = _cstr(logical.get("job_id") or "")
					flag = saved_flags.get(jid, cint(logical.get("recycle_to_next") or 0))
					if existing is not None and not flag:
						flag = cint(
							getattr(existing, "recycle_to_next", None)
							or getattr(existing, "custom_recycle_to_next", None)
							or 0
						)
					logical = _spr_apply_patty_recycle_net(logical, flag)
					values = _spr_write_patty_child_row(logical)
					if not values:
						continue
					idx += 1
					values["idx"] = idx
					if existing is not None and submitted:
						for k, v in values.items():
							if k in ("name", "parent", "parenttype", "parentfield", "doctype"):
								continue
							try:
								existing.set(k, v)
								existing.db_set(k, v, update_modified=False)
							except Exception:
								try:
									frappe.db.set_value(
										existing.doctype, existing.name, k, v, update_modified=False
									)
								except Exception:
									pass
						result["patty_added"] += 1
					elif existing is not None and not submitted:
						for k, v in values.items():
							if k in ("name", "parent", "parenttype", "parentfield", "doctype"):
								continue
							try:
								existing.set(k, v)
							except Exception:
								setattr(existing, k, v)
						result["patty_added"] += 1
					elif submitted:
						child = spr.append(patty_field, values)
						child.db_insert()
						result["patty_added"] += 1
					else:
						spr.append(patty_field, values)
						result["patty_added"] += 1

				# Drop leftover existing rows that are still zero after repair (submitted)
				if submitted and zero_broken:
					for row in existing_rows:
						rn = _cstr(getattr(row, "name", None) or "")
						if rn in matched_existing:
							continue
						row_dict = row.as_dict() if hasattr(row, "as_dict") else {}
						if _spr_patty_row_wastage_kg(row_dict) > 0:
							continue
						try:
							frappe.delete_doc(
								row.doctype, row.name, force=1, ignore_permissions=True, delete_permanently=True
							)
						except Exception:
							try:
								frappe.db.sql(f"DELETE FROM `tab{row.doctype}` WHERE name=%s", (row.name,))
							except Exception:
								pass
				elif (not only_if_empty or zero_broken) and not submitted and not any(
					a[1] is not None for a in assignments
				):
					# Draft with no matchable rows — clear and rewrite fresh
					spr.set(patty_field, [])
					idx = 0
					for logical in computed_list:
						if flt(logical.get("wastage") or logical.get("wastage_qty") or 0) <= 0:
							continue
						jid = _cstr(logical.get("job_id") or "")
						flag = saved_flags.get(jid, 0)
						logical = _spr_apply_patty_recycle_net(logical, flag)
						values = _spr_write_patty_child_row(logical)
						if not values:
							continue
						idx += 1
						values["idx"] = idx
						spr.append(patty_field, values)
						result["patty_added"] += 1
				elif not submitted and (not only_if_empty or zero_broken) and matched_existing:
					# Drop leftover draft patty rows for jobs that no longer compute
					# (e.g. old "all Available Jobs" rows after rolls exist for 1 job only).
					keep_names = set(matched_existing)
					kept = []
					for row in existing_rows:
						rn = _cstr(getattr(row, "name", None) or "")
						if rn and rn in keep_names:
							kept.append(row)
							continue
						row_job = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
						if row_job and any(
							_spr_job_keys_match(row_job, _cstr(logical.get("job_id") or ""))
							for logical, _ex in assignments
						):
							kept.append(row)
					if len(kept) != len(existing_rows):
						spr.set(patty_field, kept)
	else:
		result["skipped"].append("patty_field_missing")

	# Reload submitted doc so recycled sync sees in-place db_set values
	if submitted and result.get("patty_added") and spr.name:
		try:
			spr = frappe.get_doc("Shaft Production Run", spr.name)
		except Exception:
			pass

	# --- Recycled wastage details (auto from patty) ---
	try:
		rec_field = _spr_recycled_wastage_fieldname()
		if rec_field:
			auto_existing = [
				r
				for r in (spr.get(rec_field) or [])
				if _cstr(getattr(r, "job_id", None) or "").lower() != "patty"
			]
			need_recycled = (
				(not submitted)
				or refresh_zero_rows
				or result.get("patty_added")
				or (submitted and not auto_existing)
			)
			if need_recycled and submitted:
				keep_patty = []
				for row in list(spr.get(rec_field) or []):
					jid = _cstr(getattr(row, "job_id", None) or "").lower()
					if jid == "patty":
						keep_patty.append(row)
					else:
						try:
							frappe.delete_doc(
								row.doctype, row.name, force=1, ignore_permissions=True, delete_permanently=True
							)
						except Exception:
							frappe.db.sql(f"DELETE FROM `tab{row.doctype}` WHERE name=%s", (row.name,))
				spr.set(rec_field, keep_patty)
				added = _spr_sync_recycled_wastage_from_patty(spr)
				for row in spr.get(rec_field) or []:
					if getattr(row, "name", None) and frappe.db.exists(row.doctype, row.name):
						continue
					if hasattr(row, "db_insert"):
						row.db_insert()
				result["recycled_synced"] = added
			elif need_recycled and not submitted:
				result["recycled_synced"] = _spr_sync_recycled_wastage_from_patty(spr)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"SPR sync recycled from patty:{spr.name}")
		result["skipped"].append("recycled_sync_failed")

	# --- Core details ---
	if core_field and frappe.db.exists("DocType", "Shaft Core Detail"):
		existing_core = _spr_table_row_count(spr, core_field)
		if only_if_empty and existing_core > 0:
			result["skipped"].append("core_already_present")
		else:
			computed_core = _spr_compute_core_details_from_items(spr) or []
			if not computed_core:
				result["skipped"].append("core_no_computed")
			else:
				if not only_if_empty and existing_core > 0 and not submitted:
					spr.set(core_field, [])
				idx = existing_core
				for logical in computed_core:
					values = _spr_write_core_child_row(logical)
					if not values:
						continue
					idx += 1
					values["idx"] = idx
					if submitted:
						child = spr.append(core_field, values)
						child.db_insert()
					else:
						spr.append(core_field, values)
					result["core_added"] += 1
	else:
		result["skipped"].append("core_field_missing")

	if (
		not submitted
		and save_if_draft
		and (result["patty_added"] or result["core_added"] or result["recycled_synced"])
	):
		spr.flags._spr_incremental_roll_save = True
		spr.flags.ignore_version = True
		spr.save(ignore_permissions=True)

	result["status"] = "ok"
	return result


def sync_running_patty_wastage_from_items(
	spr, *, persist: bool = False, refresh_zero_rows: bool = False
) -> bool:
	"""Persist computed running patty wastage (+ core when persist)."""
	if not persist:
		return False
	res = persist_spr_patty_and_core(
		spr, only_if_empty=not refresh_zero_rows, refresh_zero_rows=refresh_zero_rows
	)
	return bool(res.get("patty_added") or res.get("core_added") or res.get("recycled_synced"))


@frappe.whitelist()
def sync_spr_running_patty_wastage(spr_name, persist=1, refresh_zero_rows=0):
	"""Persist patty wastage (and core) from roll lines onto the SPR.

	Pass refresh_zero_rows=1 to rewrite rows that have shafts/recycle but qty=0
	(common after GSM Recycle to Next before this fix).
	"""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	force = cint(refresh_zero_rows)
	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		res = persist_spr_patty_and_core(
			spr_name, only_if_empty=not force, refresh_zero_rows=bool(force)
		)
	res["synced"] = bool(res.get("patty_added") or res.get("core_added") or res.get("recycled_synced"))
	return res


def _spr_submitted_needs_wastage_repair(spr_name: str) -> bool:
	"""True when submitted SPR has empty/zero patty qty or auto-recycle table is empty."""
	patty_field = _spr_patty_wastage_fieldname()
	rec_field = _spr_recycled_wastage_fieldname()
	if not patty_field:
		return False
	try:
		spr = frappe.get_doc("Shaft Production Run", spr_name)
	except Exception:
		return False
	if cint(spr.docstatus) != 1:
		return False
	if not _spr_patty_is_valid_unit(spr):
		return False
	if _spr_patty_rows_are_effectively_empty(spr, patty_field):
		return True
	if rec_field:
		auto = [
			r
			for r in (spr.get(rec_field) or [])
			if _cstr(getattr(r, "job_id", None) or "").lower() != "patty"
		]
		if not auto:
			# Has real wastage but no auto recycled rows — still repair recycle side
			patty_rows = spr.get(patty_field) or []
			if any(_spr_patty_row_wastage_kg(r.as_dict() if hasattr(r, "as_dict") else {}) > 0 for r in patty_rows):
				return True
			if any(
				cint(getattr(r, "recycle_to_next", None) or getattr(r, "custom_recycle_to_next", None) or 0)
				for r in patty_rows
			):
				return True
	return False


@frappe.whitelist()
def backfill_spr_patty_and_core(
	spr_names=None,
	run_date=None,
	shift=None,
	unit=None,
	only_empty=1,
	refresh_zero_rows=1,
	limit=200,
):
	"""Restore Running Patty Wastage + Recycled Wastage Details on submitted/draft SPRs.

	Default refresh_zero_rows=1 repairs rows that show shafts/recycle but qty=0 after GSM submit.
	Also fills empty Recycled Wastage Details from patty rows.
	"""
	import json

	only_empty = cint(only_empty)
	force_refresh = cint(refresh_zero_rows)
	limit = max(1, min(cint(limit) or 200, 500))
	names = spr_names
	if isinstance(names, str):
		try:
			names = json.loads(names)
		except Exception:
			names = [n.strip() for n in names.split(",") if n.strip()]
	names = [_cstr(n).strip() for n in (names or []) if _cstr(n).strip()]

	if not names:
		filters = {"docstatus": ["in", [0, 1]]}
		meta = frappe.get_meta("Shaft Production Run")
		if run_date and meta.has_field("run_date"):
			filters["run_date"] = run_date
		if unit:
			unit_field = "custom_unit" if meta.has_field("custom_unit") else ("unit" if meta.has_field("unit") else None)
			if unit_field:
				filters[unit_field] = ["like", f"%{unit}%"]
		if shift:
			shift_field = "shift" if meta.has_field("shift") else ("custom_shift" if meta.has_field("custom_shift") else None)
			if shift_field:
				filters[shift_field] = ["like", f"%{shift}%"]
		# Prefer recent submitted first
		order = "modified desc"
		candidates = frappe.get_all(
			"Shaft Production Run",
			filters=filters,
			pluck="name",
			order_by=order,
			limit_page_length=limit,
		) or []
		names = [n for n in candidates if _spr_submitted_needs_wastage_repair(n)]
		# If filters narrowed the set (date/unit), also include drafts that need repair
		if run_date or unit or shift:
			for n in candidates:
				if n in names:
					continue
				if cint(frappe.db.get_value("Shaft Production Run", n, "docstatus")) == 0:
					try:
						spr = frappe.get_doc("Shaft Production Run", n)
						pf = _spr_patty_wastage_fieldname()
						if pf and _spr_patty_is_valid_unit(spr) and _spr_patty_rows_are_effectively_empty(spr, pf):
							names.append(n)
					except Exception:
						pass

	results = []
	fixed = 0
	for name in names:
		try:
			with _spr_operation_lock(name, "write", ttl_sec=120):
				res = persist_spr_patty_and_core(
					name,
					only_if_empty=bool(only_empty) and not force_refresh,
					save_if_draft=True,
					refresh_zero_rows=bool(force_refresh),
				)
			if res.get("patty_added") or res.get("recycled_synced") or res.get("core_added"):
				fixed += 1
			results.append(res)
		except Exception as e:
			results.append({"spr_name": name, "status": "error", "error": _cstr(e)})
	return {
		"status": "ok",
		"count": len(results),
		"fixed": fixed,
		"results": results,
	}


@frappe.whitelist()
def repair_submitted_spr_wastage_and_recycle(spr_names=None, run_date=None, unit=None, limit=100):
	"""One-shot: restore wastage qty + Recycled Wastage Details on submitted SPRs that are broken/empty."""
	import json

	names = spr_names
	if isinstance(names, str):
		try:
			names = json.loads(names)
		except Exception:
			names = [n.strip() for n in names.split(",") if n.strip()]
	names = [_cstr(n).strip() for n in (names or []) if _cstr(n).strip()]
	# Always include known broken docs from the floor
	for known in (
		"SPR-2026-00809",
		"SPR-2026-00802",
		"SPR-2026-00803",
		"SPR-2026-00799",
	):
		if known not in names and frappe.db.exists("Shaft Production Run", known):
			names.append(known)
	if not names:
		return backfill_spr_patty_and_core(
			spr_names=None,
			run_date=run_date,
			unit=unit,
			only_empty=0,
			refresh_zero_rows=1,
			limit=limit,
		)
	results = []
	fixed = 0
	for name in names:
		try:
			res = force_recalculate_spr_wastage_and_recycle(name)
			if res.get("patty_rows") or res.get("recycled_rows"):
				fixed += 1
			results.append(res)
		except Exception as e:
			results.append({"spr_name": name, "status": "error", "error": _cstr(e)})
	return {"status": "ok", "count": len(results), "fixed": fixed, "results": results}


@frappe.whitelist()
def force_recalculate_spr_wastage_and_recycle(spr_name):
	"""Hard rebuild Running Patty Wastage + Recycled Wastage Details for one SPR (draft or submitted).

	Deletes existing auto patty/recycle child rows and rewrites from jobs/rolls using live
	field discovery so Width / Meter / Wastage Qty / Recycled Qty actually land for reports.
	"""
	spr_name = _cstr(spr_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))

	with _spr_operation_lock(spr_name, "write", ttl_sec=180):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		patty_field = _spr_patty_wastage_fieldname()
		rec_field = _spr_recycled_wastage_fieldname()
		if not patty_field:
			frappe.throw(_("Running Patty Wastage table is not configured on Shaft Production Run"))

		# Preserve recycle_to_next by job
		saved_flags = {}
		for row in spr.get(patty_field) or []:
			jid = _cstr(getattr(row, "job_id", None) or getattr(row, "job", None) or "")
			if jid:
				saved_flags[jid] = cint(
					getattr(row, "recycle_to_next", None) or getattr(row, "custom_recycle_to_next", None) or 0
				)

		computed = _spr_compute_patty_wastage_by_job(spr) or {}
		live_map = _spr_patty_live_field_map()
		if not computed:
			return {
				"status": "no_computed",
				"spr_name": spr_name,
				"live_fields": live_map,
				"unit": _cstr(spr.get("custom_unit") or spr.get("unit") or ""),
				"jobs": len(spr.shaft_jobs or []),
				"items": len([it for it in (spr.items or []) if _spr_is_real_roll_item_row(it)]),
				"message": _(
					"Could not compute wastage — check Unit is Unit 1–4 and Available Jobs have GSM, meter/roll, and shafts."
				),
			}

		submitted = cint(spr.docstatus) == 1
		child_dt = "Running Patty Wastage Row"

		# Wipe existing patty rows (keep none — full force rewrite)
		for old in list(spr.get(patty_field) or []):
			try:
				frappe.delete_doc(old.doctype, old.name, force=1, ignore_permissions=True, delete_permanently=True)
			except Exception:
				frappe.db.sql(f"DELETE FROM `tab{old.doctype}` WHERE name=%s", (old.name,))
		spr.set(patty_field, [])

		written = []
		idx = 0
		for jid, logical in computed.items():
			flag = saved_flags.get(_cstr(jid), cint(logical.get("recycle_to_next") or 0))
			logical = _spr_apply_patty_recycle_net(dict(logical), flag)
			values = _spr_write_patty_child_row(logical)
			if not values:
				continue
			idx += 1
			values["idx"] = idx
			child = spr.append(patty_field, values)
			if submitted:
				child.db_insert()
			written.append(
				{
					"job_id": jid,
					"meter": flt(logical.get("meter_per_roll") or 0),
					"width": flt(logical.get("width_inch") or 0),
					"shafts": cint(logical.get("no_of_shafts") or 0),
					"wastage_qty": flt(logical.get("wastage_qty") or 0),
					"recycled_qty": flt(logical.get("recycled_qty") or 0),
					"net_wastage": flt(logical.get("net_wastage") or 0),
					"one_shaft": flt(logical.get("one_shaft_gross") or 0),
					"recycle_to_next": flag,
					"db_fields": values,
				}
			)

		# Recycled table
		recycled_count = 0
		if rec_field:
			for old in list(spr.get(rec_field) or []):
				jid = _cstr(getattr(old, "job_id", None) or "").lower()
				if jid == "patty":
					continue
				try:
					frappe.delete_doc(old.doctype, old.name, force=1, ignore_permissions=True, delete_permanently=True)
				except Exception:
					frappe.db.sql(f"DELETE FROM `tab{old.doctype}` WHERE name=%s", (old.name,))
			# keep only Patty stock rows
			keep = [
				r
				for r in (spr.get(rec_field) or [])
				if _cstr(getattr(r, "job_id", None) or "").lower() == "patty"
			]
			spr.set(rec_field, keep)
			# Reload patty into memory for sync after db_insert
			if submitted:
				spr = frappe.get_doc("Shaft Production Run", spr_name)
			recycled_count = _spr_sync_recycled_wastage_from_patty(spr)
			if submitted:
				for row in spr.get(rec_field) or []:
					if getattr(row, "name", None) and frappe.db.exists(row.doctype, row.name):
						continue
					if hasattr(row, "db_insert"):
						row.db_insert()
			elif not submitted:
				spr.flags._spr_incremental_roll_save = True
				spr.flags.ignore_version = True
				spr.save(ignore_permissions=True)
		elif not submitted:
			spr.flags._spr_incremental_roll_save = True
			spr.flags.ignore_version = True
			spr.save(ignore_permissions=True)

		frappe.db.commit()
		return {
			"status": "ok",
			"spr_name": spr_name,
			"patty_rows": len(written),
			"recycled_rows": recycled_count,
			"live_fields": live_map,
			"rows": written,
		}


@frappe.whitelist()
def save_gsm_roll_line_to_spr(spr_name, roll_payload, shift=None):
	"""Real-time GSM Save Row — upsert one roll line on draft SPR (server, not local only)."""
	spr_name = _cstr(spr_name).strip()
	if isinstance(roll_payload, str):
		try:
			roll_payload = json.loads(roll_payload)
		except Exception:
			frappe.throw(_("Invalid roll payload"))
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not roll_payload or not isinstance(roll_payload, dict):
		frappe.throw(_("Roll payload is required"))

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot save roll lines to a submitted Shaft Production Run"))
		payload_unit = _cstr((roll_payload or {}).get("custom_unit") or "").strip()
		payload_date = roll_payload.get("run_date") if isinstance(roll_payload, dict) else None
		if payload_unit or payload_date or shift:
			from production_entry.production_planning.unified_production_entry_api import (
				_apply_gsm_session_header_to_spr,
				_normalize_gsm_shift_label,
			)

			_apply_gsm_session_header_to_spr(
				spr,
				run_date=payload_date,
				shift=_normalize_gsm_shift_label(shift),
				unit=payload_unit or None,
			)
		pp_id = _cstr(spr.get("production_plan")).strip()
		is_mix = spr_doc_is_mix_roll(spr)
		if is_mix:
			pp_id = ""
			if not _cstr(roll_payload.get("job_id") or roll_payload.get("job")).strip():
				roll_payload["job_id"] = "1"
			roll_payload.pop("pp_id", None)
			roll_payload.pop("work_order", None)
		result = _gsm_upsert_roll_line_on_spr(spr, pp_id, roll_payload, shift=shift)
		spr._validate_no_duplicate_roll_batches()
		# Keep Running Patty / Recycled in sync with GSM compute (not zero desk leftovers)
		try:
			spr._spr_sync_patty_on_gsm_roll_save(submitting=False, incremental=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"GSM roll save patty sync:{spr_name}")
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		# Additive: push bay to Batch immediately on Save Row (Batch.custom_bay already exists)
		try:
			_gsm_sync_single_roll_bay_to_batch(spr, roll_payload)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"GSM bay→Batch sync:{spr_name}")
		result.update(
			{
				"status": "ok",
				"spr_name": spr_name,
				"total_items": len(spr.items or []),
				"modified": spr.modified,
			}
		)
		_gsm_publish_session_update(spr)
		return result


def _gsm_sync_single_roll_bay_to_batch(spr, roll_payload: dict) -> None:
	"""Set Batch.custom_bay from roll payload when both field and bay value exist."""
	if not frappe.get_meta("Batch").has_field("custom_bay"):
		return
	bay = _cstr((roll_payload or {}).get("custom_bay"))
	bn = _cstr((roll_payload or {}).get("batch_no"))
	if not bay or not bn:
		return
	ic = _cstr((roll_payload or {}).get("item_code"))
	batch_name = bn if frappe.db.exists("Batch", bn) else ""
	if not batch_name and ic:
		batch_name = frappe.db.get_value("Batch", {"batch_id": bn, "item": ic}, "name") or ""
	if not batch_name:
		batch_name = frappe.db.get_value("Batch", {"batch_id": bn}, "name") or ""
	if not batch_name or not frappe.db.exists("Batch", batch_name):
		return
	frappe.db.set_value("Batch", batch_name, "custom_bay", bay, update_modified=False)


@frappe.whitelist()
def delete_gsm_bundle_packaging_from_spr(spr_name, bundle_batch_no, child_roll_batches=None):
	"""GSM Remove bundle row — delete child rolls, bundle sticker, and any -B1 summary line."""
	spr_name = _cstr(spr_name).strip()
	bundle_batch_no = _cstr(bundle_batch_no).strip()
	if isinstance(child_roll_batches, str):
		try:
			child_roll_batches = json.loads(child_roll_batches)
		except Exception:
			child_roll_batches = []
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not bundle_batch_no and not child_roll_batches:
		frappe.throw(_("Bundle batch number or child roll batches are required"))

	batches_to_remove = {_cstr(b).strip() for b in (child_roll_batches or []) if _cstr(b).strip()}
	if bundle_batch_no:
		batches_to_remove.add(bundle_batch_no)

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot delete bundle packaging from a submitted Shaft Production Run"))

		removed_items = []
		for row in list(spr.items or []):
			bn = _cstr(getattr(row, "batch_no", "")).strip()
			if bn and bn in batches_to_remove:
				removed_items.append(bn)
				spr.remove(row)

		removed_stickers = []
		for row in list(spr.bundle_stickers or []):
			bn = _cstr(getattr(row, "batch_no", "")).strip()
			if bundle_batch_no and bn == bundle_batch_no:
				removed_stickers.append(bn)
				spr.remove(row)

		if not removed_items and not removed_stickers:
			return {
				"status": "not_found",
				"spr_name": spr_name,
				"bundle_batch_no": bundle_batch_no,
			}

		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		return {
			"status": "ok",
			"spr_name": spr_name,
			"bundle_batch_no": bundle_batch_no,
			"removed_item_batches": removed_items,
			"removed_sticker_batches": removed_stickers,
			"total_items": len(spr.items or []),
			"total_stickers": len(spr.bundle_stickers or []),
		}


@frappe.whitelist()
def delete_gsm_roll_line_from_spr(spr_name, batch_no=None, row_name=None):
	"""GSM Remove Row — delete one roll line from draft SPR (by batch_no or child row name)."""
	spr_name = _cstr(spr_name).strip()
	batch_no = _cstr(batch_no).strip()
	row_name = _cstr(row_name).strip()
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not batch_no and not row_name:
		frappe.throw(_("Batch number or SPR item row name is required"))

	with _spr_operation_lock(spr_name, "write", ttl_sec=120):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot delete roll lines from a submitted Shaft Production Run"))

		removed_batch = ""
		removed_row = ""
		for row in list(spr.items or []):
			match = False
			if batch_no and _cstr(getattr(row, "batch_no", "")).strip() == batch_no:
				match = True
			elif row_name and _cstr(getattr(row, "name", "")).strip() == row_name:
				match = True
			if match:
				removed_batch = _cstr(getattr(row, "batch_no", "")).strip()
				removed_row = _cstr(getattr(row, "name", "")).strip()
				spr.remove(row)
				break

		if not removed_batch and not removed_row:
			return {"status": "not_found", "spr_name": spr_name, "batch_no": batch_no, "row_name": row_name}

		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		_gsm_publish_session_update(spr)
		return {
			"status": "ok",
			"spr_name": spr_name,
			"batch_no": removed_batch,
			"row_name": removed_row,
			"total_items": len(spr.items or []),
		}


@frappe.whitelist()
def import_gsm_roll_lines_to_spr(spr_name, roll_payloads, shift=None):
	"""Bulk import GSM roll grid rows onto a draft SPR (batch_no dedup — safe to re-submit)."""
	spr_name = _cstr(spr_name).strip()
	if isinstance(roll_payloads, str):
		try:
			roll_payloads = json.loads(roll_payloads)
		except Exception:
			frappe.throw(_("Invalid roll payloads"))
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found"))
	if not roll_payloads:
		return {"status": "ok", "spr_name": spr_name, "added": 0, "updated": 0, "lines": []}

	with _spr_operation_lock(spr_name, "write", ttl_sec=180):
		spr = frappe.get_doc("Shaft Production Run", spr_name)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot import roll lines to a submitted Shaft Production Run"))
		pp_id = _cstr(spr.get("production_plan")).strip()
		lines = []
		added = updated = 0
		for payload in roll_payloads:
			if not isinstance(payload, dict):
				continue
			res = _gsm_upsert_roll_line_on_spr(spr, pp_id, payload, shift=shift)
			if res.get("action") == "skipped":
				lines.append(res)
				continue
			if res.get("action") == "updated":
				updated += 1
			else:
				added += 1
			lines.append(res)
		spr._validate_no_duplicate_roll_batches()
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)
		return {
			"status": "ok",
			"spr_name": spr_name,
			"added": added,
			"updated": updated,
			"total_items": len(spr.items or []),
			"lines": lines,
		}


@frappe.whitelist()
def append_roll_lines_for_job_and_save(
	shaft_production_run,
	job_id,
	lamination_rolls_per_combination=None,
	lamination_exact_roll_lines=None,
	exact_roll_lines=None,
	roll_start_index=None,
	replace_job_lines=0,
):
	"""Build roll lines on the server, assign batches, save once — avoids slow client grid append/save."""
	if not job_id:
		frappe.throw(_("Job ID is required"))
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		frappe.throw(_("Save Shaft Production Run first"))

	with _spr_operation_lock(shaft_production_run, "write", ttl_sec=120):
		lines = build_spr_roll_result_lines_for_job(
			shaft_production_run=shaft_production_run,
			job_id=job_id,
			lamination_rolls_per_combination=lamination_rolls_per_combination,
			lamination_exact_roll_lines=lamination_exact_roll_lines,
			exact_roll_lines=exact_roll_lines,
			roll_start_index=roll_start_index,
		)
		if not lines:
			return {"added": 0, "job_id": _cstr(job_id), "total_items": 0, "lines": []}

		spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot add roll lines to a submitted Shaft Production Run"))

		if cint(replace_job_lines):
			for row in list(spr.items or []):
				if _cstr(getattr(row, "job", None)) == _cstr(job_id):
					spr.remove(row)

		client_max_roll = _spr_max_roll_suffix_for_job(spr, job_id)
		if roll_start_index not in (None, ""):
			try:
				client_max_roll = max(client_max_roll, cint(roll_start_index))
			except Exception:
				pass

		batch_rows = _get_next_spr_batch_numbers_unlocked(
			shaft_production_run=shaft_production_run,
			count=len(lines),
			client_max_roll=client_max_roll,
			run_date=spr.run_date,
			custom_unit=spr.get("custom_unit"),
			shift=spr.shift,
			client_series_prefix=_spr_existing_series_prefix_for_job(spr, job_id) or None,
		)

		added_rows: list[dict] = []
		for idx, line in enumerate(lines):
			row = spr.append("items", line)
			if idx < len(batch_rows or []):
				br = batch_rows[idx] or {}
				if br.get("batch_no"):
					row.batch_no = br.get("batch_no")
				if br.get("roll_no") is not None:
					row.roll_no = br.get("roll_no")
			added_rows.append(
				{
					"batch_no": _cstr(getattr(row, "batch_no", "")),
					"roll_no": getattr(row, "roll_no", None),
					"job": _cstr(getattr(row, "job", None)),
				}
			)

		spr._validate_no_duplicate_roll_batches()
		spr.flags._spr_incremental_roll_save = True
		spr.save(ignore_permissions=True)

		return {
			"added": len(added_rows),
			"job_id": _cstr(job_id),
			"total_items": len(spr.items or []),
			"lines": added_rows,
		}


def _build_gsm_width_to_wo_map_from_item_names(wo_list: list) -> dict:
	"""
	Γ£à Extract GSM and WIDTH from Item Code (the source of truth).
	Item Code format: "1001050010251600"
	  - Positions [9:12] = GSM (e.g., "025" = 25)
	  - Positions [12:16] = WIDTH in MM (e.g., "1600" = 1600mm = 63 inches)
	
	Returns: {(25, 63.0): WO, (90, 63.0): WO, ...}
	Keeps FIRST occurrence of each (GSM, WIDTH) pair.
	"""
	gsm_width_to_wo = {}
	
	for wo in wo_list:
		try:
			production_item = frappe.db.get_value("Work Order", wo["name"], "production_item")
			if not production_item:
				frappe.logger().warning(f"[WO MAP] No production_item for WO {wo['name']}")
				continue
			
			# Γ£à Parse item code to get GSM and WIDTH (SOURCE OF TRUTH)
			gsm, width = parse_item_code(_cstr(production_item))
			
			if gsm > 0 and width > 0:
				key = (gsm, width)
				# Γ£à KEEP FIRST OCCURRENCE: Don't overwrite if key already exists
				if key not in gsm_width_to_wo:
					gsm_width_to_wo[key] = wo
					frappe.logger().info(f"[WO MAP] {wo['name']} ΓåÆ GSM {gsm} + WIDTH {width}\" (from item code: {production_item})")
				else:
					# Duplicate width found - log it but keep first WO
					frappe.logger().info(f"[WO MAP] Note: {wo['name']} also has GSM {gsm} + WIDTH {width}\", but keeping first WO {gsm_width_to_wo[key]['name']}")
			else:
				frappe.logger().warning(f"[WO MAP] Could not parse item code {production_item} (GSM={gsm}, WIDTH={width})")
		
		except Exception as e:
			frappe.logger().warning(f"[WO MAP ERROR] WO {wo.get('name')}: {str(e)}")
	
	return gsm_width_to_wo


@frappe.whitelist()
def get_job_rows_for_production_plan(production_plan):
	if not production_plan:
		return []
	if not frappe.db.exists("Production Plan", production_plan):
		frappe.throw(_("Production Plan {0} not found").format(production_plan))
	custom_sd = _build_shaft_jobs_from_custom_shaft_details(production_plan)
	if custom_sd is not None:
		return custom_sd
	detailed = _build_shaft_jobs_from_pp_details(production_plan)
	if detailed is not None:
		return detailed
	rows = frappe.db.sql(
		"""
		SELECT wo.production_plan_item AS job_no, SUM(wo.qty) AS total_weight
		FROM `tabWork Order` wo
		WHERE wo.production_plan = %(pp)s
		  AND wo.docstatus < 2
		  AND IFNULL(wo.production_plan_item, '') != ''
		GROUP BY wo.production_plan_item
		ORDER BY MIN(wo.creation)
		""",
		{"pp": production_plan},
		as_dict=True,
	)
	job_meta = frappe.get_meta("Shaft Production Run Job")
	out = []
	for i, r in enumerate(rows):
		row = {"job_id": r.job_no, "total_weight": flt(r.total_weight)}
		if job_meta.has_field("production_plan_item"):
			row["production_plan_item"] = r.job_no
		comb = None
		if job_meta.has_field("combination"):
			comb = get_shaft_combination(production_plan, r.job_no)
			if comb:
				row["combination"] = comb
		m = dict(row)
		job_gsm = None
		if m.get("gsm") is not None:
			try:
				job_gsm = int(flt(m.get("gsm")))
			except Exception:
				pass
		wos = _resolve_wos_for_pp_job_row(
			production_plan,
			ppi=m.get("production_plan_item"),
			job_id=_cstr(m.get("job_id")),
			row_index=i,
			combination=m.get("combination"),
			job_gsm=job_gsm,
		)
		if job_meta.has_field("work_orders") and wos:
			row["work_orders"] = ", ".join(w["name"] for w in wos)
		_fill_party_code_from_resolved_wos(row, job_meta, wos)
		out.append(row)
	return out


def _spr_job_rows(spr_doc):
	return getattr(spr_doc, "shaft_jobs", None) or getattr(spr_doc, "jobs", None) or []


def _spr_job_id(job):
	return getattr(job, "job_id", None) or getattr(job, "job_no", None)


def _spr_shaft_job_for_roll(spr_doc, job_id_str):
	pid = _cstr(job_id_str)
	if not pid:
		return None
	for sj in _spr_job_rows(spr_doc):
		if _spr_job_keys_match(_spr_job_id(sj), pid):
			return sj
		if _cstr(getattr(sj, "production_plan_item", None)) == pid:
			return sj
	return None


def _spr_numeric_str_ok_for_eq(s: str) -> bool:
	s = (s or "").strip()
	if not s:
		return False
	try:
		float(s)
		return True
	except ValueError:
		return False


def _spr_job_keys_match(a, b) -> bool:
	"""Match job ids across 1 / 1.0 / '1 ' and identical non-numeric strings."""
	na = _cstr(a)
	nb = _cstr(b)
	if na == nb:
		return True
	if not na or not nb:
		return False
	if _spr_numeric_str_ok_for_eq(na) and _spr_numeric_str_ok_for_eq(nb):
		return flt(na) == flt(nb)
	return False


def _spr_item_roll_matches_bundle_job(sj, it, job_id: str) -> bool:
	if _spr_job_keys_match(getattr(it, "job", None), job_id):
		return True
	if sj:
		jj = _cstr(getattr(it, "job", None))
		ppi = _cstr(getattr(sj, "production_plan_item", None))
		if ppi and jj == ppi:
			return True
	return False


def _spr_resolve_item_job_to_canonical_id(spr_doc, it) -> str:
	"""Map a roll line's job field to shaft_jobs job_id (handles numeric drift + PP item name)."""
	raw = _cstr(getattr(it, "job", None))
	if not raw:
		return ""
	for sj in _spr_job_rows(spr_doc):
		canon = _cstr(_spr_job_id(sj))
		if _spr_job_keys_match(raw, canon):
			return canon
		ppi = _cstr(getattr(sj, "production_plan_item", None))
		if ppi and raw == ppi:
			return canon
	return raw


def _spr_job_product_code(sj):
	"""Product item from Available Jobs: manual_items, else Work Order production_item."""
	if not sj:
		return ""
	mi = _cstr(getattr(sj, "manual_items", None) or "").strip()
	if mi:
		return mi
	wos = _cstr(getattr(sj, "work_orders", None) or "")
	for raw in wos.replace("\n", ",").split(","):
		wo = raw.strip()
		if wo and frappe.db.exists("Work Order", wo):
			pi = frappe.db.get_value("Work Order", wo, "production_item")
			if pi:
				return _cstr(pi)
	return ""


def _spr_bundle_job_label(sj):
	jid = _cstr(_spr_job_id(sj))
	comb = _cstr(getattr(sj, "combination", None) or "").strip()
	if comb:
		return f"Job {jid} — {comb}"
	prod = _spr_job_product_code(sj) or ""
	if prod:
		return f"Job {jid} — {prod}"
	return f"Job {jid}"


def _spr_bundle_segment_widths_for_job(spr_doc, sj) -> list[float]:
	"""Per-job width options for Bundle Packaging: combination segments / WO item widths / existing roll widths."""
	out: list[float] = []
	if not sj:
		return out
	comb = _cstr(getattr(sj, "combination", None) or "")
	for w in _parse_combination_widths_inches(comb):
		fw = flt(w)
		if fw > 0 and fw not in out:
			out.append(fw)
	for wo in _get_work_orders_for_spr_job(get_pp_from_spr(spr_doc.name), spr_doc, sj):
		wo_name = _cstr(wo.get("name"))
		if not wo_name:
			continue
		item_code = frappe.db.get_value("Work Order", wo_name, "production_item")
		_gsm, width_inch = parse_item_code(item_code)
		fw = flt(width_inch)
		if fw > 0 and fw not in out:
			out.append(fw)
	jid = _cstr(_spr_job_id(sj))
	for it in spr_doc.items or []:
		if not _spr_item_roll_matches_bundle_job(sj, it, jid):
			continue
		fw = flt(getattr(it, "width_inch", None))
		if fw > 0 and fw not in out:
			out.append(fw)
	if not out:
		tw = flt(getattr(sj, "total_width", None))
		if tw > 0:
			out.append(tw)
	return sorted(set(out))


def _spr_bundle_job_segments_detail(spr_doc, sj) -> list[dict]:
	"""Per combination segment: width, net kg/shaft, linked WO item — for Bundle packaging UI."""
	if not sj:
		return []
	pp_name = get_pp_from_spr(spr_doc.name)
	comb = getattr(sj, "combination", None)
	segs = max(1, _count_combination_segments(comb))
	widths = _parse_combination_widths_inches(comb) if comb else []
	weights = _segment_weights_kg(sj, segs)
	wos = _get_work_orders_for_spr_job(pp_name, spr_doc, sj)
	out: list[dict] = []
	for i in range(segs):
		w_seg = flt(widths[i]) if i < len(widths) else flt(getattr(sj, "total_width", None))
		if w_seg <= 0:
			continue
		nk = weights[i] if i < len(weights) else None
		item_code = ""
		item_name = ""
		matched_wo = ""
		for wo in wos:
			won = _cstr(wo.get("name"))
			if not won:
				continue
			ic = frappe.db.get_value("Work Order", won, "production_item")
			if not ic:
				continue
			_g, w_item = parse_item_code(ic)
			if abs(flt(w_item) - w_seg) <= 0.75:
				item_code = _cstr(ic)
				item_name = _cstr(frappe.db.get_value("Item", ic, "item_name") or "")
				matched_wo = won
				break
		out.append(
			{
				"width_inch": round(w_seg, 1),
				"net_kg_per_shaft": round(flt(nk), 3) if nk is not None else None,
				"item_code": item_code,
				"item_name": item_name,
				"work_order": matched_wo,
			}
		)
	return out


def _spr_bundle_wo_for_width(spr_doc, sj, width_inch) -> dict | None:
	"""Resolve Work Order dict ({name}) for a combination segment width."""
	wx = flt(width_inch)
	if wx <= 0 or not sj:
		return None
	for seg in _spr_bundle_job_segments_detail(spr_doc, sj):
		if abs(flt(seg.get("width_inch")) - wx) > 0.75:
			continue
		won = _cstr(seg.get("work_order"))
		if won:
			return {"name": won}
		ic = _cstr(seg.get("item_code"))
		if not ic:
			continue
		pp_name = get_pp_from_spr(spr_doc.name)
		for wo in _get_work_orders_for_spr_job(pp_name, spr_doc, sj):
			won2 = _cstr(wo.get("name"))
			if not won2:
				continue
			if _cstr(frappe.db.get_value("Work Order", won2, "production_item")) == ic:
				return wo
	pp_name = get_pp_from_spr(spr_doc.name)
	for wo in _get_work_orders_for_spr_job(pp_name, spr_doc, sj):
		won = _cstr(wo.get("name"))
		if not won:
			continue
		ic = frappe.db.get_value("Work Order", won, "production_item")
		if not ic:
			continue
		_g, w_item = parse_item_code(ic)
		if abs(flt(w_item) - wx) <= 0.75:
			return wo
	return None


def _spr_bundle_line_dicts_for_width_mix(spr_doc, sj, job_id, roll_gross_plan: list[dict]) -> list[dict]:
	"""One SPR item template per planned roll, with WO/item matching that roll's width."""
	pp_name = get_pp_from_spr(spr_doc.name)
	comb = getattr(sj, "combination", None) or ""
	out: list[dict] = []
	for plan in roll_gross_plan or []:
		w = flt(plan.get("width_inch"))
		wo = _spr_bundle_wo_for_width(spr_doc, sj, w)
		if not wo:
			frappe.throw(
				_("No Work Order / item for width {0}\". Fix Available Jobs WO mapping.").format(
					_bp_format_width_label_static(w) if w else "?"
				)
			)
		row = _spr_item_line_from_wo(pp_name, job_id, comb, 0.0, wo)
		row["width_inch"] = w
		row["job"] = job_id
		out.append(row)
	return out


def _spr_roll_effective_width_inch(it) -> float:
	"""Roll line width: prefer stored width_inch; else derive from item_code (handles total-width on row)."""
	rw = flt(getattr(it, "width_inch", None))
	if rw > 0.001:
		return rw
	ic = getattr(it, "item_code", None)
	if ic:
		_g, w = parse_item_code(ic)
		if flt(w) > 0.001:
			return flt(w)
	return 0.0


def _spr_roll_matches_bundle_width(it, width_inch: float, job_w: float) -> bool:
	"""Match roll to selected segment width; use item_code width when stored width is total or zero."""
	rw = _spr_roll_effective_width_inch(it)
	wx = flt(width_inch)
	jw = flt(job_w)
	tol = 0.75
	if rw > 0.001:
		return abs(rw - wx) <= tol
	if jw > 0.001 and wx > 0.001:
		return abs(jw - wx) <= tol
	return False


def _spr_item_line_from_wo(pp_name, job_id, shaft_combination, planned_qty, wo):
	wo_doc = frappe.get_doc("Work Order", wo["name"])
	item_code = wo_doc.production_item
	item_name = frappe.db.get_value("Item", item_code, "item_name") or ""
	_, width_inch = parse_item_code(item_code)
	specs = _spr_resolve_roll_line_specs_from_item_code(item_code, item_name)
	quality = specs.get("quality") or ""
	color = specs.get("color") or ""
	gsm = cint(specs.get("gsm") or 0)
	if flt(specs.get("width_inch") or 0) > 0:
		width_inch = flt(specs.get("width_inch"))
	spi_meta = frappe.get_meta("Shaft Production Run Item")
	row: dict = {
		"work_order": wo["name"],
		"item_code": item_code,
		"item_name": item_name,
		"quality": quality,
		"gsm": gsm,
		"planned_qty": planned_qty,
		"job": job_id,
		"batch_no": "",
		"party_code": get_order_code(wo_doc),
		"uom": _item_stock_uom_for_spr(item_code),
		"roll_no": 0,
		"meter_roll": 0,
		"net_weight": 0,
		"gross_weight": 0,
		"width_inch": width_inch,
		"color": color,
	}
	if _is_bag_bundle_fg_code(item_code):
		if spi_meta.has_field("custom_bag_size"):
			bag_sz = _cstr(specs.get("bag_size") or "").strip() or _spr_bag_size_from_item_code(item_code)
			if bag_sz:
				row["custom_bag_size"] = bag_sz
	elif _is_sheet_cutting_fg_code(item_code):
		if spi_meta.has_field("custom_sheet_size"):
			sz = _cstr(specs.get("sheet_size") or "").strip() or _spr_sheet_size_from_item_code(item_code)
			if sz:
				row["custom_sheet_size"] = sz
		if spi_meta.has_field("custom_planned_sheets_pcs") and planned_qty > 0:
			row["custom_planned_sheets_pcs"] = planned_qty
	# Fabric GSM (F-60 in item name) and Lamination GSM (L-15 GSM in item name or -C suffix)
	if spi_meta.has_field("custom_fabric_gsm"):
		fab_gsm = _fabric_gsm_from_item_name(item_name) or _fabric_gsm_from_item_name(item_code)
		if fab_gsm > 0:
			row["custom_fabric_gsm"] = fab_gsm
	if spi_meta.has_field("custom_lam_gsm"):
		lam_gsm = _lam_gsm_from_item(item_name, item_code)
		if lam_gsm > 0:
			row["custom_lam_gsm"] = lam_gsm
	if spi_meta.has_field("custom_bopp_gsm"):
		bopp_gsm = _bopp_gsm_from_item(item_code, item_name)
		if bopp_gsm > 0:
			row["custom_bopp_gsm"] = bopp_gsm
	return row


def _build_spr_items_from_pp(spr_doc, pp_name):
	items = []
	for job in _spr_job_rows(spr_doc):
		job_id = _spr_job_id(job)
		if not job_id:
			continue
		shaft_combination = get_shaft_combination(pp_name, job_id)
		if getattr(job, "combination", None) and not shaft_combination:
			shaft_combination = job.combination
		planned_qty = getattr(job, "total_weight", None) or 0
		for wo in _get_work_orders_for_spr_job(pp_name, spr_doc, job):
			items.append(_spr_item_line_from_wo(pp_name, job_id, shaft_combination, planned_qty, wo))
	return items


def _build_roll_items_from_spr(spr_doc, pp_name, job_id_filter=None):
	items = []
	for job in _spr_job_rows(spr_doc):
		job_id = _spr_job_id(job)
		if not job_id:
			continue
		if job_id_filter is not None and _cstr(job_id) != _cstr(job_id_filter):
			continue
		shaft_combination = get_shaft_combination(pp_name, job_id)
		if getattr(job, "combination", None) and not shaft_combination:
			shaft_combination = job.combination
		planned_qty = getattr(job, "total_weight", None) or 0
		for wo in _get_work_orders_for_spr_job(pp_name, spr_doc, job):
			wo_doc = frappe.get_doc("Work Order", wo["name"])
			item_code = wo_doc.production_item
			item_name = frappe.db.get_value("Item", item_code, "item_name")
			gsm, width_inch = parse_item_code(item_code)
			items.append(
				{
					"job_no": job_id,
					"shaft_combination": shaft_combination,
					"planned_qty": planned_qty,
					"wo_id": wo["name"],
					"item_code": item_code,
					"item_name": item_name,
					"gsm": gsm,
					"width_inches": width_inch,
					"order_code": get_order_code(wo_doc),
					"batch_no": "",
					"roll_no": "",
					"meter_per_roll": 0,
					"net_weight": 0,
					"gross_weight": 0,
				}
			)
	return items


@frappe.whitelist()
def get_item_rows_for_production_plan(production_plan):
	"""Build SPR Item rows from WOs for all jobs (API / legacy). Desk flow uses build_spr_roll_result_lines_for_job per job."""
	if not production_plan:
		return []
	jobs = get_job_rows_for_production_plan(production_plan)
	spr = frappe._dict(shaft_jobs=[])
	for j in jobs:
		spr.shaft_jobs.append(frappe._dict(job_id=j["job_id"], total_weight=j.get("total_weight")))
	return _build_spr_items_from_pp(spr, production_plan)


@frappe.whitelist()
def get_or_create_roll_entry(shaft_production_run):
	"""All jobs on SPR (legacy API). Prefer get_or_create_roll_entry_for_job from shaft_jobs row."""
	existing = frappe.db.get_value(
		"Roll Production Entry",
		{"shaft_production_run": shaft_production_run, "docstatus": ["!=", 2]},
		"name",
	)
	if existing:
		return {"existing": existing}
	pp_name = get_pp_from_spr(shaft_production_run)
	if not pp_name:
		frappe.throw(_("Could not find Production Plan linked to {0}").format(shaft_production_run))
	spr_doc = frappe.get_doc("Shaft Production Run", shaft_production_run)
	items = _build_roll_items_from_spr(spr_doc, pp_name)
	return {"production_plan": pp_name, "items": items}


@frappe.whitelist()
def get_or_create_roll_entry_for_job(shaft_production_run, job_id):
	"""Open/create Roll Production Entry for a single PP job (shaft + combination from shaft_jobs row)."""
	if not job_id:
		frappe.throw(_("Job ID is required"))
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		frappe.throw(_("Save Shaft Production Run first"))
	meta_rpe = frappe.get_meta("Roll Production Entry")
	filters = {
		"shaft_production_run": shaft_production_run,
		"docstatus": ["!=", 2],
	}
	if meta_rpe.has_field("job_id"):
		filters["job_id"] = _cstr(job_id)
	existing = frappe.db.get_value("Roll Production Entry", filters, "name")
	if existing:
		return {"existing": existing, "job_id": _cstr(job_id)}
	pp_name = get_pp_from_spr(shaft_production_run)
	if not pp_name:
		frappe.throw(_("Could not find Production Plan linked to {0}").format(shaft_production_run))
	spr_doc = frappe.get_doc("Shaft Production Run", shaft_production_run)
	items = _build_roll_items_from_spr(spr_doc, pp_name, job_id_filter=job_id)
	if not items:
		frappe.throw(
			_("No roll lines for job {0}. Check Work Orders for this Production Plan.").format(job_id)
		)
	return {
		"production_plan": pp_name,
		"items": items,
		"job_id": _cstr(job_id),
	}


def get_pp_from_spr(spr_name):
	pp_field = frappe.db.get_value("Shaft Production Run", spr_name, "production_plan")
	if pp_field:
		return pp_field
	if spr_name.startswith("SPR-"):
		return spr_name[4:]
	return None


def get_shaft_combination(pp_name, job_no):
	if frappe.db.exists("DocType", "Production Plan Shaft Detail"):
		v = frappe.db.get_value(
			"Production Plan Shaft Detail",
			{"parent": pp_name, "job_no": job_no},
			"shaft_combination",
		)
		if v:
			return v
	child_dt = _production_plan_custom_shaft_child_doctype()
	if child_dt:
		meta_child = frappe.get_meta(child_dt)
		if meta_child.has_field("s_no"):
			s_no_candidates = [job_no]
			try:
				s_no_candidates.append(cint(job_no))
			except Exception:
				pass
			for candidate in s_no_candidates:
				if candidate is None or candidate == "":
					continue
				v = frappe.db.get_value(child_dt, {"parent": pp_name, "s_no": candidate}, "combination")
				if v:
					return v
		for jf in ("job", "job_no", "job_id"):
			if not meta_child.has_field(jf):
				continue
			v = frappe.db.get_value(child_dt, {"parent": pp_name, jf: job_no}, "shaft_combination")
			if v:
				return v
			if meta_child.has_field("combination"):
				v = frappe.db.get_value(child_dt, {"parent": pp_name, jf: job_no}, "combination")
				if v:
					return v
	return ""


def _production_plan_item_row_names_ordered(pp_name: str) -> list[str]:
	"""Production Plan Item row `name` values in table order (matches WO.production_plan_item)."""
	if not pp_name or not frappe.db.exists("Production Plan", pp_name):
		return []
	if not frappe.db.exists("DocType", "Production Plan Item"):
		return []
	rows = frappe.db.sql(
		"""
		SELECT name FROM `tabProduction Plan Item`
		WHERE parent = %(p)s
		ORDER BY idx ASC, name ASC
		""",
		{"p": pp_name},
	)
	return [_cstr(r[0]) for r in rows if r[0]]


def _resolve_job_ref_to_production_plan_item(pp_name: str, job_ref: str) -> str | None:
	"""
	Map human job id (1, 2, ΓÇª from s_no) to the correct Production Plan Item row name.
	Work Orders link via production_plan_item = PPI row name, not the display job number.
	When the PP has only one line, every shaft job row maps to that line (same WO for multiple SPR jobs).
	"""
	names = _production_plan_item_row_names_ordered(pp_name)
	if not names:
		return None
	t = _cstr(job_ref).strip()
	if not t:
		return None
	if t in names:
		return t
	if len(names) == 1 and t.isdigit():
		return names[0]
	return None


def get_work_orders_for_job(pp_name, job_no):
	if not pp_name or job_no is None:
		return []
	jn = _cstr(job_no).strip()
	if not jn:
		return []
	wos = frappe.db.sql(
		"""
		SELECT wo.name, wo.production_item, wo.qty as planned_qty, wo.produced_qty, wo.status
		FROM `tabWork Order` wo
		WHERE wo.production_plan = %(pp_name)s
		  AND wo.production_plan_item = %(job_no)s
		  AND wo.docstatus != 2
		ORDER BY wo.name
		""",
		{"pp_name": pp_name, "job_no": jn},
		as_dict=True,
	)
	if wos:
		return wos
	pi = _resolve_job_ref_to_production_plan_item(pp_name, jn)
	if pi and pi != jn:
		return get_work_orders_for_job(pp_name, pi)
	return []


def parse_item_code(item_code):
	try:
		if len(item_code) >= 16:
			gsm = int(item_code[9:12])
			width_inch = _spr_nominal_roll_width_inch(item_code)
			if width_inch > 0:
				return gsm, width_inch
	except Exception:
		pass
	return 0, 0


def _spr_nominal_roll_width_inch(item_code, item_name=None) -> float:
	"""Roll width in inches: prefer item-name W - X.X, else 4-digit mm tail rounded to nearest 0.5\"."""
	ic = _cstr(item_code).strip()
	if not ic:
		return 0.0
	inm = _cstr(item_name).strip() if item_name else _cstr(frappe.db.get_value("Item", ic, "item_name") or "")
	try:
		from production_entry.production_planning.scheduler_api import _parse_gsm_width_from_item_text

		_, w_name = _parse_gsm_width_from_item_text(f"{ic} {inm}")
		if w_name > 0:
			rw = round(flt(w_name), 1)
			return float(int(rw)) if abs(rw - round(rw)) < 1e-9 else rw
	except Exception:
		pass
	try:
		if len(ic) >= 16 and ic[12:16].isdigit():
			width_mm = int(ic[12:16])
			if width_mm > 0:
				return round(round(width_mm / 25.4 * 2) / 2, 1)
	except Exception:
		pass
	return 0.0


def _item_stock_uom_for_spr(item_code: str) -> str:
	"""Resolve a valid UOM Link for Shaft Production Run Item (prefer Item.stock_uom, usually Kg)."""
	if not item_code:
		return "Kg"
	u = frappe.db.get_value("Item", item_code, "stock_uom")
	u = (u or "").strip()
	if u and frappe.db.exists("UOM", u):
		return u
	for cand in ("Kg", "kg", "Kgs", "KG"):
		if frappe.db.exists("UOM", cand):
			return cand
	return u or "Kg"


# Manual job + bundle packaging (Actions on Shaft Production Run)
SPR_MANUAL_SOURCE_WH = "Raw Materials - JSB-1ZT"
SPR_MANUAL_FG_WH = "Finished Goods - JSB-1ZT"


def _spr_require_saved(spr_name: str):
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Save the Shaft Production Run first"))


def _spr_pp_and_company(spr_name: str):
	pp_name = get_pp_from_spr(spr_name)
	if not pp_name:
		frappe.throw(_("Set Production Plan on this Shaft Production Run"))
	doc = frappe.get_doc("Shaft Production Run", spr_name)
	company = doc.get("company") or frappe.db.get_value("Production Plan", pp_name, "company")
	if not company:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
	if not company:
		frappe.throw(_("Company could not be resolved"))
	return pp_name, company, doc


def _spr_warehouses_exist(spr_doc=None):
	unit = ""
	if spr_doc:
		unit = _cstr(getattr(spr_doc, "custom_unit", None) or getattr(spr_doc, "unit", None))
	if unit:
		from production_entry.production_planning.spr_unit_warehouses import (
			resolve_spr_unit_manufacturing_warehouses,
		)

		resolve_spr_unit_manufacturing_warehouses(unit)
		return
	for wh in (SPR_MANUAL_SOURCE_WH, SPR_MANUAL_FG_WH):
		if not frappe.db.exists("Warehouse", wh):
			frappe.throw(_("Warehouse {0} not found. Create it or update SPR_MANUAL_* constants.").format(wh))


def _spr_work_orders_linked_to_spr(spr_doc) -> set[str]:
	linked: set[str] = set()
	if not spr_doc:
		return linked
	for j in _spr_job_rows(spr_doc):
		wos = _cstr(getattr(j, "work_orders", None) or "")
		for part in wos.replace("\n", ",").split(","):
			p = part.strip()
			if p:
				linked.add(p)
	return linked


def _spr_is_manual_shaft_job(sj) -> bool:
	return cint(getattr(sj, "is_manual", 0) or 0) == 1


def _spr_net_kg_per_shaft_for_pp_line_width(
	spr_doc,
	width_inch: float,
	production_plan_item: str | None,
	job_id: str | None = None,
	gsm=None,
) -> tuple[float | None, str | None]:
	"""
	Match item width (inch) to a segment in Available Jobs (non-manual): kg per shaft for that segment.
	Uses combination widths + net_weight split, or total_width for single-segment jobs.

	When multiple jobs share the same width (e.g. Job1 20GSM 63" → 32kg, Job2 30GSM 63" → 24kg),
	prefer ``job_id`` then ``gsm`` so planned qty maps to the selected job — not the first width hit.
	Within one job, 33+35 with 32+50 maps each width to its segment weight.
	"""
	wx = flt(width_inch)
	if wx <= 0:
		return None, None
	ppi = _cstr(production_plan_item) if production_plan_item else ""
	jid_want = _cstr(job_id) if job_id else ""
	target_gsm = cint(gsm) if gsm is not None and _cstr(gsm) != "" else 0
	rows = list(_spr_job_rows(spr_doc))
	non_manual = [sj for sj in rows if not _spr_is_manual_shaft_job(sj)]

	candidates = non_manual
	if jid_want:
		by_job = [sj for sj in non_manual if _spr_job_keys_match(_spr_job_id(sj), jid_want)]
		if by_job:
			candidates = by_job
	elif ppi:
		by_ppi = [
			sj
			for sj in non_manual
			if _cstr(getattr(sj, "production_plan_item", None)) == ppi
		]
		if by_ppi:
			candidates = by_ppi

	if target_gsm:
		by_gsm = [sj for sj in candidates if cint(getattr(sj, "gsm", None) or 0) == target_gsm]
		if by_gsm:
			candidates = by_gsm

	def _match_width_on_job(sj):
		comb = getattr(sj, "combination", None)
		segs = max(1, _count_combination_segments(comb))
		widths = _parse_combination_widths_inches(comb) if comb else []
		weights = _segment_weights_kg(sj, segs)
		jid = _cstr(_spr_job_id(sj))
		if segs > 1 and len(widths) >= segs:
			for i in range(segs):
				if abs(flt(widths[i]) - wx) <= 0.5:
					if i < len(weights) and flt(weights[i]) > 0:
						return flt(weights[i]), jid
			return None
		tw = flt(getattr(sj, "total_width", None))
		if tw > 0 and abs(tw - wx) <= 0.5 and weights:
			return flt(weights[0]), jid
		# Single-width job with blank combination — match shaft width / first segment
		sw = flt(getattr(sj, "width_inch", None) or getattr(sj, "width", None) or 0)
		if sw <= 0 and widths:
			sw = flt(widths[0])
		if (sw <= 0 or abs(sw - wx) <= 0.5) and weights and flt(weights[0]) > 0:
			return flt(weights[0]), jid
		return None

	for sj in candidates:
		hit = _match_width_on_job(sj)
		if hit:
			return hit
	# Last resort: any non-manual job matching width (legacy callers without job_id/gsm)
	if jid_want or target_gsm:
		for sj in non_manual:
			hit = _match_width_on_job(sj)
			if hit:
				return hit
	return None, None


def _spr_try_submit_manual_work_order(wo_name: str):
	"""Submit Work Order when possible so manufacturing can start (best-effort)."""
	try:
		wo = frappe.get_doc("Work Order", wo_name)
		if wo.docstatus == 0:
			wo.submit()
	except Exception:
		frappe.log_error(
			title=f"SPR manual job: could not submit Work Order {wo_name}",
			message=frappe.get_traceback(),
		)


def _spr_manual_wo_has_submitted_stock_entries(wo_name: str) -> bool:
	"""True when this WO already has submitted Stock Entries (manufacture/transfer)."""
	if not wo_name:
		return False
	cnt = frappe.db.sql(
		"""
		SELECT COUNT(*)
		FROM `tabStock Entry` se
		WHERE se.work_order = %(wo)s
		  AND se.docstatus = 1
		  AND IFNULL(se.purpose, '') IN (
			'Manufacture',
			'Material Transfer for Manufacture',
			'Material Consumption for Manufacture'
		  )
		""",
		{"wo": wo_name},
	)
	try:
		return cint((cnt or [[0]])[0][0]) > 0
	except Exception:
		return False


def _spr_find_reusable_manual_work_order(
	pp_name: str,
	item_code: str,
	production_plan_item: str,
	spr_doc=None,
) -> str | None:
	"""
	Find reusable open WO for this PP+item so manual job flow prefers reusing over creating duplicates.
	Priority:
	1) WOs tagged by SPR manual description for the same PP line
	2) Any open WO for same PP+item (fallback)
	"""
	if not pp_name or not item_code:
		return None
	rows = (
		frappe.get_all(
			"Work Order",
			filters={
				"production_plan": pp_name,
				"production_item": item_code,
				"docstatus": ["<", 2],
				"status": ["not in", ["Completed", "Stopped", "Cancelled"]],
			},
			fields=["name", "description", "produced_qty", "creation", "modified"],
			order_by="modified desc",
		)
		or []
	)
	ppi_tag = _cstr(production_plan_item)
	linked = _spr_work_orders_linked_to_spr(spr_doc) if spr_doc else set()
	fallback: list[str] = []
	for r in rows:
		wo_name = _cstr(r.get("name"))
		if not wo_name or wo_name in linked:
			continue
		desc = _cstr(r.get("description"))
		# Prefer explicit SPR-manual WO with matching PP-line tag when available.
		if "SPR manual job" in desc:
			if ppi_tag and ppi_tag in desc:
				return wo_name
			fallback.append(wo_name)
			continue
		# Generic fallback: still allow reusing open WO for same PP+item to avoid duplicate WO creation.
		fallback.append(wo_name)
	if fallback:
		return fallback[0]
	return None


def _spr_list_reusable_manual_work_orders(pp_name: str, item_code: str, production_plan_item: str) -> list[str]:
	"""All reusable open WOs (newest first) for a PP+item, manual-tagged first."""
	if not pp_name or not item_code:
		return []
	rows = (
		frappe.get_all(
			"Work Order",
			filters={
				"production_plan": pp_name,
				"production_item": item_code,
				"docstatus": ["<", 2],
				"status": ["not in", ["Completed", "Stopped", "Cancelled"]],
			},
			fields=["name", "description", "produced_qty", "modified"],
			order_by="modified desc",
		)
		or []
	)
	ppi_tag = _cstr(production_plan_item)
	manual_pref: list[str] = []
	fallback: list[str] = []
	for r in rows:
		wo_name = _cstr(r.get("name"))
		if not wo_name:
			continue
		desc = _cstr(r.get("description"))
		if "SPR manual job" in desc:
			if ppi_tag and ppi_tag in desc:
				manual_pref.append(wo_name)
			else:
				fallback.append(wo_name)
		else:
			fallback.append(wo_name)
	seen = set()
	out: list[str] = []
	for arr in (manual_pref, fallback):
		for wo in arr:
			if wo in seen:
				continue
			seen.add(wo)
			out.append(wo)
	return out


def _spr_resolve_manual_job_work_order(
	pp,
	company: str,
	pp_name: str,
	item_code: str,
	production_plan_item: str,
	ppi_row,
	qty: float,
	selected_reuse_work_order,
	spr_doc,
) -> tuple[str, bool]:
	"""Return (wo_name, reused). __NEW__ forces insert; blank auto-reuses; name reuses if candidate."""
	selected = _cstr(selected_reuse_work_order)
	reused = False
	wo_name = ""
	if selected and selected != "__NEW__":
		candidates = _spr_list_reusable_manual_work_orders(pp_name, item_code, production_plan_item)
		if selected in candidates:
			wo_name = selected
			reused = True
	if not wo_name and selected != "__NEW__":
		wo_name = _spr_find_reusable_manual_work_order(pp_name, item_code, production_plan_item, spr_doc=spr_doc) or ""
		if wo_name:
			reused = True
	if not wo_name:
		wo_name = _spr_insert_manual_work_order(
			pp, company, item_code, production_plan_item, ppi_row, qty, spr_doc=spr_doc
		)
	return wo_name, reused


def _spr_insert_manual_work_order(
	pp,
	company: str,
	item_code: str,
	production_plan_item: str,
	ppi_row,
	qty: float,
	spr_doc=None,
) -> str:
	"""Insert a new Work Order for manual job flow."""
	from production_entry.production_planning.doctype.planning_sheet.planning_sheet import (
		get_default_bom_for_item,
	)

	source_wh = SPR_MANUAL_SOURCE_WH
	fg_wh = SPR_MANUAL_FG_WH
	wip_wh = ""
	unit = _cstr(getattr(spr_doc, "custom_unit", None) or getattr(spr_doc, "unit", None)) if spr_doc else ""
	if unit:
		from production_entry.production_planning.spr_unit_warehouses import (
			resolve_spr_unit_manufacturing_warehouses,
		)

		wh_ctx = resolve_spr_unit_manufacturing_warehouses(unit)
		source_wh = _cstr(wh_ctx.get("source_warehouse"))
		fg_wh = _cstr(wh_ctx.get("fg_warehouse"))
		wip_wh = _cstr(wh_ctx.get("wip_warehouse"))
		if wh_ctx.get("company"):
			company = _cstr(wh_ctx["company"])

	bom = get_default_bom_for_item(item_code, company)
	if not bom:
		frappe.throw(_("No active BOM for item {0}").format(item_code))
	pp_name = pp.name
	wo = frappe.new_doc("Work Order")
	wo.production_item = item_code
	wo.bom_no = bom
	wo.qty = flt(qty)
	wo.company = company
	wo.production_plan = pp_name
	# Leave production_plan_item unset on insert so site "one WO per PP line" Server Scripts
	# do not block additional SPR manual Work Orders. Production Plan link stays for traceability.
	wo.production_plan_item = None
	meta_wo = frappe.get_meta("Work Order")
	if meta_wo.has_field("description"):
		wo.description = _("SPR manual job — PP line {0} ┬╖ Item {1}").format(production_plan_item, item_code)
	if pp.get("sales_order"):
		wo.sales_order = pp.sales_order
	if frappe.get_meta("Work Order").has_field("sales_order_item"):
		wo.sales_order_item = getattr(ppi_row, "sales_order_item", None) or None
	wo.source_warehouse = source_wh
	wo.fg_warehouse = fg_wh
	if meta_wo.has_field("wip_warehouse"):
		if wip_wh:
			wo.wip_warehouse = wip_wh
		else:
			wip = frappe.db.get_value("Stock Settings", None, "default_wip_warehouse")
			if wip:
				wo.wip_warehouse = wip
	frappe.flags.spr_manual_work_order_insert = True
	try:
		wo.insert(ignore_permissions=True)
	finally:
		frappe.flags.spr_manual_work_order_insert = False
	wo_name = wo.name
	_spr_set_wo_required_item_source_warehouses(wo_name, source_wh)
	try:
		wo.reload()
		wo.add_comment("Comment", _("SPR manual — Production Plan line {0}").format(production_plan_item))
	except Exception:
		pass
	_spr_try_submit_manual_work_order(wo_name)
	return wo_name


@frappe.whitelist()
def spr_get_manual_job_catalog(shaft_production_run):
	"""Production Plan po_items + width + existing net on SPR by item_code (reuse hint)."""
	_spr_require_saved(shaft_production_run)
	pp_name, company, spr = _spr_pp_and_company(shaft_production_run)
	pp = frappe.get_doc("Production Plan", pp_name)
	pp_order_code = _cstr(
		pp.get("custom_party_code")
		or pp.get("party_code")
		or pp.get("custom_order_code")
		or pp.get("order_code")
	)
	out = []
	net_by_item: dict[str, float] = {}
	for it in spr.items or []:
		ic = _cstr(getattr(it, "item_code", None))
		if not ic:
			continue
		net_by_item[ic] = net_by_item.get(ic, 0.0) + flt(getattr(it, "net_weight", None))
	for row in pp.get("po_items") or []:
		ic = _cstr(getattr(row, "item_code", None))
		if not ic:
			continue
		item_name = frappe.db.get_value("Item", ic, "item_name")
		gsm, width_inch = parse_item_code(ic)
		if flt(width_inch) <= 0:
			width_inch = _spr_nominal_roll_width_inch(ic, item_name)
		first_seg_kg = None
		for sj in _spr_job_rows(spr):
			if _cstr(getattr(sj, "production_plan_item", None)) == _cstr(row.name):
				segs = max(1, _count_combination_segments(getattr(sj, "combination", None)))
				segw = _segment_weights_kg(sj, segs)
				if segw:
					first_seg_kg = round(flt(segw[0]), 3)
				break
		if first_seg_kg is None:
			for sj in _spr_job_rows(spr):
				if _spr_job_product_code(sj) != ic:
					continue
				segs = max(1, _count_combination_segments(getattr(sj, "combination", None)))
				segw = _segment_weights_kg(sj, segs)
				if segw:
					first_seg_kg = round(flt(segw[0]), 3)
				break
		net_ps, mj = _spr_net_kg_per_shaft_for_pp_line_width(spr, width_inch, row.name)
		row_order_code = _cstr(
			getattr(row, "custom_party_code", None)
			or getattr(row, "party_code", None)
			or getattr(row, "custom_order_code", None)
			or getattr(row, "order_code", None)
			or pp_order_code
		)
		out.append(
			{
				"item_code": ic,
				"item_name": item_name or ic,
				"production_plan_item": row.name,
				"planned_qty": flt(getattr(row, "planned_qty", None)),
				"gsm": gsm,
				"width_inch": width_inch,
				"existing_net_weight_kg": round(net_by_item.get(ic, 0.0), 2),
				"first_segment_planned_kg": first_seg_kg,
				"net_per_shaft_kg": round(net_ps, 3) if net_ps is not None else None,
				"matched_job_id": mj,
				"order_code": row_order_code,
				"reusable_work_orders": _spr_list_reusable_manual_work_orders(pp_name, ic, row.name),
			}
		)
	unit = normalize_planning_unit_for_select(_cstr(getattr(spr, "custom_unit", None)))
	return {
		"production_plan": pp_name,
		"company": company,
		"custom_unit": unit,
		"max_shaft_inches": get_mix_roll_unit_max_shaft_inches(unit),
		"lines": out,
	}


def _format_shaft_combination_inches(width_inch) -> str:
	"""Combination column text: width in inches (e.g. 78\"). Not item color — used for manual jobs."""
	w = flt(width_inch)
	if w <= 0:
		return ""
	s = str(int(w)) if w == int(w) else str(w)
	return f'{s}"'


def _format_combination_from_input(combo_raw: str) -> str:
	"""Preserve every segment from user input (e.g. 2+2+6 → 2\" + 2\" + 6\")."""
	widths = _parse_combination_widths_inches(combo_raw)
	if not widths:
		return _cstr(combo_raw).strip()

	def _fmt_w(w):
		w = flt(w)
		return str(int(w)) if w == int(w) else str(w)

	return " + ".join([f'{_fmt_w(w)}"' for w in widths])


def _net_weight_string_for_combination_segments(combo_raw: str, net_by_width: dict) -> str:
	"""Build job net_weight display: one kg value per combination segment."""
	widths = _parse_combination_widths_inches(combo_raw)
	if not widths:
		return ""
	parts: list[str] = []
	for w in widths:
		key = round(flt(w), 4)
		val = net_by_width.get(key)
		if val is None:
			for nk, nv in net_by_width.items():
				if abs(flt(nk) - flt(w)) < 0.06:
					val = nv
					break
		if val is not None and flt(val) > 0:
			parts.append(f"{flt(val):.2f}")
		else:
			parts.append("0")
	return " + ".join(parts) if parts else ""


@frappe.whitelist()
def spr_create_manual_job(
	shaft_production_run,
	item_code,
	production_plan_item,
	no_of_shafts,
	wo_qty=None,
	width_inch=None,
):
	"""Create draft Work Order + append manual Shaft Production Run Job row."""
	item_code = _cstr(item_code)
	production_plan_item = _cstr(production_plan_item)
	selected_reuse_work_order = _cstr(frappe.form_dict.get("selected_reuse_work_order"))
	no_of_shafts = cint(no_of_shafts)
	if no_of_shafts < 1:
		frappe.throw(_("Number of shafts must be at least 1"))
	if not item_code or not production_plan_item:
		frappe.throw(_("Item and Production Plan line are required"))

	_spr_require_saved(shaft_production_run)
	pp_name, company, spr = _spr_pp_and_company(shaft_production_run)
	_spr_warehouses_exist(spr)

	pp = frappe.get_doc("Production Plan", pp_name)
	ppi_row = None
	for r in pp.get("po_items") or []:
		if _cstr(r.name) == production_plan_item and _cstr(r.item_code) == item_code:
			ppi_row = r
			break
	if not ppi_row:
		frappe.throw(_("Production Plan item line not found for this item"))

	qty = flt(wo_qty) if wo_qty is not None and str(wo_qty).strip() != "" else None
	if qty is None or qty <= 0:
		qty = flt(getattr(ppi_row, "planned_qty", None) or 0) or 1.0

	wo_name, reused = _spr_resolve_manual_job_work_order(
		pp,
		company,
		pp_name,
		item_code,
		production_plan_item,
		ppi_row,
		qty,
		selected_reuse_work_order,
		spr,
	)

	spr.reload()
	for j in _spr_job_rows(spr):
		wos = _cstr(getattr(j, "work_orders", None) or "")
		for part in wos.replace("\n", ",").split(","):
			if part.strip() == wo_name:
				frappe.throw(
					_("Work Order {0} is already linked to this Shaft Production Run (Job {1}).").format(
						wo_name,
						_spr_job_id(j),
					)
				)

	job_id = f"MAN-{frappe.generate_hash(length=6).upper()}"
	for _attempt in range(20):
		if not any(_cstr(_spr_job_id(j)) == job_id for j in _spr_job_rows(spr)):
			break
		job_id = f"MAN-{frappe.generate_hash(length=6).upper()}"

	gsm, parsed_width = parse_item_code(item_code)
	w_override = flt(width_inch) if width_inch is not None and str(width_inch).strip() != "" else flt(
		frappe.form_dict.get("width_inch")
	)
	if w_override > 0:
		width_inch = w_override
	elif flt(parsed_width) > 0:
		width_inch = flt(parsed_width)
	else:
		item_name_tmp = frappe.db.get_value("Item", item_code, "item_name")
		width_inch = _spr_nominal_roll_width_inch(item_code, item_name_tmp)
	item_name = frappe.db.get_value("Item", item_code, "item_name")
	quality, color = extract_quality_and_color(item_name or "", item_code=item_code)
	order_code = ""
	try:
		wo_doc = frappe.get_doc("Work Order", wo_name)
		order_code = _cstr(get_order_code(wo_doc))
	except Exception:
		order_code = ""

	row = {
		"job_id": job_id,
		"production_plan_item": production_plan_item,
		"is_manual": 1,
		"no_of_shafts": no_of_shafts,
		"work_orders": wo_name,
		"total_weight": qty,
	}
	meta = frappe.get_meta("Shaft Production Run Job")
	if meta.has_field("gsm") and gsm:
		try:
			row["gsm"] = int(gsm)
		except Exception:
			row["gsm"] = gsm
	if meta.has_field("quality") and quality:
		row["quality"] = quality
	if meta.has_field("combination"):
		cb = _format_shaft_combination_inches(width_inch)
		if cb:
			row["combination"] = cb
	if meta.has_field("total_width"):
		row["total_width"] = width_inch
	if meta.has_field("manual_items"):
		row["manual_items"] = item_code
	if meta.has_field("party_code") and order_code:
		row["party_code"] = order_code

	spr.reload()
	spr.append("shaft_jobs", row)
	spr.save(ignore_permissions=True)

	return {
		"work_order": wo_name,
		"job_id": job_id,
		"shaft_production_run": spr.name,
		"reused_work_order": wo_name if reused else "",
	}


@frappe.whitelist()
def spr_create_manual_jobs_multi(
	shaft_production_run, no_of_shafts, items, no_of_rolls=None, combination_input=None
):
	"""
	Create one new Work Order per selected Production Plan line; one manual Available Jobs row.
	items: list of { item_code, production_plan_item, wo_qty, meter_roll }.
	wo_qty is manufacturing Kg = net per roll × rolls_per_shaft × shafts (from the dialog).
	"""
	no_of_shafts = cint(no_of_shafts)
	if no_of_shafts < 1:
		frappe.throw(_("Number of shafts must be at least 1"))
	no_of_rolls = cint(no_of_rolls) if no_of_rolls is not None else 1
	if no_of_rolls < 1:
		no_of_rolls = 1
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items or not isinstance(items, list):
		frappe.throw(_("Select at least one Production Plan line"))

	_spr_require_saved(shaft_production_run)
	pp_name, company, spr = _spr_pp_and_company(shaft_production_run)
	_spr_warehouses_exist(spr)
	pp = frappe.get_doc("Production Plan", pp_name)
	combo_raw = _cstr(combination_input).strip()
	if combo_raw:
		unit = normalize_planning_unit_for_select(_cstr(getattr(spr, "custom_unit", None)))
		validate_mix_shaft_width(unit, combo_raw)

	wo_names: list[str] = []
	reused_wo_names: list[str] = []
	qtys: list[float] = []
	widths_list: list[float] = []
	item_codes_list: list[str] = []
	ppi_rows = []
	meter_roll_from_popup: float | None = None
	net_by_width: dict[float, float] = {}

	for raw in items:
		if not isinstance(raw, dict):
			frappe.throw(_("Invalid line payload"))
		item_code = _cstr(raw.get("item_code"))
		production_plan_item = _cstr(raw.get("production_plan_item"))
		selected_reuse_work_order = _cstr(raw.get("selected_reuse_work_order"))
		qty = flt(raw.get("wo_qty"))
		if not item_code or not production_plan_item or qty <= 0:
			frappe.throw(_("Each line needs item, Production Plan row, and Work Order qty greater than zero"))
		ppi_row = None
		for r in pp.get("po_items") or []:
			if _cstr(r.name) == production_plan_item and _cstr(r.item_code) == item_code:
				ppi_row = r
				break
		if not ppi_row:
			frappe.throw(_("Production Plan item line not found for {0}").format(item_code))
		wo_name, reused = _spr_resolve_manual_job_work_order(
			pp,
			company,
			pp_name,
			item_code,
			production_plan_item,
			ppi_row,
			qty,
			selected_reuse_work_order,
			spr,
		)
		if reused:
			reused_wo_names.append(wo_name)
		spr.reload()
		for j in _spr_job_rows(spr):
			wos = _cstr(getattr(j, "work_orders", None) or "")
			for part in wos.replace("\n", ",").split(","):
				if part.strip() == wo_name:
					frappe.throw(
						_("Work Order {0} is already linked to this Shaft Production Run (Job {1}).").format(
							wo_name,
							_spr_job_id(j),
						)
					)
		wo_names.append(wo_name)
		qtys.append(qty)
		item_codes_list.append(item_code)
		ppi_rows.append(ppi_row)
		w_override = flt(raw.get("width_inch"))
		if w_override > 0:
			widths_list.append(w_override)
		else:
			_gsm, w_in = parse_item_code(item_code)
			if flt(w_in) <= 0:
				inm = frappe.db.get_value("Item", item_code, "item_name")
				w_in = _spr_nominal_roll_width_inch(item_code, inm)
			widths_list.append(flt(w_in))
		w_key = round(flt(widths_list[-1]), 4)
		if w_key > 0 and w_key not in net_by_width:
			nps, _mj = _spr_net_kg_per_shaft_for_pp_line_width(spr, w_key, production_plan_item)
			if nps is not None and flt(nps) > 0:
				net_by_width[w_key] = flt(nps)
		if meter_roll_from_popup is None and raw.get("meter_roll") not in (None, ""):
			mr = flt(raw.get("meter_roll"))
			if mr > 0:
				meter_roll_from_popup = mr

	job_id = f"MAN-{frappe.generate_hash(length=6).upper()}"
	for _attempt in range(20):
		if not any(_cstr(_spr_job_id(j)) == job_id for j in _spr_job_rows(spr)):
			break
		job_id = f"MAN-{frappe.generate_hash(length=6).upper()}"

	first_ic = item_codes_list[0]
	gsm, width_inch_one = parse_item_code(first_ic)
	item_name = frappe.db.get_value("Item", first_ic, "item_name")
	quality, color = extract_quality_and_color(item_name or "", item_code=first_ic)
	first_order_code = ""
	try:
		if wo_names:
			wo_doc = frappe.get_doc("Work Order", wo_names[0])
			first_order_code = _cstr(get_order_code(wo_doc))
	except Exception:
		first_order_code = ""

	def _fmt_w(w):
		w = flt(w)
		return str(int(w)) if w == int(w) else str(w)

	combo_seg_widths = _parse_combination_widths_inches(combo_raw) if combo_raw else []
	comb_str = ""
	job_no_of_rolls = no_of_rolls
	if combo_raw and combo_seg_widths:
		comb_str = _format_combination_from_input(combo_raw)
		total_w = sum(combo_seg_widths)
		job_no_of_rolls = 1
	elif len(widths_list) > 1:
		comb_str = " + ".join([f'{_fmt_w(w)}"' for w in widths_list])
		total_w = sum(widths_list)
	else:
		total_w = sum(widths_list) if widths_list else width_inch_one
	total_qty = sum(qtys)

	row = {
		"job_id": job_id,
		"production_plan_item": _cstr(getattr(ppi_rows[0], "name", None)) if ppi_rows else None,
		"is_manual": 1,
		"no_of_shafts": no_of_shafts,
		"work_orders": ",".join(wo_names),
		"total_weight": total_qty,
	}
	meta = frappe.get_meta("Shaft Production Run Job")
	if meta.has_field("no_of_rolls"):
		row["no_of_rolls"] = job_no_of_rolls
	if meta.has_field("gsm") and gsm:
		try:
			row["gsm"] = int(gsm)
		except Exception:
			row["gsm"] = gsm
	if meta.has_field("quality") and quality:
		row["quality"] = quality
	if meta.has_field("combination"):
		if comb_str:
			row["combination"] = comb_str
		else:
			single_w = flt(widths_list[0]) if widths_list else flt(width_inch_one)
			cb = _format_shaft_combination_inches(single_w)
			if cb:
				row["combination"] = cb
	if meta.has_field("net_weight") and combo_raw and combo_seg_widths:
		nw_str = _net_weight_string_for_combination_segments(combo_raw, net_by_width)
		if nw_str:
			row["net_weight"] = nw_str
	if meta.has_field("total_width"):
		row["total_width"] = total_w
	if meta.has_field("manual_items"):
		row["manual_items"] = ",".join(item_codes_list)
	if meta.has_field("party_code") and first_order_code:
		row["party_code"] = first_order_code
	if meta.has_field("meter_roll_mtrs") and meter_roll_from_popup is not None and meter_roll_from_popup > 0:
		row["meter_roll_mtrs"] = flt(meter_roll_from_popup)

	spr.reload()
	spr.append("shaft_jobs", row)
	spr.save(ignore_permissions=True)

	return {
		"work_orders": wo_names,
		"reused_work_orders": reused_wo_names,
		"job_id": job_id,
		"shaft_production_run": spr.name,
	}


def _spr_company_from_doc(spr_doc) -> str:
	company = _cstr(getattr(spr_doc, "company", None))
	if company:
		return company
	pp_name = get_pp_from_spr(spr_doc.name)
	if pp_name:
		company = _cstr(frappe.db.get_value("Production Plan", pp_name, "company"))
	if company:
		return company
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if not company:
		frappe.throw(_("Company could not be resolved"))
	return company


def _spr_transfer_for_manufacture_type_name() -> str:
	if frappe.db.exists("Stock Entry Type", "Material Transfer for Manufacture"):
		p = _cstr(frappe.db.get_value("Stock Entry Type", "Material Transfer for Manufacture", "purpose"))
		if p == "Material Transfer for Manufacture":
			return "Material Transfer for Manufacture"
	name = frappe.db.get_value("Stock Entry Type", {"purpose": "Material Transfer for Manufacture"}, "name")
	return _cstr(name) if name else "Material Transfer for Manufacture"


def _spr_wo_has_submitted_mtfm(wo_name: str) -> bool:
	if not wo_name:
		return False
	return bool(
		frappe.db.exists(
			"Stock Entry",
			{"work_order": wo_name, "purpose": "Material Transfer for Manufacture", "docstatus": 1},
		)
	)


def _spr_validate_rm_stock_for_wo(bom_no: str, qty: float, source_wh: str, company: str) -> list[tuple]:
	"""Return shortages as (item_code, required, available, warehouse)."""
	bom_no = _cstr(bom_no)
	source_wh = _cstr(source_wh)
	rm_map, _multi = _bom_rm_stock_qty_map_for_fg(bom_no, flt(qty))
	shortages: list[tuple] = []
	for item_code, required in sorted((rm_map or {}).items()):
		req = _spr_round_rm_stock_qty(required)
		tol = _spr_rm_wip_shortage_tolerance(req)
		if req <= tol:
			continue
		avl = flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": source_wh}, "actual_qty") or 0
		)
		if avl + tol < req:
			shortages.append((item_code, req, avl, source_wh))
	return shortages


def _spr_throw_trial_rm_stock_shortages(shortages: list[tuple]) -> None:
	if not shortages:
		return
	prec = _spr_rm_stock_qty_precision()
	lines = []
	for item_code, req, avl, wh in shortages:
		stock_uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Kg"
		lines.append(
			_(
				"No stock for {0} in {1} (required {2} {3}, available {4} {3}). "
				"Transfer not done — Work Order not created."
			).format(item_code, wh, flt(req, prec), stock_uom, flt(avl, prec))
		)
	frappe.throw("\n".join(lines), title=_("Raw material stock shortage"))


def _spr_assign_batch_for_mtfm_line(sed, source_wh: str, need_qty: float = 0) -> None:
	"""Pick a batch with stock in source warehouse; skip batch when bulk stock is enough."""
	if sed.get("batch_no"):
		return
	has_batch = cint(frappe.db.get_value("Item", sed.item_code, "has_batch_no") or 0)
	if not has_batch:
		return
	need = flt(need_qty or sed.get("transfer_qty") or sed.get("qty"))
	if need <= 0:
		return
	total_wh_qty = _spr_rm_available_qty(sed.item_code, source_wh)
	batches = _spr_batches_in_warehouse(sed.item_code, source_wh)
	total_batch_qty = sum(flt(br.get("qty") or 0) for br in batches)
	if total_wh_qty + 1e-9 >= need and total_batch_qty + 1e-9 < need:
		sed.batch_no = ""
		return
	if not batches:
		sed.batch_no = ""
		return
	for br in batches:
		if flt(br.get("qty") or 0) + 1e-9 >= need:
			sed.batch_no = _cstr(br.get("batch_no"))
			return
	best = max(batches, key=lambda br: flt(br.get("qty") or 0))
	if flt(best.get("qty") or 0) > 0:
		sed.batch_no = _cstr(best.get("batch_no"))
		return
	sed.batch_no = ""


def _spr_create_and_submit_mtfm(wo_doc) -> str:
	"""Create and submit Material Transfer for Manufacture for a Work Order."""
	wo_name = _cstr(getattr(wo_doc, "name", None))
	if not wo_name:
		frappe.throw(_("Work Order is required for material transfer"))
	if _spr_wo_has_submitted_mtfm(wo_name):
		return _cstr(
			frappe.db.get_value(
				"Stock Entry",
				{"work_order": wo_name, "purpose": "Material Transfer for Manufacture", "docstatus": 1},
				"name",
			)
			or ""
		)

	wo_doc = frappe.get_doc("Work Order", wo_name)
	source_wh = _cstr(getattr(wo_doc, "source_warehouse", None))
	wip_wh = _cstr(getattr(wo_doc, "wip_warehouse", None))
	if not source_wh or not wip_wh:
		frappe.throw(_("Work Order {0} is missing source or WIP warehouse.").format(wo_name))

	se = frappe.new_doc("Stock Entry")
	se.purpose = "Material Transfer for Manufacture"
	se.stock_entry_type = _spr_transfer_for_manufacture_type_name()
	se.work_order = wo_name
	se.company = wo_doc.company
	se.from_warehouse = source_wh
	se.to_warehouse = wip_wh
	se.wip_warehouse = wip_wh
	se.fg_completed_qty = flt(getattr(wo_doc, "qty", 0)) or 1.0
	se.posting_date = today()
	se.posting_time = nowtime()
	se.set_posting_time = 1
	se_meta = frappe.get_meta("Stock Entry")
	if se_meta.has_field("use_serial_batch_fields"):
		se.use_serial_batch_fields = 1

	try:
		se.from_bom = 1
		se.bom_no = wo_doc.bom_no
		se.use_multi_level_bom = wo_doc.use_multi_level_bom
		se.get_items()
	except Exception:
		se.from_bom = 0
		se.items = []

	line_meta = frappe.get_meta("Stock Entry Detail")
	has_line_work_order = line_meta.has_field("work_order")
	for sed in se.items or []:
		if has_line_work_order and not sed.get("work_order"):
			sed.work_order = wo_name
		if not sed.s_warehouse:
			sed.s_warehouse = source_wh
		if not sed.t_warehouse:
			sed.t_warehouse = wip_wh
		if se_meta.has_field("use_serial_batch_fields"):
			sed.use_serial_batch_fields = 1
		_spr_finalize_mtfm_line_qty(sed, sed.s_warehouse or source_wh, flt(sed.qty))

	if not se.items:
		for row in wo_doc.get("required_items") or []:
			item_src = _cstr(getattr(row, "source_warehouse", None)) or source_wh
			line = {
				"item_code": row.item_code,
				"qty": row.required_qty,
				"transfer_qty": row.required_qty,
				"uom": row.stock_uom,
				"stock_uom": row.stock_uom,
				"s_warehouse": item_src,
				"t_warehouse": wip_wh,
				"conversion_factor": 1,
			}
			if has_line_work_order:
				line["work_order"] = wo_name
			se.append("items", line)
		for sed in se.items or []:
			_spr_finalize_mtfm_line_qty(sed, sed.s_warehouse or source_wh, flt(sed.qty))

	if not se.items:
		frappe.throw(_("No raw material lines to transfer for Work Order {0}.").format(wo_name))

	se.flags.ignore_permissions = True
	se.insert(ignore_permissions=True)
	se.flags.ignore_validate = True
	se.submit()

	frappe.db.set_value(
		"Work Order",
		wo_name,
		{
			"material_transferred_for_manufacturing": wo_doc.qty,
			"status": "In Process",
		},
		update_modified=True,
	)
	try:
		frappe.db.set_value(
			"Work Order",
			wo_name,
			"actual_start_date",
			frappe.utils.now_datetime(),
			update_modified=True,
		)
	except Exception:
		pass
	return se.name


def _spr_set_wo_required_item_source_warehouses(wo_name: str, source_wh: str) -> None:
	source_wh = _cstr(source_wh)
	if not wo_name or not source_wh:
		return
	for req in frappe.get_all(
		"Work Order Item",
		filters={"parent": wo_name, "parenttype": "Work Order"},
		pluck="name",
	) or []:
		frappe.db.set_value("Work Order Item", req, "source_warehouse", source_wh, update_modified=False)


def _spr_submit_trial_work_order(wo_name: str) -> None:
	wo = frappe.get_doc("Work Order", wo_name)
	if wo.docstatus == 0:
		wo.flags.ignore_permissions = True
		wo.submit()


def _spr_finalize_trial_work_order(
	wo_name: str,
	wh_ctx: dict,
	bom_no: str,
	qty: float,
) -> str:
	"""Ensure trial WO has warehouses, RM transfer, and is submitted."""
	if not wo_name or not frappe.db.exists("Work Order", wo_name):
		frappe.throw(_("Work Order {0} not found").format(wo_name or "—"))

	if _spr_wo_has_submitted_mtfm(wo_name):
		wo = frappe.get_doc("Work Order", wo_name)
		if wo.docstatus == 0:
			_spr_submit_trial_work_order(wo_name)
		return wo_name

	shortages = _spr_validate_rm_stock_for_wo(
		bom_no,
		qty,
		_cstr(wh_ctx.get("source_warehouse")),
		_cstr(wh_ctx.get("company")),
	)
	_spr_throw_trial_rm_stock_shortages(shortages)

	wo = frappe.get_doc("Work Order", wo_name)
	company = _cstr(wh_ctx.get("company"))
	source_wh = _cstr(wh_ctx.get("source_warehouse"))
	wip_wh = _cstr(wh_ctx.get("wip_warehouse"))
	fg_wh = _cstr(wh_ctx.get("fg_warehouse"))

	if wo.docstatus == 0:
		updates = {}
		if company and wo.company != company:
			updates["company"] = company
		if source_wh and wo.source_warehouse != source_wh:
			updates["source_warehouse"] = source_wh
		if wip_wh and getattr(wo, "wip_warehouse", None) != wip_wh:
			updates["wip_warehouse"] = wip_wh
		if fg_wh and wo.fg_warehouse != fg_wh:
			updates["fg_warehouse"] = fg_wh
		if updates:
			frappe.db.set_value("Work Order", wo_name, updates, update_modified=True)
		_spr_set_wo_required_item_source_warehouses(wo_name, source_wh)

	wo.reload()
	if wo.docstatus == 0:
		_spr_submit_trial_work_order(wo_name)
	wo.reload()
	try:
		_spr_create_and_submit_mtfm(wo)
	except Exception:
		wo.reload()
		if wo.docstatus == 1 and not _spr_wo_has_submitted_mtfm(wo_name):
			try:
				wo.cancel()
			except Exception:
				pass
		raise
	return wo_name


def _spr_insert_trial_work_order(unit: str, item_code: str, qty: float, order_code: str) -> str:
	from production_entry.production_planning.doctype.planning_sheet.planning_sheet import (
		get_default_bom_for_item,
	)
	from production_entry.production_planning.spr_unit_warehouses import (
		resolve_spr_unit_manufacturing_warehouses,
	)

	wh_ctx = resolve_spr_unit_manufacturing_warehouses(unit)
	company = _cstr(wh_ctx.get("company"))
	source_wh = _cstr(wh_ctx.get("source_warehouse"))
	wip_wh = _cstr(wh_ctx.get("wip_warehouse"))
	fg_wh = _cstr(wh_ctx.get("fg_warehouse"))

	bom = get_default_bom_for_item(item_code, company)
	if not bom:
		frappe.throw(_("No active BOM for item {0}").format(item_code))

	shortages = _spr_validate_rm_stock_for_wo(bom, qty, source_wh, company)
	_spr_throw_trial_rm_stock_shortages(shortages)

	wo = frappe.new_doc("Work Order")
	wo.production_item = item_code
	wo.bom_no = bom
	wo.qty = flt(qty)
	wo.company = company
	meta_wo = frappe.get_meta("Work Order")
	if meta_wo.has_field("description"):
		wo.description = _("SPR trial order — Item {0} — Order {1}").format(item_code, order_code)
	for fn in ("custom_order_code", "order_code", "custom_party_code"):
		if meta_wo.has_field(fn) and order_code:
			wo.set(fn, order_code)
	wo.source_warehouse = source_wh
	wo.fg_warehouse = fg_wh
	if meta_wo.has_field("wip_warehouse"):
		wo.wip_warehouse = wip_wh
	frappe.flags.spr_manual_work_order_insert = True
	try:
		wo.insert(ignore_permissions=True)
	finally:
		frappe.flags.spr_manual_work_order_insert = False
	wo_name = wo.name
	_spr_set_wo_required_item_source_warehouses(wo_name, source_wh)
	_spr_finalize_trial_work_order(wo_name, wh_ctx, bom, qty)
	return wo_name


def _spr_list_reusable_trial_work_orders(item_code: str, order_code: str = "") -> list[str]:
	if not item_code:
		return []
	rows = (
		frappe.get_all(
			"Work Order",
			filters={
				"production_item": item_code,
				"docstatus": ["<", 2],
				"status": ["not in", ["Completed", "Stopped", "Cancelled"]],
			},
			fields=["name", "description", "modified", "production_plan"],
			order_by="modified desc",
		)
		or []
	)
	rows = [r for r in rows if not _cstr(r.get("production_plan"))]
	oc = _cstr(order_code)
	out: list[str] = []
	seen = set()
	for r in rows:
		wo_name = _cstr(r.get("name"))
		if not wo_name or wo_name in seen:
			continue
		desc = _cstr(r.get("description"))
		if "SPR trial" not in desc and oc:
			continue
		if oc:
			try:
				wo_doc = frappe.get_doc("Work Order", wo_name)
				if get_order_code(wo_doc) != oc:
					continue
			except Exception:
				pass
		seen.add(wo_name)
		out.append(wo_name)
	if not out:
		for r in rows:
			wo_name = _cstr(r.get("name"))
			if wo_name and wo_name not in seen:
				seen.add(wo_name)
				out.append(wo_name)
	return out


def _spr_apply_session_people(spr, operator=None, supervisor=None) -> None:
	if not spr:
		return
	meta = frappe.get_meta("Shaft Production Run")
	for value, candidates in (
		(operator, ("operator", "custom_operator", "custom_shift_operator")),
		(supervisor, ("supervisor", "custom_supervisor", "custom_shift_supervisor")),
	):
		val = _cstr(value).strip()
		if not val:
			continue
		for key in candidates:
			if meta.has_field(key):
				spr.set(key, val)
				break


def _spr_trial_context_for_unit(unit: str, spr=None) -> dict:
	unit = normalize_planning_unit_for_select(_cstr(unit))
	if not unit and spr:
		unit = normalize_planning_unit_for_select(_cstr(getattr(spr, "custom_unit", None)))
	if not unit:
		frappe.throw(_("Unit is required for Trail Order"))
	from production_entry.production_planning.spr_unit_warehouses import (
		resolve_spr_unit_manufacturing_warehouses,
	)

	wh_ctx = resolve_spr_unit_manufacturing_warehouses(unit)
	company = _cstr(wh_ctx.get("company"))
	if not company and spr:
		company = _spr_company_from_doc(spr)
	if not company:
		company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
	qualities = frappe.get_all("Quality Master", fields=["name", "quality_name"], order_by="quality_name asc") or []
	colors = frappe.get_all("Colour Master", fields=["name", "colour_name"], order_by="colour_name asc") or []
	return {
		"company": company,
		"custom_unit": unit,
		"max_shaft_inches": get_mix_roll_unit_max_shaft_inches(unit),
		"source_warehouse": _cstr(wh_ctx.get("source_warehouse")),
		"wip_warehouse": _cstr(wh_ctx.get("wip_warehouse")),
		"fg_warehouse": _cstr(wh_ctx.get("fg_warehouse")),
		"plant_floor": _cstr(wh_ctx.get("plant_floor")),
		"workstation": _cstr(wh_ctx.get("workstation")),
		"qualities": qualities,
		"colors": colors,
	}


def _spr_new_standalone_trial_spr(
	unit: str,
	order_code: str,
	run_date=None,
	shift=None,
	operator=None,
	supervisor=None,
):
	"""Create a dedicated draft SPR for Trail Order — never reuse an existing production SPR."""
	ensure_planning_line_unit_docfield_options()
	ctx = _spr_trial_context_for_unit(unit)
	unit = ctx["custom_unit"]
	spr = frappe.new_doc("Shaft Production Run")
	spr.run_date = getdate(run_date) if run_date else today()
	spr.custom_unit = unit
	if shift:
		spr.shift = shift
	if ctx.get("company"):
		spr.company = ctx["company"]
	if frappe.get_meta("Shaft Production Run").has_field("status"):
		spr.status = "Draft"
	order_code = _cstr(order_code).strip()
	if order_code:
		if frappe.get_meta("Shaft Production Run").has_field("custom_order_code"):
			spr.custom_order_code = order_code
		if frappe.get_meta("Shaft Production Run").has_field("custom_party_code"):
			spr.custom_party_code = order_code
	_spr_apply_session_people(spr, operator, supervisor)
	spr.insert(ignore_permissions=True)
	return spr


@frappe.whitelist(methods=["GET", "POST"])
def spr_get_trial_order_context(shaft_production_run=None, unit=None):
	spr = None
	if _cstr(shaft_production_run).strip():
		_spr_require_saved(shaft_production_run)
		spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
		if not unit:
			unit = getattr(spr, "custom_unit", None)
	return _spr_trial_context_for_unit(unit, spr)


@frappe.whitelist(methods=["GET", "POST"])
def spr_resolve_trial_fabric_item(
	quality,
	color,
	gsm,
	width_inch,
	company=None,
	create_if_missing=1,
):
	from production_entry.production_planning.fabric_item_bom import (
		ensure_fabric_item,
		ensure_nonwoven_fabric_bom,
		resolve_fabric_item_code,
	)

	company = _cstr(company) or frappe.defaults.get_global_default("company")
	resolved = resolve_fabric_item_code(quality, color, gsm, width_inch)
	item_code = _cstr(resolved.get("item_code"))
	created = 0
	if not frappe.db.exists("Item", item_code) and cint(create_if_missing):
		out = ensure_fabric_item(company, quality, color, gsm, width_inch)
		item_code = _cstr(out.get("item_code"))
		created = cint(out.get("created"))
	bom_name = None
	if frappe.db.exists("Item", item_code):
		bom_name = ensure_nonwoven_fabric_bom(item_code, company, quality, color, gsm=gsm)
	return {
		"item_code": item_code,
		"item_name": frappe.db.get_value("Item", item_code, "item_name") if item_code else "",
		"width_inch": resolved.get("width_inch"),
		"width_mm": resolved.get("width_mm"),
		"gsm": int(flt(gsm)),
		"created": created,
		"bom": bom_name,
		"exists": bool(frappe.db.exists("Item", item_code)),
	}


@frappe.whitelist(methods=["GET", "POST"])
def spr_preview_trial_fabric_bom(item_code, company=None, quality=None, color=None, gsm=None):
	from production_entry.production_planning.fabric_item_bom import preview_fabric_bom

	return preview_fabric_bom(item_code, company=company, quality=quality, color=color, gsm=gsm)


@frappe.whitelist(methods=["GET", "POST"])
def spr_create_trial_fabric_bom(
	item_code,
	company=None,
	quality=None,
	color=None,
	gsm=None,
	recipe_payload=None,
	force_new=0,
):
	from production_entry.production_planning.fabric_item_bom import create_fabric_bom_from_recipe

	return create_fabric_bom_from_recipe(
		item_code,
		company=company,
		quality=quality,
		color=color,
		gsm=gsm,
		recipe_payload=recipe_payload,
		force_new=force_new,
	)


@frappe.whitelist(methods=["GET", "POST"])
def spr_create_trial_jobs_multi(
	shaft_production_run=None,
	order_code=None,
	no_of_shafts=None,
	items=None,
	no_of_rolls=None,
	combination_input=None,
	create_new_spr=1,
	run_date=None,
	shift=None,
	unit=None,
	operator=None,
	supervisor=None,
):
	"""Create standalone trial Work Orders on a NEW Shaft Production Run (not an existing production SPR)."""
	order_code = _cstr(order_code)
	if not order_code:
		frappe.throw(_("Order code is required for Trail Order"))
	no_of_shafts = cint(no_of_shafts)
	if no_of_shafts < 1:
		frappe.throw(_("Number of shafts must be at least 1"))
	no_of_rolls = cint(no_of_rolls) if no_of_rolls is not None else 1
	if no_of_rolls < 1:
		no_of_rolls = 1
	if isinstance(items, str):
		items = frappe.parse_json(items)
	if not items or not isinstance(items, list):
		frappe.throw(_("Add at least one trial fabric line"))

	unit = _cstr(unit).strip()
	if not unit and _cstr(shaft_production_run).strip():
		unit = _cstr(frappe.db.get_value("Shaft Production Run", shaft_production_run, "custom_unit"))
		if not run_date:
			run_date = frappe.db.get_value("Shaft Production Run", shaft_production_run, "run_date")
		if not shift:
			shift = frappe.db.get_value("Shaft Production Run", shaft_production_run, "shift")
		if not operator:
			for key in ("operator", "custom_operator", "custom_shift_operator"):
				if frappe.get_meta("Shaft Production Run").has_field(key):
					operator = frappe.db.get_value("Shaft Production Run", shaft_production_run, key)
					if operator:
						break
		if not supervisor:
			for key in ("supervisor", "custom_supervisor", "custom_shift_supervisor"):
				if frappe.get_meta("Shaft Production Run").has_field(key):
					supervisor = frappe.db.get_value("Shaft Production Run", shaft_production_run, key)
					if supervisor:
						break
	# Trail Order always gets its own SPR so it cannot alter an in-progress production run.
	spr = _spr_new_standalone_trial_spr(
		unit,
		order_code,
		run_date=run_date,
		shift=shift,
		operator=operator,
		supervisor=supervisor,
	)
	unit = normalize_planning_unit_for_select(_cstr(getattr(spr, "custom_unit", None)))
	from production_entry.production_planning.spr_unit_warehouses import (
		resolve_spr_unit_manufacturing_warehouses,
	)
	from production_entry.production_planning.doctype.planning_sheet.planning_sheet import (
		get_default_bom_for_item,
	)

	resolve_spr_unit_manufacturing_warehouses(unit)
	company = _spr_company_from_doc(spr)
	combo_raw = _cstr(combination_input).strip()
	if combo_raw:
		validate_mix_shaft_width(unit, combo_raw)

	wo_names: list[str] = []
	reused_wo_names: list[str] = []
	qtys: list[float] = []
	widths_list: list[float] = []
	item_codes_list: list[str] = []
	meter_roll_from_popup: float | None = None

	for raw in items:
		if not isinstance(raw, dict):
			frappe.throw(_("Invalid line payload"))
		item_code = _cstr(raw.get("item_code"))
		qty = flt(raw.get("wo_qty"))
		if not item_code or qty <= 0:
			frappe.throw(_("Each line needs item and Work Order qty greater than zero"))
		selected_reuse = _cstr(raw.get("selected_reuse_work_order"))
		wo_name = ""
		if selected_reuse and selected_reuse != "__NEW__":
			candidates = _spr_list_reusable_trial_work_orders(item_code, order_code)
			if selected_reuse in candidates:
				wo_name = selected_reuse
		if not wo_name:
			candidates = _spr_list_reusable_trial_work_orders(item_code, order_code)
			if candidates and selected_reuse != "__NEW__":
				wo_name = candidates[0]
		if not wo_name:
			wo_name = _spr_insert_trial_work_order(unit, item_code, qty, order_code)
		else:
			reused_wo_names.append(wo_name)
			wh_ctx = resolve_spr_unit_manufacturing_warehouses(unit)
			bom = get_default_bom_for_item(item_code, wh_ctx["company"])
			if not bom:
				frappe.throw(_("No active BOM for item {0}").format(item_code))
			_spr_finalize_trial_work_order(wo_name, wh_ctx, bom, qty)
		spr.reload()
		for j in _spr_job_rows(spr):
			wos = _cstr(getattr(j, "work_orders", None) or "")
			for part in wos.replace("\n", ",").split(","):
				if part.strip() == wo_name:
					frappe.throw(
						_("Work Order {0} is already linked to this Shaft Production Run (Job {1}).").format(
							wo_name,
							_spr_job_id(j),
						)
					)
		wo_names.append(wo_name)
		qtys.append(qty)
		item_codes_list.append(item_code)
		_gsm, w_in = parse_item_code(item_code)
		widths_list.append(flt(w_in))
		if meter_roll_from_popup is None and raw.get("meter_roll") not in (None, ""):
			mr = flt(raw.get("meter_roll"))
			if mr > 0:
				meter_roll_from_popup = mr

	job_id = f"TRIAL-{frappe.generate_hash(length=6).upper()}"
	for _attempt in range(20):
		if not any(_cstr(_spr_job_id(j)) == job_id for j in _spr_job_rows(spr)):
			break
		job_id = f"TRIAL-{frappe.generate_hash(length=6).upper()}"

	first_ic = item_codes_list[0]
	gsm, width_inch_one = parse_item_code(first_ic)
	item_name = frappe.db.get_value("Item", first_ic, "item_name")
	quality, color = extract_quality_and_color(item_name or "", item_code=first_ic)

	def _fmt_w(w):
		w = flt(w)
		return str(int(w)) if w == int(w) else str(w)

	comb_str = ""
	combo_seg_widths = _parse_combination_widths_inches(combo_raw) if combo_raw else []
	job_no_of_rolls = no_of_rolls
	if combo_raw and combo_seg_widths:
		comb_str = _format_combination_from_input(combo_raw)
		total_w = sum(combo_seg_widths)
		job_no_of_rolls = 1
	elif len(widths_list) > 1:
		comb_str = " + ".join([f'{_fmt_w(w)}"' for w in widths_list])
		total_w = sum(widths_list)
	else:
		total_w = sum(widths_list) if widths_list else width_inch_one
	total_qty = sum(qtys)

	row = {
		"job_id": job_id,
		"is_manual": 1,
		"no_of_shafts": no_of_shafts,
		"work_orders": ",".join(wo_names),
		"total_weight": total_qty,
	}
	meta = frappe.get_meta("Shaft Production Run Job")
	if meta.has_field("no_of_rolls"):
		row["no_of_rolls"] = job_no_of_rolls
	if meta.has_field("gsm") and gsm:
		try:
			row["gsm"] = int(gsm)
		except Exception:
			row["gsm"] = gsm
	if meta.has_field("quality") and quality:
		row["quality"] = quality
	if meta.has_field("color") and color:
		row["color"] = color
	if meta.has_field("combination"):
		if comb_str:
			row["combination"] = comb_str
		else:
			cb = _format_shaft_combination_inches(width_inch_one)
			if cb:
				row["combination"] = cb
	if meta.has_field("total_width"):
		row["total_width"] = total_w
	if meta.has_field("manual_items"):
		row["manual_items"] = ",".join(item_codes_list)
	if meta.has_field("party_code"):
		row["party_code"] = order_code
	if meta.has_field("meter_roll_mtrs") and meter_roll_from_popup is not None and meter_roll_from_popup > 0:
		row["meter_roll_mtrs"] = flt(meter_roll_from_popup)

	spr.reload()
	spr.append("shaft_jobs", row)
	spr.save(ignore_permissions=True)

	return {
		"work_orders": wo_names,
		"reused_work_orders": reused_wo_names,
		"job_id": job_id,
		"shaft_production_run": spr.name,
		"order_code": order_code,
		"is_trial": 1,
		"pp_id": spr.name,
	}


@frappe.whitelist()
def spr_cancel_duplicate_mtfm_entries(stock_entries, work_order=None):
	"""Cancel duplicate Material Transfer for Manufacture entries and resync WO required items."""
	frappe.only_for(("System Manager", "Manufacturing Manager", "Administrator"))

	if isinstance(stock_entries, str):
		try:
			stock_entries = json.loads(stock_entries)
		except Exception:
			stock_entries = [x.strip() for x in stock_entries.split(",") if x.strip()]
	names = [_cstr(x).strip() for x in (stock_entries or []) if _cstr(x).strip()]
	if not names:
		frappe.throw(_("Select at least one Stock Entry to cancel"))

	wo = _cstr(work_order).strip()
	if not wo:
		wo = _cstr(frappe.db.get_value("Stock Entry", names[0], "work_order"))
	if not wo:
		frappe.throw(_("Work Order is required"))

	entries = []
	for name in names:
		if not frappe.db.exists("Stock Entry", name):
			frappe.throw(_("Stock Entry {0} not found").format(name))
		se = frappe.get_doc("Stock Entry", name)
		if cint(se.docstatus) != 1:
			continue
		if _cstr(se.purpose) != "Material Transfer for Manufacture":
			frappe.throw(_("Stock Entry {0} is not Material Transfer for Manufacture").format(name))
		if _cstr(se.work_order) and _cstr(se.work_order) != wo:
			frappe.throw(_("Stock Entry {0} belongs to a different Work Order").format(name))
		entries.append(se)

	if not entries:
		frappe.throw(_("No submitted Material Transfer entries to cancel"))

	entries.sort(
		key=lambda s: (
			getdate(s.posting_date),
			_cstr(s.posting_time),
			s.creation,
		),
		reverse=True,
	)

	cancelled = []
	frappe.flags.spr_skip_wo_transfer_qty_validation = True
	try:
		for se in entries:
			se.flags.ignore_validate = True
			se.cancel()
			cancelled.append(se.name)
	finally:
		frappe.flags.spr_skip_wo_transfer_qty_validation = False

	dummy = frappe.new_doc("Shaft Production Run")
	dummy._sync_work_order_required_item_progress(wo)
	try:
		frappe.db.commit()
	except Exception:
		pass

	return {
		"status": "ok",
		"work_order": wo,
		"cancelled": cancelled,
		"message": _("Cancelled {0} transfer(s). Reopen Work Order {1} and submit SPR again.").format(
			len(cancelled), wo
		),
	}


@frappe.whitelist()
def spr_cancel_duplicate_manufacture_entries(stock_entries, work_order=None):
	"""Cancel duplicate Manufacture Stock Entries (newest first) and resync WO produced qty."""
	frappe.only_for(("System Manager", "Manufacturing Manager", "Administrator"))

	if isinstance(stock_entries, str):
		try:
			stock_entries = json.loads(stock_entries)
		except Exception:
			stock_entries = [x.strip() for x in stock_entries.split(",") if x.strip()]
	names = [_cstr(x).strip() for x in (stock_entries or []) if _cstr(x).strip()]
	if not names:
		frappe.throw(_("Select at least one Stock Entry to cancel"))

	wo = _cstr(work_order).strip()
	if not wo:
		wo = _cstr(frappe.db.get_value("Stock Entry", names[0], "work_order"))
	if not wo:
		frappe.throw(_("Work Order is required"))

	entries = []
	for name in names:
		if not frappe.db.exists("Stock Entry", name):
			frappe.throw(_("Stock Entry {0} not found").format(name))
		se = frappe.get_doc("Stock Entry", name)
		if cint(se.docstatus) != 1:
			continue
		if _cstr(se.purpose) != "Manufacture":
			frappe.throw(_("Stock Entry {0} is not Manufacture").format(name))
		if _cstr(se.work_order) and _cstr(se.work_order) != wo:
			frappe.throw(_("Stock Entry {0} belongs to a different Work Order").format(name))
		entries.append(se)

	if not entries:
		frappe.throw(_("No submitted Manufacture entries to cancel"))

	entries.sort(
		key=lambda s: (
			getdate(s.posting_date),
			_cstr(s.posting_time),
			s.creation,
		),
		reverse=True,
	)

	cancelled = []
	frappe.flags.spr_skip_wo_transfer_qty_validation = True
	try:
		for se in entries:
			se.flags.ignore_validate = True
			se.cancel()
			cancelled.append(se.name)
	finally:
		frappe.flags.spr_skip_wo_transfer_qty_validation = False

	dummy = frappe.new_doc("Shaft Production Run")
	dummy._sync_work_order_produced_qty_from_submitted_manufacture(wo)
	dummy._sync_work_order_required_item_progress(wo)
	try:
		frappe.db.commit()
	except Exception:
		pass

	return {
		"status": "ok",
		"work_order": wo,
		"cancelled": cancelled,
		"message": _("Cancelled {0} manufacture entry/entries. Work Order {1} produced qty resynced.").format(
			len(cancelled), wo
		),
	}


@frappe.whitelist()
def spr_resync_work_order_consumption(work_order: str):
	"""Manual utility: recompute consumed/transferred qty on WO required items from submitted Stock Entries."""
	wo = _cstr(work_order)
	if not wo:
		frappe.throw(_("Work Order is required"))
	if not frappe.db.exists("Work Order", wo):
		frappe.throw(_("Work Order {0} not found").format(wo))
	dummy = frappe.new_doc("Shaft Production Run")
	dummy._sync_work_order_required_item_progress(wo)
	return {"status": "ok", "work_order": wo}


@frappe.whitelist()
def spr_resync_work_order_progress(work_order: str):
	"""Manual utility: recompute WO produced + required-item progress from submitted Stock Entries."""
	wo = _cstr(work_order)
	if not wo:
		frappe.throw(_("Work Order is required"))
	if not frappe.db.exists("Work Order", wo):
		frappe.throw(_("Work Order {0} not found").format(wo))

	dummy = frappe.new_doc("Shaft Production Run")
	dummy._sync_work_order_produced_qty_from_submitted_manufacture(wo)
	dummy._sync_work_order_required_item_progress(wo)

	wo_doc = frappe.get_doc("Work Order", wo)
	produced = flt(getattr(wo_doc, "produced_qty", 0))
	target = flt(getattr(wo_doc, "qty", 0))
	if target > 0 and produced + 1e-9 >= target and _cstr(getattr(wo_doc, "status", "")) not in ("Completed", "Stopped", "Cancelled"):
		wo_doc.db_set("status", "Completed")
	return {"status": "ok", "work_order": wo, "produced_qty": produced}


@frappe.whitelist()
def spr_resync_production_plan_progress(production_plan: str):
	"""Manual utility: recompute all Work Orders progress under a Production Plan for existing data."""
	pp = _cstr(production_plan)
	if not pp:
		frappe.throw(_("Production Plan is required"))
	if not frappe.db.exists("Production Plan", pp):
		frappe.throw(_("Production Plan {0} not found").format(pp))

	wo_names = frappe.get_all(
		"Work Order",
		filters={"production_plan": pp, "docstatus": ["<", 2]},
		pluck="name",
	) or []
	dummy = frappe.new_doc("Shaft Production Run")
	out = []
	for wo in wo_names:
		dummy._sync_work_order_produced_qty_from_submitted_manufacture(wo)
		dummy._sync_work_order_required_item_progress(wo)
		wo_doc = frappe.get_doc("Work Order", wo)
		produced = flt(getattr(wo_doc, "produced_qty", 0))
		target = flt(getattr(wo_doc, "qty", 0))
		if target > 0 and produced + 1e-9 >= target and _cstr(getattr(wo_doc, "status", "")) not in ("Completed", "Stopped", "Cancelled"):
			wo_doc.db_set("status", "Completed")
		out.append({"work_order": wo, "produced_qty": produced, "qty": target, "status": _cstr(getattr(wo_doc, "status", ""))})
	dummy._sync_production_plan_progress_from_work_orders(pp)
	return {"status": "ok", "production_plan": pp, "work_orders": out}


@frappe.whitelist()
def spr_force_relink_and_resync(production_plan: str, shaft_production_run: str | None = None):
	"""
	Recovery utility for old partial data:
	1) Relink submitted Manufacture Stock Entries with blank work_order (DB-level update).
	2) Resync WO produced/consumption progress.
	3) Resync Production Plan produced progress.
	"""
	pp = _cstr(production_plan)
	if not pp:
		frappe.throw(_("Production Plan is required"))
	if not frappe.db.exists("Production Plan", pp):
		frappe.throw(_("Production Plan {0} not found").format(pp))

	spr_name = _cstr(shaft_production_run)
	spr_doc = None
	if spr_name:
		if not frappe.db.exists("Shaft Production Run", spr_name):
			frappe.throw(_("Shaft Production Run {0} not found").format(spr_name))
		spr_doc = frappe.get_doc("Shaft Production Run", spr_name)

	wo_rows = frappe.get_all(
		"Work Order",
		filters={"production_plan": pp, "docstatus": ["<", 2]},
		fields=["name", "production_item"],
	) or []
	wo_by_item = defaultdict(list)
	for w in wo_rows:
		ic = _cstr(w.get("production_item"))
		if ic:
			wo_by_item[ic].append(_cstr(w.get("name")))

	se_names = []
	if spr_doc and _cstr(getattr(spr_doc, "manufacturing_entries", "")):
		se_names = [x.strip() for x in _cstr(getattr(spr_doc, "manufacturing_entries", "")).split(",") if x and x.strip()]
	if not se_names and spr_doc:
		spr_company = _cstr(getattr(spr_doc, "company", None) or spr_doc.get("company"))
		spr_posting_date = getattr(spr_doc, "run_date", None) or spr_doc.get("run_date")
		filters = {
			"purpose": "Manufacture",
			"docstatus": 1,
		}
		if spr_company:
			filters["company"] = spr_company
		if spr_posting_date:
			filters["posting_date"] = spr_posting_date
		se_names = frappe.get_all("Stock Entry", filters=filters, pluck="name") or []
	else:
		# Fallback without SPR: all submitted Manufacture entries whose WO belongs to this PP plus blanks.
		se_names = frappe.get_all(
			"Stock Entry",
			filters={"purpose": "Manufacture", "docstatus": 1},
			pluck="name",
		) or []

	linked = []
	skipped = []
	for se_name in se_names:
		se = frappe.get_doc("Stock Entry", se_name)
		if _cstr(getattr(se, "purpose", "")) != "Manufacture" or cint(getattr(se, "docstatus", 0)) != 1:
			continue
		if _cstr(getattr(se, "work_order", "")):
			continue
		fg_items = sorted(
			{
				_cstr(getattr(d, "item_code", ""))
				for d in (se.items or [])
				if cint(getattr(d, "is_finished_item", 0)) == 1 and _cstr(getattr(d, "item_code", ""))
			}
		)
		if len(fg_items) != 1:
			skipped.append({"stock_entry": se_name, "reason": "ambiguous_fg_items", "fg_items": fg_items})
			continue
		candidates = wo_by_item.get(fg_items[0], [])
		if len(candidates) != 1:
			skipped.append({"stock_entry": se_name, "reason": "ambiguous_wo_candidates", "item_code": fg_items[0], "candidates": candidates})
			continue
		wo_id = candidates[0]
		frappe.db.set_value("Stock Entry", se_name, "work_order", wo_id, update_modified=False)
		linked.append({"stock_entry": se_name, "work_order": wo_id, "item_code": fg_items[0]})

	dummy = frappe.new_doc("Shaft Production Run")
	out = []
	for wo in [w.get("name") for w in wo_rows]:
		dummy._sync_work_order_produced_qty_from_submitted_manufacture(wo)
		dummy._sync_work_order_required_item_progress(wo)
		wo_doc = frappe.get_doc("Work Order", wo)
		produced = flt(getattr(wo_doc, "produced_qty", 0))
		target = flt(getattr(wo_doc, "qty", 0))
		out.append({"work_order": wo, "produced_qty": produced, "qty": target, "status": _cstr(getattr(wo_doc, "status", ""))})
	dummy._sync_production_plan_progress_from_work_orders(pp)

	return {
		"status": "ok",
		"production_plan": pp,
		"shaft_production_run": spr_name or None,
		"linked_count": len(linked),
		"linked": linked,
		"skipped_count": len(skipped),
		"skipped": skipped[:50],
		"work_orders": out,
	}


@frappe.whitelist()
def spr_backfill_missing_manufacture_from_spr(shaft_production_run: str, submit_entries: int = 0):
	"""
	Create missing Manufacture entries from SPR roll rows (batch-wise), not whole WO qty.
	Useful for legacy partial cases where some WO/rolls were skipped earlier.
	"""
	spr_name = _cstr(shaft_production_run)
	if not spr_name:
		frappe.throw(_("Shaft Production Run is required"))
	if not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run {0} not found").format(spr_name))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if not spr.get("production_plan"):
		frappe.throw(_("SPR {0} has no Production Plan").format(spr_name))

	# Group positive roll rows by WO
	wo_rows = defaultdict(list)
	for r in spr.items or []:
		q = flt(spr._row_fg_qty(r))
		wo = _cstr(r.get("work_order") or r.get("wo_id"))
		if q > 0 and wo:
			wo_rows[wo].append(r)
	if not wo_rows:
		return {"status": "ok", "created": [], "skipped": [{"reason": "no_wo_roll_rows"}]}

	created = []
	skipped = []
	do_submit = cint(submit_entries) == 1

	for wo_id, rows in wo_rows.items():
		if not frappe.db.exists("Work Order", wo_id):
			skipped.append({"work_order": wo_id, "reason": "wo_not_found"})
			continue
		wo_doc = frappe.get_doc("Work Order", wo_id)
		item_code = _cstr(getattr(wo_doc, "production_item", None))
		if not item_code:
			skipped.append({"work_order": wo_id, "reason": "wo_missing_production_item"})
			continue

		# Existing posted qty per batch in this SPR+WO (submitted and draft to avoid duplicates).
		existing_batch_qty = defaultdict(float)
		existing_total = 0.0
		se_has_spr_ref = frappe.db.has_column("Stock Entry", "custom_spr_reference")
		if se_has_spr_ref:
			existing = frappe.db.sql(
				"""
				SELECT IFNULL(sed.batch_no, '') AS batch_no, IFNULL(SUM(IFNULL(sed.qty, 0)), 0) AS qty
				FROM `tabStock Entry` se
				INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE IFNULL(se.custom_spr_reference, '') = %(spr)s
				  AND IFNULL(se.purpose, '') = 'Manufacture'
				  AND IFNULL(se.docstatus, 0) < 2
				  AND IFNULL(se.work_order, '') = %(wo)s
				  AND IFNULL(sed.is_finished_item, 0) = 1
				  AND IFNULL(sed.item_code, '') = %(item)s
				GROUP BY IFNULL(sed.batch_no, '')
				""",
				{"spr": spr.name, "wo": wo_id, "item": item_code},
				as_dict=True,
			) or []
		else:
			# Site schema fallback: derive scope by WO + date (+ company) when custom_spr_reference is unavailable.
			existing = frappe.db.sql(
				"""
				SELECT IFNULL(sed.batch_no, '') AS batch_no, IFNULL(SUM(IFNULL(sed.qty, 0)), 0) AS qty
				FROM `tabStock Entry` se
				INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
				WHERE IFNULL(se.purpose, '') = 'Manufacture'
				  AND IFNULL(se.docstatus, 0) < 2
				  AND IFNULL(se.work_order, '') = %(wo)s
				  AND IFNULL(se.posting_date, '') = %(posting_date)s
				  AND IFNULL(se.company, '') = %(company)s
				  AND IFNULL(sed.is_finished_item, 0) = 1
				  AND IFNULL(sed.item_code, '') = %(item)s
				GROUP BY IFNULL(sed.batch_no, '')
				""",
				{
					"wo": wo_id,
					"posting_date": spr.run_date,
					"company": wo_doc.company,
					"item": item_code,
				},
				as_dict=True,
			) or []
		for e in existing:
			b = _cstr(e.get("batch_no"))
			q = flt(e.get("qty"))
			existing_total += q
			if b:
				existing_batch_qty[b] += q

		# Pick only truly missing roll rows (batch-wise when possible).
		missing_rows = []
		expected_total = 0.0
		for r in rows:
			rq = flt(spr._row_fg_qty(r))
			if rq <= 0:
				continue
			expected_total += rq
			bn_raw = _cstr(r.get("batch_no"))
			b_link = ""
			if bn_raw:
				try:
					b_link = _cstr(spr._get_batch_link_name_for_stock_entry(bn_raw, item_code, wo_doc.company, r))
				except Exception:
					b_link = ""
			if b_link:
				posted = flt(existing_batch_qty.get(b_link, 0))
				if posted + 1e-9 >= rq:
					continue
			missing_rows.append(r)

		missing_total = flt(sum(spr._row_fg_qty(x) for x in missing_rows))
		if missing_total <= 0:
			skipped.append(
				{
					"work_order": wo_id,
					"reason": "no_missing_rows",
					"expected_total": flt(expected_total, 3),
					"already_created_total": flt(existing_total, 3),
				}
			)
			continue

		# Build Manufacture entry exactly like SPR flow, but only for missing rows.
		se = frappe.new_doc("Stock Entry")
		se.flags.ignore_duplicate_for_work_order = True
		se.company = wo_doc.company
		se.posting_date = spr.run_date or today()
		se.posting_time = nowtime()
		se.set_posting_time = 1
		se.stock_entry_type = spr._manufacture_stock_entry_type_name()
		se.purpose = "Manufacture"
		se.work_order = None
		se.production_item = wo_doc.production_item
		se.fg_completed_qty = missing_total
		se.from_bom = 1
		se.bom_no = wo_doc.bom_no
		se.use_multi_level_bom = wo_doc.use_multi_level_bom
		se.wip_warehouse = wo_doc.wip_warehouse
		se.to_warehouse = wo_doc.fg_warehouse
		spr._set_stock_entry_spr_link(se)
		spr._set_stock_entry_unit(se, wo_doc)
		se.get_items()
		for d in se.items or []:
			if d.item_code and not d.get("t_warehouse"):
				d.s_warehouse = wo_doc.wip_warehouse
		spr._strip_finished_goods_from_stock_entry(se)
		spr._append_manufacture_fg_from_spr_rolls(se, wo_doc, missing_rows)
		se.insert()
		frappe.db.set_value("Stock Entry", se.name, "work_order", wo_id, update_modified=False)
		spr._apply_order_code_to_submitted_stock_entry(se.name)
		if do_submit:
			se.reload()
			se.flags.ignore_duplicate_for_work_order = True
			se.submit()
		created.append(
			{
				"work_order": wo_id,
				"stock_entry": se.name,
				"rows_added": len(missing_rows),
				"qty": flt(missing_total, 3),
				"docstatus": 1 if do_submit else 0,
			}
		)

	# Final sync
	dummy = frappe.new_doc("Shaft Production Run")
	for wo_id in wo_rows.keys():
		if frappe.db.exists("Work Order", wo_id):
			dummy._sync_work_order_produced_qty_from_submitted_manufacture(wo_id)
			dummy._sync_work_order_required_item_progress(wo_id)
	dummy._sync_production_plan_progress_from_work_orders(_cstr(spr.get("production_plan")))

	return {
		"status": "ok",
		"shaft_production_run": spr.name,
		"production_plan": _cstr(spr.get("production_plan")),
		"created_count": len(created),
		"created": created,
		"skipped_count": len(skipped),
		"skipped": skipped[:100],
	}


def _spr_manufacture_fg_line_posted_qty(se_name: str, fg_line, item_code: str = "", warehouse: str = "") -> float:
	"""Positive FG qty already posted for a manufacture Stock Entry line (v14 + v16 bundle paths)."""
	se_name = _cstr(se_name).strip()
	if not se_name or not fg_line:
		return 0.0
	item_code = _cstr(item_code or fg_line.get("item_code")).strip()
	warehouse = _cstr(warehouse or fg_line.get("t_warehouse")).strip()
	fg_detail_name = _cstr(getattr(fg_line, "name", None) or fg_line.get("name", "")).strip()
	if not item_code:
		return 0.0

	clauses = [
		"IFNULL(is_cancelled, 0) = 0",
		"voucher_type = 'Stock Entry'",
		"voucher_no = %s",
		"IFNULL(item_code, '') = %s",
		"actual_qty > 0",
	]
	params: list = [se_name, item_code]
	if warehouse:
		clauses.append("IFNULL(warehouse, '') = %s")
		params.append(warehouse)
	if fg_detail_name and frappe.db.has_column("Stock Ledger Entry", "voucher_detail_no"):
		clauses.append("IFNULL(voucher_detail_no, '') = %s")
		params.append(fg_detail_name)
	try:
		return flt(
			frappe.db.sql(
				f"""
				SELECT IFNULL(SUM(actual_qty), 0)
				FROM `tabStock Ledger Entry`
				WHERE {' AND '.join(clauses)}
				""",
				tuple(params),
			)[0][0]
			or 0
		)
	except Exception:
		return 0.0


def _spr_ensure_fg_serial_batch_bundle(se_doc, fg_line, batch_no: str, fg_qty: float, fg_wh: str) -> str:
	"""Ensure submitted manufacture FG line has a populated Serial and Batch Bundle."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no or not se_doc or not fg_line:
		return ""
	if not frappe.db.has_column("Stock Entry Detail", "serial_and_batch_bundle"):
		return ""
	fg_detail_name = _cstr(getattr(fg_line, "name", None) or fg_line.get("name", "")).strip()
	if not fg_detail_name:
		return ""
	fg_qty = flt(fg_qty or fg_line.get("transfer_qty") or fg_line.get("qty"))
	fg_wh = _cstr(fg_wh or fg_line.get("t_warehouse") or se_doc.get("to_warehouse")).strip()
	item_code = _cstr(fg_line.get("item_code")).strip()
	if not item_code or fg_qty <= 0:
		return ""

	_spr_create_missing_bundle_for_fg(se_doc, fg_line, batch_no, fg_qty, fg_wh)
	bundle_name = _cstr(
		frappe.db.get_value("Stock Entry Detail", fg_detail_name, "serial_and_batch_bundle")
	).strip()
	if not bundle_name:
		return ""

	entry_qty = flt(
		frappe.db.sql(
			"""
			SELECT IFNULL(SUM(qty), 0)
			FROM `tabSerial and Batch Entry`
			WHERE parent = %s AND IFNULL(batch_no, '') = %s
			""",
			(bundle_name, batch_no),
		)[0][0]
		or 0
	)
	if entry_qty <= 0:
		try:
			child = frappe.get_doc(
				{
					"doctype": "Serial and Batch Entry",
					"parent": bundle_name,
					"parenttype": "Serial and Batch Bundle",
					"parentfield": "entries",
					"batch_no": batch_no,
					"qty": fg_qty,
					"warehouse": fg_wh,
				}
			)
			child.flags.ignore_permissions = True
			child.flags.ignore_mandatory = True
			child.insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR bundle entry repair:{bundle_name}")

	if not cint(frappe.db.get_value("Serial and Batch Bundle", bundle_name, "docstatus") or 0):
		frappe.db.sql("UPDATE `tabSerial and Batch Bundle` SET docstatus=1 WHERE name=%s", bundle_name)
		frappe.db.sql("UPDATE `tabSerial and Batch Entry` SET docstatus=1 WHERE parent=%s", bundle_name)

	frappe.db.sql(
		"""UPDATE `tabStock Ledger Entry`
		SET serial_and_batch_bundle = %s
		WHERE voucher_type = 'Stock Entry'
		  AND voucher_no = %s
		  AND voucher_detail_no = %s
		  AND actual_qty > 0
		  AND IFNULL(is_cancelled, 0) = 0""",
		(bundle_name, se_doc.name, fg_detail_name),
	)
	return bundle_name


@frappe.whitelist()
def spr_repair_broken_fg_batch_stock(shaft_production_run: str, submit_entry: int = 1):
	"""
	Repair legacy SPR Manufacture entries whose FG batch rows exist but never posted positive stock ledger.

	Creates a corrective Material Receipt for only the missing FG batch quantities, without consuming RM again.
	"""
	spr_name = _cstr(shaft_production_run)
	if not spr_name:
		frappe.throw(_("Shaft Production Run is required"))
	if not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run {0} not found").format(spr_name))

	spr = frappe.get_doc("Shaft Production Run", spr_name)
	se_names = [
		x.strip() for x in _cstr(getattr(spr, "manufacturing_entries", "")).split(",") if x and x.strip()
	]
	if not se_names:
		se_names = spr._get_existing_submitted_manufacture_entries_for_spr()
	if not se_names:
		return {"status": "ok", "shaft_production_run": spr.name, "created": [], "skipped": [{"reason": "no_manufacture_entries"}]}

	repair_rows = []
	skipped = []
	for se_name in se_names:
		if not frappe.db.exists("Stock Entry", se_name):
			skipped.append({"stock_entry": se_name, "reason": "stock_entry_not_found"})
			continue
		se = frappe.get_doc("Stock Entry", se_name)
		if _cstr(se.get("purpose")) != "Manufacture" or cint(se.get("docstatus")) != 1:
			skipped.append({"stock_entry": se_name, "reason": "not_submitted_manufacture"})
			continue
		for row in se.items or []:
			if cint(row.get("is_finished_item")) != 1:
				continue
			batch_no = _cstr(row.get("batch_no"))
			item_code = _cstr(row.get("item_code"))
			target_wh = _cstr(row.get("t_warehouse") or getattr(se, "to_warehouse", None))
			qty = flt(row.get("qty"))
			if not batch_no or not item_code or qty <= 0 or not target_wh:
				continue
			posted_qty = _spr_manufacture_fg_line_posted_qty(se.name, row, item_code, target_wh)
			missing_qty = flt(qty - posted_qty, 6)
			if missing_qty <= 1e-6:
				skipped.append({"stock_entry": se.name, "batch_no": batch_no, "reason": "already_has_fg_sle"})
				continue
			repair_rows.append(
				{
					"source_manufacture": se.name,
					"work_order": _cstr(se.get("work_order")),
					"item_code": item_code,
					"item_name": row.get("item_name"),
					"batch_no": batch_no,
					"qty": missing_qty,
					"uom": row.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom") or "Kg",
					"stock_uom": row.get("stock_uom") or row.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom") or "Kg",
					"conversion_factor": flt(row.get("conversion_factor") or 1),
					"basic_rate": flt(row.get("basic_rate") or row.get("valuation_rate") or 0),
					"t_warehouse": target_wh,
				}
			)

	if not repair_rows:
		return {
			"status": "ok",
			"shaft_production_run": spr.name,
			"created": [],
			"skipped_count": len(skipped),
			"skipped": skipped[:200],
		}

	receipt = frappe.new_doc("Stock Entry")
	receipt.company = repair_rows[0]["t_warehouse"] and frappe.db.get_value("Warehouse", repair_rows[0]["t_warehouse"], "company")
	receipt.posting_date = today()
	receipt.posting_time = nowtime()
	receipt.set_posting_time = 1
	receipt.stock_entry_type = spr._stock_entry_type_name_for_purpose("Material Receipt")
	receipt.purpose = "Material Receipt"
	receipt.remarks = _("SPR FG batch repair for {0}").format(spr.name)
	spr._set_stock_entry_spr_link(receipt)
	spr._set_stock_entry_unit(receipt)

	for r in repair_rows:
		row = {
			"item_code": r["item_code"],
			"item_name": r.get("item_name"),
			"qty": r["qty"],
			"transfer_qty": r["qty"],
			"uom": r["uom"],
			"stock_uom": r["stock_uom"],
			"conversion_factor": r["conversion_factor"],
			"t_warehouse": r["t_warehouse"],
			"batch_no": r["batch_no"],
			"basic_rate": r["basic_rate"],
		}
		if flt(r["basic_rate"]) <= 0:
			row["allow_zero_valuation_rate"] = 1
		receipt.append("items", row)

	receipt.insert()
	spr._persist_stock_entry_spr_reference_db(receipt.name)
	spr._apply_order_code_to_submitted_stock_entry(receipt.name)
	if cint(submit_entry) == 1:
		receipt.submit()

	spr._refresh_batch_qty_for_codes([r["batch_no"] for r in repair_rows])

	return {
		"status": "ok",
		"shaft_production_run": spr.name,
		"repair_stock_entry": receipt.name,
		"repair_docstatus": cint(receipt.docstatus),
		"repaired_batch_count": len(repair_rows),
		"repaired_batches": repair_rows[:200],
		"skipped_count": len(skipped),
		"skipped": skipped[:200],
	}


@frappe.whitelist()
def spr_get_submit_summary(shaft_production_run: str):
	"""Return post-submit manufacture summary HTML for client display after reload."""
	spr_name = _cstr(shaft_production_run)
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run {0} not found").format(spr_name))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if cint(spr.docstatus) != 1:
		return {"html": "", "title": ""}
	wo_groups: dict = {}
	for row in spr.items or []:
		wo = _cstr(row.get("work_order") or row.get("wo_id"))
		if wo:
			wo_groups.setdefault(wo, []).append(row)
	created_by_wo = spr._spr_group_manufacture_entries_by_wo(
		spr._get_existing_submitted_manufacture_entries_for_spr(), wo_groups
	)
	html = spr._spr_build_submit_summary_html(wo_groups, created_by_wo)
	return {
		"html": html,
		"title": _("Manufacture Summary — {0}").format(spr_name),
	}


@frappe.whitelist()
def spr_sync_batches_to_manufacture_entries(shaft_production_run: str):
	"""Sync SPR roll batches to Manufacture FG lines and activate Batch masters.

	Optimized for large SPRs: uses SQL bulk reads, batch-by-batch matching, auto RM
	transfer when backfill needs stock, and Material Receipt repair for empty batches.
	Never blocks the whole sync on one WO shortage — logs and continues.
	"""
	spr_name = _cstr(shaft_production_run)
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run {0} not found").format(spr_name))
	spr = frappe.get_doc("Shaft Production Run", spr_name)
	if cint(spr.docstatus) == 2:
		frappe.throw(_("Cannot sync batches on a cancelled SPR."))

	frappe.db.auto_commit_on_many_writes = 1

	# 1) Ensure Batch masters exist with WO / weight custom fields from SPR rows.
	spr.sync_batch_custom_fields()

	# WO → batch_id → SPR row (primary match key)
	wo_batch_rows: dict[str, dict[str, object]] = defaultdict(dict)
	wo_item_rows: dict[str, list] = defaultdict(list)
	for row in spr.items or []:
		wo = _cstr(row.get("work_order") or row.get("wo_id"))
		bn = _cstr(getattr(row, "batch_no", "")).strip()
		if not wo or flt(spr._row_fg_qty(row)) <= 0:
			continue
		wo_item_rows[wo].append(row)
		if bn:
			wo_batch_rows[wo][bn] = row

	se_names = spr._get_existing_submitted_manufacture_entries_for_spr()
	submitted_drafts = spr._spr_submit_all_draft_manufactures_for_spr() or []
	if submitted_drafts:
		se_names = sorted(set(list(se_names) + submitted_drafts))
	early_backfill: list = []
	if not se_names:
		try:
			early_backfill = spr._spr_sync_backfill_missing_manufacture() or []
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR batch sync early backfill:{spr_name}")
		se_names = spr._get_existing_submitted_manufacture_entries_for_spr()
	if not se_names:
		all_batch_codes: set = set()
		company = _spr_company_from_doc(spr)
		for row in spr.items or []:
			bn_raw = _cstr(getattr(row, "batch_no", "")).strip()
			ic = _cstr(getattr(row, "item_code", "")).strip()
			if bn_raw and ic:
				try:
					link = _cstr(spr._get_batch_link_name_for_stock_entry(bn_raw, ic, company, row))
				except Exception:
					link = bn_raw
				if link:
					all_batch_codes.add(link)
		spr._refresh_batch_qty_for_codes(list(all_batch_codes))
		try:
			frappe.db.commit()
		except Exception:
			pass
		return {
			"status": "ok",
			"shaft_production_run": spr_name,
			"created_batches": 0,
			"updated_fg_lines": 0,
			"activated_batches": sum(1 for bn in all_batch_codes if _spr_batch_is_active(bn)),
			"force_activated": 0,
			"repaired_batches": 0,
			"backfilled_entries": len(early_backfill),
			"backfill_errors": [],
			"updated_details": [],
			"skipped_count": 0,
			"message": _("Manufacture backfill attempted; no linked entries yet."),
		}

	created_batches: list = []
	updated_lines: list = []
	skipped: list = []
	all_batch_codes: set = set()
	posted_batch_keys: set = set()  # (wo, batch_link) already on a manufacture FG line

	# Bulk-load FG lines (avoid get_doc per SE on large SPRs)
	fg_rows = frappe.db.sql(
		"""
		SELECT sed.name, sed.parent, sed.idx, sed.item_code,
		       IFNULL(sed.batch_no, '') AS batch_no,
		       IFNULL(sed.qty, 0) AS qty,
		       IFNULL(sed.t_warehouse, '') AS t_warehouse,
		       IFNULL(se.work_order, '') AS work_order,
		       IFNULL(se.company, '') AS company
		FROM `tabStock Entry Detail` sed
		INNER JOIN `tabStock Entry` se ON se.name = sed.parent
		WHERE sed.parent IN %(parents)s
		  AND IFNULL(se.docstatus, 0) = 1
		  AND IFNULL(se.purpose, '') = 'Manufacture'
		  AND IFNULL(sed.is_finished_item, 0) = 1
		ORDER BY se.work_order, sed.parent, sed.idx
		""",
		{"parents": tuple(se_names)},
		as_dict=True,
	) or []

	se_meta_cache: dict = {}

	def _se_doc_light(se_name: str):
		if se_name not in se_meta_cache:
			se_meta_cache[se_name] = frappe.get_doc("Stock Entry", se_name)
		return se_meta_cache[se_name]

	def _resolve_batch_link(bn_raw: str, item_code: str, company: str, spr_row) -> str:
		if not bn_raw:
			return ""
		if frappe.db.exists("Batch", bn_raw):
			return bn_raw
		try:
			return _cstr(spr._get_batch_link_name_for_stock_entry(bn_raw, item_code, company, spr_row))
		except Exception:
			return bn_raw

	def _activate_fg_line(fg_row: dict, batch_link: str, spr_row=None) -> float:
		se_doc = _se_doc_light(fg_row["parent"])
		fg_line = frappe._dict(
			{
				"name": fg_row["name"],
				"item_code": fg_row["item_code"],
				"qty": fg_row["qty"],
				"transfer_qty": fg_row["qty"],
				"t_warehouse": fg_row["t_warehouse"],
				"batch_no": batch_link,
			}
		)
		fallback = flt(fg_row["qty"])
		if spr_row is not None:
			fallback = flt(spr._row_fg_qty(spr_row)) or fallback
		return _spr_activate_batch_from_manufacture(batch_link, fg_line, se_doc, fallback_qty=fallback)

	# Index existing posted batches per WO
	for fg in fg_rows:
		wo = _cstr(fg.get("work_order"))
		bn = _cstr(fg.get("batch_no")).strip()
		if wo and bn:
			posted_batch_keys.add((wo, bn))

	# 2) Patch FG lines: match by batch_no first, then qty fallback per WO
	used_batches_by_wo: dict[str, set] = defaultdict(set)
	for wo, batch_map in wo_batch_rows.items():
		used_batches_by_wo[wo] = {
			bn for (w, bn) in posted_batch_keys if w == wo
		}

	for fg in fg_rows:
		se_name = fg["parent"]
		wo = _cstr(fg.get("work_order"))
		item_code = _cstr(fg.get("item_code"))
		company = _cstr(fg.get("company"))
		existing_bn = _cstr(fg.get("batch_no")).strip()
		batch_map = wo_batch_rows.get(wo) or {}

		if existing_bn:
			spr_row = batch_map.get(existing_bn)
			if not spr_row:
				for bn_raw, row in batch_map.items():
					link = _resolve_batch_link(bn_raw, item_code, company, row)
					if link == existing_bn or bn_raw == existing_bn:
						spr_row = row
						break
			posted_qty = _spr_manufacture_fg_line_posted_qty(se_name, fg, item_code, _cstr(fg.get("t_warehouse")))
			activated = _activate_fg_line(fg, existing_bn, spr_row)
			all_batch_codes.add(existing_bn)
			used_batches_by_wo[wo].add(existing_bn)
			if posted_qty > 0:
				skipped.append({
					"stock_entry": se_name,
					"line_idx": fg["idx"],
					"reason": "already_has_batch",
					"batch_no": existing_bn,
					"batch_qty": activated or flt(spr._row_fg_qty(spr_row) if spr_row else 0),
				})
				continue
			updated_lines.append({
				"stock_entry": se_name,
				"line_idx": fg["idx"],
				"batch_no": existing_bn,
				"batch_qty": activated or flt(spr._row_fg_qty(spr_row) if spr_row else 0),
				"reason": "repaired_missing_sle",
			})
			continue

		fg_qty = flt(fg.get("qty"))
		matched_bn = ""
		matched_row = None
		# Prefer unmatched SPR batch with same qty for this WO
		qty_key = f"{flt(fg_qty, 6)}"
		for bn_raw, row in batch_map.items():
			if bn_raw in used_batches_by_wo.get(wo, set()):
				continue
			link = _resolve_batch_link(bn_raw, item_code, company, row)
			if link in used_batches_by_wo.get(wo, set()):
				continue
			if f"{flt(spr._row_fg_qty(row), 6)}" == qty_key:
				matched_bn = bn_raw
				matched_row = row
				break
		if not matched_row:
			for bn_raw, row in batch_map.items():
				link = _resolve_batch_link(bn_raw, item_code, company, row)
				if bn_raw not in used_batches_by_wo.get(wo, set()) and link not in used_batches_by_wo.get(wo, set()):
					matched_bn = bn_raw
					matched_row = row
					break
		if not matched_row:
			skipped.append({"stock_entry": se_name, "line_idx": fg["idx"], "reason": "no_matching_roll_batch"})
			continue

		batch_link = _resolve_batch_link(matched_bn, item_code, company, matched_row)
		if not batch_link:
			skipped.append({"stock_entry": se_name, "line_idx": fg["idx"], "batch_no": matched_bn, "reason": "batch_create_failed"})
			continue

		created_batches.append({"batch_no": batch_link, "item_code": item_code})
		all_batch_codes.add(batch_link)
		used_batches_by_wo[wo].add(batch_link)
		used_batches_by_wo[wo].add(matched_bn)
		try:
			updates = {"batch_no": batch_link}
			line_meta = frappe.get_meta("Stock Entry Detail")
			if line_meta.has_field("use_serial_batch_fields"):
				updates["use_serial_batch_fields"] = 1
			frappe.db.set_value("Stock Entry Detail", fg["name"], updates, update_modified=False)
			activated = _activate_fg_line(fg, batch_link, matched_row)
			updated_lines.append({
				"stock_entry": se_name,
				"line_idx": fg["idx"],
				"batch_no": batch_link,
				"batch_qty": activated or flt(spr._row_fg_qty(matched_row)),
			})
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR batch sync line:{spr_name}")

	# 2b) Ensure Serial and Batch Bundle on every existing Manufacture FG line.
	for se_name in se_names:
		try:
			spr._spr_ensure_manufacture_fg_bundles(se_name)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"SPR batch sync bundle:{spr_name}:{se_name}",
			)

	# 3) Repair FG batches that have manufacture lines but no positive SLE (no RM re-consumed)
	repair_res = {}
	try:
		repair_res = spr_repair_broken_fg_batch_stock(spr_name, submit_entry=1) or {}
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"SPR batch sync repair:{spr_name}")

	# 4) Backfill leftover rolls that are not yet on a Manufacture STE (draft or submitted SPR).
	backfill_created: list = []
	backfill_errors: list = []
	try:
		backfill_created = spr._spr_sync_backfill_missing_manufacture() or []
	except Exception as exc:
		backfill_errors.append(_cstr(exc))
		frappe.log_error(frappe.get_traceback(), f"SPR batch sync backfill:{spr_name}")

	# 5) Refresh batch qty from stock ledger only (no cosmetic force-activate without Manufacture)
	company = _spr_company_from_doc(spr)
	for row in spr.items or []:
		bn_raw = _cstr(getattr(row, "batch_no", "")).strip()
		ic = _cstr(getattr(row, "item_code", "")).strip()
		if not bn_raw or not ic:
			continue
		link = _resolve_batch_link(bn_raw, ic, company, row)
		if link:
			all_batch_codes.add(link)

	spr._refresh_batch_qty_for_codes(list(all_batch_codes))

	activated_count = sum(1 for bn in all_batch_codes if _spr_batch_is_active(bn))

	try:
		frappe.db.commit()
	except Exception:
		pass

	return {
		"status": "ok",
		"shaft_production_run": spr_name,
		"created_batches": len(created_batches),
		"updated_fg_lines": len(updated_lines),
		"activated_batches": activated_count,
		"force_activated": 0,
		"repaired_batches": cint((repair_res or {}).get("repaired_batch_count") or 0),
		"backfilled_entries": len(backfill_created),
		"backfill_errors": backfill_errors[:5],
		"updated_details": updated_lines[:100],
		"skipped_count": len(skipped),
		"skipped": skipped[:50],
	}


def _spr_set_batch_qty_on_master(batch_no: str, qty: float) -> None:
	"""Update Batch.batch_qty and status using direct SQL to bypass any Frappe hook that
	recomputes and resets those fields back to Empty from the stock ledger."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no or not frappe.db.exists("Batch", batch_no):
		return
	qty = flt(qty)
	status = "Active" if qty > 0 else "Empty"
	has_qty = frappe.db.has_column("Batch", "batch_qty")
	has_status = frappe.db.has_column("Batch", "status")
	if has_qty and has_status:
		frappe.db.sql(
			"UPDATE `tabBatch` SET batch_qty = %s, status = %s WHERE name = %s",
			(qty, status, batch_no),
		)
	elif has_qty:
		frappe.db.sql(
			"UPDATE `tabBatch` SET batch_qty = %s WHERE name = %s",
			(qty, batch_no),
		)
	elif has_status:
		frappe.db.sql(
			"UPDATE `tabBatch` SET status = %s WHERE name = %s",
			(status, batch_no),
		)


def _spr_batch_is_active(batch_no: str) -> bool:
	"""True when batch master shows positive qty (status column optional)."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no or not frappe.db.exists("Batch", batch_no):
		return False
	if frappe.db.has_column("Batch", "batch_qty"):
		return flt(frappe.db.get_value("Batch", batch_no, "batch_qty") or 0) > 0
	if frappe.db.has_column("Batch", "status"):
		return _cstr(frappe.db.get_value("Batch", batch_no, "status")) == "Active"
	return False


def _spr_batch_sle_qty(batch_no: str, item_code: str = "", warehouse: str = "") -> float:
	"""Net warehouse qty for a batch (SLE.batch_no or Serial and Batch Bundle)."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no:
		return 0.0

	clauses = [
		"IFNULL(is_cancelled, 0) = 0",
		"IFNULL(batch_no, '') = %s",
	]
	params: list = [batch_no]
	if item_code:
		clauses.append("IFNULL(item_code, '') = %s")
		params.append(_cstr(item_code).strip())
	if warehouse:
		clauses.append("IFNULL(warehouse, '') = %s")
		params.append(_cstr(warehouse).strip())
	sle_qty = flt(
		frappe.db.sql(
			f"""
			SELECT IFNULL(SUM(actual_qty), 0)
			FROM `tabStock Ledger Entry`
			WHERE {' AND '.join(clauses)}
			""",
			tuple(params),
		)[0][0]
		or 0
	)
	if abs(sle_qty) > 1e-9:
		return sle_qty

	if not frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
		return 0.0
	try:
		bundle_qty = flt(
			frappe.db.sql(
				"""
				SELECT IFNULL(SUM(
					CASE
						WHEN IFNULL(sle.actual_qty, 0) < 0 THEN -ABS(IFNULL(sbe.qty, 0))
						ELSE ABS(IFNULL(sbe.qty, 0))
					END
				), 0)
				FROM `tabStock Ledger Entry` sle
				INNER JOIN `tabSerial and Batch Entry` sbe
					ON sbe.parent = sle.serial_and_batch_bundle
				WHERE IFNULL(sle.is_cancelled, 0) = 0
				  AND IFNULL(sle.serial_and_batch_bundle, '') != ''
				  AND IFNULL(sbe.batch_no, '') = %s
				""",
				(batch_no,),
			)[0][0]
			or 0
		)
		return bundle_qty
	except Exception:
		return 0.0


def _spr_activate_batch_from_manufacture(batch_no: str, fg_line, se_doc, fallback_qty: float = 0) -> float:
	"""Patch FG SLE + set Batch qty/status from manufacture FG line."""
	batch_no = _cstr(batch_no).strip()
	if not batch_no or not frappe.db.exists("Batch", batch_no):
		return 0.0
	se_name = _cstr(getattr(se_doc, "name", None))
	fg_wh = _cstr(fg_line.get("t_warehouse") or se_doc.get("to_warehouse")).strip()
	item_code = _cstr(fg_line.get("item_code")).strip()
	fg_qty = flt(fg_line.get("transfer_qty") or fg_line.get("qty")) or flt(fallback_qty)

	_spr_patch_sle_batch_for_fg_line(se_name, fg_line, batch_no, fg_wh)

	if frappe.db.has_column("Stock Ledger Entry", "serial_and_batch_bundle"):
		_spr_create_missing_bundle_for_fg(se_doc, fg_line, batch_no, fg_qty, fg_wh)
		_spr_ensure_fg_serial_batch_bundle(se_doc, fg_line, batch_no, fg_qty, fg_wh)

	sle_qty = _spr_batch_sle_qty(batch_no, item_code=item_code, warehouse=fg_wh)
	if sle_qty <= 0:
		sle_qty = _spr_batch_sle_qty(batch_no, item_code=item_code)
	if sle_qty <= 0:
		sle_qty = _spr_batch_sle_qty(batch_no)

	final_qty = sle_qty if sle_qty > 0 else fg_qty
	_spr_set_batch_qty_on_master(batch_no, final_qty)
	return final_qty if final_qty > 0 else 0.0


def _spr_create_missing_bundle_for_fg(se_doc, fg_line, batch_no: str, fg_qty: float, fg_wh: str) -> None:
	"""Manually create and link a Serial and Batch Bundle for the FG line to avoid resubmitting the Stock Entry."""
	try:
		fg_detail_name = getattr(fg_line, "name", None) or fg_line.get("name", "")
		bundle_name = frappe.db.get_value("Stock Entry Detail", fg_detail_name, "serial_and_batch_bundle")
		
		if bundle_name:
			docstatus = frappe.db.get_value("Serial and Batch Bundle", bundle_name, "docstatus")
			if not docstatus:
				frappe.db.sql("UPDATE `tabSerial and Batch Bundle` SET docstatus=1 WHERE name=%s", bundle_name)
				frappe.db.sql("UPDATE `tabSerial and Batch Entry` SET docstatus=1 WHERE parent=%s", bundle_name)
		else:
			frappe.db.set_value("Stock Entry Detail", fg_detail_name, "use_serial_batch_fields", 1, update_modified=False)
			bundle = frappe.get_doc({
				"doctype": "Serial and Batch Bundle",
				"item_code": fg_line.get("item_code"),
				"warehouse": fg_wh,
				"voucher_type": "Stock Entry",
				"voucher_no": se_doc.name,
				"voucher_detail_no": fg_detail_name,
				"type_of_transaction": "Inward",
				"posting_date": se_doc.posting_date,
				"posting_time": se_doc.posting_time,
				"has_batch_no": 1,
				"company": getattr(se_doc, "company", ""),
				"entries": [
					{
						"batch_no": batch_no,
						"qty": fg_qty,
						"warehouse": fg_wh
					}
				]
			})
			bundle.flags.ignore_permissions = True
			bundle.flags.ignore_mandatory = True
			bundle.flags.ignore_validate = True
			bundle.insert()
			
			bundle_name = bundle.name
			frappe.db.sql("UPDATE `tabSerial and Batch Bundle` SET docstatus=1 WHERE name=%s", bundle_name)
			frappe.db.sql("UPDATE `tabSerial and Batch Entry` SET docstatus=1 WHERE parent=%s", bundle_name)
			frappe.db.set_value("Stock Entry Detail", fg_detail_name, "serial_and_batch_bundle", bundle_name, update_modified=False)
			
		frappe.db.sql("""UPDATE `tabStock Ledger Entry` 
			SET serial_and_batch_bundle = %s 
			WHERE voucher_type = 'Stock Entry' 
			  AND voucher_no = %s 
			  AND voucher_detail_no = %s
			  AND actual_qty > 0
			  AND IFNULL(is_cancelled, 0) = 0""", (bundle_name, se_doc.name, fg_detail_name))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "SPR bundle creation failed")


def _spr_patch_sle_batch_for_fg_line(
	se_name: str, fg_line, batch_no: str, fg_warehouse: str = ""
) -> int:
	"""Update inbound FG Stock Ledger Entry rows to include batch_no."""
	if not se_name or not fg_line or not batch_no:
		return 0
	item_code = _cstr(fg_line.get("item_code")).strip()
	if not item_code:
		return 0
	fg_detail_name = _cstr(getattr(fg_line, "name", None) or fg_line.get("name", "")).strip()
	if not fg_warehouse:
		fg_warehouse = _cstr(
			fg_line.get("t_warehouse") or frappe.db.get_value("Stock Entry", se_name, "to_warehouse")
		).strip()

	has_vdn = frappe.db.has_column("Stock Ledger Entry", "voucher_detail_no")
	fg_qty = flt(getattr(fg_line, "transfer_qty", None) or getattr(fg_line, "qty", 0))

	patched = 0
	if fg_detail_name and has_vdn:
		try:
			params = [batch_no, se_name, fg_detail_name, batch_no]
			wh_sql = ""
			if fg_warehouse:
				wh_sql = " AND IFNULL(warehouse, '') = %s"
				params.append(fg_warehouse)
			frappe.db.sql(
				f"""UPDATE `tabStock Ledger Entry`
				SET batch_no = %s
				WHERE voucher_type = 'Stock Entry'
				  AND voucher_no = %s
				  AND voucher_detail_no = %s
				  AND actual_qty > 0
				  AND IFNULL(is_cancelled, 0) = 0
				  AND (IFNULL(batch_no, '') = '' OR IFNULL(batch_no, '') = %s){wh_sql}""",
				tuple(params),
			)
			patched = frappe.db.sql("SELECT ROW_COUNT()")[0][0] or 0
		except Exception:
			patched = 0

	if not patched:
		try:
			params = [batch_no, se_name, item_code]
			qty_sql = ""
			if fg_qty > 0:
				qty_sql = " AND ROUND(actual_qty, 3) = %s"
				params.append(round(fg_qty, 3))
			params.append(batch_no)
			wh_sql = ""
			if fg_warehouse:
				wh_sql = " AND IFNULL(warehouse, '') = %s"
				params.append(fg_warehouse)
			frappe.db.sql(
				f"""UPDATE `tabStock Ledger Entry`
				SET batch_no = %s
				WHERE voucher_type = 'Stock Entry'
				  AND voucher_no = %s
				  AND IFNULL(item_code, '') = %s
				  {qty_sql}
				  AND actual_qty > 0
				  AND IFNULL(is_cancelled, 0) = 0
				  AND (IFNULL(batch_no, '') = '' OR IFNULL(batch_no, '') = %s)
				  {wh_sql}
				LIMIT 1""",
				tuple(params),
			)
			patched = frappe.db.sql("SELECT ROW_COUNT()")[0][0] or 0
		except Exception:
			patched = 0

	if not patched and fg_qty > 0:
		try:
			params2 = [batch_no, se_name, item_code, batch_no]
			wh_sql = ""
			if fg_warehouse:
				wh_sql = " AND IFNULL(warehouse, '') = %s"
				params2.append(fg_warehouse)
			frappe.db.sql(
				f"""UPDATE `tabStock Ledger Entry`
				SET batch_no = %s
				WHERE voucher_type = 'Stock Entry'
				  AND voucher_no = %s
				  AND IFNULL(item_code, '') = %s
				  AND actual_qty > 0
				  AND IFNULL(is_cancelled, 0) = 0
				  AND (IFNULL(batch_no, '') = '' OR IFNULL(batch_no, '') = %s)
				  {wh_sql}
				LIMIT 1""",
				tuple(params2),
			)
			patched = frappe.db.sql("SELECT ROW_COUNT()")[0][0] or 0
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR batch sync SLE patch:{se_name}")
			patched = 0

	return patched


def _spr_activate_batches(batch_codes: list[str]) -> int:
	"""Set Batch status to Active and recompute batch_qty from SLE. Returns count activated."""
	activated = 0
	for bn in {_cstr(x).strip() for x in (batch_codes or []) if _cstr(x).strip()}:
		if not frappe.db.exists("Batch", bn):
			continue
		try:
			qty = flt(
				frappe.db.sql(
					"""
					SELECT IFNULL(SUM(actual_qty), 0)
					FROM `tabStock Ledger Entry`
					WHERE IFNULL(is_cancelled, 0) = 0
					  AND IFNULL(batch_no, '') = %s
					""",
					(bn,),
				)[0][0] or 0
			)
			new_status = "Active" if qty > 0 else "Empty"
			_spr_set_batch_qty_on_master(bn, qty)
			if new_status == "Active":
				activated += 1
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"SPR batch activate:{bn}")
	return activated


@frappe.whitelist()
def spr_set_bundle_packaging_on_submit(shaft_production_run, enabled=0):
	"""Toggle whether SPR submit posts combined FG from Bundle Stickers (testing / rollout)."""
	_spr_require_saved(shaft_production_run)
	if not frappe.db.has_column("Shaft Production Run", "custom_use_bundle_packaging_on_submit"):
		frappe.throw(
			_("Field custom_use_bundle_packaging_on_submit is missing. Run bench migrate on Production Planning app."),
			title=_("Migrate required"),
		)
	on = 1 if cint(enabled) else 0
	spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
	if cint(spr.docstatus) != 0:
		frappe.throw(_("Cannot change bundle packaging mode after submit."))
	spr.custom_use_bundle_packaging_on_submit = on
	spr.save(ignore_permissions=True)
	mode = _("ON — Bundle Stickers combined FG first, then roll lines") if on else _(
		"OFF — Each roll line is its own FG (Bundle Stickers ignored)"
	)
	return {"enabled": on, "mode_label": mode}


@frappe.whitelist()
def spr_get_bundle_packaging_on_submit_status(shaft_production_run):
	"""Return current bundle-packaging toggle for SPR toolbar."""
	if not shaft_production_run or not frappe.db.exists("Shaft Production Run", shaft_production_run):
		return {"enabled": 0, "available": False}
	if not frappe.db.has_column("Shaft Production Run", "custom_use_bundle_packaging_on_submit"):
		return {"enabled": 0, "available": False}
	enabled = cint(frappe.db.get_value("Shaft Production Run", shaft_production_run, "custom_use_bundle_packaging_on_submit") or 0)
	return {"enabled": enabled, "available": True}


def _spr_catalog_widths_for_job_row(sj, roll_widths: list | None = None, pp_name: str | None = None) -> list[float]:
	"""Width options for bundle catalog without loading full SPR + all roll rows."""
	widths: list[float] = []
	sj = frappe._dict(sj) if sj else frappe._dict()
	comb = _cstr(sj.get("combination") or "")
	for w in _parse_combination_widths_inches(comb):
		fw = flt(w)
		if fw > 0 and fw not in widths:
			widths.append(fw)
	for fw in roll_widths or []:
		fw = flt(fw)
		if fw > 0 and fw not in widths:
			widths.append(fw)
	explicit = _cstr(sj.get("work_orders") or "")
	if explicit:
		for raw in explicit.replace("\n", ",").split(","):
			wo_name = _cstr(raw).strip()
			if not wo_name or not frappe.db.exists("Work Order", wo_name):
				continue
			ic = frappe.db.get_value("Work Order", wo_name, "production_item")
			if not ic:
				continue
			_g, w_item = parse_item_code(ic)
			fw = flt(w_item)
			if fw > 0 and fw not in widths:
				widths.append(fw)
	if not widths and pp_name:
		try:
			wos = _resolve_wos_for_pp_job_row(
				pp_name,
				ppi=_cstr(sj.get("production_plan_item") or "") or None,
				job_id=_cstr(_spr_job_id(sj)) or None,
				row_index=None,
				combination=comb or None,
				job_gsm=cint(sj.get("gsm")) if sj.get("gsm") else None,
			) or []
			for wo in wos:
				ic = wo.get("production_item") if isinstance(wo, dict) else None
				if not ic:
					continue
				_g, w_item = parse_item_code(ic)
				fw = flt(w_item)
				if fw > 0 and fw not in widths:
					widths.append(fw)
		except Exception:
			pass
	return sorted({round(w, 4) for w in widths if w > 0})


@frappe.whitelist()
def spr_get_bundle_packaging_catalog(shaft_production_run):
	"""Jobs from Available Jobs; width options use combination widths then roll widths.

	Avoids loading the full SPR document (which includes all roll-item rows) to keep
	the response fast.  Segment detail (WO-item mapping) is computed lazily via the
	separate ``spr_get_job_segments`` endpoint when the user actually selects a job.
	"""
	_spr_require_saved(shaft_production_run)

	pp_name = frappe.db.get_value("Shaft Production Run", shaft_production_run, "production_plan")

	# Fetch only the shaft_jobs child rows — much faster than frappe.get_doc() which
	# would also load every roll-item row.
	sj_rows = frappe.db.sql(
		"""
		SELECT name, job_id, gsm, quality, combination, total_width,
		       production_plan_item, work_orders, is_manual
		FROM `tabShaft Production Run Job`
		WHERE parent = %(spr)s AND parentfield = 'shaft_jobs'
		ORDER BY idx ASC
		""",
		{"spr": shaft_production_run},
		as_dict=True,
	)

	jobs_out = []
	seen = set()
	for sj in sj_rows:
		sj = frappe._dict(sj)
		jid = _cstr(_spr_job_id(sj))
		if not jid or jid in seen:
			continue
		seen.add(jid)
		jobs_out.append(
			{
				"job_id": jid,
				"label": _spr_bundle_job_label(sj),
				"total_width_available": flt(sj.get("total_width")),
				"combination_text": _cstr(sj.get("combination") or ""),
				"segments": [],  # lazy-loaded per job via spr_get_job_segments
				"widths": [],
			}
		)

	# Build widths_by_job without loading spr.items:
	# 1) parse combination string  2) fallback to a single aggregate DB query for roll widths
	roll_widths_by_job: dict[str, list] = {}
	if jobs_out:
		# One query for all roll widths grouped by job
		roll_width_rows = frappe.db.sql(
			"""
			SELECT IFNULL(job, '') AS job_key,
			       ROUND(width_inch, 2) AS w
			FROM `tabShaft Production Run Item`
			WHERE parent = %(spr)s AND width_inch > 0
			  AND IFNULL(job, '') != ''
			GROUP BY job_key, w
			ORDER BY job_key, w
			""",
			{"spr": shaft_production_run},
			as_dict=True,
		)
		roll_widths_by_job: dict[str, list] = {}
		for row in roll_width_rows:
			k = _cstr(row.job_key)
			roll_widths_by_job.setdefault(k, []).append(flt(row.w))

	widths_by_job: dict[str, list] = {j["job_id"]: [] for j in jobs_out}
	for sj in sj_rows:
		sj = frappe._dict(sj)
		jid = _cstr(_spr_job_id(sj))
		if not jid or jid not in widths_by_job:
			continue
		widths = _spr_catalog_widths_for_job_row(sj, roll_widths_by_job.get(jid, []), pp_name=pp_name)
		widths_by_job[jid] = widths

	for j in jobs_out:
		j["widths"] = widths_by_job.get(j["job_id"], [])

	return {"jobs": jobs_out, "widths_by_job": widths_by_job}


@frappe.whitelist()
def spr_get_bundle_width_options(shaft_production_run, job_id):
	"""Width inch options for one job — used by Bundle packaging dialog."""
	_spr_require_saved(shaft_production_run)
	job_id = _cstr(job_id).strip()
	cat = spr_get_bundle_packaging_catalog(shaft_production_run)
	widths = (cat.get("widths_by_job") or {}).get(job_id) or []
	if widths:
		return {"widths": widths, "job_id": job_id}
	pp_name = frappe.db.get_value("Shaft Production Run", shaft_production_run, "production_plan")
	sj_rows = frappe.db.sql(
		"""
		SELECT name, job_id, gsm, quality, combination, total_width,
		       production_plan_item, work_orders, is_manual
		FROM `tabShaft Production Run Job`
		WHERE parent = %(spr)s AND parentfield = 'shaft_jobs'
		ORDER BY idx ASC
		""",
		{"spr": shaft_production_run},
		as_dict=True,
	)
	roll_widths: list = []
	roll_rows = frappe.db.sql(
		"""
		SELECT IFNULL(job, '') AS job_key, ROUND(width_inch, 2) AS w
		FROM `tabShaft Production Run Item`
		WHERE parent = %(spr)s AND width_inch > 0
		GROUP BY job_key, w
		""",
		{"spr": shaft_production_run},
		as_dict=True,
	)
	for row in roll_rows:
		if _spr_job_keys_match(_cstr(row.job_key), job_id):
			roll_widths.append(flt(row.w))
	for sj in sj_rows:
		sj = frappe._dict(sj)
		if not _spr_job_keys_match(_cstr(_spr_job_id(sj)), job_id):
			continue
		widths = _spr_catalog_widths_for_job_row(sj, roll_widths, pp_name=pp_name)
		break
	return {"widths": widths or [], "job_id": job_id}


@frappe.whitelist()
def spr_get_job_segments(shaft_production_run, job_id):
	"""Lazy-load segment detail (Width / Net/shaft / WO item) for one job.

	Called client-side when the user selects a job in the Bundle packaging dialog,
	so the initial catalog load stays fast.
	"""
	_spr_require_saved(shaft_production_run)
	spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
	sj = _spr_shaft_job_for_roll(spr, _cstr(job_id))
	if not sj:
		return []
	return _spr_bundle_job_segments_detail(spr, sj)


@frappe.whitelist()
def spr_get_bundle_packaging_lines(shaft_production_run):
	"""Deprecated: use spr_get_bundle_packaging_catalog."""
	return spr_get_bundle_packaging_catalog(shaft_production_run)


def _spr_calc_net_weight_from_gross_for_bundle(row, gross_kg: float) -> float:
	"""
	Server-side replica of the desk calculation used when operators enter `gross_weight`.
	This is required because bundle packaging sets gross/prod length in the backend and
	grid triggers do not run on reload.
	"""
	try:
		width = flt(getattr(row, "width_inch", None))
	except Exception:
		width = 0.0
	gw = flt(gross_kg)
	if width <= 0 or gw <= 0:
		return 0.0

	gsm_val = flt(getattr(row, "gsm", None) or 0) or flt(getattr(row, "sticker_gsm", None) or 0) or 90.0
	width_in_meter = width * 0.0254
	raw_weight = (gsm_val * width_in_meter * gw) / 1000.0

	standard_widths = (63.0, 85.0, 90.0, 118.0, 126.0)
	is_standard = any(abs(width - w) < 0.01 for w in standard_widths)

	core_weight = 0.0
	if is_standard:
		base_weight_of_core = 1.3
		if 50.0 <= raw_weight <= 100.0:
			base_weight_of_core = 1.8
		elif raw_weight > 100.0:
			base_weight_of_core = 2.5
		numeric_core_width = flt(getattr(row, "custom_core_width_mm", None) or 1600.0) or 1600.0
		core_weight = (base_weight_of_core / 1600.0) * numeric_core_width
	else:
		if width < 63.0:
			core_width, prorate = 63.0, 1.30
		elif width < 85.0:
			core_width, prorate = 85.0, 1.75
		elif width < 90.0:
			core_width, prorate = 90.0, 1.86
		elif width < 118.0:
			core_width, prorate = 118.0, 2.43
		else:
			core_width, prorate = 126.0, 2.60
		core_weight = (width / core_width) * prorate

	net_val = gw - core_weight
	if net_val <= 0:
		net_val = gw
	return flt(net_val, 2)


@frappe.whitelist()
def spr_apply_bundle_packaging_for_job_width(
	shaft_production_run,
	job_id,
	width_inch,
	no_of_packaging,
	whole_gross_kg,
	produced_length_mtrs=None,
):
	"""Apply packaging to first-unpacked N roll lines for Job + selected width segment."""
	_spr_require_saved(shaft_production_run)
	job_id = _cstr(job_id)
	width_inch = flt(width_inch)
	no_of_packaging = cint(no_of_packaging)
	whole_gross_kg = flt(whole_gross_kg)
	produced_length_mtrs = flt(produced_length_mtrs)
	if no_of_packaging < 1:
		frappe.throw(_("Number of packaging must be at least 1"))
	if whole_gross_kg <= 0:
		frappe.throw(_("Whole gross weight must be greater than zero"))
	if produced_length_mtrs <= 0:
		frappe.throw(_("Produced length must be greater than zero"))
	if not job_id:
		frappe.throw(_("Select a job from Available Jobs"))
	if width_inch <= 0:
		frappe.throw(_("Select a width (in)"))

	spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
	sj = _spr_shaft_job_for_roll(spr, job_id)
	if not sj:
		frappe.throw(_("Job {0} not found in Available Jobs").format(job_id))

	job_w = width_inch
	matching = []
	for it in spr.items or []:
		if not _spr_item_roll_matches_bundle_job(sj, it, job_id):
			continue
		if _spr_roll_matches_bundle_width(it, width_inch, job_w):
			matching.append(it)

	if not matching:
		for it in spr.items or []:
			if not _spr_item_roll_matches_bundle_job(sj, it, job_id):
				continue
			rw = _spr_roll_effective_width_inch(it)
			if rw > 0.001 and abs(rw - flt(width_inch)) <= 0.75:
				matching.append(it)

	if not matching:
		frappe.throw(
			_(
				"No roll lines for job {0} with width {1} in. Create roll entry or check widths. "
				"(If this is a combination job, pick the segment width that matches the roll item, not total width.)"
			).format(job_id, width_inch)
		)

	# Sequential real-world packing: always use first N unpacked rolls by roll_no/index.
	def _sort_key(it):
		rn = cint(getattr(it, "roll_no", 0) or 0)
		idx = cint(getattr(it, "idx", 0) or 0)
		return (rn if rn > 0 else 999999, idx if idx > 0 else 999999, _cstr(getattr(it, "name", "")))

	matching = sorted(matching, key=_sort_key)

	def _row_key(it):
		return _cstr(getattr(it, "name", "")) or str(cint(getattr(it, "idx", 0) or 0))

	def _is_unpackable(it):
		return flt(getattr(it, "gross_weight", 0) or 0) <= 0

	unpacked = []
	for it in matching:
		if not _is_unpackable(it):
			continue
		unpacked.append(it)

	if len(unpacked) < no_of_packaging:
		seen = {_row_key(it) for it in matching}
		ref_items = {_cstr(getattr(it, "item_code", None)) for it in matching if _cstr(getattr(it, "item_code", None))}
		job_item = _spr_job_product_code(sj)
		if job_item:
			ref_items.add(job_item)
		extra = []
		for it in spr.items or []:
			if not _spr_item_roll_matches_bundle_job(sj, it, job_id):
				continue
			if _row_key(it) in seen:
				continue
			if not _is_unpackable(it):
				continue
			item_code = _cstr(getattr(it, "item_code", None))
			if ref_items and item_code and item_code not in ref_items:
				continue
			extra.append(it)
		if extra:
			matching = sorted(list(matching) + extra, key=_sort_key)
			unpacked = [it for it in matching if _is_unpackable(it)]

	if len(unpacked) < no_of_packaging:
		frappe.throw(
			_("Only {0} unpacked rolls available for job {1} width {2} Inches, but {3} requested.")
			.format(len(unpacked), job_id, width_inch, no_of_packaging)
		)

	selected = unpacked[:no_of_packaging]
	single_gross = round(whole_gross_kg / float(no_of_packaging), 2)
	total_width_inch = round(width_inch * float(no_of_packaging), 4)

	item_meta = frappe.get_meta("Shaft Production Run Item")
	can_set_net = item_meta.has_field("net_weight")
	can_set_len = item_meta.has_field("produced_length_mtrs")
	for it in selected:
		it.gross_weight = single_gross
		if can_set_len:
			it.produced_length_mtrs = produced_length_mtrs
		if can_set_net:
			it.net_weight = _spr_calc_net_weight_from_gross_for_bundle(it, single_gross)

	bundle_net = round(sum(flt(getattr(it, "net_weight", None)) for it in selected), 2)

	# Extract batch_no (common prefix) and roll numbers from the selected rolls.
	# Batch format is "BATCHPREFIX/ROLLNO" — all rolls in a bundle share the same prefix.
	bundle_batch_no = ""
	roll_numbers_list = []
	for it in selected:
		bn = _cstr(getattr(it, "batch_no", "") or "")
		rn = _cstr(getattr(it, "roll_no", "") or "")
		if bn and "/" in bn:
			prefix = bn.rsplit("/", 1)[0]
			if not bundle_batch_no:
				bundle_batch_no = prefix
		elif bn and not bundle_batch_no:
			bundle_batch_no = bn
		if rn:
			roll_numbers_list.append(rn)
		elif bn and "/" in bn:
			# Fallback: extract roll number from batch_no suffix
			roll_numbers_list.append(bn.rsplit("/", 1)[1])
	roll_numbers_str = ", ".join(roll_numbers_list)

	# Store combination as: NO_OF_PACKAGING * WIDTH Inches (example: 4 * 39 Inches)
	comb_calculated = f"{no_of_packaging} * {width_inch} Inches"
	if bundle_batch_no:
		bundle_batch_no = _spr_next_bundle_batch_no(spr, bundle_batch_no)
	bs = {
		"combination": comb_calculated,
		"rolls_per_bundle": no_of_packaging,
		"single_roll_gross_weight_kg": single_gross,
		"sticker_width": total_width_inch,
		"sticker_bundle_gross_weight_kg": round(whole_gross_kg, 2),
		"sticker_bundle_weight": bundle_net,
	}
	bs_meta = frappe.get_meta("Bundle Stickers")
	# Some sites use a custom produced-length field on Bundle Stickers; populate whichever exists.
	if bs_meta.has_field("produced_length_mtrs"):
		bs["produced_length_mtrs"] = produced_length_mtrs
	if bs_meta.has_field("custom_produced_length_mtrs"):
		bs["custom_produced_length_mtrs"] = produced_length_mtrs
	if bs_meta.has_field("job_id"):
		bs["job_id"] = job_id or None
	if bs_meta.has_field("batch_no"):
		bs["batch_no"] = bundle_batch_no or None
	if bs_meta.has_field("roll_numbers"):
		bs["roll_numbers"] = roll_numbers_str or None
	spr.append("bundle_stickers", bs)
	spr.save(ignore_permissions=True)
	remaining_unpacked = max(len(unpacked) - no_of_packaging, 0)

	return {
		"updated_rolls": len(selected),
		"single_roll_gross_kg": single_gross,
		"total_width_inch": total_width_inch,
		"sticker_bundle_weight_kg": bundle_net,
		"remaining_unpacked_rolls": remaining_unpacked,
		"applied_produced_length": produced_length_mtrs,
	}


def _bp_format_width_label_static(w):
	fw = flt(w)
	if fw <= 0:
		return ""
	return str(int(round(fw))) if abs(fw - round(fw)) < 0.001 else f"{fw:.1f}"


def _spr_parse_bundle_width_mix(width_mix=None, width_inch=None, no_of_packaging=None) -> list[dict]:
	"""Normalize width mix: [{width_inch, rolls}, ...]. Single-width falls back to width_inch + count."""
	mix = []
	if width_mix:
		raw = width_mix
		if isinstance(raw, str):
			try:
				raw = json.loads(raw)
			except Exception:
				raw = []
		if isinstance(raw, dict):
			raw = raw.get("widths") or raw.get("width_mix") or []
		for row in raw or []:
			if not isinstance(row, dict):
				continue
			w = flt(row.get("width_inch") or row.get("width") or 0)
			n = cint(row.get("rolls") or row.get("qty") or row.get("count") or 0)
			if w > 0 and n > 0:
				mix.append({"width_inch": w, "rolls": n})
	if not mix:
		w = flt(width_inch)
		n = cint(no_of_packaging)
		if w > 0 and n > 0:
			mix.append({"width_inch": w, "rolls": n})
	if not mix:
		frappe.throw(_("Select at least one width with roll quantity"))
	return mix


def _spr_allocate_bundle_roll_grosses(whole_gross_kg: float, mix: list[dict]) -> list[dict]:
	"""Assign gross to each roll. Multi-width: share proportional to width. Single: equal split."""
	whole = flt(whole_gross_kg)
	slots = []
	for entry in mix:
		w = flt(entry.get("width_inch"))
		n = cint(entry.get("rolls"))
		for _ in range(n):
			slots.append(w)
	if not slots:
		frappe.throw(_("No rolls to package"))
	total_w = sum(slots)
	plan = []
	if len(set(round(s, 4) for s in slots)) == 1:
		# Single width — equal split (shop-floor parity)
		each = round(whole / float(len(slots)), 2)
		assigned = 0.0
		for i, w in enumerate(slots):
			g = each if i < len(slots) - 1 else round(whole - assigned, 2)
			assigned += g
			plan.append({"width_inch": w, "gross_weight": g})
		return plan
	# Multi-width — proportional to width
	assigned = 0.0
	for i, w in enumerate(slots):
		if i < len(slots) - 1:
			g = round(whole * (w / total_w), 2)
			assigned += g
		else:
			g = round(whole - assigned, 2)
		plan.append({"width_inch": w, "gross_weight": g})
	return plan


def _spr_bundle_combination_label(mix: list[dict]) -> str:
	parts = []
	for entry in mix:
		w = flt(entry.get("width_inch"))
		n = cint(entry.get("rolls"))
		if w > 0 and n > 0:
			parts.append(f"{n} * {_bp_format_width_label_static(w)}")
	return " + ".join(parts) + " Inches" if parts else ""


def _spr_append_bundle_sticker_row(
	spr,
	job_id,
	width_inch,
	no_of_packaging,
	whole_gross_kg,
	produced_length_mtrs,
	single_gross,
	total_width_inch,
	bundle_net,
	selected_items,
	combination=None,
):
	"""Append one Bundle Stickers child row from packed roll lines."""
	bundle_batch_no = ""
	roll_numbers_list = []
	for it in selected_items or []:
		bn = _cstr(getattr(it, "batch_no", "") or "")
		rn = _cstr(getattr(it, "roll_no", "") or "")
		if bn and "/" in bn:
			prefix = bn.rsplit("/", 1)[0]
			if not bundle_batch_no:
				bundle_batch_no = prefix
		elif bn and not bundle_batch_no:
			bundle_batch_no = bn
		if rn:
			roll_numbers_list.append(rn)
		elif bn and "/" in bn:
			roll_numbers_list.append(bn.rsplit("/", 1)[1])
	roll_numbers_str = ", ".join(roll_numbers_list)
	comb_calculated = _cstr(combination).strip() or f"{no_of_packaging} * {width_inch} Inches"
	if bundle_batch_no:
		bundle_batch_no = _spr_next_bundle_batch_no(spr, bundle_batch_no)
	bs = {
		"combination": comb_calculated,
		"rolls_per_bundle": no_of_packaging,
		"single_roll_gross_weight_kg": single_gross,
		"sticker_width": total_width_inch,
		"sticker_bundle_gross_weight_kg": round(whole_gross_kg, 2),
		"sticker_bundle_weight": bundle_net,
	}
	bs_meta = frappe.get_meta("Bundle Stickers")
	if bs_meta.has_field("produced_length_mtrs"):
		bs["produced_length_mtrs"] = produced_length_mtrs
	if bs_meta.has_field("custom_produced_length_mtrs"):
		bs["custom_produced_length_mtrs"] = produced_length_mtrs
	if bs_meta.has_field("job_id"):
		bs["job_id"] = job_id or None
	if bs_meta.has_field("batch_no"):
		bs["batch_no"] = bundle_batch_no or None
	if bs_meta.has_field("roll_numbers"):
		bs["roll_numbers"] = roll_numbers_str or None
	spr.append("bundle_stickers", bs)
	return bundle_batch_no, roll_numbers_str, comb_calculated


@frappe.whitelist()
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
	"""GSM bundle flow: create N fresh roll lines + Bundle Stickers (single or multi width)."""
	_spr_require_saved(shaft_production_run)
	job_id = _cstr(job_id).strip()
	whole_gross_kg = flt(whole_gross_kg)
	produced_length_mtrs = flt(produced_length_mtrs)
	pp_id = _cstr(pp_id).strip()
	mix = _spr_parse_bundle_width_mix(width_mix, width_inch, no_of_packaging)
	no_of_packaging = sum(cint(m.get("rolls")) for m in mix)
	primary_width = flt(mix[0].get("width_inch")) if len(mix) == 1 else 0.0

	if no_of_packaging < 1:
		frappe.throw(_("Number of packaging must be at least 1"))
	if whole_gross_kg <= 0:
		frappe.throw(_("Whole gross weight must be greater than zero"))
	if produced_length_mtrs <= 0:
		frappe.throw(_("Produced length must be greater than zero"))
	if not job_id:
		frappe.throw(_("Select a job from Available Jobs"))

	roll_gross_plan = _spr_allocate_bundle_roll_grosses(whole_gross_kg, mix)
	comb_label = _spr_bundle_combination_label(mix)
	total_width_inch = round(sum(flt(p.get("width_inch")) for p in roll_gross_plan), 4)
	avg_single_gross = round(whole_gross_kg / float(no_of_packaging), 2) if no_of_packaging else 0

	with _spr_operation_lock(shaft_production_run, "write", ttl_sec=180):
		spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
		if cint(spr.docstatus) != 0:
			frappe.throw(_("Cannot apply bundle packaging to a submitted Shaft Production Run"))

		sj = _spr_shaft_job_for_roll(spr, job_id)
		if not sj:
			frappe.throw(_("Job {0} not found in Available Jobs").format(job_id))

		pp_resolved = pp_id or _cstr(spr.get("production_plan")).strip()
		unit = _cstr(spr.get("custom_unit") or "").strip()
		limits = _gsm_job_roll_limits_from_job_row(sj)
		max_job_rolls = cint(limits.get("max_rolls") or 0)
		if unit:
			current_rolls = _gsm_count_job_rolls_all_sprs(pp_resolved, job_id, unit=unit, produced_only=True)
		else:
			current_rolls = _spr_count_roll_lines_for_job(spr, job_id, produced_only=True)
		if max_job_rolls > 0 and current_rolls + no_of_packaging > max_job_rolls:
			_spr_throw_roll_quota_exceeded(job_id, max_job_rolls, current_rolls)

		# Build one template per roll from the chosen width(s) — never cycle mix widths
		# (combo jobs like 30+33 have different WO/item per width; single-width pack uses only that WO).
		line_dicts = _spr_bundle_line_dicts_for_width_mix(spr, sj, job_id, roll_gross_plan)
		if len(line_dicts) < no_of_packaging:
			frappe.throw(_("Could not build {0} roll line(s) for job {1}").format(no_of_packaging, job_id))

		client_max_roll = _spr_max_roll_suffix_for_job(spr, job_id)
		batch_rows = _get_next_spr_batch_numbers_unlocked(
			shaft_production_run=shaft_production_run,
			count=len(line_dicts),
			client_max_roll=client_max_roll,
			run_date=spr.run_date,
			custom_unit=spr.get("custom_unit"),
			shift=spr.shift,
			client_series_prefix=_spr_existing_series_prefix_for_job(spr, job_id) or None,
		)

		item_meta = frappe.get_meta("Shaft Production Run Item")
		can_set_net = item_meta.has_field("net_weight")
		can_set_len = item_meta.has_field("produced_length_mtrs")
		item_meta_spi = item_meta
		selected = []
		for idx, ld in enumerate(line_dicts):
			plan = roll_gross_plan[idx] if idx < len(roll_gross_plan) else roll_gross_plan[-1]
			w = flt(plan.get("width_inch"))
			g = flt(plan.get("gross_weight"))
			row = spr.append("items", {})
			for key, val in (ld or {}).items():
				if val in (None, "") or not item_meta_spi.has_field(key):
					continue
				row.set(key, val)
			row.width_inch = w
			row.job = job_id
			if idx < len(batch_rows or []):
				br = batch_rows[idx] or {}
				if br.get("batch_no"):
					row.batch_no = br.get("batch_no")
				if br.get("roll_no") is not None:
					row.roll_no = br.get("roll_no")
			row.gross_weight = g
			if can_set_len:
				row.produced_length_mtrs = produced_length_mtrs
			if can_set_net:
				row.net_weight = _spr_calc_net_weight_from_gross_for_bundle(row, g)
			if item_meta.has_field("custom_produced_length_mtrs"):
				row.custom_produced_length_mtrs = produced_length_mtrs
			selected.append(row)

		bundle_net = round(sum(flt(getattr(it, "net_weight", None)) for it in selected), 2)
		bundle_batch_no, roll_numbers_str, comb_calculated = _spr_append_bundle_sticker_row(
			spr,
			job_id,
			primary_width or flt(selected[0].width_inch if selected else 0),
			no_of_packaging,
			whole_gross_kg,
			produced_length_mtrs,
			avg_single_gross,
			total_width_inch,
			bundle_net,
			selected,
			combination=comb_label,
		)

		spr._validate_no_duplicate_roll_batches()
		spr.save(ignore_permissions=True)

		pp_resolved = pp_id or _cstr(spr.get("production_plan")).strip()
		order_code = _cstr(spr.get("custom_order_code") or "")
		if not order_code and pp_resolved and frappe.db.exists("Production Plan", pp_resolved):
			pp_doc = frappe.get_doc("Production Plan", pp_resolved)
			order_code = _cstr(
				pp_doc.get("custom_party_code") or pp_doc.get("custom_order_code") or ""
			)

		if len(mix) == 1:
			width_label = f'{_bp_format_width_label_static(mix[0]["width_inch"])}" ({no_of_packaging} rolls)'
			segment_width = flt(mix[0]["width_inch"])
		else:
			width_label = comb_calculated
			segment_width = 0
		child_batches = [_cstr(getattr(it, "batch_no", "")) for it in selected if _cstr(getattr(it, "batch_no", ""))]

		return {
			"status": "ok",
			"spr_name": spr.name,
			"pp_id": pp_resolved,
			"updated_rolls": len(selected),
			"single_roll_gross_kg": avg_single_gross,
			"total_width_inch": total_width_inch,
			"sticker_bundle_weight_kg": bundle_net,
			"whole_gross_kg": round(whole_gross_kg, 2),
			"bundle_batch_no": bundle_batch_no,
			"roll_numbers": roll_numbers_str,
			"combination": comb_calculated,
			"width_label": width_label,
			"job_id": job_id,
			"segment_width": segment_width,
			"pack_count": no_of_packaging,
			"produced_length_mtrs": produced_length_mtrs,
			"child_roll_batches": child_batches,
			"child_spr_item_names": [_cstr(getattr(it, "name", "")) for it in selected],
			"width_mix": mix,
			"order_code": order_code,
			"quality": _cstr(getattr(selected[0], "quality", "") if selected else ""),
			"color": _cstr(getattr(selected[0], "color", "") if selected else ""),
			"gsm": cint(getattr(selected[0], "gsm", 0) if selected else 0),
			"meter_roll": flt(getattr(selected[0], "meter_roll", 0) if selected else 0),
			"planned_qty": flt(getattr(selected[0], "planned_qty", 0) if selected else 0),
			"work_order": _cstr(getattr(selected[0], "work_order", "") if selected else ""),
			"uom": _cstr(getattr(selected[0], "uom", "Kg") if selected else "Kg"),
		}


@frappe.whitelist()
def spr_apply_bundle_packaging(
	shaft_production_run,
	spr_item_row_name,
	no_of_packaging,
	whole_gross_kg,
):
	"""Single-roll gross on matched line + Bundle Stickers row (Kg / inch)."""
	_spr_require_saved(shaft_production_run)
	no_of_packaging = cint(no_of_packaging)
	whole_gross_kg = flt(whole_gross_kg)
	if no_of_packaging < 1:
		frappe.throw(_("Number of packaging must be at least 1"))
	if whole_gross_kg <= 0:
		frappe.throw(_("Whole gross weight must be greater than zero"))
	if not spr_item_row_name:
		frappe.throw(_("Select a roll line"))

	spr = frappe.get_doc("Shaft Production Run", shaft_production_run)
	target = None
	for it in spr.items or []:
		if _cstr(it.name) == _cstr(spr_item_row_name):
			target = it
			break
	if not target:
		frappe.throw(_("Roll line not found"))

	jid = _cstr(getattr(target, "job", None))
	sj = _spr_shaft_job_for_roll(spr, jid)
	sel_w = flt(getattr(sj, "total_width", None)) if sj else 0.0
	if sel_w <= 0:
		sel_w = flt(getattr(target, "width_inch", None))
	single_gross = round(whole_gross_kg / float(no_of_packaging), 2)
	total_width_inch = round(sel_w * float(no_of_packaging), 4)
	net_one = flt(getattr(target, "net_weight", None))
	item_meta = frappe.get_meta("Shaft Production Run Item")
	if item_meta.has_field("net_weight"):
		target.net_weight = _spr_calc_net_weight_from_gross_for_bundle(target, single_gross)
	net_one = flt(getattr(target, "net_weight", None))
	bundle_net = round(net_one * float(no_of_packaging), 2)

	target.gross_weight = single_gross

	comb = ""
	if sj:
		comb = _cstr(getattr(sj, "combination", None))
	else:
		for row in spr.shaft_jobs or []:
			if _cstr(_spr_job_id(row)) == jid:
				comb = _cstr(getattr(row, "combination", None))
				break

	# Extract batch_no and roll_no from the target roll line.
	bundle_batch_no = ""
	roll_numbers_str = ""
	bn = _cstr(getattr(target, "batch_no", "") or "")
	rn = _cstr(getattr(target, "roll_no", "") or "")
	if bn and "/" in bn:
		bundle_batch_no = bn.rsplit("/", 1)[0]
		if not rn:
			rn = bn.rsplit("/", 1)[1]
	elif bn:
		bundle_batch_no = bn
	if bundle_batch_no:
		bundle_batch_no = _spr_next_bundle_batch_no(spr, bundle_batch_no)
	roll_numbers_str = rn

	bs = {
		"combination": comb or None,
		"rolls_per_bundle": no_of_packaging,
		"single_roll_gross_weight_kg": single_gross,
		"sticker_width": total_width_inch,
		"sticker_bundle_gross_weight_kg": round(whole_gross_kg, 2),
		"sticker_bundle_weight": bundle_net,
	}
	bs_meta = frappe.get_meta("Bundle Stickers")
	if bs_meta.has_field("job_id"):
		bs["job_id"] = jid or None
	if bs_meta.has_field("batch_no"):
		bs["batch_no"] = bundle_batch_no or None
	if bs_meta.has_field("roll_numbers"):
		bs["roll_numbers"] = roll_numbers_str or None
	spr.append("bundle_stickers", bs)
	spr.save(ignore_permissions=True)

	return {
		"single_roll_gross_kg": single_gross,
		"total_width_inch": total_width_inch,
		"sticker_bundle_weight_kg": bundle_net,
	}


def _fill_party_code_from_resolved_wos(m: dict, job_meta, wos: list) -> None:
	"""Set child row party_code (Order Code) from the resolved WO when the plan row did not supply it."""
	if not wos or not job_meta or not job_meta.has_field("party_code"):
		return
	v = m.get("party_code")
	if v is not None and str(v).strip():
		return
	try:
		wo_doc = frappe.get_doc("Work Order", wos[0]["name"])
		pc = get_order_code(wo_doc)
		if pc:
			m["party_code"] = pc
	except Exception:
		pass


def get_order_code(wo_doc):
	"""Party / order code for roll lines: WO custom fields, then Sales Order."""
	for attr in ("order_code", "custom_order_code", "custom_party_code"):
		v = getattr(wo_doc, attr, None)
		if v is not None and str(v).strip():
			return str(v).strip()
	so = getattr(wo_doc, "sales_order", None)
	if so:
		for col in ("custom_party_code", "po_no"):
			if frappe.db.has_column("Sales Order", col):
				v = frappe.db.get_value("Sales Order", so, col)
				if v is not None and str(v).strip():
					return str(v).strip()
		return str(so).strip()
	return ""


@frappe.whitelist()
def spr_sync_bundle_produced_sheets(spr_name: str | None = None):
	"""Recompute bundle_calculation totals and sheet-cutting header fields (desk)."""
	spr_name = _cstr(spr_name)
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found."))
	doc = frappe.get_doc("Shaft Production Run", spr_name)
	sync_bundle_total_produced_sheets_for_doc(doc)
	sync_bundle_total_produced_bag_pcs_for_doc(doc)
	if cint(getattr(doc, "custom_is_sheet_cutting", 0)) or cint(getattr(doc, "custom_is_box_bag", 0)):
		sync_bundle_total_achieved_weight_for_doc(doc)
		sync_bundle_consumed_meter_header(doc)
	if cint(doc.docstatus) == 0:
		doc._spr_recalc_total_produced_weight_header()
		doc.save(ignore_permissions=True)
	out = []
	for br in doc.get("bundle_calculation") or []:
		out.append(
			{
				"name": br.name,
				"total_produced_sheets": flt(getattr(br, "total_produced_sheets", 0) or 0),
				"total_produced_bag_pcs": flt(getattr(br, "total_produced_bag_pcs", 0) or 0),
				"total_achieved_weight": flt(getattr(br, "total_achieved_weight", 0) or 0),
				"total_consumed_meter": flt(getattr(br, "total_consumed_meter", 0) or 0),
			}
		)
	return {
		"status": "ok",
		"rows": out,
		"total_produced_weight": flt(getattr(doc, "total_produced_weight", 0) or 0),
		"custom_total_achieved_meter": flt(getattr(doc, "custom_total_achieved_meter", 0) or 0),
	}


@frappe.whitelist()
def spr_get_fabric_batch_pick_context(spr_name: str | None = None):
	"""Return WO / 100-fabric requirements, available WIP batches, and saved picks for the desk dialog."""
	spr_name = _cstr(spr_name)
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found."))
	doc = frappe.get_doc("Shaft Production Run", spr_name)
	return doc._spr_build_fabric_batch_pick_context_dict()


def _spr_resolve_batch_link_name(batch_no: str, item_code: str = "") -> str:
	"""Map SLE / label batch text to a valid Batch.name for Link fields."""
	bn = _cstr(batch_no).strip()
	if not bn:
		return ""
	if frappe.db.exists("Batch", bn):
		return bn
	alt = frappe.db.get_value("Batch", {"batch_id": bn}, "name")
	if alt:
		return _cstr(alt)
	if frappe.db.has_column("Batch", "batch_id"):
		row = frappe.db.sql(
			"""
			SELECT name, item FROM `tabBatch`
			WHERE batch_id = %s OR name = %s
			ORDER BY creation DESC
			LIMIT 1
			""",
			(bn, bn),
			as_dict=True,
		)
		if row:
			name = _cstr(row[0].get("name"))
			batch_item = _cstr(row[0].get("item"))
			ic = _cstr(item_code)
			if ic and batch_item and batch_item != ic:
				frappe.throw(_("Batch {0} is for item {1}, not {2}.").format(name, batch_item, ic))
			return name
	frappe.throw(
		_("Batch {0} was not found in Batch master. Pick a batch from stock list or create the Batch record first.").format(
			bn
		),
		title=_("Invalid batch"),
	)


@frappe.whitelist()
def spr_diagnose_save_blockers(spr_name: str | None = None):
	"""Desk troubleshooting: duplicate fields, validate preview, RM batch gaps."""
	spr_name = _cstr(spr_name)
	out: dict = {
		"spr": spr_name,
		"docstatus": None,
		"is_bag_spr": False,
		"duplicate_custom_fields": [],
		"fabric_batch_picks_count": 0,
		"rm_batch_context": {},
		"validate_ok": True,
		"validate_error": "",
		"batch_prefix_ok": True,
		"batch_prefix_note": "",
		"form_dirty_causes": [],
	}
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		out["validate_ok"] = False
		out["validate_error"] = _("Shaft Production Run not found.")
		return out

	for fn in ("company", "custom_total_planned_pcs", "custom_total_achieved_pcs"):
		cfs = frappe.get_all(
			"Custom Field",
			filters={"dt": "Shaft Production Run", "fieldname": fn},
			pluck="name",
		)
		if cfs:
			out["duplicate_custom_fields"].append({"fieldname": fn, "names": cfs})

	doc = frappe.get_doc("Shaft Production Run", spr_name)
	out["docstatus"] = cint(doc.docstatus)
	out["is_bag_spr"] = spr_doc_is_bag_spr(doc)
	out["fabric_batch_picks_count"] = len(doc.get("fabric_batch_picks") or [])
	try:
		ctx = doc._spr_build_fabric_batch_pick_context_dict()
		out["rm_batch_context"] = {
			"needs_picks": bool(ctx.get("needs_picks")),
			"line_count": len(ctx.get("lines") or []),
			"is_bag_spr": bool(ctx.get("is_bag_spr")),
		}
	except Exception as exc:
		out["rm_batch_context"] = {"error": _cstr(exc)}

	parts = spr_batch_prefix_for_unit(doc.get("custom_unit"))
	if not parts and _cstr(doc.get("custom_unit")).strip():
		out["batch_prefix_ok"] = False
		out["batch_prefix_note"] = _(
			"Unit «{0}» has no roll batch prefix — roll batch_no assignment is skipped on Save."
		).format(doc.get("custom_unit"))

	if out["duplicate_custom_fields"]:
		out["form_dirty_causes"].append(
			_("Duplicate Custom Field rows on this site — run bench migrate (cleanup_spr_duplicate_custom_fields).")
		)

	try:
		probe = frappe.copy_doc(doc)
		probe.run_method("validate")
	except Exception as exc:
		out["validate_ok"] = False
		out["validate_error"] = _cstr(exc)

	if not out["validate_ok"]:
		out["form_dirty_causes"].append(_("Server validate() failed — see validate_error."))
	elif out["duplicate_custom_fields"]:
		out["form_dirty_causes"].append(
			_("Save may succeed but duplicate header fields confuse the desk — migrate to remove Custom Field copies.")
		)
	else:
		out["form_dirty_causes"].append(
			_(
				"If the form shows Not Saved after Save, hard-refresh (Ctrl+F5). "
				"Desk auto-sync was re-marking the form dirty — fixed in latest JS."
			)
		)

	return out


@frappe.whitelist()
def spr_save_fabric_batch_picks(spr_name: str | None = None, picks_json=None):
	"""Replace `fabric_batch_picks` on a Draft SPR from the desk dialog."""
	import json

	spr_name = _cstr(spr_name)
	if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
		frappe.throw(_("Shaft Production Run not found."))
	doc = frappe.get_doc("Shaft Production Run", spr_name)
	if cint(doc.docstatus) != 0:
		frappe.throw(_("Fabric batch picks can only be saved on a Draft SPR."))
	if not frappe.get_meta("Shaft Production Run").has_field("fabric_batch_picks"):
		frappe.throw(_("Run bench migrate to add fabric batch fields."))
	picks = picks_json
	if isinstance(picks, str):
		picks = json.loads(picks)
	doc.fabric_batch_picks = []
	for p in picks or []:
		wo = _cstr((p or {}).get("work_order"))
		ic = _cstr((p or {}).get("item_code"))
		bn_raw = _cstr((p or {}).get("batch_no"))
		q = flt((p or {}).get("qty"))
		if not wo or not ic or not bn_raw or q <= 0:
			continue
		bn = _spr_resolve_batch_link_name(bn_raw, ic)
		doc.append(
			"fabric_batch_picks",
			{"work_order": wo, "item_code": ic, "batch_no": bn, "qty": q},
		)
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), f"spr_save_fabric_batch_picks:{spr_name}")
		frappe.throw(
			_("Could not save RM batch picks on {0}: {1}").format(spr_name, _cstr(exc)),
			title=_("Save failed"),
		)
	return {"status": "ok", "name": doc.name, "count": len(doc.fabric_batch_picks or [])}


@frappe.whitelist(methods=["GET", "POST"])
def spr_set_item_row_lock(spr_name, row_name, locked, gross_weight=None, net_weight=None, produced_gsm=None):
	"""Lightweight API: update row_locked (and optionally weights) on a single SPR Item row.
	Uses direct DB update to avoid the full doc.save() / after_save cycle that freezes the UI."""
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.throw(_("Please log in to continue."), frappe.AuthenticationError)
	locked = cint(locked)
	docstatus = frappe.db.get_value("Shaft Production Run", spr_name, "docstatus")
	if docstatus is None:
		frappe.throw(_("Shaft Production Run not found."))
	if cint(docstatus) != 0:
		frappe.throw(_("Cannot update a submitted Shaft Production Run."))

	fields = {"row_locked": locked}
	if gross_weight is not None:
		fields["gross_weight"] = flt(gross_weight)
	if net_weight is not None:
		fields["net_weight"] = flt(net_weight)
	if produced_gsm is not None:
		fields["produced_gsm"] = flt(produced_gsm)

	has_row_ready = frappe.db.has_column("Shaft Production Run Item", "row_ready_for_print")
	if has_row_ready:
		fields["row_ready_for_print"] = locked

	now_str = frappe.utils.now()
	frappe.db.set_value("Shaft Production Run Item", row_name, fields, update_modified=False)
	frappe.db.set_value("Shaft Production Run", spr_name, "modified", now_str, update_modified=False)
	frappe.db.commit()
	return {"status": "ok", "row_name": row_name, "locked": locked, "modified": now_str}


