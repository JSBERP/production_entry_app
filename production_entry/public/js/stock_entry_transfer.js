// Transfer STE: barcode scans update scanned_qty only; approved qty stays fixed.

function _is_material_transfer(frm) {
	return (frm.doc.stock_entry_type || "").trim() === "Material Transfer";
}

function _externalTransferFieldname() {
	const candidates = [
		"custom_external_transfer",
		"external_transfer",
		"is_external_transfer",
		"ge_external_transfer",
	];
	for (const f of candidates) {
		if (frappe.meta.has_field("Stock Entry", f)) return f;
	}
	return "";
}

/** Scan / approved-roll rules only for logistics Transfer Approval STE — not manual Material Transfer. */
function _is_logistics_material_transfer(frm) {
	if (!_is_material_transfer(frm)) return false;
	const fn = _externalTransferFieldname();
	if (fn && cint(frm.doc[fn])) return true;
	if (frm._pe_logistics_transfer === true) return true;
	return false;
}

function cint(v) {
	const n = parseInt(v, 10);
	return Number.isFinite(n) ? n : 0;
}

function _applyTransferToCompanyFieldAccess(frm) {
	const logistics = _is_logistics_material_transfer(frm);
	["custom_transfer_to_company", "transfer_to_company"].forEach((fieldname) => {
		if (!frm.fields_dict[fieldname]) return;
		frm.set_df_property(fieldname, "read_only", logistics ? 1 : 0);
	});
}

function _refreshLogisticsTransferFlag(frm) {
	frm._pe_logistics_transfer = false;
	if (!_is_material_transfer(frm) || !frm.doc.name || frm.is_new()) return;
	frappe.call({
		method: "production_entry.production_planning.transfer_logistics.stock_entry_is_logistics_transfer",
		args: { stock_entry: frm.doc.name },
		async: false,
		callback(r) {
			frm._pe_logistics_transfer = !!(r.message);
			frm.refresh_fields();
		},
	});
}

function _disable_native_barcode_scanner(frm) {
	if (!_is_logistics_material_transfer(frm)) return;
	if (frm.barcode_scanner) {
		const noop = function () {};
		frm.barcode_scanner.process_scan = noop;
		frm.barcode_scanner.clean_up = noop;
		frm.barcode_scanner.update_item_quantity = noop;
		frm.barcode_scanner.update_item = noop;
		frm.barcode_scanner.scan_barcode = noop;
	}
	["scan_barcode", "custom_barcode_scanner"].forEach((fieldname) => {
		const fd = frm.fields_dict[fieldname];
		if (!fd || !fd.$input) return;
		fd.$input.off("change.barcode_scan");
		fd.$input.off("keydown.barcode_scan");
	});
}

function _protect_transfer_row_qty(frm) {
	if (!_is_logistics_material_transfer(frm)) return;
	(frm.doc.items || []).forEach((r) => {
		if (r && r.name) {
			r._protected_qty = Number(r._protected_qty != null ? r._protected_qty : r.qty || 0);
		}
	});
}

function _bind_scan_input(frm) {
	if (!_is_logistics_material_transfer(frm)) return;
	const fields = ["scan_barcode", "custom_barcode_scanner"];
	fields.forEach((fieldname) => {
		if (!frm.fields_dict[fieldname] || !frm.fields_dict[fieldname].$input) return;
		const $input = frm.fields_dict[fieldname].$input;
		$input.off(".transfer_scan");
		$input.on("keypress.transfer_scan", function (e) {
			if (e.which !== 13) return;
			e.preventDefault();
			e.stopImmediatePropagation();
			const val = ($input.val() || "").trim();
			$input.val("");
			if (frm.doc[fieldname]) frm.doc[fieldname] = "";
			if (val) _run_transfer_scan(frm, val);
		});
	});
}

