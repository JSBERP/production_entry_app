/** GSM Production Entry — SPR Tools helpers (read-only: never creates SPR). */

import { openSprBundlePackagingDialog } from "./spr_bundle_packaging_dialog.js";
import { openSprManualJobDialog } from "./spr_manual_job_dialog.js";
import { openSprTrialOrderDialog } from "./spr_trial_order_dialog.js";
import "./spr_label.js";
import "./spr_quality_check.js";

async function loadSprDocForGsm(sprName) {
	if (production_entry.spr_label && typeof production_entry.spr_label.load_spr_doc === "function") {
		return production_entry.spr_label.load_spr_doc(sprName);
	}
	const res = await frappe.call({
		method: "production_entry.production_planning.unified_production_entry_api.get_gsm_spr_doc",
		args: { spr_name: sprName },
	});
	return res.message || null;
}

export async function findSprForGsm(ppId, preferDraft = true, session = {}) {
	session = session && typeof session === "object" ? session : {};
	const res = await frappe.call({
		method: "production_entry.production_planning.unified_production_entry_api.get_spr_for_pp",
		args: {
			pp_id: ppId,
			prefer_draft: preferDraft ? 1 : 0,
			unit: session.unit || undefined,
			run_date: session.runDate || session.run_date || undefined,
			shift: session.shift || undefined,
		},
	});
	return (res.message || {}).spr_name || null;
}

export function openSprForm(sprName) {
	if (!sprName) {
		return;
	}
	frappe.set_route("Form", "Shaft Production Run", sprName);
}

function noSprMessage() {
	frappe.msgprint(
		__(
			"No SPR found for this Production Plan. Use Create SPRs in GSM Production Entry, or create from Production Table."
		)
	);
}

export async function gsmOpenManualJob(ppId, _planningItemNames, unit, runDate, shift, onSuccess) {
	const sprName = await findSprForGsm(ppId, true, { unit, runDate, shift });
	if (!sprName) {
		noSprMessage();
		return;
	}
	openSprManualJobDialog({
		sprName,
		onSuccess: () => {
			if (typeof onSuccess === "function") {
				onSuccess(sprName);
			}
		},
	});
}

export async function gsmOpenTrailOrder(opts, onSuccess) {
	opts = opts && typeof opts === "object" ? opts : { ppId: opts };
	const unit = String(opts.unit || "").trim();
	if (!unit) {
		frappe.msgprint(__("Open the GSM shift (unit) first."));
		return;
	}
	openSprTrialOrderDialog({
		unit,
		runDate: opts.runDate,
		shift: opts.shift,
		operator: opts.operator,
		supervisor: opts.supervisor,
		onSuccess: (result) => {
			if (typeof onSuccess === "function") {
				onSuccess(result);
			}
			if (typeof opts.onSuccess === "function") {
				opts.onSuccess(result);
			}
		},
	});
}

/** Open Bundle packaging for GSM — creates fresh SPR rolls + one GSM summary row. */
export async function gsmOpenBundlePackaging(ppId, onSuccess, options = {}) {
	const sprName = await findSprForGsm(ppId, true);
	if (!sprName) {
		noSprMessage();
		return;
	}
	openSprBundlePackagingDialog({
		sprName,
		gsmMode: true,
		ppId,
		fallbackJob: options.fallbackJob || null,
		onSuccess: (result) => {
			if (typeof onSuccess === "function") {
				onSuccess(result, sprName);
			}
		},
	});
}

/** Toggle SPR custom_use_bundle_packaging_on_submit (Manufacture SE on submit). */
export async function gsmToggleBundleSeOnSubmit(ppId) {
	const sprName = await findSprForGsm(ppId, true);
	if (!sprName) {
		noSprMessage();
		return;
	}
	const doc = await loadSprDocForGsm(sprName);
	if (!doc) {
		noSprMessage();
		return;
	}
	const cur = cint(doc.custom_use_bundle_packaging_on_submit);
	const next = cur ? 0 : 1;
	await frappe.call({
		method:
			"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_set_bundle_packaging_on_submit",
		args: { shaft_production_run: sprName, enabled: next },
	});
	frappe.show_alert({
		message: next ? __("Bundle SE on Submit: ON") : __("Bundle SE on Submit: OFF"),
		indicator: "green",
	});
}

export async function gsmOpenRmBatches(ppId) {
	const sprName = await findSprForGsm(ppId, true);
	if (!sprName) {
		noSprMessage();
		return;
	}
	await openGsmRollInputsDialog(sprName);
}

function escapeHtml(s) {
	return frappe.utils.escape_html(String(s == null ? "" : s));
}

