# -*- coding: utf-8 -*-
"""Clubbing Sheet document hooks — before_save validation + stamp / clear Planning."""

from __future__ import annotations

import frappe
from frappe.utils import cint, cstr, flt

# Shared Madurai → destination belts (lowercase city names).
ROUTE_BELTS = [
	["madurai", "virudhunagar", "sivakasi", "tuticorin", "thoothukudi"],
	["madurai", "karur", "coimbatore"],
	["madurai", "karur", "erode", "salem"],
	["madurai", "dindigul", "karur", "salem"],
	["madurai", "pondicherry", "puducherry", "vellore", "kanchipuram", "chennai"],
	["madurai", "trivandrum", "thiruvananthapuram", "changanacherry"],
	["madurai", "kollam", "kayankulam", "pathanamthitta", "kottayam"],
	["madurai", "coimbatore", "palakkad", "trissur", "thrissur", "ernakulam"],
	[
		"madurai", "coimbatore", "pallakad", "trissur", "thrissur", "malappuram",
		"kozhikode", "calicut", "mahe", "kannur", "kasargod", "mangaluru",
		"mangalore", "uduppi", "udupi",
	],
	["madurai", "mysore", "mysuru", "hassan", "shimoga", "dawangeree", "davangere", "davanagere"],
	["madurai", "salem", "hosur", "bangalore", "bengaluru", "dawangeree", "davangere", "davanagere"],
	["madurai", "mysore", "mysuru", "bangalore", "bengaluru"],
	["madurai", "bangalore", "bengaluru", "tumkur", "hospet", "hospete", "koppal"],
	["madurai", "ananthapur", "kurnool", "hyderabad", "karimnagar"],
	["madurai", "ananthapur", "kurnool", "hyderabad", "nizambad"],
	["madurai", "kurnool", "hyderabad", "warangal"],
	["madurai", "vizag", "bhuvaneswar", "bhubaneswar", "cuttack"],
	["madurai", "brahmbur", "berhampur", "bhubaneswar", "cuttack"],
	["madurai", "guntur", "vijayawada", "kakinada"],
	["madurai", "kakinada", "vizag"],
	["madurai", "kuppam", "palamaner", "bangalore", "bengaluru"],
	["madurai", "bangalore", "bengaluru", "hospete", "hospet", "vijayapura"],
	["madurai", "bangalore", "bengaluru", "belgaum", "goa"],
	[
		"madurai", "bangalore", "bengaluru", "hospete", "hospet", "vijayapura",
		"satara", "pune", "mumbai",
	],
]

