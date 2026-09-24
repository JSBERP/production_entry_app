/* global frappe, __ */

const LOT_API = "production_entry.production_planning.lot_sample_api";

function _esc(s) {
	return frappe.utils.escape_html(String(s ?? ""));
}

function _args(ctx) {
	return {
		run_date: ctx.run_date || "",
		shift: ctx.shift || "",
		custom_unit: ctx.custom_unit || "",
		gsm_shift_session: ctx.gsm_shift_session || "",
		doc_name: ctx.doc_name || "",
		spr_names: ctx.spr_names || [],
		pp_ids: ctx.pp_ids || [],
	};
}

function _orderOpt(options, orderCode) {
	return (options.orders || []).find((o) => String(o.order_code) === String(orderCode)) || null;
}

function _qualities(opt) {
	return (opt && opt.qualities) || [];
}

function _colours(opt, quality) {
	const q = _qualities(opt).find((x) => String(x.quality) === String(quality));
	return (q && q.colours) || [];
}

function _gsms(opt, quality, colour) {
	const c = _colours(opt, quality).find((x) => String(x.colour || "") === String(colour || ""));
	return (c && c.gsms) || [];
}

function _selectHtml(value, items, placeholder, disabled) {
	const opts = [`<option value="">${_esc(placeholder)}</option>`]
		.concat(
			(items || []).map((it) => {
				const v = typeof it === "object" ? it.value : it;
				const l = typeof it === "object" ? it.label : it;
				const sel = String(v) === String(value || "") ? " selected" : "";
				return `<option value="${_esc(v)}"${sel}>${_esc(l)}</option>`;
			})
		);
	return `<select class="form-control input-sm" ${disabled ? "disabled" : ""}>${opts.join("")}</select>`;
}

function _tableHtml(rows, options) {
	const orders = (options.orders || []).map((o) => ({
		value: o.order_code,
		label: o.order_code,
	}));
	if (!rows.length) {
		rows.push({
			order_code: "",
			quality: "",
			colour: "",
			gsm: "",
			fabric_type: "",
			no_of_lot_sample: 1,
		});
	}
	const body = rows
		.map((row, i) => {
			const opt = _orderOpt(options, row.order_code);
			const qItems = _qualities(opt).map((q) => q.quality);
			const cItems = _colours(opt, row.quality).map((c) => ({
				value: c.colour,
				label: c.colour || "—",
			}));
			const gItems = _gsms(opt, row.quality, row.colour);
			let gsmCell;
			if (gItems.length > 1) {
				gsmCell = _selectHtml(row.gsm, gItems, __("GSM"), false);
			} else if (gItems.length === 1) {
				gsmCell = `<input class="form-control input-sm" type="number" readonly value="${_esc(row.gsm || gItems[0])}">`;
			} else {
				gsmCell = `<input class="form-control input-sm" type="number" min="0" step="1" value="${_esc(row.gsm || "")}">`;
			}
			return `<tr data-idx="${i}">
				<td class="ls-order">${_selectHtml(row.order_code, orders, __("Order Code"), !orders.length)}</td>
				<td class="ls-quality">${_selectHtml(row.quality, qItems, __("Quality"), !row.order_code)}</td>
				<td class="ls-colour">${_selectHtml(row.colour, cItems, __("Colour"), !row.quality)}</td>
				<td class="ls-gsm">${gsmCell}</td>
				<td class="ls-qty"><input class="form-control input-sm" type="number" min="1" step="1" value="${_esc(row.no_of_lot_sample || "")}"></td>
				<td class="ls-print"><button type="button" class="btn btn-xs btn-default ls-print-btn">${__("Print Label")}</button></td>
				<td class="ls-remove"><button type="button" class="btn btn-xs btn-danger ls-remove-btn" title="${__("Remove Row")}">×</button></td>
			</tr>`;
		})
		.join("");
	return `<style>
		.ls-wrap { display:flex; flex-direction:column; gap:12px; }
		.ls-table { margin:0; font-size:12px; }
		.ls-table th, .ls-table td { vertical-align:middle; }
		.ls-table select, .ls-table input { min-width: 90px; }
		.ls-empty { padding:8px; color:#64748b; }
		.ls-toolbar { display:flex; justify-content:flex-start; gap:8px; }
	</style>
	<div class="ls-wrap">
		${orders.length ? "" : `<div class="ls-empty">${__("No production orders are selected for this shift yet.")}</div>`}
		<table class="table table-bordered table-sm ls-table">
			<thead><tr>
				<th>${__("Order Code")}</th>
				<th>${__("Quality")}</th>
				<th>${__("Colour")}</th>
				<th>${__("GSM")}</th>
				<th>${__("No of Samples")}</th>
				<th></th>
				<th></th>
			</tr></thead>
			<tbody>${body}</tbody>
		</table>
		<div class="ls-toolbar">
			<button type="button" class="btn btn-xs btn-primary" id="ls-add-row">${__("Add Row")}</button>
			<button type="button" class="btn btn-xs btn-danger" id="ls-remove-row">${__("Remove Row")}</button>
		</div>
	</div>`;
}

