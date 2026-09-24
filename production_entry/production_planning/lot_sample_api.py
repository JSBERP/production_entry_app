# -*- coding: utf-8 -*-
"""Shaft Lot Sample — one document per date + shift + unit."""

from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, formatdate, getdate


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


def _unit_label(unit: str) -> str:
	u = _cstr(unit)
	low = u.lower()
	if low.startswith("unit ") and len(u) > 5:
		return u[5:].strip() or u
	return u


def _company_name() -> str:
	company = _cstr(frappe.defaults.get_user_default("Company") or "")
	if not company:
		try:
			company = _cstr(frappe.db.get_single_value("Global Defaults", "default_company") or "")
		except Exception:
			company = ""
	if company:
		name = _cstr(frappe.db.get_value("Company", company, "company_name") or company)
		if name:
			cleaned = re.sub(r"\s*[-–]\s*[A-Z0-9]{2,8}\s*$", "", name, flags=re.I).strip()
			return (cleaned or name).upper()
	return "JAYASHREE SPUN BOND"


def _fabric_type_from_plan(plan_id: str) -> str:
	plan_id = _cstr(plan_id)
	if not plan_id or not frappe.db.exists("Production Plan", plan_id):
		return ""
	candidates = ("custom_fabric_type", "fabric_type", "custom_type")
	for fn in candidates:
		try:
			if frappe.db.has_column("Production Plan", fn):
				val = _cstr(frappe.db.get_value("Production Plan", plan_id, fn) or "")
				if val:
					return val
		except Exception:
			continue
	try:
		pp = frappe.get_doc("Production Plan", plan_id)
		for fn in candidates:
			val = _cstr(pp.get(fn) or "")
			if val:
				return val
	except Exception:
		pass
	return ""


def _fabric_type_for_pp(pp_id: str, spr_name: str = "") -> str:
	pp_id = _cstr(pp_id)
	spr_name = _cstr(spr_name)
	plan_id = ""
	if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
		plan_id = _cstr(frappe.db.get_value("Shaft Production Run", spr_name, "production_plan") or "")
	if not plan_id and pp_id:
		if frappe.db.exists("Production Plan", pp_id):
			plan_id = pp_id
		elif frappe.db.exists("Shaft Production Run", pp_id):
			plan_id = _cstr(frappe.db.get_value("Shaft Production Run", pp_id, "production_plan") or "")
	if not plan_id and pp_id:
		plan_id = _cstr(
			frappe.db.get_value("Shaft Production Run", {"production_plan": pp_id}, "production_plan") or ""
		)
	return _fabric_type_from_plan(plan_id)


def _row_payload(row) -> dict:
	return {
		"name": _cstr(getattr(row, "name", None)),
		"order_code": _cstr(getattr(row, "order_code", None)),
		"quality": _cstr(getattr(row, "quality", None)),
		"colour": _cstr(getattr(row, "colour", None) or getattr(row, "color", None)),
		"gsm": cint(getattr(row, "gsm", None) or 0),
		"fabric_type": _cstr(getattr(row, "fabric_type", None)),
		"no_of_lot_sample": cint(getattr(row, "no_of_lot_sample", None) or 0),
	}


def _doc_payload(doc, extra: dict | None = None) -> dict:
	out = {
		"name": doc.name,
		"run_date": str(doc.run_date or ""),
		"shift": doc.shift or "",
		"custom_unit": doc.custom_unit or "",
		"gsm_shift_session": doc.gsm_shift_session or "",
		"rows": [_row_payload(r) for r in (doc.samples or [])],
		"company": _company_name(),
		"unit_label": _unit_label(doc.custom_unit or ""),
	}
	if extra:
		out.update(extra)
	return out