function _run_transfer_scan(frm, barcode) {
	if (!barcode || !_is_logistics_material_transfer(frm)) return;
	if (frm._pe_scan_busy) return;
	frm._pe_scan_busy = true;
	const done = () => {
		setTimeout(() => {
			frm._pe_scan_busy = false;
		}, 400);
	};

	if (!frm.doc.name || frm.is_new()) {
		_scan_locally(frm, barcode);
		done();
		return;
	}

	frappe.call({
		method: "production_entry.production_planning.transfer_logistics.record_transfer_barcode_scan",
		args: { stock_entry: frm.doc.name, barcode: barcode },
		callback: function (r) {
			const msg = r.message || {};
			if (!msg.ok) {
				_scan_locally(frm, barcode);
				done();
				return;
			}
			if (msg.ask_unscan) {
				const batch = msg.batch_no || barcode;
				frappe.confirm(
					__("Remove {0} rolls?", [batch]),
					() => {
						frappe.call({
							method: "production_entry.production_planning.transfer_logistics.record_transfer_barcode_scan",
							args: { stock_entry: frm.doc.name, barcode: batch, confirm_unscan: 1 },
							callback: function (r2) {
								const cleared = r2.message || {};
								if (cleared.ok) {
									_apply_scan_to_locals(frm, cleared.row_name, 0, cleared.qty);
									frappe.show_alert({
										message: __("Scanned qty for {0} set to 0", [batch]),
										indicator: "orange",
									});
								}
								done();
							},
							error: () => done(),
						});
					},
					() => done()
				);
				return;
			}
			_apply_scan_to_locals(frm, msg.row_name, msg.scanned_qty, msg.qty);
			frappe.show_alert({
				message: __("Row #{0}: Scanned {1} / {2}", [msg.idx, msg.scanned_qty, msg.qty]),
				indicator: "green",
			});
			frappe.utils.play_sound("submit");
			done();
		},
		error: function () {
			_scan_locally(frm, barcode);
			done();
		},
	});
}

function _apply_scan_to_locals(frm, row_name, scanned_qty, qty) {
	const cdt = "Stock Entry Detail";
	const row = (frm.doc.items || []).find((r) => r.name === row_name);
	if (!row) return;
	row.scanned_qty = scanned_qty;
	row.custom_scanned_qty = scanned_qty;
	row.qty = qty;
	row._protected_qty = qty;
	frm.refresh_field("items");
}

function _normalizeTransferScan(raw) {
	let s = (raw || "").trim().toUpperCase().replace(/\s+/g, "");
	s = s.replace(/\*+$/, "");
	if (s.startsWith("#S")) s = "JS" + s.slice(2);
	else if (s.startsWith("IS-")) s = "JS-" + s.slice(3);
	else if (s.startsWith("IS") && s.length > 2) s = "JS" + s.slice(2);
	for (const prefix of ["JVE", "TTT", "VTP", "JS", "JV", "TS", "TT", "VR", "VT"]) {
		if (new RegExp(`^${prefix}\\d`).test(s)) {
			s = `${prefix}-` + s.slice(prefix.length);
			break;
		}
	}
	if (/^\d{6,}\/\d/.test(s)) s = "JS-" + s;
	return s;
}

function _batchMatchKey(v) {
	return _normalizeTransferScan(v).replace(/[^A-Z0-9/]/g, "");
}

function _transferScanCandidates(raw) {
	const cands = [];
	const add = (v) => {
		v = (v || "").trim();
		if (v && !cands.includes(v)) cands.push(v);
	};
	add(raw);
	add((raw || "").toUpperCase());
	const norm = _normalizeTransferScan(raw);
	add(norm);
	add(_batchMatchKey(norm));
	const m = norm.match(/\/(\d+)/);
	if (m) {
		add("/" + m[1]);
		if (m[1].length === 1) add("/" + m[1] + "0");
	}
	return cands;
}

