/* global frappe, __ */

const MIXING_API = "production_entry.production_planning.mixing_sheet_api";

const PRINTING_MACHINES = [
	"VR - 1200MM BOPP PRINTING MACHINE",
	"JVE - PRINTING MACHINE 2 COLOUR 1600MM",
	"JVE - PRINTING MACHINE 4 COLOUR 1600MM",
	"TT - PRINTING MACHINE 4 COLOUR 1200MM",
];

const SOLVENT_MACHINES = ["VR - 1200MM BOPP PRINTING MACHINE"];

const EXCLUDED_MACHINES = [
	"TTT- L3 - OYANG C900 BAG MAKING LINE",
	"TTT- L2 - OYANG C700 BAG MAKING LINE",
	"TTT- L1 - OYANG C700 BAG MAKING LINE",
	"VTP-L1 LEADER OYANG MACHINE",
	"VTP-L2 LEADER ZX MACHINE",
	"JVE-L3 B700 BAG MAKING MACHINE",
	"JVE-L2 B700 BAG MAKING MACHINE",
	"JVE-L1 B700 BAG MAKING MACHINE",
];

let _mix_dialog = null;

function mixing_api_args(ctx) {
	const args = {
		mixing_sheet_name: ctx.mixing_sheet_name || "",
		gsm_shift_session: ctx.gsm_shift_session || "",
		run_date: ctx.run_date || "",
		shift: ctx.shift || "",
		custom_unit: ctx.custom_unit || "",
	};
	if (!ctx.shift_only) {
		args.spr_name = ctx.spr_name || "";
		args.order_code = ctx.order_code || "";
	}
	return args;
}

function make_empty_set() {
	return { materials: {}, extras: [], rows: [], item_names: {} };
}

function set_has_materials(set) {
	const m = set?.materials || {};
	return !!(m.PP || m.Ink);
}

function extra_group_key(ex) {
	const code = String(ex?.item_code || "").toUpperCase().trim();
	const name = String(ex?.item_name || "").toUpperCase();
	const blob = `${code} ${name}`;
	if (code.startsWith("PP ") || code.startsWith("PP-")) return "PP";
	if (code.startsWith("LD ") || code.startsWith("LD-") || blob.includes(" LD") || /\bLD\b/.test(blob))
		return "LD";
	if (code.startsWith("FL ") || code.startsWith("FL-") || blob.includes("FILLER")) return "Filler";
	if (code.startsWith("MB ") || code.startsWith("MB-") || blob.includes("MASTERBATCH")) return "Masterbatch";
	if (blob.includes("ANTISTATIC")) return "Antistatic";
	if (code.startsWith("SA ") || code.startsWith("SA-")) return "PPA";
	return "Other";
}

function extras_in_group(set, group) {
	return (set.extras || []).filter((ex) => extra_group_key(ex) === group);
}

function extras_headers_html(set, group) {
	return extras_in_group(set, group)
		.map((ex) => `<th>${frappe.utils.escape_html(ex.item_name || ex.item_code)} (kg)</th>`)
		.join("");
}

function extras_cells_html(set, row, si, ri, dis, group) {
	return extras_in_group(set, group)
		.map((ex) => {
			const val = (row.extras && row.extras[ex.item_code]) || 0;
			const code = frappe.utils.escape_html(ex.item_code);
			return `<td><input class="form-control form-control-sm row-qty-extra" data-set="${si}" data-row="${ri}" data-item="${code}" value="${val}" style="width:70px;text-align:center" ${dis}></td>`;
		})
		.join("");
}

function extras_print_headers(set, group) {
	return extras_in_group(set, group)
		.map((ex) => `<th>${frappe.utils.escape_html(ex.item_name || ex.item_code)} (kg)</th>`)
		.join("");
}

function extras_print_cells(set, row, group) {
	return extras_in_group(set, group)
		.map((ex) => `<td style="text-align:center">${(row.extras && row.extras[ex.item_code]) || 0}</td>`)
		.join("");
}

function has_material(m, key) {
	return !!(m?.[key] || "").toString().trim();
}

function fetch_item_name(item_code) {
	if (!item_code) {
		return Promise.resolve({ message: { item_name: "" } });
	}
	return frappe.db.get_value("Item", item_code, "item_name");
}

function mixing_set_label(si) {
	return __("Set {0}", [si + 1]);
}

function default_mixing_type(unit) {
	const u = String(unit || "")
		.toUpperCase()
		.replace(/-/g, " ");
	if (u.includes("UNIT 4") || /\bUNIT4\b/.test(u)) {
		return "Half Mixing";
	}
	if (u.includes("UNIT 2") || u.includes("UNIT 3") || /\bUNIT[23]\b/.test(u)) {
		return "Full Mixing";
	}
	if (u.includes("UNIT 1") || /\bUNIT1\b/.test(u)) {
		return "Half Mixing";
	}
	return "Full Mixing";
}

function make_empty_row(custom_unit) {
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const uses_solvent = SOLVENT_MACHINES.includes(custom_unit);
	if (is_printing) {
		const row = { ink_qty: 0, consumed: false, consumed_by: null, consumed_at: null };
		if (uses_solvent) {
			row.ea_qty = 0;
			row.tol_qty = 0;
			row.iso_qty = 0;
		}
		return row;
	}
	return {
		pp_qty: 0,
		filler_qty: 0,
		ld_qty: 0,
		mb_qty: 0,
		anti_qty: 0,
		ppa_qty: 0,
		mixing_type: default_mixing_type(custom_unit),
		consumed: false,
		consumed_by: null,
		consumed_at: null,
	};
}

function build_rows(set_obj, custom_unit) {
	set_obj.rows = [make_empty_row(custom_unit)];
}

function isMixingExcludedUnit(unit) {
	return EXCLUDED_MACHINES.includes((unit || "").trim());
}

function isLaminationMixingUnit(unit) {
	const u = String(unit || "")
		.toUpperCase()
		.replace(/-/g, " ");
	return u.includes("LAMINATION");
}

function mixingUsesLdSlot(custom_unit, materials) {
	if (isLaminationMixingUnit(custom_unit)) {
		return true;
	}
	return has_material(materials, "LD");
}