/** In-page Roll Inputs dialog (same as SPR Select RM batches) — does not open SPR form. */
export async function openGsmRollInputsDialog(sprName) {
	if (!sprName) {
		noSprMessage();
		return;
	}
	frappe.dom.freeze(__("Loading roll inputs..."));
	let ctx = {};
	try {
		const r = await frappe.call({
			method:
				"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_fabric_batch_pick_context",
			args: { spr_name: sprName },
		});
		ctx = r.message || {};
	} catch (e) {
		frappe.dom.unfreeze();
		frappe.msgprint(__("Could not load RM batches for this SPR."));
		return;
	}
	frappe.dom.unfreeze();

	if (!ctx.needs_picks) {
		frappe.msgprint(
			__(
				"No RM batch selection is required for this SPR yet. Save FG roll lines / ensure WO BOM has batch-tracked materials, then try again."
			)
		);
		return;
	}

	const picksByKey = {};
	(ctx.current_picks || []).forEach((p) => {
		const k = `${p.work_order || ""}|${p.item_code || ""}|${p.batch_no || ""}`;
		picksByKey[k] = flt(p.qty);
	});

	let bodyHtml = '<div class="spr-batch-dlg" style="max-height:460px;overflow:auto">';
	(ctx.lines || []).forEach((ln) => {
		bodyHtml +=
			`<h4 style="margin-top:0.75rem">${escapeHtml(ln.work_order || "")} — FG ${escapeHtml(ln.fg_item || "")}` +
			(ln.fg_process ? ` (${escapeHtml(String(ln.fg_process))})` : "") +
			` — ${__("SPR total")} ${escapeHtml(String(ln.total_fg_kg || ""))} Kg</h4>`;
		bodyHtml +=
			`<p class="text-muted small">${__("WIP warehouse")}: ${escapeHtml(ln.wip_warehouse || "")} — ${__(
				"Work In Progress batches only. Tick the roll you are loading now."
			)}</p>`;
		(ln.raw_materials || []).forEach((rm) => {
			const procTag = rm.process_code ? ` [${escapeHtml(String(rm.process_code))}]` : "";
			bodyHtml += `<h5 style="margin-top:0.5rem">${escapeHtml(rm.item_code || "")}${procTag} — ${escapeHtml(
				rm.item_name || ""
			)}</h5>`;
			bodyHtml +=
				`<p class="small">${__("Required")}: <b>${String(flt(rm.required_qty))}</b> Kg</p>` +
				`<table class="table table-bordered table-condensed"><thead><tr>` +
				`<th style="width:2rem">${__("Use")}</th><th>${__("Batch No")}</th><th>${__("Warehouse")}</th>` +
				`<th>${__("Avail (Kg)")}</th><th>${__("Use (Kg)")}</th></tr></thead><tbody>`;
			const batches = (rm.batches || []).filter((b) => {
				const bwh = String(b.warehouse || "");
				const wip = String(ln.wip_warehouse || "");
				if (wip && bwh === wip) return true;
				return /work\s*in\s*progress/i.test(bwh) || /^wip\b/i.test(bwh);
			});
			batches.forEach((b) => {
				const bn = String(b.batch_no || "");
				const key = `${ln.work_order || ""}|${rm.item_code || ""}|${bn}`;
				const defq = picksByKey[key] != null ? picksByKey[key] : "";
				const mx = Math.round(flt(b.qty) * 1000) / 1000;
				const hasPick = defq !== "" && flt(defq) > 0;
				bodyHtml +=
					`<tr data-wo="${escapeHtml(ln.work_order || "")}" data-item="${escapeHtml(rm.item_code || "")}" data-batch="${escapeHtml(bn)}">` +
					`<td><input type="checkbox" class="spr-bch-use"${hasPick ? " checked" : ""} /></td>` +
					`<td>${escapeHtml(bn)}</td><td>${escapeHtml(b.warehouse || "")}</td><td>${String(mx)}</td>` +
					`<td><input type="number" class="input-with-feedback form-control spr-bch-qty" step="0.001" min="0" data-max="${String(
						mx
					)}" value="${hasPick ? String(Math.round(flt(defq) * 1000) / 1000) : ""}" style="max-width:9rem" /></td></tr>`;
			});
			if (!batches.length) {
				bodyHtml += `<tr><td colspan="5">${__(
					"No batches transferred for this WO yet. Submit Material Transfer for Manufacture first."
				)}</td></tr>`;
			}
			bodyHtml += "</tbody></table>";
		});
	});
	bodyHtml += "</div>";

	const d = new frappe.ui.Dialog({
		title: __("Roll Inputs — Select RM batches"),
		fields: [{ fieldtype: "HTML", fieldname: "spr_batch_html" }],
		size: "extra-large",
		primary_action_label: __("Save picks"),
		primary_action() {
			const out = [];
			let qtyErr = "";
			d.$wrapper.find("tr[data-batch]").each(function () {
				const $tr = $(this);
				if (!$tr.find(".spr-bch-use").prop("checked")) return;
				const q = Math.round(flt($tr.find(".spr-bch-qty").val()) * 1000) / 1000;
				const mx = Math.round(flt($tr.find(".spr-bch-qty").attr("data-max")) * 1000) / 1000;
				if (q <= 0) return;
				let useQty = q;
				if (mx > 0 && useQty > mx) {
					if (useQty - mx <= 0.02) useQty = mx;
					else {
						qtyErr = __("Use quantity cannot exceed available stock for one of the selected batches.");
						return false;
					}
				}
				out.push({
					work_order: $tr.attr("data-wo"),
					item_code: $tr.attr("data-item"),
					batch_no: $tr.attr("data-batch"),
					qty: useQty,
				});
			});
			if (qtyErr) {
				frappe.msgprint(qtyErr);
				return;
			}
			frappe.call({
				method:
					"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_save_fabric_batch_picks",
				args: { spr_name: sprName, picks_json: JSON.stringify(out) },
				freeze: true,
				freeze_message: __("Saving..."),
				callback(r) {
					if (r.exc) {
						frappe.msgprint({ title: __("Save failed"), indicator: "red", message: r.exc });
						return;
					}
					d.hide();
					frappe.show_alert({
						message: __("Roll Inputs saved ({0} line(s)).", [
							(r.message && r.message.count) || out.length,
						]),
						indicator: "green",
					});
				},
			});
		},
	});
	d.fields_dict.spr_batch_html.$wrapper.html(bodyHtml);
	d.show();
}

