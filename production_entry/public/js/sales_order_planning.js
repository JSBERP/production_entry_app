/**
 * Sales Order — unified Update (reopen as Draft) + Update Planning Sheet (upsert).
 *
 * Replaces separate "Update Items" / "Update Meter / Roll" site buttons when this app JS loads.
 * Workflow: Update → edit Draft → Submit → Update Planning Sheet (overrides existing PS rows).
 */
frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		_jsb_so_bind_planning_buttons(frm);
	},
});

function _jsb_so_bind_planning_buttons(frm) {
	if (!frm || frm.is_new()) {
		return;
	}

	frm.add_custom_button(__("Update"), function () {
		_jsb_so_prepare_update(frm);
	});

	frm.add_custom_button(__("Update Planning Sheet"), function () {
		_jsb_so_update_planning_sheet(frm);
	});
}

function _jsb_so_prepare_update(frm) {
	if (frm.doc.docstatus === 0) {
		frappe.show_alert({
			message: __("Sales Order is already Draft — edit fields, Submit, then Update Planning Sheet."),
			indicator: "blue",
		});
		frm.set_read_only(false);
		frm.refresh();
		return;
	}

	if (frm.doc.docstatus !== 1) {
		frappe.msgprint(__("Only Draft or Submitted Sales Orders can be updated."));
		return;
	}

	frappe.confirm(
		__(
			"This will reopen the Sales Order as Draft so you can edit the full form (items, meter/roll, and header). After you Submit, use Update Planning Sheet to override existing planning rows. Continue?"
		),
		function () {
			frappe.call({
				method: "production_entry.production_planning.scheduler_api.prepare_sales_order_for_update",
				args: { sales_order: frm.doc.name },
				freeze: true,
				freeze_message: __("Preparing Sales Order for update…"),
				callback(r) {
					if (r.exc) {
						return;
					}
					const msg = (r.message || {}) || {};
					if (!msg.ok) {
						frappe.msgprint(msg.message || __("Could not prepare update."));
						return;
					}
					frappe.show_alert({
						message: msg.message || __("Opened as Draft."),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		}
	);
}

function _jsb_so_update_planning_sheet(frm) {
	if (frm.is_dirty()) {
		frappe.msgprint(__("Please save the Sales Order before updating the Planning Sheet."));
		return;
	}
	frappe.call({
		method: "production_entry.production_planning.scheduler_api.update_planning_sheet_from_sales_order",
		args: { sales_order: frm.doc.name },
		freeze: true,
		freeze_message: __("Updating Planning Sheet…"),
		callback(r) {
			if (r.exc) {
				return;
			}
			const msg = r.message || {};
			frappe.show_alert({
				message: msg.message || __("Planning Sheet updated."),
				indicator: msg.ok === false ? "orange" : "green",
			});
			if (msg.planning_sheet) {
				frappe.set_route("Form", "Planning sheet", msg.planning_sheet);
			}
		},
	});
}
