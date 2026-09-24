/**
 * GSM Wastage + Recycle dialogs — uses shared SPR patty stock + label modules.
 */

import { gsmPrintWastageLabel } from "./spr_gsm_tools.js";
import "./spr_patty_stock.js";

const GWM_STYLE_ID = "gsm-wastage-recycle-styles";

const DESK_STOCK_COLS = [
	{ key: "batch_no", label: __("Batch No"), filter: true },
	{ key: "quality", label: __("Quality"), filter: true },
	{ key: "color", label: __("Color"), filter: true },
	{ key: "gsm", label: __("GSM"), filter: true, num: true },
	{ key: "width_inch", label: __("Width"), filter: true, num: true },
	{ key: "available_kg", label: __("Available (Kg)"), filter: false, num: true },
];

const DESK_RECYCLED_COLS = [
	{ field: "job_id", label: __("Job ID") },
	{ field: "quality", label: __("Quality") },
	{ field: "color", label: __("Color") },
	{ field: "gsm", label: __("GSM"), num: true },
	{ field: "width_inch", label: __("Width (Inch)"), num: true },
	{ field: "meter_per_roll", label: __("Meter / Roll"), num: true },
	{ field: "no_of_shafts", label: __("No of Shafts"), num: true },
	{ field: "wastage", label: __("Wastage"), num: true },
	{ field: "recycled_qty", label: __("Recycled Qty"), num: true },
	{ field: "recycled", label: __("Recycled"), num: true },
	{ field: "available_qty", label: __("Available Qty"), num: true },
];

const DESK_ROLL_WASTE_COLS = [
	{ field: "batch_no", label: __("Batch No") },
	{ field: "roll_number", label: __("Roll No"), num: true },
	{ field: "item_code", label: __("Item Code") },
	{ field: "item_name", label: __("Item Name") },
	{ field: "job_id", label: __("Job ID") },
	{ field: "quality", label: __("Quality") },
	{ field: "color", label: __("Color") },
	{ field: "gsm", label: __("GSM"), num: true },
	{ field: "width_inch", label: __("Width (Inch)"), num: true },
	{ field: "meter_per_roll", label: __("Meter / Roll"), num: true },
	{ field: "no_of_shafts", label: __("No of Shafts"), num: true },
	{ field: "wastage", label: __("Wastage"), num: true },
	{ field: "spr_item_name", label: __("SPR Item Row") },
	{ field: "source_roll", label: __("Source Roll") },
];

/** Fixed GSM Running Patty columns — do not mirror full SPR child DocType. */
const DESK_PATTY_COLS = [
	{ field: "batch_no", label: __("Batch No") },
	{ field: "job_id", label: __("Job ID") },
	{ field: "quality", label: __("Quality") },
	{ field: "color", label: __("Color") },
	{ field: "gsm", label: __("GSM"), num: true },
	{ field: "width_inch", label: __("Width"), num: true },
	{ field: "meter_per_roll", label: __("Meter / Roll"), num: true },
	{ field: "wastage", label: __("Wastage Qty"), num: true },
	{ field: "net_wastage", label: __("Net Wastage"), num: true },
	{ field: "recycle_to_next", label: __("Recycle to Next"), check: true },
];

function _flt(v) {
	return typeof flt === "function" ? flt(v) : parseFloat(v) || 0;
}

function _esc(s) {
	return frappe.utils.escape_html(String(s ?? ""));
}

function _injectGwmStyles() {
	if (document.getElementById(GWM_STYLE_ID)) {
		return;
	}
	const style = document.createElement("style");
	style.id = GWM_STYLE_ID;
	style.textContent = `
.gwm-shell { font-family: inherit; color: #1e293b; }
.gwm-section-title {
  margin: 0 0 10px;
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  letter-spacing: 0.02em;
}
.gwm-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
}
.gwm-actions .btn {
  border-radius: 8px;
  font-weight: 600;
  padding: 8px 14px;
}
.gwm-card {
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
  padding: 14px 16px;
  margin-bottom: 12px;
}
.gwm-card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  margin-top: 8px;
}
.gwm-data-card {
  background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
  border: 1px solid #cbd5e1;
  border-radius: 12px;
  padding: 12px 14px;
  box-shadow: 0 2px 8px rgba(15, 23, 42, 0.05);
}
.gwm-data-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid #e2e8f0;
}
.gwm-data-card-head strong {
  font-size: 13px;
  color: #0f172a;
}
.gwm-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  border: 1px solid #c7d2fe;
}
.gwm-kv-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 12px;
}
.gwm-kv {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.gwm-kv span {
  font-size: 10px;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.gwm-kv strong {
  font-size: 13px;
  color: #0f172a;
  word-break: break-word;
}
.gwm-kv.gwm-kv-wide { grid-column: 1 / -1; }
.gwm-recycle-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  cursor: pointer;
  user-select: none;
}
.gwm-recycle-label input {
  width: 16px;
  height: 16px;
  margin: 0;
  accent-color: #2563eb;
}
.gwm-card-foot {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed #e2e8f0;
  display: flex;
  justify-content: flex-end;
}
.gwm-table-wrap {
  overflow: auto;
  max-height: min(52vh, 420px);
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  background: #fff;
}
.gwm-desk-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12px;
  min-width: 720px;
}
.gwm-desk-table th,
.gwm-desk-table td {
  border-bottom: 1px solid #e2e8f0;
  border-right: 1px solid #f1f5f9;
  padding: 8px 10px;
  vertical-align: middle;
  text-align: left;
}
.gwm-desk-table th:last-child,
.gwm-desk-table td:last-child { border-right: none; }
.gwm-desk-table thead th {
  background: #f1f5f9;
  font-weight: 700;
  color: #334155;
  position: sticky;
  top: 0;
  z-index: 2;
}
.gwm-desk-table thead tr.gwm-filter-row th {
  top: 36px;
  background: #f8fafc;
  padding: 4px 6px;
  z-index: 1;
}
.gwm-desk-table tbody tr:hover { background: #f8fafc; }
.gwm-desk-table tbody tr.gwm-selected { background: #eef2ff; }
.gwm-desk-table .gwm-num { text-align: right; font-variant-numeric: tabular-nums; }
.gwm-desk-table .gwm-check { width: 36px; text-align: center; }
.gwm-desk-table th.gwm-print-col,
.gwm-desk-table td.gwm-print-col {
  width: 104px;
  min-width: 104px;
  max-width: 104px;
  text-align: center;
  white-space: nowrap;
  position: sticky;
  right: 0;
  background: #fff;
  box-shadow: -4px 0 8px rgba(15, 23, 42, 0.05);
  z-index: 1;
}
.gwm-desk-table thead th.gwm-print-col { background: #f1f5f9; z-index: 2; }
.gwm-print-btn {
  white-space: nowrap;
  max-width: 100%;
  padding: 4px 8px;
  font-size: 11px;
  border-radius: 6px !important;
  font-weight: 600 !important;
}
.gwm-filter-input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  padding: 4px 6px;
  font-size: 11px;
  background: #fff;
}
.gwm-empty {
  padding: 24px 16px;
  text-align: center;
  color: #64748b;
  font-size: 13px;
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  background: #f8fafc;
}
.gwm-warn {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #f59e0b;
  background: #fffbeb;
  color: #92400e;
  font-size: 13px;
  line-height: 1.45;
}
.gwm-warn p { margin: 0 0 6px; }
.gwm-warn p:last-child { margin-bottom: 0; }
`;
	document.head.appendChild(style);
}