MADURAI_DISTANCES = {
	"Madurai": 0, "Melur": 30, "Usilampatti": 45, "Manamadurai": 60,
	"Sivaganga": 55, "Dindigul": 65, "Virudhunagar": 65, "Theni": 70,
	"Srivilliputhur": 75, "Aruppukottai": 80, "Sivakasi": 80,
	"Periyakulam": 85, "Tiruppathur": 90, "Oddanchatram": 95, "Sattur": 95,
	"Cumbum": 90, "Pudukkottai": 100, "Rajapalayam": 100,
	"Paramakudi": 130, "Kovilpatti": 130, "Palani": 120, "Ramanathapuram": 115,
	"Kodaikanal": 115, "Thenkasi": 115, "Tenkasi": 115, "Courtallam": 120,
	"Sankarankoil": 125, "Kumily": 130, "Gudalur": 130, "Munnar": 140,
	"Idukki": 145, "Karur": 140, "Tiruchirappalli": 135, "Trichy": 135,
	"Tirunelveli": 155, "Thoothukudi": 160, "Tuticorin": 160, "Perambalur": 160,
	"Ariyalur": 175, "Thanjavur": 175, "Pollachi": 180, "Kottayam": 185,
	"Namakkal": 200, "Alappuzha": 200, "Alleppey": 200, "Thodupuzha": 205,
	"Padmanabhapuram": 205, "Coimbatore": 210, "Tirupur": 215, "Kumbakonam": 215,
	"Ernakulam": 220, "Kochi": 220, "Cochin": 220, "Salem": 220,
	"Nagercoil": 230, "Erode": 240, "Muvattupuzha": 235, "Muvuttupuzha": 235,
	"Pala": 195, "Changanacherry": 195, "Pathanamthitta": 215,
	"Kollam": 250, "Quilon": 250, "Palakkad": 250, "Mayiladuthurai": 250,
	"Kanyakumari": 250, "Nilgiris": 265, "Ooty": 265, "Nagapattinam": 280,
	"Thrissur": 280, "Thiruvananthapuram": 290, "Trivandrum": 290,
	"Malappuram": 320, "Dharmapuri": 320, "Wayanad": 345, "Cuddalore": 365,
	"Kozhikode": 360, "Calicut": 360, "Villupuram": 370, "Mysuru": 370,
	"Mysore": 370, "Chamarajanagar": 390, "Mandya": 390, "Hosur": 385,
	"Pondicherry": 395, "Puducherry": 395, "Hassan": 395,
	"Tiruvannamalai": 400, "Virajpet": 405, "Kanchipuram": 440, "Vellore": 410,
	"Chengalpattu": 420, "Ramanagara": 420, "Kodagu": 420, "Madikeri": 420,
	"Kannur": 430, "Cannanore": 430, "Kolar": 430, "Bengaluru": 445,
	"Bangalore": 445, "Chikmagalur": 450, "Chennai": 455, "Tumkur": 475,
	"Puttur": 480, "Kasaragod": 490, "Mangaluru": 490, "Mangalore": 490,
	"Shimoga": 485, "Davangere": 510, "Davanagere": 510, "Nellore": 520, "Udupi": 530,
	"Tirupati": 530, "Hubli": 600, "Dharwad": 610, "Guntur": 650,
	"Vijayawada": 680, "Hyderabad": 770, "Warangal": 850, "Vizag": 970,
	"Berhampur": 1520, "Brahmapur": 1520, "Bhubaneswar": 1650,
	"Cuttack": 1680, "Puri": 1700, "Sambalpur": 1800, "Rourkela": 1850,
	"Krishnagiri": 355, "Pune": 1250, "Mumbai": 1450, "Chittoor": 480,
	"Karaikudi": 95,
}


def clubbing_sheet_before_save(doc, method=None):
	"""Customer fix, total weight, load type, route belt, distance, loading sequence."""
	_fix_customers_from_so(doc)
	_default_despatch_customer(doc)
	_set_total_weight(doc)
	_set_load_type(doc)
	_validate_route_belt(doc)
	_set_distances_and_loading_sequence(doc)
	# Avoid noisy link validation when customer display names are off
	doc.flags.ignore_links = True


def _default_despatch_customer(doc):
	"""Fill Despatch Customer from Planning/SO customer when blank."""
	for item in doc.get("items") or []:
		dc = cstr(item.get("custom_despatch_customer") or item.get("despatch_customer") or "").strip()
		if dc:
			continue
		fallback = cstr(item.get("customer") or "").strip()
		if not fallback:
			continue
		# Prefer Customer link id; if display name, resolve
		if frappe.db.exists("Customer", fallback):
			item.custom_despatch_customer = fallback
		else:
			found = frappe.db.get_value("Customer", {"customer_name": fallback}, "name")
			if found:
				item.custom_despatch_customer = found
			elif frappe.get_meta("Clubbing Sheet Item").has_field("custom_despatch_customer"):
				# leave blank if cannot resolve
				pass


def _fix_customers_from_so(doc):
	for item in doc.get("items") or []:
		so = cstr(item.get("sales_order") or "").strip()
		if not so:
			continue
		correct = frappe.db.get_value("Sales Order", so, "customer")
		if correct:
			item.customer = correct


def _set_total_weight(doc):
	total = 0.0
	for item in doc.get("items") or []:
		total += flt(item.get("weight_kgs"))
	doc.total_weight = total