/** Print production label — delegates to shared desk SPR label flow. */
export async function gsmPrintRollLabel(sprName, sprItemRowName, gridRow = null) {
	if (!sprName) {
		frappe.msgprint(__("Create SPRs first."));
		return;
	}
	if (!sprItemRowName) {
		frappe.msgprint(__("Save Row first to enable the label."));
		return;
	}
	if (gridRow && gridRow.produced_length_mtrs) {
		try {
			await frappe.call({
				method:
					"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_set_item_row_lock",
				args: {
					spr_name: sprName,
					row_name: sprItemRowName,
					locked: 1,
					produced_length_mtrs: gridRow.produced_length_mtrs,
					produced_gsm: gridRow.produced_gsm,
					gross_weight: gridRow.gross_weight,
					net_weight: gridRow.net_weight,
				},
			});
		} catch (e) {
			// print uses saved SPR row
		}
	}
	try {
		if (production_entry.spr_label && typeof production_entry.spr_label.print_roll === "function") {
			await production_entry.spr_label.print_roll(sprName, sprItemRowName);
			return;
		}
		frappe.msgprint(__("Label print helper not loaded."));
	} catch (e) {
		console.error("gsmPrintRollLabel", e);
		frappe.msgprint(__("Could not open label print."));
	}
}

/** Print bundle sticker label (Bundle Stickers row — not single-roll label). */
export async function gsmPrintBundleLabel(sprName, gridRow = null) {
	if (!sprName) {
		frappe.msgprint(__("Create SPRs first."));
		return;
	}
	if (!gridRow || !gridRow.batch_no) {
		frappe.msgprint(__("Bundle batch is missing."));
		return;
	}
	if (typeof frappe.generate_bundle_sticker_flow !== "function") {
		await import("./custom_print_sticker.js");
	}
	const doc = await loadSprDocForGsm(sprName);
	if (!doc) {
		frappe.msgprint(__("Could not load SPR for bundle label print."));
		return;
	}
	const bundleBatch = String(gridRow.batch_no || "").trim();
	let sticker = (doc.bundle_stickers || []).find((bs) => String(bs.batch_no || "").trim() === bundleBatch);
	if (!sticker && bundleBatch) {
		try {
			const res = await frappe.db.get_list("Bundle Stickers", {
				filters: { parent: sprName, batch_no: bundleBatch },
				fields: ["name", "batch_no", "combination", "roll_numbers", "rolls_per_bundle", "produced_length_mtrs", "sticker_width", "sticker_bundle_gross_weight_kg", "sticker_bundle_weight"],
				limit: 1,
			});
			sticker = res?.[0] || null;
		} catch (e) {
			sticker = null;
		}
	}
	const frm = { doc };
	if (typeof frappe.generate_bundle_sticker_flow === "function") {
		frappe.generate_bundle_sticker_flow(sticker, frm, gridRow);
		return;
	}
	frappe.msgprint(__("Bundle label print module could not be loaded."));
}

