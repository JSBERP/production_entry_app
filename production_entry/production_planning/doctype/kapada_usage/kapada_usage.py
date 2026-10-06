# Copyright (c) 2026, Production Entry and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class KapadaUsage(Document):
	def validate(self):
		shift = (self.shift or "").strip()
		if shift and "night" in shift.lower():
			self.shift = "Night Shift"
		elif shift and "day" in shift.lower():
			self.shift = "Day Shift"
