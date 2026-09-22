# -*- coding: utf-8 -*-
"""Clubbing Sheet APIs — Planning Table Despatch rows + Madurai distances.

City comes from Sales Order shipping Address only (backend).
Order list comes from Planning sheet / Planning Table (movement Despatch).
"""

from __future__ import annotations

import json

import frappe
import requests
from frappe import _
from frappe.utils import cint, cstr, flt, getdate


def _cstr(v):
	return cstr(v or "").strip()


def _planned_date_expr():
	"""Prefer custom_item_planned_date, else planned_date."""
	has_custom = frappe.db.has_column("Planning Table", "custom_item_planned_date")
	has_planned = frappe.db.has_column("Planning Table", "planned_date")
	if has_custom and has_planned:
		return "coalesce(nullif(pt.custom_item_planned_date, ''), pt.planned_date)"
	if has_custom:
		return "pt.custom_item_planned_date"
	if has_planned:
		return "pt.planned_date"
	return "null"


def _pt_col_expr(candidates, alias, default="''"):
	"""First existing Planning Table column as SQL expr."""
	for col in candidates:
		if frappe.db.has_column("Planning Table", col):
			return f"ifnull(pt.{col}, {default}) as {alias}"
	return f"{default} as {alias}"


@frappe.whitelist()
def get_planning_orders_for_clubbing(party_code=None, planned_date=None, customer=None, city=None):
	"""List Planning Table rows with movement Despatch for Clubbing Get Orders.

	One row per Planning Table line (user picks which planned-date items to club).
	City is joined from Sales Order → Address (not from Planning).
	"""
	pc_filter = _cstr(party_code).lower()
	pd_filter = _cstr(planned_date)
	cu_filter = _cstr(customer).lower()
	city_filter = _cstr(city).lower()

	assigned_ptr = set()
	assigned_so = set()
	assigned_party = set()
	has_csi_ptr = frappe.db.exists("DocType", "Clubbing Sheet Item") and frappe.db.has_column(
		"Clubbing Sheet Item", "custom_planning_table_row"
	)
	if frappe.db.exists("DocType", "Clubbing Sheet Item"):
		cols = ["sales_order", "party_code"]
		if has_csi_ptr:
			cols.append("custom_planning_table_row")
		col_sql = ", ".join(cols)
		for r in frappe.db.sql(
			f"""
			select {col_sql}
			from `tabClubbing Sheet Item`
			where ifnull(docstatus, 0) < 2
			""",
			as_dict=True,
		) or []:
			ptr = _cstr(r.get("custom_planning_table_row")) if has_csi_ptr else ""
			if ptr:
				assigned_ptr.add(ptr)
			elif not has_csi_ptr:
				# Legacy sheets without PT link — block whole SO/party
				if r.sales_order:
					assigned_so.add(_cstr(r.sales_order))
				if r.party_code:
					assigned_party.add(_cstr(r.party_code).upper())

	pdate_sql = _planned_date_expr()
	club_col = (
		"ifnull(pt.custom_clubbing_sheet, '') as custom_clubbing_sheet"
		if frappe.db.has_column("Planning Table", "custom_clubbing_sheet")
		else "'' as custom_clubbing_sheet"
	)
	quality_col = _pt_col_expr(["quality", "custom_quality"], "quality")
	color_col = _pt_col_expr(["color", "colour", "custom_color"], "color")
	gsm_col = _pt_col_expr(["gsm", "custom_gsm"], "gsm", "0")
	width_col = _pt_col_expr(
		[
			"width_inch",
			"width",
			"custom_width_inch",
			"size_inch",
			"width_in_inches",
			"custom_width_in_inches",
			"size",
			"custom_size",
		],
		"width_inch",
		"0",
	)
	width_mm_col = _pt_col_expr(
		["width_mm", "custom_width_mm", "width_in_mm"],
		"width_mm",
		"0",
	)

	rows = frappe.db.sql(
		f"""
		select
			pt.name as planning_table_row,
			ps.name as planning_sheet,
			ps.party_code,
			ps.customer,
			coalesce(c.customer_name, ps.customer) as customer_name,
			ps.sales_order,
			ps.planning_status,
			pt.item_code,
			ifnull(pt.qty, 0) as qty,
			ifnull(pt.no_of_rolls, 0) as no_of_rolls,
			ifnull(pt.total_weight, 0) as total_weight,
			ifnull(pt.custom_movement_type, '') as movement_type,
			{pdate_sql} as planned_date,
			{quality_col},
			{color_col},
			{gsm_col},
			{width_col},
			{width_mm_col},
			{club_col}
		from `tabPlanning Table` pt
		inner join `tabPlanning sheet` ps on ps.name = pt.parent
		left join `tabCustomer` c on c.name = ps.customer
		where ifnull(ps.docstatus, 0) < 2
		  and ifnull(ps.planning_status, '') != 'Cancelled'
		  and ifnull(pt.custom_movement_type, '') = 'Despatch'
		order by ps.party_code asc, planned_date asc, pt.idx asc
		limit 5000
		""",
		as_dict=True,
	)

	so_names = list({_cstr(r.sales_order) for r in rows if r.sales_order})
	city_by_so = {}
	ship_by_so = {}
	if so_names:
		addrs = frappe.db.sql(
			"""
			select so.name as so_name, ifnull(addr.city, '') as city,
			       ifnull(so.shipping_address_name, '') as shipping_address_name
			from `tabSales Order` so
			left join `tabAddress` addr on addr.name = so.shipping_address_name
			where so.name in %(names)s
			""",
			{"names": so_names},
			as_dict=True,
		)
		for a in addrs or []:
			city_by_so[_cstr(a.so_name)] = _cstr(a.city)
			ship_by_so[_cstr(a.so_name)] = _cstr(a.shipping_address_name)

	out = []
	for r in rows:
		so = _cstr(r.sales_order)
		party = _cstr(r.party_code)
		ptr = _cstr(r.planning_table_row)
		if ptr and ptr in assigned_ptr:
			continue
		if so and so in assigned_so:
			continue
		if party and party.upper() in assigned_party:
			continue
		if _cstr(r.custom_clubbing_sheet):
			continue

		row_city = city_by_so.get(so, "")
		if pc_filter and pc_filter not in party.lower() and pc_filter not in so.lower() and pc_filter not in _cstr(r.planning_sheet).lower():
			continue
		if cu_filter:
			blob = f"{_cstr(r.customer)} {_cstr(r.customer_name)}".lower()
			if cu_filter not in blob:
				continue
		if city_filter and city_filter not in row_city.lower():
			continue
		if pd_filter:
			row_pd = _cstr(r.planned_date)
			if not row_pd or _cstr(getdate(row_pd)) != _cstr(getdate(pd_filter)):
				# also allow YYYY-MM-DD string compare
				if row_pd[:10] != pd_filter[:10]:
					continue

		# Board row Qty (KG) from Planned items — not sheet-level total_weight
		weight = flt(r.get("qty"))
		if weight <= 0:
			weight = flt(r.total_weight)
		width_inch = flt(r.get("width_inch"))
		# Data fields may store "16" or "16\"" — coerce
		if width_inch <= 0:
			raw_w = _cstr(r.get("width_inch"))
			if raw_w:
				try:
					width_inch = flt("".join(ch for ch in raw_w if ch.isdigit() or ch == "."))
				except Exception:
					width_inch = 0
		if width_inch <= 0:
			wmm = flt(r.get("width_mm"))
			if wmm > 0:
				width_inch = round(wmm / 25.4, 2)
		if width_inch <= 0:
			# Fallback: last 3 digits of item code as width*10 (e.g. …160 → 16.0")
			ic = _cstr(r.item_code)
			digits = "".join(ch for ch in ic if ch.isdigit())
			if len(digits) >= 3:
				code = cint(digits[-3:])
				if 20 <= code <= 500:
					width_inch = round(code / 10.0, 2)
		out.append(
			{
				"name": _cstr(r.planning_table_row),
				"planning_table_row": _cstr(r.planning_table_row),
				"planning_sheet": _cstr(r.planning_sheet),
				"party_code": party,
				"customer": _cstr(r.customer),
				"customer_name": _cstr(r.customer_name or r.customer),
				"sales_order": so,
				"city": row_city,
				"shipping_address_name": ship_by_so.get(so, ""),
				"custom_party_code": party,
				"item_code": _cstr(r.item_code),
				"quality": _cstr(r.get("quality")),
				"color": _cstr(r.get("color")),
				"gsm": flt(r.get("gsm")),
				"width_inch": width_inch,
				"inch": width_inch,
				"planned_date": _cstr(r.planned_date)[:10] if r.planned_date else "",
				"total_qty": weight,
				"no_of_rolls": flt(r.no_of_rolls),
				"weight_kgs": weight,
				"movement_type": "Despatch",
				"source": "planning_table",
			}
		)

	return out