function _val(row, ...keys) {
	for (const k of keys) {
		const v = row?.[k];
		if (v !== undefined && v !== null && String(v).trim() !== "") {
			return v;
		}
	}
	return "";
}

function _cint(v) {
	if (typeof cint === "function") {
		return cint(v) ? 1 : 0;
	}
	return v === true || v === 1 || v === "1" ? 1 : 0;
}

function _recycleNextCheckboxHtml(row, opts = {}) {
	const printKey = _printRowKey(row);
	const checked = _cint(row.recycle_to_next) ? " checked" : "";
	const label = opts.showLabel === false ? "" : `<span>${__("Recycle to Next")}</span>`;
	return `<label class="gwm-recycle-label">
		<input type="checkbox" class="gwm-recycle-next-cb"${checked}
			data-row="${_esc(printKey)}" data-job="${_esc(row.job_id || "")}" />
		${label}
	</label>`;
}

function _fmtNum(v, decimals = 3) {
	const n = _flt(v);
	if (!n && n !== 0) {
		return "";
	}
	return n.toFixed(decimals);
}

function _apiColsToDesk(apiCols, fallbackCols) {
	if (!Array.isArray(apiCols) || !apiCols.length) {
		return fallbackCols;
	}
	const numericTypes = new Set(["Float", "Int", "Currency"]);
	return apiCols
		.filter((c) => c.fieldname && !["name", "parent", "parentfield", "parenttype", "idx", "docstatus"].includes(c.fieldname))
		.map((c) => ({
			field: c.fieldname,
			label: c.label || c.fieldname,
			check: c.fieldtype === "Check" || c.fieldname === "recycle_to_next",
			num:
				c.fieldtype !== "Check" &&
				c.fieldname !== "recycle_to_next" &&
				(numericTypes.has(c.fieldtype) ||
					/width|meter|wastage|recycled|available|shaft|gsm|qty|kg/i.test(c.fieldname)),
		}));
}

function _cellValue(row, field) {
	const aliases = {
		batch_no: ["batch_no", "batch", "source_roll"],
		width_inch: ["width_inches", "width_inch", "width", "w", "custom_width_inch", "custom_width"],
		width: ["width", "width_inches", "width_inch", "w", "custom_width_inch", "custom_width"],
		meter_per_roll: [
			"meter__roll_mtrs",
			"meter_roll_mtrs",
			"meter__roll",
			"meter_per_roll",
			"meter_roll",
			"meter",
			"produced_length_mtrs",
			"produced_length_mtr",
		],
		wastage: ["wastage_qty_kgs", "wastage", "wastage_qty", "wastage_qt", "net_wastage"],
		wastage_qty: ["wastage_qty_kgs", "wastage_qty", "wastage_qt", "wastage"],
		net_wastage: ["net_wastage", "net_wastage_kg", "wastage_qty_kgs", "wastage_qty", "wastage"],
		recycled: ["recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"],
		recycled_qty: ["recycled_qty_kgs", "recycled_qty", "recycled", "recycled_kg"],
		available_qty: ["available_qty_kgs", "available_qty", "available", "available_kg"],
		available_kg: ["available_qty_kgs", "available_kg", "available", "available_qty"],
		roll_number: ["roll_number", "roll_no"],
	};
	return _val(row, ...(aliases[field] || [field]));
}

function _rollNoFromBatch(batchNo) {
	const bn = String(batchNo || "").trim();
	if (!bn || !bn.includes("/")) {
		return "";
	}
	const suffix = bn.split("/").pop();
	const n = parseInt(suffix, 10);
	return Number.isFinite(n) ? String(n) : "";
}

function _valQty(row, ...keys) {
	for (const k of keys) {
		const n = parseFloat(row?.[k]);
		if (Number.isFinite(n) && n > 0) {
			return n;
		}
	}
	return _val(row, ...keys);
}

function _looksLikeBatchNo(v) {
	const s = String(v || "").trim();
	if (!s) {
		return false;
	}
	if (s.includes("/")) {
		return true;
	}
	if (/^[a-z0-9]{8,12}$/.test(s)) {
		return false;
	}
	return s.length > 4;
}

function _normalizePattyRow(row) {
	const jobId = _val(row, "job_id", "job");
	const rawBatch = _val(row, "batch_no", "batch", "source_roll");
	const batchNo = _looksLikeBatchNo(rawBatch) ? rawBatch : "";
	const printKey = _val(row, "name") || (jobId ? `preview::${jobId}` : "") || batchNo;
	const normalized = {
		...row,
		name: printKey,
		batch_no: batchNo,
		roll_number: _val(row, "roll_number", "roll_no") || _rollNoFromBatch(batchNo),
		quality: _val(row, "quality"),
		color: _val(row, "color"),
		gsm: _val(row, "gsm"),
		width_inch: _val(row, "width_inch", "width", "w", "custom_width_inch", "custom_width"),
		width: _val(row, "width", "width_inch", "w", "custom_width_inch", "custom_width"),
		meter_per_roll: _val(
			row,
			"meter__roll_mtrs",
			"meter_roll_mtrs",
			"meter__roll",
			"meter_per_roll",
			"meter_roll",
			"meter",
			"produced_length_mtrs",
			"produced_length_mtr"
		),
		no_of_shafts: _val(row, "no_of_shafts", "shafts", "no_of_shaft"),
		wastage: _valQty(row, "wastage", "wastage_qty", "wastage_qt", "available", "available_qty", "available_kg", "net_weight", "gross_weight"),
		wastage_qty: _valQty(row, "wastage_qty", "wastage_qt", "wastage", "net_wastage", "net_weight"),
		net_wastage: _valQty(row, "net_wastage", "net_wastage_kg", "net_wastage_kgs", "wastage_qty", "wastage", "available", "available_qty", "net_weight"),
		recycled: _valQty(row, "recycled_qty", "recycled", "recycled_kg"),
		recycled_qty: _valQty(row, "recycled_qty", "recycled", "recycled_kg"),
		available: _valQty(row, "available", "available_qty", "available_kg", "wastage", "net_wastage"),
		available_kg: _valQty(row, "available_kg", "available", "available_qty", "wastage", "net_wastage"),
		spr_item_name: _val(row, "spr_item_name", "source_roll_waste_row"),
		source_roll: _val(row, "source_roll", "batch_no", "batch"),
		order_code: _val(row, "order_code", "party_code"),
		party_code: _val(row, "party_code", "order_code"),
		job_id: jobId,
		recycle_to_next: _cint(_val(row, "recycle_to_next", "custom_recycle_to_next")),
	};
	return normalized;
}