function _find_transfer_row_for_barcode(frm, barcode) {
	const rows = frm.doc.items || [];
	const cands = _transferScanCandidates(barcode);
	for (const cand of cands) {
		const ck = _batchMatchKey(cand);
		for (const r of rows) {
			const bn = (r.batch_no || "").trim();
			const roll = (r.custom_roll_no || "").trim();
			if (cand === bn || cand === roll || ck === _batchMatchKey(bn)) return r;
		}
	}
	for (const cand of cands) {
		const m = _batchMatchKey(cand).match(/\/(\d+)$/);
		if (!m) continue;
		const suf = "/" + m[1];
		let matched = rows.filter((r) => _batchMatchKey(r.batch_no || "").includes(suf));
		if (matched.length === 1) return matched[0];
		if (m[1].length === 1) {
			const suf10 = "/" + m[1] + "0";
			matched = rows.filter((r) => _batchMatchKey(r.batch_no || "").endsWith(suf10));
			if (matched.length === 1) return matched[0];
		}
	}
	return null;
}

function _scan_locally(frm, barcode) {
	const existing_row = _find_transfer_row_for_barcode(frm, barcode);
	if (!existing_row) {
		frappe.msgprint({
			title: __("Batch Not Found"),
			indicator: "orange",
			message: __("The scanned batch <b>{0}</b> is not in the approved list.", [barcode]),
		});
		frappe.utils.play_sound("error");
		return;
	}

	const approved = Number(existing_row._protected_qty != null ? existing_row._protected_qty : existing_row.qty || 0);
	const already = flt(existing_row.scanned_qty || existing_row.custom_scanned_qty);
	if (already > 0) {
		const batch = existing_row.batch_no || barcode;
		frappe.confirm(__("Remove {0} rolls?", [batch]), () => {
			const updates = { custom_scanned_qty: 0, qty: approved };
			if (frappe.meta.has_field("Stock Entry Detail", "scanned_qty")) {
				updates.scanned_qty = 0;
			}
			frappe.model.set_value(existing_row.doctype, existing_row.name, updates).then(() => {
				existing_row._protected_qty = approved;
				frm.refresh_field("items");
				frappe.show_alert({
					message: __("Scanned qty for {0} set to 0", [batch]),
					indicator: "orange",
				});
			});
		});
		return;
	}
	const new_scanned = approved > 0 ? approved : 0;
	const updates = { custom_scanned_qty: new_scanned, qty: approved };
	if (frappe.meta.has_field("Stock Entry Detail", "scanned_qty")) {
		updates.scanned_qty = new_scanned;
	}

	frappe.model.set_value(existing_row.doctype, existing_row.name, updates).then(() => {
		existing_row._protected_qty = approved;
		frm.refresh_field("items");
		frappe.show_alert({
			message: __("Row #{0}: Scanned {1} / {2}", [existing_row.idx, new_scanned, approved]),
			indicator: "green",
		});
		frappe.utils.play_sound("submit");
	});
}

function _syncManualScannedQty(frm, cdt, cdn, sourceField) {
	if (!_is_logistics_material_transfer(frm)) return;
	const row = frappe.get_doc(cdt, cdn);
	const val = flt(row[sourceField] || 0);
	const updates = {};
	if (frappe.meta.has_field("Stock Entry Detail", "scanned_qty")) {
		updates.scanned_qty = val;
	}
	if (frappe.meta.has_field("Stock Entry Detail", "custom_scanned_qty")) {
		updates.custom_scanned_qty = val;
	}
	Object.keys(updates).forEach((field) => {
		if (flt(row[field]) !== updates[field]) {
			frappe.model.set_value(cdt, cdn, field, updates[field]);
		}
	});
}

function _normalizeTransferScanRows(frm) {
	if (!_is_logistics_material_transfer(frm) || !frm.doc.items) return;
	(frm.doc.items || []).forEach((row) => {
		const qty = flt(row.qty);
		const scanned = flt(row.scanned_qty || row.custom_scanned_qty);
		if (qty > 0 && scanned >= qty - 0.01) {
			row.scanned_qty = qty;
			row.custom_scanned_qty = qty;
		}
	});
}