def _set_load_type(doc):
	items = doc.get("items") or []
	if not items:
		doc.load_type = ""
		return

	customer_weights = {}
	for item in items:
		cust = cstr(item.get("customer") or "").strip()
		if not cust:
			continue
		customer_weights[cust] = customer_weights.get(cust, 0) + flt(item.get("weight_kgs"))

	customers = list(customer_weights.keys())
	full_load_customers = [c for c in customers if customer_weights[c] >= 5000]

	if full_load_customers:
		if len(customers) > 1:
			# Soft warning — planners may still save/submit mixed sheets
			frappe.msgprint(
				frappe._(
					"Customer {0} has a total weight of {1} kgs (>= 5000 kgs). "
					"Orders >= 5000 kgs are normally a Full Load alone — you can still save and submit."
				).format(full_load_customers[0], customer_weights[full_load_customers[0]]),
				title=frappe._("Full Load Warning"),
				indicator="orange",
				alert=True,
			)
			doc.load_type = "Part Load"
		else:
			doc.load_type = "Full Load"
	elif customers:
		doc.load_type = "Part Load"
	else:
		doc.load_type = ""


def _city_in_belt(city, belt):
	city = cstr(city or "").strip().lower()
	if not city:
		return False
	for bc in belt:
		if city == bc or city in bc or bc in city:
			return True
	return False


def _selected_cities(doc):
	out = []
	for item in doc.get("items") or []:
		c = cstr(item.get("party_location") or "").strip().lower()
		if c and c not in out:
			out.append(c)
	return out


def _validate_route_belt(doc):
	"""Soft warning only — never block Save / Submit (planners may club across belts)."""
	items = doc.get("items") or []
	if len(items) < 2:
		return
	selected = _selected_cities(doc)
	# One destination (or blank) cannot be a route conflict — same city twice is fine
	if len(selected) <= 1:
		return
	is_valid = any(all(_city_in_belt(city, belt) for city in selected) for belt in ROUTE_BELTS)
	if not is_valid:
		frappe.msgprint(
			frappe._(
				"Route conflict detected! Cities {0} do not fall together "
				"on any single established forward route/belt. You can still save and submit."
			).format(", ".join(selected)),
			title=frappe._("Route Conflict"),
			indicator="orange",
			alert=True,
		)


def _lookup_distance(city):
	city = cstr(city or "").strip()
	if not city:
		return 0
	if city in MADURAI_DISTANCES:
		return MADURAI_DISTANCES[city]
	lower = city.lower()
	for key, dist in MADURAI_DISTANCES.items():
		if key.lower() == lower:
			return dist
	for key, dist in MADURAI_DISTANCES.items():
		if lower in key.lower() or key.lower() in lower:
			return dist
	return 0


def _pick_active_belt(selected_cities):
	all_belt_cities = []
	for belt in ROUTE_BELTS:
		for bc in belt:
			if bc not in all_belt_cities:
				all_belt_cities.append(bc)

	known = []
	for city in selected_cities:
		if any(_city_in_belt(city, [bc]) for bc in all_belt_cities) and city not in known:
			known.append(city)

	for belt in ROUTE_BELTS:
		if known and all(_city_in_belt(city, belt) for city in known):
			return list(belt)

	best, max_matches = [], 0
	for belt in ROUTE_BELTS:
		matches = sum(1 for city in known if _city_in_belt(city, belt))
		if matches > max_matches:
			max_matches = matches
			best = list(belt)
	return best


def _loading_sequence_locked(doc):
	return cint(doc.get("custom_lock_loading_sequence")) > 0


def _loading_customer_key(item) -> str:
	"""One loading slot per order code — not per item row or despatch customer."""
	for key in ("party_code", "order_code", "sales_order"):
		val = cstr(item.get(key) or "").strip()
		if val:
			return val.upper()
	return cstr(item.get("name") or item.get("idx") or "")


def _item_sort_tuple(item, active_belt):
	"""Sort key for loading: farther from Madurai first → Inside.

	Distance is primary (Namakkal 200 before Karur 140). Belt index is only a
	tie-break when distances match — never override a larger distance.
	"""
	city_lower = cstr(item.get("party_location") or "").lower()
	dist = flt(item.get("distance_from_madurai"))
	if dist <= 0:
		dist = flt(_lookup_distance(item.get("party_location")))
	belt_idx = 0
	if active_belt:
		for idx, bc in enumerate(active_belt):
			if city_lower == bc or city_lower in bc or bc in city_lower:
				belt_idx = idx
				break
	return dist, belt_idx