async function openSprMixingSheet(ctx = {}) {
	const unit = (ctx.custom_unit || "").trim();
	if (!unit) {
		frappe.msgprint(__("Unit is required to open Mixing Sheet."));
		return;
	}
	if (isMixingExcludedUnit(unit)) {
		frappe.msgprint(__("Mixing Sheet is not available for this machine."));
		return;
	}

	const res = await frappe.call({
		method: `${MIXING_API}.get_mixing_sheet`,
		args: mixing_api_args(ctx),
	});
	const msg = res.message || {};
	if (msg.completed_exists || String(msg.status || "") === "Completed") {
		frappe.msgprint({
			title: __("Mixing already submitted"),
			indicator: "orange",
			message: __(
				"Mixing for this date and shift is already finished. Select another date or shift."
			),
		});
		if (typeof ctx.on_completed_blocked === "function") {
			ctx.on_completed_blocked();
		}
		return;
	}
	const sheetCtx = {
		...ctx,
		custom_unit: msg.custom_unit || unit,
		run_date: msg.run_date || ctx.run_date || "",
		shift: msg.shift || ctx.shift || "",
		gsm_shift_session: msg.gsm_shift_session || ctx.gsm_shift_session || "",
		spr_name: msg.shaft_production_run || ctx.spr_name || "",
		mixing_sheet_name: msg.mixing_sheet_name || ctx.mixing_sheet_name || "",
		order_code: msg.order_code || ctx.order_code || "",
	};
	let existing = null;
	try {
		existing = msg.existing_mixing_data ? JSON.parse(msg.existing_mixing_data) : null;
	} catch (e) {
		existing = null;
	}
	show_dialog(sheetCtx, existing, ctx.frm || null);
}

function sprMixingShiftStorageKey(sprName) {
	return "pe_spr_mixing_shift:" + String(sprName || "");
}

function readSprMixingShiftChoice(frm) {
	if (!frm || !frm.doc || !frm.doc.name) {
		return null;
	}
	if (frm._spr_mixing_run_date && frm._spr_mixing_shift) {
		return { run_date: frm._spr_mixing_run_date, shift: frm._spr_mixing_shift };
	}
	try {
		const raw = sessionStorage.getItem(sprMixingShiftStorageKey(frm.doc.name));
		if (!raw) {
			return null;
		}
		const parsed = JSON.parse(raw);
		if (parsed && parsed.run_date && parsed.shift) {
			frm._spr_mixing_run_date = parsed.run_date;
			frm._spr_mixing_shift = parsed.shift;
			return parsed;
		}
	} catch (e) {
		/* ignore */
	}
	return null;
}

function rememberSprMixingShiftChoice(frm, run_date, shift) {
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	frm._spr_mixing_run_date = run_date;
	frm._spr_mixing_shift = shift;
	try {
		sessionStorage.setItem(
			sprMixingShiftStorageKey(frm.doc.name),
			JSON.stringify({ run_date, shift })
		);
	} catch (e) {
		/* ignore */
	}
}

function forgetSprMixingShiftChoice(frm) {
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	frm._spr_mixing_run_date = "";
	frm._spr_mixing_shift = "";
	try {
		sessionStorage.removeItem(sprMixingShiftStorageKey(frm.doc.name));
	} catch (e) {
		/* ignore */
	}
}

function mixingUnitForSpr(frm) {
	const unit = String((frm && frm.doc && frm.doc.custom_unit) || "").trim();
	if (unit) {
		return unit;
	}
	return "TNSPL - LAMINATION UNIT";
}

function promptSprMixingDateShift(frm, on_pick) {
	frappe.prompt(
		[
			{
				fieldname: "run_date",
				label: __("Date"),
				fieldtype: "Date",
				reqd: 1,
				default: frappe.datetime.get_today(),
			},
			{
				fieldname: "shift",
				label: __("Shift"),
				fieldtype: "Select",
				options: "Day Shift\nNight Shift",
				reqd: 1,
				default: "Day Shift",
			},
		],
		(v) => {
			if (typeof on_pick === "function") {
				on_pick(v.run_date, v.shift);
			}
		},
		__("Mixing Sheet — Date & Shift"),
		__("Open")
	);
}

function openMixingSheetForSprChoice(frm, run_date, shift) {
	const unit = mixingUnitForSpr(frm);
	rememberSprMixingShiftChoice(frm, run_date, shift);
	openSprMixingSheet({
		frm,
		shift_only: true,
		spr_name: frm.doc.name,
		custom_unit: unit,
		run_date,
		shift,
		title_label: `${run_date} · ${shift} · ${unit}`,
		on_completed_blocked: () => {
			forgetSprMixingShiftChoice(frm);
			promptSprMixingDateShift(frm, (d, s) => openMixingSheetForSprChoice(frm, d, s));
		},
	});
}

function open_mixing_sheet_desk(frm, forcePrompt) {
	if (frm.doc.docstatus !== 0) {
		frappe.msgprint({
			title: __("Action Restricted"),
			indicator: "orange",
			message: __(
				"The Mixing Sheet cannot be opened because this Shaft Production Run is already submitted or cancelled."
			),
		});
		return;
	}
	const saved = forcePrompt ? null : readSprMixingShiftChoice(frm);
	if (saved) {
		openMixingSheetForSprChoice(frm, saved.run_date, saved.shift);
		return;
	}
	promptSprMixingDateShift(frm, (run_date, shift) => {
		openMixingSheetForSprChoice(frm, run_date, shift);
	});
}

function normalizeMixingState(raw, custom_unit) {
	const state = raw || { mixing_type: "", sets: [make_empty_set()], completed: false };
	if (state.sets && typeof state.sets === "object" && !Array.isArray(state.sets)) {
		state.sets = [state.sets];
	}
	if (!Array.isArray(state.sets) || !state.sets.length) {
		state.sets = [make_empty_set()];
	}
	const fallbackType = state.mixing_type || default_mixing_type(custom_unit);
	state.sets = state.sets.map((setObj) => {
		const set = setObj && typeof setObj === "object" ? { ...setObj } : make_empty_set();
		if (!Array.isArray(set.extras)) set.extras = [];
		if (!Array.isArray(set.rows)) set.rows = [];
		if (!set.materials || typeof set.materials !== "object") set.materials = {};
		if (isLaminationMixingUnit(custom_unit) && !set.materials.LD && set.materials.Filler) {
			set.materials.LD = set.materials.Filler;
		}
		set.rows = set.rows.map((row) => {
			const r = row && typeof row === "object" ? { ...row } : make_empty_row(custom_unit);
			if (!r.mixing_type) {
				r.mixing_type = fallbackType;
			}
			if (isLaminationMixingUnit(custom_unit) && !r.ld_qty && r.filler_qty) {
				r.ld_qty = r.filler_qty;
			}
			return r;
		});
		return set;
	});
	return state;
}