function _printRowKey(row) {
	return String(row?.name || row?.job_id || row?.batch_no || "").trim();
}

function _uniqueRollWasteRows(rows) {
	const seen = new Set();
	const out = [];
	for (const raw of rows || []) {
		const row = _normalizePattyRow(raw);
		const key = String(row.batch_no || row.source_roll || row.name || "").trim();
		if (key && seen.has(key)) {
			continue;
		}
		if (key) {
			seen.add(key);
		}
		out.push(row);
	}
	return out;
}

function _deskTableHtml(cols, rows, opts = {}) {
	const showPrint = !!opts.showPrint;
	const tableClass = opts.tableClass || "gwm-desk-table";
	let head = cols
		.map((c) => {
			const cls = c.num ? "gwm-num" : "";
			return `<th class="${cls}">${_esc(c.label)}</th>`;
		})
		.join("");
	if (showPrint) {
		head += `<th class="gwm-print-col">${__("Print")}</th>`;
	}
	let body = "";
	if (!rows.length) {
		body = `<tr><td colspan="${cols.length + (showPrint ? 1 : 0)}" class="gwm-empty">${__(
			"No rows"
		)}</td></tr>`;
	} else {
		body = rows
			.map((raw) => {
				const row = _normalizePattyRow(raw);
				const printKey = _printRowKey(row);
				const cells = cols
					.map((c) => {
						if (c.check || c.field === "recycle_to_next") {
							return `<td class="gwm-check">${_recycleNextCheckboxHtml(row, { showLabel: false })}</td>`;
						}
						let v = _cellValue(row, c.field);
						if (c.num) {
							v = _fmtNum(v, c.field === "gsm" || c.field === "no_of_shafts" ? 0 : 3);
						}
						const cls = c.num ? "gwm-num" : "";
						return `<td class="${cls}">${_esc(v)}</td>`;
					})
					.join("");
				const printBtn = showPrint && printKey
					? `<td class="gwm-print-col"><button type="button" class="btn btn-xs btn-default gwm-print-btn" data-row="${_esc(
							printKey
					  )}">${__("Print Label")}</button></td>`
					: showPrint
						? `<td class="gwm-print-col"></td>`
						: "";
				return `<tr data-row-name="${_esc(printKey)}">${cells}${printBtn}</tr>`;
			})
			.join("");
	}
	return `<div class="gwm-table-wrap"><table class="${tableClass}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function _dataCardsHtml(rows, opts = {}) {
	const kind = opts.kind || "waste";
	if (!rows.length) {
		return `<div class="gwm-empty">${opts.emptyText || __("No rows")}</div>`;
	}
	const cards = rows
		.map((raw) => {
			const row = _normalizePattyRow(raw);
			const printKey = _printRowKey(row);
			const title =
				kind === "roll"
					? _val(row, "batch_no") || __("Roll Waste")
					: `${__("Job")} ${_val(row, "job_id") || "—"}`;
			const badge =
				kind === "roll"
					? __("Roll Waste")
					: row.net_wastage
						? `${_fmtNum(row.net_wastage)} Kg`
						: __("Patty Wastage");
			const printAttr = printKey ? `data-row="${_esc(printKey)}"` : "";
			return `<div class="gwm-data-card">
				<div class="gwm-data-card-head">
					<strong>${_esc(title)}</strong>
					<span class="gwm-badge">${_esc(badge)}</span>
				</div>
				<div class="gwm-kv-grid">
					${
						kind === "patty"
							? `<div class="gwm-kv"><span>${__("Batch No")}</span><strong>${_esc(row.batch_no)}</strong></div>
					<div class="gwm-kv"><span>${__("Job ID")}</span><strong>${_esc(row.job_id)}</strong></div>`
							: ""
					}
					<div class="gwm-kv"><span>${__("Quality")}</span><strong>${_esc(row.quality)}</strong></div>
					<div class="gwm-kv"><span>${__("Color")}</span><strong>${_esc(row.color)}</strong></div>
					<div class="gwm-kv"><span>${__("GSM")}</span><strong>${_esc(row.gsm)}</strong></div>
					<div class="gwm-kv"><span>${__("Width")}</span><strong>${_esc(row.width_inch)}</strong></div>
					<div class="gwm-kv"><span>${__("Meter / Roll")}</span><strong>${_esc(row.meter_per_roll)}</strong></div>
					${
						kind === "patty"
							? ""
							: `<div class="gwm-kv"><span>${__("Shafts")}</span><strong>${_esc(row.no_of_shafts)}</strong></div>`
					}
					<div class="gwm-kv"><span>${__("Wastage Qty")}</span><strong>${_esc(_fmtNum(row.wastage))}</strong></div>
					<div class="gwm-kv"><span>${__("Net Wastage")}</span><strong>${_esc(_fmtNum(row.net_wastage || row.wastage))} Kg</strong></div>
					<div class="gwm-kv gwm-kv-wide">${_recycleNextCheckboxHtml(row)}</div>
					${
						kind !== "patty" && _cint(row.recycle_to_next) && Number(row.recycled) > 0
							? `<div class="gwm-kv"><span>${__("Recycled")}</span><strong>${_esc(_fmtNum(row.recycled))} Kg</strong></div>`
							: ""
					}
				</div>
				${
					opts.showPrint && printKey
						? `<div class="gwm-card-foot"><button type="button" class="btn btn-xs btn-default gwm-print-btn" ${printAttr}>${__(
								"Print Label"
						  )}</button></div>`
						: ""
				}
			</div>`;
		})
		.join("");
	return `<div class="gwm-card-grid">${cards}</div>`;
}

function _stockPickerHtml(stock) {
	_injectGwmStyles();
	if (!stock.length) {
		return `<div class="gwm-empty">${__("No patty stock available for this SPR.")}</div>`;
	}

	const headCells = DESK_STOCK_COLS.map(
		(c) => `<th class="${c.num ? "gwm-num" : ""}">${_esc(c.label)}</th>`
	).join("");
	const filterCells = DESK_STOCK_COLS.map((c, i) => {
		if (!c.filter) {
			return "<th></th>";
		}
		return `<th><input type="text" class="gwm-filter-input" data-filter-col="${i}" placeholder="${__(
			"Filter"
		)}" /></th>`;
	}).join("");

	const body = stock
		.map((raw, idx) => {
			const row = _normalizePattyRow(raw);
			const batch = row.batch_no || "";
			const key = row.name || batch || String(idx);
			const cells = DESK_STOCK_COLS.map((c) => {
				let v = row[c.key];
				if (c.key === "width_inch") {
					v = row.width_inch;
				}
				if (c.num) {
					v = c.key === "gsm" ? _fmtNum(v, 0) : _fmtNum(v, 3);
				}
				return `<td class="${c.num ? "gwm-num" : ""}">${_esc(v)}</td>`;
			}).join("");
			const searchParts = DESK_STOCK_COLS.map((c) => {
				if (c.key === "width_inch") {
					return String(row.width_inch || "");
				}
				if (c.key === "available_kg") {
					return String(row.available_kg || "");
				}
				return String(row[c.key] || "");
			});
			return `<tr class="gwm-stock-row" data-stock-key="${_esc(key)}" data-search="${_esc(
				searchParts.join("|")
			)}">
				<td class="gwm-check"><input type="checkbox" class="gwm-stock-cb" data-key="${_esc(key)}" /></td>
				${cells}
			</tr>`;
		})
		.join("");

	return `<div class="gwm-shell gwm-card">
		<div class="gwm-section-title">${__("Available Patty Stock")}</div>
		<div class="gwm-table-wrap gwm-desk-stock-wrap">
			<table class="gwm-desk-table gwm-desk-stock">
				<thead>
					<tr>
						<th class="gwm-check"><input type="checkbox" class="gwm-select-all" title="${__(
							"Select all"
						)}" /></th>
						${headCells}
					</tr>
					<tr class="gwm-filter-row">
						<th></th>
						${filterCells}
					</tr>
				</thead>
				<tbody>${body}</tbody>
			</table>
		</div>
	</div>`;
}