def _sequence_labels_for_customer_count(n: int) -> list[str]:
	"""Inside / Center 1..10 / Outside — one label per distinct order code."""
	max_center = 10
	if n <= 0:
		return []
	if n == 1:
		return ["Full Load"]
	if n == 2:
		return ["Inside", "Outside"]
	labels = ["Inside"]
	middle = n - 2
	for i in range(middle):
		labels.append(f"Center {min(i + 1, max_center)}")
	labels.append("Outside")
	return labels


def _set_distances_and_loading_sequence(doc):
	items = doc.get("items") or []
	if not items:
		return

	for item in items:
		if not flt(item.get("distance_from_madurai")):
			item.distance_from_madurai = _lookup_distance(item.get("party_location"))

	if _loading_sequence_locked(doc):
		return

	if cstr(doc.get("load_type")) == "Full Load":
		for item in items:
			item.loading_sequence = "Full Load"
		return

	active_belt = _pick_active_belt(_selected_cities(doc))

	# Group rows by order code — one slot per order (I26125 Inside, all I2694 Outside).
	groups = {}  # key -> {dist, belt_idx, first_idx, items}
	order_keys = []
	for row_idx, item in enumerate(items):
		key = _loading_customer_key(item)
		dist, belt_idx = _item_sort_tuple(item, active_belt)
		if key not in groups:
			groups[key] = {"dist": dist, "belt_idx": belt_idx, "first_idx": row_idx, "items": []}
			order_keys.append(key)
		else:
			g = groups[key]
			if dist > g["dist"] or (dist == g["dist"] and belt_idx > g["belt_idx"]):
				g["dist"], g["belt_idx"] = dist, belt_idx
		groups[key]["items"].append(item)

	# Farther from Madurai first → Inside. Same city: keep sheet order (first order = Inside).
	ordered_keys = sorted(
		order_keys,
		key=lambda k: (-groups[k]["dist"], -groups[k]["belt_idx"], groups[k]["first_idx"]),
	)
	labels = _sequence_labels_for_customer_count(len(ordered_keys))
	for i, key in enumerate(ordered_keys):
		seq = labels[i] if i < len(labels) else f"Center {min(i, 10)}"
		for item in groups[key]["items"]:
			item.loading_sequence = seq

	# Re-order child rows: Inside customer block → centers → Outside
	flat = []
	for key in ordered_keys:
		flat.extend(groups[key]["items"])
	if flat and hasattr(doc, "set"):
		for idx, item in enumerate(flat, start=1):
			item.idx = idx
		doc.set("items", flat)


def clubbing_sheet_before_submit(doc, method=None):
	"""Require Trip ID; stamp only the selected Planning Table Despatch rows."""
	if not cstr(doc.get("trip_id") or "").strip():
		frappe.throw("Please enter a Trip ID before submitting the Clubbing Sheet.")

	if not doc.get("items"):
		return

	has_pt_club = frappe.db.has_column("Planning Table", "custom_clubbing_sheet")
	has_psi_club = frappe.db.has_column("Planning sheet Item", "custom_clubbing_sheet")
	if not has_pt_club and not has_psi_club:
		frappe.msgprint(
			"Planning club fields missing — run bench migrate (custom_clubbing_sheet).",
			indicator="orange",
			alert=True,
		)
		return

	missing = []
	for item in doc.items:
		seq = cstr(item.get("loading_sequence") or "").strip()
		load_order = cint(item.get("idx") or 0)
		ptr = cstr(item.get("planning_table_row") or item.get("custom_planning_table_row") or "").strip()

		matches = _resolve_pt_rows_for_item(item, club_name=doc.name)
		if not matches:
			label = cstr(item.get("party_code") or item.get("sales_order") or item.idx)
			missing.append(f"row {item.idx} ({label})")
			continue

		for m in matches:
			_stamp_pt(
				m.pt_name,
				doc.name,
				seq,
				load_order,
				has_pt_club,
				despatch_customer=cstr(item.get("custom_despatch_customer") or item.get("despatch_customer") or "").strip(),
				despatch_sales_order=cstr(
					item.get("custom_despatch_sales_order") or item.get("despatch_sales_order") or ""
				).strip(),
			)
			if has_psi_club:
				_stamp_psi_matching_pt(m.pt_name, m.parent, doc.name, seq, load_order)

	if missing:
		frappe.throw(
			"Could not map Clubbing items to Planning Table rows: "
			+ ", ".join(missing)
			+ ". Use Get Sales Orders again so each line stores Planning Table Row."
		)


