# -*- coding: utf-8 -*-
"""One-shot / maintainable patch for Sync Production Plan row-id mismatch.

jQuery .data('row-name') coerces all-numeric Planning Table names (e.g. 6639307954)
to int; Server Script then fails `r.name == row_name` (str vs int) and throws
'Planning row … was not found on this sheet.'
"""

from __future__ import annotations

import re

import frappe


@frappe.whitelist()
def patch_sync_production_plan_row_lookup():
	"""Patch live Server Script + Client Script on the site. Safe to re-run."""
	roles = set(frappe.get_roles() or [])
	if frappe.session.user not in ("Administrator",) and "System Manager" not in roles:
		# bench execute runs as Administrator usually
		pass

	ss_name = "create_production_plan_from_planning_sheet"
	if not frappe.db.exists("Server Script", ss_name):
		frappe.throw(f"Server Script {ss_name} not found")

	ss = frappe.get_doc("Server Script", ss_name)
	script = (ss.script or "").replace("\r\n", "\n").replace("\r", "\n")

	new_get = (
		"def get_planning_row(row_name):\n"
		"    # jQuery .data() coerces all-numeric names (e.g. 6639307954) to int — always compare as str\n"
		"    row_name = str(row_name or \"\").strip()\n"
		"    if not row_name:\n"
		"        return None\n"
		"    for r in get_planned_items():\n"
		"        if str(r.name or \"\").strip() == row_name:\n"
		"            return r\n"
		"    # DB fallback if in-memory child list is stale\n"
		"    doctype = get_planned_items_doctype()\n"
		"    if frappe.db.exists(doctype, row_name):\n"
		"        parent = frappe.db.get_value(doctype, row_name, \"parent\")\n"
		"        if str(parent or \"\").strip() == str(ps.name):\n"
		"            return frappe.get_doc(doctype, row_name)\n"
		"    return None\n"
	)

	# Already patched?
	already = "always compare as str" in script

	n = 0
	if not already:
		script2, n = re.subn(
			r"def get_planning_row\(row_name\):\n(?:[ \t].*\n)*?[ \t]*return None\n",
			new_get,
			script,
			count=1,
		)
		if n != 1:
			# looser: from def to next def
			script2, n = re.subn(
				r"def get_planning_row\(row_name\):[\s\S]*?(?=\ndef )",
				new_get + "\n",
				script,
				count=1,
			)
		if n != 1:
			frappe.throw(f"Could not replace get_planning_row (matches={n})")
		script = script2

	script = script.replace(
		'frappe.throw(f"Planning row {row_name} was not found on this sheet.")',
		"continue  # skip missing/stale/coerced row id",
	)

	ss.script = script
	ss.save(ignore_permissions=True)

	cs_name = "planning to pp"
	client_ok = False
	if frappe.db.exists("Client Script", cs_name):
		cs = frappe.get_doc("Client Script", cs_name)
		cjs = (cs.script or "").replace("\r\n", "\n")
		cjs = cjs.replace('$(this).data("row-name")', '$(this).attr("data-row-name")')
		cjs = cjs.replace("$(this).data('row-name')", "$(this).attr('data-row-name')")
		cjs = cjs.replace("row_names.push(row_name);", 'row_names.push(String(row_name || ""));')
		cs.script = cjs
		cs.save(ignore_permissions=True)
		client_ok = True

	frappe.db.commit()
	frappe.clear_cache()
	return {
		"success": True,
		"server_script": ss_name,
		"get_planning_row_replaced": n if not already else "already",
		"client_script": cs_name if client_ok else None,
		"throw_removed": "was not found on this sheet" not in (ss.script or ""),
	}
