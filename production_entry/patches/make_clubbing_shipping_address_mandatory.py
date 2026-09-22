# -*- coding: utf-8 -*-
"""Make Clubbing Sheet Item.custom_shipping_address mandatory."""

from __future__ import annotations

import frappe


def execute():
	if not frappe.db.exists("DocType", "Clubbing Sheet Item"):
		return

	cf_name = frappe.db.get_value(
		"Custom Field",
		{"dt": "Clubbing Sheet Item", "fieldname": "custom_shipping_address"},
		"name",
	)
	if cf_name:
		frappe.db.set_value("Custom Field", cf_name, "reqd", 1, update_modified=False)
		frappe.clear_cache(doctype="Clubbing Sheet Item")
		frappe.db.commit()
		return

	# Native field (unlikely) — update DocField if present
	if frappe.db.exists(
		"DocField", {"parent": "Clubbing Sheet Item", "fieldname": "custom_shipping_address"}
	):
		frappe.db.set_value(
			"DocField",
			{"parent": "Clubbing Sheet Item", "fieldname": "custom_shipping_address"},
			"reqd",
			1,
			update_modified=False,
		)
		frappe.clear_cache(doctype="Clubbing Sheet Item")
		frappe.db.commit()