def _find_doc(run_date=None, shift=None, custom_unit=None, gsm_shift_session=None, name=None):
	name = _cstr(name)
	if name and frappe.db.exists("Shaft Lot Sample", name):
		return name
	session = _cstr(gsm_shift_session)
	unit = _cstr(custom_unit)
	shift_n = _normalize_shift(shift)
	rd = getdate(run_date) if run_date else None
	if session and unit:
		found = frappe.db.get_value(
			"Shaft Lot Sample",
			{"gsm_shift_session": session, "custom_unit": unit},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	if rd and shift_n and unit:
		found = frappe.db.get_value(
			"Shaft Lot Sample",
			{"run_date": rd, "shift": shift_n, "custom_unit": unit},
			"name",
			order_by="modified desc",
		)
		if found:
			return found
	return None


def _get_or_create(run_date=None, shift=None, custom_unit=None, gsm_shift_session=None, name=None):
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=name,
	)
	if found:
		doc = frappe.get_doc("Shaft Lot Sample", found)
	else:
		unit = _cstr(custom_unit)
		shift_n = _normalize_shift(shift)
		rd = getdate(run_date) if run_date else None
		if not (rd and shift_n and unit):
			frappe.throw(_("Date, Shift, and Unit are required to save lot samples."))
		doc = frappe.new_doc("Shaft Lot Sample")
		doc.run_date = rd
		doc.shift = shift_n
		doc.custom_unit = unit
	session = _cstr(gsm_shift_session)
	if session:
		doc.gsm_shift_session = session
	return doc


def _session_pp_ids(run_date=None, shift=None, custom_unit=None, gsm_shift_session=None, spr_names=None, extra_pp_ids=None):
	from production_entry.production_planning.unified_production_entry_api import (
		_gsm_draft_sprs_for_session,
		_gsm_locked_jobs_from_session,
		_gsm_open_shift_session_doc,
	)

	pp_ids = []
	seen = set()

	def _add(pp_id: str):
		pp_id = _cstr(pp_id)
		if pp_id and pp_id not in seen:
			seen.add(pp_id)
			pp_ids.append(pp_id)

	raw_pps = extra_pp_ids if isinstance(extra_pp_ids, list) else _parse_rows(extra_pp_ids)
	for item in raw_pps:
		_add(item.get("pp_id") if isinstance(item, dict) else item)

	session = None
	sid = _cstr(gsm_shift_session)
	if sid and frappe.db.exists("GSM Shift Session", sid):
		session = frappe.get_doc("GSM Shift Session", sid)
	elif run_date and shift and custom_unit:
		session = _gsm_open_shift_session_doc(run_date, shift, custom_unit)
	if session:
		for row in _gsm_locked_jobs_from_session(session):
			_add(row.get("pp_id"))
		for spr in _gsm_draft_sprs_for_session(session.run_date, session.shift, session.custom_unit):
			_add(spr.get("pp_id"))

	raw_sprs = spr_names if isinstance(spr_names, list) else _parse_rows(spr_names)
	for item in raw_sprs:
		if isinstance(item, dict):
			spr_name = _cstr(item.get("spr_name") or item.get("name"))
			_add(item.get("pp_id") or item.get("production_plan"))
		else:
			spr_name = _cstr(item)
		if not spr_name or not frappe.db.exists("Shaft Production Run", spr_name):
			continue
		_add(frappe.db.get_value("Shaft Production Run", spr_name, "production_plan") or spr_name)
	return pp_ids


