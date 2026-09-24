// Planning Sheet Custom Script - Display customer name instead of ID
// Grid column logic lives in planning_sheet_process_grid.js (loaded before this file).

/** Process-wise grid columns — auto-detected from item_code (see planning_sheet_process_grid.js). */
function ps_schedule_planning_grid_columns(frm, delay) {
	const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
	if (pg && typeof pg.schedule === 'function') {
		pg.schedule(frm, delay != null ? delay : 80);
		return;
	}
	if (typeof schedule_apply_process_code_visibility === 'function') {
		schedule_apply_process_code_visibility(frm, delay != null ? delay : 80);
	}
}

/** reload_doc then rebuild child grids (create / regenerate / sync). */
function ps_reload_planning_sheet_doc(frm) {
	if (!frm || typeof frm.reload_doc !== 'function') {
		return;
	}
	const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
	const after = function () {
		if (pg && typeof pg.after_reload === 'function') {
			pg.after_reload(frm);
		} else {
			ps_schedule_planning_grid_columns(frm, 80);
		}
	};
	const p = frm.reload_doc();
	if (p && typeof p.then === 'function') {
		p.then(after);
	} else {
		setTimeout(after, 300);
	}
}

// Keep legacy `items` and board `planned_items` unit fields in sync when `source_item` links rows
// (avoids stale board grid until full reload).
function _norm(v) {
    return String(v || "").trim();
}

/** Administrator / System Manager: edit board Unit on submitted Planning Sheet (POC testing). */
function ps_enable_admin_submitted_board_unit_edit(frm) {
    if (!frm || !frm.doc || Number(frm.doc.docstatus) !== 1) {
        return;
    }
    const roles = frappe.boot && frappe.boot.user && frappe.boot.user.roles;
    const privileged =
        frappe.session.user === 'Administrator' ||
        (roles && (roles.includes('System Manager') || roles.includes('Administrator')));
    if (!privileged) {
        return;
    }
    const grid = frm.fields_dict.planned_items && frm.fields_dict.planned_items.grid;
    if (grid && typeof grid.update_docfield_property === 'function') {
        grid.update_docfield_property('unit', 'read_only', 0);
    }
    if (!frm._ps_admin_board_unit_hint) {
        frm._ps_admin_board_unit_hint = true;
        frappe.show_alert({
            message: __('Testing: you can edit board Unit on this submitted sheet (Save to apply).'),
            indicator: 'blue',
        }, 8);
    }
}

function _rowSoKey(row) {
    // Planning sheet Item uses `so_item`; board rows can be `so_item` or `sales_order_item`
    return _norm(row?.so_item || row?.sales_order_item || row?.salesOrderItem);
}

function _rowItemCode(row) {
    return _norm(row?.item_code || row?.itemCode);
}

function _findPlannedRowForLegacy(frm, legacyRow) {
    const psi = _norm(legacyRow?.name);
    const soKey = _rowSoKey(legacyRow);
    const ic = _rowItemCode(legacyRow);
    const planned = frm?.doc?.planned_items || [];
    if (!planned.length) return null;

    // 1) strict link: board.source_item == legacy.name
    if (psi) {
        const hit = planned.find((pr) => _norm(pr.source_item) === psi);
        if (hit) return hit;
    }
    // 2) fallback: match by SO line + item_code
    if (soKey && ic) {
        const hit2 = planned.find((pr) => _rowSoKey(pr) === soKey && _rowItemCode(pr) === ic);
        if (hit2) return hit2;
    }
    return null;
}

function _findLegacyRowForPlanned(frm, plannedRow) {
    const si = _norm(plannedRow?.source_item);
    const soKey = _rowSoKey(plannedRow);
    const ic = _rowItemCode(plannedRow);
    const legacy = frm?.doc?.items || [];
    if (!legacy.length) return null;

    // 1) strict link: legacy.name == board.source_item
    if (si) {
        const hit = legacy.find((it) => _norm(it.name) === si);
        if (hit) return hit;
    }
    // 2) fallback: match by SO line + item_code
    if (soKey && ic) {
        const hit2 = legacy.find((it) => _rowSoKey(it) === soKey && _rowItemCode(it) === ic);
        if (hit2) return hit2;
    }
    return null;
}