/** Print QC / approval label — same format as desk SPR approval label. */
export async function gsmPrintQcLabel(sprName, sprItemRowName, gridRow = null, extra = {}) {
	if (!sprName) {
		frappe.msgprint(__("Create SPRs first."));
		return;
	}
	const options = {
		operator: extra.operator || "",
		supervisor: extra.supervisor || "",
		batch_no: (gridRow && gridRow.batch_no) || extra.batch_no || "",
	};
	if (!sprItemRowName && !options.batch_no) {
		frappe.msgprint(__("Save Row first to enable the QC label."));
		return;
	}
	try {
		if (production_entry.spr_label && typeof production_entry.spr_label.print_qc === "function") {
			await production_entry.spr_label.print_qc(sprName, sprItemRowName, options);
			return;
		}
		frappe.msgprint(__("QC label print helper not loaded."));
	} catch (e) {
		console.error("gsmPrintQcLabel", e);
		frappe.msgprint(__("Could not open QC label print."));
	}
}

/** Print wastage label — shared desk SPR wastage print functions only. */
export async function gsmPrintWastageLabel(sprName, childRowName, tableField, rowData) {
	if (!production_entry.spr_label || typeof production_entry.spr_label.print_wastage !== "function") {
		frappe.msgprint(__("Wastage label print helper not loaded."));
		return;
	}
	await production_entry.spr_label.print_wastage(sprName, childRowName, tableField, rowData);
}

/** Start a Quality Checking form from GSM — uses the session SPR name when known. */
export async function gsmOpenQualityCheck({ sprName, ppId, kind, jobId, session } = {}) {
	let name = String(sprName || "").trim();
	if (!name && ppId) {
		name = await findSprForGsm(ppId, true, session || {});
	}
	if (!name) {
		noSprMessage();
		return;
	}
	const qc = production_entry.spr_quality_check;
	if (!qc) {
		frappe.msgprint(__("Quality Check helper not loaded."));
		return;
	}
	const k = String(kind || "round_gsm");
	try {
		if (k === "patty_gsm" && typeof qc.openSprPattyCuttingGsmTesting === "function") {
			await qc.openSprPattyCuttingGsmTesting(name, jobId);
			return;
		}
		if (k === "tensile" && typeof qc.openSprTensileTesting === "function") {
			await qc.openSprTensileTesting(name, jobId);
			return;
		}
		if (
			(k === "colour_spectrum" || k === "color_spectrum") &&
			typeof qc.openSprColourSpectrum === "function"
		) {
			await qc.openSprColourSpectrum(name, jobId);
			return;
		}
		if (typeof qc.openSprRoundCuttingGsmTesting === "function") {
			await qc.openSprRoundCuttingGsmTesting(name, jobId);
			return;
		}
		if (typeof qc.openSprGsmTesting === "function") {
			await qc.openSprGsmTesting(name, jobId);
			return;
		}
		frappe.msgprint(__("Quality Check helper not loaded."));
	} catch (e) {
		console.error("gsmOpenQualityCheck", e);
		frappe.msgprint(__("Could not open Quality Checking."));
	}
}

/** Start Round Cutting GSM Test — same redirect as desk SPR Quality Check. */
export async function gsmOpenGsmTesting(ppId, jobId, session) {
	return gsmOpenQualityCheck({ ppId, kind: "round_gsm", jobId, session });
}

/** Start Round Cutting GSM Test — same as SPR. */
export async function gsmOpenRoundCuttingGsmTesting(ppId, jobId, session) {
	return gsmOpenQualityCheck({ ppId, kind: "round_gsm", jobId, session });
}

/** Start Patty Cutting GSM Test — same as SPR. */
export async function gsmOpenPattyCuttingGsmTesting(ppId, jobId, session) {
	return gsmOpenQualityCheck({ ppId, kind: "patty_gsm", jobId, session });
}

/** Start Tensile Testing — same redirect as desk SPR Quality Check. */
export async function gsmOpenTensileTesting(ppId, jobId, session) {
	return gsmOpenQualityCheck({ ppId, kind: "tensile", jobId, session });
}

/** Start Colour Spectrum — opens Quality Checking with testing_type Colour Spectrum. */
export async function gsmOpenColourSpectrum(ppId, jobId, session) {
	return gsmOpenQualityCheck({ ppId, kind: "colour_spectrum", jobId, session });
}

/** Fix No. of Shaft = 0 on draft SPR roll lines. */
export async function gsmBackfillShaftNumbers(ppId) {
	const sprName = await findSprForGsm(ppId, true);
	if (!sprName) {
		noSprMessage();
		return null;
	}
	const res = await frappe.call({
		method:
			"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.backfill_spr_roll_shaft_numbers",
		args: { spr_name: sprName },
	});
	const fixed = cint(res?.message?.rows_fixed);
	frappe.show_alert({
		message: fixed
			? __("Fixed {0} roll row(s) on {1}", [fixed, sprName])
			: __("No shaft numbers needed fixing on {0}", [sprName]),
		indicator: fixed ? "green" : "blue",
	});
	return res.message || {};
}

function cint(v) {
	const n = parseInt(v, 10);
	return Number.isFinite(n) ? n : 0;
}