function _wireStockFilters($wrapper) {
	$wrapper.on("input", ".gwm-filter-input", function () {
		const filters = [];
		$wrapper.find(".gwm-filter-input").each(function () {
			filters.push({
				col: parseInt($(this).data("filter-col"), 10),
				val: String($(this).val() || "").trim().toLowerCase(),
			});
		});
		$wrapper.find(".gwm-stock-row").each(function () {
			const parts = String($(this).data("search") || "").split("|");
			let show = true;
			for (const f of filters) {
				if (!f.val && f.val !== "0") {
					continue;
				}
				const cell = String(parts[f.col] || "").toLowerCase();
				if (!cell.includes(f.val)) {
					show = false;
					break;
				}
			}
			$(this).toggle(show);
		});
	});
}

function _wireSelectAll($wrapper, rowCb, allCb) {
	$wrapper.on("change", allCb, function () {
		const on = $(this).prop("checked");
		$wrapper.find(rowCb).prop("checked", on);
	});
}

export function pickSessionSpr(sessionSprList, opts = {}) {
	_injectGwmStyles();
	const list = (sessionSprList || []).filter((s) => s && s.spr_name);
	if (!list.length) {
		frappe.msgprint(__("No SPR found for this shift."));
		return Promise.resolve(null);
	}
	const preferPpId = opts.pp_id || opts.ppId || "";
	const preferSpr = _cstr(opts.spr_name || opts.sprName);
	if (list.length === 1) {
		return Promise.resolve(list[0]);
	}
	return new Promise((resolve) => {
		const optionLines = list.map((s) => `${s.order_code || "—"} · ${s.spr_name}`);
		const options = optionLines.join("\n");
		let defaultIdx = 0;
		if (preferPpId) {
			const idx = list.findIndex((s) => s.pp_id === preferPpId);
			if (idx >= 0) {
				defaultIdx = idx;
			}
		} else if (preferSpr) {
			const idx = list.findIndex((s) => s.spr_name === preferSpr);
			if (idx >= 0) {
				defaultIdx = idx;
			}
		}
		const d = new frappe.ui.Dialog({
			title: __("Select Order / SPR"),
			fields: [
				{
					fieldname: "spr_pick",
					fieldtype: "Select",
					label: __("Order · SPR"),
					options,
					reqd: 1,
					default: optionLines[defaultIdx],
				},
			],
			primary_action_label: __("Continue"),
			primary_action() {
				const v = d.get_value("spr_pick");
				const idx = optionLines.indexOf(v);
				d.hide();
				resolve(idx >= 0 ? list[idx] : list[0]);
			},
		});
		d.show();
	});
}

function _cstr(v) {
	return String(v ?? "").trim();
}

async function _fetchWastageContext(sprName) {
	const res = await frappe.call({
		method: "production_entry.production_planning.unified_production_entry_api.get_gsm_spr_wastage_context",
		args: { spr_name: sprName },
	});
	return res.message || {};
}

function _bindGwmLiveRefresh(dialog, refreshFn, intervalMs = 60000) {
	let busy = false;
	const timer = setInterval(async () => {
		if (!dialog.$wrapper?.is(":visible") || busy) {
			return;
		}
		busy = true;
		try {
			await refreshFn(dialog);
		} catch (e) {
			console.warn("GWM live refresh", e);
		} finally {
			busy = false;
		}
	}, intervalMs);
	const prevOnhide = dialog.onhide;
	dialog.onhide = () => {
		clearInterval(timer);
		if (typeof prevOnhide === "function") {
			prevOnhide();
		}
	};
}

async function _renderPattyWastageView(sprName, opts = {}) {
	const ctx = await _fetchWastageContext(sprName);
	const table = _pattyWastageTable(ctx);
	const orderCode = _cstr(ctx.order_code);
	const realRollCount = Number(ctx.real_roll_count || 0) || 0;
	const warnings = Array.isArray(ctx.warnings) ? ctx.warnings.filter(Boolean) : [];
	const unsavedOnGsm = (opts.rollLines || []).filter(
		(r) =>
			r &&
			!r.is_wasted &&
			!r.is_bundle_row &&
			r.pp_id &&
			opts.sprRow &&
			r.pp_id === opts.sprRow.pp_id &&
			r.batch_no &&
			!(r.row_locked || r.spr_item_name)
	);
	if (unsavedOnGsm.length) {
		warnings.push(
			__(
				"{0} roll row(s) on GSM for this order are not Save Row'd yet — save them so rolls appear on the Shaft Production Run.",
				[unsavedOnGsm.length]
			)
		);
	}
	const rows = (table.rows || []).map((raw) => {
		const row = _normalizePattyRow(raw);
		if (!row.order_code && orderCode) {
			row.order_code = orderCode;
		}
		if (!row.party_code && orderCode) {
			row.party_code = orderCode;
		}
		return row;
	});
	const pattyCols = DESK_PATTY_COLS.slice();
	const isPreview =
		table.source === "gsm_preview_from_spr" || table.source === "gsm_preview_from_roll_lines";
	const fromJobsOnly = !!table.from_jobs_only;
	let hint = isPreview
		? __("Preview only — values are not the saved SPR table.")
		: __("Fetched from Shaft Production Run tables (Running Patty Wastage / Recycled Wastage Details).");
	if (fromJobsOnly || (isPreview && realRollCount <= 0)) {
		hint = __(
			"No saved Running Patty rows yet. Open the Shaft Production Run so wastage scripts can fill the table, then Refresh."
		);
	}
	const warnHtml = warnings.length
		? `<div class="gwm-warn">${warnings.map((w) => `<p>${_esc(String(w))}</p>`).join("")}</div>`
		: "";
	const emptyMsg =
		unsavedOnGsm.length > 0
			? __("No patty wastage yet. Save Row on the {0} unsaved GSM roll(s) for this order first.", [
					unsavedOnGsm.length,
			  ])
			: realRollCount <= 0
			  ? __("No patty wastage yet. This SPR has no produced rolls — Save Row on GSM first.")
			  : __("No patty wastage yet. Save roll rows on GSM first.");
	const content =
		rows.length > 0
			? `<div class="gwm-shell">
				${warnHtml}
				<div class="gwm-card">
					<p style="margin:0 0 10px;color:#64748b;font-size:13px">${hint}</p>
					<div class="gwm-section-title">${__("Running Patty Wastage")}</div>
					${_dataCardsHtml(rows, { kind: "patty", showPrint: true })}
				</div>
				<div class="gwm-card" style="margin-top:12px;">
					<div class="gwm-section-title">${__("Table View")}</div>
					${_deskTableHtml(pattyCols, rows, { showPrint: true })}
				</div>
			</div>`
			: `<div class="gwm-shell">${warnHtml}<div class="gwm-empty">${emptyMsg}</div></div>`;
	return { content, table, rows };
}