def _combo_from_jobs(jobs: list[dict], pp_id: str, spr_name: str = "") -> dict:
	from production_entry.production_planning.unified_production_entry_api import _gsm_order_code_for_pp

	order_code = ""
	qualities: dict[str, dict] = {}

	def _add(quality: str, colour: str, gsm: int = 0):
		quality = _cstr(quality)
		colour = _cstr(colour)
		if not quality:
			return
		qnode = qualities.setdefault(quality, {"quality": quality, "colours": {}})
		cnode = qnode["colours"].setdefault(colour or "—", {"colour": colour, "gsms": []})
		g = cint(gsm or 0)
		if g > 0 and g not in cnode["gsms"]:
			cnode["gsms"].append(g)

	for job in jobs or []:
		oc = _cstr(job.get("order_code") or "")
		if oc and not order_code:
			order_code = oc
		_add(
			job.get("quality") or "",
			job.get("color") or job.get("colour") or "",
			job.get("gsm") or job.get("fabric_gsm") or job.get("lam_gsm") or 0,
		)

	# Lamination / FG: job board may have no fabric shaft jobs — fall back to PP + Planning Table + SPR
	if not qualities:
		pp_id = _cstr(pp_id)
		if pp_id and frappe.db.exists("Production Plan", pp_id):
			pp_fields = []
			for f in (
				"custom_quality",
				"custom_color",
				"custom_colour",
				"quality",
				"color",
				"colour",
			):
				if frappe.db.has_column("Production Plan", f):
					pp_fields.append(f)
			if pp_fields:
				pp_row = frappe.db.get_value("Production Plan", pp_id, pp_fields, as_dict=True) or {}
				_add(
					pp_row.get("custom_quality") or pp_row.get("quality") or "",
					pp_row.get("custom_color")
					or pp_row.get("custom_colour")
					or pp_row.get("color")
					or pp_row.get("colour")
					or "",
					0,
				)
			# Planning Table rows for this PP (quality may live in custom_quality)
			pt_filters = []
			for f in ("order_sheet", "custom_order_sheet", "production_plan", "custom_production_plan"):
				if frappe.db.has_column("Planning Table", f):
					pt_filters.append([f, "=", pp_id])
			if pt_filters:
				or_filters = pt_filters if len(pt_filters) > 1 else None
				filters = pt_filters[0] if len(pt_filters) == 1 else None
				pt_fields = ["name"]
				for f in (
					"quality",
					"custom_quality",
					"color",
					"colour",
					"gsm",
					"fabric_gsm",
					"lam_gsm",
					"item_code",
					"production_item",
				):
					if frappe.db.has_column("Planning Table", f):
						pt_fields.append(f)
				try:
					pts = frappe.get_all(
						"Planning Table",
						filters=filters,
						or_filters=or_filters,
						fields=pt_fields,
						limit_page_length=50,
					) or []
				except Exception:
					pts = []
				for pt in pts:
					q = _cstr(pt.get("quality") or pt.get("custom_quality") or "")
					c = _cstr(pt.get("color") or pt.get("colour") or "")
					g = cint(pt.get("gsm") or pt.get("fabric_gsm") or pt.get("lam_gsm") or 0)
					if not q:
						ic = _cstr(pt.get("item_code") or pt.get("production_item") or "")
						if ic:
							try:
								from production_entry.production_planning.scheduler_api import (
									resolve_quality_color_gsm_from_item_code,
								)

								rq, rc, rg = resolve_quality_color_gsm_from_item_code(ic)
								q = q or _cstr(rq)
								c = c or _cstr(rc)
								g = g or cint(rg or 0)
							except Exception:
								pass
					_add(q, c, g)

		spr_name = _cstr(spr_name)
		if spr_name and frappe.db.exists("Shaft Production Run", spr_name):
			spr = frappe.get_doc("Shaft Production Run", spr_name)
			for it in spr.get("items") or []:
				_add(
					getattr(it, "quality", None) or "",
					getattr(it, "color", None) or getattr(it, "colour", None) or "",
					getattr(it, "gsm", None) or getattr(it, "custom_fabric_gsm", None) or 0,
				)
			for sj in spr.get("shaft_jobs") or []:
				_add(
					getattr(sj, "quality", None) or "",
					getattr(sj, "color", None) or getattr(sj, "colour", None) or "",
					getattr(sj, "gsm", None) or 0,
				)

	if not order_code:
		order_code = _gsm_order_code_for_pp(pp_id)
	qualities_out = []
	for q in qualities.values():
		colours_out = []
		for c in q["colours"].values():
			colours_out.append({"colour": c["colour"], "gsms": sorted(c["gsms"])})
		qualities_out.append({"quality": q["quality"], "colours": colours_out})
	return {
		"pp_id": pp_id,
		"spr_name": spr_name,
		"order_code": order_code,
		"fabric_type": _fabric_type_for_pp(pp_id, spr_name),
		"qualities": qualities_out,
	}


@frappe.whitelist()
def get_shaft_lot_sample(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	doc_name=None,
	spr_names=None,
	pp_ids=None,
):
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	options = get_lot_sample_order_options(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		spr_names=spr_names,
		pp_ids=pp_ids,
	)
	if found:
		payload = _doc_payload(frappe.get_doc("Shaft Lot Sample", found), {"options": options})
		return payload
	return {
		"name": "",
		"run_date": str(run_date or ""),
		"shift": _normalize_shift(shift),
		"custom_unit": _cstr(custom_unit),
		"gsm_shift_session": _cstr(gsm_shift_session),
		"rows": [],
		"options": options,
		"company": _company_name(),
		"unit_label": _unit_label(custom_unit),
	}


