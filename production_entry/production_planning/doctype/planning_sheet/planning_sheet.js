// Planning sheet grid columns: child doctype JSON field_order + planning_sheet_custom.js grid_columns.apply.

frappe.ui.form.on('Planning sheet', {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__('Generate Order Code'), function () {
				if ((frm.doc.party_code || '').trim()) {
					frappe.show_alert({
						message: __('Party Code already set to {0}. Left unchanged.', [frm.doc.party_code]),
						indicator: 'blue',
					});
					return;
				}
				frappe.call({
					method: 'production_entry.production_planning.scheduler_api.generate_planning_sheet_order_code',
					args: { planning_sheet_name: frm.doc.name },
					freeze: true,
					freeze_message: __('Generating order code…'),
					callback(r) {
						if (r.exc) {
							return;
						}
						const msg = r.message || {};
						frappe.show_alert({
							message: msg.message || __('Done'),
							indicator: msg.ok ? 'green' : 'blue',
						});
						if (typeof ps_reload_planning_sheet_doc === 'function') {
							ps_reload_planning_sheet_doc(frm);
						} else {
							frm.reload_doc();
						}
					},
				});
			}, __('Actions'));

			frm.add_custom_button(__('Meter to Kgs (All Bag BOM)'), function () {
				frappe.call({
					method: 'production_entry.production_planning.scheduler_api.convert_meter_to_kgs_for_box_bag_bom',
					args: { planning_sheet_name: frm.doc.name },
					freeze: true,
					callback(r) {
						if (!r.exc) {
							const msg = r.message || {};
							frappe.show_alert({
								message: __('Converted {0} line(s).', [msg.updated || 0]),
								indicator: 'green',
							});
							if (msg.warning) {
								frappe.msgprint({
									title: __('Meter to Kg'),
									message: msg.warning,
									indicator: 'orange',
								});
							}
							if (typeof ps_reload_planning_sheet_doc === 'function') {
								ps_reload_planning_sheet_doc(frm);
							} else {
								frm.reload_doc();
							}
						}
					},
				});
			}, __('Actions'));
		}
	},

	/** Legacy hook — planning_sheet_custom.js; column visibility no longer applied here. */
	toggle_221_fields() {},
});

function ps_fill_design_from_item_code(frm, cdt, cdn) {
	if (!cdt || !cdn) {
		return;
	}
	const row = frappe.get_doc(cdt, cdn);
	if (!row || !row.item_code) {
		return;
	}
	const ic = row.item_code;
	if (
		ic.includes('-221') || ic.startsWith('221')
		|| ic.includes('-224') || ic.startsWith('224')
		|| ic.includes('-222') || ic.startsWith('222')
		|| ic.includes('-223') || ic.startsWith('223')
		|| ic.includes('-211') || ic.startsWith('211')
		|| ic.includes('-212') || ic.startsWith('212')
		|| ic.includes('-213') || ic.startsWith('213')
		|| ic.includes('-231') || ic.startsWith('231')
		|| ic.includes('-232') || ic.startsWith('232')
		|| ic.includes('-233') || ic.startsWith('233')
		|| ic.includes('-241') || ic.startsWith('241')
		|| ic.includes('-242') || ic.startsWith('242')
	) {
		const parts = ic.split('-');
		if (parts.length > 1) {
			const dc = parts[0];
			if (row.custom_design_code !== dc) {
				frappe.model.set_value(cdt, cdn, 'custom_design_code', dc);
			} else {
				ps_fetch_design_name(cdt, cdn);
			}
		}
	}
}

function ps_fetch_design_name(cdt, cdn) {
	const row = frappe.get_doc(cdt, cdn);
	if (!row || !row.custom_design_code) {
		return;
	}
	frappe.call({
		method: 'production_entry.production_planning.scheduler_api._design_master_extra_fields',
		args: { design_code: row.custom_design_code },
		callback(r) {
			if (r.message && r.message.custom_design_name) {
				frappe.model.set_value(cdt, cdn, 'custom_design_name', r.message.custom_design_name);
			}
		},
	});
}

function ps_schedule_process_grid_columns(frm, delay) {
	const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
	if (pg && typeof pg.schedule === 'function') {
		pg.schedule(frm, delay != null ? delay : 80);
	}
}

frappe.ui.form.on('Planning sheet Item', {
	item_code(frm, cdt, cdn) {
		ps_fill_design_from_item_code(frm, cdt, cdn);
		ps_schedule_process_grid_columns(frm, 80);
	},
	custom_design_code(frm, cdt, cdn) {
		ps_fetch_design_name(cdt, cdn);
	},
});

frappe.ui.form.on('Planning Table', {
	item_code(frm, cdt, cdn) {
		ps_fill_design_from_item_code(frm, cdt, cdn);
		ps_schedule_process_grid_columns(frm, 80);
	},
	custom_design_code(frm, cdt, cdn) {
		ps_fetch_design_name(cdt, cdn);
	},
});