function show_dialog(ctx, existing, frm) {
	const custom_unit = ctx.custom_unit;
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const uses_solvent = SOLVENT_MACHINES.includes(custom_unit);
	if (_mix_dialog) _mix_dialog.hide();

	const state = normalizeMixingState(existing, custom_unit);
	if (state.completed || ctx.read_only) {
		frappe.msgprint({
			title: __("Mixing Completed"),
			indicator: "green",
			message: __("This Mixing Sheet is completed and read-only."),
		});
	}

	const title =
		ctx.title_label ||
		ctx.order_code ||
		ctx.spr_name ||
		`${ctx.run_date || ""} · ${ctx.shift || ""} · ${custom_unit}`;

	const fields = [];
	fields.push({ fieldtype: "Section Break", label: __("Raw Materials") });

	if (is_printing) {
		fields.push(
			{ fieldtype: "Column Break" },
			{
				fieldname: "ink_item",
				label: __("BOPP Ink"),
				fieldtype: "Link",
				options: "Item",
				get_query: () => ({ filters: { item_code: ["like", "INK -%"] } }),
			}
		);
		if (uses_solvent) {
			fields.push(
				{ fieldtype: "Column Break" },
				{ fieldname: "ethyl_acetate_item", label: __("Ethyl Acetate"), fieldtype: "Link", options: "Item" },
				{ fieldtype: "Column Break" },
				{ fieldname: "toluene_item", label: __("Toluene"), fieldtype: "Link", options: "Item" },
				{ fieldtype: "Column Break" },
				{
					fieldname: "iso_butanol_item",
					label: __("Iso Butanol (Optional)"),
					fieldtype: "Link",
					options: "Item",
				}
			);
		}
	} else {
		const useLd = isLaminationMixingUnit(custom_unit);
		fields.push(
			{ fieldtype: "Column Break" },
			{
				fieldname: "pp_item",
				label: __("Polypropylene"),
				fieldtype: "Link",
				options: "Item",
				get_query: () => ({ filters: { item_code: ["like", "PP -%"] } }),
			},
			{ fieldtype: "Column Break" },
			useLd
				? {
						fieldname: "ld_item",
						label: __("LD"),
						fieldtype: "Link",
						options: "Item",
						get_query: () => ({ filters: { item_code: ["like", "%LD%"] } }),
					}
				: {
						fieldname: "filler_item",
						label: __("Filler"),
						fieldtype: "Link",
						options: "Item",
						get_query: () => ({ filters: { item_code: ["like", "FL -%"] } }),
					},
			{ fieldtype: "Column Break" },
			{
				fieldname: "masterbatch_item",
				label: __("Colour Masterbatch"),
				fieldtype: "Link",
				options: "Item",
				get_query: () => ({ filters: { item_code: ["like", "MB -%"] } }),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "antistatic_item",
				label: __("Antistatic"),
				fieldtype: "Link",
				options: "Item",
				get_query: () => ({ filters: { item_code: ["like", "SA -%"] } }),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "ppa_item",
				label: __("Modifier (PPA)"),
				fieldtype: "Link",
				options: "Item",
				get_query: () => ({ filters: { item_code: ["like", "SA -%"] } }),
			}
		);
	}
	fields.push({ fieldtype: "Section Break" }, { fieldname: "main_html", fieldtype: "HTML" });

	const d = new frappe.ui.Dialog({
		title: `${__("Mixing Sheet")} — ${title}`,
		size: "extra-large",
		fields,
	});
	const readOnly = !!(state.completed || ctx.read_only);

	d.footer.empty().append(`
		<div style="display:flex;justify-content:space-between;width:100%;flex-wrap:wrap;gap:6px">
			<button class="btn btn-sm btn-default" id="btn_add_set" ${readOnly ? "disabled" : ""}>➕ ${__("Add Raw Material Set")}</button>
			<div style="display:flex;gap:8px;flex-wrap:wrap">
				<button class="btn btn-sm btn-warning" id="btn_print">🖨 ${__("Print Sheet")}</button>
				${is_printing ? `<button class="btn btn-sm btn-info" id="btn_add_ink" ${readOnly ? "disabled" : ""}>➕ ${__("Add Ink")}</button>` : `<button class="btn btn-sm btn-info" id="btn_add_additive" ${readOnly ? "disabled" : ""}>➕ ${__("Add Special Item")}</button>`}
				<button class="btn btn-sm btn-success" id="btn_save_rm" ${readOnly ? "disabled" : ""}>💾 ${__("Save Raw Materials")}</button>
				<button class="btn btn-sm btn-primary" id="btn_save_all" ${readOnly ? "disabled" : ""}>✅ ${__("Save Mixing Sheet")}</button>
				<button class="btn btn-sm btn-primary" id="btn_submit_mixing" style="background:#2e7d32;border:none;" ${readOnly ? "disabled" : ""}>🚩 ${__("Finish & Submit")}</button>
			</div>
		</div>`);

	d.show();
	_mix_dialog = d;
	d.footer.removeClass("hide").show();

	const m0 = state.sets[0]?.materials || {};
	if (is_printing) {
		d.set_value("ink_item", m0.Ink || "");
		if (uses_solvent) {
			d.set_value("ethyl_acetate_item", m0.EthylAcetate || "");
			d.set_value("toluene_item", m0.Toluene || "");
			d.set_value("iso_butanol_item", m0.IsoButanol || "");
		}
	} else {
		d.set_value("pp_item", m0.PP || "");
		if (isLaminationMixingUnit(custom_unit)) {
			d.set_value("ld_item", m0.LD || m0.Filler || "");
		} else {
			d.set_value("filler_item", m0.Filler || "");
		}
		d.set_value("masterbatch_item", m0.Masterbatch || "");
		d.set_value("antistatic_item", m0.Antistatic || "");
		d.set_value("ppa_item", m0.PPA || "");
	}

	render_all(d, ctx, state, frm);

	d.footer.find("#btn_save_rm").on("click", () => save_raw_materials(d, ctx, state, frm));
	d.footer.find("#btn_add_set").on("click", () => {
		if (state.completed || ctx.read_only) return;
		collect_row_qtys(d, state);
		const last = state.sets[state.sets.length - 1];
		if (!set_has_materials(last)) {
			frappe.msgprint(__("Save raw materials on the current set first."));
			return;
		}
		state.sets.push(make_empty_set());
		render_all(d, ctx, state, frm);
		if (typeof d._mixPersist === "function") d._mixPersist();
	});
	d.footer.find("#btn_print").on("click", () => print_mixing_sheet(state, ctx));

	const add_extra_item = (item_code) => {
		const active_idx = state.sets.length - 1;
		if (!state.sets[active_idx].extras) state.sets[active_idx].extras = [];
		frappe.db.get_value("Item", item_code, "item_name").then((r) => {
			state.sets[active_idx].extras.push({
				item_code,
				item_name: r.message?.item_name || item_code,
			});
			state.sets[active_idx].rows.forEach((row) => {
				if (row.extras === undefined) row.extras = {};
				row.extras[item_code] = 0;
			});
			render_all(d, ctx, state, frm);
		});
	};

	d.footer.find("#btn_add_additive").on("click", () => {
		if (state.completed) return;
		const last = state.sets[state.sets.length - 1];
		if (!last.materials?.PP) {
			frappe.msgprint(__("Select raw materials and click <b>Save Raw Materials</b> first."));
			return;
		}
		frappe.prompt(
			[
				{
					label: __("Select Item"),
					fieldname: "item_code",
					fieldtype: "Link",
					options: "Item",
					reqd: 1,
					get_query: () => ({
						query: "production_entry.production_planning.shift_consumables_api.consumable_item_query",
					}),
				},
			],
			(v) => add_extra_item(v.item_code),
			__("Add Dana / Special Additive")
		);
	});
	d.footer.find("#btn_add_ink").on("click", () => {
		if (state.completed) return;
		const last = state.sets[state.sets.length - 1];
		if (!last.materials?.Ink) {
			frappe.msgprint(__("Select ink and click <b>Save Raw Materials</b> first."));
			return;
		}
		frappe.prompt(
			[
				{
					label: __("Select Ink"),
					fieldname: "item_code",
					fieldtype: "Link",
					options: "Item",
					reqd: 1,
					get_query: () => ({ filters: { item_code: ["like", "INK -%"] } }),
				},
			],
			(v) => add_extra_item(v.item_code),
			__("Add Ink Item")
		);
	});

	const persist = (done) => {
		frappe.call({
			method: `${MIXING_API}.save_shift_mixing_sheet`,
			args: {
				...mixing_api_args(ctx),
				mixing_sheet_json: JSON.stringify(state),
			},
			callback(r) {
				if (!r.exc && r.message) {
					ctx.mixing_sheet_name = r.message.mixing_sheet_name || ctx.mixing_sheet_name;
					if (done) done(r);
				}
			},
		});
	};
	d._mixPersist = persist;

	const on_submit_mixing = () => {
		if (state.completed) return;
		frappe.confirm(
			__("<b>Finish and submit this Mixing Sheet?</b><br><br>This will lock the sheet."),
			() => {
				collect_row_qtys(d, state);
				state.completed = true;
				persist(() => {
					frappe.msgprint(__("Mixing Sheet submitted successfully."));
					d.hide();
					if (frm) frm.reload_doc();
					if (ctx.on_saved) ctx.on_saved();
				});
			}
		);
	};
	d.footer.find("#btn_submit_mixing").on("click", on_submit_mixing);

	d.footer.find("#btn_save_all").on("click", () => {
		collect_row_qtys(d, state);
		persist(() => {
			frappe.show_alert({ message: __("Mixing Sheet saved."), indicator: "green" });
			if (frm) frm.reload_doc();
			if (ctx.on_saved) ctx.on_saved();
		});
	});

	let mixAutosaveTimer = null;
	const scheduleMixAutosave = () => {
		if (readOnly || state.completed) return;
		if (mixAutosaveTimer) clearTimeout(mixAutosaveTimer);
		mixAutosaveTimer = setTimeout(() => {
			mixAutosaveTimer = null;
			collect_row_qtys(d, state);
			persist();
		}, 5000);
	};
	d.wrapper.on("input change", ".row-qty, .row-qty-extra, .row-mixing-type", scheduleMixAutosave);
	const prevOnhide = d.onhide;
	d.onhide = () => {
		if (mixAutosaveTimer) clearTimeout(mixAutosaveTimer);
		if (typeof prevOnhide === "function") prevOnhide();
	};
}