frappe.ui.form.on('Planning sheet Item', {
    unit: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const pr = _findPlannedRowForLegacy(frm, row);
        if (!pr) return;
        frappe.model.set_value(pr.doctype, pr.name, 'unit', row.unit);
        if (row.custom_plan_code) {
            frappe.model.set_value(pr.doctype, pr.name, 'custom_plan_code', row.custom_plan_code);
        }
        if (row.plan_name) {
            frappe.model.set_value(pr.doctype, pr.name, 'plan_name', row.plan_name);
        }
        frm.refresh_field('planned_items');
        ps_schedule_planning_grid_columns(frm);
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 100);
    },
    custom_plan_code: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const pr = _findPlannedRowForLegacy(frm, row);
        if (!pr) return;
        if (row.custom_plan_code) {
            frappe.model.set_value(pr.doctype, pr.name, 'custom_plan_code', row.custom_plan_code);
        }
        if (row.plan_name) {
            frappe.model.set_value(pr.doctype, pr.name, 'plan_name', row.plan_name);
        }
        frm.refresh_field('planned_items');
        ps_schedule_planning_grid_columns(frm);
    },
    custom_item_planned_date: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const pr = _findPlannedRowForLegacy(frm, row);
        if (!pr) return;
        frappe.model.set_value(pr.doctype, pr.name, 'planned_date', row.custom_item_planned_date || null);
        frm.refresh_field('planned_items');
    },
});

frappe.ui.form.on('Planning Table', {
    unit: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const leg = _findLegacyRowForPlanned(frm, row);
        if (!leg) return;
        frappe.model.set_value(leg.doctype, leg.name, 'unit', row.unit);
        if (row.custom_plan_code) {
            frappe.model.set_value(leg.doctype, leg.name, 'custom_plan_code', row.custom_plan_code);
        }
        if (row.plan_name) {
            frappe.model.set_value(leg.doctype, leg.name, 'plan_name', row.plan_name);
        }
        frm.refresh_field('items');
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 100);
    },
    custom_plan_code: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const leg = _findLegacyRowForPlanned(frm, row);
        if (!leg) return;
        if (row.custom_plan_code) {
            frappe.model.set_value(leg.doctype, leg.name, 'custom_plan_code', row.custom_plan_code);
        }
        if (row.plan_name) {
            frappe.model.set_value(leg.doctype, leg.name, 'plan_name', row.plan_name);
        }
        frm.refresh_field('items');
    },
    planned_date: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];
        const leg = _findLegacyRowForPlanned(frm, row);
        if (!leg) return;
        frappe.model.set_value(leg.doctype, leg.name, 'custom_item_planned_date', row.planned_date || null);
        frm.refresh_field('items');
    },
});

function planningSheetBomLineLabel(row, idx) {
    const code = _norm(row?.item_code);
    const name = _norm(row?.item_name);
    const qty = row?.qty ? ` - ${row.qty} ${row.uom || ''}` : '';
    const serial = Number.isFinite(idx) ? `${idx + 1}. ` : '';
    return `${serial}${code}${name ? ` - ${name}` : ''}${qty}`;
}

function planningSheetBomOptionLabel(bom) {
    const children = (bom?.children || [])
        .map((it) => `${it.item_code}${it.qty ? ` (${it.qty})` : ''}`)
        .join(', ');
    return `${bom.label || bom.name}${children ? ` -> ${children}` : ''}`;
}