def clubbing_sheet_on_cancel(doc, method=None):
	"""Clear club ID / loading sequence from Planning when Clubbing Sheet is cancelled."""
	_clear_stamps_for_club(doc.name)


@frappe.whitelist()
def clear_planning_stamps_for_club(clubbing_sheet=None):
	"""Repair helper — clear stamps for a cancelled (or any) Clubbing Sheet name."""
	name = cstr(clubbing_sheet or "").strip()
	if not name:
		frappe.throw("clubbing_sheet required")
	n = _clear_stamps_for_club(name)
	return {"cleared": n, "clubbing_sheet": name}


def _clear_stamps_for_club(club_name):
	club_name = cstr(club_name).strip()
	if not club_name:
		return 0
	cleared = 0
	for dt in ("Planning Table", "Planning sheet Item"):
		if not frappe.db.has_column(dt, "custom_clubbing_sheet"):
			continue
		rows = frappe.db.sql(
			f"""
			select name from `tab{dt}`
			where custom_clubbing_sheet = %s
			""",
			club_name,
			as_dict=True,
		) or []
		for r in rows:
			vals = {"custom_clubbing_sheet": ""}
			if frappe.db.has_column(dt, "custom_loading_sequence"):
				vals["custom_loading_sequence"] = ""
			if frappe.db.has_column(dt, "custom_club_load_order"):
				vals["custom_club_load_order"] = 0
			if frappe.db.has_column(dt, "custom_despatch_customer"):
				vals["custom_despatch_customer"] = ""
			if frappe.db.has_column(dt, "custom_despatch_sales_order"):
				vals["custom_despatch_sales_order"] = ""
			frappe.db.set_value(dt, r.name, vals, update_modified=False)
			cleared += 1
	return cleared


def _resolve_pt_rows_for_item(item, club_name=""):
	"""Resolve exact Planning Table row(s). Never stamp whole SO / party."""
	ptr = cstr(item.get("planning_table_row") or item.get("custom_planning_table_row") or "").strip()
	if ptr and frappe.db.exists("Planning Table", ptr):
		parent = frappe.db.get_value("Planning Table", ptr, "parent")
		return [frappe._dict(pt_name=ptr, parent=parent)]

	# Fallback: unique match by SO/party + weight + rolls (+ gsm if present)
	so = cstr(item.get("sales_order") or "").strip()
	party = cstr(item.get("party_code") or "").strip()
	planning_sheet = cstr(item.get("planning_sheet") or item.get("custom_planning_sheet") or "").strip()
	weight = flt(item.get("weight_kgs"))
	rolls = flt(item.get("no_of_rolls"))
	gsm = item.get("gsm")
	quality = cstr(item.get("quality") or "").strip()
	color = cstr(item.get("color") or "").strip()

	sql = """
		select pt.name as pt_name, pt.parent
		from `tabPlanning Table` pt
		inner join `tabPlanning sheet` ps on ps.name = pt.parent
		where ifnull(pt.custom_movement_type, '') = 'Despatch'
		  and ifnull(ps.docstatus, 0) < 2
		  and abs(ifnull(pt.total_weight, 0) - %(wt)s) < 0.02
		  and abs(ifnull(pt.no_of_rolls, 0) - %(rolls)s) < 0.01
	"""
	args = {"wt": weight, "rolls": rolls}

	if planning_sheet:
		sql += " and ps.name = %(ps)s"
		args["ps"] = planning_sheet
	elif so:
		sql += " and ps.sales_order = %(so)s"
		args["so"] = so
	elif party:
		sql += " and ps.party_code = %(party)s"
		args["party"] = party
	else:
		return []

	if frappe.db.has_column("Planning Table", "custom_clubbing_sheet"):
		sql += """
		  and (
		    ifnull(pt.custom_clubbing_sheet, '') = ''
		    or pt.custom_clubbing_sheet = %(club)s
		  )
		"""
		args["club"] = club_name or ""

	if gsm is not None and cstr(gsm) != "" and frappe.db.has_column("Planning Table", "gsm"):
		sql += " and abs(ifnull(pt.gsm, 0) - %(gsm)s) < 0.01"
		args["gsm"] = flt(gsm)
	if quality and frappe.db.has_column("Planning Table", "quality"):
		sql += " and ifnull(pt.quality, '') = %(quality)s"
		args["quality"] = quality
	if color and frappe.db.has_column("Planning Table", "color"):
		sql += " and ifnull(pt.color, '') = %(color)s"
		args["color"] = color

	rows = frappe.db.sql(sql, args, as_dict=True) or []
	# Only accept exact unique match — never stamp multiple ambiguous rows
	if len(rows) == 1:
		return rows
	return []


