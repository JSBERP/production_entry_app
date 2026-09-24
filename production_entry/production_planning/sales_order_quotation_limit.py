# -*- coding: utf-8 -*-
"""Never block Sales Order submit with ERPNext Quotation Item 'Limit Crossed'.

ERPNext StatusUpdater compares sum(SO Item.stock_qty) linked via quotation_item
against Quotation Item.stock_qty. Meter / UOM edits often push SO stock_qty above
the quotation cap and throw OverAllowanceError ("Limit Crossed").

We:
1. Override limits_crossed_error for Quotation Item targets only (SO can still
   exceed; DN/SI vs SO limits are unchanged).
2. On validate / before_submit, raise Quotation Item stock_qty (and qty) so the
   cap covers all open/submitted SO stock against that row.
"""

from __future__ import annotations

from collections import defaultdict

import frappe
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder
from frappe.utils import flt


def _cstr(val) -> str:
	return (val or "").strip() if val is not None else ""


def _expand_quotation_item_caps(doc) -> None:
	"""Raise Quotation Item.stock_qty so StatusUpdater will not trip Limit Crossed."""
	needed_by_qi: dict[str, float] = defaultdict(float)
	for item in doc.get("items") or []:
		qi = _cstr(getattr(item, "quotation_item", None))
		if not qi:
			continue
		needed_by_qi[qi] += flt(getattr(item, "stock_qty", None) or 0)

	if not needed_by_qi:
		return

	so_name = _cstr(getattr(doc, "name", None))
	for qi_name, this_stock in needed_by_qi.items():
		if not frappe.db.exists("Quotation Item", qi_name):
			continue
		qi = frappe.db.get_value(
			"Quotation Item",
			qi_name,
			["stock_qty", "qty", "ordered_qty", "conversion_factor", "item_code"],
			as_dict=True,
		)
		if not qi:
			continue

		other_stock = flt(
			frappe.db.sql(
				"""
				SELECT COALESCE(SUM(soi.stock_qty), 0)
				FROM `tabSales Order Item` soi
				INNER JOIN `tabSales Order` so ON so.name = soi.parent
				WHERE soi.quotation_item = %s
				  AND so.docstatus < 2
				  AND so.name != %s
				""",
				(qi_name, so_name or ""),
			)[0][0]
		)
		total_needed = flt(other_stock) + flt(this_stock)
		current_cap = flt(qi.stock_qty)
		# Small float slack — only bump when truly over
		if total_needed <= current_cap + 0.0001:
			continue

		cf = flt(qi.conversion_factor) or 1.0
		new_stock = total_needed
		new_qty = new_stock / cf if cf else new_stock
		updates = {"stock_qty": new_stock}
		if new_qty > flt(qi.qty):
			updates["qty"] = new_qty
		frappe.db.set_value("Quotation Item", qi_name, updates, update_modified=False)


def sales_order_validate(doc, method=None):
	_expand_quotation_item_caps(doc)


def sales_order_before_submit(doc, method=None):
	_expand_quotation_item_caps(doc)


class JSBSalesOrder(SalesOrder):
	"""Sales Order that never throws Limit Crossed against Quotation Item."""

	def limits_crossed_error(self, args, item, qty_or_amount):
		# Only suppress Quotation Item ceiling for Sales Orders.
		# Purchase / DN / SI against SO still use the normal check.
		target_dt = ""
		if isinstance(args, dict):
			target_dt = _cstr(args.get("target_dt"))
		if self.doctype == "Sales Order" and target_dt == "Quotation Item":
			return
		return super().limits_crossed_error(args, item, qty_or_amount)