function _readRows($wrap, options) {
	const rows = [];
	$wrap.find("tbody tr").each(function () {
		const $tr = $(this);
		const order_code = $tr.find(".ls-order select").val() || "";
		const quality = $tr.find(".ls-quality select").val() || "";
		const colour = $tr.find(".ls-colour select").val() || "";
		const gsmRaw = $tr.find(".ls-gsm select").length
			? $tr.find(".ls-gsm select").val()
			: $tr.find(".ls-gsm input").val();
		const opt = _orderOpt(options, order_code);
		rows.push({
			order_code,
			quality,
			colour,
			gsm: gsmRaw || "",
			fabric_type: (opt && opt.fabric_type) || "",
			pp_id: (opt && opt.pp_id) || "",
			spr_name: (opt && opt.spr_name) || "",
			no_of_lot_sample: parseInt($tr.find(".ls-qty input").val(), 10) || 0,
		});
	});
	return rows;
}

function _printHtml(html) {
	const w = window.open("", "_blank", "width=480,height=420");
	if (!w) {
		frappe.msgprint(__("Allow pop-ups to print the lot sample label."));
		return;
	}
	w.document.open();
	w.document.write(html);
	w.document.close();
	setTimeout(() => {
		w.focus();
		w.print();
	}, 300);
}