frappe.ui.form.on("Stock Entry", {
	setup(frm) {
		_disable_native_barcode_scanner(frm);
		if (frm.script_manager && frm.script_manager.events) {
			frm.script_manager.events.scan_barcode = [
				function (f) {
					if (_is_material_transfer(f)) f.trigger("process_barcode_scan");
				},
			];
			frm.script_manager.events.custom_barcode_scanner = [
				function (f) {
					if (_is_material_transfer(f)) f.trigger("process_barcode_scan");
				},
			];
		}
	},

	scan_barcode(frm) {
		if (!_is_logistics_material_transfer(frm)) return;
		const val = (frm.doc.scan_barcode || "").trim();
		if (val) {
			frm.doc.scan_barcode = "";
			_run_transfer_scan(frm, val);
		}
	},

	custom_barcode_scanner(frm) {
		if (!_is_logistics_material_transfer(frm)) return;
		const val = (frm.doc.custom_barcode_scanner || "").trim();
		if (val) {
			frm.doc.custom_barcode_scanner = "";
			_run_transfer_scan(frm, val);
		}
	},

	refresh(frm) {
		_refreshLogisticsTransferFlag(frm);
		_disable_native_barcode_scanner(frm);
		_protect_transfer_row_qty(frm);
		_bind_scan_input(frm);
		_applyTransferToCompanyFieldAccess(frm);

		const logistics = _is_logistics_material_transfer(frm);
		const co = (frm.doc.custom_transfer_to_company || "").trim();
		if (logistics && co) {
			if (frm.fields_dict.party_type && !frm.doc.party_type) {
				frm.set_value("party_type", "Company");
			}
			if (frm.fields_dict.party && !frm.doc.party) {
				frm.set_value("party", co);
			}
		}

		if (
			frm.doc.docstatus === 0 &&
			_is_logistics_material_transfer(frm) &&
			frm.doc.items &&
			frm.doc.items.length > 0
		) {
			frm.add_custom_button(
				__("Removed Rolls"),
				() => _showTransferRemovedRolls(frm),
				__("Actions")
			);
			frm.add_custom_button(
				__("Approved Rolls"),
				() => {
					frappe.call({
						method: "production_entry.production_planning.transfer_logistics.get_transfer_approval_roll_list",
						args: { stock_entry: frm.doc.name },
						callback: function (r) {
							const msg = r.message || {};
							const rolls = msg.rolls || [];
							if (!rolls.length) {
								frappe.msgprint(__("No Transfer Approval lines linked to this Stock Entry."));
								return;
							}
							if (typeof jsb_show_despatch_rolls_dialog === "function") {
								const orderCodes = [];
								const headerOc = String(msg.order_code || frm.doc.custom_order_code || frm.doc.order_code || "").trim();
								if (headerOc) orderCodes.push(headerOc);
								rolls.forEach((row) => {
									const oc = String(row.party_code || row.order_code || "").trim();
									if (oc && orderCodes.indexOf(oc) === -1) orderCodes.push(oc);
								});
								jsb_show_despatch_rolls_dialog({
									rolls: rolls,
									order_code: orderCodes.join(", "),
									sales_order: orderCodes.join(", "),
								});
								return;
							}
							frappe.msgprint(__("Roll print dialog not loaded — hard refresh (Ctrl+Shift+R)."));
						},
					});
				},
				__("Actions")
			);
		}

		if (frm.doc.docstatus === 1 && frm.doc.items && frm.doc.items.length > 0) {
			frm.add_custom_button(__("Sales Invoice"), () => {
				frappe.model.open_mapped_doc({
					method: "production_entry.stock_entry_sales_invoice.make_sales_invoice_from_stock_entry",
					frm: frm,
				});
			}, __("Create"));
		}

		_refreshTransferLoad(frm);
	},

	before_submit(frm) {
		if (!_is_logistics_material_transfer(frm) || !frm.doc.items) {
			return;
		}
		_normalizeTransferScanRows(frm);
		const pending = frm.doc.items.filter(
			(r) => (r.qty || 0) > ((r.scanned_qty || r.custom_scanned_qty) || 0) + 0.01
		);
		if (pending.length > 0) {
			const pending_items = pending.map((r) => r.item_code).join(", ");
			frappe.msgprint({
				title: "Scan validation",
				indicator: "red",
				message: __(
					"You must scan all approved rolls before submit. Missing scan for: <b>{0}</b>.",
					[pending_items]
				),
			});
			frappe.validated = false;
		}
	},

	process_barcode_scan: function (frm) {
		const barcode = (frm.doc.scan_barcode || frm.doc.custom_barcode_scanner || "").trim();
		if (!barcode || !_is_logistics_material_transfer(frm)) return;
		frm.doc.scan_barcode = "";
		frm.doc.custom_barcode_scanner = "";
		_run_transfer_scan(frm, barcode);
	},
});

