/* global frappe, __ */

const KAPADA_API = "production_entry.production_planning.kapada_usage_api";

function _esc(s) {
	return frappe.utils.escape_html(String(s ?? ""));
}

function _num(v) {
	const n = parseFloat(v);
	return Number.isFinite(n) ? n : 0;
}

function _scanValue(data) {
	if (!data) return "";
	if (typeof data === "string" || typeof data === "number") return String(data).trim();
	if (Array.isArray(data)) {
		for (const item of data) {
			const val = _scanValue(item);
			if (val) return val;
		}
		return "";
	}
	for (const key of ["decodedText", "result", "text", "rawValue", "barcode", "value", "data"]) {
		if (data[key]) {
			const val = _scanValue(data[key]);
			if (val) return val;
		}
	}
	return "";
}

function _args(ctx) {
	return {
		run_date: ctx.run_date || "",
		shift: ctx.shift || "",
		custom_unit: ctx.custom_unit || "",
		gsm_shift_session: ctx.gsm_shift_session || "",
		doc_name: ctx.doc_name || "",
	};
}

function _savedHtml(rows) {
	if (!rows.length) {
		return `<p class="ku-empty">${__("No rolls recorded for this shift yet.")}</p>`;
	}
	const body = rows
		.map(
			(row, i) => `<tr>
			<td>${i + 1}</td>
			<td>${_esc(row.batch_no)}</td>
			<td>${_esc(row.quality)}</td>
			<td>${_esc(row.color)}</td>
			<td>${row.gsm ? _esc(row.gsm) : ""}</td>
			<td>${row.width ? _esc(row.width) : ""}</td>
			<td>${_num(row.used_qty).toFixed(3)}</td>
		</tr>`
		)
		.join("");
	return `<table class="table table-bordered table-sm ku-table">
		<thead><tr>
			<th>#</th>
			<th>${__("Batch No")}</th>
			<th>${__("Quality")}</th>
			<th>${__("Colour")}</th>
			<th>${__("GSM")}</th>
			<th>${__("Width")}</th>
			<th>${__("Used Qty")}</th>
		</tr></thead>
		<tbody>${body}</tbody>
	</table>`;
}

function _pendingHtml(rows) {
	if (!rows.length) {
		return `<p class="ku-empty">${__("Scan a roll to add it.")}</p>`;
	}
	const body = rows
		.map((row, i) => {
			const qty = _num(row.qty);
			const balance = row.balance_qty === "" || row.balance_qty == null ? "" : row.balance_qty;
			return `<tr data-idx="${i}">
			<td>${i + 1}</td>
			<td>${_esc(row.batch_no)}</td>
			<td>${_esc(row.quality)}</td>
			<td>${_esc(row.color)}</td>
			<td>${row.gsm ? _esc(row.gsm) : ""}</td>
			<td>${row.width ? _esc(row.width) : ""}</td>
			<td class="ku-qty">${qty.toFixed(3)}</td>
			<td><input class="form-control input-sm ku-balance" type="number" min="0" step="0.001" value="${_esc(balance)}"></td>
			<td><button type="button" class="btn btn-xs btn-danger ku-remove">×</button></td>
		</tr>`;
		})
		.join("");
	return `<table class="table table-bordered table-sm ku-table">
		<thead><tr>
			<th>#</th>
			<th>${__("Batch No")}</th>
			<th>${__("Quality")}</th>
			<th>${__("Colour")}</th>
			<th>${__("GSM")}</th>
			<th>${__("Width")}</th>
			<th>${__("Qty")}</th>
			<th>${__("Balance Qty")}</th>
			<th></th>
		</tr></thead>
		<tbody>${body}</tbody>
	</table>
	<p class="ku-note">${__("Save keeps these rolls here. At the end of the shift, enter Balance Qty and Save again. Enter 0 to use the full roll. Only the used weight is recorded.")}</p>`;
}