@frappe.whitelist()
def get_lot_sample_order_options(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	spr_names=None,
	pp_ids=None,
):
	from production_entry.production_planning.unified_production_entry_api import (
		_gsm_draft_sprs_for_session,
		get_gsm_lamination_order_board,
		get_gsm_pp_job_board,
	)

	pp_ids = _session_pp_ids(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		spr_names=spr_names,
		extra_pp_ids=pp_ids,
	)
	if not pp_ids:
		return {"orders": []}

	board = get_gsm_pp_job_board(
		pp_ids=pp_ids, run_date=run_date, shift=shift, unit=custom_unit
	) or {}
	by_pp = board.get("by_pp") or {}
	# Lamination FG board carries quality/color/gsm on the order row (no fabric shaft jobs)
	unit_l = _cstr(custom_unit).lower()
	if "lam" in unit_l:
		try:
			lam = get_gsm_lamination_order_board(run_date=run_date, unit=custom_unit) or {}
		except Exception:
			lam = {}
		pp_set = set(pp_ids)
		for order in lam.get("orders") or []:
			pp = _cstr(order.get("pp_id") or "")
			if not pp or (pp_set and pp not in pp_set):
				continue
			jobs = by_pp.setdefault(pp, [])
			jobs.append(
				{
					"order_code": order.get("order_code") or "",
					"quality": order.get("quality") or "",
					"color": order.get("color") or order.get("colour") or "",
					"gsm": order.get("gsm")
					or order.get("fabric_gsm")
					or order.get("lam_gsm")
					or 0,
				}
			)

	spr_by_pp = {}
	for spr in _gsm_draft_sprs_for_session(run_date, shift, custom_unit):
		pp = _cstr(spr.get("pp_id"))
		if pp and spr.get("spr_name"):
			spr_by_pp[pp] = spr.get("spr_name")

	merged = {}
	for pp_id in pp_ids:
		combo = _combo_from_jobs(by_pp.get(pp_id) or [], pp_id, spr_by_pp.get(pp_id, ""))
		code = _cstr(combo.get("order_code") or pp_id)
		if not code:
			continue
		combo["order_code"] = code
		if code not in merged:
			merged[code] = combo
			continue
		exist = merged[code]
		qmap = {q["quality"]: q for q in exist.get("qualities") or []}
		for q in combo.get("qualities") or []:
			if q["quality"] not in qmap:
				qmap[q["quality"]] = q
				continue
			cmap = {c["colour"]: c for c in qmap[q["quality"]].get("colours") or []}
			for c in q.get("colours") or []:
				if c["colour"] not in cmap:
					cmap[c["colour"]] = c
					continue
				gsms = list(cmap[c["colour"]].get("gsms") or [])
				for g in c.get("gsms") or []:
					if g not in gsms:
						gsms.append(g)
				cmap[c["colour"]]["gsms"] = sorted(gsms)
			qmap[q["quality"]]["colours"] = list(cmap.values())
		exist["qualities"] = list(qmap.values())
		exist["fabric_type"] = exist.get("fabric_type") or combo.get("fabric_type")
		exist["spr_name"] = exist.get("spr_name") or combo.get("spr_name")
	return {"orders": list(merged.values())}


