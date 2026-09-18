# -*- coding: utf-8 -*-
"""Disable Clubbing Sheet Client Scripts that assign loading sequence per item row.

Those scripts produce Inside / Center 1 / Center 2 / Outside for 4 item lines
of only 2 order codes. Loading sequence is one slot per order code in the app JS.
"""

from __future__ import annotations

import frappe


_NEEDLES = (
	"center1Count",
	"middleCount = n - 2",
	"items[i].loading_sequence",
	"items[0].loading_sequence = 'Inside'",
)


def execute():
	if not frappe.db.exists("DocType", "Client Script"):
		return
	if not frappe.db.has_column("Client Script", "dt"):
		return
	rows = frappe.get_all(
		"Client Script",
		filters={"dt": "Clubbing Sheet", "enabled": 1},
		fields=["name", "script"],
		limit_page_length=0,
	) or []
	for r in rows:
		src = r.get("script") or ""
		if not any(n in src for n in _NEEDLES):
			continue
		frappe.db.set_value("Client Script", r.name, "enabled", 0)
		frappe.logger().info(
			"Disabled Clubbing Client Script %s (per-row loading sequence)", r.name
		)