const LOADING_API = "production_entry.production_planning.loading_session";

function _clearTransferLoad(frm) {
	if (frm._pe_load_tick) {
		clearInterval(frm._pe_load_tick);
		frm._pe_load_tick = null;
	}
	const $head = _transferLoadHead(frm);
	if ($head) {
		$head.find(".pe-load-clock, .pe-load-btns").remove();
	}
}

function _transferLoadHead(frm) {
	if (!frm.page || !frm.page.wrapper) return null;
	const $head = $(frm.page.wrapper).find(".page-head").first();
	return $head.length ? $head : null;
}

function _formatLoadHms(totalSeconds) {
	const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
	const h = Math.floor(sec / 3600);
	const m = Math.floor((sec % 3600) / 60);
	const s = sec % 60;
	return [h, m, s].map((n) => String(n).padStart(2, "0")).join(":");
}

function _ensureTransferLoadHeader(frm) {
	const $head = _transferLoadHead(frm);
	if (!$head) return null;
	let $clock = $head.find(".pe-load-clock");
	if (!$clock.length) {
		$clock = $('<span class="pe-load-clock">00:00:00</span>');
		$clock.css({
			marginLeft: "12px",
			marginRight: "8px",
			fontFamily: "ui-monospace, monospace",
			fontWeight: "800",
			fontSize: "15px",
			color: "#14532d",
			background: "#dcfce7",
			border: "1px solid #86efac",
			borderRadius: "8px",
			padding: "4px 10px",
			alignSelf: "center",
			letterSpacing: "0.03em",
		});
		const $actions = $head.find(".page-actions").first();
		if ($actions.length) $clock.insertBefore($actions);
		else $head.append($clock);
	}
	let $btns = $head.find(".pe-load-btns");
	if (!$btns.length) {
		$btns = $('<span class="pe-load-btns"></span>');
		$btns.css({
			display: "inline-flex",
			gap: "6px",
			alignItems: "center",
			marginRight: "8px",
		});
		const $actions = $head.find(".page-actions").first();
		if ($actions.length) $actions.prepend($btns);
		else $head.append($btns);
	}
	return { $clock, $btns };
}

function _paintTransferLoadClock(frm) {
	const $head = _transferLoadHead(frm);
	if (!$head) return;
	const $clock = $head.find(".pe-load-clock");
	if (!$clock.length) {
		_paintTransferLoadButtons(frm);
		return;
	}
	const session = frm._pe_load_session || {};
	let sec = Number(session.elapsed_seconds) || 0;
	if (session.status === "running" && frm._pe_load_session_at) {
		sec += Math.max(0, (Date.now() - frm._pe_load_session_at) / 1000);
	}
	$clock.text(_formatLoadHms(sec));
}