function _bestChildTable(ctx, preferredKey, matchRe, skipKeys = []) {
	const tables = ctx?.tables || {};
	const candidates = [];
	const direct = tables[preferredKey];
	if (direct) {
		candidates.push(direct);
	}
	for (const [key, table] of Object.entries(tables)) {
		if (!table || skipKeys.includes(key)) {
			continue;
		}
		if (matchRe.test(key) || matchRe.test(table.child_doctype || "")) {
			candidates.push(table);
		}
	}
	return candidates.reduce(
		(best, table) => ((table?.rows || []).length > (best?.rows || []).length ? table : best),
		direct || { rows: [], columns: [] }
	);
}

function _pattyWastageTable(ctx) {
	const tables = ctx?.tables || {};
	const direct = tables.custom_running_patty_wastage;
	if ((direct?.rows || []).length) {
		return direct;
	}
	return _bestChildTable(
		ctx,
		"custom_running_patty_wastage",
		/patty/i,
		["custom_roll_waste", "custom_recycled_wastage_details"]
	);
}

function _rollWasteTable(ctx) {
	return _bestChildTable(
		ctx,
		"custom_roll_waste",
		/roll.?waste/i,
		["custom_running_patty_wastage", "custom_recycled_wastage_details"]
	);
}

function _rowDataFromPrintBtn($btn, rows) {
	const rowName = String($btn.attr("data-row") || "").trim();
	if (!rowName) {
		return null;
	}
	return (
		(rows || []).find((r) => r && String(r.name || "").trim() === rowName) ||
		(rows || []).find((r) => r && `preview::${String(r.job_id || "").trim()}` === rowName) ||
		(rows || []).find((r) => r && String(r.job_id || "").trim() === rowName) ||
		(rows || []).find((r) => r && String(r.batch_no || "").trim() === rowName) ||
		null
	);
}

async function _bindRecycleToNext($wrapper, sprName, rows, onSaved) {
	$wrapper.off("change.gwmRecycle").on("change.gwmRecycle", ".gwm-recycle-next-cb", async function () {
		const $cb = $(this);
		const checked = $cb.is(":checked") ? 1 : 0;
		$cb.prop("disabled", true);
		try {
			await frappe.call({
				method: "production_entry.production_planning.unified_production_entry_api.set_gsm_patty_recycle_to_next",
				args: {
					spr_name: sprName,
					row_name: String($cb.attr("data-row") || "").trim(),
					job_id: String($cb.attr("data-job") || "").trim(),
					recycle_to_next: checked,
				},
			});
			frappe.show_alert({
				message: checked ? __("Recycle to Next saved on SPR") : __("Recycle to Next cleared on SPR"),
				indicator: "green",
			});
			if (typeof onSaved === "function") {
				await onSaved();
			}
		} catch (e) {
			$cb.prop("checked", !checked);
			frappe.msgprint({
				title: __("Could not save Recycle to Next"),
				message: e?.message || e,
				indicator: "red",
			});
		} finally {
			$cb.prop("disabled", false);
		}
	});
}

async function _bindWastagePrint($wrapper, sprName, tableField, rows) {
	$wrapper.off("click.gwmPrint").on("click.gwmPrint", ".gwm-print-btn", async function () {
		const $btn = $(this);
		const rowData = _rowDataFromPrintBtn($btn, rows);
		await gsmPrintWastageLabel(
			sprName,
			String($btn.attr("data-row") || "").trim(),
			tableField,
			rowData
		);
	});
}

function _rollsForSpr(rollLines, sprRow) {
	const ppId = sprRow.pp_id;
	const seen = new Set();
	const out = [];
	for (const r of rollLines || []) {
		if (
			r.pp_id !== ppId ||
			r.is_wasted ||
			r.is_bundle_row ||
			!r.batch_no ||
			!(r.row_locked || r.spr_item_name)
		) {
			continue;
		}
		const key = String(r.batch_no || "").trim();
		if (key && seen.has(key)) {
			continue;
		}
		if (key) {
			seen.add(key);
		}
		out.push(r);
	}
	return out;
}

export async function openGsmWastageDialog(opts = {}) {
	_injectGwmStyles();
	const sprRow = await pickSessionSpr(opts.sessionSprList, opts);
	if (!sprRow) {
		return;
	}
	await _openRollWastage(sprRow.spr_name, sprRow, opts);
}

function _otherWastageTable(ctx) {
	const tables = (ctx && ctx.tables) || {};
	return (
		tables.custom_other_wastages || {
			rows: [],
			columns: [],
			resolved_fieldname: "custom_other_wastages",
			configured: false,
		}
	);
}

function _otherWastageEditorHtml(rows) {
	const list = Array.isArray(rows) ? rows : [];
	if (!list.length) {
		return `<div class="gwm-empty">${__(
			"No Other Wastages rows on this SPR. Add rows on Shaft Production Run first."
		)}</div>`;
	}
	const body = list
		.map((r) => {
			const name = _esc(r.name || "");
			const qty = _flt(r.quantity);
			return `<tr data-row="${name}">
				<td>${_esc(r.item_code || "")}</td>
				<td>${_esc(r.item_name || "")}</td>
				<td class="gwm-num">
					<input type="number" step="0.001" min="0" class="gwm-other-qty form-control"
						data-row="${name}" value="${qty}" style="width:110px;text-align:right;" />
				</td>
				<td>${_esc(r.uom || "Kg")}</td>
			</tr>`;
		})
		.join("");
	return `<div class="gwm-table-wrap">
		<table class="gwm-desk-table">
			<thead><tr>
				<th>${__("Item Code")}</th>
				<th>${__("Item Name")}</th>
				<th class="gwm-num">${__("Wastage Quantity")}</th>
				<th>${__("UOM")}</th>
			</tr></thead>
			<tbody>${body}</tbody>
		</table>
	</div>
	<p style="margin:10px 0 0;color:#64748b;font-size:12px;">${__(
		"Enter quantity only. Changes save to Shaft Production Run → Other Wastages."
	)}</p>`;
}