export async function openGsmKapadaUsageDialog(opts = {}) {
	const ctx = {
		run_date: opts.run_date || opts.runDate || "",
		shift: opts.shift || "",
		custom_unit: (opts.custom_unit || opts.headerUnit || "").trim(),
		gsm_shift_session: opts.gsm_shift_session || opts.shiftSessionId || "",
		doc_name: "",
	};
	if (!ctx.custom_unit) {
		frappe.msgprint(__("Select a unit first."));
		return;
	}
	if (!ctx.run_date || !ctx.shift) {
		frappe.msgprint(__("Set Run Date and Shift first."));
		return;
	}

	let saved = [];
	let pending = [];

	const load = async () => {
		const res = await frappe.call({
			method: `${KAPADA_API}.get_kapada_usage`,
			args: _args(ctx),
		});
		const msg = res.message || {};
		ctx.doc_name = msg.name || "";
		saved = msg.rows || [];
		pending = (msg.open_rows || []).map((row) => ({ ...row, balance_qty: "" }));
	};

	await load();

	const d = new frappe.ui.Dialog({
		title: `${__("Kapada Usage")} — ${ctx.run_date} · ${ctx.shift} · ${ctx.custom_unit}`,
		size: "extra-large",
		primary_action_label: __("Save"),
		primary_action: () => saveRows(),
	});

	const $body = $(`<div class="ku-wrap">
		<style>
			.ku-wrap { display:flex; flex-direction:column; gap:14px; }
			.ku-scan-row { display:flex; align-items:center; gap:8px; }
			.ku-scan { display:flex; align-items:center; flex:1; border:1px solid #e2e8f0; border-radius:12px; padding:8px 12px; background:#f8fafc; }
			.ku-scan input { border:0; background:transparent; width:100%; font-size:15px; outline:none; }
			.ku-open-scan { white-space:nowrap; }
			.ku-table { margin:0; font-size:12px; }
			.ku-table th, .ku-table td { vertical-align:middle; text-align:center; }
			.ku-empty, .ku-note { margin:0; color:#64748b; font-size:12px; }
			.ku-section h5 { margin:0 0 6px; font-size:13px; }
		</style>
		<div class="ku-scan-row">
			<label class="ku-scan">
				<input type="text" class="ku-barcode" placeholder="${__("Scan Barcode or enter batch no")}" autocomplete="off">
			</label>
			<button type="button" class="btn btn-default ku-open-scan"><i class="fa fa-barcode"></i> ${__("Scan")}</button>
		</div>
		<div class="ku-section">
			<h5>${__("This entry")}</h5>
			<div class="ku-pending"></div>
		</div>
		<div class="ku-section">
			<h5>${__("Recorded this shift")}</h5>
			<div class="ku-saved"></div>
		</div>
	</div>`).appendTo(d.body);

	function readPending() {
		$body.find(".ku-pending tbody tr").each(function () {
			const idx = Number($(this).attr("data-idx"));
			if (!pending[idx]) return;
			pending[idx].balance_qty = $(this).find(".ku-balance").val();
		});
	}

	function render() {
		readPending();
		$body.find(".ku-pending").html(_pendingHtml(pending));
		$body.find(".ku-saved").html(_savedHtml(saved));
	}

	async function addBatch(raw) {
		const batchNo = String(raw || "").trim();
		if (!batchNo) return;
		readPending();
		const known = pending.some((r) => r.batch_no === batchNo) || saved.some((r) => r.batch_no === batchNo);
		if (known) {
			frappe.msgprint(__("Roll {0} is already on this shift.", [batchNo]));
			return;
		}
		const res = await frappe.call({
			method: `${KAPADA_API}.lookup_kapada_roll`,
			args: { batch_no: batchNo },
			freeze: true,
			freeze_message: __("Fetching roll…"),
		});
		const roll = res.message || {};
		if (!roll.batch_no) return;
		if (pending.some((r) => r.batch_no === roll.batch_no) || saved.some((r) => r.batch_no === roll.batch_no)) {
			frappe.msgprint(__("Roll {0} is already on this shift.", [roll.batch_no]));
			return;
		}
		pending.push({
			batch_no: roll.batch_no,
			quality: roll.quality || "",
			color: roll.color || "",
			gsm: roll.gsm || "",
			width: roll.width || "",
			qty: roll.qty || 0,
			balance_qty: "",
		});
		render();
	}

	async function saveRows() {
		readPending();
		if (!pending.length) {
			frappe.msgprint(__("Scan a roll first."));
			return;
		}
		const rows = pending.map((row) => ({
			batch_no: row.batch_no,
			balance_qty: row.balance_qty === "" || row.balance_qty == null ? "" : row.balance_qty,
			quality: row.quality,
			color: row.color,
			gsm: row.gsm,
			width: row.width,
			qty: row.qty,
		}));
		const res = await frappe.call({
			method: `${KAPADA_API}.save_kapada_usage`,
			args: { ..._args(ctx), rows: JSON.stringify(rows) },
			freeze: true,
			freeze_message: __("Saving…"),
		});
		const msg = res.message || {};
		ctx.doc_name = msg.name || ctx.doc_name;
		saved = msg.rows || [];
		pending = (msg.open_rows || []).map((row) => ({ ...row, balance_qty: "" }));
		render();
		const issued = (msg.issued_now || []).map((q) => _num(q).toFixed(3)).join(", ");
		frappe.show_alert({
			message: issued
				? __("Material Issue {0} created for {1} kg.", [msg.stock_entry || "", issued])
				: __("Scanned rolls saved. Enter balance qty at the end of the shift."),
			indicator: "green",
		});
	}

	$body.on("keydown", ".ku-barcode", async (e) => {
		if (e.key !== "Enter") return;
		e.preventDefault();
		const value = $body.find(".ku-barcode").val();
		$body.find(".ku-barcode").val("");
		try {
			await addBatch(value);
		} catch (e) {
			console.error(e);
		}
	});
	$body.on("click", ".ku-open-scan", () => {
		const scanner = new frappe.ui.Scanner({
			dialog: true,
			multiple: false,
			on_scan(raw) {
				const value = _scanValue(raw);
				if (scanner.stop_scan) scanner.stop_scan();
				if (!value) return;
				addBatch(value).catch((e) => console.error(e));
			},
		});
	});
	$body.on("click", ".ku-remove", function () {
		readPending();
		const idx = Number($(this).closest("tr").attr("data-idx"));
		pending.splice(idx, 1);
		render();
	});

	render();
	d.show();
	setTimeout(() => $body.find(".ku-barcode").trigger("focus"), 200);
}
