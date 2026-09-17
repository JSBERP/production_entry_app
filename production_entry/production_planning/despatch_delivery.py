# -*- coding: utf-8 -*-
"""Delivery Note creation from Despatch Approval.

DN.customer = Despatch Customer (override).
against_sales_order is set only when that customer matches the Planning SO
(or an explicit Despatch Sales Order is set) — avoids SO qty conflict on
emergency reallocation (e.g. Dharshan plan → Gowtham DN).
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import flt, getdate

from production_entry.production_planning.despatch_logistics import _cstr, _fg_warehouse_for_company, _resolve_customer


def _line_get(ln, key, default=""):
	if isinstance(ln, dict):
		return ln.get(key, default)
	return getattr(ln, key, default)


def _line_despatch_customer(ln):
	"""Customer id for DN — prefer custom_despatch_customer, else resolve customer_name."""
	dc = _cstr(_line_get(ln, "custom_despatch_customer") or _line_get(ln, "despatch_customer"))
	if dc and frappe.db.exists("Customer", dc):
		return dc
	return _resolve_customer(_line_get(ln, "customer_name")) or ""


def _planning_sales_order(ln):
	"""Order-giver SO from Planning sheet / line (audit source)."""
	so = _cstr(_line_get(ln, "sales_order"))
	if so:
		return so
	ps = _cstr(_line_get(ln, "planning_sheet"))
	if ps and frappe.db.exists("Planning sheet", ps):
		return _cstr(frappe.db.get_value("Planning sheet", ps, "sales_order"))
	pc = _cstr(_line_get(ln, "party_code"))
	if pc:
		found = frappe.db.get_value("Planning sheet", {"party_code": pc}, "sales_order")
		return _cstr(found)
	return ""


def _resolve_against_sales_order(ln, despatch_customer):
	"""Link SO only when it belongs to Despatch Customer (or explicit override)."""
	override = _cstr(
		_line_get(ln, "custom_despatch_sales_order") or _line_get(ln, "despatch_sales_order")
	)
	if override and frappe.db.exists("Sales Order", override):
		return override

	so = _planning_sales_order(ln)
	if not so or not despatch_customer:
		return ""
	so_cust = _cstr(frappe.db.get_value("Sales Order", so, "customer"))
	if so_cust and so_cust == _cstr(despatch_customer):
		return so
	return ""


def _apply_dn_addresses(dn, customer, sales_order=None):
	"""Prefill billing/shipping from linked SO, else Customer defaults."""
	ship = bill = ""
	if sales_order and frappe.db.exists("Sales Order", sales_order):
		row = frappe.db.get_value(
			"Sales Order",
			sales_order,
			["shipping_address_name", "customer_address"],
			as_dict=True,
		)
		if row:
			ship = _cstr(row.shipping_address_name)
			bill = _cstr(row.customer_address)

	if not ship or not bill:
		# Dynamic Link Customer → Address
		addrs = frappe.db.sql(
			"""
			select parent, ifnull(is_shipping_address, 0) as is_ship,
			       ifnull(is_primary_address, 0) as is_primary
			from `tabDynamic Link`
			where link_doctype = 'Customer' and link_name = %s
			  and parenttype = 'Address'
			""",
			customer,
			as_dict=True,
		) or []
		# Prefer Address flags when columns exist
		for a in addrs:
			aname = _cstr(a.parent)
			if not aname:
				continue
			flags = {}
			if frappe.db.has_column("Address", "is_shipping_address"):
				flags["ship"] = cint_safe(frappe.db.get_value("Address", aname, "is_shipping_address"))
			if frappe.db.has_column("Address", "is_primary_address"):
				flags["primary"] = cint_safe(frappe.db.get_value("Address", aname, "is_primary_address"))
			if not ship and flags.get("ship"):
				ship = aname
			if not bill and flags.get("primary"):
				bill = aname
		if not ship and addrs:
			ship = _cstr(addrs[0].parent)
		if not bill and addrs:
			bill = _cstr(addrs[0].parent)

	if ship and hasattr(dn, "shipping_address_name"):
		dn.shipping_address_name = ship
	if bill and hasattr(dn, "customer_address"):
		dn.customer_address = bill


def cint_safe(v):
	try:
		return int(v or 0)
	except Exception:
		return 0


def _doc_has_field(doctype, fieldname):
	if not doctype or not fieldname:
		return False
	try:
		return bool(frappe.get_meta(doctype).has_field(fieldname))
	except Exception:
		return bool(frappe.db.has_column(doctype, fieldname))


def _set_dn_field(dn, fieldname, value):
	val = _cstr(value).strip() if value is not None else ""
	if not val or not _doc_has_field("Delivery Note", fieldname):
		return
	dn.set(fieldname, val)


def _club_field_value(club, *fieldnames):
	for fn in fieldnames:
		if not _doc_has_field("Clubbing Sheet", fn):
			continue
		val = _cstr(club.get(fn)).strip()
		if val:
			return fn, val
	return None, ""


def _driver_link_doctype():
	try:
		df = frappe.get_meta("Clubbing Sheet").get_field("driver")
		if df and df.fieldtype == "Link" and _cstr(df.options):
			return _cstr(df.options)
	except Exception:
		pass
	return "Driver"


def _resolve_transporter(value):
	val = _cstr(value).strip()
	if not val:
		return ""
	if frappe.db.exists("Supplier", val):
		return val
	found = frappe.db.get_value("Supplier", {"supplier_name": val}, "name")
	if found:
		return found
	if frappe.db.has_column("Supplier", "is_transporter"):
		found = frappe.db.get_value(
			"Supplier", {"supplier_name": val, "is_transporter": 1}, "name"
		)
		if found:
			return found
	return val


def _value_from_driver_master(driver, driver_dt, *fieldnames):
	if not driver or not driver_dt or not frappe.db.exists(driver_dt, driver):
		return ""
	for fn in fieldnames:
		if not frappe.db.has_column(driver_dt, fn):
			continue
		val = _cstr(frappe.db.get_value(driver_dt, driver, fn)).strip()
		if val:
			return val
	return ""


def _apply_clubbing_transporter_to_dn(dn, despatch_approval):
	"""Copy transport fields from linked Clubbing Sheet onto Delivery Note."""
	if not frappe.db.exists("DocType", "Clubbing Sheet"):
		return
	club_name = _cstr(getattr(despatch_approval, "custom_clubbing_sheet", None) or "")
	if not club_name or not frappe.db.exists("Clubbing Sheet", club_name):
		return

	club = frappe.get_doc("Clubbing Sheet", club_name)

	_, logistics = _club_field_value(club, "logistics_partner", "transporter")
	if logistics:
		_set_dn_field(dn, "transporter", _resolve_transporter(logistics))

	_, driver = _club_field_value(club, "driver")
	driver_dt = _driver_link_doctype()
	if driver:
		_set_dn_field(dn, "driver", driver)
		driver_name = _value_from_driver_master(
			driver, driver_dt, "driver_name", "full_name", "employee_name", "name1"
		)
		if driver_name:
			_set_dn_field(dn, "driver_name", driver_name)

	_, vehicle_no = _club_field_value(club, "vehicle_no")
	if vehicle_no:
		_set_dn_field(dn, "vehicle_no", vehicle_no)

	_, driver_phone = _club_field_value(club, "driver_ph_no", "driver_phone", "driver_ph")
	if not driver_phone and driver:
		driver_phone = _value_from_driver_master(
			driver, driver_dt, "cell_number", "mobile_no", "phone", "driver_ph_no", "contact_number"
		)
	if driver_phone:
		# Prefer custom_driver_ph_no (Logistics Kanban / form field); also set aliases if present.
		phone_fields = ("custom_driver_ph_no", "driver_ph_no", "driver_phone", "driver_ph")
		for fn in phone_fields:
			if _doc_has_field("Delivery Note", fn):
				_set_dn_field(dn, fn, driver_phone)


def _match_so_detail(sales_order, item_code, qty):
	"""Best-effort SO Item name for against_sales_order_item / so_detail."""
	if not sales_order or not item_code:
		return ""
	rows = frappe.db.sql(
		"""
		select name, qty, delivered_qty
		from `tabSales Order Item`
		where parent = %s and item_code = %s
		order by idx asc
		""",
		(sales_order, item_code),
		as_dict=True,
	) or []
	if len(rows) == 1:
		return rows[0].name
	# Prefer line with remaining qty covering this delivery
	need = flt(qty)
	for r in rows:
		remaining = flt(r.qty) - flt(r.delivered_qty)
		if remaining + 0.01 >= need:
			return r.name
	return rows[0].name if rows else ""


def _group_despatch_lines_by_item(lines):
	"""Group despatch approval lines by item_code — sum qty, collect batches."""
	groups = {}
	for ln in lines or []:
		qty = flt(_line_get(ln, "qty")) or flt(_line_get(ln, "net_weight"))
		if qty <= 0:
			continue
		item_code = _cstr(_line_get(ln, "item_code"))
		if not item_code:
			continue
		grp = groups.setdefault(item_code, {"qty": 0.0, "lines": [], "batches": []})
		grp["qty"] += qty
		grp["lines"].append(ln)
		bn = _cstr(_line_get(ln, "batch_no"))
		if bn:
			grp["batches"].append((bn, qty))
	return groups


def _batch_row_as_dict(batch_no):
	if not batch_no or not frappe.db.exists("Batch", batch_no):
		return {}
	fields = ["item", "batch_qty"]
	for fn in (
		"custom_quality",
		"quality",
		"custom_color",
		"color",
		"custom_gsm",
		"gsm",
		"custom_width_inch",
		"width_inch",
		"custom_width",
		"width",
		"meter_roll",
		"meter_per_roll",
		"custom_meter",
		"custom_meter_roll",
		"custom_meter_per_roll",
		"custom_length_mtrs",
		"length_mtrs",
		"net_weight",
		"custom_net_weight",
		"gross_weight",
		"custom_gross_weight",
	):
		if frappe.db.has_column("Batch", fn):
			fields.append(fn)
	return frappe.db.get_value("Batch", batch_no, fields, as_dict=True) or {}


def _roll_detail_from_batch(batch_no, qty=None):
	"""Build roll print row from Batch master (quality/color/gsm/width/meters)."""
	meta = _batch_row_as_dict(batch_no)
	item_code = _cstr(meta.get("item"))
	item_name = ""
	if item_code:
		item_name = _cstr(frappe.db.get_value("Item", item_code, "item_name") or "")

	net_weight = (
		flt(qty)
		if qty is not None and flt(qty) > 0
		else (
			flt(meta.get("custom_net_weight"))
			or flt(meta.get("net_weight"))
			or flt(meta.get("batch_qty"))
			or 0
		)
	)
	quality = _cstr(meta.get("custom_quality") or meta.get("quality") or "")
	color = _cstr(meta.get("custom_color") or meta.get("color") or "")
	gsm = cint_safe(meta.get("custom_gsm") or meta.get("gsm") or 0)
	width = flt(
		meta.get("custom_width_inch")
		or meta.get("width_inch")
		or meta.get("custom_width")
		or meta.get("width")
		or 0
	)
	meter = flt(
		meta.get("custom_meter")
		or meta.get("custom_meter_roll")
		or meta.get("custom_meter_per_roll")
		or meta.get("meter_roll")
		or meta.get("meter_per_roll")
		or meta.get("custom_length_mtrs")
		or meta.get("length_mtrs")
		or 0
	)

	# Fill gaps from FG item code / item name (same path SPR uses)
	if item_code and (not quality or not color or gsm <= 0 or width <= 0):
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
				gsm = cint_safe(specs.get("gsm") or 0)
			if width <= 0:
				width = flt(specs.get("width_inch") or 0)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "despatch_delivery._roll_detail_from_batch specs")

	if item_code and (gsm <= 0 or width <= 0):
		try:
			from production_entry.production_planning.doctype.shaft_production_run.shaft_production_run import (
				parse_item_code,
			)

			parsed_gsm, parsed_w = parse_item_code(item_code)
			if gsm <= 0:
				gsm = cint_safe(parsed_gsm)
			if width <= 0:
				width = flt(parsed_w)
		except Exception:
			pass

	gross = (
		flt(meta.get("custom_gross_weight") or meta.get("gross_weight") or 0)
		or (net_weight + 2 if net_weight else 0)
	)
	return {
		"batch_no": _cstr(batch_no),
		"quality": quality,
		"color": color,
		"gsm": gsm,
		"width_inch": width,
		"net_weight": net_weight,
		"gross_weight": gross,
		"meter_roll": meter,
		"item_code": item_code,
	}


def _refresh_rolls_from_batch(rolls):
	"""Re-read quality/color/gsm/width/meters from Batch for each roll row."""
	out = []
	for r in rolls or []:
		if not isinstance(r, dict):
			continue
		bn = _cstr(r.get("batch_no"))
		if not bn:
			continue
		qty = flt(r.get("net_weight"))
		fresh = _roll_detail_from_batch(bn, qty if qty > 0 else None)
		# Keep explicit qty from stored JSON / despatch line when Batch qty differs
		if qty > 0:
			fresh["net_weight"] = qty
			if not flt(fresh.get("gross_weight")):
				fresh["gross_weight"] = qty + 2
		out.append(fresh)
	return out


def _rolls_json_for_batches(batch_entries):
	rolls = []
	for bn, qty in batch_entries or []:
		if not bn:
			continue
		rolls.append(_roll_detail_from_batch(bn, qty))
	return rolls


def _make_outward_batch_bundle(item_code, warehouse, batch_entries, company=None):
	"""Create Serial and Batch Bundle with all scanned batches for one DN line."""
	if not batch_entries or not frappe.db.exists("DocType", "Serial and Batch Bundle"):
		return ""
	if not frappe.db.has_column("Delivery Note Item", "serial_and_batch_bundle"):
		return ""
	bundle = frappe.new_doc("Serial and Batch Bundle")
	bundle.item_code = item_code
	bundle.warehouse = warehouse
	bundle.type_of_transaction = "Outward"
	bundle.voucher_type = "Delivery Note"
	bundle.has_batch_no = 1
	if company and hasattr(bundle, "company"):
		bundle.company = company
	for bn, qty in batch_entries:
		if not bn or flt(qty) <= 0:
			continue
		bundle.append(
			"entries",
			{"batch_no": bn, "qty": abs(flt(qty)), "warehouse": warehouse},
		)
	if not bundle.entries:
		return ""
	bundle.flags.ignore_permissions = True
	bundle.insert(ignore_permissions=True)
	return bundle.name


def _apply_batch_fields_to_dn_row(row, item_code, warehouse, batch_entries, company=None):
	"""Attach all scanned batches via Serial and Batch Bundle (never single batch_no for multi).

	Must set use_serial_batch_fields=0 so ERPNext uses the bundle instead of the
	legacy Batch No field (which only supports one batch per row).
	"""
	if not batch_entries:
		return
	has_batch = frappe.db.get_value("Item", item_code, "has_batch_no")
	if not has_batch:
		return

	# Always use Serial and Batch Bundle so all scanned rolls despatch on submit
	bundle = _make_outward_batch_bundle(item_code, warehouse, batch_entries, company=company)
	if bundle:
		row["serial_and_batch_bundle"] = bundle
		# Do not set batch_no when bundle has the batches — avoids overwrite
		if frappe.db.has_column("Delivery Note Item", "use_serial_batch_fields"):
			row["use_serial_batch_fields"] = 0
	elif len(batch_entries) == 1:
		# Fallback if bundle DocType/column missing
		row["batch_no"] = batch_entries[0][0]
		if frappe.db.has_column("Delivery Note Item", "use_serial_batch_fields"):
			row["use_serial_batch_fields"] = 1


def _link_dn_item_bundles(dn):
	"""After insert, point each Serial and Batch Bundle at its DN row."""
	if not dn or not dn.name:
		return
	for item in dn.items or []:
		bundle = _cstr(getattr(item, "serial_and_batch_bundle", None) or "")
		if not bundle or not frappe.db.exists("Serial and Batch Bundle", bundle):
			continue
		updates = {"voucher_no": dn.name, "voucher_detail_no": item.name}
		if frappe.db.has_column("Serial and Batch Bundle", "voucher_type"):
			updates["voucher_type"] = "Delivery Note"
		frappe.db.set_value("Serial and Batch Bundle", bundle, updates, update_modified=False)


def _order_code_from_lines(grp_lines, fallback_party_code=""):
	for ln in grp_lines or []:
		pc = _cstr(_line_get(ln, "party_code"))
		if pc:
			return pc
	return _cstr(fallback_party_code)


def build_delivery_note_from_despatch(despatch_approval, party_code=None, despatch_customer=None):
	"""Build Delivery Note doc (not saved) from approved despatch lines.

	Filters:
	- party_code is not None → only that Order Code
	- despatch_customer is not None → only that Despatch Customer
	"""
	da = despatch_approval
	if isinstance(da, str):
		da = frappe.get_doc("Despatch Approval", da)
	if not da.lines:
		frappe.throw(_("Despatch Approval has no lines."))

	fc = _cstr(da.from_company)
	wh = _fg_warehouse_for_company(fc)
	if not wh:
		frappe.throw(_("No finished-goods warehouse configured for {0}.").format(fc))

	use_pc = party_code is not None
	pc_filter = _cstr(party_code) if use_pc else None
	use_dc = despatch_customer is not None
	dc_filter = _cstr(despatch_customer) if use_dc else None

	lines = []
	for ln in da.lines or []:
		if use_pc and _cstr(_line_get(ln, "party_code")) != pc_filter:
			continue
		if use_dc and _line_despatch_customer(ln) != dc_filter:
			continue
		lines.append(ln)
	if not lines:
		frappe.throw(
			_("No despatch lines for order {0} / customer {1}.").format(
				pc_filter if use_pc else "—", dc_filter if use_dc else "—"
			)
		)

	customer = ""
	for ln in lines:
		customer = _line_despatch_customer(ln)
		if customer:
			break
	if not customer:
		frappe.throw(_("Could not resolve Despatch Customer from despatch lines."))

	# Resolve SO once from first matching line (conditional)
	against_so = ""
	for ln in lines:
		against_so = _resolve_against_sales_order(ln, customer)
		if against_so:
			break

	dn = frappe.new_doc("Delivery Note")
	dn.company = fc
	dn.customer = customer
	dn.set_posting_time = 1
	dn.posting_date = getdate()
	dn.set_warehouse = wh
	_apply_dn_addresses(dn, customer, against_so)
	_apply_clubbing_transporter_to_dn(dn, da)
	if _doc_has_field("Delivery Note", "custom_despatch_approval"):
		dn.custom_despatch_approval = da.name

	groups = _group_despatch_lines_by_item(lines)
	for item_code, grp in groups.items():
		total_qty = flt(grp.get("qty"))
		if total_qty <= 0:
			continue
		grp_lines = grp.get("lines") or []
		first_ln = grp_lines[0] if grp_lines else None
		so_for_line = _resolve_against_sales_order(first_ln, customer) if first_ln else ""
		row = {
			"item_code": item_code,
			"qty": total_qty,
			"uom": _line_get(first_ln, "uom")
			or frappe.db.get_value("Item", item_code, "stock_uom")
			or "Kg",
			"warehouse": wh,
			"against_sales_order": so_for_line,
		}
		so_detail = _match_so_detail(so_for_line, item_code, total_qty) if so_for_line else ""
		if so_detail:
			if frappe.db.has_column("Delivery Note Item", "so_detail"):
				row["so_detail"] = so_detail
			if frappe.db.has_column("Delivery Note Item", "against_sales_order_item"):
				row["against_sales_order_item"] = so_detail

		batch_entries = grp.get("batches") or []
		_apply_batch_fields_to_dn_row(row, item_code, wh, batch_entries, company=fc)

		order_code = _order_code_from_lines(grp_lines, pc_filter or "")
		if order_code:
			if _doc_has_field("Delivery Note Item", "custom_order_code"):
				row["custom_order_code"] = order_code
			elif _doc_has_field("Delivery Note Item", "order_code"):
				row["order_code"] = order_code
			elif _doc_has_field("Delivery Note Item", "party_code"):
				row["party_code"] = order_code

		rolls = _rolls_json_for_batches(batch_entries)
		if rolls and _doc_has_field("Delivery Note Item", "custom_despatch_rolls_json"):
			row["custom_despatch_rolls_json"] = json.dumps(rolls)

		dn.append("items", row)

	if not dn.items:
		frappe.throw(_("No delivery lines to create."))
	return dn


def create_draft_delivery_notes_by_order(despatch_approval):
	"""Insert one draft DN per (despatch_customer, party_code)."""
	da = despatch_approval
	if isinstance(da, str):
		da = frappe.get_doc("Despatch Approval", da)

	order_keys = []
	seen = set()
	for ln in da.lines or []:
		pc = _cstr(_line_get(ln, "party_code"))
		dc = _line_despatch_customer(ln)
		key = (dc, pc)
		if key in seen:
			continue
		seen.add(key)
		order_keys.append(key)

	names = []
	for dc, pc in order_keys:
		dn = build_delivery_note_from_despatch(da, party_code=pc, despatch_customer=dc)
		dn.insert(ignore_permissions=True)
		_link_dn_item_bundles(dn)
		names.append(dn.name)
	return names


def make_delivery_note_from_despatch(despatch_approval):
	"""Insert draft Delivery Note (legacy auto-create path)."""
	dn = build_delivery_note_from_despatch(despatch_approval)
	dn.insert(ignore_permissions=True)
	_link_dn_item_bundles(dn)
	return dn.name