def _discard_password_decrypt_messages():
	"""frappe.throw still leaves entries in message_log after we catch them."""
	try:
		log = getattr(frappe.local, "message_log", None)
		if not log:
			return
		kept = []
		needles = (
			"encrypt",
			"password not found",
			"site_config.json",
			"google_api_key",
			"google_maps_api_key",
			"routes_api_key",
			"jsb integrations",
		)
		for m in log:
			if isinstance(m, dict):
				text = str(m.get("message") or m.get("title") or m)
			else:
				text = str(m)
			low = text.lower()
			if any(n in low for n in needles):
				continue
			kept.append(m)
		frappe.local.message_log = kept
	except Exception:
		pass


def _google_api_key():
	"""Read key from JSB Integrations (Password-safe) or site_config. Never surface decrypt errors."""
	key = ""
	if frappe.db.exists("DocType", "JSB Integrations"):
		for fieldname in ("google_api_key", "google_maps_api_key", "routes_api_key"):
			try:
				key = (
					frappe.utils.password.get_decrypted_password(
						"JSB Integrations",
						"JSB Integrations",
						fieldname=fieldname,
						raise_exception=False,
					)
					or ""
				)
			except Exception:
				key = ""
			_discard_password_decrypt_messages()
			if key:
				break
		if not key:
			for fieldname in ("google_api_key", "google_maps_api_key", "routes_api_key"):
				try:
					key = frappe.db.get_single_value("JSB Integrations", fieldname) or ""
				except Exception:
					key = ""
				if key:
					break
	if not key:
		key = frappe.conf.get("google_maps_api_key") or frappe.conf.get("google_api_key") or ""
	_discard_password_decrypt_messages()
	return _cstr(key)