async function _openOtherWastages(sprName, sprRow, opts = {}) {
	const ctx = await _fetchWastageContext(sprName);
	const table = _otherWastageTable(ctx);
	let rows = Array.isArray(table.rows) ? table.rows.slice() : [];

	const d = new frappe.ui.Dialog({
		title: __("Other Waste") + ` · ${sprRow.order_code || ""} · ${sprName}`,
		size: "large",
		fields: [
			{
				fieldname: "grid_html",
				fieldtype: "HTML",
				options: `<div class="gwm-shell"><div class="gwm-card">
					<div class="gwm-section-title">${__("Other Wastages")}</div>
					<div class="gwm-other-body">${_otherWastageEditorHtml(rows)}</div>
				</div></div>`,
			},
		],
		primary_action_label: __("Save Quantities"),
		async primary_action() {
			const updates = [];
			d.$wrapper.find(".gwm-other-qty").each(function () {
				const rn = String($(this).data("row") || "").trim();
				if (!rn) {
					return;
				}
				updates.push({
					row_name: rn,
					quantity: _flt($(this).val()),
				});
			});
			if (!updates.length) {
				frappe.msgprint(__("No other wastage rows to save."));
				return;
			}
			d.get_primary_btn().prop("disabled", true);
			try {
				const r = await frappe.call({
					method:
						"production_entry.production_planning.unified_production_entry_api.save_gsm_other_wastages",
					args: {
						spr_name: sprName,
						updates_json: JSON.stringify(updates),
					},
				});
				const msg = r.message || {};
				frappe.show_alert({
					message: __("Saved {0} other wastage qty on {1}", [
						Number(msg.updated || 0),
						sprName,
					]),
					indicator: "green",
				});
				rows = Array.isArray(msg.rows) ? msg.rows : rows;
				d.$wrapper.find(".gwm-other-body").html(_otherWastageEditorHtml(rows));
			} catch (e) {
				frappe.msgprint(e.message || __("Failed to save other wastages"));
			} finally {
				d.get_primary_btn().prop("disabled", false);
			}
		},
		secondary_action_label: __("Back to Roll Waste"),
		secondary_action() {
			d.hide();
			_openRollWastage(sprName, sprRow, opts);
		},
	});
	d.show();
}

async function _openRunningPattyWastage(sprName, sprRow, opts = {}) {
	const viewOpts = { sprRow, rollLines: opts.rollLines || [] };
	const initial = await _renderPattyWastageView(sprName, viewOpts);
	const d = new frappe.ui.Dialog({
		title: __("Running Patty Wasteage") + ` · ${sprRow.order_code || ""}`,
		size: "extra-large",
		fields: [{ fieldname: "grid_html", fieldtype: "HTML", options: initial.content }],
		primary_action_label: __("Refresh"),
		primary_action() {
			d.hide();
			_openRunningPattyWastage(sprName, sprRow, opts);
		},
	});
	d.show();

	const paint = async (view) => {
		d.fields_dict.grid_html.$wrapper.html(view.content);
		_bindWastagePrint(
			d.$wrapper,
			sprName,
			view.table.resolved_fieldname || "custom_running_patty_wastage",
			view.rows
		);
		_bindRecycleToNext(d.$wrapper, sprName, view.rows, async () => {
			const next = await _renderPattyWastageView(sprName, viewOpts);
			await paint(next);
		});
	};
	await paint(initial);
	_bindGwmLiveRefresh(d, async () => {
		if (d.$wrapper.find(".gwm-recycle-next-cb:disabled").length) {
			return;
		}
		const view = await _renderPattyWastageView(sprName, viewOpts);
		await paint(view);
	});
}