function save_raw_materials(d, ctx, state, frm) {
	const custom_unit = ctx.custom_unit;
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const uses_solvent = SOLVENT_MACHINES.includes(custom_unit);
	const v = d.get_values();

	let selected = {};
	if (is_printing) {
		if (!v.ink_item) {
			frappe.msgprint(__("Please select Ink before saving."));
			return;
		}
		selected = { Ink: v.ink_item };
		if (uses_solvent) {
			if (!v.ethyl_acetate_item || !v.toluene_item) {
				frappe.msgprint(__("Please select Ethyl Acetate and Toluene. Iso Butanol is optional."));
				return;
			}
			selected.EthylAcetate = v.ethyl_acetate_item;
			selected.Toluene = v.toluene_item;
			selected.IsoButanol = v.iso_butanol_item || "";
		}
	} else {
		if (!v.pp_item) {
			frappe.msgprint(__("Please select Polypropylene before saving. Other materials are optional."));
			return;
		}
		if (isLaminationMixingUnit(custom_unit)) {
			selected = {
				PP: v.pp_item,
				LD: v.ld_item || "",
				Masterbatch: v.masterbatch_item || "",
				Antistatic: v.antistatic_item || "",
				PPA: v.ppa_item || "",
			};
		} else {
			selected = {
				PP: v.pp_item,
				Filler: v.filler_item || "",
				Masterbatch: v.masterbatch_item || "",
				Antistatic: v.antistatic_item || "",
				PPA: v.ppa_item || "",
			};
		}
	}

	const apply_to_set = (index) => {
		state.sets[index].materials = selected;
		state.sets[index].rows = [];
		build_rows(state.sets[index], custom_unit);

		const fetches = is_printing
			? [
					frappe.db.get_value("Item", selected.Ink, "item_name"),
					uses_solvent ? frappe.db.get_value("Item", selected.EthylAcetate, "item_name") : null,
					uses_solvent ? frappe.db.get_value("Item", selected.Toluene, "item_name") : null,
					uses_solvent && selected.IsoButanol
						? frappe.db.get_value("Item", selected.IsoButanol, "item_name")
						: null,
				].filter(Boolean)
			: [
					fetch_item_name(selected.PP),
					fetch_item_name(selected.LD || selected.Filler),
					fetch_item_name(selected.Masterbatch),
					fetch_item_name(selected.Antistatic),
					fetch_item_name(selected.PPA),
				];

		Promise.all(fetches).then((results) => {
			if (is_printing) {
				state.sets[index].item_names = { Ink: results[0]?.message?.item_name || selected.Ink };
				if (uses_solvent) {
					let ri = 1;
					state.sets[index].item_names.EthylAcetate =
						results[ri]?.message?.item_name || selected.EthylAcetate;
					ri += 1;
					state.sets[index].item_names.Toluene = results[ri]?.message?.item_name || selected.Toluene;
					if (selected.IsoButanol) {
						ri += 1;
						state.sets[index].item_names.IsoButanol =
							results[ri]?.message?.item_name || selected.IsoButanol;
					}
				}
			} else {
				state.sets[index].item_names = {
					PP: results[0]?.message?.item_name || selected.PP,
					LD: selected.LD ? results[1]?.message?.item_name || selected.LD : "",
					Filler: selected.Filler ? results[1]?.message?.item_name || selected.Filler : "",
					Masterbatch: selected.Masterbatch
						? results[2]?.message?.item_name || selected.Masterbatch
						: "",
					Antistatic: selected.Antistatic
						? results[3]?.message?.item_name || selected.Antistatic
						: "",
					PPA: selected.PPA ? results[4]?.message?.item_name || selected.PPA : "",
				};
			}
			render_all(d, ctx, state, frm);
			frappe.call({
				method: `${MIXING_API}.save_shift_mixing_sheet`,
				args: {
					...mixing_api_args(ctx),
					mixing_sheet_json: JSON.stringify(state),
				},
				callback(r) {
					if (!r.exc) {
						if (r.message?.mixing_sheet_name) ctx.mixing_sheet_name = r.message.mixing_sheet_name;
						frappe.show_alert({
							message: __(`Mixing grid — Set ${index + 1} created.`),
							indicator: "green",
						});
					}
				},
			});
		});
	};

	let target_idx = state.sets.length - 1;
	for (let i = 0; i < state.sets.length; i++) {
		if (!set_has_materials(state.sets[i])) {
			target_idx = i;
			break;
		}
	}
	apply_to_set(target_idx);
}

