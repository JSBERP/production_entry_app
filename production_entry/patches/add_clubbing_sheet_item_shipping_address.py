# -*- coding: utf-8 -*-
"""Add Shipping Address (Link → Address) on Clubbing Sheet Item."""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "Clubbing Sheet Item"):
		return

	fields = []
	meta = frappe.get_meta("Clubbing Sheet Item")
	for f in (
		{
			"fieldname": "custom_shipping_address",
			"label": "Shipping Address",
			"fieldtype": "Link",
			"options": "Address",
			"reqd": 1,
			"in_list_view": 1,
			"insert_after": "customer",
		},
	):
		fn = f["fieldname"]
		if frappe.db.exists("Custom Field", {"dt": "Clubbing Sheet Item", "fieldname": fn}):
			continue
		if meta.has_field(fn):
			continue
		fields.append(f)

	if fields:
		create_custom_fields({"Clubbing Sheet Item": fields}, ignore_validate=True, update=False)
		frappe.clear_cache(doctype="Clubbing Sheet Item")

	frappe.db.commit()