async function _openRollWastage(sprName, sprRow, opts) {
	const ctx = await _fetchWastageContext(sprName);
	const wasteTable = _rollWasteTable(ctx);
	const wasteRows = _uniqueRollWasteRows(wasteTable.rows || []);
	const rollWasteCols = _apiColsToDesk(wasteTable.columns, DESK_ROLL_WASTE_COLS);
	const rolls = _rollsForSpr(opts.rollLines, sprRow);
	const selectRollHtml = rolls.length
		? `<div class="gwm-card">
			<div class="gwm-section-title">${__("Select rolls to mark as waste")}</div>
			<div class="gwm-table-wrap">
				<table class="gwm-desk-table">
					<thead><tr>
						<th class="gwm-check"><input type="checkbox" class="gwm-roll-all" /></th>
						<th>${__("Batch")}</th><th>${__("Job")}</th><th>${__("Quality")}</th>
						<th>${__("GSM")}</th><th class="gwm-num">${__("Width")}</th><th class="gwm-num">${__("Net Kg")}</th>
					</tr></thead>
					<tbody>${rolls
						.map(
							(r) => `<tr>
						<td class="gwm-check"><input type="checkbox" class="gwm-roll-cb" data-batch="${_esc(
							r.batch_no
						)}" data-row="${_esc(r.spr_item_name || "")}" /></td>
						<td>${_esc(r.batch_no)}</td><td>${_esc(r.job_id || r.job || "")}</td>
						<td>${_esc(r.quality || "")}</td><td>${_esc(r.gsm || "")}</td>
						<td class="gwm-num">${_esc(r.width_inch || "")}</td><td class="gwm-num">${_flt(r.net_weight).toFixed(3)}</td>
					</tr>`
						)
						.join("")}</tbody>
				</table>
			</div>
		</div>`
		: `<div class="gwm-card"><div class="gwm-empty">${__(
				"No active saved roll lines available to mark as waste."
		  )}</div></div>`;
	const existingWasteHtml = `<div class="gwm-roll-waste-existing">
	<div class="gwm-card" style="margin-top:12px;">
		<div class="gwm-section-title">${__("Already Marked Roll Waste")}</div>
		${_dataCardsHtml(wasteRows, { kind: "roll", showPrint: true })}
	</div>
	<div class="gwm-card" style="margin-top:12px;">
		<div class="gwm-section-title">${__("Roll Waste Table")}</div>
		${_deskTableHtml(rollWasteCols, wasteRows, { showPrint: true })}
	</div>
	</div>`;
	const lamMode = !!opts.laminationMode;
	const rollHtml = `<div class="gwm-shell"><p style="margin:0 0 12px;color:#64748b;font-size:13px">${__(
		lamMode
			? "Roll waste and Other Waste save to Shaft Production Run. Running Patty / Recycle are not used for lamination."
			: "Roll waste saves to SPR immediately. Recycle uses saved patty / roll waste rows."
	)}</p>
	${
		lamMode
			? `<div class="gwm-actions" style="margin-bottom:12px;">
			<button type="button" class="btn btn-default gwm-btn-other-waste">${__("Other Waste")}</button>
		</div>`
			: ""
	}
	${selectRollHtml}${existingWasteHtml}</div>`;

	const dialogOpts = {
		title: __("Roll Wasteage") + ` · ${sprRow.order_code || ""}`,
		size: "extra-large",
		fields: [{ fieldname: "rolls_html", fieldtype: "HTML", options: rollHtml }],
		primary_action_label: rolls.length ? __("Mark as Waste") : __("Refresh"),
		async primary_action() {
			if (!rolls.length) {
				d.hide();
				_openRollWastage(sprName, sprRow, opts);
				return;
			}
			const selected = [];
			const seenSel = new Set();
			d.$wrapper.find(".gwm-roll-cb:checked").each(function () {
				const batch = String($(this).data("batch") || "").trim();
				const rowName = String($(this).data("row") || "").trim();
				const key = batch || rowName;
				if (key && seenSel.has(key)) {
					return;
				}
				if (key) {
					seenSel.add(key);
				}
				selected.push({
					batch_no: batch,
					row_name: rowName,
				});
			});
			if (!selected.length) {
				frappe.msgprint(__("Select at least one roll."));
				return;
			}
			d.get_primary_btn().prop("disabled", true);
			try {
				for (const sel of selected) {
					const roll = rolls.find(
						(r) => r.batch_no === sel.batch_no || r.spr_item_name === sel.row_name
					);
					await frappe.call({
						method:
							"production_entry.production_planning.unified_production_entry_api.mark_gsm_roll_waste",
						args: {
							spr_name: sprName,
							roll_payload: JSON.stringify(roll || sel),
							batch_no: sel.batch_no,
							row_name: sel.row_name,
						},
					});
					if (typeof opts.onRollWasted === "function") {
						opts.onRollWasted(roll || sel, sprRow);
					}
				}
				frappe.show_alert({ message: __("Roll(s) marked as waste"), indicator: "green" });
				d.hide();
				_openRollWastage(sprName, sprRow, opts);
			} catch (e) {
				frappe.msgprint(e.message || __("Failed to mark roll waste"));
			} finally {
				d.get_primary_btn().prop("disabled", false);
			}
		},
	};
	// Fabric GSM: allow jump to Running Patty. Lamination: no patty wastage.
	if (!lamMode) {
		dialogOpts.secondary_action_label = __("View Patty Wastage");
		dialogOpts.secondary_action = () => {
			d.hide();
			_openRunningPattyWastage(sprName, sprRow, opts);
		};
	}

	const d = new frappe.ui.Dialog(dialogOpts);
	d.show();
	_wireSelectAll(d.$wrapper, ".gwm-roll-cb", ".gwm-roll-all");
	_bindWastagePrint(d.$wrapper, sprName, wasteTable.resolved_fieldname || "custom_roll_waste", wasteRows);
	if (lamMode) {
		d.$wrapper.on("click", ".gwm-btn-other-waste", () => {
			d.hide();
			_openOtherWastages(sprName, sprRow, opts);
		});
	}
	_bindGwmLiveRefresh(d, async (dialog) => {
		const ctxLive = await _fetchWastageContext(sprName);
		const wasteTableLive = _rollWasteTable(ctxLive);
		const wasteRowsLive = _uniqueRollWasteRows(wasteTableLive.rows || []);
		const rollWasteColsLive = _apiColsToDesk(wasteTableLive.columns, DESK_ROLL_WASTE_COLS);
		const html = `<div class="gwm-card" style="margin-top:12px;">
			<div class="gwm-section-title">${__("Already Marked Roll Waste")}</div>
			${_dataCardsHtml(wasteRowsLive, { kind: "roll", showPrint: true })}
		</div>
		<div class="gwm-card" style="margin-top:12px;">
			<div class="gwm-section-title">${__("Roll Waste Table")}</div>
			${_deskTableHtml(rollWasteColsLive, wasteRowsLive, { showPrint: true })}
		</div>`;
		dialog.$wrapper.find(".gwm-roll-waste-existing").html(html);
		_bindWastagePrint(
			dialog.$wrapper,
			sprName,
			wasteTableLive.resolved_fieldname || "custom_roll_waste",
			wasteRowsLive
		);
	});
}

async function _showRollWasteGrid(sprName, sprRow) {
	const ctx = await _fetchWastageContext(sprName);
	const table = _rollWasteTable(ctx);
	const rows = _uniqueRollWasteRows(table.rows || []);
	const rollCols = _apiColsToDesk(table.columns, DESK_ROLL_WASTE_COLS);
	const content = `<div class="gwm-shell">
		<div class="gwm-card">
			<div class="gwm-section-title">${__("Roll Waste")}</div>
			${_dataCardsHtml(rows, { kind: "roll", showPrint: true })}
		</div>
		<div class="gwm-card" style="margin-top:12px;">
			<div class="gwm-section-title">${__("Table View")}</div>
			${_deskTableHtml(rollCols, rows, { showPrint: true })}
		</div>
	</div>`;

	const d = new frappe.ui.Dialog({
		title: __("Roll Waste") + ` · ${sprRow.order_code || ""}`,
		size: "extra-large",
		fields: [{ fieldname: "grid_html", fieldtype: "HTML", options: content }],
		primary_action_label: __("Close"),
		primary_action() {
			d.hide();
		},
	});
	d.show();
	_bindWastagePrint(d.$wrapper, sprName, table.resolved_fieldname || "custom_roll_waste", rows);
}

function _recycledWastageTable(ctx) {
	const tables = ctx?.tables || {};
	const direct = tables.custom_recycled_wastage_details;
	if ((direct?.rows || []).length) {
		return direct;
	}
	for (const [key, table] of Object.entries(tables)) {
		if (!table) {
			continue;
		}
		if (/manual/i.test(key) || /manual/i.test(table.child_doctype || "")) {
			continue;
		}
		if (/recycl/i.test(key) || /recycl/i.test(table.child_doctype || "")) {
			if ((table.rows || []).length) {
				return table;
			}
		}
	}
	return direct || { rows: [], columns: [] };
}

function _manualRecycleTable(ctx) {
	const tables = ctx?.tables || {};
	const direct = tables.custom_gsm_manual_recycle_details;
	if (direct) {
		return direct;
	}
	for (const [key, table] of Object.entries(tables)) {
		if (!table) {
			continue;
		}
		if (/manual.*recycl|gsm_manual_recycl/i.test(key) || /Manual Recycle/i.test(table.child_doctype || "")) {
			return table;
		}
	}
	return { rows: [], columns: [], configured: false };
}