function openSheetCuttingChangeBomDialog(frm) {
    frappe.call({
        method: 'production_entry.production_planning.scheduler_api.get_sheet_cutting_bom_change_options',
        args: { planning_sheet_name: frm.doc.name },
        freeze: true,
        freeze_message: __('Loading Sheet Cutting BOMs...'),
        callback: function (r) {
            const rows = (r.message && r.message.rows) || [];
            if (!rows.length) {
                frappe.msgprint({
                    title: __('No Sheet Cutting BOMs'),
                    message: __('No 251 Sheet Cutting Sales Order rows with submitted active BOMs were found.'),
                    indicator: 'orange',
                });
                return;
            }
            const byKey = {};
            rows.forEach((row) => {
                byKey[row.sales_order_item] = row;
            });
            const firstKey = rows[0].sales_order_item;
            const lineOptions = rows.map((row, idx) => planningSheetBomLineLabel(row, idx)).join('\n');
            const d = new frappe.ui.Dialog({
                title: __('Change Sheet Cutting BOM'),
                fields: [
                    {
                        fieldname: 'sales_order_item',
                        fieldtype: 'Select',
                        label: __('Finished Goods Row'),
                        reqd: 1,
                        options: lineOptions,
                        default: planningSheetBomLineLabel(rows[0], 0),
                        onchange: function () {
                            const selectedLabel = d.get_value('sales_order_item') || '';
                            const selected =
                                rows.find((row, idx) => planningSheetBomLineLabel(row, idx) === selectedLabel) || rows[0];
                            const bomOptions = (selected.boms || []).map(planningSheetBomOptionLabel).join('\n');
                            d.fields_dict.bom_no.df.options = bomOptions;
                            d.fields_dict.bom_no.refresh();
                            d.set_value('bom_no', planningSheetBomOptionLabel((selected.boms || [])[0] || {}));
                        },
                    },
                    {
                        fieldname: 'bom_no',
                        fieldtype: 'Select',
                        label: __('BOM'),
                        reqd: 1,
                        options: ((rows[0].boms || []).map(planningSheetBomOptionLabel)).join('\n'),
                        default: planningSheetBomOptionLabel((rows[0].boms || [])[0] || {}),
                    },
                    {
                        fieldname: 'help',
                        fieldtype: 'HTML',
                        options:
                            '<div class="text-muted small">' +
                            __('Only the BOM child rows for the selected 251 line will be replaced. The finished goods row stays unchanged.') +
                            '</div>',
                    },
                ],
                primary_action_label: __('Confirm'),
                primary_action: function () {
                    const selectedLabel = d.get_value('sales_order_item') || '';
                    const selected =
                        rows.find((row, idx) => planningSheetBomLineLabel(row, idx) === selectedLabel) || byKey[firstKey];
                    const selectedBomLabel = d.get_value('bom_no') || '';
                    const bom = (selected.boms || []).find((b) => planningSheetBomOptionLabel(b) === selectedBomLabel);
                    if (!selected || !bom) {
                        frappe.msgprint(__('Please select both Finished Goods row and BOM.'));
                        return;
                    }
                    frappe.call({
                        method: 'production_entry.production_planning.scheduler_api.apply_sheet_cutting_bom_to_planning_sheet',
                        args: {
                            planning_sheet_name: frm.doc.name,
                            sales_order_item: selected.sales_order_item,
                            bom_no: bom.name,
                        },
                        freeze: true,
                        freeze_message: __('Replacing BOM child items...'),
                        callback: function (res) {
                            const m = res.message || {};
                            d.hide();
                            frappe.show_alert({
                                message: __(m.message || 'BOM child rows updated.'),
                                indicator: 'green',
                            });
                            ps_reload_planning_sheet_doc(frm);
                        },
                    });
                },
            });
            d.show();
        },
    });
}

