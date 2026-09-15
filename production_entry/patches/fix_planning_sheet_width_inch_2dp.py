"""Recompute Planning Sheet fabric width_inch from item name (keep 2 decimals).

Previously widths like 42.32 / 45.47 were collapsed to 42.3 / 45.5 via round(..., 1).
"""

from __future__ import annotations

import frappe
from frappe.utils import flt


def execute():
	from production_entry.production_planning.scheduler_api import _parse_gsm_width_from_item_text

	for doctype in ("Planning sheet Item", "Planning Table"):
		if not frappe.db.exists("DocType", doctype):
			continue
		if not frappe.db.has_column(doctype, "width_inch"):
			continue
		rows = frappe.get_all(
			doctype,
			fields=["name", "item_code", "item_name", "width_inch"],
			filters={},
			limit_page_length=0,
		)
		for r in rows:
			ic = (r.get("item_code") or "").strip()
			inm = (r.get("item_name") or "").strip()
			if not ic and not inm:
				continue
			# Fabric / nonwoven lines (100*) — prefer exact name width
			if not (ic.startswith("100") or "NON WOVEN" in inm.upper() or " W -" in inm.upper() or "W -" in inm.upper()):
				continue
			_, parsed = _parse_gsm_width_from_item_text(f"{ic} {inm}".strip())
			parsed = round(flt(parsed), 2)
			if parsed <= 0:
				continue
			cur = round(flt(r.get("width_inch") or 0), 2)
			if abs(cur - parsed) < 0.001:
				continue
			frappe.db.set_value(doctype, r["name"], "width_inch", parsed, update_modified=False)

	frappe.db.commit()