def _stamp_pt(pt_name, club_name, seq, load_order, has_pt_club, despatch_customer="", despatch_sales_order=""):
	if not has_pt_club:
		return
	vals = {"custom_clubbing_sheet": club_name}
	if frappe.db.has_column("Planning Table", "custom_loading_sequence"):
		vals["custom_loading_sequence"] = seq
	if frappe.db.has_column("Planning Table", "custom_club_load_order"):
		vals["custom_club_load_order"] = load_order
	if despatch_customer and frappe.db.has_column("Planning Table", "custom_despatch_customer"):
		vals["custom_despatch_customer"] = despatch_customer
	if frappe.db.has_column("Planning Table", "custom_despatch_sales_order"):
		vals["custom_despatch_sales_order"] = despatch_sales_order or ""
	frappe.db.set_value("Planning Table", pt_name, vals, update_modified=False)


def _stamp_psi_matching_pt(pt_name, parent, club_name, seq, load_order):
	"""Stamp PSI only when we can uniquely match the PT line — never all Despatch PSI."""
	if not parent or not frappe.db.has_column("Planning sheet Item", "custom_clubbing_sheet"):
		return

	fields = ["item_code", "total_weight", "no_of_rolls"]
	for f in ("gsm", "quality", "color"):
		if frappe.db.has_column("Planning Table", f):
			fields.append(f)
	pt = frappe.db.get_value("Planning Table", pt_name, fields, as_dict=True)
	if not pt:
		return

	sql = """
		select name from `tabPlanning sheet Item`
		where parent = %(parent)s
		  and ifnull(custom_movement_type, '') = 'Despatch'
		  and abs(ifnull(total_weight, 0) - %(wt)s) < 0.02
		  and abs(ifnull(no_of_rolls, 0) - %(rolls)s) < 0.01
	"""
	args = {
		"parent": parent,
		"wt": flt(pt.total_weight),
		"rolls": flt(pt.no_of_rolls),
	}
	if pt.item_code and frappe.db.has_column("Planning sheet Item", "item_code"):
		sql += " and ifnull(item_code, '') = %(ic)s"
		args["ic"] = cstr(pt.item_code)

	psi_rows = frappe.db.sql(sql, args, as_dict=True) or []
	if len(psi_rows) != 1:
		return

	pvals = {"custom_clubbing_sheet": club_name}
	if frappe.db.has_column("Planning sheet Item", "custom_loading_sequence"):
		pvals["custom_loading_sequence"] = seq
	if frappe.db.has_column("Planning sheet Item", "custom_club_load_order"):
		pvals["custom_club_load_order"] = load_order
	frappe.db.set_value("Planning sheet Item", psi_rows[0].name, pvals, update_modified=False)