export async function openGsmLotSampleDialog(opts = {}) {
	const ctx = {
		run_date: opts.run_date || opts.runDate || "",
		shift: opts.shift || "",
		custom_unit: (opts.custom_unit || opts.headerUnit || "").trim(),
		gsm_shift_session: opts.gsm_shift_session || opts.shiftSessionId || "",
		doc_name: "",
		spr_names: (opts.sessionSprList || []).map((s) => s.spr_name).filter(Boolean),
		pp_ids: [
			...((opts.sessionSprList || []).map((s) => s.pp_id).filter(Boolean)),
			...((opts.selectedPpIds || []).filter(Boolean)),
		],
	};
	if (!ctx.custom_unit) {
		frappe.msgprint(__("Select a unit first."));
		return;
	}
	if (!ctx.run_date || !ctx.shift) {
		frappe.msgprint(__("Set Run Date and Shift first."));
		return;
	}

	const load = async () => {
		const res = await frappe.call({
			method: `${LOT_API}.get_shaft_lot_sample`,
			args: _args(ctx),
		});
		const msg = res.message || {};
		ctx.doc_name = msg.name || ctx.doc_name;
		return msg;
	};

	let payload = await load();
	let options = payload.options || { orders: [] };
	let rows = (payload.rows || []).map((r) => ({ ...r }));

	const d = new frappe.ui.Dialog({
		title: `${__("Lot Sample")} — ${ctx.run_date} · ${ctx.shift} · ${ctx.custom_unit}`,
		size: "extra-large",
		primary_action_label: __("Save"),
		primary_action: async () => {
			await saveRows();
			frappe.show_alert({ message: __("Lot sample saved"), indicator: "green" });
		},
	});

	const $body = $('<div class="ls-body"></div>').appendTo(d.body);

	const render = () => {
		$body.html(_tableHtml(rows, options));
		$body.find("#ls-add-row").on("click", () => {
			rows = _readRows($body, options);
			rows.push({
				order_code: "",
				quality: "",
				colour: "",
				gsm: "",
				fabric_type: "",
				no_of_lot_sample: 1,
			});
			render();
		});
		$body.find("#ls-remove-row").on("click", () => {
			rows = _readRows($body, options);
			if (rows.length <= 1) {
				rows = [
					{
						order_code: "",
						quality: "",
						colour: "",
						gsm: "",
						fabric_type: "",
						no_of_lot_sample: 1,
					},
				];
				render();
				return;
			}
			const choices = rows
				.map((r, i) => {
					const detail = [r.order_code, r.quality, r.colour, r.gsm].filter(Boolean).join(" · ") || __("empty");
					return { value: String(i), label: `${i + 1}. ${detail}` };
				});
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
					const idx = choice ? parseInt(choice.value, 10) : -1;
					if (Number.isNaN(idx) || idx < 0 || idx >= rows.length) return;
					rows.splice(idx, 1);
					if (!rows.length) {
						rows.push({
							order_code: "",
							quality: "",
							colour: "",
							gsm: "",
							fabric_type: "",
							no_of_lot_sample: 1,
						});
					}
					render();
				},
				__("Remove Row"),
				__("Remove")
			);
		});
		$body.find("tbody tr").each(function () {
			const $tr = $(this);
			const idx = Number($tr.attr("data-idx"));
			$tr.find(".ls-order select").on("change", function () {
				rows = _readRows($body, options);
				const row = rows[idx] || {};
				row.quality = "";
				row.colour = "";
				row.gsm = "";
				const opt = _orderOpt(options, row.order_code);
				row.fabric_type = (opt && opt.fabric_type) || "";
				row.pp_id = (opt && opt.pp_id) || "";
				row.spr_name = (opt && opt.spr_name) || "";
				// Auto-pick when only one quality / colour / gsm for this order
				const qs = _qualities(opt);
				if (qs.length === 1) {
					row.quality = qs[0].quality || "";
					const cols = _colours(opt, row.quality);
					if (cols.length === 1) {
						row.colour = cols[0].colour || "";
						const gsms = _gsms(opt, row.quality, row.colour);
						if (gsms.length === 1) {
							row.gsm = gsms[0];
						}
					}
				}
				rows[idx] = row;
				render();
			});
			$tr.find(".ls-quality select").on("change", function () {
				rows = _readRows($body, options);
				const row = rows[idx] || {};
				row.colour = "";
				row.gsm = "";
				const cols = _colours(_orderOpt(options, row.order_code), row.quality);
				if (cols.length === 1) {
					row.colour = cols[0].colour || "";
					const gsms = _gsms(_orderOpt(options, row.order_code), row.quality, row.colour);
					if (gsms.length === 1) {
						row.gsm = gsms[0];
					}
				}
				rows[idx] = row;
				render();
			});
			$tr.find(".ls-colour select").on("change", function () {
				rows = _readRows($body, options);
				const row = rows[idx] || {};
				const gsms = _gsms(_orderOpt(options, row.order_code), row.quality, row.colour);
				row.gsm = gsms.length === 1 ? gsms[0] : row.gsm && gsms.includes(Number(row.gsm)) ? row.gsm : "";
				rows[idx] = row;
				render();
			});
			$tr.find(".ls-remove-btn").on("click", () => {
				rows = _readRows($body, options);
				if (rows.length <= 1) {
					rows = [
						{
							order_code: "",
							quality: "",
							colour: "",
							gsm: "",
							fabric_type: "",
							no_of_lot_sample: 1,
						},
					];
				} else {
					rows.splice(idx, 1);
				}
				render();
			});
			$tr.find(".ls-print-btn").on("click", async () => {
				rows = _readRows($body, options);
				const row = rows[idx];
				if (!row || !row.order_code || !row.quality || !row.colour) {
					frappe.msgprint(__("Select order code, quality, and colour before printing."));
					return;
				}
				if (!row.no_of_lot_sample) {
					frappe.msgprint(__("Enter the number of samples."));
					return;
				}
				await saveRows();
				const saved = (payload.rows || [])[idx] || {};
				const res = await frappe.call({
					method: `${LOT_API}.get_lot_sample_label_html`,
					args: {
						..._args(ctx),
						row_name: saved.name || "",
						order_code: row.order_code,
						quality: row.quality,
						colour: row.colour,
						gsm: row.gsm,
						fabric_type: row.fabric_type,
						no_of_lot_sample: row.no_of_lot_sample,
					},
				});
				_printHtml((res.message || {}).html || "");
			});
		});
	};

	async function saveRows() {
		rows = _readRows($body, options);
		d.get_primary_btn().prop("disabled", true);
		try {
			const res = await frappe.call({
				method: `${LOT_API}.save_shaft_lot_sample`,
				args: {
					..._args(ctx),
					rows: JSON.stringify(rows.filter((r) => r.order_code)),
				},
			});
			payload = res.message || payload;
			ctx.doc_name = payload.name || ctx.doc_name;
			options = payload.options || options;
			rows = (payload.rows || []).map((r) => ({ ...r }));
			if (!rows.length) {
				rows.push({
					order_code: "",
					quality: "",
					colour: "",
					gsm: "",
					fabric_type: "",
					no_of_lot_sample: 1,
				});
			}
			render();
			return payload;
		} finally {
			d.get_primary_btn().prop("disabled", false);
		}
	}

	d.show();
	render();
}

frappe.provide("production_entry.gsm_lot_sample");
production_entry.gsm_lot_sample.openGsmLotSampleDialog = openGsmLotSampleDialog;