function render_all(d, ctx, state, frm) {
	const $wrap = d.fields_dict.main_html.$wrapper;
	$wrap.empty();
	const custom_unit = ctx.custom_unit;
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const readOnly = !!(state.completed || ctx.read_only);

	state.sets.forEach((set, si) => {
		if (!set_has_materials(set)) {
			$wrap.append(render_empty_set_html(si, readOnly));
			return;
		}
		if (!set.rows.length) build_rows(set, custom_unit);
		$wrap.append(render_set_html(set, si, ctx, state, readOnly));
	});

	$wrap.find(".btn-consume").on("click", function () {
		consume_row(ctx, state, parseInt($(this).data("set"), 10), parseInt($(this).data("row"), 10), d, frm);
	});
	$wrap.find(".btn-add-row").on("click", function () {
		const si = parseInt($(this).data("set"), 10);
		state.sets[si].rows.push(make_empty_row(custom_unit));
		render_all(d, ctx, state, frm);
	});
	$wrap.find(".btn-copy-first-row").on("click", function () {
		const si = parseInt($(this).data("set"), 10);
		const times = parseInt($(this).data("times"), 10) || 1;
		copy_first_mixing_row(d, ctx, state, frm, si, times);
	});
	$wrap.find(".btn-del-row").on("click", function () {
		const si = parseInt($(this).data("set"), 10);
		const rows = state.sets[si].rows;
		if (!rows.length) return;
		collect_row_qtys(d, state);
		const removable = rows
			.map((row, ri) => ({ row, ri }))
			.filter(({ row }) => !row.consumed);
		if (!removable.length) {
			frappe.show_alert({ message: __("No unconsumed rows to remove."), indicator: "orange" });
			return;
		}
		if (removable.length === 1 && rows.length === 1) {
			frappe.show_alert({ message: __("At least one row required."), indicator: "orange" });
			return;
		}
		const choices = removable.map(({ ri }) => ({
			value: String(ri),
			label: __("Row {0}", [ri + 1]),
		}));
		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "row_choice",
					label: __("Row to remove"),
					options: choices.map((c) => c.label).join("\n"),
					reqd: 1,
					default: choices[choices.length - 1].label,
				},
			],
			(values) => {
				const choice = choices.find((c) => c.label === values.row_choice);
				const ri = choice ? parseInt(choice.value, 10) : -1;
				if (Number.isNaN(ri) || ri < 0 || ri >= rows.length) return;
				if (rows[ri]?.consumed) {
					frappe.show_alert({ message: __("Consumed rows cannot be removed."), indicator: "orange" });
					return;
				}
				if (rows.length <= 1) {
					frappe.show_alert({ message: __("At least one row required."), indicator: "orange" });
					return;
				}
				collect_row_qtys(d, state);
				rows.splice(ri, 1);
				render_all(d, ctx, state, frm);
				if (typeof d._mixPersist === "function") d._mixPersist();
			},
			__("Remove Row"),
			__("Remove")
		);
	});
	$wrap.find(".btn-del-row-one").on("click", function () {
		const si = parseInt($(this).data("set"), 10);
		const ri = parseInt($(this).data("row"), 10);
		const rows = state.sets[si].rows;
		if (!rows[ri] || rows[ri].consumed) return;
		if (rows.length <= 1) {
			frappe.show_alert({ message: __("At least one row required."), indicator: "orange" });
			return;
		}
		collect_row_qtys(d, state);
		rows.splice(ri, 1);
		render_all(d, ctx, state, frm);
		if (typeof d._mixPersist === "function") d._mixPersist();
	});
	$wrap.find(".btn-del-set").on("click", function () {
		delete_mixing_set(d, ctx, state, frm, parseInt($(this).data("set"), 10));
	});
}

