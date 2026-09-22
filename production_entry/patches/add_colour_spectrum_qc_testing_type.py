# -*- coding: utf-8 -*-
"""Add Colour Spectrum to Quality Checking.testing_type Select options."""

from __future__ import annotations

import frappe

TESTING_TYPE_OPTIONS = (
	"Round Cutting GSM Test\nPatty Cutting GSM Test\nTensile Testing\nColour Spectrum"
)


def execute():
	if not frappe.db.exists("DocType", "Quality Checking"):
		return

	dt = frappe.get_doc("DocType", "Quality Checking")
	changed = False
	for field in dt.fields:
		if field.fieldname != "testing_type":
			continue
		opts = (field.options or "").replace("\r\n", "\n").strip()
		if "Colour Spectrum" not in opts.split("\n"):
			# Keep existing lines and append Colour Spectrum
			lines = [ln for ln in opts.split("\n") if ln.strip()]
			if "Colour Spectrum" not in lines:
				lines.append("Colour Spectrum")
			# Prefer canonical order when the three known types are present
			wanted = [
				"Round Cutting GSM Test",
				"Patty Cutting GSM Test",
				"Tensile Testing",
				"Colour Spectrum",
			]
			if all(w in lines for w in wanted[:3]):
				field.options = TESTING_TYPE_OPTIONS
			else:
				field.options = "\n".join(lines)
			changed = True
		break

	if changed:
		dt.save(ignore_permissions=True)
		frappe.clear_cache(doctype="Quality Checking")
		frappe.db.commit()