@frappe.whitelist()
def save_shaft_lot_sample(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	doc_name=None,
	rows=None,
):
	rows = _parse_rows(rows)
	doc = _get_or_create(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	doc.samples = []
	for raw in rows:
		if not isinstance(raw, dict):
			continue
		order_code = _cstr(raw.get("order_code"))
		if not order_code:
			continue
		pp_id = _cstr(raw.get("pp_id"))
		spr_name = _cstr(raw.get("spr_name"))
		fabric_type = _cstr(raw.get("fabric_type")) or _fabric_type_for_pp(pp_id, spr_name)
		if not fabric_type:
			for opt in (get_lot_sample_order_options(
				run_date=run_date,
				shift=shift,
				custom_unit=custom_unit,
				gsm_shift_session=gsm_shift_session,
			).get("orders") or []):
				if _cstr(opt.get("order_code")) == order_code:
					fabric_type = _cstr(opt.get("fabric_type"))
					pp_id = pp_id or _cstr(opt.get("pp_id"))
					break
			if pp_id:
				fabric_type = fabric_type or _fabric_type_for_pp(pp_id, spr_name)
		doc.append(
			"samples",
			{
				"order_code": order_code,
				"quality": _cstr(raw.get("quality")),
				"colour": _cstr(raw.get("colour") or raw.get("color")),
				"gsm": cint(raw.get("gsm") or 0),
				"fabric_type": fabric_type,
				"no_of_lot_sample": cint(raw.get("no_of_lot_sample") or 0),
			},
		)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	options = get_lot_sample_order_options(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
	)
	return _doc_payload(doc, {"options": options})


@frappe.whitelist()
def get_lot_sample_label_html(
	run_date=None,
	shift=None,
	custom_unit=None,
	gsm_shift_session=None,
	doc_name=None,
	row_name=None,
	order_code=None,
	quality=None,
	colour=None,
	gsm=None,
	fabric_type=None,
	no_of_lot_sample=None,
):
	shift_n = _normalize_shift(shift)
	unit = _cstr(custom_unit)
	rd = getdate(run_date) if run_date else None
	found = _find_doc(
		run_date=run_date,
		shift=shift,
		custom_unit=custom_unit,
		gsm_shift_session=gsm_shift_session,
		name=doc_name,
	)
	row = None
	doc = None
	if found:
		doc = frappe.get_doc("Shaft Lot Sample", found)
		shift_n = doc.shift or shift_n
		unit = doc.custom_unit or unit
		rd = doc.run_date or rd
		want = _cstr(row_name)
		for r in doc.samples or []:
			if want and r.name == want:
				row = r
				break
	order_code = _cstr(getattr(row, "order_code", None) if row else order_code)
	quality = _cstr(getattr(row, "quality", None) if row else quality)
	colour = _cstr((getattr(row, "colour", None) if row else None) or colour)
	gsm_val = cint((getattr(row, "gsm", None) if row else None) or gsm or 0)
	fabric_type = _cstr((getattr(row, "fabric_type", None) if row else None) or fabric_type)
	if not fabric_type and order_code:
		opts = get_lot_sample_order_options(
			run_date=rd,
			shift=shift_n,
			custom_unit=unit,
			gsm_shift_session=gsm_shift_session,
		)
		for opt in opts.get("orders") or []:
			if _cstr(opt.get("order_code")) == order_code:
				fabric_type = _cstr(opt.get("fabric_type"))
				break
	copies = max(1, cint((getattr(row, "no_of_lot_sample", None) if row else None) or no_of_lot_sample or 1))
	date_txt = formatdate(rd, "dd-MM-yyyy") if rd else ""
	shift_txt = (shift_n or "").upper()
	spec = f"{gsm_val} GSM / {colour}".strip(" /") if gsm_val or colour else ""
	company = _company_name()
	unit_lbl = _unit_label(unit)

	def _cell(label, value):
		return f"""<tr>
			<td class="ls-k">{frappe.utils.escape_html(label)}</td>
			<td class="ls-v">{frappe.utils.escape_html(value or "")}</td>
		</tr>"""

	card = f"""
	<div class="ls-card">
		<div class="ls-co">{frappe.utils.escape_html(company)}</div>
		<div class="ls-hd">LOT SAMPLE DETAILS</div>
		<table class="ls-tbl">
			{_cell("UNIT", unit_lbl)}
			{_cell("CODE", order_code)}
			{_cell("SPEC", spec)}
			{_cell("QUALITY", quality)}
			{_cell("TYPE", fabric_type)}
			{_cell("DATE", f"{date_txt} / {shift_txt}".strip(" /"))}
		</table>
	</div>
	"""
	pages = "\n".join(card for _ in range(copies))
	html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Lot Sample Label</title>
<style>
@page {{ size: 90mm 60mm; margin: 4mm; }}
body {{ margin: 0; font-family: "Times New Roman", Times, serif; }}
.ls-card {{
	width: 82mm; border: 2px solid #000; padding: 4mm 3mm;
	page-break-after: always; box-sizing: border-box;
}}
.ls-card:last-child {{ page-break-after: auto; }}
.ls-co {{ text-align: center; font-weight: 700; font-size: 16px; letter-spacing: 0.5px; }}
.ls-hd {{
	text-align: center; font-weight: 700; font-size: 14px;
	border-top: 1.5px solid #000; border-bottom: 1.5px solid #000;
	padding: 3px 0; margin: 4px 0 6px;
}}
.ls-tbl {{ width: 100%; border-collapse: collapse; }}
.ls-tbl td {{ border: 1px solid #000; padding: 4px 6px; font-size: 13px; font-weight: 700; }}
.ls-k {{ width: 28%; }}
.ls-v {{ text-align: center; }}
</style></head>
<body>{pages}</body></html>"""
	return {"html": html, "copies": copies}