function openWorkingBomPickerForFgRow(frm, fgRow) {
    const itemCode = _norm(fgRow?.item_code);
    if (!itemCode) return;
    frappe.call({
        method: 'production_entry.production_planning.scheduler_api.get_sheet_cutting_bom_change_options',
        args: { planning_sheet_name: frm.doc.name },
        freeze: true,
        freeze_message: __('Loading Sheet Cutting BOMs...'),
        callback: function (r) {
            const rows = (r.message && r.message.rows) || [];
            const selected = rows.find((row) => _norm(row.item_code) === itemCode);
            if (!selected) {
                frappe.msgprint(__('No 251 Sheet Cutting row found for {0}', [itemCode]));
                return;
            }
            const boms = selected.boms || [];
            if (!boms.length) {
                frappe.msgprint(__('No active BOM for {0}', [itemCode]));
                return;
            }
            const defaultBom = (boms.find((b) => b.is_default) || boms[0]).name;
            const d = new frappe.ui.Dialog({
                title: __('Select BOM'),
                fields: [
                    {
                        fieldtype: 'HTML',
                        fieldname: 'info',
                        options:
                            '<p><b>' +
                            frappe.utils.escape_html(selected.item_code || '') +
                            '</b><br>' +
                            frappe.utils.escape_html(selected.item_name || '') +
                            '</p>',
                    },
                    {
                        fieldtype: 'Select',
                        fieldname: 'bom_no',
                        label: __('BOM'),
                        options: boms.map((b) => b.name).join('\n'),
                        default: defaultBom,
                        reqd: 1,
                    },
                ],
                primary_action_label: __('Confirm'),
                primary_action: function (values) {
                    frappe.call({
                        method: 'production_entry.production_planning.scheduler_api.apply_sheet_cutting_bom_to_planning_sheet',
                        args: {
                            planning_sheet_name: frm.doc.name,
                            sales_order_item: selected.sales_order_item,
                            bom_no: values.bom_no,
                        },
                        freeze: true,
                        freeze_message: __('Replacing BOM child items...'),
                        callback: function (res) {
                            const m = res.message || {};
                            d.hide();
                            frappe.show_alert({
                                message: __(m.message || 'BOM child rows updated.'),
                                indicator: 'green',
                            });
                            ps_reload_planning_sheet_doc(frm);
                        },
                    });
                },
            });
            d.show();
        },
    });
}

function installWorkingSheetCuttingBomPicker(frm) {
    if (!frm || !frm.doc || !frm.doc.name) return;
    window.open_bom_picker = function (fgRow) {
        openWorkingBomPickerForFgRow(frm, fgRow);
    };
}

function registerWorkingSheetCuttingChangeBomButton(frm) {
    if (!frm || !frm.doc || !frm.doc.name) return;
    installWorkingSheetCuttingBomPicker(frm);
    try {
        frm.remove_custom_button(__('Change BOM'), __('Actions'));
    } catch (e) {}
    frm.add_custom_button(__('Change BOM'), function () {
        openSheetCuttingChangeBomDialog(frm);
    }, __('Actions'));
}

