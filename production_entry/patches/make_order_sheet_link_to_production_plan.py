# -*- coding: utf-8 -*-
"""Change Order Sheet fields from Data → Link (Production Plan) for arrow navigation."""

from __future__ import annotations

import frappe


TARGETS = (
	("Planning Table", "order_sheet"),
	("Planning sheet Item", "order_sheet"),
	("Planning sheet", "order_sheet"),
)


def execute():
	for dt, fieldname in TARGETS:
		if not frappe.db.exists("DocType", dt):
			continue
		# Native DocField
		if frappe.db.exists("DocField", {"parent": dt, "fieldname": fieldname}):
			frappe.db.sql(
				"""
				update `tabDocField`
				set fieldtype = 'Link', options = 'Production Plan'
				where parent = %s and fieldname = %s
				""",
				(dt, fieldname),
			)
		# Custom Field fallback
		cf = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname}, "name")
		if cf:
			frappe.db.set_value(
				"Custom Field",
				cf,
				{"fieldtype": "Link", "options": "Production Plan"},
				update_modified=False,
			)
		frappe.clear_cache(doctype=dt)
	frappe.db.commit()