function render_set_html(set, si, ctx, state, readOnly) {
	const custom_unit = ctx.custom_unit;
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const uses_solvent = SOLVENT_MACHINES.includes(custom_unit);
	const m = set.materials || {};
	const names = set.item_names || {};
	const label = mixing_set_label(si);

	let rows_html = "";
	set.rows.forEach((row, ri) => {
		const status_badge = row.consumed
			? `<span style="color:green;font-size:11px">✅ ${frappe.utils.escape_html(row.status || `${(row.consumed_by || "").split("@")[0]} @ ${(row.consumed_at || "").slice(11, 16)}`)}</span>`
			: readOnly
				? ""
				: `<button class="btn btn-xs btn-primary btn-consume" data-set="${si}" data-row="${ri}">${__("Consume")}</button>
				   <button class="btn btn-xs btn-default btn-del-row-one" data-set="${si}" data-row="${ri}" title="${__("Remove Row")}">×</button>`;

		const extras_cols = (set.extras || [])
			.map((ex) => {
				const val = (row.extras && row.extras[ex.item_code]) || 0;
				const disExtra = readOnly || row.consumed ? "disabled" : "";
				return `<td><input class="form-control form-control-sm row-qty-extra" data-set="${si}" data-row="${ri}" data-item="${ex.item_code}" value="${val}" style="width:70px;text-align:center" ${disExtra}></td>`;
			})
			.join("");

		const dis = readOnly || row.consumed ? "disabled" : "";
		const qtyCell = (field, value) =>
			`<td><input class="form-control form-control-sm row-qty" data-set="${si}" data-row="${ri}" data-field="${field}" value="${value || 0}" style="width:70px;text-align:center" ${dis}></td>`;
		const mixType = row.mixing_type || default_mixing_type(custom_unit);
		const typeSelect = is_printing
			? ""
			: `<td><select class="form-control form-control-sm row-mixing-type" data-set="${si}" data-row="${ri}" ${dis}>
					<option value="Full Mixing"${mixType === "Full Mixing" ? " selected" : ""}>${__("Full Mixing")}</option>
					<option value="Half Mixing"${mixType === "Half Mixing" ? " selected" : ""}>${__("Half Mixing")}</option>
				</select></td>`;
		if (is_printing) {
			const solvent_cols = uses_solvent
				? `
				<td><input class="form-control form-control-sm row-qty" data-set="${si}" data-row="${ri}" data-field="ea_qty" value="${row.ea_qty || 0}" style="width:70px;text-align:center" ${dis}></td>
				<td><input class="form-control form-control-sm row-qty" data-set="${si}" data-row="${ri}" data-field="tol_qty" value="${row.tol_qty || 0}" style="width:70px;text-align:center" ${dis}></td>
				<td><input class="form-control form-control-sm row-qty" data-set="${si}" data-row="${ri}" data-field="iso_qty" value="${row.iso_qty || 0}" style="width:70px;text-align:center" ${dis}></td>`
				: "";
			rows_html += `<tr style="${row.consumed ? "background:#f0fff0" : ""}">
				<td style="text-align:center;width:40px">${ri + 1}</td>
				<td><input class="form-control form-control-sm row-qty" data-set="${si}" data-row="${ri}" data-field="ink_qty" value="${row.ink_qty || 0}" style="width:70px;text-align:center" ${dis}></td>
				${extras_cols}${solvent_cols}
				<td style="text-align:center">${status_badge}</td>
			</tr>`;
		} else {
			const useLd = mixingUsesLdSlot(custom_unit, m);
			const gsm_cells = [
				has_material(m, "PP") ? qtyCell("pp_qty", row.pp_qty) : "",
				extras_cells_html(set, row, si, ri, dis, "PP"),
				useLd
					? has_material(m, "LD")
						? qtyCell("ld_qty", row.ld_qty || row.filler_qty)
						: ""
					: has_material(m, "Filler")
						? qtyCell("filler_qty", row.filler_qty)
						: "",
				extras_cells_html(set, row, si, ri, dis, useLd ? "LD" : "Filler"),
				has_material(m, "Masterbatch") ? qtyCell("mb_qty", row.mb_qty) : "",
				extras_cells_html(set, row, si, ri, dis, "Masterbatch"),
				has_material(m, "Antistatic") ? qtyCell("anti_qty", row.anti_qty) : "",
				extras_cells_html(set, row, si, ri, dis, "Antistatic"),
				has_material(m, "PPA") ? qtyCell("ppa_qty", row.ppa_qty) : "",
				extras_cells_html(set, row, si, ri, dis, "PPA"),
				extras_cells_html(set, row, si, ri, dis, "Other"),
			].join("");
			rows_html += `<tr style="${row.consumed ? "background:#f0fff0" : ""}">
				<td style="text-align:center;width:40px">${ri + 1}</td>
				${typeSelect}
				${gsm_cells}
				<td style="text-align:center">${status_badge}</td>
			</tr>`;
		}
	});

	let table = "";
	if (is_printing) {
		const solvent_headers = uses_solvent
			? `<th>${names.EthylAcetate || m.EthylAcetate || __("Ethyl Acetate")} (kg)</th>
			   <th>${names.Toluene || m.Toluene || __("Toluene")} (kg)</th>
			   <th>${names.IsoButanol || m.IsoButanol || __("Iso Butanol")} (kg)</th>`
			: "";
		table = `<table class="table table-bordered table-sm" style="margin-top:8px;font-size:13px">
			<thead><tr>
				<th>#</th><th>${names.Ink || m.Ink || __("BOPP Ink")} (kg)</th>
				${(set.extras || []).map((ex) => `<th>${ex.item_name} (kg)</th>`).join("")}
				${solvent_headers}<th>${__("Status")}</th>
			</tr></thead><tbody>${rows_html}</tbody></table>`;
	} else {
		const useLd = mixingUsesLdSlot(custom_unit, m);
		const gsm_headers = [
			has_material(m, "PP")
				? `<th>${frappe.utils.escape_html(names.PP || m.PP || "PP")} (kg)</th>`
				: "",
			extras_headers_html(set, "PP"),
			useLd
				? has_material(m, "LD")
					? `<th>${frappe.utils.escape_html(names.LD || m.LD || __("LD"))} (kg)</th>`
					: ""
				: has_material(m, "Filler")
					? `<th>${frappe.utils.escape_html(names.Filler || m.Filler || __("Filler"))} (kg)</th>`
					: "",
			extras_headers_html(set, useLd ? "LD" : "Filler"),
			has_material(m, "Masterbatch")
				? `<th>${frappe.utils.escape_html(names.Masterbatch || m.Masterbatch || __("Colour Masterbatch"))} (kg)</th>`
				: "",
			extras_headers_html(set, "Masterbatch"),
			has_material(m, "Antistatic")
				? `<th>${frappe.utils.escape_html(names.Antistatic || m.Antistatic || __("Antistatic"))} (kg)</th>`
				: "",
			extras_headers_html(set, "Antistatic"),
			has_material(m, "PPA")
				? `<th>${frappe.utils.escape_html(names.PPA || m.PPA || __("Modifier"))} (kg)</th>`
				: "",
			extras_headers_html(set, "PPA"),
			extras_headers_html(set, "Other"),
		].join("");
		table = `<table class="table table-bordered table-sm" style="margin-top:8px;font-size:13px">
			<thead><tr>
				<th>#</th>
				<th>${__("Mixing Type")}</th>
				${gsm_headers}
				<th>${__("Status")}</th>
			</tr></thead><tbody>${rows_html}</tbody></table>`;
	}

	const rowBtns = readOnly
		? ""
		: `<button class="btn btn-xs btn-default btn-add-row" data-set="${si}">➕ ${__("Add Row")}</button>
		   <button class="btn btn-xs btn-danger btn-del-row" data-set="${si}">🗑 ${__("Remove Row")}</button>`;
	const delSetBtn = readOnly
		? ""
		: `<button class="btn btn-xs btn-danger btn-del-set" data-set="${si}" style="margin-left:auto">🗑 ${__("Delete Set")}</button>`;
	const copyBtns = readOnly
		? ""
		: [1, 3]
				.map(
					(n) =>
						`<button type="button" class="btn btn-xs btn-default btn-copy-first-row" data-set="${si}" data-times="${n}">x${n}</button>`
				)
				.join("");

	return `
		<div style="margin-top:${si > 0 ? "24px" : "0"};padding:8px 0 4px;font-weight:600;color:#5e35b1;border-bottom:2px solid #ede7f6;display:flex;align-items:center;gap:8px">
			<span>🧪 ${__("Mixing Grid")} — ${label}</span>
			${copyBtns}
			${delSetBtn}
		</div>
		${table}
		<div style="display:flex;gap:8px;margin-bottom:4px;align-items:center">
			${rowBtns}
			<span style="margin-left:auto;font-size:12px;color:#888">
				${__("Rows")}: ${set.rows.length} | ${__("Consumed")}: ${set.rows.filter((r) => r.consumed).length}
			</span>
		</div>`;
}