frappe.ui.form.on('Planning sheet', {
    onload: function (frm) {
        if (frm._cg_repopulating_grid) {
            return;
        }
        const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
        if (pg && typeof pg.install_guards === 'function') {
            pg.install_guards(frm);
        }
        ps_schedule_planning_grid_columns(frm, 80);
    },

    refresh: function(frm) {
        if (!frm.doc || !frm.doc.name) return;
        if (frm._cg_repopulating_grid) {
            return;
        }
        const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
        if (pg && typeof pg.install_guards === 'function') {
            pg.install_guards(frm);
        }
        ps_schedule_planning_grid_columns(frm, 80);
        ps_schedule_planning_grid_columns(frm, 400);
        registerWorkingSheetCuttingChangeBomButton(frm);
        // Site Client Scripts may re-add their own non-saving Change BOM button after app scripts.
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 300);
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 1000);
        if (typeof register_planning_sheet_stock_check_button === 'function') {
            setTimeout(function () { register_planning_sheet_stock_check_button(frm); }, 500);
            setTimeout(function () { register_planning_sheet_stock_check_button(frm); }, 1200);
        }
        ps_enable_admin_submitted_board_unit_edit(frm);
        frm.add_custom_button(__('Update Colors'), function() {
            frappe.call({
                method: 'production_entry.production_planning.scheduler_api.refresh_planning_sheet_colors',
                args: { planning_sheet: frm.doc.name },
                freeze: true,
                freeze_message: __('Updating colors from Sales Order...'),
                callback: function(r) {
                    const m = r.message || {};
                    frappe.show_alert({
                        message: __(m.message || 'Color update completed.'),
                        indicator: 'green'
                    });
                    ps_reload_planning_sheet_doc(frm);
                }
            });
        }, __('Actions'));
        frm.add_custom_button(__('Update SPR + Order Sheet'), function() {
            frappe.call({
                method: 'production_entry.production_planning.scheduler_api.refresh_planning_sheet_spr_and_order_sheet',
                args: { planning_sheet: frm.doc.name },
                freeze: true,
                freeze_message: __('Updating SPR and Order Sheet links...'),
                callback: function(r) {
                    const m = r.message || {};
                    const changed = cint(m.updated_order_sheet) + cint(m.updated_spr);
                    frappe.show_alert({
                        message: __(m.message || 'SPR/Order Sheet update completed.'),
                        indicator: changed > 0 ? 'green' : 'orange'
                    });
                    ps_reload_planning_sheet_doc(frm);
                }
            });
        }, __('Actions'));
        frm.add_custom_button(__('Manual Update SPR/Order Sheet'), function() {
            const d = new frappe.ui.Dialog({
                title: __('Manual Update SPR / Order Sheet'),
                fields: [
                    {
                        fieldname: 'help',
                        fieldtype: 'HTML',
                        options:
                            '<p class="text-muted small">' +
                            __('Paste one line: row_name OR item_code,order_sheet,spr_name') +
                            '<br>' +
                            __('Example: 1001001...,MFG-PP-2026-00354,SPR-2026-00180') +
                            '</p>',
                    },
                    { fieldname: 'lines', fieldtype: 'Long Text', reqd: 1, label: __('Mappings') },
                ],
                primary_action_label: __('Apply'),
                primary_action: function(vals) {
                    const text = (vals.lines || '').trim();
                    if (!text) return;
                    const mappings = text
                        .split(/\r?\n/)
                        .map((ln) => ln.trim())
                        .filter(Boolean)
                        .map((ln) => {
                            const p = ln.split(',').map((x) => (x || '').trim());
                            const first = p[0] || '';
                            // Planning Table names are often hash ids (e.g. qc2rnv2pht), not PT-…
                            const looksLikeItemCode = /^[0-9]{6,}/.test(first) || first.includes('-');
                            const looksLikeRow =
                                !looksLikeItemCode &&
                                (/^PT-|^new-|^ROW-|^PTROW/i.test(first) || /^[a-z0-9]{8,14}$/i.test(first));
                            return {
                                row_name: looksLikeRow ? first : '',
                                item_code: looksLikeRow ? '' : first,
                                order_sheet: p[1] || '',
                                spr_name: p[2] || '',
                            };
                        });
                    frappe.call({
                        method: 'production_entry.production_planning.scheduler_api.manual_update_planning_sheet_links',
                        args: { planning_sheet: frm.doc.name, mappings: JSON.stringify(mappings) },
                        freeze: true,
                        freeze_message: __('Applying manual mapping...'),
                        callback: function(r) {
                            const m = r.message || {};
                            d.hide();
                            if (m.errors && m.errors.length) {
                                frappe.msgprint({
                                    title: __('Manual Update Completed with Errors'),
                                    message: (m.message || '') + '<br><br>' + m.errors.map((e) => frappe.utils.escape_html(e)).join('<br>'),
                                });
                            } else {
                                frappe.show_alert({ message: __(m.message || 'Updated successfully'), indicator: 'green' });
                            }
                            ps_reload_planning_sheet_doc(frm);
                        },
                    });
                },
            });
            d.show();
        }, __('Actions'));
        frm.add_custom_button(__('Fill Parent Child Trace IDs'), function () {
            frappe.call({
                method: 'production_entry.production_planning.scheduler_api.ensure_planning_sheet_trace_ids',
                args: { planning_sheet_name: frm.doc.name },
                freeze: true,
                freeze_message: __('Stamping trace IDs on all rows…'),
                callback: function (r) {
                    const m = r.message || {};
                    frappe.show_alert({
                        message: __('Updated {0} row(s)', [m.updated || 0]),
                        indicator: (m.updated || 0) > 0 ? 'green' : 'orange',
                    });
                    ps_reload_planning_sheet_doc(frm);
                },
            });
        }, __('Actions'));
    },

    onload_post_render: function (frm) {
        if (frm._cg_repopulating_grid) {
            return;
        }
        ps_schedule_planning_grid_columns(frm, 50);
    },

    after_save: function (frm) {
        ps_schedule_planning_grid_columns(frm, 0);
        ps_schedule_planning_grid_columns(frm, 250);
    },

    on_submit: function (frm) {
        ps_schedule_planning_grid_columns(frm, 0);
        ps_schedule_planning_grid_columns(frm, 250);
    },

    validate: function (frm) {
        if (frm._cg_repopulating_grid) {
            return;
        }
        ps_schedule_planning_grid_columns(frm, 0);
    },

    items_add: function (frm) {
        ps_schedule_planning_grid_columns(frm);
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 100);
    },

    items_remove: function (frm) {
        ps_schedule_planning_grid_columns(frm);
        setTimeout(function () {
            registerWorkingSheetCuttingChangeBomButton(frm);
        }, 100);
    },

    planned_items_add: function (frm) {
        ps_schedule_planning_grid_columns(frm);
    },

    planned_items_remove: function (frm) {
        ps_schedule_planning_grid_columns(frm);
    },

    after_load: function(frm) {
        // Align unit Select metadata; then stabilize grids (no refresh_field — avoids column collapse).
        frappe.call({
            method: 'production_entry.production_planning.scheduler_api.sync_planning_line_unit_options_meta',
            callback: function () {
                try {
                    ['Planning sheet Item', 'Planning Table'].forEach(function (dt) {
                        if (locals.DocType && locals.DocType[dt]) delete locals.DocType[dt];
                    });
                } catch (e) {}
                frappe.model.with_doctype('Planning sheet Item', function () {
                    frappe.model.with_doctype('Planning Table', function () {
                        const pg = typeof production_entry !== 'undefined' && production_entry.planning_sheet_process_grid;
                        if (pg && typeof pg.after_reload === 'function') {
                            pg.after_reload(frm);
                        } else {
                            ps_schedule_planning_grid_columns(frm, 100, true);
                        }
                    });
                });
            },
        });

        // Fetch and display customer name
        if (frm.doc.customer) {
            frappe.call({
                method: 'frappe.client.get_value',
                args: {
                    doctype: 'Customer',
                    filters: { name: frm.doc.customer },
                    fieldname: ['customer_name']
                },
                callback: function(r) {
                    if (r.message) {
                        const customerName = r.message.customer_name || frm.doc.customer;
                        // Update the customer field display in the form
                        frm.set_df_property('customer', 'label', `Customer (${frm.doc.customer}): ${customerName}`);
                        // Also add visual indicator
                        frm.refresh_field('customer');
                    }
                }
            });
        }
    },
    
    customer: function(frm) {
        // When customer changes, update the display label
        if (frm.doc.customer) {
            frappe.call({
                method: 'frappe.client.get_value',
                args: {
                    doctype: 'Customer',
                    filters: { name: frm.doc.customer },
                    fieldname: ['customer_name']
                },
                callback: function(r) {
                    if (r.message) {
                        const customerName = r.message.customer_name || frm.doc.customer;
                        frm.set_df_property('customer', 'label', `Customer (${frm.doc.customer}): ${customerName}`);
                        frm.refresh_field('customer');
                    }
                }
            });
        }
    }
});

frappe.ui.form.on('Planning sheet Item', {
    item_code: function () {
        const frm = cur_frm;
        if (frm && frm.doctype === 'Planning sheet') {
            ps_schedule_planning_grid_columns(frm);
        }
    },
});

frappe.ui.form.on('Planning Table', {
    item_code: function () {
        const frm = cur_frm;
        if (frm && frm.doctype === 'Planning sheet') {
            ps_schedule_planning_grid_columns(frm);
        }
    },
});