async function _renderRecycleMainBody(sprName) {
	const ctx = await _fetchWastageContext(sprName);
	const recycled = _recycledWastageTable(ctx);
	const manual = _manualRecycleTable(ctx);
	const autoRows = (recycled.rows || []).map(_normalizePattyRow);
	const manualRows = (manual.rows || []).map(_normalizePattyRow);
	const recycledCols = _apiColsToDesk(recycled.columns, DESK_RECYCLED_COLS);
	const manualCols = _apiColsToDesk(manual.columns, DESK_RECYCLED_COLS);

	const autoHtml = autoRows.length
		? _deskTableHtml(recycledCols, autoRows)
		: `<div class="gwm-empty">${__(
				"No automated recycled rows yet."
			)}</div>`;
	const manualHtml = manualRows.length
		? _deskTableHtml(manualCols, manualRows)
		: `<div class="gwm-empty">${__(
				"No manual recycle yet. Use View Patty Stock to add."
			)}</div>`;

	return `<div class="gwm-shell">
		<div class="gwm-card">
			<div class="gwm-section-title">${__("Automated Recycled Wastage Details")}</div>
			${autoHtml}
		</div>
		<div class="gwm-card">
			<div class="gwm-section-title">${__("GSM Manual Recycle (Patty Stock)")}</div>
			${manualHtml}
		</div>
	</div>`;
}

export async function openGsmRecycleDialog(opts = {}) {
	_injectGwmStyles();
	const sprRow = await pickSessionSpr(opts.sessionSprList, opts);
	if (!sprRow) {
		return;
	}
	await _openRecycleMain(sprRow.spr_name, sprRow, opts);
}

async function _openRecycleMain(sprName, sprRow, opts) {
	const recycledBody = await _renderRecycleMainBody(sprName);

	const content = `<div class="gwm-shell">
		<div class="gwm-actions">
			<button type="button" class="btn btn-default gwm-btn-patty">
				<span class="fa fa-eye" style="margin-right:6px;"></span>${__("View Patty Stock")}
			</button>
			<button type="button" class="btn btn-default gwm-btn-roll-waste">${__("Roll Waste")}</button>
		</div>
		<div class="gwm-recycle-body">${recycledBody}</div>
	</div>`;

	const d = new frappe.ui.Dialog({
		title: __("Recycle") + ` · ${sprRow.order_code || ""} · ${sprName}`,
		size: "extra-large",
		fields: [{ fieldname: "body_html", fieldtype: "HTML", options: content }],
		primary_action_label: __("Refresh"),
		primary_action() {
			d.hide();
			_openRecycleMain(sprName, sprRow, opts);
		},
	});
	d.show();

	d.$wrapper.on("click", ".gwm-btn-patty", () => {
		_openPattyStockPicker(sprName, sprRow, () => {
			d.hide();
			_openRecycleMain(sprName, sprRow, opts);
		});
	});
	d.$wrapper.on("click", ".gwm-btn-roll-waste", () => {
		_openRollWasteRecyclePicker(sprName, sprRow, () => {
			d.hide();
			_openRecycleMain(sprName, sprRow, opts);
		});
	});
	_bindGwmLiveRefresh(d, async (dialog) => {
		const body = await _renderRecycleMainBody(sprName);
		dialog.$wrapper.find(".gwm-recycle-body").html(body);
	});
}

async function _openPattyStockPicker(sprName, sprRow, onDone) {
	if (!production_entry.spr_patty_stock || typeof production_entry.spr_patty_stock.open_dialog !== "function") {
		frappe.msgprint(__("Patty stock dialog not loaded."));
		return;
	}
	await production_entry.spr_patty_stock.open_dialog(sprName, {
		on_consume: async (picks, dialog) => {
			await frappe.call({
				method:
					"production_entry.production_planning.unified_production_entry_api.consume_gsm_recycled_wastage",
				args: {
					spr_name: sprName,
					patty_selections: JSON.stringify(picks),
				},
			});
			frappe.show_alert({
				message: __("Added to GSM Manual Recycle Details"),
				indicator: "green",
			});
			if (dialog) {
				dialog.hide();
			}
			if (typeof onDone === "function") {
				onDone();
			}
		},
	});
}

async function _openRollWasteRecyclePicker(sprName, sprRow, onDone) {
	const ctx = await _fetchWastageContext(sprName);
	const rows = _uniqueRollWasteRows(((ctx.tables || {}).custom_roll_waste || {}).rows || []);
	if (!rows.length) {
		frappe.msgprint(__("No roll waste rows on this SPR."));
		return;
	}

	const html = `<div class="gwm-shell gwm-card">
		<div class="gwm-section-title">${__("Roll Waste — select to recycle")}</div>
		<div class="gwm-table-wrap">
			<table class="gwm-desk-table">
				<thead><tr>
					<th class="gwm-check"><input type="checkbox" class="gwm-rw-all" /></th>
					<th>${__("Batch")}</th><th>${__("Job")}</th><th>${__("Quality")}</th>
					<th>${__("GSM")}</th><th class="gwm-num">${__("Width")}</th><th class="gwm-num">${__("Wastage Kg")}</th>
				</tr></thead>
				<tbody>${rows
					.map((r) => {
						const row = _normalizePattyRow(r);
						return `<tr>
					<td class="gwm-check"><input type="checkbox" class="gwm-rw-cb" data-name="${_esc(r.name)}" /></td>
					<td>${_esc(row.batch_no)}</td><td>${_esc(row.job_id)}</td>
					<td>${_esc(row.quality)}</td><td>${_esc(row.gsm)}</td>
					<td class="gwm-num">${_esc(row.width_inch)}</td><td class="gwm-num">${_fmtNum(row.wastage)}</td>
				</tr>`;
					})
					.join("")}</tbody>
			</table>
		</div>
	</div>`;

	const d = new frappe.ui.Dialog({
		title: __("Roll Waste — Recycle") + ` · ${sprRow.order_code || ""}`,
		size: "large",
		fields: [{ fieldname: "rw_html", fieldtype: "HTML", options: html }],
		primary_action_label: __("Consume Selected"),
		async primary_action() {
			const names = [];
			d.$wrapper.find(".gwm-rw-cb:checked").each(function () {
				names.push($(this).data("name"));
			});
			if (!names.length) {
				frappe.msgprint(__("Select at least one row."));
				return;
			}
			d.get_primary_btn().prop("disabled", true);
			try {
				const res = await frappe.call({
					method:
						"production_entry.production_planning.unified_production_entry_api.consume_gsm_recycled_wastage",
					args: {
						spr_name: sprName,
						roll_waste_row_names: JSON.stringify(names),
					},
				});
				const recycledRows = res.message?.recycled?.rows || [];
				if (!recycledRows.length) {
					frappe.msgprint(
						__("Recycle did not stay on GSM Manual Recycle Details. Refresh the SPR and try again.")
					);
				} else {
					frappe.show_alert({
						message: __("Added to GSM Manual Recycle Details"),
						indicator: "green",
					});
				}
				d.hide();
				if (typeof onDone === "function") {
					onDone();
				}
			} catch (e) {
				frappe.msgprint(e.message || __("Consume failed"));
			} finally {
				d.get_primary_btn().prop("disabled", false);
			}
		},
	});
	d.show();
	_wireSelectAll(d.$wrapper, ".gwm-rw-cb", ".gwm-rw-all");
}