function render_empty_set_html(si, readOnly) {
	const delSetBtn = readOnly
		? ""
		: `<button class="btn btn-xs btn-danger btn-del-set" data-set="${si}" style="margin-left:auto">🗑 ${__("Delete Set")}</button>`;
	return `
		<div style="margin-top:${si > 0 ? "24px" : "0"};padding:8px 0 4px;font-weight:600;color:#5e35b1;border-bottom:2px solid #ede7f6;display:flex;align-items:center;gap:8px">
			<span>🧪 ${__("Mixing Grid")} — ${mixing_set_label(si)}</span>
			${delSetBtn}
		</div>
		<div style="padding:30px;text-align:center;color:#888;font-style:italic;border:1px dashed #ccc;margin-top:8px;border-radius:4px;">
			${__("Select raw materials, then click <b>Save Raw Materials</b> to generate the grid.")}
		</div>`;
}

function delete_mixing_set(d, ctx, state, frm, si) {
	if (state.completed || ctx.read_only) return;
	const set = state.sets[si];
	if (!set) return;
	const consumed = (set.rows || []).filter((r) => r.consumed).length;
	const msg = consumed
		? __("Delete Mixing Grid — Set {0}? This set has {1} consumed row(s).", [si + 1, consumed])
		: __("Delete Mixing Grid — Set {0}? The entire table will be removed.", [si + 1]);
	frappe.confirm(msg, () => {
		collect_row_qtys(d, state);
		if (state.sets.length <= 1) {
			state.sets = [make_empty_set()];
		} else {
			state.sets.splice(si, 1);
		}
		render_all(d, ctx, state, frm);
		if (typeof d._mixPersist === "function") d._mixPersist();
		frappe.show_alert({ message: __("Mixing set deleted."), indicator: "orange" });
	});
}

function consume_row(ctx, state, si, ri, d, frm) {
	collect_row_qtys(d, state);
	frappe.confirm(__("Confirm consumption for Mixing #{0}?", [ri + 1]), () => {
		frappe.call({
			method: `${MIXING_API}.record_mixing_consumption`,
			args: {
				...mixing_api_args(ctx),
				set_index: si,
				row_index: ri,
				state_json: JSON.stringify(state),
			},
			callback(r) {
				if (!r.exc && r.message?.sets) {
					state.sets = r.message.sets;
					render_all(d, ctx, state, frm);
					frappe.show_alert({ message: __("Consumption recorded."), indicator: "green" });
				}
			},
		});
	});
}

function clone_unconsumed_mixing_row(row) {
	const copy = JSON.parse(JSON.stringify(row || {}));
	copy.consumed = false;
	copy.consumed_by = null;
	copy.consumed_at = null;
	return copy;
}

function copy_first_mixing_row(d, ctx, state, frm, si, times) {
	if (state.completed || ctx.read_only) return;
	collect_row_qtys(d, state);
	const set = state.sets[si];
	const source = set?.rows?.[0];
	if (!source) {
		frappe.show_alert({ message: __("This set has no first row to copy."), indicator: "orange" });
		return;
	}
	const n = Math.max(1, parseInt(times, 10) || 1);
	for (let i = 0; i < n; i++) {
		set.rows.push(clone_unconsumed_mixing_row(source));
	}
	render_all(d, ctx, state, frm);
	if (typeof d._mixPersist === "function") d._mixPersist();
	frappe.show_alert({
		message: __("Added {0} row(s) from the first row.", [n]),
		indicator: "green",
	});
}

function collect_row_qtys(d, state) {
	d.fields_dict.main_html.$wrapper.find(".row-qty").each(function () {
		const el = $(this);
		const si = parseInt(el.data("set"), 10);
		const ri = parseInt(el.data("row"), 10);
		const field = el.data("field");
		if (state.sets[si]?.rows[ri]) {
			state.sets[si].rows[ri][field] = parseFloat(el.val()) || 0;
		}
	});
	d.fields_dict.main_html.$wrapper.find(".row-qty-extra").each(function () {
		const el = $(this);
		const si = parseInt(el.data("set"), 10);
		const ri = parseInt(el.data("row"), 10);
		const item_code = el.data("item");
		if (state.sets[si]?.rows[ri]) {
			if (!state.sets[si].rows[ri].extras) state.sets[si].rows[ri].extras = {};
			state.sets[si].rows[ri].extras[item_code] = parseFloat(el.val()) || 0;
		}
	});
	d.fields_dict.main_html.$wrapper.find(".row-mixing-type").each(function () {
		const el = $(this);
		const si = parseInt(el.data("set"), 10);
		const ri = parseInt(el.data("row"), 10);
		if (state.sets[si]?.rows[ri]) {
			state.sets[si].rows[ri].mixing_type = el.val() || default_mixing_type("");
		}
	});
}

