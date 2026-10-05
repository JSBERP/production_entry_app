# -*- coding: utf-8 -*-
"""Hidden loading-timer and removed-roll fields on Despatch Approval and Stock Entry."""

from __future__ import annotations

import frappe


def execute():
	from production_entry.production_planning.loading_session import ensure_loading_fields

	ensure_loading_fields()
	frappe.db.commit()