function _paintTransferLoadButtons(frm) {
	const ui = _ensureTransferLoadHeader(frm);
	if (!ui) return;
	const status = (frm._pe_load_session && frm._pe_load_session.status) || "idle";
	const buttons = [];
	if (status === "idle") {
		buttons.push(["start", __("Start Loading"), "btn-success"]);
		buttons.push(["pause", __("Pause Loading"), "btn-warning"]);
		buttons.push(["stop", __("Stop Loading"), "btn-danger"]);
	} else if (status === "running") {
		buttons.push(["pause", __("Pause Loading"), "btn-warning"]);
		buttons.push(["stop", __("Stop Loading"), "btn-danger"]);
	} else if (status === "paused") {
		buttons.push(["continue", __("Continue Loading"), "btn-info"]);
		buttons.push(["stop", __("Stop Loading"), "btn-danger"]);
	}
	ui.$btns.empty();
	buttons.forEach(([action, label, cls]) => {
		const $b = $(`<button type="button" class="btn btn-sm ${cls}"></button>`).text(label);
		$b.on("click", () => _setTransferLoad(frm, action));
		ui.$btns.append($b);
	});
	const $removed = $(`<button type="button" class="btn btn-sm"></button>`).text(__("Removed Rolls"));
	$removed.css({ background: "#b45309", color: "#fff" });
	$removed.on("click", () => _showTransferRemovedRolls(frm));
	ui.$btns.append($removed);
	_paintTransferLoadClock(frm);
}

function _applyTransferLoadSession(frm, session) {
	frm._pe_load_session = session || { status: "idle", elapsed_seconds: 0 };
	frm._pe_load_session_at = Date.now();
	_paintTransferLoadButtons(frm);
	if (frm._pe_load_tick) clearInterval(frm._pe_load_tick);
	frm._pe_load_tick = setInterval(() => _paintTransferLoadClock(frm), 1000);
}

function _refreshTransferLoad(frm) {
	if (!_is_logistics_material_transfer(frm) || !frm.doc.name || frm.is_new() || frm.doc.docstatus !== 0) {
		_clearTransferLoad(frm);
		return;
	}
	frappe.call({
		method: `${LOADING_API}.get_loading_session`,
		args: { doctype: "Stock Entry", name: frm.doc.name },
		callback(r) {
			if (!_is_logistics_material_transfer(frm)) return;
			_applyTransferLoadSession(frm, r.message);
		},
	});
}

function _setTransferLoad(frm, action) {
	const status = (frm._pe_load_session && frm._pe_load_session.status) || "idle";
	if ((action === "pause" || action === "stop") && status === "idle") {
		frappe.show_alert({ message: __("Start loading first."), indicator: "orange" });
		return;
	}
	frappe.call({
		method: `${LOADING_API}.set_loading_session`,
		args: { doctype: "Stock Entry", name: frm.doc.name, action },
		callback(r) {
			_applyTransferLoadSession(frm, r.message);
		},
	});
}

function _showTransferRemovedRolls(frm) {
	if (!frm.doc.name) return;
	frappe.call({
		method: `${LOADING_API}.get_removed_rolls`,
		args: { doctype: "Stock Entry", name: frm.doc.name },
		freeze: true,
		freeze_message: __("Loading removed rolls…"),
		callback(r) {
			const rolls = (r.message && r.message.rolls) || [];
			if (typeof jsb_show_removed_rolls_dialog === "function") {
				const orderCodes = [];
				(frm.doc.items || []).forEach((row) => {
					const oc = String(row.custom_order_code || row.order_code || "").trim();
					if (oc && orderCodes.indexOf(oc) === -1) orderCodes.push(oc);
				});
				const headerOc = String(frm.doc.custom_order_code || frm.doc.order_code || "").trim();
				jsb_show_removed_rolls_dialog({
					rolls,
					order_code: headerOc || orderCodes.join(", "),
				});
				return;
			}
			frappe.msgprint(__("Roll dialog not loaded — hard refresh (Ctrl+Shift+R)."));
		},
	});
}

frappe.ui.form.on("Stock Entry Detail", {
	scanned_qty(frm, cdt, cdn) {
		_syncManualScannedQty(frm, cdt, cdn, "scanned_qty");
	},
	custom_scanned_qty(frm, cdt, cdn) {
		_syncManualScannedQty(frm, cdt, cdn, "custom_scanned_qty");
	},
	qty: function (frm, cdt, cdn) {
		let row = frappe.get_doc(cdt, cdn);
		if (
			_is_logistics_material_transfer(frm) &&
			row._protected_qty !== undefined &&
			row.qty !== row._protected_qty
		) {
			frappe.model.set_value(cdt, cdn, "qty", row._protected_qty);
		}
	},
});