# Hardcoded Madurai road distances (km) — used when Google key fails
MADURAI_DISTANCES = {
	"Madurai": 0,
	"Melur": 30,
	"Usilampatti": 45,
	"Manamadurai": 60,
	"Sivaganga": 55,
	"Dindigul": 65,
	"Virudhunagar": 65,
	"Theni": 70,
	"Srivilliputhur": 75,
	"Aruppukottai": 80,
	"Sivakasi": 80,
	"Karaikudi": 95,
	"Karur": 140,
	"Tiruchirappalli": 135,
	"Trichy": 135,
	"Tirunelveli": 155,
	"Thoothukudi": 160,
	"Tuticorin": 160,
	"Coimbatore": 210,
	"Tirupur": 215,
	"Ernakulam": 220,
	"Kochi": 220,
	"Cochin": 220,
	"Salem": 220,
	"Erode": 240,
	"Thrissur": 280,
	"Thiruvananthapuram": 290,
	"Trivandrum": 290,
	"Mysuru": 370,
	"Mysore": 370,
	"Hosur": 385,
	"Pondicherry": 395,
	"Puducherry": 395,
	"Bengaluru": 445,
	"Bangalore": 445,
	"Chennai": 455,
	"Mangaluru": 490,
	"Mangalore": 490,
	"Guntur": 650,
	"Vijayawada": 680,
	"Hyderabad": 770,
	"Pune": 1250,
	"Mumbai": 1450,
}


def _fallback_distance(city):
	if not city:
		return 0
	if city in MADURAI_DISTANCES:
		return MADURAI_DISTANCES[city]
	lower = city.lower()
	for k, v in MADURAI_DISTANCES.items():
		if k.lower() == lower or lower in k.lower() or k.lower() in lower:
			return v
	return 0


@frappe.whitelist()
def get_distances_from_madurai(cities=None):
	"""Return {city: km}. Uses Google Routes if key works; else hardcoded map (no throw)."""
	if isinstance(cities, str):
		try:
			cities = json.loads(cities)
		except Exception:
			cities = [cities] if cities else []
	cities = [_cstr(c) for c in (cities or []) if _cstr(c)]
	if not cities:
		return {}

	fallback = {c: _fallback_distance(c) for c in cities}
	key = _google_api_key()
	if not key:
		return fallback

	body = {
		"origins": [{"waypoint": {"address": "Madurai, Tamil Nadu, India"}}],
		"destinations": [{"waypoint": {"address": f"{c}, India"}} for c in cities],
		"travelMode": "DRIVE",
		"routingPreference": "TRAFFIC_UNAWARE",
	}
	url = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
	headers = {
		"Content-Type": "application/json",
		"X-Goog-Api-Key": key,
		"X-Goog-FieldMask": "originIndex,destinationIndex,distanceMeters,status",
	}

	try:
		resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=20)
		if resp.status_code != 200:
			frappe.log_error(
				f"Routes API {resp.status_code}: {(resp.text or '')[:500]}",
				"get_distances_from_madurai",
			)
			return fallback
		data = resp.json()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "get_distances_from_madurai")
		return fallback

	distance_map = dict(fallback)
	if isinstance(data, list):
		for el in data:
			idx = el.get("destinationIndex")
			meters = el.get("distanceMeters")
			if idx is not None and meters is not None and idx < len(cities):
				distance_map[cities[idx]] = int(round(flt(meters) / 1000.0))
	return distance_map