function print_mixing_sheet(state, ctx) {
	const custom_unit = ctx.custom_unit;
	const is_printing = PRINTING_MACHINES.includes(custom_unit);
	const uses_solvent = SOLVENT_MACHINES.includes(custom_unit);
	const title =
		ctx.title_label ||
		ctx.order_code ||
		ctx.spr_name ||
		`${ctx.run_date || ""} · ${ctx.shift || ""}`;

	const rows_html = (set) =>
		set.rows
			.map((r, i) => {
				const extras_cols = (set.extras || [])
					.map((ex) => `<td style="text-align:center">${(r.extras && r.extras[ex.item_code]) || 0}</td>`)
					.join("");
				if (is_printing) {
					const solvent_cols = uses_solvent
						? `<td>${r.ea_qty || 0}</td><td>${r.tol_qty || 0}</td><td>${r.iso_qty || 0}</td>`
						: "";
					return `<tr><td>${i + 1}</td><td>${r.ink_qty || 0}</td>${extras_cols}${solvent_cols}
						<td>${r.consumed ? "✅ " + (r.consumed_at || "").slice(11, 16) : ""}</td><td style="height:28px"></td></tr>`;
				}
				const m = set.materials || {};
				const useLd = mixingUsesLdSlot(custom_unit, m);
				const gsm_cells = [
					has_material(m, "PP") ? `<td>${r.pp_qty || 0}</td>` : "",
					extras_print_cells(set, r, "PP"),
					useLd
						? has_material(m, "LD")
							? `<td>${r.ld_qty || r.filler_qty || 0}</td>`
							: ""
						: has_material(m, "Filler")
							? `<td>${r.filler_qty || 0}</td>`
							: "",
					extras_print_cells(set, r, useLd ? "LD" : "Filler"),
					has_material(m, "Masterbatch") ? `<td>${r.mb_qty || 0}</td>` : "",
					extras_print_cells(set, r, "Masterbatch"),
					has_material(m, "Antistatic") ? `<td>${r.anti_qty || 0}</td>` : "",
					extras_print_cells(set, r, "Antistatic"),
					has_material(m, "PPA") ? `<td>${r.ppa_qty || 0}</td>` : "",
					extras_print_cells(set, r, "PPA"),
					extras_print_cells(set, r, "Other"),
				].join("");
				return `<tr><td>${i + 1}</td><td>${r.mixing_type || ""}</td>${gsm_cells}
					<td>${r.consumed ? "✅ " + (r.consumed_at || "").slice(11, 16) : ""}</td><td style="height:28px"></td></tr>`;
			})
			.join("");

	const sets_html = state.sets
		.map((set, si) => {
			const m = set.materials || {};
			const extras_headers = (set.extras || []).map((ex) => `<th>${ex.item_name} (kg)</th>`).join("");
			if (is_printing) {
				const solvent_headers = uses_solvent
					? "<th>Ethyl Acetate (kg)</th><th>Toluene (kg)</th><th>Iso Butanol (kg)</th>"
					: "";
				return `<h3>Raw Material Set ${si + 1}</h3><p>BOPP Ink: <b>${m.Ink || "-"}</b></p>
					<table border="1" cellpadding="6" style="width:100%;font-size:12px;border-collapse:collapse;text-align:center">
					<thead><tr><th>#</th><th>BOPP Ink (kg)</th>${extras_headers}${solvent_headers}<th>Time</th><th>Signature</th></tr></thead>
					<tbody>${rows_html(set)}</tbody></table>`;
			}
			const names = set.item_names || {};
			const summary = [
				m.PP ? `PP: <b>${m.PP}</b>` : "",
				m.LD ? `LD: <b>${m.LD}</b>` : "",
				m.Filler ? `Filler: <b>${m.Filler}</b>` : "",
				m.Masterbatch ? `MB: <b>${m.Masterbatch}</b>` : "",
				m.Antistatic ? `Anti: <b>${m.Antistatic}</b>` : "",
				m.PPA ? `Modifier: <b>${m.PPA}</b>` : "",
			]
				.filter(Boolean)
				.join(" | ");
			const gsm_headers = [
				has_material(m, "PP") ? `<th>${names.PP || m.PP || "PP"}</th>` : "",
				extras_print_headers(set, "PP"),
				mixingUsesLdSlot(custom_unit, m)
					? has_material(m, "LD")
						? `<th>${names.LD || m.LD || "LD"}</th>`
						: ""
					: has_material(m, "Filler")
						? `<th>${names.Filler || m.Filler || "Filler"}</th>`
						: "",
				extras_print_headers(set, mixingUsesLdSlot(custom_unit, m) ? "LD" : "Filler"),
				has_material(m, "Masterbatch") ? `<th>${names.Masterbatch || m.Masterbatch || "MB"}</th>` : "",
				extras_print_headers(set, "Masterbatch"),
				has_material(m, "Antistatic") ? `<th>${names.Antistatic || m.Antistatic || "Anti"}</th>` : "",
				extras_print_headers(set, "Antistatic"),
				has_material(m, "PPA") ? `<th>${names.PPA || m.PPA || "Modifier"}</th>` : "",
				extras_print_headers(set, "PPA"),
				extras_print_headers(set, "Other"),
			].join("");
			return `<h3>Raw Material Set ${si + 1}</h3>
				<p>${summary || "-"}</p>
				<table border="1" cellpadding="6" style="width:100%;font-size:12px;border-collapse:collapse">
				<thead><tr><th>#</th><th>Mixing Type</th>${gsm_headers}<th>Time</th><th>Signature</th></tr></thead>
				<tbody>${rows_html(set)}</tbody></table>`;
		})
		.join("");

	const win = window.open("", "_blank");
	win.document.write(`<!DOCTYPE html><html><head><title>Mixing Sheet — ${title}</title>
		<style>body{font-family:Arial,sans-serif;padding:20px}h2{text-align:center}</style></head><body>
		<h2>MIXING SHEET</h2>
		<p style="text-align:center"><b>${title}</b> | ${frappe.datetime.now_datetime()}</p>
		${sets_html}
		<script>window.print();</script></body></html>`);
	win.document.close();
}

frappe.provide("production_entry.spr_mixing_sheet");

production_entry.spr_mixing_sheet.openSprMixingSheet = openSprMixingSheet;
production_entry.spr_mixing_sheet.isMixingExcludedUnit = isMixingExcludedUnit;
production_entry.spr_mixing_sheet.EXCLUDED_MACHINES = EXCLUDED_MACHINES;

frappe.ui.form.on("Shaft Production Run", {
	refresh(frm) {
		if (!frm.doc.custom_unit || isMixingExcludedUnit(frm.doc.custom_unit)) return;
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Mixing Sheet"), () => open_mixing_sheet_desk(frm));
		}
	},
});
