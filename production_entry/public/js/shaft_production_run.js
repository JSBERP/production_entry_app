const SPR_DEBUG_LOGS = false;
function sprLog() {
	if (!SPR_DEBUG_LOGS || !window.console || !console.log) return;
	console.log.apply(console, arguments);
}

/** Net weight (kg) to 2 decimals — matches roll line precision, manual sums, and Total Produced Weight. */
function spr_round_net_weight_kg(v) {
	return Math.round(flt(v) * 100) / 100;
}

/** Child tables on SPR that must keep header/body columns aligned after show/hide or save. */
const SPR_SPR_CHILD_TABLE_FIELDS = ['items', 'shaft_jobs', 'bundle_calculation'];

const SPR_GRID_META_BY_FIELD = {
	items: 'Shaft Production Run Item',
	bundle_calculation: 'Bundle Calculation',
	shaft_jobs: 'Shaft Production Run Job',
};

/** bundle_calculation: scroll-only — bag grid stability. items/shaft_jobs use ordered visible_columns. */
const SPR_GRID_SHOW_ALL_FIELDNAMES = ['bundle_calculation'];

const SPR_SPI_DOCTYPE = 'Shaft Production Run Item';

const SPR_ITEMS_FIELD_FALLBACKS = {
	custom_qc_approval_label: ['custom_qc_approval_label', 'qc_approval_label', 'custom_approval_label'],
	custom_diameter_inches: ['custom_diameter_inches', 'diameter'],
	custom_cbm_cubic_meters: ['custom_cbm_cubic_meters', 'cbm'],
	custom_polybag_kgs: ['custom_polybag_kgs', 'polybag_kgs'],
	custom_core_width_mm: ['custom_core_width_mm', 'core_width'],
};

const SPR_ITEM_KNOWN_PREFIXES = [
	'100', '102', '103', '104', '105', '106', '107', '108', '109', '110',
	'200', '201', '202', '203', '211', '212', '213', '214', '216', '217',
	'221', '222', '223', '224', '231', '232', '233', '241', '242', '225', '226',
];

function spr_resolve_spi_field(fieldname) {
	if (frappe.meta.get_docfield(SPR_SPI_DOCTYPE, fieldname)) {
		return fieldname;
	}
	const alts = SPR_ITEMS_FIELD_FALLBACKS[fieldname];
	if (alts) {
		for (let i = 0; i < alts.length; i += 1) {
			if (frappe.meta.get_docfield(SPR_SPI_DOCTYPE, alts[i])) {
				return alts[i];
			}
		}
	}
	return null;
}

function spr_item_process_prefix(item_code) {
	const code = String(item_code || '').trim().toUpperCase();
	if (!code) {
		return '';
	}
	if (code.indexOf('-') >= 0) {
		const segments = code.split('-');
		for (let i = 0; i < segments.length; i += 1) {
			const segDigits = segments[i].replace(/\D/g, '');
			if (segDigits.length >= 3) {
				const sp = segDigits.substring(0, 3);
				if (SPR_ITEM_KNOWN_PREFIXES.indexOf(sp) >= 0) {
					return sp;
				}
			}
		}
	}
	if (code.length >= 3) {
		const prefix = code.substring(0, 3);
		if (SPR_ITEM_KNOWN_PREFIXES.indexOf(prefix) >= 0) {
			return prefix;
		}
	}
	return '';
}

function spr_resolve_spi_field_list(fieldnames) {
	const out = [];
	const seen = {};
	(fieldnames || []).forEach(function (fn) {
		const resolved = spr_resolve_spi_field(fn) || (frappe.meta.get_docfield(SPR_SPI_DOCTYPE, fn) ? fn : null);
		if (resolved && !seen[resolved]) {
			seen[resolved] = 1;
			out.push(resolved);
		}
	});
	return out;
}

/** Process 100 / mix-roll Roll Production Results — strict column order. */
function spr_build_fabric100_roll_items_show_list(frm) {
	const ordered = [
		'job',
		'custom_no_of_shaft',
		'party_code',
		'item_code',
		'item_name',
		'quality',
		'color',
		'width_inch',
		'gsm',
	];
	if (sprUsesLaminationRollPrompt(frm)) {
		ordered.push('custom_fabric_gsm', 'custom_lam_gsm', 'custom_bopp_gsm');
	}
	ordered.push(
		'meter_roll',
		'produced_length_mtrs',
		'produced_gsm',
		'batch_no',
		'net_weight',
		'gross_weight',
		'planned_qty',
		'uom',
		'custom_core_width_mm',
		'custom_polybag_kgs',
		'save_row',
		'custom_production_label',
		'custom_qc_approval_label',
		'print_sticker',
		'custom_diameter_inches',
		'custom_cbm_cubic_meters',
		'edit_row',
		'work_order'
	);
	return spr_resolve_spi_field_list(ordered);
}

function spr_build_roll_items_show_list(frm, opts) {
	opts = opts || {};
	const showGsmTrio = opts.gsmTrio !== false;
	const ordered = [
		'job',
		'custom_no_of_shaft',
		'party_code',
		'item_code',
		'item_name',
		'quality',
		'color',
		'width_inch',
		'gsm',
	];
	if (showGsmTrio) {
		ordered.push('custom_fabric_gsm', 'custom_lam_gsm', 'custom_bopp_gsm');
	}
	ordered.push(
		'meter_roll',
		'produced_length_mtrs',
		'produced_gsm',
		'batch_no',
		'net_weight',
		'gross_weight',
		'planned_qty',
		'uom',
		'custom_core_width_mm',
		'save_row',
		'custom_production_label',
		'custom_qc_approval_label',
		'custom_diameter_inches',
		'custom_cbm_cubic_meters',
		'custom_polybag_kgs',
		'edit_row',
		'print_sticker',
		'work_order'
	);
	const out = [];
	const seen = {};
	ordered.forEach(function (fn) {
		const resolved = spr_resolve_spi_field(fn) || (frappe.meta.get_docfield(SPR_SPI_DOCTYPE, fn) ? fn : null);
		if (resolved && !seen[resolved]) {
			seen[resolved] = 1;
			out.push(resolved);
		}
	});
	return out;
}

function spr_has_bag_fg_in_items(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	const bagFg = ['221', '222', '223', '224', '231', '232', '233', '241', '242', '225', '226'];
	return (frm.doc.items || []).some(function (row) {
		const p = spr_item_process_prefix(row && row.item_code);
		return bagFg.indexOf(p) >= 0;
	});
}

function spr_items_process_mode(frm) {
	if (!frm || !frm.doc) {
		return 'default';
	}
	if (sprIsBag(frm)) {
		return 'bag';
	}
	if (sprIsSheetCutting(frm)) {
		return 'sheetcutting';
	}
	if (sprIsFabric100Run(frm)) {
		return 'fabric100';
	}
	let prefix = sprRollProcessPrefix(frm);
	if (!prefix) {
		(frm.doc.items || []).some(function (row) {
			const p = spr_item_process_prefix(row && row.item_code);
			if (p) {
				prefix = p;
				return true;
			}
			return false;
		});
	}
	if (prefix === '104' || prefix === '107' || sprUsesLaminationRollPrompt(frm)) {
		return 'lamination';
	}
	if (prefix === '108' || prefix === '109' || prefix === '110') {
		return 'slitting108109';
	}
	if (prefix === '103' || sprUsesSlittingRollPrompt(frm)) {
		return 'slitting103';
	}
	return 'default';
}

function spr_grid_has_user_column_settings(grid) {
	return spr_grid_user_configured(grid);
}

/** True when operator used the grid gear to pick visible columns. */
function spr_grid_user_configured(grid) {
	if (!grid) {
		return false;
	}
	if (grid._spr_columns_user_locked) {
		return true;
	}
	try {
		if (grid.user_defined_columns && grid.user_defined_columns.length) {
			return true;
		}
		if (Array.isArray(grid.user_settings) && grid.user_settings.length) {
			return true;
		}
		if (typeof grid.get_user_settings === 'function') {
			const settings = grid.get_user_settings() || {};
			const gv = settings.GridView || settings.grid_view;
			if (Array.isArray(gv) && gv.length) {
				return true;
			}
		}
	} catch (e) {
		/* ignore */
	}
	return false;
}

function spr_items_grid_is_editing(frm) {
	if (!frm) {
		return false;
	}
	if (frm._spr_items_grid_editing) {
		return true;
	}
	const grid = frm.fields_dict && frm.fields_dict.items && frm.fields_dict.items.grid;
	if (grid && grid.grid_form && grid.grid_form.display) {
		return true;
	}
	return false;
}

function spr_reset_batch_roll_cache(frm) {
	if (!frm) {
		return;
	}
	delete frm._spr_max_roll_cache;
	delete frm._spr_max_roll_cache_before_idx;
	delete frm._spr_batch_shift_cache;
}

/** Normalize shift for batch reuse (matches server batch_shift_value). */
function spr_batch_shift_label(shift) {
	const s = String(shift || '').toLowerCase();
	if (s.includes('night')) {
		return 'Night';
	}
	if (s.includes('day')) {
		return 'Day';
	}
	return String(shift || '').trim();
}

/** Batch prefix (before ``/``) from existing roll lines — same shift only. */
function spr_existing_series_prefix_before_idx(frm, beforeIdx) {
	const curShift = spr_batch_shift_label(frm && frm.doc && frm.doc.shift);
	const all = (frm && frm.doc && frm.doc.items) || [];
	for (let i = 0; i < beforeIdx; i++) {
		const row = all[i];
		const bn = row && row.batch_no;
		if (!bn || String(bn).indexOf('/') === -1) {
			continue;
		}
		if (curShift) {
			const rowShift = spr_batch_shift_label(row.custom_shift);
			if (rowShift && rowShift !== curShift) {
				continue;
			}
		}
		return String(bn).split('/')[0];
	}
	return null;
}

/** Roll suffix from batch_no or roll_no on one item row. */
function spr_roll_suffix_from_row(row) {
	if (!row) {
		return 0;
	}
	let max = 0;
	const bn = row.batch_no;
	if (bn && String(bn).indexOf('/') !== -1) {
		const p = parseInt(String(bn).split('/').pop(), 10);
		if (!isNaN(p)) {
			max = Math.max(max, p);
		}
	}
	if (row.roll_no !== undefined && row.roll_no !== null && row.roll_no !== '') {
		const p = parseInt(String(row.roll_no), 10);
		if (!isNaN(p)) {
			max = Math.max(max, p);
		}
	}
	return max;
}

/** Highest roll suffix among items[0 .. beforeIdx-1] for the current shift only. */
function spr_max_roll_before_idx(frm, beforeIdx) {
	const curShift = spr_batch_shift_label(frm && frm.doc && frm.doc.shift);
	const all = (frm && frm.doc && frm.doc.items) || [];
	const limit = beforeIdx != null ? beforeIdx : all.length;
	if (limit <= 0) {
		if (frm) {
			frm._spr_max_roll_cache = 0;
			frm._spr_max_roll_cache_before_idx = 0;
		}
		return 0;
	}
	const prev = frm._spr_max_roll_cache_before_idx;
	let maxRoll =
		frm._spr_max_roll_cache != null && prev != null && prev <= limit ? frm._spr_max_roll_cache : 0;
	const start = prev != null && prev < limit && frm._spr_max_roll_cache != null ? prev : 0;
	for (let i = start; i < limit; i++) {
		const row = all[i];
		if (curShift) {
			const rowShift = spr_batch_shift_label(row.custom_shift);
			if (rowShift && rowShift !== curShift) {
				continue;
			}
		}
		maxRoll = Math.max(maxRoll, spr_roll_suffix_from_row(row));
	}
	frm._spr_max_roll_cache = maxRoll;
	frm._spr_max_roll_cache_before_idx = limit;
	return maxRoll;
}

function spr_bump_roll_suffix_cache(frm, rollNo) {
	const n = parseInt(rollNo, 10);
	if (!frm || isNaN(n)) {
		return;
	}
	const len = (frm.doc.items || []).length;
	spr_max_roll_before_idx(frm, len);
	frm._spr_max_roll_cache = Math.max(cint(frm._spr_max_roll_cache), n);
	frm._spr_max_roll_cache_before_idx = len;
}

/** Shared post–Create Entry UI pass (single grid refresh, defer heavy styling on large grids). */
function spr_finish_create_entry(frm, opts) {
	opts = opts || {};
	if (!frm) {
		return;
	}
	const lineCount = cint(opts.lineCount) || 0;
	const startIdx =
		opts.startIdx != null ? opts.startIdx : Math.max(0, (frm.doc.items || []).length - lineCount);
	const totalRows = (frm.doc.items || []).length;
	const heavyGrid = totalRows > 15;
	if (opts.bundle) {
		sprSyncBundleProducedSheets(frm, { silent: true });
		for (let i = startIdx; i < totalRows; i++) {
			const it = (frm.doc.items || [])[i];
			if (it && it.name) {
				spr_update_produced_gsm_with_retry(frm, 'Shaft Production Run Item', it.name);
			}
		}
	} else {
		spr_apply_mix_roll_planned_qty(frm);
	}
	update_shaft_job_achieved_from_items(frm, { deferRefresh: true, skipGridRefresh: true });
	spr_sync_no_of_rolls_created(frm);
	sprScheduleTotalProducedSync(frm, { silent: true });
	if (!heavyGrid && !opts.bundle) {
		spr_apply_fabric100_item_grid_columns(frm);
	}
	spr_stabilize_spr_child_grids(frm, {
		delay: heavyGrid ? 500 : 120,
		light: true,
		skipRowStyles: heavyGrid,
	});
	if (!heavyGrid) {
		spr_after_child_table_refresh(frm);
	} else {
		spr_apply_grid_wrap_classes(frm);
		if (!frm.__spr_items_add_style_timer) {
			frm.__spr_items_add_style_timer = setTimeout(function () {
				frm.__spr_items_add_style_timer = null;
				schedule_spr_item_row_styles(frm);
			}, 2000);
		}
	}
	spr_enforce_roll_line_grid_policy(frm);
	// Server Create Entry already saved rolls; wastage/core client scripts still dirty the form —
	// always auto-save so the indicator does not stay Not Saved after every roll.
	sprAutoSaveAfterCreateEntry(frm, { heavy: heavyGrid, quiet: true });
	if (opts.alertMsg) {
		frappe.show_alert({ message: opts.alertMsg, indicator: 'green' });
	}
}

/** Block column realign while inline row editor is open or during lightweight save pass. */
function spr_should_block_grid_realign(frm) {
	if (!frm) {
		return false;
	}
	const itemsGrid = frm.fields_dict && frm.fields_dict.items && frm.fields_dict.items.grid;
	if (itemsGrid && itemsGrid.grid_form && itemsGrid.grid_form.display) {
		return true;
	}
	if (frm._spr_allow_save_row_align) {
		return spr_items_grid_is_editing(frm);
	}
	if (spr_should_use_lightweight_grid_pass(frm)) {
		return true;
	}
	return spr_items_grid_is_editing(frm);
}

/** After Save Row remounts the items grid, restore fieldname column order (no flex/index hacks). */
function spr_repair_items_grid_after_save_row(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	if (spr_items_grid_is_editing(frm)) {
		return;
	}
	frm._spr_allow_save_row_align = true;
	try {
		const fd = spr_get_field_dict(frm, 'items');
		const grid = fd && fd.grid;
		if (grid) {
			grid._spr_columns_user_locked = false;
			spr_clear_spr_grid_saved_columns(grid, SPR_SPI_DOCTYPE);
		}
		const cols = (spr_get_items_list_view_config(frm) && spr_get_items_list_view_config(frm).show) || [];
		const cg = spr_get_grid_columns_module();
		if (cg && typeof cg.apply === 'function' && cols.length) {
			spr_reset_items_grid_field_visibility(frm);
			cg.apply(frm, 'items', SPR_SPI_DOCTYPE, cols, { fullRefresh: true });
		} else {
			spr_repair_child_grid_alignment(frm, 'items');
		}
		spr_apply_grid_column_min_widths(frm, 'items', SPR_ITEMS_COL_MIN_PX);
		spr_apply_items_row_lock_ui(frm);
		apply_spr_item_row_styles(frm);
	} finally {
		frm._spr_allow_save_row_align = false;
	}
}

function spr_schedule_items_align_after_save_row(frm) {
	if (!frm) {
		return;
	}
	[80, 280, 650].forEach(function (ms) {
		setTimeout(function () {
			spr_repair_items_grid_after_save_row(frm);
		}, ms);
	});
}

/** After Save / Save Row — repair-only path; no full column mirror (prevents blink/collapse). */
function spr_should_use_lightweight_grid_pass(frm) {
	if (!frm) {
		return false;
	}
	if (frm._spr_light_reload) {
		return true;
	}
	if (frm._spr_submit_in_progress) {
		return true;
	}
	if (frm._spr_just_submitted && Date.now() - frm._spr_just_submitted < 12000) {
		return true;
	}
	if (frm._spr_row_save_in_progress) {
		return true;
	}
	return !!(frm._spr_just_saved && Date.now() - frm._spr_just_saved < 6000);
}

/**
 * SPR child-grid alignment contract — do not regress (fixed cae2c52).
 * Cursor rule: .cursor/rules/spr-grid-alignment.mdc
 *
 * - Min-widths: fieldname via spr_find_grid_header_col (never header index).
 * - after_save / Save Row: light pass only — no staggered spr_force_child_grids_realign.
 * - spr_light_grid_scroll_sync: scroll only — no dynamic header width hacks.
 * - row-index 60px in CSS (sprItemsCssVer); repair via spr_child_grid_needs_repair only.
 * - Never: flex+order on grid rows, MutationObserver on grid body, spr_sync_header_body_alignment.
 */
const SPR_GRID_ALIGNMENT_CONTRACT_VER = '1';

const SPR_ITEMS_COL_MIN_PX = {
	party_code: 92,
	work_order: 108,
	item_code: 108,
	item_name: 120,
	batch_no: 96,
	job: 44,
	quality: 72,
	color: 112,
	width_inch: 72,
	gsm: 64,
	produced_gsm: 72,
	planned_qty: 80,
	net_weight: 72,
	gross_weight: 72,
	meter_roll: 72,
	produced_length_mtrs: 88,
	uom: 48,
	save_row: 72,
	edit_row: 72,
};

const SPR_SHAFT_JOBS_COL_MIN_PX = {
	party_code: 92,
	job: 44,
	combination: 140,
	total_width: 88,
	planned_qty: 80,
	achieved_qty: 80,
	quality: 72,
	color: 112,
	gsm: 64,
	manual_items: 100,
};

const SPR_GRID_DEFAULT_COL_MIN_PX = 72;

function spr_find_grid_header_col($wrap, df) {
	if (!$wrap || !$wrap.length || !df) {
		return $();
	}
	let $hc = $wrap
		.find('.grid-heading-row [data-fieldname="' + df.fieldname + '"]')
		.closest('.grid-static-col, .col')
		.first();
	if ($hc.length) {
		return $hc;
	}
	const label = String(df.label || df.fieldname || '').trim().toLowerCase();
	return $wrap
		.find('.grid-heading-row .grid-static-col')
		.filter(function () {
			if ($(this).hasClass('row-check') || $(this).hasClass('row-index')) {
				return false;
			}
			const t = String($(this).text() || '').trim().toLowerCase();
			return t && (t === label || t.indexOf(label) === 0 || label.indexOf(t) === 0);
		})
		.first();
}

function spr_apply_grid_column_min_widths(frm, fieldname, widthMap) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.grid || !fd.$wrapper || !fd.$wrapper.length || !widthMap) {
		return;
	}
	const grid = fd.grid;
	const $wrap = fd.$wrapper;
	const visible = (grid.docfields || []).filter(function (df) {
		return df && df.in_list_view && !df.hidden && !cg_skip_field_spr(df);
	});
	if (!visible.length) {
		return;
	}
	visible.forEach(function (df) {
		const px = widthMap[df.fieldname] || SPR_GRID_DEFAULT_COL_MIN_PX;
		// min-width only — match header/body by fieldname, not column index.
		const css = { minWidth: px + 'px' };
		const $hc = spr_find_grid_header_col($wrap, df);
		if ($hc.length) {
			$hc.css(css);
		}
		$wrap.find('.grid-body .rows > .grid-row').not('.grid-form-row').each(function () {
			const $col = $(this)
				.find('[data-fieldname="' + df.fieldname + '"]')
				.closest('.col, .grid-static-col')
				.first();
			if ($col.length) {
				$col.css(css);
			}
		});
	});
}

/** Compare DOM column order between header and first body row (catches pencil/offset zig-zag). */
function spr_grid_dom_columns_misaligned(frm, fieldname) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return false;
	}
	const $wrap = fd.$wrapper;
	const $bodyRow = $wrap.find('.grid-body .rows > .grid-row').not('.grid-form-row').first();
	if (!$bodyRow.length) {
		return false;
	}
	function collectFieldnames($row) {
		const out = [];
		if (!$row || !$row.length) {
			return out;
		}
		const $root = $row.find('.data-row').first().length ? $row.find('.data-row').first() : $row;
		$root.children().each(function () {
			const $el = $(this);
			if (
				$el.hasClass('row-check') ||
				$el.hasClass('row-index') ||
				$el.hasClass('grid-settings') ||
				$el.hasClass('configure-columns')
			) {
				return;
			}
			const fn =
				$el.attr('data-fieldname') ||
				($el.find('[data-fieldname]').first().attr('data-fieldname') || '');
			if (fn) {
				out.push(fn);
			}
		});
		return out;
	}
	const headFns = collectFieldnames($wrap.find('.grid-heading-row').first());
	const bodyFns = collectFieldnames($bodyRow);
	if (!headFns.length || !bodyFns.length) {
		return false;
	}
	if (headFns.length !== bodyFns.length) {
		return true;
	}
	for (let i = 0; i < headFns.length; i++) {
		if (headFns[i] !== bodyFns[i]) {
			return true;
		}
	}
	return false;
}

function cg_skip_field_spr(df) {
	return (
		!df ||
		df.fieldtype === 'Column Break' ||
		df.fieldtype === 'Section Break' ||
		df.fieldtype === 'Tab Break' ||
		df.fieldtype === 'HTML'
	);
}

function spr_apply_spr_child_grid_min_widths(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	spr_apply_grid_column_min_widths(frm, 'items', SPR_ITEMS_COL_MIN_PX);
	spr_apply_grid_column_min_widths(frm, 'shaft_jobs', SPR_SHAFT_JOBS_COL_MIN_PX);
}

function spr_grid_party_code_looks_collapsed(frm, fieldname) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return false;
	}
	const $party = fd.$wrapper.find(
		'.grid-heading-row .grid-static-col:has([data-fieldname="party_code"]), ' +
			'.grid-heading-row [data-fieldname="party_code"]'
	);
	let $col = $party.closest('.grid-static-col');
	if (!$col.length) {
		$col = fd.$wrapper.find('.grid-heading-row .grid-static-col').filter(function () {
			const t = $(this).text() || '';
			return /order\s*code/i.test(t) || /party/i.test(t);
		});
	}
	if (!$col.length) {
		return false;
	}
	const w = $col.outerWidth() || 0;
	return w > 0 && w < 58;
}

function spr_grid_first_row_field_misaligned(frm, fieldname, field) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return false;
	}
	const rows = (frm.doc && frm.doc[fieldname]) || [];
	if (!rows.length) {
		return false;
	}
	const expected = String(rows[0][field] || '').trim();
	if (!expected) {
		return false;
	}
	const $cell = fd.$wrapper
		.find('.grid-body .rows > .grid-row')
		.not('.grid-form-row')
		.first()
		.find('[data-fieldname="' + field + '"]');
	if (!$cell.length) {
		return true;
	}
	const actual = String(
		$cell.find('.static-area').text() || $cell.find('input').val() || $cell.text() || ''
	).trim();
	if (!actual) {
		return true;
	}
	const norm = function (s) {
		return s.toUpperCase().replace(/\s+/g, ' ').trim();
	};
	const e = norm(expected);
	const a = norm(actual);
	const probe = e.substring(0, Math.min(8, e.length));
	return probe.length > 2 && a.indexOf(probe) < 0 && e.indexOf(a.substring(0, Math.min(8, a.length))) < 0;
}

function spr_child_grid_needs_repair(frm, fieldname) {
	const fd = spr_get_field_dict(frm, fieldname);
	const grid = fd && fd.grid;
	if (!grid) {
		return false;
	}
	const cgMod = spr_get_grid_columns_module();
	if (cgMod && typeof cgMod.header_matches_rows === 'function' && !cgMod.header_matches_rows(grid)) {
		return true;
	}
	if (spr_is_submitted_spr(frm)) {
		return false;
	}
	if (spr_grid_party_code_looks_collapsed(frm, fieldname)) {
		return true;
	}
	if (spr_grid_first_row_field_misaligned(frm, fieldname, 'color')) {
		return true;
	}
	if (spr_grid_first_row_field_misaligned(frm, fieldname, 'party_code')) {
		return true;
	}
	if (fieldname === 'items' && spr_grid_dom_columns_misaligned(frm, fieldname)) {
		return true;
	}
	return false;
}

function spr_debounced_repair_child_grid_alignment(frm, fieldname) {
	if (!frm || !fieldname) {
		return;
	}
	const key = '_spr_repair_timer_' + fieldname;
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		spr_repair_child_grid_alignment(frm, fieldname);
	}, 60);
}

function spr_repair_child_grid_alignment(frm, fieldname) {
	if (!frm || !frm.fields_dict || spr_should_block_grid_realign(frm)) {
		return;
	}
	if (spr_is_submitted_spr(frm)) {
		spr_stabilize_submitted_spr_grids_once(frm);
		return;
	}
	const fd = spr_get_field_dict(frm, fieldname);
	const grid = fd && fd.grid;
	if (!grid || grid._spr_repair_running) {
		return;
	}
	if (!spr_child_grid_needs_repair(frm, fieldname)) {
		spr_apply_grid_column_min_widths(
			frm,
			fieldname,
			fieldname === 'items' ? SPR_ITEMS_COL_MIN_PX : SPR_SHAFT_JOBS_COL_MIN_PX
		);
		return;
	}
	grid._spr_repair_running = true;
	try {
		const cg = spr_get_grid_columns_module();
		if (!cg || typeof cg.apply !== 'function') {
			return;
		}
		if (fieldname === 'items') {
			const cols = (spr_get_items_list_view_config(frm) && spr_get_items_list_view_config(frm).show) || [];
			if (!cols.length) {
				return;
			}
			spr_reset_items_grid_field_visibility(frm);
			cg.apply(frm, 'items', SPR_SPI_DOCTYPE, cols, { fullRefresh: true });
			spr_apply_grid_column_min_widths(frm, 'items', SPR_ITEMS_COL_MIN_PX);
		} else if (fieldname === 'shaft_jobs') {
			const cols = spr_build_shaft_jobs_show_list(frm) || [];
			if (!cols.length) {
				return;
			}
			spr_reset_shaft_jobs_grid_field_visibility(frm);
			cg.apply(frm, 'shaft_jobs', 'Shaft Production Run Job', cols, { fullRefresh: true });
			spr_apply_grid_column_min_widths(frm, 'shaft_jobs', SPR_SHAFT_JOBS_COL_MIN_PX);
		}
		if (cg.sync_header_scroll) {
			cg.sync_header_scroll(frm, fieldname);
		} else {
			spr_light_grid_scroll_sync(frm, fieldname);
		}
	} finally {
		setTimeout(function () {
			if (grid) {
				grid._spr_repair_running = false;
			}
		}, 200);
	}
}

function spr_is_large_submitted_grid(frm) {
	return spr_is_submitted_spr(frm) && ((frm.doc.items || []).length > 15);
}

function spr_apply_item_row_styles_light(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	const key = '_spr_light_style_timer';
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		if (!frm || !frm.fields_dict) {
			return;
		}
		apply_spr_item_row_styles(frm);
	}, 450);
}

/** Submitted SPR: one-shot column sync — no repeated paint hooks (stops header zig-zag). */
function spr_stabilize_submitted_spr_grids_once(frm) {
	if (!spr_is_submitted_spr(frm) || !frm || frm._spr_submitted_grids_stable) {
		return;
	}
	const key = '_spr_submitted_stabilize_timer';
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		if (!frm || !frm.fields_dict || frm._spr_submitted_grids_stable) {
			return;
		}
		frm._spr_submitted_grids_stable = true;
		spr_apply_grid_wrap_classes(frm);
		ensure_spr_item_stylesheet();
		spr_reset_items_grid_field_visibility(frm);
		spr_reset_shaft_jobs_grid_field_visibility(frm);
		spr_apply_items_grid_columns(frm, true);
		spr_apply_shaft_jobs_grid_columns(frm, true);
		const cg = spr_get_grid_columns_module();
		if (cg && typeof cg.apply === 'function') {
			const itemCols = (spr_get_items_list_view_config(frm) && spr_get_items_list_view_config(frm).show) || [];
			const jobCols = spr_build_shaft_jobs_show_list(frm) || [];
			if (itemCols.length && frm.fields_dict.items) {
				cg.apply(frm, 'items', SPR_SPI_DOCTYPE, itemCols, { fullRefresh: true });
			}
			if (jobCols.length && frm.fields_dict.shaft_jobs) {
				cg.apply(frm, 'shaft_jobs', 'Shaft Production Run Job', jobCols, { fullRefresh: true });
			}
		}
		spr_ensure_child_grid_heights(frm);
		apply_spr_item_row_styles(frm);
		['items', 'shaft_jobs'].forEach(function (fn) {
			const fd = spr_get_field_dict(frm, fn);
			if (fd) {
				spr_sync_grid_header_body_scroll(fd);
			}
		});
	}, 280);
}

function spr_install_post_save_grid_health_watch(frm) {
	if (!frm) {
		return;
	}
	const key = '_spr_post_save_grid_watch_gen';
	const gen = (frm[key] || 0) + 1;
	frm[key] = gen;
	const checks = [800, 2500];
	checks.forEach(function (ms) {
		setTimeout(function () {
			if (!frm || frm[key] !== gen || !frm.fields_dict) {
				return;
			}
			if (!frm._spr_just_saved || Date.now() - frm._spr_just_saved > 12000) {
				return;
			}
			let collapsed = false;
			let misaligned = false;
			['items', 'shaft_jobs'].forEach(function (fn) {
				if (spr_grid_party_code_looks_collapsed(frm, fn)) {
					collapsed = true;
				}
				if (spr_child_grid_needs_repair(frm, fn)) {
					misaligned = true;
				}
			});
			spr_apply_spr_child_grid_min_widths(frm);
			['items', 'shaft_jobs'].forEach(function (fn) {
				spr_light_grid_scroll_sync(frm, fn);
			});
			if (collapsed || misaligned) {
				['items', 'shaft_jobs'].forEach(function (fn) {
					spr_repair_child_grid_alignment(frm, fn);
				});
			}
		}, ms);
	});
}

function spr_bind_items_grid_edit_guard(frm) {
	if (!frm || frm._spr_items_edit_guard_bound) {
		return;
	}
	const fd = frm.fields_dict && frm.fields_dict.items;
	const grid = fd && fd.grid;
	if (!grid || !grid.wrapper || !grid.wrapper.length) {
		return;
	}
	frm._spr_items_edit_guard_bound = true;
	spr_install_items_row_action_handlers(frm);
	grid.wrapper.on('focusin.sprGridEdit', 'input, textarea, select', function () {
		frm._spr_items_grid_editing = true;
	});

	grid.wrapper.on('focusout.sprGridEdit', 'input, textarea, select', function () {
		setTimeout(function () {
			if (!frm.fields_dict || !frm.fields_dict.items || !frm.fields_dict.items.grid) {
				return;
			}
			const active = document.activeElement;
			const wrap = frm.fields_dict.items.grid.wrapper && frm.fields_dict.items.grid.wrapper[0];
			if (!wrap || !active || !wrap.contains(active)) {
				frm._spr_items_grid_editing = false;
				if (frm._spr_gw_clear_timers) {
					Object.keys(frm._spr_gw_clear_timers).forEach(function (cdn) {
						clearTimeout(frm._spr_gw_clear_timers[cdn]);
						delete frm._spr_gw_clear_timers[cdn];
					});
				}
				(frm.doc.items || []).forEach(function (row) {
					if (!row || !row.name) {
						return;
					}
					if (spr_normalize_gross_weight_input(row.gross_weight) <= 0) {
						spr_clear_roll_weight_dependents(frm, 'Shaft Production Run Item', row.name);
					}
				});
				if (frm._spr_roll_policy_pending) {
					frm._spr_roll_policy_pending = false;
				}
				if (frm._spr_grid_totals_debounce) {
					clearTimeout(frm._spr_grid_totals_debounce);
					frm._spr_grid_totals_debounce = null;
				}
				update_shaft_job_achieved_from_items(frm);
				sprScheduleTotalProducedSync(frm);
				spr_flush_deferred_grid_side_effects(frm);
				spr_enforce_roll_line_grid_policy(frm, { force: true });
			}
		}, 120);
	});
}

function spr_install_items_grid_column_guard(frm) {
	const cg = spr_get_grid_columns_module();
	if (!cg || typeof cg.install_refresh_column_guard !== 'function') {
		return;
	}
	if (frm._spr_items_cg_guard_installed) {
		return;
	}
	frm._spr_items_cg_guard_installed = true;
	cg.install_refresh_column_guard(frm, 'items', function () {
		const cfg = spr_get_items_list_view_config(frm);
		return (cfg && cfg.show) || [];
	});
}

function spr_after_items_grid_columns_changed(frm) {
	if (!frm || !frm.fields_dict || !frm.fields_dict.items) {
		return;
	}
	ensure_spr_item_stylesheet();
	spr_apply_grid_wrap_classes(frm);
	spr_reapply_item_row_styles_with_retries(frm, [0, 80, 200, 450, 800, 1200, 1800]);
	[0, 120, 350, 700].forEach(function (ms) {
		setTimeout(function () {
			if (frm && frm.fields_dict && frm.fields_dict.items) {
				spr_apply_items_row_lock_ui(frm);
			}
		}, ms);
	});
}

function spr_bind_spr_grid_column_configure_hook(frm, fieldname) {
	if (!frm || !fieldname) {
		return;
	}
	const hookKey = '_spr_configure_hooked_' + fieldname;
	if (frm[hookKey]) {
		return;
	}
	const grid = frm.fields_dict && frm.fields_dict[fieldname] && frm.fields_dict[fieldname].grid;
	if (!grid) {
		return;
	}
	frm[hookKey] = true;
	const markUserConfigured = function () {
		grid._spr_columns_user_locked = true;
	};
	const afterColumnsChanged = function () {
		if (fieldname === 'items') {
			spr_after_items_grid_columns_changed(frm);
		}
	};
	if (typeof grid.configure_columns === 'function') {
		const origConfigure = grid.configure_columns.bind(grid);
		grid.configure_columns = function () {
			const ret = origConfigure.apply(grid, arguments);
			setTimeout(markUserConfigured, 80);
			return ret;
		};
	}
	if (typeof grid.setup_visible_columns === 'function' && !grid._spr_setup_visible_columns_wrapped) {
		grid._spr_setup_visible_columns_wrapped = true;
		const origSetup = grid.setup_visible_columns.bind(grid);
		grid.setup_visible_columns = function () {
			const ret = origSetup.apply(grid, arguments);
			setTimeout(afterColumnsChanged, 40);
			return ret;
		};
	}
	if (grid.wrapper && grid.wrapper.length) {
		grid.wrapper.on('click.sprGridConfigure', '.grid-settings, .configure-columns', function () {
			setTimeout(function () {
				if (spr_grid_user_configured(grid)) {
					markUserConfigured();
				}
			}, 400);
		});
	}
	if (fieldname === 'items' && !frm._spr_items_col_modal_hook) {
		frm._spr_items_col_modal_hook = true;
		$(document).on(
			'click.sprItemsColModal',
			'.modal.show .btn-modal-primary, .modal.show .reset-grid, .modal.show .reset-to-default',
			function () {
				const $modal = $(this).closest('.modal');
				if (!$modal.length) {
					return;
				}
				const title = ($modal.find('.modal-title').text() || '').trim();
				if (title.indexOf('Configure Columns') === -1 && title.indexOf('Configure Grid') === -1) {
					return;
				}
				setTimeout(afterColumnsChanged, 120);
			}
		);
		$(document).on('hidden.bs.modal.sprItemsColModal', '.modal', function () {
			const $modal = $(this);
			const title = ($modal.find('.modal-title').text() || '').trim();
			if (title.indexOf('Configure Columns') === -1 && title.indexOf('Configure Grid') === -1) {
				return;
			}
			afterColumnsChanged();
		});
	}
}

function spr_attach_grid_scroll_sync(fd) {
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return;
	}
	const $w = fd.$wrapper;
	if (!$w.data('spr-scroll-sync')) {
		$w.data('spr-scroll-sync', 1);
		$w.on(
			'scroll.sprGridAlign',
			'.form-grid-container, .dt-scrollable, .form-grid .grid-body, .grid-heading-row',
			function () {
				spr_sync_grid_header_body_scroll(fd);
			}
		);
	}
}

/** Custom-field duplicates of Produced GSM — must stay out of list view (single pass with column apply). */
function spr_duplicate_produced_gsm_fieldnames() {
	const out = {};
	['custom_produced_gsm', 'produced_gsm_copy'].forEach(function (fn) {
		if (frappe.meta.get_docfield(SPR_SPI_DOCTYPE, fn)) {
			out[fn] = 1;
		}
	});
	try {
		(frappe.get_meta(SPR_SPI_DOCTYPE).fields || []).forEach(function (df) {
			if (
				df.fieldname &&
				df.fieldname !== 'produced_gsm' &&
				String(df.label || '').trim() === 'Produced GSM'
			) {
				out[df.fieldname] = 1;
			}
		});
	} catch (e) {
		/* ignore */
	}
	return out;
}

/** Re-render grid body rows so column count matches header after in_list_view changes. */
function spr_refresh_grid_body_rows(grid) {
	if (!grid) {
		return;
	}
	try {
		const rows = grid.grid_rows || [];
		rows.forEach(function (gr) {
			if (gr && typeof gr.refresh === 'function') {
				gr.refresh();
			}
		});
	} catch (e) {
		/* ignore */
	}
}

function spr_is_draft_spr(frm) {
	return !!(frm && frm.doc && cint(frm.doc.docstatus) === 0);
}

function spr_is_submitted_spr(frm) {
	return !!(frm && frm.doc && cint(frm.doc.docstatus) === 1);
}

/** After submit, site Client Scripts (core / wastage) used to rewrite child tables and leave "Not Saved". */
function spr_persist_or_clear_submitted_client_dirty(frm) {
	if (!spr_is_submitted_spr(frm) || frm._spr_submitted_dirty_handled) {
		return;
	}
	if (frm._spr_submitted_dirty_timer) {
		clearTimeout(frm._spr_submitted_dirty_timer);
	}
	frm._spr_submitted_dirty_timer = setTimeout(function () {
		frm._spr_submitted_dirty_timer = null;
		if (!spr_is_submitted_spr(frm)) {
			return;
		}
		if (typeof frm.is_dirty !== 'function' || !frm.is_dirty()) {
			return;
		}
		frm._spr_submitted_dirty_handled = true;
		if (typeof frm.save === 'function') {
			frm.save('Update').fail(function () {
				frm._spr_submitted_dirty_handled = false;
			});
		}
	}, 2200);
}

function spr_grid_run_without_paint_hook(grid, fn) {
	if (!grid || typeof fn !== 'function') {
		return;
	}
	grid._spr_skip_paint_hook = true;
	try {
		fn();
	} finally {
		setTimeout(function () {
			grid._spr_skip_paint_hook = false;
		}, 0);
	}
}

/** All list-viewable fields for a child doctype in meta field_order (skip hidden / breaks). */
function spr_build_all_grid_show_fields(metaDoctype, extraSkip) {
	const skipNames = extraSkip || {};
	const skipTypes = { 'Column Break': 1, 'Section Break': 1, 'Tab Break': 1, 'HTML': 1 };
	const names = [];
	const seen = {};
	function pushField(fn) {
		if (!fn || seen[fn] || skipNames[fn]) {
			return;
		}
		const df = frappe.meta.get_docfield(metaDoctype, fn);
		if (!df || skipTypes[df.fieldtype] || cint(df.hidden)) {
			return;
		}
		seen[fn] = 1;
		names.push(fn);
	}
	const meta = frappe.get_meta(metaDoctype);
	const fo = meta && meta.field_order;
	if (Array.isArray(fo) && fo.length) {
		fo.forEach(pushField);
	} else {
		(frappe.meta.get_docfields(metaDoctype) || []).forEach(function (df) {
			pushField(df && df.fieldname);
		});
	}
	return names;
}

/** Build ordered visible field list for items / shaft_jobs grids. */
function spr_resolve_grid_show_columns(frm, gridFieldname, columnFieldList) {
	const metaDoctype = SPR_GRID_META_BY_FIELD[gridFieldname];
	if (!metaDoctype) {
		return [];
	}
	const isDraft = spr_is_draft_spr(frm);
	const resolvedShow = [];
	(columnFieldList || []).forEach(function (fn) {
		if (frappe.meta.get_docfield(metaDoctype, fn)) {
			resolvedShow.push(fn);
		}
	});
	let visibleCols = resolvedShow;
	if (gridFieldname === 'items') {
		const hideExtra = spr_duplicate_produced_gsm_fieldnames();
		visibleCols = visibleCols.filter(function (fn) {
			return !hideExtra[fn];
		});
	}
	if (!isDraft) {
		if (gridFieldname === 'shaft_jobs') {
			visibleCols = visibleCols.filter(function (fn) {
				return fn !== 'create_roll_entry';
			});
		}
		if (gridFieldname === 'items') {
			visibleCols = visibleCols.filter(function (fn) {
				return fn !== 'save_row' && fn !== 'edit_row' && fn !== 'print_sticker';
			});
		}
	}
	return visibleCols;
}

/** Default Available Jobs columns — avoids showing every child field (breaks header alignment). */
function spr_build_shaft_jobs_show_list(frm) {
	const isDraft = spr_is_draft_spr(frm);
	const ordered = [
		'job_id',
		'gsm',
		'quality',
		'combination',
		'total_width',
		'meter_roll_mtrs',
		'net_weight',
		'total_weight',
		'custom_total_achieved_weight',
		'custom_total_achieved_meter',
		'no_of_shafts',
		'no_of_rolls',
		'party_code',
	];
	if (isDraft) {
		ordered.push('create_roll_entry');
	}
	const out = [];
	const seen = {};
	ordered.forEach(function (fn) {
		if (!fn || seen[fn] || !frappe.meta.get_docfield('Shaft Production Run Job', fn)) {
			return;
		}
		seen[fn] = 1;
		out.push(fn);
	});
	return out;
}

/** Apply column list — uses cg.apply on forced setup, lightweight sync otherwise. */
function spr_apply_grid_visible_columns(frm, gridFieldname, columnFieldList, force) {
	const fd = frm && frm.fields_dict && frm.fields_dict[gridFieldname];
	if (!fd || !fd.grid) {
		return;
	}
	const grid = fd.grid;
	const metaDoctype = SPR_GRID_META_BY_FIELD[gridFieldname];
	if (!metaDoctype) {
		spr_attach_grid_scroll_sync(fd);
		spr_sync_grid_header_body_scroll(fd);
		return;
	}
	const visibleCols = spr_resolve_grid_show_columns(frm, gridFieldname, columnFieldList);
	if (!visibleCols.length) {
		spr_attach_grid_scroll_sync(fd);
		spr_sync_grid_header_body_scroll(fd);
		return;
	}
	if (grid._spr_columns_user_locked) {
		spr_mirror_grid_docfields_to_rows(grid);
		spr_attach_grid_scroll_sync(fd);
		spr_sync_grid_header_body_scroll(fd);
		return;
	}
	grid._spr_desired_column_order = visibleCols;
	const showSet = {};
	visibleCols.forEach(function (fn) {
		showSet[fn] = 1;
	});
	const cg = spr_get_grid_columns_module();
	if (force && cg && typeof cg.apply === 'function') {
		cg.apply(frm, gridFieldname, metaDoctype, visibleCols, {
			fullRefresh: gridFieldname === 'items' || gridFieldname === 'shaft_jobs',
		});
		spr_sync_meta_list_view_flags(metaDoctype, showSet);
		spr_attach_grid_scroll_sync(fd);
		if (typeof cg.sync_header_scroll === 'function') {
			cg.sync_header_scroll(frm, gridFieldname);
		} else {
			spr_sync_grid_header_body_scroll(fd);
		}
		if (gridFieldname === 'items' || gridFieldname === 'shaft_jobs') {
			spr_apply_grid_column_min_widths(
				frm,
				gridFieldname,
				gridFieldname === 'items' ? SPR_ITEMS_COL_MIN_PX : SPR_SHAFT_JOBS_COL_MIN_PX
			);
		}
		return;
	}
	(frappe.meta.get_docfields(metaDoctype) || []).forEach(function (df) {
		if (!df || df.fieldtype === 'Column Break' || df.fieldtype === 'Section Break') {
			return;
		}
		const show = !!showSet[df.fieldname];
		try {
			grid.update_docfield_property(df.fieldname, 'hidden', 0);
			grid.update_docfield_property(df.fieldname, 'in_list_view', show ? 1 : 0);
		} catch (e) {
			/* ignore */
		}
	});
	spr_light_sync_grid_columns(grid, visibleCols, showSet, { refreshRows: false });
	spr_sync_meta_list_view_flags(metaDoctype, showSet);
	spr_attach_grid_scroll_sync(fd);
	spr_sync_grid_header_body_scroll(fd);
	if (gridFieldname === 'items' || gridFieldname === 'shaft_jobs') {
		spr_apply_grid_column_min_widths(
			frm,
			gridFieldname,
			gridFieldname === 'items' ? SPR_ITEMS_COL_MIN_PX : SPR_SHAFT_JOBS_COL_MIN_PX
		);
	}
}

/** Draft only: Create Entry in grid + row expand. Submitted: fully hidden (not in list view). */
function spr_apply_create_entry_buttons_ui(frm) {
	const isDraft = spr_is_draft_spr(frm);
	const jobsGrid = frm && frm.fields_dict && frm.fields_dict.shaft_jobs && frm.fields_dict.shaft_jobs.grid;
	if (jobsGrid) {
		try {
			jobsGrid.update_docfield_property('create_roll_entry', 'in_list_view', isDraft ? 1 : 0);
			jobsGrid.update_docfield_property('create_roll_entry', 'hidden', isDraft ? 0 : 1);
		} catch (e) {
			/* ignore */
		}
	}
	const bundleGrid =
		frm && frm.fields_dict && frm.fields_dict.bundle_calculation && frm.fields_dict.bundle_calculation.grid;
	if (bundleGrid) {
		try {
			bundleGrid.update_docfield_property('create_bundle_entry', 'in_list_view', isDraft ? 1 : 0);
			bundleGrid.update_docfield_property('create_bundle_entry', 'hidden', isDraft ? 0 : 1);
		} catch (e) {
			/* ignore */
		}
	}
}

function spr_install_shaft_jobs_grid_column_guard(frm) {
	const cg = spr_get_grid_columns_module();
	if (!cg || typeof cg.install_refresh_column_guard !== 'function') {
		return;
	}
	if (frm._spr_shaft_jobs_cg_guard_installed) {
		return;
	}
	frm._spr_shaft_jobs_cg_guard_installed = true;
	cg.install_refresh_column_guard(frm, 'shaft_jobs', function () {
		return spr_build_shaft_jobs_show_list(frm) || [];
	});
}

function spr_apply_shaft_jobs_create_entry_ui(frm) {
	spr_apply_create_entry_buttons_ui(frm);
}

function spr_apply_shaft_jobs_grid_columns(frm, force) {
	const fd = spr_get_field_dict(frm, 'shaft_jobs');
	const grid = fd && fd.grid;
	if (!grid) {
		return;
	}
	spr_bind_spr_grid_column_configure_hook(frm, 'shaft_jobs');
	spr_install_shaft_jobs_grid_column_guard(frm);
	if (grid._spr_columns_user_locked) {
		spr_light_realign_field(frm, 'shaft_jobs');
		spr_apply_shaft_jobs_create_entry_ui(frm);
		spr_ensure_child_grid_heights(frm);
		return;
	}
	const show = spr_build_shaft_jobs_show_list(frm);
	if (force) {
		spr_reset_shaft_jobs_grid_field_visibility(frm);
	}
	spr_apply_grid_visible_columns(frm, 'shaft_jobs', show, !!force);
	spr_apply_shaft_jobs_create_entry_ui(frm);
	spr_ensure_child_grid_heights(frm);
}

function spr_apply_items_grid_columns(frm, force) {
	const fd = spr_get_field_dict(frm, 'items');
	if (!fd || !fd.grid) {
		return;
	}
	const grid = fd.grid;
	const mode = spr_items_process_mode(frm);
	const modeChanged = frm._spr_items_cols_mode !== mode;
	if (modeChanged) {
		force = true;
		frm._spr_items_cols_mode = mode;
		grid._spr_columns_user_locked = false;
	}
	spr_bind_items_grid_edit_guard(frm);
	spr_install_items_grid_column_guard(frm);
	spr_bind_spr_grid_column_configure_hook(frm, 'items');
	if (!force && spr_should_block_grid_realign(frm)) {
		spr_restore_cached_grid_columns(frm, 'items');
		return;
	}
	if (grid._spr_columns_user_locked && !force) {
		spr_mirror_grid_docfields_to_rows(grid);
		spr_light_grid_scroll_sync(frm, 'items');
		return;
	}
	const cfg = spr_get_items_list_view_config(frm);
	const show = cfg.show || [];
	if (force) {
		spr_reset_items_grid_field_visibility(frm);
	}
	spr_apply_grid_visible_columns(frm, 'items', show, !!force);
}

/** Emergency: clear hidden flags on all item grid fields (recover from bad column apply). */
function spr_reset_items_grid_field_visibility(frm) {
	const grid = frm && frm.fields_dict && frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) {
		return;
	}
	(frappe.meta.get_docfields(SPR_SPI_DOCTYPE) || []).forEach(function (df) {
		if (!df || df.fieldtype === 'Column Break' || df.fieldtype === 'Section Break') {
			return;
		}
		try {
			grid.update_docfield_property(df.fieldname, 'hidden', 0);
			grid.update_docfield_property(df.fieldname, 'in_list_view', 0);
		} catch (e) {
			/* ignore */
		}
	});
}
function spr_reset_bundle_calc_grid_field_visibility(frm) {
	const grid = frm && frm.fields_dict && frm.fields_dict.bundle_calculation && frm.fields_dict.bundle_calculation.grid;
	if (!grid) {
		return;
	}
	(frappe.meta.get_docfields('Bundle Calculation') || []).forEach(function (df) {
		if (!df || df.fieldtype === 'Column Break' || df.fieldtype === 'Section Break') {
			return;
		}
		try {
			grid.update_docfield_property(df.fieldname, 'hidden', 0);
			grid.update_docfield_property(df.fieldname, 'in_list_view', 0);
		} catch (e) {
			/* ignore */
		}
	});
}

/** Emergency: clear hidden flags on shaft_jobs grid (recover from bad column apply). */
function spr_reset_shaft_jobs_grid_field_visibility(frm) {
	const grid = frm && frm.fields_dict && frm.fields_dict.shaft_jobs && frm.fields_dict.shaft_jobs.grid;
	if (!grid) {
		return;
	}
	(frappe.meta.get_docfields('Shaft Production Run Job') || []).forEach(function (df) {
		if (!df || df.fieldtype === 'Column Break' || df.fieldtype === 'Section Break') {
			return;
		}
		try {
			grid.update_docfield_property(df.fieldname, 'hidden', 0);
			grid.update_docfield_property(df.fieldname, 'in_list_view', 0);
		} catch (e) {
			/* ignore */
		}
	});
}

/** Mirror header docfield order/visibility onto each grid row (prevents refresh misalignment). */
function spr_mirror_grid_docfields_to_rows(grid) {
	const cg = spr_get_grid_columns_module();
	if (cg && typeof cg.mirror_grid_docfields_to_rows === 'function') {
		cg.mirror_grid_docfields_to_rows(grid);
		return;
	}
	if (!grid || !(grid.grid_rows || []).length) {
		return;
	}
	const masterOrder = (grid.docfields || []).map(function (df) {
		return df && df.fieldname;
	}).filter(Boolean);
	(grid.grid_rows || []).forEach(function (gr) {
		if (!gr) {
			return;
		}
		const rowByName = {};
		(gr.docfields || []).forEach(function (df) {
			if (df && df.fieldname) {
				rowByName[df.fieldname] = df;
			}
		});
		(grid.docfields || []).forEach(function (gdf) {
			if (!gdf || !gdf.fieldname) {
				return;
			}
			const rdf = rowByName[gdf.fieldname];
			if (rdf) {
				rdf.in_list_view = gdf.in_list_view;
				rdf.hidden = gdf.hidden;
			}
		});
		if (masterOrder.length && gr.docfields) {
			gr.docfields = spr_reorder_docfields_array(gr.docfields, masterOrder);
		}
	});
}

function spr_sync_meta_list_view_flags(metaDoctype, showSet) {
	if (!metaDoctype || !showSet) {
		return;
	}
	(frappe.meta.get_docfields(metaDoctype) || []).forEach(function (df) {
		if (!df || df.fieldtype === 'Column Break' || df.fieldtype === 'Section Break') {
			return;
		}
		const show = !!showSet[df.fieldname];
		df.in_list_view = show ? 1 : 0;
		df.hidden = 0;
	});
}

/** Safe access — child_grid_columns.js may not be loaded; bare `production_entry` throws ReferenceError. */
function spr_get_grid_columns_module() {
	try {
		const pe = typeof window !== 'undefined' && window.production_entry;
		if (pe && pe.grid_columns) {
			return pe.grid_columns;
		}
	} catch (e) {
		/* ignore */
	}
	return null;
}

function spr_get_field_dict(frm, fieldname) {
	return frm && frm.fields_dict && frm.fields_dict[fieldname];
}

function spr_reorder_docfields_array(docfields, preferredOrder) {
	const source = (docfields || []).slice();
	if (!source.length || !preferredOrder || !preferredOrder.length) {
		return source;
	}
	const byName = {};
	source.forEach(function (df) {
		if (df && df.fieldname) {
			byName[df.fieldname] = df;
		}
	});
	const ordered = [];
	const seen = {};
	preferredOrder.forEach(function (fn) {
		if (byName[fn] && !seen[fn]) {
			ordered.push(byName[fn]);
			seen[fn] = 1;
		}
	});
	source.forEach(function (df) {
		if (df && df.fieldname && !seen[df.fieldname]) {
			ordered.push(df);
			seen[df.fieldname] = 1;
		}
	});
	return ordered;
}

/** Re-apply cached column order without reset (Save Row / save refresh path). */
function spr_restore_cached_grid_columns(frm, fieldname) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.grid) {
		return false;
	}
	const grid = fd.grid;
	let order = grid._spr_desired_column_order;
	if (!order || !order.length) {
		if (fieldname === 'items') {
			const cfg = spr_get_items_list_view_config(frm);
			order = (cfg && cfg.show) || [];
		} else if (fieldname === 'shaft_jobs') {
			order = spr_build_shaft_jobs_show_list(frm) || [];
		}
	}
	if (!order || !order.length) {
		return false;
	}
	const showSet = {};
	order.forEach(function (fn) {
		showSet[fn] = 1;
	});
	spr_light_sync_grid_columns(grid, order, showSet, { refreshRows: false });
	spr_light_grid_scroll_sync(frm, fieldname);
	return true;
}

/** Sync header/body column order without grid.refresh() or DOM remount (SPR-safe). */
function spr_light_sync_grid_columns(grid, order, showSet, opts) {
	if (!grid || !order || !order.length) {
		return;
	}
	const settings = opts || {};
	const show = showSet || {};
	order.forEach(function (fn) {
		show[fn] = 1;
	});
	if ((grid.docfields || []).length) {
		grid.docfields = spr_reorder_docfields_array(grid.docfields, order);
	}
	(grid.docfields || []).forEach(function (df) {
		if (!df || !df.fieldname) {
			return;
		}
		const visible = !!show[df.fieldname];
		df.in_list_view = visible ? 1 : 0;
		df.hidden = 0;
	});
	(grid.grid_rows || []).forEach(function (gr) {
		if (!gr) {
			return;
		}
		if (gr.docfields && gr.docfields.length) {
			gr.docfields = spr_reorder_docfields_array(gr.docfields, order);
			gr.docfields.forEach(function (df) {
				if (!df || !df.fieldname) {
					return;
				}
				const visible = !!show[df.fieldname];
				df.in_list_view = visible ? 1 : 0;
				df.hidden = 0;
			});
		}
	});
	try {
		delete grid.visible_columns;
		delete grid.user_settings;
		grid.user_defined_columns = null;
	} catch (e) {
		/* ignore */
	}
	try {
		if (typeof grid.setup_visible_columns === 'function') {
			grid.setup_visible_columns();
		}
	} catch (e) {
		/* ignore */
	}
	try {
		spr_grid_run_without_paint_hook(grid, function () {
			if (typeof grid.refresh_header === 'function') {
				grid.refresh_header();
			}
		});
	} catch (e) {
		/* ignore */
	}
	spr_mirror_grid_docfields_to_rows(grid);
	if (!settings.refreshRows) {
		return;
	}
	try {
		(grid.grid_rows || []).forEach(function (gr) {
			if (gr && typeof gr.refresh === 'function') {
				gr.refresh();
			}
		});
	} catch (e) {
		/* ignore */
	}
}

function spr_light_realign_field(frm, fieldname) {
	const fd = spr_get_field_dict(frm, fieldname);
	if (!fd || !fd.grid) {
		return;
	}
	const grid = fd.grid;
	try {
		delete grid.visible_columns;
		delete grid.user_settings;
		grid.user_defined_columns = null;
	} catch (e) {
		/* ignore */
	}
	try {
		if (typeof grid.setup_visible_columns === 'function') {
			grid.setup_visible_columns();
		}
	} catch (e) {
		/* ignore */
	}
	try {
		if (typeof grid.refresh_header === 'function') {
			grid.refresh_header();
		}
	} catch (e) {
		/* ignore */
	}
	spr_sync_grid_header_body_scroll(fd);
}

/**
 * Force full header+body column realignment for a single grid.
 * Deletes cached visible_columns, re-runs setup + header + body refresh.
 */
function spr_force_grid_realign(frm, fieldname) {
	spr_light_realign_field(frm, fieldname);
	const fd = spr_get_field_dict(frm, fieldname);
	if (fd) {
		spr_ensure_child_grid_heights(frm);
		if (typeof requestAnimationFrame === 'function') {
			requestAnimationFrame(function () {
				spr_sync_grid_header_body_scroll(fd);
			});
		}
	}
}

/** Debounced column apply after grid refresh/render — skipped while operator is editing. */
function spr_schedule_grid_column_apply(frm, fieldname, delay) {
	if (!frm || !fieldname) {
		return;
	}
	const key = '_spr_col_apply_timer_' + fieldname;
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		if (!frm.fields_dict || !frm.fields_dict[fieldname] || !frm.fields_dict[fieldname].grid) {
			return;
		}
		if (fieldname === 'items' && spr_items_grid_is_editing(frm)) {
			return;
		}
		if (fieldname === 'items') {
			spr_apply_items_grid_columns(frm, true);
		} else if (fieldname === 'shaft_jobs') {
			spr_apply_shaft_jobs_grid_columns(frm, true);
		} else if (fieldname === 'bundle_calculation') {
			spr_apply_bundle_calculation_grid_columns(frm, true);
		}
		spr_ensure_child_grid_heights(frm);
		spr_light_grid_scroll_sync(frm, fieldname);
	}, delay != null ? delay : 40);
}

/** Debounced full realign — fixes header/body column collapse after save or browser refresh. */
function spr_schedule_grid_realign(frm, fieldname, delay) {
	if (!frm || !fieldname) {
		return;
	}
	const key = '_spr_realign_timer_' + fieldname;
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		if (!frm.fields_dict || !frm.fields_dict[fieldname] || !frm.fields_dict[fieldname].grid) {
			return;
		}
		spr_force_grid_realign(frm, fieldname);
	}, delay != null ? delay : 80);
}

function spr_set_grid_col_hidden(grid, fieldname, hidden) {
	if (!grid || !fieldname) {
		return;
	}
	const parentField = grid.df && grid.df.fieldname;
	if (parentField && SPR_GRID_SHOW_ALL_FIELDNAMES.indexOf(parentField) >= 0) {
		return;
	}
	// items / shaft_jobs: never hidden=1 — breaks header/body alignment and blanks columns.
	const useListViewOnly =
		parentField === 'items' || parentField === 'shaft_jobs';
	try {
		if (typeof grid.update_docfield_property !== 'function') {
			return;
		}
		if (useListViewOnly) {
			grid.update_docfield_property(fieldname, 'hidden', 0);
			grid.update_docfield_property(fieldname, 'in_list_view', hidden ? 0 : 1);
			return;
		}
		grid.update_docfield_property(fieldname, 'hidden', hidden ? 1 : 0);
	} catch (e) {
		/* ignore */
	}
}

/** Keep grid header horizontally aligned with body when DataTable scrolls or columns toggle. */
function spr_sync_grid_header_body_scroll(fd) {
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return;
	}
	const $w = fd.$wrapper;
	const $container = $w.find('.form-grid-container').first();
	const $head = $w.find('.grid-heading-row, .dt-row-header, .dt-header').first();
	if ($container.length && $head.length) {
		const sl = $container.scrollLeft() || 0;
		if (Math.abs(($head.scrollLeft() || 0) - sl) > 0.5) {
			$head.scrollLeft(sl);
		}
		return;
	}
	const $bodyScroll = $w.find('.dt-scrollable, .form-grid .grid-body').first();
	if (!$bodyScroll.length || !$head.length) {
		return;
	}
	const bodySl = $bodyScroll.scrollLeft() || 0;
	const headSl = $head.scrollLeft() || 0;
	if (Math.abs(headSl - bodySl) > 0.5) {
		const active = document.activeElement;
		const headScrolled = active && $head.has(active).length;
		if (headScrolled) {
			$bodyScroll.scrollLeft(headSl);
		} else {
			$head.scrollLeft(bodySl);
		}
	}
}

function spr_grid_list_view_config(frm, fieldname) {
	if (fieldname === 'shaft_jobs') {
		return { show: spr_build_shaft_jobs_show_list(frm), hide: [] };
	}
	if (fieldname === 'bundle_calculation' || fieldname === 'items') {
		return { show: [], hide: [] };
	}
	return { show: [], hide: [] };
}

/** Apply show/hide only for configured columns — do not hide every other field (breaks grid headers). */
function spr_apply_grid_list_view_config(grid, metaDoctype, cfg) {
	if (!grid || !cfg) {
		return;
	}
	(cfg.show || []).forEach(function (fn) {
		const df = frappe.meta.get_docfield(metaDoctype, fn);
		if (df) {
			df.in_list_view = 1;
		}
		try {
			grid.update_docfield_property(fn, 'hidden', 0);
			grid.update_docfield_property(fn, 'in_list_view', 1);
		} catch (e) {
			/* ignore */
		}
	});
	(cfg.hide || []).forEach(function (fn) {
		const df = frappe.meta.get_docfield(metaDoctype, fn);
		if (df) {
			df.in_list_view = 0;
		}
		try {
			grid.update_docfield_property(fn, 'hidden', 0);
			grid.update_docfield_property(fn, 'in_list_view', 0);
		} catch (e) {
			/* ignore */
		}
	});
}

/**
 * items + bundle_calculation: scroll sync only — never mutate columns (causes hang / blank grid).
 */
function spr_light_grid_scroll_sync(frm, fieldname) {
	const fd = frm && frm.fields_dict && frm.fields_dict[fieldname];
	if (fd) {
		spr_sync_grid_header_body_scroll(fd);
	}
}

function spr_show_all_grid_columns(frm, fieldname) {
	spr_light_grid_scroll_sync(frm, fieldname);
}

/**
 * Sync visible columns + header/body scroll — never grid.refresh() here (breaks headers / collapses rows).
 */
function spr_sync_grid_columns_visible(frm, fieldname) {
	if (fieldname === 'items') {
		if (spr_items_grid_is_editing(frm)) {
			const fd = spr_get_field_dict(frm, 'items');
			if (fd && fd.grid) {
				spr_mirror_grid_docfields_to_rows(fd.grid);
			}
			spr_light_grid_scroll_sync(frm, 'items');
			return;
		}
		spr_apply_items_grid_columns(frm);
		return;
	}
	if (fieldname === 'shaft_jobs') {
		spr_apply_shaft_jobs_grid_columns(frm);
		spr_ensure_child_grid_heights(frm);
		return;
	}
	if (fieldname === 'bundle_calculation') {
		spr_apply_bundle_calculation_grid_columns(frm);
		spr_ensure_child_grid_heights(frm);
		return;
	}
	const fd = frm && frm.fields_dict && frm.fields_dict[fieldname];
	if (!fd || !fd.grid) {
		return;
	}
	try {
		if (SPR_GRID_SHOW_ALL_FIELDNAMES.indexOf(fieldname) >= 0) {
			spr_light_grid_scroll_sync(frm, fieldname);
			return;
		}
	} catch (e) {
		/* ignore desk variants */
	}
}

function spr_apply_grid_wrap_classes(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	SPR_SPR_CHILD_TABLE_FIELDS.forEach(function (fn) {
		const fd = frm.fields_dict[fn];
		if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
			return;
		}
		fd.$wrapper.addClass('spr-grid-wrap');
		const isSubmitted = frm.doc && cint(frm.doc.docstatus) === 1;
		if (fn === 'items') {
			fd.$wrapper.addClass('spr-items-wrap');
			fd.$wrapper.toggleClass('spr-doc-submitted', isSubmitted);
		}
		if (fn === 'shaft_jobs') {
			fd.$wrapper.addClass('spr-shaft-jobs-wrap');
			fd.$wrapper.toggleClass('spr-doc-submitted', isSubmitted);
		}
		if (fn === 'bundle_calculation') {
			fd.$wrapper.addClass('spr-bundle-calc-wrap');
			fd.$wrapper.toggleClass('spr-doc-submitted', isSubmitted);
		}
	});
	spr_ensure_child_grid_heights(frm);
}

/** Prevent child grids from collapsing to a thin strip (Available Jobs + Roll lines). */
function spr_ensure_child_grid_heights(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	const isSubmitted = frm.doc && cint(frm.doc.docstatus) === 1;
	const minGrid = isSubmitted ? '200px' : '120px';
	const minRows = isSubmitted ? '88px' : '52px';
	const minRow = isSubmitted ? '42px' : '38px';
	['shaft_jobs', 'items', 'bundle_calculation'].forEach(function (fn) {
		const fd = frm.fields_dict[fn];
		if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
			return;
		}
		const $w = fd.$wrapper;
		$w.find('.form-grid-container, .form-grid').css({
			'min-height': minGrid,
			'overflow-x': 'auto',
			'max-width': '100%',
		});
		const $rows = $w.find('.grid-body .rows');
		if ($rows.length) {
			$rows.css({ 'min-height': minRows, display: 'block' });
		}
		$w.find('.grid-row').css({ 'min-height': minRow });
		$w.find('.grid-heading-row').css({ 'min-height': '32px' });
	});
}

/** Re-apply default columns + heights after refresh / PP load (debounced). */
function spr_clear_spr_grid_saved_columns(grid, metaDoctype) {
	if (!grid || grid._spr_columns_user_locked) {
		return;
	}
	try {
		delete grid.user_settings;
		grid.user_defined_columns = null;
		if (grid.visible_columns) {
			delete grid.visible_columns;
		}
		const key = frappe.scrub(metaDoctype);
		const us = frappe.model.user_settings && frappe.model.user_settings[key];
		if (us && us.GridView) {
			delete us.GridView;
		}
	} catch (e) {
		/* ignore */
	}
}

function spr_stabilize_spr_child_grids(frm, opts) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	if (spr_is_submitted_spr(frm)) {
		spr_stabilize_submitted_spr_grids_once(frm);
		return;
	}
	const settings = opts || {};
	const run = function () {
		if (!frm.fields_dict || spr_should_block_grid_realign(frm)) {
			return;
		}
		spr_apply_grid_wrap_classes(frm);
		if (settings.light) {
			spr_apply_spr_child_grid_min_widths(frm);
			['shaft_jobs', 'items', 'bundle_calculation'].forEach(function (fn) {
				spr_sync_grid_columns_visible(frm, fn);
				spr_light_grid_scroll_sync(frm, fn);
			});
			spr_refresh_draft_child_grids_light(frm);
			spr_enforce_roll_line_grid_policy(frm);
			if (!settings.skipRowStyles) {
				spr_reapply_item_row_styles_with_retries(frm, [80, 280]);
			}
			return;
		}
		if (!sprIsBundlePackagingMode(frm)) {
			spr_apply_shaft_jobs_grid_columns(frm, true);
		}
		spr_apply_items_grid_columns(frm, true);
		if (sprIsBundlePackagingMode(frm)) {
			spr_apply_bundle_calculation_grid_columns(frm, true);
		}
		spr_apply_create_entry_buttons_ui(frm);
		spr_ensure_child_grid_heights(frm);
		spr_apply_spr_child_grid_min_widths(frm);
		['shaft_jobs', 'items', 'bundle_calculation'].forEach(function (fn) {
			spr_light_grid_scroll_sync(frm, fn);
		});
		spr_enforce_roll_line_grid_policy(frm);
		spr_reapply_item_row_styles_with_retries(frm, [80, 280, 650]);
	};
	if (settings.immediate) {
		run();
		return;
	}
	const key = '_spr_stabilize_timer';
	if (frm[key]) {
		clearTimeout(frm[key]);
	}
	frm[key] = setTimeout(function () {
		frm[key] = null;
		run();
	}, settings.delay != null ? settings.delay : 220);
}

/** Lightweight grid pass after save / reopen — repair only when misaligned (no full rebuild). */
function spr_refresh_draft_child_grids_light(frm) {
	if (!frm || !frm.fields_dict || spr_is_submitted_spr(frm)) {
		return;
	}
	spr_apply_grid_wrap_classes(frm);
	spr_apply_spr_child_grid_min_widths(frm);
	frm._spr_allow_save_row_align = true;
	try {
		['items', 'shaft_jobs'].forEach(function (fn) {
			if (spr_child_grid_needs_repair(frm, fn)) {
				spr_repair_child_grid_alignment(frm, fn);
			} else {
				spr_light_grid_scroll_sync(frm, fn);
			}
		});
	} finally {
		frm._spr_allow_save_row_align = false;
	}
	if (!spr_items_grid_is_editing(frm)) {
		apply_spr_item_row_styles(frm);
	}
}

/** One-shot column sync after open / refresh — light touch, no grid.refresh(). */
function spr_layout_all_grids(frm, opts) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	const settings = opts || {};
	spr_apply_grid_wrap_classes(frm);
	ensure_spr_item_stylesheet();
	if (settings.toggleUi !== false) {
		sprToggleSheetCuttingUi(frm);
	} else {
		SPR_SPR_CHILD_TABLE_FIELDS.forEach(function (fn) {
			spr_sync_grid_columns_visible(frm, fn);
		});
	}
	if (settings.styles !== false) {
		const itemsFd = spr_get_field_dict(frm, 'items');
		if (itemsFd && itemsFd.grid) {
			apply_spr_item_row_styles(frm);
		}
	}
}

/** Debounced GSM row colours + light column sync (safe after save / create entry / grid render). */
function spr_schedule_grid_ui_debounced(frm, opts) {
	if (!frm) {
		return;
	}
	if (spr_items_grid_is_editing(frm)) {
		frm._spr_row_styles_pending = true;
		return;
	}
	const settings = opts || {};
	if (frm._spr_ui_debounce_timer) {
		clearTimeout(frm._spr_ui_debounce_timer);
	}
	frm._spr_ui_debounce_timer = setTimeout(function () {
		frm._spr_ui_debounce_timer = null;
		if (!frm.fields_dict || !frm.fields_dict.items) {
			return;
		}
		if (settings.styles !== false) {
			sprEnsureItemsGridObserver(frm);
			try { spr_apply_items_row_lock_ui(frm); } catch (e) {}
			apply_spr_item_row_styles(frm);
		}
		if (settings.columns !== false) {
			SPR_SPR_CHILD_TABLE_FIELDS.forEach(function (fn) {
				spr_sync_grid_columns_visible(frm, fn);
			});
		}
	}, settings.delay != null ? settings.delay : 220);
}

function spr_after_child_table_refresh(frm) {
	spr_schedule_grid_ui_debounced(frm, { delay: 380, columns: false });
	if (typeof requestAnimationFrame === 'function') {
		requestAnimationFrame(function () {
			requestAnimationFrame(function () {
				if (!frm || !frm.fields_dict) {
					return;
				}
				if (spr_items_grid_is_editing(frm)) {
					return;
				}
				if (spr_is_submitted_spr(frm)) {
					spr_apply_items_grid_columns(frm, true);
					spr_apply_shaft_jobs_grid_columns(frm, true);
					if (sprIsBundlePackagingMode(frm)) {
						spr_apply_bundle_calculation_grid_columns(frm, true);
					}
					spr_ensure_child_grid_heights(frm);
				} else {
					spr_refresh_draft_child_grids_light(frm);
					spr_enforce_roll_line_grid_policy(frm);
				}
				['items', 'shaft_jobs', 'bundle_calculation'].forEach(function (fn) {
					spr_light_grid_scroll_sync(frm, fn);
				});
			});
		});
	}
}

/** When PP / operator selects a unit, default the matching process checkbox on (still user-clearable). */
function sprApplyLaminationUnitDefaults(frm, unitVal) {
	if (!frm || !frm.doc) {
		return;
	}
	const u = (unitVal != null ? String(unitVal) : String(frm.doc.custom_unit || '')).trim();
	const meta = frappe.meta;
	const hasField = (f) => !!meta.get_docfield('Shaft Production Run', f);
	if (u === 'TNSPL - LAMINATION UNIT' || u === 'Lamination Unit') {
		if (hasField('custom_is_lamination') && !cint(frm.doc.custom_is_lamination)) {
			frm.set_value('custom_is_lamination', 1);
		}
	} else if (u === 'TSNPL - L3 REWINDING MACHINE' || u === 'JSB - L4 REWINDING MACHINE' || u === 'JSB - L5 REWINDING MACHINE') {
		if (hasField('custom_is_rewinding') && !cint(frm.doc.custom_is_rewinding)) {
			frm.set_value('custom_is_rewinding', 1);
		}
	} else if (u === 'JVE - SHEET CUTTING MACHINE') {
		if (hasField('custom_is_sheet_cutting') && !cint(frm.doc.custom_is_sheet_cutting)) {
			frm.set_value('custom_is_sheet_cutting', 1);
		}
	} else if (u === 'VR - 1200MM BOPP PRINTING MACHINE') {
		if (hasField('custom_is_bopp_film') && !cint(frm.doc.custom_is_bopp_film)) {
			frm.set_value('custom_is_bopp_film', 1);
		}
	}
}

function sprSumProducedLengthMeters(it) {
	if (!it) {
		return 0;
	}
	const aliases = [
		'produced_length_mtrs',
		'custom_produced_length_mtrs',
	];
	for (let i = 0; i < aliases.length; i++) {
		const v = flt(it[aliases[i]]);
		if (v > 0) {
			return v;
		}
	}
	return 0;
}

const SPR_LAM_GSM_SUFFIX_MAP = {
	A: 10,
	B: 12,
	B1: 13,
	C: 15,
	D: 20,
	E: 30,
	F: 13,
};

function sprRollProcessPrefix(frm) {
	if (!frm || !frm.doc) {
		return '';
	}
	const rows = frm.doc.items || [];
	for (let i = 0; i < rows.length; i += 1) {
		const prefix = spr_item_process_prefix(rows[i] && rows[i].item_code);
		if (prefix) {
			return prefix;
		}
	}
	return '';
}

function sprStickerGsmFromItemCode(itemCode) {
	const code = ((itemCode || '') + '').trim();
	// Printed BOPP (PB-*) does not carry sticker GSM in numeric process-code slots.
	// Avoid deriving bogus GSM like "5" from arbitrary substrings.
	if (!code || code.toUpperCase().startsWith('PB')) {
		return 0;
	}
	// Parse only numeric process-style item codes (e.g. 104xxx..., 102xxx..., 251xxx...).
	if (!/^\d+$/.test(code)) {
		return 0;
	}
	if (code.length < 12) {
		return 0;
	}
	const n = parseInt(code.substring(9, 12), 10);
	return !isNaN(n) && n > 0 ? n : 0;
}

function sprWidthInchFromItemCode(itemCode) {
	const code = String(itemCode || '').trim();
	if (!/^\d+$/.test(code) || code.length < 16) {
		return 0;
	}
	const mm = parseInt(code.substring(12, 16), 10);
	if (isNaN(mm) || mm <= 0) {
		return 0;
	}
	return Math.round(Math.round((mm / 25.4) * 2) / 2 * 10) / 10;
}

function sprParseWidthFromItemName(itemName) {
	const text = String(itemName || '');
	let m = text.match(/W\s*-\s*(\d+(?:\.\d+)?)/i);
	if (m) {
		return flt(m[1]);
	}
	m = text.match(/(\d+(?:\.\d+)?)\s*(?:''|"|″)/);
	if (m) {
		return flt(m[1]);
	}
	return 0;
}

function sprResolveMixRollPlannedLengthMeters(doc) {
	const aliases = [
		'meter_roll',
		'meter_roll_mtrs',
		'custom_meter_roll_mtrs',
		'ordered_length',
		'ordered_length_mtrs',
		'custom_ordered_length',
		'roll_mtrs',
		'roll',
	];
	for (let i = 0; i < aliases.length; i++) {
		const v = flt(doc[aliases[i]]);
		if (v > 0) {
			return v;
		}
	}
	return 0;
}

function sprResolveMixRollProducedLengthMeters(doc) {
	const aliases = ['produced_length_mtrs', 'custom_produced_length_mtrs'];
	for (let i = 0; i < aliases.length; i++) {
		const v = flt(doc[aliases[i]]);
		if (v > 0) {
			return v;
		}
	}
	return 0;
}

function sprResolveLengthMetersForProducedGsm(frm, row) {
	if (frm && frm.doc && cint(frm.doc.is_mix_roll)) {
		return sprResolveMixRollProducedLengthMeters(row);
	}
	return sprResolveLengthMeters(row);
}

function sprComputeMixRollPlannedQtyKg(row) {
	const gsm =
		sprStickerGsmFromItemCode(row.item_code) ||
		flt(row.gsm) ||
		flt(row.custom_sticker_gsm) ||
		flt(row.sticker_gsm);
	const wi = flt(row.width_inch) || sprWidthInchFromItemCode(row.item_code);
	const mr = sprResolveMixRollPlannedLengthMeters(row);
	if (gsm > 0 && wi > 0 && mr > 0) {
		return Math.round(((gsm * wi * mr * 0.0254) / 1000) * 100) / 100;
	}
	return 0;
}

function spr_update_mix_roll_planned_qty(frm, cdt, cdn) {
	if (!frm || !frm.doc || !cint(frm.doc.is_mix_roll)) {
		return;
	}
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	const pq = sprComputeMixRollPlannedQtyKg(row);
	if (flt(row.planned_qty) !== pq) {
		frappe.model.set_value(cdt, cdn, 'planned_qty', pq);
	}
}

function spr_apply_mix_roll_planned_qty(frm) {
	if (!frm || !frm.doc || !cint(frm.doc.is_mix_roll)) {
		return;
	}
	(frm.doc.items || []).forEach(function (row) {
		if (!row || !row.name) {
			return;
		}
		const pq = sprComputeMixRollPlannedQtyKg(row);
		if (flt(row.planned_qty) !== pq) {
			frappe.model.set_value(row.doctype, row.name, 'planned_qty', pq);
		}
	});
}

function spr_is_meaningful_roll_row(row) {
	if (!row) {
		return false;
	}
	if (String((row.item_code || '')).trim()) {
		return true;
	}
	if (String((row.job || '')).trim() && (flt(row.gross_weight) > 0 || flt(row.net_weight) > 0)) {
		return true;
	}
	if (String((row.batch_no || '')).trim()) {
		return true;
	}
	return false;
}

function spr_is_blank_roll_row(row) {
	if (!row || spr_is_meaningful_roll_row(row)) {
		return false;
	}
	return !(
		flt(row.gross_weight) > 0 ||
		flt(row.net_weight) > 0 ||
		flt(row.planned_qty) > 0 ||
		String(row.batch_no || '').trim()
	);
}

function spr_normalize_gross_weight_input(val) {
	if (val === undefined || val === null || val === '') {
		return 0;
	}
	if (typeof val === 'number') {
		return flt(val);
	}
	let s = String(val).trim().replace(/,/g, '');
	// Paste from Excel / clipboard: use first numeric token only.
	const firstNum = s.match(/-?\d+(?:\.\d+)?/);
	if (firstNum) {
		s = firstNum[0];
	}
	// Grid editor glitch: duplicated fragment e.g. 23.4023.40 → 23.40
	const dup = s.match(/^(\d+\.\d{1,4})\1+$/);
	if (dup) {
		s = dup[1];
	}
	// Two decimals glued: 36.4036.40 → 36.40
	const glued = s.match(/^(\d+\.\d{1,4})(\d+\.\d{1,4})$/);
	if (glued && glued[1] === glued[2]) {
		s = glued[1];
	}
	return flt(s);
}

/** When gross weight is cleared, zero net weight + produced GSM (works during inline grid edit). */
function spr_refresh_grid_row_cells(frm, cdn, fieldnames) {
	const grid = frm.fields_dict && frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || !cdn) {
		return;
	}
	const gr = grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];
	if (!gr) {
		return;
	}
	(fieldnames || []).forEach(function (fn) {
		if (typeof gr.refresh_field === 'function') {
			try {
				gr.refresh_field(fn);
			} catch (e) {
				/* ignore */
			}
		}
	});
}

function spr_clear_roll_weight_dependents(frm, cdt, cdn) {
	const row = locals[cdt] && locals[cdt][cdn];
	if (!row) {
		return;
	}
	if (spr_normalize_gross_weight_input(row.gross_weight) > 0) {
		return;
	}
	row.net_weight = 0;
	row.produced_gsm = 0;
	spr_refresh_grid_row_cells(frm, cdn, ['net_weight', 'produced_gsm']);
	schedule_spr_item_row_styles(frm);
	update_shaft_job_achieved_from_items(frm);
	sprScheduleTotalProducedSync(frm);
	const persist = function () {
		const r = locals[cdt] && locals[cdt][cdn];
		if (!r || spr_normalize_gross_weight_input(r.gross_weight) > 0) {
			return;
		}
		if (flt(r.net_weight) !== 0) {
			frappe.model.set_value(cdt, cdn, 'net_weight', 0);
		}
		if (flt(r.produced_gsm) !== 0) {
			frappe.model.set_value(cdt, cdn, 'produced_gsm', 0);
		}
		spr_refresh_grid_row_cells(frm, cdn, ['net_weight', 'produced_gsm']);
		update_shaft_job_achieved_from_items(frm);
		sprScheduleTotalProducedSync(frm);
		schedule_spr_item_row_styles(frm);
	};
	if (spr_items_grid_is_editing(frm)) {
		if (!frm._spr_gw_clear_timers) {
			frm._spr_gw_clear_timers = {};
		}
		if (frm._spr_gw_clear_timers[cdn]) {
			clearTimeout(frm._spr_gw_clear_timers[cdn]);
		}
		frm._spr_gw_clear_timers[cdn] = setTimeout(function () {
			delete frm._spr_gw_clear_timers[cdn];
			persist();
		}, 280);
		return;
	}
	persist();
}

function spr_flush_deferred_grid_side_effects(frm) {
	if (!frm) {
		return;
	}
	if (frm._spr_job_achieved_pending) {
		frm._spr_job_achieved_pending = false;
		update_shaft_job_achieved_from_items(frm, { force: true });
	}
	if (frm._spr_row_styles_pending) {
		frm._spr_row_styles_pending = false;
		apply_spr_item_row_styles(frm);
	}
}

function spr_run_programmatic_item_adds(frm, fn) {
	if (!frm) {
		return fn && fn();
	}
	frm._spr_programmatic_item_adds = cint(frm._spr_programmatic_item_adds) + 1;
	try {
		return fn && fn();
	} finally {
		frm._spr_programmatic_item_adds = Math.max(0, cint(frm._spr_programmatic_item_adds) - 1);
	}
}

function spr_sync_items_add_row_ui(frm, blockAdd) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) {
		return;
	}
	const blocked = !!blockAdd;
	if (grid._spr_add_row_blocked === blocked) {
		if (grid.wrapper && grid.wrapper.length) {
			grid.wrapper.find('.grid-add-row, .grid-add-multiple-rows').toggle(!blocked);
		}
		grid.cannot_add_rows = blocked;
		if (grid.df) {
			grid.df.cannot_add_rows = blocked ? 1 : 0;
		}
		return;
	}
	grid._spr_add_row_blocked = blocked;
	if (spr_items_grid_is_editing(frm)) {
		grid.cannot_add_rows = blocked;
		if (grid.df) {
			grid.df.cannot_add_rows = blocked ? 1 : 0;
		}
		if (grid.wrapper && grid.wrapper.length) {
			grid.wrapper.find('.grid-add-row, .grid-add-multiple-rows').toggle(!blocked);
		}
		return;
	}
	try {
		frm.set_df_property('items', 'cannot_add_rows', blocked ? 1 : 0);
	} catch (e) {
		/* ignore */
	}
	grid.cannot_add_rows = blocked;
	if (grid.df) {
		grid.df.cannot_add_rows = blocked ? 1 : 0;
	}
	if (grid.wrapper && grid.wrapper.length) {
		grid.wrapper.find('.grid-add-row, .grid-add-multiple-rows').toggle(!blocked);
	}
}

function spr_should_block_item_row_autocreate(frm) {
	if (!frm || cint(frm._spr_programmatic_item_adds) > 0) {
		return false;
	}
	return spr_should_block_manual_item_rows(frm);
}

function spr_get_items_grid_focus_cdn(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) {
		return null;
	}
	if (grid.grid_form && grid.grid_form.doc && grid.grid_form.doc.name) {
		return grid.grid_form.doc.name;
	}
	const wrap = grid.wrapper && grid.wrapper[0];
	if (!wrap) {
		return null;
	}
	const active = document.activeElement;
	if (!active || !wrap.contains(active)) {
		return null;
	}
	const rowEl = active.closest && active.closest('.grid-row');
	if (!rowEl) {
		return null;
	}
	return rowEl.getAttribute('data-name') || rowEl.getAttribute('data-docname') || null;
}

/** Resolve child row name from a grid Button click (Frappe 16 DataTable often has no data-docname on rows). */
function spr_cdn_from_items_grid_button($btn, frm) {
	if (!$btn || !$btn.length || !frm || !frm.doc) {
		return '';
	}
	const cdt = SPR_SPI_DOCTYPE;
	const items = frm.doc.items || [];
	const $domRows = sprGetItemsDatatableBodyRows(frm);
	if ($domRows && $domRows.length) {
		for (let i = 0; i < $domRows.length; i++) {
			const node = $domRows.get(i);
			if (node && $.contains(node, $btn[0]) && items[i] && items[i].name) {
				return items[i].name;
			}
		}
		const $clicked = $btn.closest('.dt-row, .grid-row, tbody tr[data-idx]').not('.grid-form-row, .dt-row-filter');
		if ($clicked.length) {
			const idx = $domRows.index($clicked);
			if (idx >= 0 && items[idx] && items[idx].name) {
				return items[idx].name;
			}
		}
	}
	const $row = $btn.closest('.grid-row, .dt-row, tbody tr[data-idx], .editable-row').not('.grid-form-row, .dt-row-filter');
	if ($row.length) {
		const fromAttr = $row.attr('data-docname') || $row.attr('data-name');
		if (fromAttr && locals[cdt] && locals[cdt][fromAttr]) {
			return fromAttr;
		}
	}
	return '';
}

function spr_resolve_items_row_cdn(frm, cdt, cdn) {
	cdt = cdt || SPR_SPI_DOCTYPE;
	const fromBtn = frm && frm._spr_row_action_cdn;
	if (fromBtn && locals[cdt] && locals[cdt][fromBtn]) {
		return fromBtn;
	}
	if (cdn && locals[cdt] && locals[cdt][cdn]) {
		return cdn;
	}
	const fromFocus = spr_get_items_grid_focus_cdn(frm);
	if (fromFocus && locals[cdt] && locals[cdt][fromFocus]) {
		return fromFocus;
	}
	return cdn || '';
}

function spr_invoke_items_row_action(frm, action, cdn) {
	if (!frm || !cdn) {
		return;
	}
	frm._spr_row_action_cdn = cdn;
	try {
		if (frm.script_manager) {
			frm.script_manager.trigger(action, SPR_SPI_DOCTYPE, cdn);
		} else {
			let handlers = frappe.ui.form.handlers && frappe.ui.form.handlers[SPR_SPI_DOCTYPE] && frappe.ui.form.handlers[SPR_SPI_DOCTYPE][action];
			if (Array.isArray(handlers)) {
				handlers.forEach(function (h) { if (typeof h === 'function') h(frm, SPR_SPI_DOCTYPE, cdn); });
			} else if (typeof handlers === 'function') {
				handlers(frm, SPR_SPI_DOCTYPE, cdn);
			}
		}
	} finally {
		setTimeout(function () {
			if (frm) {
				delete frm._spr_row_action_cdn;
			}
		}, 0);
	}
}

/** Capture Save Row / Edit Row / Label clicks before Frappe (wrong cdn on row 2+). */
function spr_install_items_row_action_handlers(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || !grid.wrapper || !grid.wrapper.length || grid._spr_row_action_handlers_installed) {
		return;
	}
	grid._spr_row_action_handlers_installed = true;
	const wrapEl = grid.wrapper[0];
	const selector =
		'[data-fieldname="save_row"] button,' +
		'[data-fieldname="edit_row"] button,' +
		'[data-fieldname="print_sticker"] button,' +
		'[data-fieldname="custom_production_label"] button,' +
		'[data-fieldname="custom_qc_approval_label"] button,' +
		'[data-fieldname="custom_approval_label"] button';
	const fieldToAction = {
		save_row: 'save_row',
		edit_row: 'edit_row',
		print_sticker: 'print_sticker',
		custom_production_label: 'print_sticker',
		custom_qc_approval_label: 'custom_qc_approval_label',
		custom_approval_label: 'custom_qc_approval_label',
	};
	wrapEl.addEventListener(
		'click',
		function (e) {
			const btn = e.target && e.target.closest ? e.target.closest(selector) : null;
			if (!btn) {
				return;
			}
			const cell = btn.closest('[data-fieldname]');
			const fieldname = cell && cell.getAttribute('data-fieldname');
			const action = fieldname && fieldToAction[fieldname];
			if (!action) {
				return;
			}
			const cdn = spr_cdn_from_items_grid_button($(btn), frm);
			if (!cdn) {
				return;
			}
			e.preventDefault();
			e.stopPropagation();
			e.stopImmediatePropagation();
			spr_invoke_items_row_action(frm, action, cdn);
		},
		true
	);
}

function spr_install_items_row_action_cdn_capture(frm) {
	spr_install_items_row_action_handlers(frm);
}

function spr_after_items_row_lock_doc_save(frm, alertMsg, alertIndicator) {
	if (!frm) {
		return;
	}
	frm.refresh_field('items');
	spr_schedule_item_row_styles_after_doc_write(frm);
	spr_schedule_items_align_after_save_row(frm);
	[0, 50, 200, 500].forEach(function (ms) {
		setTimeout(function () {
			if (!frm || !frm.fields_dict) {
				return;
			}
			spr_apply_items_row_lock_ui(frm);
			apply_spr_item_row_styles(frm);
		}, ms);
	});
	if (alertMsg) {
		frappe.show_alert({ message: alertMsg, indicator: alertIndicator || 'green' });
	}
}

/** Block Frappe auto-adding a blank row when tabbing out of the last roll line. */
function spr_install_items_grid_row_add_block(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || grid._spr_row_add_block_installed) {
		return;
	}
	grid._spr_row_add_block_installed = true;
	['add_new_row', 'add_multiple_rows'].forEach(function (method) {
		if (typeof grid[method] !== 'function' || grid['_spr_orig_' + method]) {
			return;
		}
		grid['_spr_orig_' + method] = grid[method];
		grid[method] = function () {
			if (spr_should_block_item_row_autocreate(frm)) {
				return;
			}
			return grid['_spr_orig_' + method].apply(grid, arguments);
		};
	});
	spr_sync_items_add_row_ui(frm, spr_should_block_item_row_autocreate(frm));
}

function spr_should_block_manual_item_rows(frm) {
	if (!frm || !frm.doc || cint(frm.doc.docstatus) !== 0) {
		return false;
	}
	if (cint(frm.doc.is_mix_roll)) {
		return false;
	}
	return (frm.doc.shaft_jobs || []).length > 0;
}

function spr_prune_trailing_blank_roll_rows(frm) {
	if (!frm || !frm.doc || cint(frm.doc.docstatus) !== 0) {
		return 0;
	}
	if (spr_items_grid_is_editing(frm)) {
		return 0;
	}
	const items = frm.doc.items || [];
	if (!items.length) {
		return 0;
	}
	const focusCdn = spr_get_items_grid_focus_cdn(frm);
	let removed = 0;
	for (let i = items.length - 1; i >= 0; i--) {
		if (!spr_is_blank_roll_row(items[i])) {
			break;
		}
		const row = items[i];
		if (focusCdn && row && row.name === focusCdn) {
			break;
		}
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (grid && row && row.name && grid.grid_rows_by_docname && grid.grid_rows_by_docname[row.name]) {
			grid.grid_rows_by_docname[row.name].remove();
		} else if (grid && grid.grid_rows && grid.grid_rows[i]) {
			grid.grid_rows[i].remove();
		} else {
			items.splice(i, 1);
		}
		removed++;
	}
	if (removed > 0) {
		spr_sync_no_of_rolls_created(frm, { silent: true });
	}
	return removed;
}

function spr_enforce_roll_line_grid_policy(frm, opts) {
	const settings = opts || {};
	if (settings.force !== true && spr_items_grid_is_editing(frm)) {
		frm._spr_roll_policy_pending = true;
		return;
	}
	frm._spr_roll_policy_pending = false;
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || !frm || !frm.doc || cint(frm.doc.docstatus) !== 0) {
		return;
	}
	spr_prune_trailing_blank_roll_rows(frm);
	if (spr_should_block_manual_item_rows(frm)) {
		spr_install_items_grid_row_add_block(frm);
		spr_sync_items_add_row_ui(frm, true);
		spr_prune_trailing_blank_roll_rows(frm);
		return;
	}
	const jobs = frm.doc.shaft_jobs || [];
	if (!jobs.length) {
		spr_sync_items_add_row_ui(frm, false);
		return;
	}
	let blockAdd = true;
	jobs.forEach(function (jobRow) {
		const jid = String(jobRow.job_id || jobRow.job_no || '').trim();
		if (!jid) {
			return;
		}
		const maxRolls = sprJobMaxRollLines(jobRow, frm);
		const curRolls = sprCountRollLinesForJob(frm, jid);
		if (maxRolls <= 0 || curRolls < maxRolls) {
			blockAdd = false;
		}
	});
	spr_sync_items_add_row_ui(frm, blockAdd);
}

function spr_reject_unauthorized_blank_item_row(frm, cdt, cdn) {
	if (!frm || !cdt || !cdn) {
		return false;
	}
	if (cint(frm._spr_programmatic_item_adds) > 0 || !spr_should_block_manual_item_rows(frm)) {
		return false;
	}
	const row = locals[cdt] && locals[cdt][cdn];
	if (!row || !spr_is_blank_roll_row(row)) {
		return false;
	}
	setTimeout(function () {
		if (!frm || !frm.fields_dict) {
			return;
		}
		const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
		if (grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn]) {
			grid.grid_rows_by_docname[cdn].remove();
		} else {
			frappe.model.clear_doc(cdt, cdn);
		}
		spr_prune_trailing_blank_roll_rows(frm);
	}, 0);
	return true;
}

function spr_debounced_enforce_roll_line_grid_policy(frm) {
	if (!frm || spr_items_grid_is_editing(frm)) {
		if (frm) {
			frm._spr_roll_policy_pending = true;
		}
		return;
	}
	if (frm._spr_roll_policy_timer) {
		clearTimeout(frm._spr_roll_policy_timer);
	}
	frm._spr_roll_policy_timer = setTimeout(function () {
		frm._spr_roll_policy_timer = null;
		if (spr_items_grid_is_editing(frm)) {
			frm._spr_roll_policy_pending = true;
			return;
		}
		spr_enforce_roll_line_grid_policy(frm);
	}, 120);
}

function spr_count_created_roll_lines(frm) {
	if (!frm || !frm.doc) {
		return 0;
	}
	return (frm.doc.items || []).filter((r) => spr_is_meaningful_roll_row(r)).length;
}

function spr_sync_no_of_rolls_created(frm, opts) {
	if (!frm || !frm.doc) {
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		return;
	}
	if (!frappe.meta.get_docfield('Shaft Production Run', 'custom_no_of_rolls_created')) {
		return;
	}
	const settings = opts || {};
	const cnt = spr_count_created_roll_lines(frm);
	const cur = cint(frm.doc.custom_no_of_rolls_created || 0);
	if (cur === cnt) {
		return;
	}
	if (settings.silent) {
		frm.doc.custom_no_of_rolls_created = cnt;
		frm.refresh_field('custom_no_of_rolls_created');
	} else {
		frm.set_value('custom_no_of_rolls_created', cnt);
	}
}

/** Dashboard alert when wastage/recycle exists but Roll Production Results is empty. */
function spr_warn_patty_without_rolls(frm) {
	if (!frm || !frm.doc || !frm.dashboard || typeof frm.dashboard.set_headline_alert !== 'function') {
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		return;
	}
	const rollCount = typeof spr_count_created_roll_lines === 'function' ? spr_count_created_roll_lines(frm) : 0;
	if (rollCount > 0) {
		return;
	}
	const wastageField = ['custom_running_patty_wastage', 'running_patty_wastage', 'wastage_details', 'custom_wastage_details'].find(
		(f) => frm.fields_dict[f]
	);
	const recycleField = ['custom_recycled_wastage_details', 'recycled_wastage_details'].find((f) => frm.fields_dict[f]);
	const pattyRows = wastageField ? frm.doc[wastageField] || [] : [];
	const recycleRows = recycleField ? frm.doc[recycleField] || [] : [];
	const hasPattyQty = pattyRows.some((r) => {
		const qty = flt(r.wastage_qty || r.wastage || r.net_wastage || 0);
		return qty > 0;
	});
	if (!hasPattyQty && !(recycleRows || []).length) {
		return;
	}
	if (frm._spr_patty_no_rolls_alert_shown === frm.doc.name) {
		return;
	}
	frm._spr_patty_no_rolls_alert_shown = frm.doc.name;
	frm.dashboard.set_headline_alert(
		__(
			'Running patty / recycle is filled but there are no produced rolls on this SPR. Save Row from GSM Production Entry (or Create Entry) so rolls appear here before submit.'
		),
		'orange'
	);
}

function spr_should_skip_desk_auto_sync(frm) {
	if (!frm) {
		return false;
	}
	return frm._spr_just_saved && Date.now() - frm._spr_just_saved < 8000;
}

function spr_mark_just_saved(frm) {
	if (!frm) {
		return;
	}
	frm._spr_just_saved = Date.now();
}

/**
 * After every roll entry / Save Row / wastage-core client sync, keep draft SPR Saved.
 * Debounced so rapid Create Entry / GSM updates do not stack saves.
 */
function spr_auto_save_draft_if_dirty(frm, opts) {
	opts = opts || {};
	if (!frm || !frm.doc || frm.is_new() || cint(frm.doc.docstatus) !== 0) {
		return;
	}
	// Avoid save↔refresh loops when client scripts re-touch grids right after a successful save
	if (opts.skipIfJustSaved && spr_should_skip_desk_auto_sync(frm)) {
		return;
	}
	if (frm._spr_create_entry_in_progress || frm.__spr_wastage_repairing) {
		const retryDelay = Math.max(cint(opts.delay) || 1200, 1500);
		if (frm.__spr_auto_save_timer) {
			clearTimeout(frm.__spr_auto_save_timer);
		}
		frm.__spr_auto_save_timer = setTimeout(function () {
			frm.__spr_auto_save_timer = null;
			spr_auto_save_draft_if_dirty(frm, opts);
		}, retryDelay);
		return;
	}
	const delay = opts.delay != null ? cint(opts.delay) : 1200;
	if (frm.__spr_auto_save_timer) {
		clearTimeout(frm.__spr_auto_save_timer);
	}
	frm.__spr_auto_save_timer = setTimeout(function () {
		frm.__spr_auto_save_timer = null;
		if (!frm || !frm.doc || frm.is_new() || cint(frm.doc.docstatus) !== 0) {
			return;
		}
		if (frm.__spr_auto_save_in_progress || frm._spr_create_entry_in_progress) {
			return;
		}
		if (typeof frm.is_dirty !== 'function' || !frm.is_dirty()) {
			return;
		}
		if (!opts.force && typeof spr_items_grid_is_editing === 'function' && spr_items_grid_is_editing(frm)) {
			spr_auto_save_draft_if_dirty(frm, $.extend({}, opts, { delay: 900 }));
			return;
		}
		frm.__spr_auto_save_in_progress = true;
		const quiet = !!opts.quiet;
		const p = frm.save();
		const doneOk = function () {
			frm.__spr_auto_save_in_progress = false;
			spr_mark_just_saved(frm);
			if (!quiet) {
				frappe.show_alert({ message: __('Shaft Production Run saved'), indicator: 'green' }, 2);
			}
			try {
				spr_schedule_grid_ui_debounced(frm, { delay: 400 });
			} catch (e) {
				/* ignore */
			}
		};
		const doneErr = function (err) {
			frm.__spr_auto_save_in_progress = false;
			if (opts.silentFail) {
				return;
			}
			frappe.msgprint({
				title: __('Save failed'),
				indicator: 'red',
				message: (err && err.message) || __('Could not auto-save Shaft Production Run after roll entry.'),
			});
		};
		if (p && typeof p.then === 'function') {
			p.then(doneOk).catch(doneErr);
		} else {
			frm.__spr_auto_save_in_progress = false;
		}
	}, delay);
}

if (typeof window !== 'undefined') {
	window.production_entry = window.production_entry || {};
	window.production_entry.spr_auto_save_draft_if_dirty = spr_auto_save_draft_if_dirty;
}

function sprAutoSaveAfterCreateEntry(frm, opts) {
	opts = opts || {};
	// Always try — even large grids; roll entry must not leave Not Saved.
	spr_auto_save_draft_if_dirty(
		frm,
		$.extend(
			{
				delay: opts.heavy ? 2000 : 1000,
				quiet: true,
				silentFail: false,
			},
			opts
		)
	);
}

/** Keep open SPR form in sync when GSM / API saves rolls on the server. */
function spr_bind_external_roll_save_realtime(frm) {
	if (typeof window !== 'undefined' && window._spr_realtime_roll_listener_bound) {
		return;
	}
	if (typeof window !== 'undefined') {
		window._spr_realtime_roll_listener_bound = true;
	}
	frappe.realtime.on('shaft_production_run_updated', function (data) {
		const cur = cur_frm;
		if (!cur || cur.doctype !== 'Shaft Production Run' || !cur.doc || !cur.doc.name) {
			return;
		}
		if (!data || data.name !== cur.doc.name) {
			return;
		}
		if (cint(cur.doc.docstatus) !== 0) {
			return;
		}
		if (cur.__spr_auto_save_in_progress || cur._spr_create_entry_in_progress) {
			return;
		}
		// External save already persisted — reload so the desk indicator stays Saved.
		if (typeof cur.is_dirty === 'function' && cur.is_dirty()) {
			spr_auto_save_draft_if_dirty(cur, { delay: 400, force: true, quiet: true });
			return;
		}
		cur._spr_light_reload = true;
		const reload = cur.reload_doc();
		const mark = function () {
			cur._spr_light_reload = false;
			spr_mark_just_saved(cur);
		};
		if (reload && typeof reload.then === 'function') {
			reload.then(mark).catch(mark);
		} else {
			mark();
		}
	});
}

function sprLaminationGsmFromItemCode(itemCode) {
	const code = ((itemCode || '') + '').trim().toUpperCase();
	if (!code || code.indexOf('-') === -1) {
		return 0;
	}
	const suffix = code.split('-').pop().trim();
	return SPR_LAM_GSM_SUFFIX_MAP[suffix] || 0;
}

function sprScheduleTotalProducedSync(frm, opts) {
	if (!frm) return;
	// Server validate() already syncs bundle/header totals on save — skip desk resync briefly to avoid dirty loop (blocks Submit).
	if (spr_should_skip_desk_auto_sync(frm)) {
		return;
	}
	if (frm.__spr_total_sync_timer) {
		clearTimeout(frm.__spr_total_sync_timer);
	}
	frm.__spr_total_sync_timer = setTimeout(function () {
		if (sprIsBundlePackagingMode(frm)) {
			sprSyncBundleProducedSheets(frm, opts || {});
		}
		spr_sync_total_produced_weight(frm, opts || {});
		frm.__spr_total_sync_timer = null;
	}, 120);
}

function spr_assign_bundle_row_metric(br, fieldname, value, silent) {
	if (Math.abs(flt(br[fieldname]) - flt(value)) <= 1e-6) {
		return;
	}
	if (silent || !br.name) {
		br[fieldname] = flt(value);
		return;
	}
	frappe.model.set_value('Bundle Calculation', br.name, fieldname, flt(value));
}

/** Sum roll-line Produced Sheets / net weight into bundle rows; consumed mtrs into SPR header (sheet cutting). */
function sprSyncBundleProducedSheets(frm, opts) {
	if (!frm || !sprIsBundlePackagingMode(frm)) {
		return;
	}
	const bundles = frm.doc.bundle_calculation || [];
	if (!bundles.length) {
		return;
	}
	const items = frm.doc.items || [];
	bundles.forEach(function (br, idx) {
		const rowId = String(br.name || '').trim() || 'idx' + String(idx);
		const prefix = rowId + '::';
		const ic = String(br.item_code || '').trim();
		const wo = String(br.work_order || '').trim();
		const nBundles = cint(br.no_of_bundles);
		const nBoxes = cint(br.no_of_boxes);
		const nRows = sprIsBag(frm) && nBundles < 1 ? nBoxes : nBundles;
		let sumPcs = 0;
		let sumBagPcs = 0;
		let sumNw = 0;
		let prefixHits = 0;
		items.forEach(function (it) {
			const job = String(it.job || '');
			if (job.indexOf(prefix) === 0) {
				prefixHits += 1;
				sumPcs += flt(it.custom_total_produced_sheets);
				sumBagPcs += flt(it.custom_achieved_bag_pcs);
				sumNw += spr_round_net_weight_kg(it.net_weight);
			}
		});
		if (!prefixHits) {
			items.forEach(function (it) {
				if (String(it.item_code || '').trim() !== ic || String(it.work_order || '').trim() !== wo) {
					return;
				}
				const jn = parseInt(String(it.job || ''), 10);
				if (isNaN(jn)) {
					return;
				}
				if (nRows > 0 && (jn < 1 || jn > nRows)) {
					return;
				}
				sumPcs += flt(it.custom_total_produced_sheets);
				sumBagPcs += flt(it.custom_achieved_bag_pcs);
				sumNw += spr_round_net_weight_kg(it.net_weight);
			});
		}
		const silent = !!(opts && opts.silent);
		spr_assign_bundle_row_metric(br, 'total_produced_sheets', sumPcs, silent);
		spr_assign_bundle_row_metric(br, 'total_achieved_weight', sumNw, silent);
		if (frappe.meta.get_docfield('Bundle Calculation', 'total_produced_bag_pcs')) {
			spr_assign_bundle_row_metric(br, 'total_produced_bag_pcs', sumBagPcs, silent);
		}
	});
	let consumedHdr = 0;
	bundles.forEach(function (br) {
		consumedHdr += flt(br.total_consumed_meter);
	});
	if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_achieved_meter')) {
		const cur = flt(frm.doc.custom_total_achieved_meter);
		if (Math.abs(cur - consumedHdr) > 1e-6) {
			const silent = !!(opts && opts.silent);
			if (silent || cint(frm.doc.docstatus) !== 0) {
				frm.doc.custom_total_achieved_meter = consumedHdr;
				if (silent) {
					frm.refresh_field('custom_total_achieved_meter');
				}
			} else {
				frm.set_value('custom_total_achieved_meter', consumedHdr);
			}
		}
	}
	if (!(opts && opts.silent)) {
		frm.refresh_field('bundle_calculation');
	}
}

function sprRecalcBundlePlannedPcs(frm, cdt, cdn) {
	const row = locals[cdt][cdn];
	if (!row) {
		return;
	}
	let tpb = 0;
	if (sprIsBag(frm)) {
		const nBoxes = cint(row.no_of_boxes);
		const pcs = cint(row.pcs_per_packet);
		if (nBoxes > 0 && pcs > 0) {
			tpb = nBoxes * pcs;
		} else {
			tpb = cint(row.pkts_per_bundle) * pcs;
		}
	} else {
		tpb = cint(row.pkts_per_bundle) * cint(row.pcs_per_packet);
	}
	frappe.model.set_value(cdt, cdn, 'total_pcs_per_bundle', tpb > 0 ? tpb : 0);
}

function sprCombinationSegmentCount(row) {
	const comb = String((row && row.combination) || '');
	if (!comb) {
		return 1;
	}
	const segments = comb
		.split('+')
		.map(function (s) {
			return s.trim();
		})
		.filter(Boolean);
	return Math.max(1, segments.length);
}

/** Normal desk Create Entry: one click adds one row per combination width (e.g. 43+33 → 2 rows). */
function sprCreateEntryRowsPerClick(row, frm) {
	if (frm && frm.doc && cint(frm.doc.is_mix_roll)) {
		return 1;
	}
	return sprCombinationSegmentCount(row);
}

function sprJobMaxRollLines(row, frm) {
	const shafts = cint((row && row.no_of_shafts) || 0) || 1;
	const rollsPerShaft = cint((row && row.no_of_rolls) || 0) || 1;
	const segCount = sprCombinationSegmentCount(row);
	if (frm && frm.doc && cint(frm.doc.is_mix_roll)) {
		if (segCount > 1) {
			return Math.max(segCount, 1) * rollsPerShaft;
		}
		return Math.max(shafts, 1) * rollsPerShaft;
	}
	if (segCount <= 1) {
		return shafts * rollsPerShaft;
	}
	return shafts * segCount * rollsPerShaft;
}

function sprCountRollLinesForJob(frm, jobId) {
	return (frm.doc.items || []).filter(function (d) {
		return String(d.job) === String(jobId) && spr_is_meaningful_roll_row(d);
	}).length;
}

function sprUsesOneRollPerCreateEntry(frm, row) {
	if (frm && frm.doc && cint(frm.doc.is_mix_roll)) {
		return true;
	}
	return !sprRollPromptMeta(frm, row);
}

function sprSaveBeforeCreateEntry(frm) {
	return new Promise(function (resolve) {
		resolve(); // Disabled to avoid lag
	});
}

function spr_finish_server_roll_append(frm, msg, opts) {
	opts = opts || {};
	msg = msg || {};
	const added = cint(msg.added) || 0;
	frm._spr_light_reload = true;
	const reload = frm.reload_doc();
	const done = function () {
		frm._spr_light_reload = false;
		spr_mark_just_saved(frm);
		spr_finish_create_entry(frm, {
			lineCount: added,
			startIdx: Math.max(0, (frm.doc.items || []).length - added),
			alertMsg: opts.alertMsg,
			serverSaved: true,
		});
	};
	if (reload && typeof reload.then === 'function') {
		reload.then(done).catch(done);
	} else {
		done();
	}
}

function invokeAppendRollLinesViaServer(
	frm,
	job_id,
	{
		laminationRollsPerCombo,
		laminationExactRollLines,
		appendMode,
		exactRollLines,
		rollStartIndex,
		quotaMeta,
	} = {}
) {
	if (frm._spr_create_entry_in_progress) {
		frappe.show_alert({
			message: __('Create Entry already running — please wait, do not click again.'),
			indicator: 'orange',
		});
		return;
	}
	frm._spr_create_entry_in_progress = true;
	const args = {
		shaft_production_run: frm.doc.name,
		job_id: String(job_id),
		replace_job_lines: appendMode ? 0 : 1,
	};
	const lrc = cint(laminationRollsPerCombo);
	if (lrc > 0) {
		args.lamination_rolls_per_combination = lrc;
	}
	const lex = cint(laminationExactRollLines);
	if (lex > 0) {
		args.lamination_exact_roll_lines = lex;
	}
	const ex = cint(exactRollLines);
	if (ex > 0) {
		args.exact_roll_lines = ex;
	}
	if (rollStartIndex !== undefined && rollStartIndex !== null && rollStartIndex !== '') {
		args.roll_start_index = cint(rollStartIndex);
	}
	let alertMsg = __('Added {0} roll line(s) for job {1}.', [ex || lex || lrc || 1, job_id]);
	if (quotaMeta && quotaMeta.max) {
		const addCount = cint(quotaMeta.addCount) || ex || 1;
		const addedIdx = cint(quotaMeta.current) + addCount;
		alertMsg =
			addCount > 1
				? __('Added {0} roll lines ({1} of {2}) for job {3}.', [addCount, addedIdx, quotaMeta.max, job_id])
				: __('Added roll {0} of {1} for job {2}.', [addedIdx, quotaMeta.max, job_id]);
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.append_roll_lines_for_job_and_save',
		args: args,
		freeze: true,
		freeze_message: ex > 1 ? __('Creating roll lines...') : __('Creating roll line...'),
		callback: function (r) {
			frm._spr_create_entry_in_progress = false;
			spr_finish_server_roll_append(frm, r.message, { alertMsg: alertMsg });
		},
		error: function () {
			frm._spr_create_entry_in_progress = false;
		},
	});
}

/** Sum job-level planned weights into header when shaft rows have explicit totals; keep PP/WO value when jobs are blank. */
function spr_sync_total_planned_qty_from_jobs(frm, opts) {
	if (!frm || !frm.doc) {
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		return;
	}
	if (!frappe.meta.get_docfield('Shaft Production Run', 'custom_total_planned_qty')) {
		return;
	}
	const settings = opts || {};
	const jobs = frm.doc.shaft_jobs || [];
	let sum = 0;
	let any = false;
	jobs.forEach(function (r) {
		const tw = flt(r.total_weight || r.total_weight_kgs || 0);
		if (tw > 0) {
			sum += tw;
			any = true;
		}
	});
	if (!any) {
		return;
	}
	const rounded = Math.round(sum * 100) / 100;
	const cur = flt(frm.doc.custom_total_planned_qty);
	if (Math.abs(cur - rounded) > 1e-6) {
		if (settings.silent) {
			frm.doc.custom_total_planned_qty = rounded;
			frm.refresh_field('custom_total_planned_qty');
		} else {
			frm.set_value('custom_total_planned_qty', rounded);
		}
	}
}

function spr_force_unfreeze_dom() {
	try {
		for (let i = 0; i < 6; i++) {
			frappe.dom.unfreeze();
		}
	} catch (e) {
		/* ignore */
	}
	try {
		document.querySelectorAll('.freeze').forEach(function (el) {
			el.remove();
		});
	} catch (e2) {
		/* ignore */
	}
}

function spr_stop_submit_watchdog(frm) {
	if (!frm) {
		return;
	}
	if (frm._spr_submit_watchdog) {
		clearInterval(frm._spr_submit_watchdog);
		frm._spr_submit_watchdog = null;
	}
	if (frm._spr_submit_max_timer) {
		clearTimeout(frm._spr_submit_max_timer);
		frm._spr_submit_max_timer = null;
	}
}

/** When savedocs times out (502) but server already submitted, recover UI + summary. */
function spr_try_recover_submitted_spr(frm, opts) {
	opts = opts || {};
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	const name = frm.doc.name;
	frappe.db.get_value('Shaft Production Run', name, 'docstatus').then(function (r) {
		const ds = cint(r && r.message && r.message.docstatus);
		if (ds !== 1) {
			return;
		}
		spr_force_unfreeze_dom();
		spr_stop_submit_watchdog(frm);
		spr_clear_save_submit_progress(frm);
		frm._spr_submit_in_progress = false;
		const afterReload = function () {
			if (!frm._spr_summary_shown) {
				frm._spr_pending_summary = true;
				spr_show_submit_summary_with_retries(frm, [300, 1200, 3000, 6000]);
			}
			if (opts.alert !== false && !frm._spr_recovery_alerted) {
				frm._spr_recovery_alerted = true;
				frappe.show_alert({
					message: __('SPR submitted successfully.'),
					indicator: 'green',
				});
			}
		};
		if (cint(frm.doc.docstatus) !== 1) {
			const reload = frm.reload_doc();
			if (reload && typeof reload.then === 'function') {
				reload.then(afterReload).catch(afterReload);
			} else {
				afterReload();
			}
		} else {
			afterReload();
		}
	});
}

function spr_start_submit_recovery_watchdog(frm) {
	spr_stop_submit_watchdog(frm);
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	let polls = 0;
	frm._spr_submit_watchdog = setInterval(function () {
		if (!frm || !frm._spr_progress_start) {
			spr_stop_submit_watchdog(frm);
			return;
		}
		polls += 1;
		spr_try_recover_submitted_spr(frm, { alert: polls === 1 });
		if (polls >= 48) {
			spr_stop_submit_watchdog(frm);
			spr_clear_save_submit_progress(frm);
			frm._spr_submit_in_progress = false;
			frappe.msgprint({
				title: __('Submit timed out'),
				indicator: 'orange',
				message: __(
					'The browser lost contact with the server. Refresh the page — if SPR shows Submitted, manufacturing may already be done.'
				),
			});
		}
	}, 5000);
	frm._spr_submit_max_timer = setTimeout(function () {
		if (!frm || !frm._spr_progress_start) {
			return;
		}
		spr_try_recover_submitted_spr(frm);
	}, 120000);
}

/** Visible progress while Save/Submit runs — updates message so operators know the system is working. */
function spr_begin_save_submit_progress(frm, mode, rollCount) {
	if (!frm) {
		return;
	}
	spr_clear_save_submit_progress(frm, false);
	frm._spr_progress_mode = mode || 'save';
	frm._spr_progress_start = Date.now();
	frm._spr_progress_roll_count = cint(rollCount) || 0;
	const rc = frm._spr_progress_roll_count;
	const firstMsg =
		mode === 'submit'
			? __('Submitting SPR — validating rolls and batches...')
			: rc > 15
				? __('Saving SPR ({0} rolls) — please wait...', [rc])
				: __('Saving SPR...');
	frappe.dom.freeze(firstMsg);
	if (mode === 'submit') {
		spr_start_submit_recovery_watchdog(frm);
	}
	frm._spr_progress_timer = setInterval(function () {
		if (!frm || !frm._spr_progress_start) {
			return;
		}
		const sec = Math.round((Date.now() - frm._spr_progress_start) / 1000);
		let msg = firstMsg;
		if (mode === 'submit') {
			if (sec >= 60) {
				msg = __('Submitting {0} rolls — {1}s elapsed. Still working, do not reload.', [rc, sec]);
			} else if (sec >= 30) {
				msg = __('Creating manufacture entries for {0} rolls — {1}s. Do not reload.', [rc, sec]);
			} else if (sec >= 10) {
				msg = __('Checking stock and posting manufacture entries — please wait...');
			} else if (sec >= 5 && rc > 15) {
				msg = __('Submitting {0} rolls — {1}s. Please wait...', [rc, sec]);
			}
		} else if (sec >= 15) {
			msg = __('Saving {0} roll lines — {1}s elapsed. Do not reload.', [rc, sec]);
		} else if (sec >= 5 && rc > 15) {
			msg = __('Saving large SPR ({0} rolls) — please wait...', [rc]);
		}
		const el = document.querySelector('.freeze-message');
		if (el) {
			el.textContent = msg;
		}
	}, 2000);
}

function spr_clear_save_submit_progress(frm, do_unfreeze) {
	if (do_unfreeze !== false) {
		spr_force_unfreeze_dom();
	}
	if (!frm) {
		return;
	}
	spr_stop_submit_watchdog(frm);
	if (frm._spr_progress_timer) {
		clearInterval(frm._spr_progress_timer);
		frm._spr_progress_timer = null;
	}
	frm._spr_progress_start = null;
}

function spr_desk_submit_roll_stats(frm) {
	const items = (frm && frm.doc && frm.doc.items) || [];
	let net = 0;
	items.forEach(function (it) {
		net += flt(it.net_weight);
	});
	return {
		rollCount: items.length,
		netKg: net,
		orderCode: cstr(frm.doc.custom_order_code || frm.doc.party_code || ''),
	};
}

function spr_show_desk_submit_confirm(frm, onYes) {
	const stats = spr_desk_submit_roll_stats(frm);
	const msg = __(
		'Submit <strong>{0}</strong> roll(s) · <strong>{1}</strong> Kg net<br>Order: <strong>{2}</strong><br>SPR: <strong>{3}</strong><br><br>Yes to submit, No to stay on this document.',
		[
			stats.rollCount,
			stats.netKg.toFixed(2),
			stats.orderCode || '—',
			frm.doc.name,
		]
	);
	frappe.confirm(msg, function () {
		if (typeof onYes === 'function') {
			onYes();
		}
	});
}

function spr_wrap_frm_save_for_progress(frm) {
	if (!frm || frm._spr_save_progress_wrapped) {
		return;
	}
	frm._spr_save_progress_wrapped = true;
	const origSave = frm.save.bind(frm);
	frm.save = function (action, callback, btn, on_error) {
		const isSubmit = action === 'Submit';
		const rollCount = (frm.doc.items || []).length;
		let progressStarted = false;
		let progressFinished = false;
		function finishProgress() {
			if (!progressStarted || progressFinished) {
				return;
			}
			progressFinished = true;
			spr_clear_save_submit_progress(frm);
		}
		function runSave() {
			if (isSubmit) {
				frm._spr_summary_shown = false;
				frm._spr_recovery_alerted = false;
				spr_begin_save_submit_progress(frm, 'submit', rollCount);
				progressStarted = true;
			} else if (rollCount > 15) {
				spr_begin_save_submit_progress(frm, 'save', rollCount);
				progressStarted = true;
			}
			function wrappedCallback(r) {
				finishProgress();
				if (isSubmit) {
					spr_handle_submit_response_summary(frm, r || {});
				}
				if (typeof callback === 'function') {
					try {
						callback(r);
					} catch (cbErr) {
						if (isSubmit) {
							setTimeout(function () {
								spr_try_recover_submitted_spr(frm);
							}, 1500);
						}
					}
				}
			}
			function wrappedOnError(err) {
				finishProgress();
				frm._spr_submit_in_progress = false;
				if (isSubmit) {
					setTimeout(function () {
						spr_try_recover_submitted_spr(frm);
					}, 2000);
				}
				if (typeof on_error === 'function') {
					try {
						on_error.apply(this, arguments);
					} catch (e) {
						/* ignore broken error handlers */
					}
				}
			}
			const result = origSave(action, wrappedCallback, btn, wrappedOnError);
			if (result && typeof result.then === 'function') {
				result
					.then(function (r) {
						finishProgress();
						if (isSubmit) {
							spr_handle_submit_response_summary(frm, r || {});
						}
						return r;
					})
					.catch(function () {
						finishProgress();
						frm._spr_submit_in_progress = false;
						if (isSubmit) {
							setTimeout(function () {
								spr_try_recover_submitted_spr(frm);
							}, 2000);
						}
					})
					.finally(function () {
						finishProgress();
						frm._spr_submit_in_progress = false;
					});
			}
			return result;
		}
		if (isSubmit && !frm._spr_submit_confirmed && cint(frm.doc.docstatus) === 0 && rollCount > 0) {
			spr_show_desk_submit_confirm(frm, function () {
				frm._spr_submit_confirmed = true;
				runSave();
				frm._spr_submit_confirmed = false;
			});
			return;
		}
		return runSave();
	};
}

function spr_show_manufacture_summary_dialog(frm, html, title) {
	if (!html) {
		return;
	}
	frappe.msgprint({
		title: title || __('Manufacture Summary — {0}', [frm.doc.name]),
		message: html,
		wide: true,
	});
}

function spr_extract_submit_summary_html(r) {
	return (
		(r && r.spr_submit_summary) ||
		(r && r.message && typeof r.message === 'object' && r.message.spr_submit_summary) ||
		(typeof frappe !== 'undefined' && frappe.response && frappe.response.spr_submit_summary) ||
		''
	);
}

function spr_handle_submit_response_summary(frm, r) {
	r = r || {};
	if (!frm || !frm.doc) {
		return;
	}
	if (cint(frm.doc.docstatus) !== 1) {
		return;
	}
	const html = spr_extract_submit_summary_html(r);
	if (html && !frm._spr_summary_shown) {
		frm._spr_summary_shown = true;
		spr_show_manufacture_summary_dialog(frm, html);
		return;
	}
	if (!frm._spr_summary_shown) {
		frm._spr_pending_summary = true;
		spr_show_submit_summary_with_retries(frm, [400, 1200, 2500, 5000, 9000, 15000]);
	}
}

function spr_open_manufacture_summary(frm) {
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_submit_summary',
		args: { shaft_production_run: frm.doc.name },
		freeze: true,
		freeze_message: __('Loading manufacture summary...'),
		callback: function (r) {
			const d = (r && r.message) || {};
			if (d.html) {
				spr_show_manufacture_summary_dialog(frm, d.html, d.title);
			} else {
				frappe.msgprint({
					title: __('Manufacture Summary'),
					indicator: 'orange',
					message: __('No manufacture summary is available for this SPR yet.'),
				});
			}
		},
	});
}

frappe.ui.form.on('Shaft Production Run', {
	setup: function (frm) {
		// Buttons registered in refresh — see spr_register_spr_page_buttons (Frappe skips duplicate labels if setup runs too early)
	},

	onload: function (frm) {
		spr_patch_items_grid_refresh(frm);
		spr_wrap_frm_save_for_progress(frm);
		spr_register_spr_page_buttons(frm);
		['items', 'shaft_jobs', 'bundle_calculation'].forEach(function (fn) {
			spr_bind_spr_grid_column_configure_hook(frm, fn);
		});
		spr_enforce_roll_line_grid_policy(frm);
		setTimeout(function () {
			if (!frm || !frm.doc) {
				return;
			}
			sprToggleSheetCuttingUi(frm);
			sprToggleLaminationRollUi(frm);
			spr_inject_gsm_legend(frm);
			spr_layout_all_grids(frm);
			spr_stabilize_spr_child_grids(frm, { light: true });
			spr_enforce_roll_line_grid_policy(frm);
			schedule_spr_item_row_styles(frm);
			sprEnsureBundleRowsFromPp(frm);
		}, 120);
		if (frm.doc && cint(frm.doc.docstatus) === 1) {
			spr_clear_save_submit_progress(frm);
			spr_stabilize_submitted_spr_grids_once(frm);
		}
	},

	production_plan: function (frm) {
		if (!frm.doc.production_plan) {
			if (frappe.meta.get_docfield('Shaft Production Run', 'company')) {
				frm.set_value('company', '');
			}
			sprResetProcessFlags(frm);
			frm.clear_table('shaft_jobs');
			frm.clear_table('bundle_calculation');
			frm.clear_table('items');
			frm.refresh_field('shaft_jobs');
			frm.refresh_field('bundle_calculation');
			frm.refresh_field('items');
			sprToggleSheetCuttingUi(frm);
			return;
		}

		// Do not keep old roll lines when switching PP (also blocks client scripts that fill later)
		frm.clear_table('items');
		frm.clear_table('shaft_jobs');
		frm.clear_table('bundle_calculation');
		frm.refresh_field('items');
		frm.refresh_field('shaft_jobs');
		frm.refresh_field('bundle_calculation');
		sprResetProcessFlags(frm);

		if (!frm.doc.run_date) {
			frm.set_value('run_date', frappe.datetime.get_today());
		}

		frappe.call({
			method:
				'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_production_plan_details',
			args: { production_plan: frm.doc.production_plan },
			callback: function (r) {
				const d = r.message || {};
				sprLog('[SPR] get_production_plan_details response:', d);
				sprLog('[SPR] response keys:', Object.keys(d));
				
				// Set all returned fields that exist on the form
				if (d.company && frappe.meta.get_docfield('Shaft Production Run', 'company')) {
					sprLog('[SPR] Setting company:', d.company);
					frm.set_value('company', d.company);
				}
				if (d.customer) {
					sprLog('[SPR] Setting customer:', d.customer);
					frm.set_value('customer', d.customer);
				}
				if (d.custom_unit) {
					sprLog('[SPR] Setting custom_unit:', d.custom_unit);
					frm.set_value('custom_unit', d.custom_unit);
					sprApplyLaminationUnitDefaults(frm, d.custom_unit);
				}
				if ('custom_order_code' in d) {
					sprLog('[SPR] Setting custom_order_code:', d.custom_order_code);
					frm.set_value('custom_order_code', d.custom_order_code || '');
				}
				if ('custom_label' in d) {
					sprLog('[SPR] Setting custom_label:', d.custom_label);
					frm.set_value('custom_label', d.custom_label || '');
				}
				if ('custom_total_planned_qty' in d) {
					sprLog('[SPR] Setting custom_total_planned_qty:', d.custom_total_planned_qty);
					frm.set_value('custom_total_planned_qty', flt(d.custom_total_planned_qty || 0));
				}
				if ('custom_total_planned_pcs' in d && frappe.meta.get_docfield('Shaft Production Run', 'custom_total_planned_pcs')) {
					frm.set_value('custom_total_planned_pcs', flt(d.custom_total_planned_pcs || 0));
				}
				sprApplyProcessFlagsFromPp(frm, d);
				sprToggleSheetCuttingUi(frm);
				if (cint(frm.doc.custom_is_slitting)) {
					if (frm._spr_items_cols_mode) {
						delete frm._spr_items_cols_mode;
					}
					spr_apply_items_grid_columns(frm, true);
				}
				if (sprIsBundlePackagingMode(frm)) {
					sprLoadBundleCalculationFromPp(frm, d.bundle_rows);
				} else {
					sprLoadShaftJobsFromPp(frm);
				}
				// Note: custom_party_code is only in the child table, not the header, so don't set it here
			},
		});
	},

	custom_unit: function (frm) {
		if (frm._spr_items_cols_mode) {
			delete frm._spr_items_cols_mode;
		}
		spr_reset_batch_roll_cache(frm);
		sprApplyLaminationUnitDefaults(frm);
		sprToggleLaminationRollUi(frm);
		sprToggleSheetCuttingUi(frm);
		spr_apply_items_grid_columns(frm, true);
	},
	shift: function (frm) {
		spr_reset_batch_roll_cache(frm);
	},
	run_date: function (frm) {
		spr_reset_batch_roll_cache(frm);
	},
	custom_is_sheet_cutting: function (frm) {
		sprToggleSheetCuttingUi(frm);
	},
	custom_is_box_bag: function (frm) {
		sprToggleSheetCuttingUi(frm);
		if (sprIsBag(frm) && frm.doc.production_plan && !(frm.doc.bundle_calculation || []).length) {
			sprLoadBundleCalculationFromPp(frm, null);
		}
	},
	custom_is_lamination: function (frm) {
		sprToggleLaminationRollUi(frm);
		schedule_spr_item_row_styles(frm);
	},
	custom_is_slitting: function (frm) {
		if (frm._spr_items_cols_mode) {
			delete frm._spr_items_cols_mode;
		}
		spr_apply_items_grid_columns(frm, true);
		schedule_spr_item_row_styles(frm);
	},
	is_mix_roll: function (frm) {
		if (frm._spr_items_cols_mode) {
			delete frm._spr_items_cols_mode;
		}
		spr_apply_items_grid_columns(frm, true);
		schedule_spr_item_row_styles(frm);
	},

	refresh: function (frm) {
		if (frm && frm._spr_progress_start && frm.doc && cint(frm.doc.docstatus) === 1) {
			spr_clear_save_submit_progress(frm);
			frm._spr_submit_in_progress = false;
		}
		const lightGridPass = spr_should_use_lightweight_grid_pass(frm);
		// Enforce read-only UI controls dynamically since we removed them from JSON to allow backend save
		if (!lightGridPass && spr_get_field_dict(frm, 'items')) {
			spr_reset_items_grid_field_visibility(frm);
		}
		try {
			frm.set_df_property('company', 'read_only', 1);
		} catch (e) {
			/* field may not exist until migrate */
		}
		frm.set_df_property('total_produced_weight', 'read_only', 1);
		try {
			frm.set_df_property('custom_total_achieved_meter', 'read_only', 1);
		} catch (e) {
			/* field may not exist until migrate */
		}
		try {
			frm.set_df_property('net_weight', 'precision', 2, null, 'items');
		} catch (e) {
			/* ignore desk variants */
		}

		sprLog('[SPR REFRESH] === REFRESH HOOK START ===');
		
		spr_sync_total_planned_qty_from_jobs(frm, { silent: true });
		sprLog('[SPR REFRESH] After total_planned_qty sync');
		spr_sync_no_of_rolls_created(frm, { silent: true });
		spr_warn_patty_without_rolls(frm);
		spr_enforce_roll_line_grid_policy(frm);
		
		sprScheduleTotalProducedSync(frm, { silent: true });
		sprLog('[SPR REFRESH] After total_produced_weight sync (scheduled)');
		if (!sprIsBundlePackagingMode(frm) && !spr_should_use_lightweight_grid_pass(frm)) {
			try {
				update_shaft_job_achieved_from_items(frm);
			} catch (e) {
				/* ignore */
			}
		}
		sprToggleLaminationRollUi(frm);
		sprToggleSheetCuttingUi(frm);
		if (
			sprIsBundlePackagingMode(frm) &&
			frm.doc.production_plan &&
			!(frm.doc.bundle_calculation || []).length &&
			(!frm.is_dirty || !frm.is_dirty())
		) {
			sprLoadBundleCalculationFromPp(frm, null);
		}
		spr_patch_items_grid_refresh(frm);
		spr_bind_items_grid_edit_guard(frm);
		spr_install_items_grid_column_guard(frm);
		spr_register_spr_page_buttons(frm);
		if (!spr_should_use_lightweight_grid_pass(frm)) {
			spr_layout_all_grids(frm, { toggleUi: false });
		} else {
			spr_apply_grid_wrap_classes(frm);
		}

		// Clear stale hidden flags, then apply column lists (JS owns in_list_view).
		// During Save Row / save refresh: never reset in_list_view — apply is blocked and grid collapses.
		if (!lightGridPass) {
			spr_reset_items_grid_field_visibility(frm);
			spr_reset_shaft_jobs_grid_field_visibility(frm);
			spr_reset_bundle_calc_grid_field_visibility(frm);
			if (!spr_is_submitted_spr(frm)) {
				spr_sync_grid_columns_visible(frm, 'items');
				spr_sync_grid_columns_visible(frm, 'shaft_jobs');
			} else {
				spr_apply_items_grid_columns(frm, true);
				spr_apply_shaft_jobs_grid_columns(frm, true);
			}
		} else {
			spr_restore_cached_grid_columns(frm, 'items');
			spr_restore_cached_grid_columns(frm, 'shaft_jobs');
		}
		if (sprIsBundlePackagingMode(frm)) {
			spr_apply_bundle_calculation_grid_columns(frm, true);
		}

		spr_schedule_grid_ui_debounced(frm, { delay: 350, columns: false });

		spr_stabilize_spr_child_grids(frm, { delay: 280, light: true });

		if (frm.doc && cint(frm.doc.docstatus) === 1) {
			spr_stabilize_submitted_spr_grids_once(frm);
			if (frm._spr_pending_summary && !frm._spr_summary_shown) {
				spr_show_submit_summary_with_retries(frm, [300, 1200, 3000]);
			}
		} else {
			const refreshAlignKey = '_spr_refresh_align';
			if (frm[refreshAlignKey]) {
				clearTimeout(frm[refreshAlignKey]);
			}
			frm[refreshAlignKey] = setTimeout(function () {
				frm[refreshAlignKey] = null;
				spr_refresh_draft_child_grids_light(frm);
				spr_enforce_roll_line_grid_policy(frm);
			}, 320);
		}

		setTimeout(function () {
			if (!spr_should_skip_desk_auto_sync(frm)) {
				sprScheduleTotalProducedSync(frm, { silent: true });
			}
		}, 400);
		
		setTimeout(function () {
			spr_register_spr_page_buttons(frm);
		}, 700);
		
		spr_inject_gsm_legend(frm);
		spr_apply_shaft_jobs_create_entry_ui(frm);
		// Removed duplicate spr_schedule_grid_ui_debounced call — already scheduled at line 3199
		if (spr_should_use_lightweight_grid_pass(frm)) {
			setTimeout(function () {
				if (!frm || !frm.fields_dict) {
					return;
				}
				spr_refresh_draft_child_grids_light(frm);
				apply_spr_item_row_styles(frm);
				spr_apply_items_row_lock_ui(frm);
				frm._spr_row_save_in_progress = false;
			}, 320);
		} else if (spr_is_large_submitted_grid(frm)) {
			spr_apply_item_row_styles_light(frm);
		} else {
			spr_reapply_item_row_styles_with_retries(frm);
		}

		sprLog('[SPR REFRESH] === REFRESH HOOK END ===');
		spr_persist_or_clear_submitted_client_dirty(frm);
		spr_bind_external_roll_save_realtime(frm);
		// Draft: wastage/core client scripts often rewrite child tables after refresh → Not Saved.
		if (spr_is_draft_spr(frm) && !frm.is_new()) {
			spr_auto_save_draft_if_dirty(frm, {
				delay: 2800,
				quiet: true,
				silentFail: true,
				skipIfJustSaved: true,
			});
		}
	},

	onload_post_render: function (frm) {
		const heavy = spr_is_large_submitted_grid(frm);
		spr_stabilize_spr_child_grids(frm, { delay: heavy ? 400 : 180, light: true });
		spr_enforce_roll_line_grid_policy(frm);
		if (heavy) {
			spr_apply_item_row_styles_light(frm);
		} else {
			spr_reapply_item_row_styles_with_retries(frm, [200, 500, 900, 1400]);
		}
	},

	before_submit: function (frm) {
		if (!frm || !frm.doc) {
			return;
		}
		frm._spr_submit_in_progress = true;
		frm._spr_summary_shown = false;
		frm._spr_pending_summary = false;
		if (cint(frm.doc.docstatus) !== 0) {
			return;
		}
		// Bag / sheet-cutting bundle runs post PCS (not kg) — skip net-weight tolerance gate.
		if (sprIsBag(frm) || sprIsSheetCutting(frm)) {
			return;
		}
		if (!frappe.meta.get_docfield('Shaft Production Run', 'tolerance_override_approved')) {
			return;
		}
		const violations = spr_collect_planned_tolerance_violations(frm);
		if (!violations.length) {
			return;
		}
		if (cint(frm.doc.tolerance_override_approved) && (frm.doc.tolerance_override_reason || '').trim()) {
			return;
		}
		frappe.validated = false;
		if (window.sprTolDialogOpen) {
			return;
		}
		spr_show_tolerance_override_dialog(frm, violations, { forSubmit: true });
	},

	after_save: function (frm) {
		// SPR_GRID_ALIGNMENT_CONTRACT_VER — keep lightweight; see .cursor/rules/spr-grid-alignment.mdc
		spr_mark_just_saved(frm);
		spr_register_spr_page_buttons_after_save(frm);
		spr_sync_no_of_rolls_created(frm, { silent: true });
		spr_apply_grid_wrap_classes(frm);
		spr_apply_spr_child_grid_min_widths(frm);
		spr_schedule_item_row_styles_after_doc_write(frm);
		const key = '_spr_after_save_align';
		if (frm[key]) {
			clearTimeout(frm[key]);
		}
		frm[key] = setTimeout(function () {
			frm[key] = null;
			if (!frm || !frm.fields_dict) {
				return;
			}
			spr_refresh_draft_child_grids_light(frm);
			spr_enforce_roll_line_grid_policy(frm);
		}, 280);
	},

	on_submit: function (frm) {
		spr_stop_submit_watchdog(frm);
		spr_clear_save_submit_progress(frm);
		frm._spr_submit_in_progress = false;
		frm._spr_just_submitted = Date.now();
		frm._spr_submitted_grids_stable = false;
		spr_apply_shaft_jobs_grid_columns(frm, true);
		spr_apply_items_grid_columns(frm, true);
		spr_apply_create_entry_buttons_ui(frm);
		spr_apply_grid_wrap_classes(frm);
		spr_ensure_child_grid_heights(frm);
		spr_stabilize_submitted_spr_grids_once(frm);
		if (!frm._spr_summary_shown) {
			frm._spr_pending_summary = true;
			spr_show_submit_summary_with_retries(frm, [600, 1800, 4000, 8000, 15000]);
		}
		if (frm.doc && frm.doc.production_plan) {
			setTimeout(function () {
				if (!frm || !frm.doc || !frm.doc.production_plan) {
					return;
				}
				frappe.call({
					method:
						'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_resync_production_plan_progress',
					args: { production_plan: frm.doc.production_plan },
					freeze: false,
				});
			}, 2500);
		}
	},

	items: {
		items_add: function (frm, cdt, cdn) {
			if (spr_reject_unauthorized_blank_item_row(frm, cdt, cdn)) {
				return;
			}
			if (cint(frm._spr_programmatic_item_adds) > 0) {
				return;
			}
			sprLog('[SPR DEBUG] items_add fired');
			spr_sync_no_of_rolls_created(frm);
			// Defer heavy job-achieved recalc to avoid lag during rapid row adds
			update_shaft_job_achieved_from_items(frm, { deferRefresh: true, skipGridRefresh: true });
			sprLog('[SPR DEBUG] items_add: schedule total_produced_weight sync with', (frm.doc.items || []).length, 'items');
			sprScheduleTotalProducedSync(frm, { silent: true });
			// Only schedule styles if not rapidly adding rows
			if (!frm.__spr_items_add_style_timer) {
				frm.__spr_items_add_style_timer = setTimeout(function () {
					frm.__spr_items_add_style_timer = null;
					schedule_spr_item_row_styles(frm);
					spr_enforce_roll_line_grid_policy(frm);
				}, 600);
			}
		},
		items_remove: function (frm) {
			delete frm._spr_max_roll_cache;
			delete frm._spr_max_roll_cache_before_idx;
			spr_sync_no_of_rolls_created(frm);
			update_shaft_job_achieved_from_items(frm);
			schedule_spr_item_row_styles(frm);
			spr_enforce_roll_line_grid_policy(frm);
		},
	},
});

/** Allowed deviation of roll net/gross vs planned_qty (%). Match server `spr_net_weight_tolerance_percent` in site_config (default 5). */
function spr_net_weight_tolerance_percent() {
	return 5.0;
}

function spr_effective_roll_weight_kg(row) {
	if (!row) {
		return 0;
	}
	let w = flt(row.net_weight);
	if (w > 0) {
		return w;
	}
	return flt(row.gross_weight);
}

function spr_collect_planned_tolerance_violations(frm) {
	const tol = spr_net_weight_tolerance_percent();
	if (!(tol > 0)) {
		return [];
	}
	if (!frappe.meta.get_docfield('Shaft Production Run Item', 'planned_qty')) {
		return [];
	}
	const out = [];
	(frm.doc.items || []).forEach(function (row) {
		const pq = flt(row.planned_qty);
		if (!(pq > 0)) {
			return;
		}
		const act = spr_effective_roll_weight_kg(row);
		if (!(act > 0)) {
			return;
		}
		const dev_pct = (Math.abs(act - pq) / pq) * 100;
		if (dev_pct > tol + 1e-9) {
			out.push({
				job: row.job,
				roll_no: row.roll_no,
				planned: pq,
				actual: act,
				dev_pct: dev_pct,
			});
		}
	});
	return out;
}

function spr_escape_html(s) {
	return String(s === undefined || s === null ? '' : s)
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;');
}

function spr_show_tolerance_override_dialog(frm, violations, opts) {
	opts = opts || {};
	const forSubmit = !!opts.forSubmit;
	window.sprTolDialogOpen = true;
	const tol = spr_net_weight_tolerance_percent();
	const rows = violations
		.map(function (v) {
			return (
				'<tr><td>' +
				spr_escape_html(v.job) +
				'</td><td>' +
				spr_escape_html(v.roll_no != null && v.roll_no !== '' ? v.roll_no : '—') +
				'</td><td class="text-right">' +
				flt(v.planned).toFixed(2) +
				'</td><td class="text-right">' +
				flt(v.actual).toFixed(2) +
				'</td><td class="text-right">' +
				flt(v.dev_pct).toFixed(2) +
				'%</td></tr>'
			);
		})
		.join('');
	const html =
		'<p class="text-muted">' +
		__(
			forSubmit
				? 'Allowed deviation: &plusmn;{0}%. Enter a reason and confirm approval to submit, or adjust roll weights.'
				: 'Allowed deviation: &plusmn;{0}%. Enter a reason and confirm approval to save, or adjust roll weights.',
			[tol]
		) +
		'</p><table class="table table-bordered table-condensed" style="font-size:12px;"><thead><tr><th>' +
		__('Job') +
		'</th><th>' +
		__('Roll') +
		'</th><th>' +
		__('Planned (Kg)') +
		'</th><th>' +
		__('Net/Gross (Kg)') +
		'</th><th>' +
		__('Variance') +
		'</th></tr></thead><tbody>' +
		rows +
		'</tbody></table>';

	const d = new frappe.ui.Dialog({
		title: __('Tolerance — approval required'),
		onhide: function () {
			window.sprTolDialogOpen = false;
			if (frm) {
				frm._spr_submit_in_progress = false;
			}
		},
		fields: [
			{ fieldname: 'h', fieldtype: 'HTML', options: html },
			{
				fieldname: 'reason',
				fieldtype: 'Small Text',
				label: __('Reason for override'),
				reqd: 1,
			},
			{
				fieldname: 'approved',
				fieldtype: 'Check',
				label: __('I approve this deviation'),
				default: 0,
			},
		],
		primary_action_label: forSubmit ? __('Submit with approval') : __('Save with approval'),
		primary_action: function () {
			const reason = (d.get_value('reason') || '').trim();
			if (!reason) {
				frappe.msgprint(__('Reason is required.'));
				return;
			}
			if (!cint(d.get_value('approved'))) {
				frappe.msgprint(__('Confirm approval to continue.'));
				return;
			}
			d.hide();
			frm.set_value('tolerance_override_reason', reason);
			frm.set_value('tolerance_override_approved', 1);
			if (forSubmit) {
				frm.save('Submit');
			} else {
				frm.save();
			}
		},
	});
	d.show();
}

function spr_open_fabric_batch_pick_dialog(frm) {
	if (!frm || frm.is_new()) {
		frappe.msgprint(__('Save the SPR first, then select fabric batches.'));
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		frappe.msgprint(__('This SPR is submitted. Fabric batch picks cannot be changed.'));
		return;
	}
	if (!frappe.meta.get_docfield('Shaft Production Run', 'fabric_batch_picks')) {
		frappe.msgprint(
			__(
				'Fabric batch fields are not installed yet. Run bench migrate on the server (SPR Fabric Batch Pick + Manual fabric batches).'
			)
		);
		return;
	}
	frappe.dom.freeze(__('Loading batches...'));
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_fabric_batch_pick_context',
		args: { spr_name: frm.doc.name },
		callback: function (r) {
			frappe.dom.unfreeze();
			const ctx = r.message || {};
			if (!ctx.needs_picks) {
				const bagHint = cint(ctx.is_bag_spr)
					? __(' Bag FG was recognized, but no batch-tracked BOM raw materials were found on linked Work Order(s). Check BOM / Item has_batch_no on fabric (100*) or slitting (103*) lines.')
					: __(' No bag / fabric Work Order with batch-tracked BOM items was recognized. Ensure Is Bag is checked, roll lines are saved with Work Order, and FG item code is a bag process (221, 224, 211–217, 200–203).');
				frappe.msgprint(
					__(
						'No RM batch selection is required for this SPR.{0}',
						[bagHint]
					)
				);
				return;
			}
			spr_show_fabric_batch_pick_dialog(frm, ctx);
		},
		error: function () {
			frappe.dom.unfreeze();
		},
	});
}

function spr_show_fabric_batch_pick_dialog(frm, ctx) {
	const picksByKey = {};
	(ctx.current_picks || []).forEach(function (p) {
		const k = (p.work_order || '') + '|' + (p.item_code || '') + '|' + (p.batch_no || '');
		picksByKey[k] = flt(p.qty);
	});
	let bodyHtml = '<div class="spr-batch-dlg" style="max-height:460px;overflow:auto">';
	(ctx.lines || []).forEach(function (ln) {
		bodyHtml +=
			'<h4 style="margin-top:0.75rem">' +
			spr_escape_html(ln.work_order || '') +
			' — FG ' +
			spr_escape_html(ln.fg_item || '') +
			(ln.fg_process ? ' (' + spr_escape_html(String(ln.fg_process)) + ')' : '') +
			' — ' +
			__('SPR total') +
			' ' +
			spr_escape_html(String(ln.total_fg_kg || '')) +
			' Kg</h4>';
		bodyHtml +=
			'<p class="text-muted small">' +
			__('WIP warehouse') +
			': ' +
			spr_escape_html(ln.wip_warehouse || '') +
			' — ' +
			__('Work In Progress batches only. Tick the roll you are loading now.') +
			'</p>';
		if (ln.bom_stack && ln.bom_stack.length) {
			bodyHtml +=
				'<table class="table table-bordered table-condensed" style="margin-bottom:0.5rem;max-width:36rem"><thead><tr>' +
				'<th>' +
				__('Step') +
				'</th><th>' +
				__('Process') +
				'</th></tr></thead><tbody>';
			(ln.bom_stack || []).forEach(function (st) {
				bodyHtml +=
					'<tr><td>' +
					spr_escape_html(st.label || st.role || '') +
					'</td><td>' +
					spr_escape_html(st.process || '') +
					(st.item_code ? ' — ' + spr_escape_html(st.item_code) : '') +
					'</td></tr>';
			});
			bodyHtml += '</tbody></table>';
		}
		(ln.raw_materials || []).forEach(function (rm) {
			const procTag = rm.process_code ? ' [' + spr_escape_html(String(rm.process_code)) + ']' : '';
			bodyHtml +=
				'<h5 style="margin-top:0.5rem">' +
				spr_escape_html(rm.item_code || '') +
				procTag +
				' — ' +
				spr_escape_html(rm.item_name || '') +
				'</h5>';
			bodyHtml +=
				'<p class="small">' +
				__('Required') +
				': <b>' +
				String(flt(rm.required_qty)) +
				'</b> Kg &nbsp;|&nbsp; ' +
				spr_escape_html(rm.quality || '') +
				' &nbsp;|&nbsp; ' +
				spr_escape_html(rm.colour || '') +
				' &nbsp;|&nbsp; GSM ' +
				String(flt(rm.gsm)) +
				' &nbsp;|&nbsp; W ' +
				String(flt(rm.width_inch)) +
				'"</p>';
		bodyHtml +=
			'<table class="table table-bordered table-condensed"><thead><tr>' +
			'<th style="width:2rem">' +
			__('Use') +
			'</th><th>' +
			__('Batch No') +
			'</th><th>' +
			__('Warehouse') +
			'</th><th>' +
			__('Avail (Kg)') +
			'</th><th>' +
			__('Use (Kg)') +
			'</th></tr></thead><tbody>';
		const batches = (rm.batches || []).filter(function (b) {
			const bwh = String(b.warehouse || '');
			const wip = String(ln.wip_warehouse || '');
			if (wip && bwh === wip) {
				return true;
			}
			return /work\s*in\s*progress/i.test(bwh) || /^wip\b/i.test(bwh);
		});
		batches.forEach(function (b) {
			const bn = String(b.batch_no || '');
			const bwh = String(b.warehouse || '');
			const key = (ln.work_order || '') + '|' + (rm.item_code || '') + '|' + bn;
			const defq = picksByKey[key] != null ? picksByKey[key] : '';
			const mx = Math.round(flt(b.qty) * 1000) / 1000;
			const hasPick = defq !== '' && flt(defq) > 0;
			const whBadge =
				'<span style="color:green;font-size:0.8em">' + spr_escape_html(bwh) + '</span>';
			bodyHtml +=
				'<tr data-wo="' +
				spr_escape_html(ln.work_order || '') +
				'" data-item="' +
				spr_escape_html(rm.item_code || '') +
				'" data-batch="' +
				spr_escape_html(bn) +
				'">' +
				'<td><input type="checkbox" class="spr-bch-use"' +
				(hasPick ? ' checked' : '') +
				' /></td>' +
				'<td>' +
				spr_escape_html(bn) +
				'</td><td>' +
				whBadge +
				'</td><td>' +
				String(mx) +
				'</td><td><input type="number" class="input-with-feedback form-control spr-bch-qty" step="0.001" min="0" data-max="' +
				String(mx) +
				'" value="' +
				(hasPick ? String(Math.round(flt(defq) * 1000) / 1000) : '') +
				'" style="max-width:9rem" /></td></tr>';
		});
		if (!batches.length) {
			bodyHtml +=
				'<tr><td colspan="5">' +
				__('No batches transferred for this WO yet. Submit Material Transfer for Manufacture first.') +
				'</td></tr>';
		}
			bodyHtml += '</tbody></table>';
		});
	});
	bodyHtml += '</div>';

	const d = new frappe.ui.Dialog({
		title: __('Select RM batches for WO consumption'),
		fields: [{ fieldtype: 'HTML', fieldname: 'spr_batch_html' }],
		size: 'extra-large',
		primary_action_label: __('Save picks'),
		primary_action: function () {
			const out = [];
			let qtyErr = '';
			d.$wrapper.find('tr[data-batch]').each(function () {
				const $tr = $(this);
				const use = $tr.find('.spr-bch-use').prop('checked');
				if (!use) {
					return;
				}
				const q = Math.round(flt($tr.find('.spr-bch-qty').val()) * 1000) / 1000;
				const mx = Math.round(flt($tr.find('.spr-bch-qty').attr('data-max')) * 1000) / 1000;
				if (q <= 0) {
					return;
				}
				let useQty = q;
				if (mx > 0 && useQty > mx) {
					if (useQty - mx <= 0.02) {
						useQty = mx;
					} else {
						qtyErr = __('Use quantity cannot exceed available stock for one of the selected batches.');
						return false;
					}
				}
				out.push({
					work_order: $tr.attr('data-wo'),
					item_code: $tr.attr('data-item'),
					batch_no: $tr.attr('data-batch'),
					qty: useQty,
				});
			});
			if (qtyErr) {
				frappe.msgprint(qtyErr);
				return;
			}
			frappe.call({
				method:
					'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_save_fabric_batch_picks',
				args: { spr_name: frm.doc.name, picks_json: JSON.stringify(out) },
				freeze: true,
				freeze_message: __('Saving...'),
				callback: function (r) {
					if (r.exc) {
						frappe.msgprint({
							title: __('Save failed'),
							indicator: 'red',
							message: r.exc,
						});
						return;
					}
					d.hide();
					spr_mark_just_saved(frm);
					const reload = frm.reload_doc();
					if (reload && typeof reload.then === 'function') {
						reload.then(function () {
							spr_mark_just_saved(frm);
						});
					}
					frappe.show_alert({
						message: __('RM batch picks saved ({0} line(s)). Form should stay saved — click Submit.', [
							(r.message && r.message.count) || out.length,
						]),
						indicator: 'green',
					});
				},
				error: function (err) {
					frappe.msgprint({
						title: __('Save failed'),
						indicator: 'red',
						message:
							(err && err.message) ||
							__('Could not save RM batch picks. Open Tools → SPR — Diagnose save for details.'),
					});
				},
			});
		},
	});
	const $w = d.fields_dict.spr_batch_html.$wrapper;
	$w.html(bodyHtml);
	$w.on('change', '.spr-bch-use', function () {
		const $tr = $(this).closest('tr');
		const $q = $tr.find('.spr-bch-qty');
		if ($(this).prop('checked') && (!($q.val() || '') || flt($q.val()) <= 0)) {
			const mx = flt($q.attr('data-max'));
			$q.val(mx > 0 ? String(mx) : '');
		}
	});
	d.show();
}

/**
 * Register toolbar + Tools menu. Frappe rebuilds the header on Save/refresh — remove then re-add
 * every time so buttons do not disappear (do not use a one-shot _spr_page_buttons_ok guard).
 * Also registers custom buttons — they survive some toolbar rebuilds better than inner_group alone.
 */
function spr_bundle_packaging_toggle_label(enabled) {
	return enabled
		? __('Bundle SE on Submit: ON')
		: __('Bundle SE on Submit: OFF');
}

function spr_toggle_bundle_packaging_on_submit(frm) {
	if (!frm || !frm.doc || !frm.doc.name) {
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		frappe.msgprint(__('Save as draft to change bundle packaging mode.'));
		return;
	}
	if (!frappe.meta.get_docfield('Shaft Production Run', 'custom_use_bundle_packaging_on_submit')) {
		frappe.msgprint(__('Run bench migrate to enable the bundle packaging submit toggle.'));
		return;
	}
	const cur = cint(frm.doc.custom_use_bundle_packaging_on_submit);
	const next = cur ? 0 : 1;
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_set_bundle_packaging_on_submit',
		args: {
			shaft_production_run: frm.doc.name,
			enabled: next,
		},
		freeze: true,
		callback(r) {
			const msg = (r.message && r.message.mode_label) || spr_bundle_packaging_toggle_label(next);
			if (frm.doc.custom_use_bundle_packaging_on_submit !== next) {
				frm.set_value('custom_use_bundle_packaging_on_submit', next);
			}
			frappe.show_alert({ message: msg, indicator: next ? 'green' : 'blue' }, 6);
			spr_register_spr_page_buttons(frm);
		},
	});
}

function spr_button_text($btn) {
	return String(($btn && $btn.text && $btn.text()) || '')
		.replace(/\s+/g, ' ')
		.trim();
}

function spr_move_existing_top_buttons_to_tools(frm) {
	if (!frm || !frm.page || typeof frm.page.add_inner_button !== 'function') {
		return;
	}
	const tg = __('Tools');
	const labels = [
		__('View Party Stock'),
		__('View Patty Stock'),
		__('Bora Weight'),
	];
	const $buttons = frm.page.wrapper
		? frm.page.wrapper.find('.page-actions button, .custom-actions button, .standard-actions button')
		: $();
	labels.forEach(function (label) {
		const lbl = String(label || '').trim();
		if (!lbl) return;
		const $btn = $buttons
			.filter(function () {
				return spr_button_text($(this)) === lbl;
			})
			.first();
		if (!$btn.length || $btn.data('spr-moved-to-tools')) {
			return;
		}
		try {
			if (typeof frm.page.remove_inner_button === 'function') {
				frm.page.remove_inner_button(lbl, tg);
				frm.page.remove_inner_button(lbl, 'Tools');
			}
		} catch (e) {}
		try {
			frm.page.add_inner_button(
				lbl,
				function () {
					$btn.trigger('click');
				},
				tg
			);
			$btn.data('spr-moved-to-tools', 1).hide();
		} catch (e) {}
	});
}

function spr_register_spr_page_buttons(frm) {
	if (!frm) {
		return;
	}
	const canRemoveCustom = typeof frm.remove_custom_button === 'function';
	if (canRemoveCustom) {
		try {
			frm.remove_custom_button(__('Manual job'));
		} catch (e) {}
		try {
			frm.remove_custom_button(__('Trail Order'));
		} catch (e) {}
		try {
			frm.remove_custom_button(__('Bundle packaging'));
		} catch (e) {}
		try {
			frm.remove_custom_button(__('Bundle SE on Submit: ON'));
		} catch (e) {}
		try {
			frm.remove_custom_button(__('Bundle SE on Submit: OFF'));
		} catch (e) {}
		try {
			frm.remove_custom_button(__('Select RM batches'));
		} catch (e) {}
	}
	if (!frm.page || typeof frm.page.add_inner_button !== 'function') {
		return;
	}
	const tg = __('Tools');
	const rm = frm.page.remove_inner_button;
	if (typeof rm === 'function') {
		[
			__('Manual job'),
			__('Trail Order'),
			__('Bundle packaging'),
			__('Bundle SE on Submit: ON'),
			__('Bundle SE on Submit: OFF'),
			__('Select RM batches'),
			__('Fix Shaft Numbers'),
		].forEach(function (lbl) {
			try {
				rm.call(frm.page, lbl);
			} catch (e) {}
		});
		[
			__('SPR — Manual job'),
			__('SPR — Trail Order'),
			__('SPR — Bundle packaging'),
			__('SPR — Select RM batches'),
			__('Fix Shaft Numbers'),
			spr_bundle_packaging_toggle_label(0),
			spr_bundle_packaging_toggle_label(1),
		].forEach(function (lbl) {
			try {
				rm.call(frm.page, lbl, tg);
			} catch (e) {}
			try {
				rm.call(frm.page, lbl, 'Tools');
			} catch (e) {}
		});
		[__('Mixing Sheet'), __('Select RM batches'), __('SPR — Select RM batches')].forEach(function (lbl) {
			try {
				rm.call(frm.page, lbl, tg);
			} catch (e) {}
			try {
				rm.call(frm.page, lbl, 'Tools');
			} catch (e) {}
		});
	}
	function spr_unhide_toolbar_button(label) {
		if (!frm.page || !frm.page.wrapper) {
			return;
		}
		const lbl = String(label || '').trim();
		frm.page.wrapper
			.find('.page-actions button, .custom-actions button, .standard-actions button')
			.filter(function () {
				return spr_button_text($(this)) === lbl;
			})
			.each(function () {
				$(this).show().removeData('spr-moved-to-tools').css('display', '');
			});
	}
	spr_unhide_toolbar_button(__('Mixing Sheet'));
	spr_unhide_toolbar_button(__('Select RM batches'));
	function addInner(fn) {
		try {
			fn();
		} catch (e) {}
	}
	addInner(function () {
		if (cint(frm.doc.docstatus) === 1 && frm.doc.name) {
			frm.add_custom_button(
				__('Manufacture Summary'),
				function () {
					spr_open_manufacture_summary(frm);
				},
				__('View')
			);
		}
	});
	addInner(function () {
		if (frm.doc.name && production_entry.spr_patty_stock && typeof production_entry.spr_patty_stock.open_dialog === 'function') {
			frm.add_custom_button(__('View Patty Stock'), function () {
				production_entry.spr_patty_stock.open_dialog(frm.doc.name);
			});
		}
	});
	addInner(function () {
		if (!frm.doc || !frm.doc.name || frm.is_new()) {
			return;
		}
		frm.page.add_inner_button(
			__('Force Recalculate Wastage'),
			function () {
				frappe.confirm(
					__(
						'Rebuild Running Patty Wastage and Recycled Wastage Details from Available Jobs / rolls? This overwrites current wastage rows.'
					),
					function () {
						frappe.call({
							method:
								'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.force_recalculate_spr_wastage_and_recycle',
							args: { spr_name: frm.doc.name },
							freeze: true,
							freeze_message: __('Force calculating wastage & recycle…'),
							callback: function (r) {
								const msg = r.message || {};
								if (msg.status === 'no_computed') {
									frappe.msgprint({
										title: __('Could not calculate'),
										message: msg.message || __('No wastage could be computed from jobs/rolls.'),
										indicator: 'orange',
									});
									return;
								}
								frappe.show_alert({
									message: __(
										'Wastage rows: {0}, Recycle rows: {1}',
										[msg.patty_rows || 0, msg.recycled_rows || 0]
									),
									indicator: 'green',
								});
								frm.reload_doc();
							},
						});
					}
				);
			},
			tg
		);
	});
	addInner(function () {
		if (cint(frm.doc.docstatus) === 1 && frm.doc.name) {
			frm.add_custom_button(__('Transfer'), function () {
				try {
					if (
						typeof production_entry !== 'undefined' &&
						production_entry.spr_transfer &&
						typeof production_entry.spr_transfer.open === 'function'
					) {
						production_entry.spr_transfer.open(frm);
						return;
					}
					if (
						typeof production_scheduler !== 'undefined' &&
						typeof production_scheduler.openSprTransferDialog === 'function'
					) {
						production_scheduler.openSprTransferDialog(frm.doc.name);
						return;
					}
					frappe.msgprint(
						__('Transfer dialog not loaded. Run bench build --app production_entry and hard-refresh (Ctrl+Shift+R).')
					);
				} catch (e) {
					console.error('SPR Transfer button', e);
					frappe.msgprint(__('Transfer failed to open. Check browser console.'));
				}
			});
		}
	});
	addInner(function () {
		if (
			frappe.meta.get_docfield('Shaft Production Run', 'custom_use_bundle_packaging_on_submit') &&
			cint(frm.doc.docstatus) === 0
		) {
			const bundleOn = cint(frm.doc.custom_use_bundle_packaging_on_submit);
			frm.page.add_inner_button(
				spr_bundle_packaging_toggle_label(bundleOn),
				function () {
					spr_toggle_bundle_packaging_on_submit(frm);
				},
				tg
			);
		}
	});
	addInner(function () {
		frm.page.add_inner_button(
			__('SPR — Manual job'),
			function () {
				spr_open_manual_job_dialog(frm);
			},
			tg
		);
	});
	addInner(function () {
		frm.page.add_inner_button(
			__('SPR — Trail Order'),
			function () {
				spr_open_trial_order_dialog(frm);
			},
			tg
		);
	});
	addInner(function () {
		frm.page.add_inner_button(
			__('SPR — Bundle packaging'),
			function () {
				spr_open_bundle_packaging_dialog(frm);
			},
			tg
		);
	});
	addInner(function () {
		if (cint(frm.doc.docstatus) === 0 && sprIsBundlePackagingMode(frm) && frm.doc.production_plan) {
			frm.page.add_inner_button(
				__('Reload bundle from PP'),
				function () {
					frappe.call({
						method:
							'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_refresh_bundle_calculation_from_pp',
						args: { shaft_production_run: frm.doc.name },
						freeze: true,
						callback: function (r) {
							if (!r.exc) {
								frm.reload_doc();
								frappe.show_alert({
									message: __('Loaded {0} bundle row(s) from Production Plan.', [
										(r.message && r.message.rows) || 0,
									]),
									indicator: 'green',
								});
							}
						},
					});
				},
				tg
			);
		}
	});
	addInner(function () {
		if (
			frappe.meta.get_docfield('Shaft Production Run', 'fabric_batch_picks') &&
			cint(frm.doc.docstatus) === 0
		) {
			frm.add_custom_button(__('Select RM batches'), function () {
				spr_open_fabric_batch_pick_dialog(frm);
			});
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0 && (frm.doc.items || []).length) {
			frm.page.add_inner_button(
				__('Fix Shaft Numbers'),
				function () {
					frappe.call({
						method:
							'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.backfill_spr_roll_shaft_numbers',
						args: { spr_name: frm.doc.name },
						freeze: true,
						callback: function (r) {
							if (r.exc) {
								return;
							}
							const fixed = cint((r.message || {}).rows_fixed);
							frappe.show_alert({
								message: fixed
									? __('Fixed {0} roll row(s)', [fixed])
									: __('No shaft numbers needed fixing'),
								indicator: fixed ? 'green' : 'blue',
							});
							if (fixed) {
								frm.reload_doc();
							}
						},
					});
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0) {
			frm.page.add_inner_button(
				__('Round Cutting GSM Test'),
				function () {
					const qc = production_entry.spr_quality_check;
					if (qc && typeof qc.openSprRoundCuttingGsmTesting === 'function') {
						qc.openSprRoundCuttingGsmTesting(frm.doc.name);
					} else if (qc && typeof qc.openSprGsmTesting === 'function') {
						qc.openSprGsmTesting(frm.doc.name);
					} else {
						frappe.msgprint(__('Quality Check helper not loaded.'));
					}
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0) {
			frm.page.add_inner_button(
				__('Patty Cutting GSM Test'),
				function () {
					const qc = production_entry.spr_quality_check;
					if (qc && typeof qc.openSprPattyCuttingGsmTesting === 'function') {
						qc.openSprPattyCuttingGsmTesting(frm.doc.name);
					} else {
						frappe.msgprint(__('Quality Check helper not loaded.'));
					}
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0) {
			frm.page.add_inner_button(
				__('Tensile Testing'),
				function () {
					const qc = production_entry.spr_quality_check;
					if (qc && typeof qc.openSprTensileTesting === 'function') {
						qc.openSprTensileTesting(frm.doc.name);
					} else {
						frappe.msgprint(__('Quality Check helper not loaded.'));
					}
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0) {
			frm.page.add_inner_button(
				__('Colour Spectrum'),
				function () {
					const qc = production_entry.spr_quality_check;
					if (qc && typeof qc.openSprColourSpectrum === 'function') {
						qc.openSprColourSpectrum(frm.doc.name);
					} else {
						frappe.msgprint(__('Quality Check helper not loaded.'));
					}
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) === 0) {
			frm.page.add_inner_button(
				__('SPR — Diagnose save'),
				function () {
					frappe.call({
						method:
							'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_diagnose_save_blockers',
						args: { spr_name: frm.doc.name },
						freeze: true,
						callback: function (r) {
							const d = r.message || {};
							let html = '<table class="table table-bordered table-condensed"><tbody>';
							html +=
								'<tr><th>' +
								__('Validate') +
								'</th><td>' +
								(d.validate_ok ? __('OK') : __('Failed')) +
								'</td></tr>';
							if (d.validate_error) {
								html +=
									'<tr><th>' +
									__('Error') +
									'</th><td style="color:#c0392b;white-space:pre-wrap">' +
									frappe.utils.escape_html(String(d.validate_error)) +
									'</td></tr>';
							}
							html +=
								'<tr><th>' +
								__('Bag SPR') +
								'</th><td>' +
								(cint(d.is_bag_spr) ? __('Yes') : __('No')) +
								'</td></tr>';
							html +=
								'<tr><th>' +
								__('RM batch picks') +
								'</th><td>' +
								String(d.fabric_batch_picks_count || 0) +
								'</td></tr>';
							if (d.rm_batch_context) {
								html +=
									'<tr><th>' +
									__('RM dialog lines') +
									'</th><td>' +
									String(d.rm_batch_context.line_count || 0) +
									' (needs_picks=' +
									String(!!d.rm_batch_context.needs_picks) +
									')</td></tr>';
							}
							if (d.duplicate_custom_fields && d.duplicate_custom_fields.length) {
								html +=
									'<tr><th>' +
									__('Duplicate fields') +
									'</th><td style="color:#c0392b">' +
									d.duplicate_custom_fields
										.map(function (x) {
											return x.fieldname;
										})
										.join(', ') +
									' — ' +
									__('run bench migrate') +
									'</td></tr>';
							}
							if (d.batch_prefix_note) {
								html +=
									'<tr><th>' +
									__('Batch prefix') +
									'</th><td>' +
									frappe.utils.escape_html(String(d.batch_prefix_note)) +
									'</td></tr>';
							}
							html += '</tbody></table>';
							if (d.form_dirty_causes && d.form_dirty_causes.length) {
								html += '<p><b>' + __('Notes') + '</b></p><ul>';
								d.form_dirty_causes.forEach(function (line) {
									html += '<li>' + frappe.utils.escape_html(String(line)) + '</li>';
								});
								html += '</ul>';
							}
							frappe.msgprint({
								title: __('SPR save diagnosis — {0}', [frm.doc.name]),
								message: html,
								wide: true,
							});
						},
					});
				},
				tg
			);
		}
	});
	addInner(function () {
		if (!frm.is_new() && cint(frm.doc.docstatus) < 2) {
			frm.page.add_inner_button(
				__('SPR — Sync Batches'),
				function () {
					frappe.confirm(
						__('Post any missing Manufacture rows and activate empty roll batches from this SPR?'),
						function () {
							frappe.call({
								method:
									'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_sync_batches_to_manufacture_entries',
								args: { shaft_production_run: frm.doc.name },
								freeze: false,
								callback: function (r) {
									const d = r.message || {};
									let msg = __(
										'Updated {0} FG line(s), activated {1}, repaired {2}, backfilled {3} manufacture entries.',
										[
											d.updated_fg_lines || 0,
											d.activated_batches || 0,
											d.repaired_batches || 0,
											d.backfilled_entries || 0,
										]
									);
									if (d.force_activated) {
										msg += '<br>' + __('Force-activated {0} empty batch(es) from roll weights.', [d.force_activated]);
									}
									if (d.skipped_count) {
										msg += '<br>' + __('Skipped {0} line(s) — see Error Log if batches still empty.', [d.skipped_count]);
									}
									if (d.backfill_errors && d.backfill_errors.length) {
										msg += '<br><span style="color:#e65100;">' + __('Some WOs could not backfill — RM transfer may be needed.') + '</span>';
									}
									frappe.msgprint({
										title: __('Batch Sync Result'),
										message: msg,
										indicator: (d.activated_batches || d.updated_fg_lines || d.repaired_batches) ? 'green' : 'orange',
									});
									frm.reload_doc();
								},
							});
						}
					);
				},
				tg
			);
		}
	});
	setTimeout(function () {
		spr_move_existing_top_buttons_to_tools(frm);
	}, 80);
	setTimeout(function () {
		spr_move_existing_top_buttons_to_tools(frm);
	}, 500);
}

/** After Save the toolbar is rebuilt asynchronously — retry so Manual job / Bundle packaging stay visible. */
function spr_register_spr_page_buttons_after_save(frm) {
	spr_register_spr_page_buttons(frm);
	[120, 300, 600, 1200, 2000, 3500, 5000].forEach(function (ms) {
		setTimeout(function () {
			spr_register_spr_page_buttons(frm);
		}, ms);
	});
}

/** Default WO qty (Kg): net per roll × rolls per shaft × number of shafts (deck positions). */
function sprManualDefaultWoQty(line, noShafts, noRolls) {
	const shafts = cint(noShafts);
	const rolls = cint(noRolls);
	const s = shafts > 0 ? shafts : 1;
	const r = rolls > 0 ? rolls : 1;
	const nps = line.net_per_shaft_kg != null ? flt(line.net_per_shaft_kg) : null;
	if (nps != null && nps > 0) {
		return nps * r * s;
	}
	const fs =
		line.first_segment_planned_kg != null && line.first_segment_planned_kg !== ''
			? flt(line.first_segment_planned_kg)
			: null;
	if (fs != null && fs > 0) {
		return fs * r * s;
	}
	const pq = flt(line.planned_qty);
	return pq > 0 ? pq : 1;
}

function sprManualNormalizeWidthToken(token) {
	const raw = String(token || '')
		.replace(/inch|inches|in/gi, '')
		.replace(/["']/g, '')
		.trim();
	if (!raw) return 0;
	return flt(raw);
}

function sprManualParseCombination(text) {
	return String(text || '')
		.split('+')
		.map(function (part) {
			return sprManualNormalizeWidthToken(part);
		})
		.filter(function (w) {
			return w > 0;
		});
}

/** Actions ΓåÆ Manual job: multi-select PP lines; WO qty defaults to net/shaft × shafts from Available Jobs. */
function spr_open_manual_job_dialog(frm) {
	if (frm.is_new() || !frm.doc.name) {
		frappe.msgprint(__('Save the Shaft Production Run first.'));
		return;
	}
	if (frm.doc.docstatus && frm.doc.docstatus !== 0) {
		frappe.msgprint(__('This document is submitted and cannot be edited.'));
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_manual_job_catalog',
		args: { shaft_production_run: frm.doc.name },
		freeze: true,
		freeze_message: __('Loading Production Plan lines...'),
		callback: function (r) {
			const payload = r.message || {};
			const lines = payload.lines || [];
			const ppName = payload.production_plan || '';
			const sprUnit = payload.custom_unit || frm.doc.custom_unit || '';
			const maxShaftInches = flt(payload.max_shaft_inches || 0);
			if (!lines.length) {
				frappe.msgprint(
					__('No Production Plan lines found. Set Production Plan and ensure it has planned items.')
				);
				return;
			}
			const d = new frappe.ui.Dialog({
				title: __('Manual job'),
				fields: [
					{
						fieldname: 'spr_manual_ui_style',
						fieldtype: 'HTML',
						options:
							'<style>' +
							'.spr-manual-shell{background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:12px 14px;margin-bottom:10px;}' +
							'.spr-manual-shell b{font-weight:600;color:#0f172a;}' +
							'.spr-manual-toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0 10px;}' +
							'.spr-manual-summary{margin-left:auto;font-size:12px;color:#334155;background:#eef2ff;border:1px solid #c7d2fe;border-radius:999px;padding:4px 10px;}' +
							'.spr-manual-table-wrap{overflow:auto;border:1px solid #dbe2ea;border-radius:12px;background:#fff;max-height:360px;}' +
							'.spr-manual-table{font-size:12px;margin:0;min-width:1020px;table-layout:fixed;}' +
							'.spr-manual-table thead th{position:sticky;top:0;background:#f1f5f9;z-index:1;border-bottom:1px solid #cbd5e1;color:#334155;}' +
							'.spr-manual-table tbody tr:hover{background:#f8fafc;}' +
							'.spr-manual-row-selected{background:#ecfeff !important;}' +
							'.spr-manual-table input[type=number],.spr-manual-table select{height:28px;font-size:12px;}' +
							'</style>',
					},
					{
						fieldname: 'spr_manual_pp_hint',
						fieldtype: 'HTML',
						options:
							'<div class="spr-manual-shell">' +
							'<div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;">' +
							'<div><b>' +
							__('Manual Work Order Planner') +
							'</b><div class="text-muted small">' +
							__('Choose shafts/roll logic, then confirm rows below.') +
							'</div></div>' +
							'<div class="text-muted small">' +
							__('Production Plan: {0}', [ppName || '—']) +
							(sprUnit && maxShaftInches > 0
								? '<br>' +
								  __('Unit: {0} — max combination width {1}"', [
										sprUnit,
										String(maxShaftInches),
								  ])
								: '') +
							'</div></div></div>',
					},
					{
						fieldname: 'no_of_shafts',
						fieldtype: 'Int',
						label: __('Number of shafts (deck positions)'),
						reqd: 1,
						default: 1,
					},
					{
						fieldname: 'no_of_rolls',
						fieldtype: 'Int',
						label: __('Number of rolls (per shaft)'),
						reqd: 1,
						default: 1,
					},
					{
						fieldname: 'combination_gsm',
						fieldtype: 'Int',
						label: __('Combination GSM'),
					},
					{
						fieldname: 'combination_input',
						fieldtype: 'Data',
						label: __('Combination widths (Inches)'),
						description: __('Example: 34+34+42. Same GSM only. Total rolls = segments × shafts.'),
					},
					{
						fieldname: 'combination_status_html',
						fieldtype: 'HTML',
						options: '<div class="text-muted small spr-manual-combination-status"></div>',
					},
					{
						fieldname: 'line_select_html',
						fieldtype: 'HTML',
						label: __('Select items'),
						options:
							'<div class="spr-manual-toolbar">' +
							'<button type="button" class="btn btn-xs btn-default spr-manual-select-all">' +
							__('Select all') +
							'</button>' +
							'<button type="button" class="btn btn-xs btn-default spr-manual-select-none">' +
							__('Clear') +
							'</button>' +
							'<span class="spr-manual-summary spr-manual-selection-summary">—</span>' +
							'</div>' +
							'<div class="spr-manual-lines-wrap"></div>' +
							'<p class="text-muted small" style="margin-top:6px;">' +
							__('WO qty default = net/roll Kg x rolls x shafts') +
							'</p>',
					},
				],
				primary_action_label: __('Create Work Order(s)'),
				primary_action: function () {
					const no_of_shafts = cint(d.get_value('no_of_shafts'));
					const no_of_rolls = cint(d.get_value('no_of_rolls'));
					if (no_of_shafts < 1) {
						frappe.msgprint(__('Number of shafts must be at least 1.'));
						return;
					}
					if (no_of_rolls < 1) {
						frappe.msgprint(__('Number of rolls per shaft must be at least 1.'));
						return;
					}
					const items = [];
					const comboRaw = String(d.get_value('combination_input') || '').trim();
					const comboMode = !!comboRaw;
					lines.forEach(function (line, idx) {
						const cb = d.$wrapper.find('.spr-manual-inc[data-idx="' + idx + '"]');
						if (!cb.length || !cb.is(':checked')) {
							return;
						}
						const q = flt(d.$wrapper.find('.spr-manual-qty[data-idx="' + idx + '"]').val());
						const mr = flt(d.$wrapper.find('.spr-manual-meter-roll[data-idx="' + idx + '"]').val());
						if (!(q > 0)) {
							frappe.msgprint(__('Enter valid Work Order qty for selected line.'));
							return;
						}
						if (!(mr > 0)) {
							frappe.msgprint(__('Enter valid Meter/Roll for selected line.'));
							return;
						}
						items.push({
							item_code: line.item_code,
							production_plan_item: line.production_plan_item,
							wo_qty: q,
							meter_roll: mr,
							width_inch: getManualLineWidth(idx),
							roll_count_per_shaft: comboMode ? cint(line.__combo_roll_count_per_shaft || 1) : cint(d.get_value('no_of_rolls')) || 1,
							selected_reuse_work_order:
								d.$wrapper.find('.spr-manual-reuse-wo[data-idx="' + idx + '"]').val() || '',
						});
					});
					if (!items.length) {
						frappe.msgprint(__('Select at least one line with valid Meter/Roll and Work Order qty.'));
						return;
					}
					const finalItems = [];
					if (comboMode) {
						const byItem = new Map();
						items.forEach(function (it) {
							const key = [
								it.item_code || '',
								it.selected_reuse_work_order || '',
							].join('::');
							if (!byItem.has(key)) {
								byItem.set(key, {
									item_code: it.item_code,
									production_plan_item: it.production_plan_item,
									wo_qty: 0,
									meter_roll: it.meter_roll,
									width_inch: it.width_inch,
									roll_count_per_shaft: 0,
									selected_reuse_work_order: it.selected_reuse_work_order || '',
								});
							}
							const agg = byItem.get(key);
							agg.wo_qty += flt(it.wo_qty);
							agg.roll_count_per_shaft += cint(it.roll_count_per_shaft || 1);
						});
						byItem.forEach(function (v) {
							finalItems.push(v);
						});
					} else {
						Array.prototype.push.apply(finalItems, items);
					}
					const runManualCreate = function () {
						d.hide();
						frappe.call({
						method:
							'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_create_manual_jobs_multi',
						args: {
							shaft_production_run: frm.doc.name,
							no_of_shafts: no_of_shafts,
							no_of_rolls: comboMode ? 1 : cint(d.get_value('no_of_rolls')) || 1,
							items: finalItems,
							combination_input: comboRaw,
						},
						freeze: true,
						freeze_message: __('Creating / fetching Work Order(s)...'),
						callback: function (r2) {
							const m = r2.message || {};
							const wos = (m.work_orders || []).join(', ');
							const reused = (m.reused_work_orders || []).join(', ');
							const msg = reused
								? __('Work Order(s) {0} (job {1}). Reused unused manual WO(s): {2}.', [
										wos || '',
										m.job_id || '',
										reused,
								  ])
								: __('Work Order(s) {0} (job {1}).', [wos || '', m.job_id || '']);
							frappe.show_alert({
								message: msg,
								indicator: 'green',
							});
							frm.reload_doc();
						},
					});
					};
					// Important: server APIs read DB state. Save latest row/job deletions first so
					// manual WO reuse does not create a new WO from stale pre-delete links.
					if (frm.is_dirty && frm.is_dirty()) {
						const p = frm.save();
						if (p && typeof p.then === 'function') {
							p.then(function () {
								runManualCreate();
							}).catch(function () {
								frappe.msgprint(__('Could not save latest SPR changes. Please save and try Manual job again.'));
							});
						} else {
							setTimeout(runManualCreate, 250);
						}
						return;
					}
					runManualCreate();
				},
			});

			function getManualLineWidth(idx) {
				const inp = d.$wrapper.find('.spr-manual-width[data-idx="' + idx + '"]');
				if (inp.length) {
					const v = flt(inp.val());
					if (v > 0) {
						return v;
					}
				}
				const line = lines[idx];
				if (!line) {
					return 0;
				}
				const fromName = sprParseWidthFromItemName(line.item_name);
				if (fromName > 0) {
					return fromName;
				}
				return flt(line.width_inch) || sprWidthInchFromItemCode(line.item_code);
			}

			function renderManualLinesTable() {
				const nShafts = cint(d.get_value('no_of_shafts'));
				const nRolls = cint(d.get_value('no_of_rolls')) || 1;
				const wrap = d.$wrapper.find('.spr-manual-lines-wrap');
				if (!wrap.length) {
					return;
				}
				let html =
					'<div class="spr-manual-table-wrap">' +
					'<table class="table table-bordered table-condensed spr-manual-table">';
				html +=
					'<thead><tr><th style="width:36px;"></th><th>' +
					__('Item / PP row') +
					'</th><th style="width:110px;">' +
					__('Order Code') +
					'</th><th style="width:70px;">' +
					__('Width (Inches)') +
					'</th><th style="width:55px;">' +
					__('GSM') +
					'</th><th style="width:110px;">' +
					__('Meter/Roll') +
					'</th><th style="width:95px;">' +
					__('Net/roll (Kg)') +
					'</th><th style="width:190px;">' +
					__('Reuse WO') +
					'</th><th style="width:110px;">' +
					__('WO qty (Kg)') +
					'</th></tr></thead><tbody>';
				lines.forEach(function (line, idx) {
					const wIn =
						sprParseWidthFromItemName(line.item_name) ||
						flt(line.width_inch) ||
						sprWidthInchFromItemCode(line.item_code);
					const nps =
						line.net_per_shaft_kg != null && line.net_per_shaft_kg !== ''
							? flt(line.net_per_shaft_kg)
							: null;
					const npsLabel =
						nps != null && nps > 0
							? nps.toFixed(2) +
							  (line.matched_job_id
								  ? ' (' + __('job') + ' ' + String(line.matched_job_id) + ')'
								  : '')
							: '—';
					const defQ = sprManualDefaultWoQty(line, nShafts, nRolls);
					const itemName = String(line.item_name || '').trim();
					const label =
						String(line.item_code || '') +
						(itemName ? ' — ' + itemName.substring(0, 40) : '');
					html += '<tr>';
					html +=
						'<td style="text-align:center;"><input type="checkbox" class="spr-manual-inc" data-idx="' +
						idx +
						'" checked /></td>';
					html +=
						'<td style="max-width:360px;white-space:normal;word-break:break-word;">' +
						frappe.utils.escape_html(label) +
						'</td>';
					html += '<td>' + frappe.utils.escape_html(line.order_code || '') + '</td>';
					html +=
						'<td><input type="number" class="input-with-feedback spr-manual-width" data-idx="' +
						idx +
						'" value="' +
						wIn.toFixed(1) +
						'" step="0.1" min="0" style="width:70px"/></td>';
					html +=
						'<td>' +
						(line.gsm != null && line.gsm !== '' ? cint(line.gsm) : '—') +
						'</td>';
					html +=
						'<td><input type="number" class="input-with-feedback spr-manual-meter-roll" data-idx="' +
						idx +
						'" value="500" step="0.1" style="width:100px" placeholder="500"/></td>';
					html += '<td>' + frappe.utils.escape_html(npsLabel) + '</td>';
					const reuseWos = Array.isArray(line.reusable_work_orders) ? line.reusable_work_orders : [];
					let woSelect =
						'<select class="input-with-feedback spr-manual-reuse-wo" data-idx="' +
						idx +
						'" style="width:170px"><option value="">' +
						frappe.utils.escape_html(__('Auto (reuse latest unused)')) +
						'</option><option value="__NEW__">' +
						frappe.utils.escape_html(__('Create New WO')) +
						'</option>';
					reuseWos.forEach(function (wo) {
						woSelect +=
							'<option value="' +
							frappe.utils.escape_html(String(wo)) +
							'">' +
							frappe.utils.escape_html(String(wo)) +
							'</option>';
					});
					woSelect += '</select>';
					html += '<td style="white-space:nowrap;">' + woSelect + '</td>';
					html +=
						'<td><input type="number" class="input-with-feedback spr-manual-qty" data-idx="' +
						idx +
						'" value="' +
						defQ.toFixed(2) +
						'" step="0.001" style="width:100px"/></td>';
					html += '</tr>';
				});
				html += '</tbody></table></div>';
				wrap.html(html);
				applyManualCombinationSelection();
				updateManualSelectionSummary();
			}

			function updateManualSelectionSummary() {
				const checked = d.$wrapper.find('.spr-manual-inc:checked');
				let totalQty = 0;
				d.$wrapper.find('.spr-manual-table tbody tr').removeClass('spr-manual-row-selected');
				checked.each(function () {
					const idx = cint($(this).attr('data-idx'));
					const q = flt(d.$wrapper.find('.spr-manual-qty[data-idx="' + idx + '"]').val());
					totalQty += q > 0 ? q : 0;
					$(this).closest('tr').addClass('spr-manual-row-selected');
				});
				d.$wrapper.find('.spr-manual-selection-summary').text(
					__('Selected: {0} row(s) | WO Qty: {1} Kg', [checked.length, totalQty.toFixed(2)])
				);
			}

			function setManualCombinationStatus(message, colorClass) {
				const wrap = d.$wrapper.find('.spr-manual-combination-status');
				if (!wrap.length) return;
				const cls = colorClass || 'text-muted';
				wrap.html(
					message
						? '<span class="' + cls + '">' + frappe.utils.escape_html(message) + '</span>'
						: ''
				);
			}

			function syncManualCombinationMode() {
				return false;
			}

			function recalcManualQtyInputs() {
				const nShafts = cint(d.get_value('no_of_shafts')) || 1;
				const nRolls = cint(d.get_value('no_of_rolls')) || 1;
				const comboRaw = String(d.get_value('combination_input') || '').trim();
				if (comboRaw) {
					applyManualCombinationSelection();
					return;
				}
				d.$wrapper.find('.spr-manual-inc:checked').each(function () {
					const idx = cint($(this).attr('data-idx'));
					const line = lines[idx];
					if (!line) return;
					d.$wrapper
						.find('.spr-manual-qty[data-idx="' + idx + '"]')
						.val(sprManualDefaultWoQty(line, nShafts, nRolls).toFixed(2));
				});
				updateManualSelectionSummary();
			}

			function applyManualCombinationSelection() {
				const comboRaw = String(d.get_value('combination_input') || '').trim();
				if (!comboRaw) {
					setManualCombinationStatus('');
					return;
				}
				const comboGsm = cint(d.get_value('combination_gsm'));
				if (comboGsm < 1) {
					setManualCombinationStatus(__('Enter Combination GSM to auto-select widths.'), 'text-warning');
					return;
				}
				const widths = sprManualParseCombination(comboRaw);
				if (!widths.length) {
					setManualCombinationStatus(__('Enter widths like 34+34+42.'), 'text-warning');
					return;
				}
				const totalWidth = widths.reduce(function (sum, w) {
					return sum + flt(w);
				}, 0);
				if (maxShaftInches > 0 && totalWidth > maxShaftInches + 1e-6) {
					setManualCombinationStatus(
						__(
							'{0} maximum shaft width is {1}". Combination {2} = {3}" is not allowed.',
							[sprUnit || __('Unit'), String(maxShaftInches), comboRaw, totalWidth.toFixed(1)]
						),
						'text-danger'
					);
					return;
				}
				const picks = [];
				const countsByIdx = {};
				for (let i = 0; i < widths.length; i++) {
					const targetWidth = flt(widths[i]);
					let matchIdx = -1;
					for (let j = 0; j < lines.length; j++) {
						const line = lines[j];
						if (cint(line.gsm) !== comboGsm) continue;
						if (Math.abs(getManualLineWidth(j) - targetWidth) > 0.05) continue;
						matchIdx = j;
						break;
					}
					if (matchIdx === -1) {
						setManualCombinationStatus(
							__('No unused PP line found for GSM {0} width {1} Inches.', [comboGsm, targetWidth]),
							'text-danger'
						);
						return;
					}
					picks.push(matchIdx);
					countsByIdx[matchIdx] = (countsByIdx[matchIdx] || 0) + 1;
				}

				d.$wrapper.find('.spr-manual-inc').prop('checked', false);
				lines.forEach(function (line) {
					delete line.__combo_roll_count_per_shaft;
				});
				Object.keys(countsByIdx).forEach(function (idxStr) {
					const idx = cint(idxStr);
					const rollCount = cint(countsByIdx[idx] || 1);
					lines[idx].__combo_roll_count_per_shaft = rollCount;
					d.$wrapper.find('.spr-manual-inc[data-idx="' + idx + '"]').prop('checked', true);
					const nRollsHdr = cint(d.get_value('no_of_rolls')) || 1;
					d.$wrapper
						.find('.spr-manual-qty[data-idx="' + idx + '"]')
						.val(
							sprManualDefaultWoQty(
								lines[idx],
								cint(d.get_value('no_of_shafts')) || 1,
								rollCount
							).toFixed(2)
						);
				});
				setManualCombinationStatus(
					__('Selected {0} segment(s) for GSM {1}: {2}', [
						picks.length,
						comboGsm,
						widths.join(' + '),
					]),
					'text-success'
				);
			}

			d.show();
			try {
				d.$wrapper.find('.modal-dialog').css('max-width', '1100px');
			} catch (e) {}
			renderManualLinesTable();
			d.$wrapper.on('click', '.spr-manual-select-all', function () {
				d.$wrapper.find('.spr-manual-inc').prop('checked', true);
				updateManualSelectionSummary();
			});
			d.$wrapper.on('click', '.spr-manual-select-none', function () {
				d.$wrapper.find('.spr-manual-inc').prop('checked', false);
				updateManualSelectionSummary();
			});
			d.$wrapper.on('change input', '.spr-manual-inc, .spr-manual-qty, .spr-manual-width', function () {
				updateManualSelectionSummary();
			});
			const ns = d.fields_dict.no_of_shafts;
			if (ns && ns.$input) {
				ns.$input.on('change input', function () {
					syncManualCombinationMode();
					recalcManualQtyInputs();
				});
			}
			const nr = d.fields_dict.no_of_rolls;
			if (nr && nr.$input) {
				nr.$input.on('change input', function () {
					syncManualCombinationMode();
					recalcManualQtyInputs();
				});
			}
			const cg = d.fields_dict.combination_gsm;
			if (cg && cg.$input) {
				cg.$input.on('change input', function () {
					syncManualCombinationMode();
					if (String(d.get_value('combination_input') || '').trim()) {
						applyManualCombinationSelection();
					} else {
						renderManualLinesTable();
					}
				});
			}
			const ci = d.fields_dict.combination_input;
			if (ci && ci.$input) {
				ci.$input.on('change input', function () {
					syncManualCombinationMode();
					if (String(d.get_value('combination_input') || '').trim()) {
						applyManualCombinationSelection();
					} else {
						renderManualLinesTable();
					}
				});
			}
		},
	});
}

/** Net weight per roll (Kg) for trial fabric lines. */
function sprTrialNetPerRollKg(gsm, widthInch, meterRoll) {
	const g = flt(gsm);
	const w = flt(widthInch);
	const m = flt(meterRoll);
	if (!(g > 0 && w > 0 && m > 0)) return 0;
	return (g * w * m * 0.0254) / 1000;
}

/** WO qty (Kg) = net/roll × rolls × shafts. */
function sprTrialDefaultWoQty(line, nShafts, nRolls) {
	const mr = flt(line.meter_roll) || 500;
	const net = sprTrialNetPerRollKg(line.gsm, line.width_inch, mr);
	const s = cint(nShafts) > 0 ? cint(nShafts) : 1;
	const r = cint(nRolls) > 0 ? cint(nRolls) : 1;
	return net > 0 ? net * r * s : 1;
}

/** Actions → Trail Order: opens in-place and creates a NEW Shaft Production Run. */
function spr_open_trial_order_dialog(frm) {
	const opener =
		typeof production_entry !== "undefined" &&
		production_entry.spr_trial_order &&
		typeof production_entry.spr_trial_order.openDialog === "function"
			? production_entry.spr_trial_order.openDialog
			: null;
	if (!opener) {
		frappe.msgprint(__("Trail Order dialog is not loaded. Hard-refresh the page (Ctrl+Shift+R)."));
		return;
	}
	opener({
		unit: frm.doc.custom_unit,
		runDate: frm.doc.run_date,
		shift: frm.doc.shift,
		operator: frm.doc.operator || frm.doc.custom_operator || "",
		supervisor: frm.doc.supervisor || frm.doc.custom_supervisor || "",
		shaftProductionRun: frm.doc.name,
		onSuccess: function (m) {
			const name = m && m.shaft_production_run;
			if (name) {
				frappe.set_route("Form", "Shaft Production Run", name);
			} else if (frm.reload_doc) {
				frm.reload_doc();
			}
		},
	});
}

/** Parse combination string into inch widths (mirrors server _parse_combination_widths_inches). */
function spr_parse_combination_widths_inches(comb) {
	if (!comb) {
		return [];
	}
	const text = String(comb)
		.replace(/\u201c/g, '"')
		.replace(/\u201d/g, '"')
		.replace(/\u2033/g, '"')
		.replace(/\u2032/g, "'");
	return text
		.split('+')
		.map(function (part) {
			const m = part.replace(/,/g, '').match(/(\d+(?:\.\d+)?)/);
			return m ? flt(m[1]) : 0;
		})
		.filter(function (w) {
			return w > 0;
		});
}

function spr_bundle_job_select_label(j) {
	return __('Job') + ' ' + String(j.job_id || '');
}

function spr_bundle_width_select_el(dialog) {
	return dialog.$wrapper.find('select.spr-bundle-width-select');
}

function spr_bundle_set_width_options(dialog, widthValues) {
	const $sel = spr_bundle_width_select_el(dialog);
	if (!$sel.length) {
		return [];
	}
	const seen = new Set();
	const labels = [];
	(widthValues || []).forEach(function (v) {
		const fw = flt(v);
		if (fw <= 0) {
			return;
		}
		const key = String(Math.round(fw * 1000) / 1000);
		if (seen.has(key)) {
			return;
		}
		seen.add(key);
		labels.push(spr_format_width_inch_label(fw));
	});
	labels.sort(function (a, b) {
		return flt(a) - flt(b);
	});
	$sel.empty();
	if (!labels.length) {
		$sel.append($('<option>').val('').text(__('Select width')));
	} else {
		labels.forEach(function (lbl) {
			$sel.append($('<option>').val(lbl).text(lbl + '"'));
		});
		$sel.val(labels[0]);
	}
	return labels;
}

function spr_bundle_get_width(dialog) {
	return flt(spr_bundle_width_select_el(dialog).val());
}

function spr_bundle_resolve_job(jobPickVal, jobById, jobByLabel) {
	const raw = String(jobPickVal || '').trim();
	if (!raw) {
		return null;
	}
	if (jobById[raw]) {
		return jobById[raw];
	}
	if (jobByLabel[raw]) {
		return jobByLabel[raw];
	}
	const m = raw.match(/Job\s+(\S+)/i);
	if (m && jobById[m[1]]) {
		return jobById[m[1]];
	}
	return null;
}

function spr_job_keys_match_js(a, b) {
	const na = String(a || '').trim();
	const nb = String(b || '').trim();
	if (na === nb) {
		return true;
	}
	if (!na || !nb) {
		return false;
	}
	const fa = parseFloat(na);
	const fb = parseFloat(nb);
	if (!isNaN(fa) && !isNaN(fb)) {
		return fa === fb;
	}
	return false;
}

function spr_format_width_inch_label(w) {
	const fw = flt(w);
	if (fw <= 0) {
		return '';
	}
	return Math.abs(fw - Math.round(fw)) < 0.001 ? String(Math.round(fw)) : fw.toFixed(1);
}

/** Force Frappe Dialog Select to show options (legacy helper — prefer spr_bundle_set_width_options). */
function spr_dialog_select_set_options(dialog, fieldname, widthValues) {
	return spr_bundle_set_width_options(dialog, widthValues);
}

function spr_collect_bundle_width_options(jp, widthsByJob, frm, segs) {
	const out = [];
	const seen = new Set();
	function add(w) {
		const fw = flt(w);
		if (fw <= 0) {
			return;
		}
		const key = String(Math.round(fw * 1000) / 1000);
		if (seen.has(key)) {
			return;
		}
		seen.add(key);
		out.push(fw);
	}
	(jp.widths || widthsByJob[jp.job_id] || []).forEach(add);
	spr_parse_combination_widths_inches(jp.combination_text).forEach(add);
	(segs || []).forEach(function (s) {
		add(s.width_inch);
	});
	(frm.doc.items || []).forEach(function (it) {
		const jMatch = String(it.job_no || it.job_id || it.job || '').trim();
		if (spr_job_keys_match_js(jMatch, jp.job_id)) {
			add(it.width_inch);
			if (it.item_code) {
				const parsed = spr_parse_item_width_from_code(it.item_code);
				if (parsed > 0) {
					add(parsed);
				}
			}
		}
	});
	return out.sort(function (a, b) {
		return a - b;
	});
}

function spr_parse_item_width_from_code(itemCode) {
	const s = String(itemCode || '');
	const m = s.match(/(\d+(?:\.\d+)?)\s*(?:IN|INCH|"|''|INCHES)\b/i) || s.match(/-(\d+(?:\.\d+)?)-(?:MM|IN)/i);
	return m ? flt(m[1]) : 0;
}

/** Actions ΓåÆ Bundle packaging: Job + Width from Available Jobs / roll widths; gross applied to all matching rolls. */
function spr_open_bundle_packaging_dialog(frm) {
	if (frm.is_new() || !frm.doc.name) {
		frappe.msgprint(__('Save the Shaft Production Run first.'));
		return;
	}
	if (frm.doc.docstatus && frm.doc.docstatus !== 0) {
		frappe.msgprint(__('This document is submitted and cannot be edited.'));
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_bundle_packaging_catalog',
		args: { shaft_production_run: frm.doc.name },
		freeze: true,
		freeze_message: __('Loading jobs...'),
		callback: function (r) {
			const cat = r.message || {};
			const jobs = cat.jobs || [];
			const widthsByJob = cat.widths_by_job || {};
			if (!jobs.length) {
				frappe.msgprint(__('Add Available Jobs (shaft jobs) first.'));
				return;
			}
			const jobById = {};
			const jobByLabel = {};
			jobs.forEach(function (j) {
				const jid = String(j.job_id);
				j.widths = j.widths || widthsByJob[j.job_id] || [];
				jobById[jid] = j;
				const shortLbl = spr_bundle_job_select_label(j);
				jobByLabel[shortLbl] = j;
				jobByLabel[jid] = j;
				if (j.label) {
					jobByLabel[String(j.label)] = j;
				}
			});
			const jobOpts = jobs.map(function (j) {
				return spr_bundle_job_select_label(j);
			}).join('\n');
			const d = new frappe.ui.Dialog({
				title: __('Bundle packaging'),
				fields: [
					{
						fieldname: 'spr_bundle_hint',
						fieldtype: 'HTML',
						options:
							'<style>' +
							'.spr-bundle-seg-table{font-size:13px;margin:8px 0;width:100%;}' +
							'.spr-bundle-seg-table th{background:#f1f5f9;font-weight:700;padding:6px 8px;}' +
							'.spr-bundle-seg-table td{padding:6px 8px;}' +
							'.spr-bundle-width-wrap{margin:8px 0 12px;}' +
							'.spr-bundle-width-wrap label{font-weight:600;font-size:12px;color:#334155;display:block;margin-bottom:4px;}' +
							'.spr-bundle-width-select{font-size:15px;font-weight:600;min-height:38px;width:100%;}' +
							'</style>' +
							'<p class="text-muted small" style="margin-bottom:10px;">' +
							__(
								'Step 1: pick Job ID. Step 2: pick width for that segment (combination widths and WO items are shown below). Same single-roll gross applies to all roll lines for that job and width. Sticker width = selected width × number of packaging.'
							) +
							'</p>',
					},
					{
						fieldname: 'job_pick',
						fieldtype: 'Select',
						label: __('Job ID (Available Jobs)'),
						options: jobOpts,
						reqd: 1,
					},
					{
						fieldname: 'job_detail_html',
						fieldtype: 'HTML',
						options: '<div class="spr-bundle-job-detail text-muted small"></div>',
					},
					{
						fieldname: 'bundle_width_wrap',
						fieldtype: 'HTML',
						options:
							'<div class="spr-bundle-width-wrap">' +
							'<label>' +
							__('Width / segment (Inches) - pick one row from the table above') +
							'</label>' +
							'<select class="form-control spr-bundle-width-select">' +
							'<option value="">' +
							__('Select width') +
							'</option>' +
							'</select></div>',
					},
					{
						fieldname: 'calc_html',
						fieldtype: 'HTML',
						options: '<div class="spr-bundle-calc text-muted small"></div>',
					},
					{
						fieldname: 'no_of_packaging',
						fieldtype: 'Int',
						label: __('Number of packaging'),
						reqd: 1,
						default: 1,
					},
					{
						fieldname: 'whole_gross_kg',
						fieldtype: 'Float',
						label: __('Whole gross (Kg)'),
						reqd: 1,
					},
					{
						fieldname: 'produced_length_mtrs',
						fieldtype: 'Float',
						label: __('Produced length (Mtrs)'),
						reqd: 1,
					},
				],
				primary_action_label: __('Apply'),
				primary_action: function (values) {
					const jp = spr_bundle_resolve_job(values.job_pick, jobById, jobByLabel);
					const w = spr_bundle_get_width(d);
					const n = cint(values.no_of_packaging);
					const whole = flt(values.whole_gross_kg);
					const producedLength = flt(values.produced_length_mtrs);
					if (!jp || !jp.job_id) {
						frappe.msgprint(__('Select a job.'));
						return;
					}
					if (w <= 0) {
						frappe.msgprint(__('Select a width.'));
						return;
					}
					if (n < 1 || whole <= 0) {
						frappe.msgprint(__('Enter a valid packaging count and whole gross weight.'));
						return;
					}
					if (producedLength <= 0) {
						frappe.msgprint(__('Enter valid produced length.'));
						return;
					}
					d.hide();
					
					function execute_apply() {
						frappe.call({
							method:
								'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_apply_bundle_packaging_for_job_width',
							args: {
								shaft_production_run: frm.doc.name,
								job_id: jp.job_id,
								width_inch: w,
								no_of_packaging: n,
								whole_gross_kg: whole,
								produced_length_mtrs: producedLength,
							},
							freeze: true,
							freeze_message: __('Applying bundle packaging...'),
							callback: function (r2) {
								const m = r2.message || {};
								frappe.show_alert({
									message: __(
										'Updated {0} roll(s). Remaining unpacked: {4}. Single gross {1} Kg, sticker width {2} Inches, bundle net {3} Kg.',
										[
											String(m.updated_rolls != null ? m.updated_rolls : ''),
											String(m.single_roll_gross_kg != null ? m.single_roll_gross_kg : ''),
											String(m.total_width_inch != null ? m.total_width_inch : ''),
											String(m.sticker_bundle_weight_kg != null ? m.sticker_bundle_weight_kg : ''),
											String(m.remaining_unpacked_rolls != null ? m.remaining_unpacked_rolls : ''),
										]
									),
									indicator: 'green',
								});
								frm.reload_doc();
							},
						});
					}

					if (frm.is_dirty()) {
						frappe.show_alert({ message: __('Saving document before apply...'), indicator: 'blue' });
						frm.save().then(execute_apply);
					} else {
						execute_apply();
					}
				},
			});
			function applySegsToDialog(jp, segs) {
				const det = d.$wrapper.find('.spr-bundle-job-detail');
				if (!jp) {
					return;
				}
				let widthOpts = spr_collect_bundle_width_options(jp, widthsByJob, frm, segs);
				if (segs && segs.length) {
					const uniqueSegs = [];
					const seenWidths = new Set();
					segs.forEach(function (s) {
						const w = flt(s.width_inch);
						if (w <= 0) {
							return;
						}
						const key = (Math.round(w * 1000) / 1000).toString();
						if (seenWidths.has(key)) {
							return;
						}
						seenWidths.add(key);
						uniqueSegs.push(s);
					});
					let html =
						'<table class="table table-bordered table-condensed spr-bundle-seg-table"><thead><tr><th>' +
						__('Width') +
						'</th><th>' +
						__('Net/shaft (Kg)') +
						'</th><th>' +
						__('WO item') +
						'</th></tr></thead><tbody>';
					uniqueSegs.forEach(function (s) {
						const net = s.net_kg_per_shaft != null ? flt(s.net_kg_per_shaft).toFixed(2) : '—';
						const ic = [s.item_code || '', (s.item_name || '').substring(0, 28)].join(' ').trim();
						html +=
							'<tr><td><strong>' +
							flt(s.width_inch).toFixed(1) +
							'"</strong></td><td>' +
							net +
							'</td><td>' +
							frappe.utils.escape_html(ic) +
							'</td></tr>';
					});
					html += '</tbody></table>';
					if (det.length) {
						det.html(html);
					}
				} else {
					const comb = jp.combination_text || '';
					if (det.length) {
						let head =
							'<p class="small"><strong>' +
							frappe.utils.escape_html(spr_bundle_job_select_label(jp)) +
							'</strong></p>';
						if (comb) {
							head +=
								'<p class="small text-muted" style="margin:4px 0 8px;">' +
								frappe.utils.escape_html(comb) +
								'</p>';
						}
						det.html(head);
					}
				}
				function finishWidthSelect() {
					const labels = spr_bundle_set_width_options(d, widthOpts);
					if (!labels.length) {
						frappe.msgprint(__('No width options for this job. Check combination / roll lines.'));
					}
					recalc();
				}
				if (widthOpts.length) {
					finishWidthSelect();
					return;
				}
				frappe.call({
					method:
						'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_bundle_width_options',
					args: { shaft_production_run: frm.doc.name, job_id: jp.job_id },
					callback: function (r) {
						const apiWidths = (r.message || {}).widths || [];
						if (apiWidths.length) {
							jp.widths = apiWidths;
							widthOpts = spr_collect_bundle_width_options(jp, widthsByJob, frm, segs);
						}
						finishWidthSelect();
					},
					error: function () {
						finishWidthSelect();
					},
				});
			}
			function refreshWidthOptions() {
				const jp = spr_bundle_resolve_job(d.get_value('job_pick'), jobById, jobByLabel);
				const det = d.$wrapper.find('.spr-bundle-job-detail');
				if (!jp) {
					spr_bundle_set_width_options(d, []);
					return;
				}
				applySegsToDialog(jp, jp.segments || []);
				if (jp.segments && jp.segments.length) {
					return;
				}
				if (det.length && !jp.combination_text) {
					det.html('<span class="text-muted small">' + __('Loading segment detail...') + '</span>');
				}
				frappe.call({
					method:
						'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_job_segments',
					args: { shaft_production_run: frm.doc.name, job_id: jp.job_id },
					callback: function (r) {
						jp.segments = r.message || [];
						applySegsToDialog(jp, jp.segments);
					},
					error: function () {
						applySegsToDialog(jp, []);
					},
				});
			}
			function recalc() {
				const jp = spr_bundle_resolve_job(d.get_value('job_pick'), jobById, jobByLabel);
				const wsel = spr_bundle_get_width(d);
				const n = cint(d.get_value('no_of_packaging'));
				const whole = flt(d.get_value('whole_gross_kg'));
				const el = d.$wrapper.find('.spr-bundle-calc');
				if (!jp || !el.length) {
					return;
				}
				const single = n > 0 ? whole / n : 0;
				const tw = flt(wsel) * n;
				el.html(
					__('Single gross: {0} Kg - Sticker width (selected width x pkg): {1} Inches', [
						single.toFixed(2),
						tw.toFixed(2),
					])
				);
			}
			d.show();
			if (jobs.length > 0) {
				const firstLbl = spr_bundle_job_select_label(jobs[0]);
				d.set_df_property('job_pick', 'options', jobOpts);
				if (d.fields_dict.job_pick) {
					d.fields_dict.job_pick.refresh();
				}
				d.set_value('job_pick', firstLbl);
			}
			setTimeout(function () {
				refreshWidthOptions();
				recalc();
			}, 50);
			if (d.fields_dict.job_pick && d.fields_dict.job_pick.$input) {
				d.fields_dict.job_pick.$input.on('change', function () {
					const sel = spr_bundle_resolve_job(d.get_value('job_pick'), jobById, jobByLabel);
					if (sel) {
						sel.segments = [];
					}
					refreshWidthOptions();
					recalc();
				});
			}
			spr_bundle_width_select_el(d).on('change input', recalc);
			['no_of_packaging', 'whole_gross_kg'].forEach(function (fn) {
				const f = d.fields_dict[fn];
				if (f && f.$input) {
					f.$input.on('change input', recalc);
				}
			});
		},
	});
}

frappe.ui.form.on('Bundle Calculation', {
	pkts_per_bundle: function (frm, cdt, cdn) {
		sprRecalcBundlePlannedPcs(frm, cdt, cdn);
	},
	pcs_per_packet: function (frm, cdt, cdn) {
		sprRecalcBundlePlannedPcs(frm, cdt, cdn);
	},
	total_consumed_meter: function (frm) {
		if (sprIsSheetCutting(frm)) {
			sprSyncBundleProducedSheets(frm, { silent: true });
		}
	},
	create_bundle_entry: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!sprIsBundlePackagingMode(frm)) {
			frappe.msgprint(__('Bundle Create Entry is only for sheet-cutting / bag SPR'));
			return;
		}
		if (cint(frm.doc.docstatus) !== 0) {
			frappe.msgprint(__('Create Entry is only available on draft Shaft Production Run.'));
			return;
		}
		if (frm.is_new() || !frm.doc.name) {
			frappe.msgprint(__('Save the Shaft Production Run before creating roll lines.'));
			return;
		}
		const nBundles = cint(row.no_of_bundles);
		const nBoxes = cint(row.no_of_boxes);
		const nRows = sprIsBag(frm) && nBundles < 1 ? nBoxes : nBundles;
		if (nRows < 1) {
			frappe.msgprint(__('No of Bundles / No of Boxes must be at least 1'));
			return;
		}
		const args = { shaft_production_run: frm.doc.name };
		if (row.name) {
			args.bundle_row_name = row.name;
		} else {
			const rows = frm.doc.bundle_calculation || [];
			args.bundle_row_idx = rows.indexOf(row);
		}
		frappe.call({
			method:
				'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.build_spr_bundle_result_lines_for_row',
			args: args,
			freeze: true,
			freeze_message: __('Creating roll lines for this bundle...'),
			callback: function (r) {
				const lines = r.message || [];
				spr_run_programmatic_item_adds(frm, function () {
					lines.forEach(function (line) {
						const it = frm.add_child('items');
						Object.keys(line).forEach(function (k) {
							if (line[k] !== undefined && line[k] !== null) {
								it[k] = line[k];
							}
						});
						if (line.quality) {
							it.quality = line.quality;
						}
						if (line.color) {
							it.color = line.color;
						}
						if (line.gsm != null && line.gsm !== '') {
							it.gsm = cint(line.gsm);
						}
						if (line.custom_bag_size) {
							it.custom_bag_size = line.custom_bag_size;
						} else if (line.custom_sheet_size && sprIsBag(frm)) {
							it.custom_bag_size = line.custom_sheet_size;
						}
						if (line.custom_sheet_size && !sprIsBag(frm)) {
							it.custom_sheet_size = line.custom_sheet_size;
						}
						if (line.custom_planned_sheets_pcs != null) {
							it.custom_planned_sheets_pcs = flt(line.custom_planned_sheets_pcs);
						}
						if (line.custom_planned_bag_pcs != null) {
							it.custom_planned_bag_pcs = flt(line.custom_planned_bag_pcs);
						}
					});
				});
				sprToggleSheetCuttingRollUi(frm);
				const n = lines.length;
				const startIdx = n > 0 ? (frm.doc.items || []).length - n : 0;

				if (n > 0) {
					frappe.call({
						method:
							'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_next_spr_batch_numbers',
						args: {
							shaft_production_run: frm.doc.name,
							count: n,
							client_max_roll: spr_max_roll_before_idx(frm, startIdx),
							client_series_prefix: spr_existing_series_prefix_before_idx(frm, startIdx),
							run_date: frm.doc.run_date,
							custom_unit: frm.doc.custom_unit,
							shift: frm.doc.shift,
						},
						freeze: n > 3,
						freeze_message: n > 3 ? __('Assigning batch numbers...') : undefined,
						callback: function (br) {
							const batches = br.message || [];
							const all = frm.doc.items || [];
							for (let i = 0; i < n; i++) {
								const it = all[startIdx + i];
								if (!it) {
									continue;
								}
								if (batches[i]) {
									it.batch_no = batches[i].batch_no || batches[i];
									if (batches[i].roll_no != null) {
										it.roll_no = batches[i].roll_no;
										spr_bump_roll_suffix_cache(frm, batches[i].roll_no);
									}
								}
							}
							frm.refresh_field('items');
							spr_finish_create_entry(frm, {
								bundle: true,
								lineCount: n,
								startIdx: startIdx,
								alertMsg: __('Added {0} roll line(s) for bundle.', [lines.length]),
							});
						},
						error: function () {
							frm.refresh_field('items');
							spr_finish_create_entry(frm, {
								bundle: true,
								lineCount: n,
								startIdx: startIdx,
								alertMsg: __('Added {0} roll line(s) for bundle.', [lines.length]),
							});
						},
					});
				} else {
					spr_finish_create_entry(frm, { bundle: true, lineCount: 0 });
				}
			},
		});
	},
});

frappe.ui.form.on('Shaft Production Run Job', {
	create_roll_entry: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const job_id = row.job_id;
		if (cint(frm.doc.docstatus) !== 0) {
			frappe.msgprint(__('Create Entry is only available on draft Shaft Production Run.'));
			return;
		}
		if (!job_id) {
			frappe.msgprint(__('Job ID is required'));
			return;
		}
		if (frm.is_new() || !frm.doc.name) {
			frappe.msgprint(__('Save the Shaft Production Run before creating roll lines.'));
			return;
		}
		function invokeBuildRollLines(
			laminationRollsPerCombo,
			laminationExactRollLines,
			appendMode,
			exactRollLines,
			rollStartIndex,
			quotaMeta
		) {
			invokeAppendRollLinesViaServer(frm, job_id, {
				laminationRollsPerCombo,
				laminationExactRollLines,
				appendMode,
				exactRollLines,
				rollStartIndex,
				quotaMeta,
			});
		}

		if (sprUsesOneRollPerCreateEntry(frm, row)) {
			const maxRolls = sprJobMaxRollLines(row, frm);
			const curRolls = sprCountRollLinesForJob(frm, job_id);
			const rowsToAdd = sprCreateEntryRowsPerClick(row, frm);
			if (curRolls >= maxRolls) {
				frappe.msgprint(
					__(
						'Maximum {0} roll lines allowed for job {1} ({2} already created). Use Manual Job for additional production.',
						[maxRolls, job_id, curRolls]
					)
				);
				return;
			}
			if (curRolls + rowsToAdd > maxRolls) {
				frappe.msgprint(
					__(
						'Cannot add {0} roll line(s) — only {1} of {2} remaining for job {3}. Use Manual Job for additional production.',
						[rowsToAdd, maxRolls - curRolls, maxRolls, job_id]
					)
				);
				return;
			}
			sprSaveBeforeCreateEntry(frm)
				.then(function () {
					const curAfterSave = sprCountRollLinesForJob(frm, job_id);
					if (curAfterSave >= maxRolls) {
						frappe.msgprint(
							__(
								'Maximum {0} roll lines allowed for job {1} ({2} already created). Use Manual Job for additional production.',
								[maxRolls, job_id, curAfterSave]
							)
						);
						return;
					}
					const rowsNow = Math.min(rowsToAdd, maxRolls - curAfterSave);
					if (rowsNow < 1) {
						frappe.msgprint(
							__(
								'Maximum {0} roll lines allowed for job {1} ({2} already created). Use Manual Job for additional production.',
								[maxRolls, job_id, curAfterSave]
							)
						);
						return;
					}
					invokeBuildRollLines(0, 0, true, rowsNow, curAfterSave, {
						max: maxRolls,
						current: curAfterSave,
						addCount: rowsNow,
					});
				})
				.catch(function (err) {
					frappe.msgprint({
						title: __('Save failed'),
						indicator: 'red',
						message: err && err.message ? err.message : __('Could not save Shaft Production Run.'),
					});
				});
			return;
		}

		const rollPromptMeta = sprRollPromptMeta(frm, row);
		if (rollPromptMeta) {
			frappe.prompt(
				[
					{
						fieldname: 'roll_lines_to_add',
						fieldtype: 'Int',
						label: __('Roll lines to add'),
						reqd: 1,
						default: cint(rollPromptMeta.defaultLines) || 1,
						description: rollPromptMeta.description,
					},
				],
				function (values) {
					const n = cint(values.roll_lines_to_add);
					if (n < 1) {
						frappe.msgprint(__('Enter at least 1 roll line.'));
						return;
					}
				if (sprUsesLaminationRollPrompt(frm)) {
					invokeBuildRollLines(0, n, true, 0);
				} else if (sprUsesPrintingRollPrompt(frm)) {
					invokeBuildRollLines(0, 0, true, n);
				} else {
					// Slitting, Rewinding, Sheet Cutting, BOPP Film — exact roll lines
					invokeBuildRollLines(0, 0, true, n);
				}
				},
				rollPromptMeta.title,
				__('Add')
			);
			return;
		}
		invokeBuildRollLines(0, 0, false);
	},
});

let _sprLastPrintKey = "";
let _sprLastPrintAt = 0;

frappe.ui.form.on('Shaft Production Run Item', {
	custom_total_produced_sheets: function (frm) {
		sprSyncBundleProducedSheets(frm);
	},
	net_weight: function (frm, cdt, cdn) {
		if (spr_items_grid_is_editing(frm)) {
			frm._spr_job_achieved_pending = true;
			frm._spr_row_styles_pending = true;
			return;
		}
		const row = locals[cdt][cdn];
		const rounded = spr_round_net_weight_kg(row.net_weight);
		if (Math.abs(flt(row.net_weight) - rounded) > 1e-6) {
			frappe.model.set_value(cdt, cdn, 'net_weight', rounded);
			return;
		}
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
		update_shaft_job_achieved_from_items(frm);
		// Avoid hard grid refresh while typing; it can reset in-cell editor values.
		schedule_spr_item_row_styles(frm);
		sprScheduleTotalProducedSync(frm);
	},
	gross_weight: function (frm, cdt, cdn) {
		// Calculate net_weight instantly when gross_weight changes
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row) {
			return;
		}
		let width = flt(row.width_inch);
		let gw = spr_normalize_gross_weight_input(row.gross_weight);
		if (gw > 0 && Math.abs(flt(row.gross_weight) - gw) > 1e-6) {
			row.gross_weight = gw;
			if (!spr_items_grid_is_editing(frm)) {
				frappe.model.set_value(cdt, cdn, 'gross_weight', gw);
				return;
			}
		}
		if (gw <= 0) {
			spr_clear_roll_weight_dependents(frm, cdt, cdn);
			return;
		}
		
		if (width > 0 && gw > 0) {
			let width_in_meter = width * 0.0254;
			let gsm_val = flt(row.gsm) || flt(row.sticker_gsm) || 90;
			let raw_weight = (gsm_val * width_in_meter * gw) / 1000;
			const standard_widths = [63, 85, 90, 118, 126];
			let is_standard = standard_widths.some(w => Math.abs(width - w) < 0.01);
			
			let core_weight = 0;
			if (is_standard) {
				let base_weight_of_core = 1.3;
				if (raw_weight >= 50 && raw_weight <= 100) {
					base_weight_of_core = 1.8;
				} else if (raw_weight > 100) {
					base_weight_of_core = 2.5;
				}
				let numeric_core_width = parseFloat(row.custom_core_width_mm) || 1600;
				core_weight = (base_weight_of_core / 1600) * numeric_core_width;
			} else {
				let core_width, prorate;
				if (width < 63) { core_width = 63; prorate = 1.30; }
				else if (width < 85) { core_width = 85; prorate = 1.75; }
				else if (width < 90) { core_width = 90; prorate = 1.86; }
				else if (width < 118) { core_width = 118; prorate = 2.43; }
				else { core_width = 126; prorate = 2.60; }
				core_weight = (width / core_width) * prorate;
			}
			
			let calc_net = gw - core_weight;
			let net_val = calc_net > 0 ? calc_net : gw;
			net_val = spr_round_net_weight_kg(net_val);

			// Also calculate produced_gsm immediately
			let mr = sprResolveLengthMetersForProducedGsm(frm, row) || 0;
			let newGsm = 0;
			if (net_val > 0 && width > 0 && mr > 0) {
				newGsm = Math.round((net_val * 1000) / (width * mr * 0.0254) * 100) / 100;
			}

			if (spr_items_grid_is_editing(frm)) {
				row.net_weight = net_val;
				row.produced_gsm = newGsm;
			} else {
				frappe.model.set_value(cdt, cdn, 'net_weight', net_val);
				frappe.model.set_value(cdt, cdn, 'produced_gsm', newGsm);
				spr_update_produced_gsm_with_retry(frm, cdt, cdn);
			}
		}

		if (!spr_items_grid_is_editing(frm)) {
			update_shaft_job_achieved_from_items(frm);
			sprScheduleTotalProducedSync(frm);
		} else {
			update_shaft_job_achieved_from_items(frm);
			sprScheduleTotalProducedSync(frm, { silent: true });
			frm._spr_row_styles_pending = true;
			if (frm._spr_grid_totals_debounce) {
				clearTimeout(frm._spr_grid_totals_debounce);
			}
			frm._spr_grid_totals_debounce = setTimeout(function () {
				frm._spr_grid_totals_debounce = null;
				if (spr_items_grid_is_editing(frm)) {
					return;
				}
				update_shaft_job_achieved_from_items(frm, { force: true });
				sprScheduleTotalProducedSync(frm);
			}, 400);
		}
	},
	gsm: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		schedule_spr_item_row_styles(frm);
	},
	width_inch: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
	},
	meter_roll: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
		try {
			update_shaft_job_achieved_from_items(frm);
		} catch (e) {}
	},
	produced_length_mtrs: function (frm, cdt, cdn) {
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
		try {
			update_shaft_job_achieved_from_items(frm);
		} catch (e) {}
		schedule_spr_item_row_styles(frm);
	},
	meter_roll_mtrs: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
		try {
			update_shaft_job_achieved_from_items(frm);
		} catch (e) {}
	},
	ordered_length: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
	},
	ordered_length_mtrs: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
	},
	custom_ordered_length: function (frm, cdt, cdn) {
		spr_update_mix_roll_planned_qty(frm, cdt, cdn);
		spr_update_produced_gsm_with_retry(frm, cdt, cdn);
	},
	produced_gsm: function (frm) {
		if (spr_items_grid_is_editing(frm)) {
			frm._spr_row_styles_pending = true;
			return;
		}
		schedule_spr_item_row_styles(frm);
	},
	
	/**
	 * OVERRIDE: Ensure net_weight calculation runs ONLY based on gross_weight & core_weight
	 * NOT dependent on meter_roll (which may be empty when bundle packaging sets gross_weight)
	 * This handler runs AFTER any old conflicting scripts to guarantee correct behavior
	 */
	custom_net_weight_trigger: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		let width = flt(row.width_inch);
		let gw = spr_normalize_gross_weight_input(row.gross_weight);

		// Net weight calculation should ONLY depend on gross_weight & width, NOT meter_roll
		if (gw <= 0) {
			row.net_weight = 0;
			return;
		}
		if (width > 0 && gw > 0) {
			let width_in_meter = width * 0.0254;
			let gsm_val = flt(row.gsm) || flt(row.sticker_gsm) || 90;
			let raw_weight = (gsm_val * width_in_meter * gw) / 1000;
			const standard_widths = [63, 85, 90, 118, 126];
			let is_standard = standard_widths.some(w => Math.abs(width - w) < 0.01);
			
			let core_weight = 0;
			if (is_standard) {
				let base_weight_of_core = 1.3;
				if (raw_weight >= 50 && raw_weight <= 100) {
					base_weight_of_core = 1.8;
				} else if (raw_weight > 100) {
					base_weight_of_core = 2.5;
				}
				let numeric_core_width = parseFloat(row.custom_core_width_mm) || 1600;
				core_weight = (base_weight_of_core / 1600) * numeric_core_width;
			} else {
				let core_width, prorate;
				if (width < 63) { core_width = 63; prorate = 1.30; }
				else if (width < 85) { core_width = 85; prorate = 1.75; }
				else if (width < 90) { core_width = 90; prorate = 1.86; }
				else if (width < 118) { core_width = 118; prorate = 2.43; }
				else { core_width = 126; prorate = 2.60; }
				core_weight = (width / core_width) * prorate;
			}
			
			let calc_net = gw - core_weight;
			let net_val = calc_net > 0 ? calc_net : gw;
			row.net_weight = spr_round_net_weight_kg(net_val);
		}
	},
	
	/**
	 * FINAL: Calculate produced_gsm only when ALL three values are ready
	 * This runs last to ensure net_weight is already set
	 */
	final_produced_gsm_calc: function (frm, cdt, cdn) {
		if (spr_items_grid_is_editing(frm)) {
			return;
		}
		const row = locals[cdt][cdn];
		if (spr_normalize_gross_weight_input(row.gross_weight) <= 0) {
			frappe.model.set_value(cdt, cdn, 'produced_gsm', 0);
			return;
		}
		let nw = flt(row.net_weight) || 0;
		let wi = flt(row.width_inch) || 0;
		let mr = sprResolveLengthMeters(row) || 0;
		
		if (nw > 0 && wi > 0 && mr > 0) {
			let newGsm = Math.round((nw * 1000) / (wi * mr * 0.0254) * 100) / 100;
			frappe.model.set_value(cdt, cdn, 'produced_gsm', newGsm);
		} else {
			frappe.model.set_value(cdt, cdn, 'produced_gsm', 0);
		}
	},
	/**
	 * Save this roll line, lock it for editing, and reveal Print Label.
	 * Until Save Row: Print Label stays hidden (see spr_apply_items_row_lock_ui).
	 */
	save_row: function (frm, cdt, cdn) {
		if (frm.is_new()) {
			frappe.msgprint(__('Save the Shaft Production Run first.'));
			return;
		}
		if (frm.doc.docstatus && frm.doc.docstatus !== 0) {
			frappe.show_alert({ message: __('Submitted document cannot be edited from Save Row.'), indicator: 'orange' });
			return;
		}
		cdn = spr_resolve_items_row_cdn(frm, cdt, cdn);
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row) {
			frappe.msgprint(__('Could not find roll line to save.'));
			return;
		}
		if (cint(row.row_locked)) {
			frappe.show_alert({ message: __('This row is already locked. Click Edit Row to change.'), indicator: 'blue' });
			return;
		}
		
		const gw = spr_normalize_gross_weight_input(row.gross_weight);
		if (gw > 0) {
			row.gross_weight = gw;
			const gwHandler =
				frappe.ui.form.handlers &&
				frappe.ui.form.handlers['Shaft Production Run Item'] &&
				frappe.ui.form.handlers['Shaft Production Run Item'].gross_weight;
			if (typeof gwHandler === 'function') {
				gwHandler(frm, cdt, cdn);
			}
		}
		update_shaft_job_achieved_from_items(frm, { force: true, skipGridRefresh: true });
		
		row.row_locked = 1;
		if (frappe.meta.get_docfield(cdt, 'row_ready_for_print')) {
			row.row_ready_for_print = 1;
		}
		frm.refresh_field('items');
		try { spr_schedule_item_row_styles_after_doc_write(frm); } catch(e) {}
		try { spr_apply_items_row_lock_ui(frm); } catch(e) {}
		try { apply_spr_item_row_styles(frm); } catch(e) {}
		try { spr_schedule_items_align_after_save_row(frm); } catch(e) {}

		frappe.call({
			method: 'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_set_item_row_lock',
			args: {
				spr_name: frm.doc.name,
				row_name: row.name,
				locked: 1,
				gross_weight: row.gross_weight,
				net_weight: row.net_weight,
				produced_gsm: row.produced_gsm,
			},
			freeze: false,
			callback: function (r) {
				if (r && r.exc) {
					row.row_locked = 0;
					if (frappe.meta.get_docfield(cdt, 'row_ready_for_print')) {
						row.row_ready_for_print = 0;
					}
					try { spr_apply_items_row_lock_ui(frm); } catch(e) {}
					frappe.msgprint(__('Could not save row. Please try again.'));
					return;
				}
				if (r.message && r.message.modified) {
					if (r.message.modified > frm.doc.modified) {
						frm.doc.modified = r.message.modified;
					}
				}
				frappe.show_alert({ message: __('Row saved.'), indicator: 'green' }, 2);
				// Persist wastage/core + clear Not Saved after every Save Row
				spr_auto_save_draft_if_dirty(frm, { delay: 500, force: true, quiet: true });
			},
		});
	},
	/** Print roll label (after Save Row) — custom sticker HTML flow. */
	print_sticker: function (frm, cdt, cdn) {
		cdn = spr_resolve_items_row_cdn(frm, cdt, cdn);
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row || !cint(row.row_ready_for_print) || !cint(row.row_locked)) {
			frappe.msgprint(__('Save Row first to lock the line and enable the label.'));
			return;
		}
		const printKey = (frm.doc.name || '') + '::' + (row.name || cdn);
		const now = Date.now();
		if (_sprLastPrintKey === printKey && now - _sprLastPrintAt < 800) {
			return;
		}
		_sprLastPrintKey = printKey;
		_sprLastPrintAt = now;
		if (typeof frappe.generate_sticker_flow === 'function') {
			frappe.generate_sticker_flow(row.name, frm);
			return;
		}
		if (
			production_entry.spr_label &&
			typeof production_entry.spr_label.print_roll === 'function'
		) {
			production_entry.spr_label.print_roll(frm.doc.name, row.name);
			return;
		}
		if (
			production_entry.spr_roll_label_print &&
			typeof production_entry.spr_roll_label_print.open === 'function'
		) {
			production_entry.spr_roll_label_print.open(frm.doc.name, row.name);
			return;
		}
		frappe.msgprint(__('Production Label ready to print'));
	},
	/** QC / approval label (after Save Row). */
	custom_qc_approval_label: function (frm, cdt, cdn) {
		cdn = spr_resolve_items_row_cdn(frm, cdt, cdn);
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row || !cint(row.row_ready_for_print) || !cint(row.row_locked)) {
			frappe.msgprint(__('Save Row first to lock the line and enable the QC label.'));
			return;
		}
		if (typeof frappe.generate_approval_label === 'function') {
			frappe.generate_approval_label(row.name, frm);
			return;
		}
		if (
			production_entry.spr_label &&
			typeof production_entry.spr_label.print_qc === 'function'
		) {
			production_entry.spr_label.print_qc(frm.doc.name, row.name);
			return;
		}
		frappe.msgprint(__('QC label print helper not loaded.'));
	},
	custom_approval_label: function (frm, cdt, cdn) {
		cdn = spr_resolve_items_row_cdn(frm, cdt, cdn);
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row || !cint(row.row_ready_for_print) || !cint(row.row_locked)) {
			frappe.msgprint(__('Save Row first to lock the line and enable the QC label.'));
			return;
		}
		if (typeof frappe.generate_approval_label === 'function') {
			frappe.generate_approval_label(row.name, frm);
			return;
		}
		if (
			production_entry.spr_label &&
			typeof production_entry.spr_label.print_qc === 'function'
		) {
			production_entry.spr_label.print_qc(frm.doc.name, row.name);
			return;
		}
		frappe.msgprint(__('QC label print helper not loaded.'));
	},
	/** Unlock this row for editing; hide Print Label until Save Row again. */
	edit_row: function (frm, cdt, cdn) {
		if (frm.is_new() || (frm.doc.docstatus && frm.doc.docstatus !== 0)) {
			return;
		}
		cdn = spr_resolve_items_row_cdn(frm, cdt, cdn);
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row) {
			frappe.msgprint(__('Could not find roll line to edit.'));
			return;
		}
		
		row.row_locked = 0;
		if (frappe.meta.get_docfield(cdt, 'row_ready_for_print')) {
			row.row_ready_for_print = 0;
		}
		frm.refresh_field('items');
		try { spr_schedule_item_row_styles_after_doc_write(frm); } catch(e) {}
		try { spr_apply_items_row_lock_ui(frm); } catch(e) {}
		try { apply_spr_item_row_styles(frm); } catch(e) {}
		try { spr_schedule_items_align_after_save_row(frm); } catch(e) {}

		frappe.call({
			method: 'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_set_item_row_lock',
			args: {
				spr_name: frm.doc.name,
				row_name: row.name,
				locked: 0,
			},
			freeze: false,
			callback: function (r) {
				if (r && r.exc) {
					row.row_locked = 1;
					if (frappe.meta.get_docfield(cdt, 'row_ready_for_print')) {
						row.row_ready_for_print = 1;
					}
					try { spr_apply_items_row_lock_ui(frm); } catch(e) {}
					frappe.msgprint(__('Could not unlock row. Please try again.'));
					return;
				}
				if (r.message && r.message.modified) {
					if (r.message.modified > frm.doc.modified) {
						frm.doc.modified = r.message.modified;
					}
				}
				frappe.show_alert({ message: __('Row state updated.'), indicator: 'green' }, 2);
			},
		});
	},
});

function sprResolveWidthInchForGsm(frm, row) {
	const wi = flt(row && row.width_inch);
	if (wi > 0 && wi < 500) {
		return wi;
	}
	return 0;
}

function spr_update_produced_gsm(frm, cdt, cdn) {
	if (!frappe.meta.get_docfield('Shaft Production Run Item', 'produced_gsm')) {
		return;
	}
	if (spr_items_grid_is_editing(frm)) {
		return;
	}
	const row = locals[cdt][cdn];

	// Keep sticker GSM aligned to item-code rule: positions 9..11.
	const stickerGsm = sprStickerGsmFromItemCode(row.item_code);
	if (stickerGsm > 0) {
		if (flt(row.gsm) !== stickerGsm) {
			frappe.model.set_value(cdt, cdn, 'gsm', stickerGsm);
		}
		if (frappe.meta.get_docfield('Shaft Production Run Item', 'custom_sticker_gsm') && flt(row.custom_sticker_gsm) !== stickerGsm) {
			frappe.model.set_value(cdt, cdn, 'custom_sticker_gsm', stickerGsm);
		}
	}

	// Keep lamination GSM aligned to item-code suffix map (e.g. "-C" => 15).
	if (frappe.meta.get_docfield('Shaft Production Run Item', 'custom_lam_gsm')) {
		const lamGsm = sprLaminationGsmFromItemCode(row.item_code);
		if (lamGsm > 0 && flt(row.custom_lam_gsm) !== lamGsm) {
			frappe.model.set_value(cdt, cdn, 'custom_lam_gsm', lamGsm);
		}
	}
	
	// Get weight: prefer net_weight, fallback to gross_weight
	let nw = flt(row.net_weight);
	if (spr_normalize_gross_weight_input(row.gross_weight) <= 0) {
		nw = 0;
	} else if (nw <= 0) {
		nw = flt(row.gross_weight);
	}
	
	const wi = sprResolveWidthInchForGsm(frm, row);

	const isMix = frm && frm.doc && cint(frm.doc.is_mix_roll);
	const mr = sprResolveLengthMetersForProducedGsm(frm, row);

	if (isMix && mr <= 0) {
		frappe.model.set_value(cdt, cdn, 'produced_gsm', 0);
		apply_spr_item_row_styles(frm);
		schedule_spr_item_row_styles(frm);
		return;
	}

	// Calculate GSM only if all required values are present
	// Formula: (net_weight * 1000) / (width_inch * length_mtrs * 0.0254)
	let pgsm = 0;
	if (nw > 0 && wi > 0 && mr > 0) {
		pgsm = Math.round((nw * 1000) / (wi * mr * 0.0254) * 100) / 100;
	}

	frappe.model.set_value(cdt, cdn, 'produced_gsm', pgsm);
	// Avoid full grid refresh on every keypress (causes lag and cursor jumps).
	apply_spr_item_row_styles(frm);
	schedule_spr_item_row_styles(frm);
}

function spr_update_produced_gsm_with_retry(frm, cdt, cdn) {
	[0, 80, 220].forEach(function (ms) {
		setTimeout(function () {
			try {
				spr_update_produced_gsm(frm, cdt, cdn);
			} catch (e) {
				// Ignore transient timing issues while rows are being populated.
			}
		}, ms);
	});
}

function sprResolveLengthMeters(doc) {
	const aliases = [
		'produced_length_mtrs',
		'meter_roll',
		'meter_roll_mtrs',
		'custom_meter_roll_mtrs',
		'ordered_length',
		'ordered_length_mtrs',
		'custom_ordered_length',
		'roll_mtrs',
		'roll',
	];
	for (let i = 0; i < aliases.length; i++) {
		const v = flt(doc[aliases[i]]);
		if (v > 0) {
			return v;
		}
	}
	return 0;
}

function fetch_and_show_pp_wo_summary(frm) {
	if (!frm.doc.production_plan) {
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_production_plan_wo_summary',
		args: { production_plan: frm.doc.production_plan },
		callback: function (r) {
			show_pp_work_order_summary_dialog(r.message || []);
		},
		error: function () {
			show_pp_work_order_summary_dialog([]);
		},
	});
}

function show_pp_work_order_summary_dialog(rows) {
	const esc =
		frappe.utils && frappe.utils.escape_html
			? frappe.utils.escape_html
			: function (t) {
					return $('<div>').text(t || '').html();
				};
	let html;
	if (rows && rows.length) {
		let body =
			'<thead><tr><th>' +
			__('Work Order') +
			'</th><th>' +
			__('Status') +
			'</th><th>' +
			__('Order Qty') +
			'</th><th>' +
			__('Pending Qty') +
			'</th></tr></thead><tbody>';
		rows.forEach(function (r) {
			body +=
				'<tr><td>' +
				esc(r.work_order || '') +
				'</td><td>' +
				esc(r.status || '') +
				'</td><td>' +
				flt(r.order_qty, 3) +
				'</td><td>' +
				flt(r.pending_qty, 3) +
				'</td></tr>';
		});
		body += '</tbody>';
		html =
			'<div class="table-responsive"><table class="table table-bordered table-condensed">' +
			body +
			'</table></div>';
	} else {
		html =
			'<p class="text-muted">' +
			__('No Work Orders linked to this Production Plan.') +
			'</p>';
	}
	// Next tick so dialog is not blocked by freeze/refresh from the previous call chain
	setTimeout(function () {
		try {
			const d = new frappe.ui.Dialog({
				title: __('Production Plan — Work Orders'),
				fields: [{ fieldtype: 'HTML', fieldname: 'wo_table', options: html }],
				primary_action_label: __('OK'),
				primary_action: function () {
					d.hide();
				},
			});
			d.show();
		} catch (e) {
			frappe.msgprint({
				title: __('Production Plan — Work Orders'),
				message: html,
				indicator: 'blue',
				wide: true,
			});
		}
	}, 200);
}

function remove_spr_items_for_job(frm, job_id) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) {
		return;
	}
	const names = (frm.doc.items || [])
		.filter(function (d) {
			return String(d.job) === String(job_id);
		})
		.map(function (d) {
			return d.name;
		});
	names.forEach(function (name) {
		const gr = grid.grid_rows_by_docname && grid.grid_rows_by_docname[name];
		if (gr && gr.remove) {
			gr.remove();
			return;
		}
		const idx = (frm.doc.items || []).findIndex(function (d) {
			return d.name === name;
		});
		if (idx !== -1 && grid.grid_rows && grid.grid_rows[idx]) {
			grid.grid_rows[idx].remove();
		}
	});
}

function sprNormalizeJobKey(v) {
	if (v === undefined || v === null) {
		return '';
	}
	const s = String(v).trim();
	return s;
}

function sprShaftJobRowKey(sj) {
	if (!sj) {
		return '';
	}
	const id = sj.job_id;
	if (id !== undefined && id !== null && String(id).trim() !== '') {
		return sprNormalizeJobKey(id);
	}
	return sprNormalizeJobKey(sj.job_no);
}

function sprUsesLaminationRollPrompt(frm) {
	return frm && frm.doc && cint(frm.doc.custom_is_lamination);
}

function sprUsesSlittingRollPrompt(frm) {
	return frm && frm.doc && cint(frm.doc.custom_is_slitting);
}

function sprUsesRewindingRollPrompt(frm) {
	return frm && frm.doc && cint(frm.doc.custom_is_rewinding);
}

function sprUsesSheetCuttingRollPrompt(frm) {
	return sprIsSheetCutting(frm);
}

const SPR_PROCESS_FLAG_FIELDS = [
	'custom_is_lamination',
	'custom_is_rewinding',
	'custom_is_sheet_cutting',
	'custom_is_box_bag',
	'custom_is_bopp_film',
	'custom_is_printing',
	'custom_is_slitting',
];

function sprResetProcessFlags(frm) {
	if (!frm) {
		return;
	}
	SPR_PROCESS_FLAG_FIELDS.forEach(function (fieldname) {
		if (frappe.meta.get_docfield('Shaft Production Run', fieldname)) {
			frm.set_value(fieldname, 0);
		}
	});
}

function sprApplyProcessFlagsFromPp(frm, payload) {
	if (!frm || !payload) {
		return;
	}
	SPR_PROCESS_FLAG_FIELDS.forEach(function (fieldname) {
		if (fieldname in payload && frappe.meta.get_docfield('Shaft Production Run', fieldname)) {
			frm.set_value(fieldname, cint(payload[fieldname]));
		}
	});
}

function sprIsBag(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	return cint(frm.doc.custom_is_box_bag);
}

function sprIsBundlePackagingMode(frm) {
	return sprIsSheetCutting(frm) || sprIsBag(frm);
}

function sprIsSheetCutting(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	if (cint(frm.doc.custom_is_sheet_cutting)) {
		return true;
	}
	const u = String(frm.doc.custom_unit || '').trim();
	return u === 'JVE - SHEET CUTTING MACHINE';
}

function sprLoadShaftJobsFromPp(frm) {
	if (!frm || !frm.doc || !frm.doc.production_plan) {
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_job_rows_for_production_plan',
		args: { production_plan: frm.doc.production_plan },
		freeze: true,
		freeze_message: __('Loading shaft jobs from Production Plan...'),
		callback: function (r) {
			frm.clear_table('shaft_jobs');
			(r.message || []).forEach(function (row) {
				const c = frm.add_child('shaft_jobs');
				Object.keys(row).forEach(function (k) {
					if (row[k] !== undefined && row[k] !== null) {
						c[k] = row[k];
					}
				});
			});
			frm.refresh_field('shaft_jobs');
			frm.clear_table('items');
			frm.refresh_field('items');
			setTimeout(function () {
				spr_apply_shaft_jobs_grid_columns(frm, true);
				spr_stabilize_spr_child_grids(frm, { delay: 150 });
				spr_after_child_table_refresh(frm);
			}, 80);
			fetch_and_show_pp_wo_summary(frm);
		},
		error: function () {
			frm.clear_table('items');
			frm.refresh_field('items');
			setTimeout(function () {
				spr_apply_shaft_jobs_grid_columns(frm, true);
				spr_stabilize_spr_child_grids(frm, { delay: 150 });
				spr_after_child_table_refresh(frm);
			}, 80);
			fetch_and_show_pp_wo_summary(frm);
		},
	});
}

function sprLoadBundleCalculationFromPp(frm, presetRows) {
	if (!frm || !frm.doc || !frm.doc.production_plan) {
		return;
	}
	frm.clear_table('shaft_jobs');
	frm.refresh_field('shaft_jobs');
	function applyRows(rows) {
		frm.clear_table('bundle_calculation');
		(rows || []).forEach(function (row) {
			const c = frm.add_child('bundle_calculation');
			Object.keys(row).forEach(function (k) {
				if (row[k] !== undefined && row[k] !== null) {
					c[k] = row[k];
				}
			});
			if (sprIsBag(frm)) {
				const bz = row.bag_size || row.sheet_cutting_size || '';
				if (bz) {
					c.bag_size = bz;
					c.sheet_cutting_size = bz;
				}
			}
		});
		frm.refresh_field('bundle_calculation');
		sprToggleBundleCalculationGrid(frm);
		sprSyncBundleProducedSheets(frm, { silent: true });
		frm.clear_table('items');
		frm.refresh_field('items');
		spr_after_child_table_refresh(frm);
		fetch_and_show_pp_wo_summary(frm);
	}
	if (presetRows && presetRows.length) {
		applyRows(presetRows);
		return;
	}
	frappe.call({
		method:
			'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_bundle_calculation_rows_for_production_plan',
		args: {
			production_plan: frm.doc.production_plan,
			order_code: frm.doc.custom_order_code || '',
			order_meter: 0,
		},
		freeze: true,
		freeze_message: __('Loading bundle calculation from Production Plan...'),
		callback: function (r) {
			applyRows(r.message || []);
		},
		error: function () {
			applyRows([]);
		},
	});
}

function sprToggleSheetCuttingUi(frm) {
	if (!frm) {
		return;
	}
	const isBundleMode = sprIsBundlePackagingMode(frm);
	const isBag = sprIsBag(frm);
	try {
		frm.set_df_property('section_break_9', 'hidden', isBundleMode ? 1 : 0);
		frm.set_df_property('shaft_jobs', 'hidden', isBundleMode ? 1 : 0);
	} catch (e) {
		/* ignore */
	}
	try {
		if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_planned_qty')) {
			frm.set_df_property('custom_total_planned_qty', 'hidden', isBag ? 1 : 0);
		}
		if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_planned_pcs')) {
			frm.set_df_property('custom_total_planned_pcs', 'hidden', isBag ? 0 : 1);
		}
		if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_achieved_pcs')) {
			frm.set_df_property('custom_total_achieved_pcs', 'hidden', isBag ? 0 : 1);
		}
		if (frappe.meta.get_docfield('Shaft Production Run', 'total_produced_weight')) {
			frm.set_df_property(
				'total_produced_weight',
				'label',
				isBag ? __('Total Produced Bag PCS') : __('Total Produced Weight (KG)')
			);
			frm.set_df_property('total_produced_weight', 'precision', isBag ? 0 : 2);
		}
		[
			'custom_core_details',
			'custom_polybag_details',
		].forEach(function (fn) {
			if (frappe.meta.get_docfield('Shaft Production Run', fn)) {
				frm.set_df_property(fn, 'hidden', isBag ? 1 : 0);
			}
		});
	} catch (e) {
		/* ignore */
	}
	sprToggleSheetCuttingRollUi(frm);
}

function sprEnsureBundleRowsFromPp(frm) {
	if (!frm || !frm.doc || !frm.doc.production_plan || !sprIsBag(frm)) {
		return;
	}
	if ((frm.doc.bundle_calculation || []).length) {
		return;
	}
	sprLoadBundleCalculationFromPp(frm, null);
}

function spr_force_sheet_cutting_item_grid_columns(grid) {
	if (!grid) {
		return;
	}
	const show = [
		'quality',
		'color',
		'gsm',
		'custom_sheet_size',
		'custom_planned_sheets_pcs',
		'custom_total_produced_sheets',
	];
	show.forEach(function (fn) {
		try {
			if (typeof grid.update_docfield_property === 'function') {
				grid.update_docfield_property(fn, 'hidden', 0);
				grid.update_docfield_property(fn, 'in_list_view', 1);
			}
		} catch (e) {
			/* ignore */
		}
	});
	spr_set_grid_col_hidden(grid, 'custom_bag_size', 1);
	spr_set_grid_col_hidden(grid, 'custom_planned_bag_pcs', 1);
	spr_set_grid_col_hidden(grid, 'custom_achieved_bag_pcs', 1);
	if (typeof grid.setup_visible_columns === 'function') {
		grid.setup_visible_columns();
	}
	if (typeof grid.refresh_header === 'function') {
		grid.refresh_header();
	}
}

function spr_bundle_bag_size_col() {
	return frappe.meta.get_docfield('Bundle Calculation', 'bag_size')
		? 'bag_size'
		: 'sheet_cutting_size';
}

function spr_get_bundle_list_view_config(frm) {
	const showCreateEntry = frm && frm.doc && cint(frm.doc.docstatus) === 0;
	if (sprIsBag(frm)) {
		const bagCol = spr_bundle_bag_size_col();
		const altBagCol = bagCol === 'bag_size' ? 'sheet_cutting_size' : 'bag_size';
		const show = [
			'item_code',
			bagCol,
			'no_of_boxes',
			'pcs_per_packet',
			'total_pcs_per_bundle',
			'total_produced_bag_pcs',
			'work_order',
			'order_code',
		];
		if (showCreateEntry) {
			show.push('create_bundle_entry');
		}
		return {
			show: show,
			hide: [
				altBagCol,
				'no_of_bundles',
				'pkts_per_bundle',
				'job',
				'total_produced_sheets',
				'total_achieved_weight',
				'total_consumed_meter',
			],
		};
	}
	if (sprIsSheetCutting(frm)) {
		const show = [
			'item_code',
			'sheet_cutting_size',
			'no_of_bundles',
			'pkts_per_bundle',
			'pcs_per_packet',
			'total_pcs_per_bundle',
			'work_order',
			'order_code',
			'job',
			'total_consumed_meter',
			'total_produced_sheets',
			'total_achieved_weight',
		];
		if (showCreateEntry) {
			show.push('create_bundle_entry');
		}
		return {
			show: show,
			hide: ['bag_size', 'no_of_boxes', 'total_produced_bag_pcs'],
		};
	}
	return { show: [], hide: [] };
}

function spr_apply_bundle_calculation_grid_columns(frm, force) {
	if (!frm || !sprIsBundlePackagingMode(frm)) {
		return;
	}
	const grid = frm.fields_dict.bundle_calculation && frm.fields_dict.bundle_calculation.grid;
	if (!grid) {
		return;
	}
	spr_bind_spr_grid_column_configure_hook(frm, 'bundle_calculation');
	if (grid._spr_columns_user_locked) {
		spr_light_realign_field(frm, 'bundle_calculation');
		spr_apply_create_entry_buttons_ui(frm);
		spr_ensure_child_grid_heights(frm);
		return;
	}
	const cfg = spr_get_bundle_list_view_config(frm);
	const show = cfg.show || [];
	if (force) {
		spr_reset_bundle_calc_grid_field_visibility(frm);
	}
	if (show.length) {
		spr_apply_grid_visible_columns(frm, 'bundle_calculation', show, !!force);
	}
	spr_apply_create_entry_buttons_ui(frm);
	spr_ensure_child_grid_heights(frm);
}

function sprToggleBundleCalculationGrid(frm) {
	if (!frm || !frm.fields_dict || !frm.fields_dict.bundle_calculation) {
		return;
	}
	const grid = frm.fields_dict.bundle_calculation.grid;
	if (!grid) {
		return;
	}
	const isBag = sprIsBag(frm);
	if (isBag && typeof grid.update_docfield_property === 'function') {
		grid.update_docfield_property('bag_size', 'label', __('Bag Size'));
		grid.update_docfield_property('sheet_cutting_size', 'label', __('Sheet Cutting Size'));
		grid.update_docfield_property('pcs_per_packet', 'label', __('Pcs per Box'));
		grid.update_docfield_property('total_pcs_per_bundle', 'label', __('Total Planned Pcs'));
	}
	spr_apply_bundle_calculation_grid_columns(frm, true);
	spr_sync_grid_header_body_scroll(frm.fields_dict.bundle_calculation);
	spr_ensure_child_grid_heights(frm);
}

function sprToggleSheetCuttingRollUi(frm) {
	const isBag = sprIsBag(frm);
	const fd = frm && frm.fields_dict ? frm.fields_dict.items : null;
	const grid = fd && fd.grid;
	if (grid) {
		if (isBag && typeof grid.update_docfield_property === 'function') {
			grid.update_docfield_property('custom_sheet_size', 'label', __('Sheet Size'));
			grid.update_docfield_property('custom_bag_size', 'label', __('Bag Size'));
		}
		if (sprIsSheetCutting(frm) && typeof grid.update_docfield_property === 'function') {
			grid.update_docfield_property('custom_sheet_size', 'label', __('Sheet Size'));
		}
		spr_show_all_grid_columns(frm, 'items');
		if (fd) {
			spr_sync_grid_header_body_scroll(fd);
		}
	}
	sprToggleBundleCalculationGrid(frm);
}

function sprUsesBoppFilmRollPrompt(frm) {
	return frm && frm.doc && cint(frm.doc.custom_is_bopp_film);
}

function sprUsesPrintingRollPrompt(frm) {
	return frm && frm.doc && cint(frm.doc.custom_is_printing);
}

function sprRollPromptMeta(frm, row) {
	const fromPp = cint((row && row.no_of_rolls) || 0);
	const noOfRollsCreated = cint((frm && frm.doc && frm.doc.custom_no_of_rolls_created) || 0);
	const defaultLines = noOfRollsCreated > 0 ? noOfRollsCreated : (fromPp > 0 ? fromPp : 1);
	const unitText = String((frm && frm.doc && (frm.doc.custom_unit || frm.doc.unit || frm.doc.workstation || "")) || "").toLowerCase();
	const isPrintingJob = unitText.includes("printing") || unitText.includes("105");
	if (frm && frm.doc && cint(frm.doc.is_mix_roll)) {
		const comb = String((row && row.combination) || '');
		const segCount = comb ? comb.split('+').map((s) => s.trim()).filter(Boolean).length : 0;
		const shafts = cint((row && row.no_of_shafts) || 0) || segCount || 1;
		return {
			title: __('Mix Roll — add roll lines'),
			description: __('Adds roll lines from manual items / combination widths (no Work Order).'),
			defaultLines: Math.max(shafts, segCount, 1),
		};
	}
	if (sprUsesSlittingRollPrompt(frm)) {
		return {
			title: __('Slitting — add roll lines'),
			description: __('Defaults to No. of Rolls from Production Plan for this job.'),
			defaultLines: defaultLines,
		};
	}
	if (sprUsesLaminationRollPrompt(frm)) {
		return {
			title: __('Lamination — add roll lines'),
			description: __('Adds exactly this many new roll lines for the selected job.'),
			defaultLines: defaultLines,
		};
	}
	if (sprUsesRewindingRollPrompt(frm)) {
		return {
			title: __('Rewinding — add roll lines'),
			description: __('Adds exactly this many new roll lines for the selected rewinding job.'),
			defaultLines: defaultLines,
		};
	}
	if (sprUsesSheetCuttingRollPrompt(frm)) {
		return {
			title: __('Sheet Cutting — add roll lines'),
			description: __('Adds exactly this many new roll lines for the selected sheet cutting job.'),
			defaultLines: defaultLines,
		};
	}
	if (sprUsesBoppFilmRollPrompt(frm)) {
		return {
			title: isPrintingJob ? __('Printing — add roll lines') : __('BOPP Film — add roll lines'),
			description: isPrintingJob ? __('Adds exactly this many new roll lines for the selected printing job.') : __('Adds exactly this many new roll lines for the selected BOPP printing job.'),
			defaultLines: defaultLines,
		};
	}
	if (sprUsesPrintingRollPrompt(frm)) {
		return {
			title: __('Printing — add roll lines'),
			description: __('Adds exactly this many new roll lines for the selected printing job.'),
			defaultLines: defaultLines,
		};
	}
	return null;
}

/** Legacy hook — duplicate Produced GSM hiding is integrated in spr_apply_items_grid_columns. */
function spr_hide_duplicate_produced_gsm_columns(frm) {
	spr_apply_items_grid_columns(frm);
}

function spr_has_other_process_flags(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	return !!(
		cint(frm.doc.custom_is_lamination)
		|| cint(frm.doc.custom_is_slitting)
		|| cint(frm.doc.custom_is_rewinding)
		|| cint(frm.doc.custom_is_sheet_cutting)
		|| cint(frm.doc.custom_is_box_bag)
		|| cint(frm.doc.custom_is_bopp_film)
		|| cint(frm.doc.custom_is_printing)
	);
}

function spr_is_fabric_unit_spr(frm) {
	if (!frm || !frm.doc || spr_has_other_process_flags(frm) || sprIsBundlePackagingMode(frm)) {
		return false;
	}
	const u = String(frm.doc.custom_unit || '').trim().toUpperCase().replace(/\s+/g, ' ');
	return /^(UNIT\s*[1-4]|MIXED|UNASSIGNED)$/.test(u);
}

function spr_has_fabric100_in_shaft_jobs(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	return (frm.doc.shaft_jobs || []).some(function (sj) {
		const mi = String((sj && sj.manual_items) || '').trim();
		if (!mi) {
			return false;
		}
		let codes = [];
		if (mi.charAt(0) === '[') {
			try {
				const parsed = JSON.parse(mi);
				if (Array.isArray(parsed)) {
					codes = parsed;
				}
			} catch (e) {
				/* ignore */
			}
		}
		if (!codes.length) {
			codes = mi.split(/[,;]/).map(function (s) {
				return s.trim();
			}).filter(Boolean);
		}
		return codes.some(function (c) {
			return spr_item_process_prefix(c) === '100';
		});
	});
}

function sprHasFabric100Rows(frm) {
	return (frm && frm.doc && (frm.doc.items || [])).some(function (row) {
		const ic = String((row && row.item_code) || '').trim().toUpperCase();
		return ic.startsWith('100') || /^[A-Z0-9]+-100/.test(ic) || spr_item_process_prefix(row.item_code) === '100';
	});
}

function sprIsFabric100Run(frm) {
	if (!frm || !frm.doc) {
		return false;
	}
	if (cint(frm.doc.is_mix_roll)) {
		return true;
	}
	if (spr_is_fabric_unit_spr(frm)) {
		return true;
	}
	if (sprRollProcessPrefix(frm) === '100' || sprHasFabric100Rows(frm)) {
		return true;
	}
	return spr_has_fabric100_in_shaft_jobs(frm);
}

function spr_get_items_list_view_config(frm) {
	const mode = spr_items_process_mode(frm);
	if (mode === 'bag') {
		return {
			show: [
				'work_order',
				'item_code',
				'batch_no',
				'party_code',
				'custom_bag_size',
				'custom_planned_bag_pcs',
				'custom_achieved_bag_pcs',
				'save_row',
			].filter(function (fn) {
				return frappe.meta.get_docfield(SPR_SPI_DOCTYPE, fn);
			}),
			hide: [],
		};
	}
	if (mode === 'sheetcutting') {
		return {
			show: [
				'batch_no',
				'party_code',
				'item_code',
				'custom_sheet_size',
				'quality',
				'color',
				'gsm',
				'produced_gsm',
				'custom_planned_sheets_pcs',
				'custom_total_produced_sheets',
				'net_weight',
				'gross_weight',
				'save_row',
				'work_order',
			],
			hide: [],
		};
	}
	if (mode === 'fabric100') {
		return { show: spr_build_fabric100_roll_items_show_list(frm), hide: [] };
	}
	if (mode === 'lamination') {
		return { show: spr_build_roll_items_show_list(frm, { gsmTrio: true }), hide: [] };
	}
	if (mode === 'slitting108109') {
		return { show: spr_build_roll_items_show_list(frm, { gsmTrio: true }), hide: [] };
	}
	if (mode === 'slitting103') {
		return { show: spr_build_roll_items_show_list(frm, { gsmTrio: false }), hide: [] };
	}
	return { show: spr_build_roll_items_show_list(frm, { gsmTrio: false }), hide: [] };
}

function spr_apply_fabric100_item_grid_columns(frm) {
	spr_apply_items_grid_columns(frm);
}

function sprToggleLaminationRollUi(frm) {
	const processPrefix = sprRollProcessPrefix(frm);
	const isLaminationProcess = processPrefix === '104' || processPrefix === '107';
	const showLamCols = isLaminationProcess || sprUsesLaminationRollPrompt(frm);
	spr_apply_items_grid_columns(frm);
	const fd = frm && frm.fields_dict ? frm.fields_dict.items : null;
	const $legend = fd && fd.$wrapper ? fd.$wrapper.prev('.spr-gsm-legend') : null;
	if ($legend && $legend.length) {
		$legend.toggle(showLamCols);
	}
}

function update_shaft_job_achieved_from_items(frm, opts) {
	if (frm && frm.doc && cint(frm.doc.docstatus) !== 0) {
		return;
	}
	const settings = opts || {};
	if (spr_items_grid_is_editing(frm) && settings.force !== true) {
		settings.deferRefresh = true;
	}
	const hasW = frappe.meta.get_docfield('Shaft Production Run Job', 'custom_total_achieved_weight');
	const hasM = frappe.meta.get_docfield('Shaft Production Run Job', 'custom_total_achieved_meter');
	const hasHdrM = frappe.meta.get_docfield('Shaft Production Run', 'custom_total_achieved_meter');
	if (!hasW && !hasM && !hasHdrM) {
		return;
	}
	const weightByJob = {};
	const meterByJob = {};
	let meterTotal = 0;
	(frm.doc.items || []).forEach(function (it) {
		const pm = sprSumProducedLengthMeters(it);
		meterTotal += pm;
		const k = sprNormalizeJobKey(it.job);
		if (!k) {
			return;
		}
		if (hasW) {
			weightByJob[k] = (weightByJob[k] || 0) + spr_round_net_weight_kg(it.net_weight);
		}
		if (hasM) {
			meterByJob[k] = (meterByJob[k] || 0) + pm;
		}
	});
	let jobGridDirty = false;
	if (hasW) {
		(frm.doc.shaft_jobs || []).forEach(function (sj) {
			const jid = sprShaftJobRowKey(sj);
			const next = spr_round_net_weight_kg(jid && weightByJob[jid] !== undefined ? weightByJob[jid] : 0);
			const cur = flt(sj.custom_total_achieved_weight);
			if (Math.abs(cur - next) > 0.005) {
				if (settings.deferRefresh) {
					sj.custom_total_achieved_weight = next;
				} else {
					frappe.model.set_value(sj.doctype, sj.name, 'custom_total_achieved_weight', next);
				}
				jobGridDirty = true;
			}
		});
	}
	if (hasM) {
		(frm.doc.shaft_jobs || []).forEach(function (sj) {
			const jid = sprShaftJobRowKey(sj);
			const next = flt(jid && meterByJob[jid] !== undefined ? meterByJob[jid] : 0, 2);
			const cur = flt(sj.custom_total_achieved_meter);
			if (Math.abs(cur - next) > 0.005) {
				if (settings.deferRefresh) {
					sj.custom_total_achieved_meter = next;
				} else {
					frappe.model.set_value(sj.doctype, sj.name, 'custom_total_achieved_meter', next);
				}
				jobGridDirty = true;
			}
		});
	}
	if (settings.deferRefresh) {
		if (jobGridDirty) {
			frm._spr_job_achieved_pending = true;
		}
		if (hasHdrM) {
			const curH = flt(frm.doc.custom_total_achieved_meter);
			const nextH = flt(meterTotal, 2);
			if (Math.abs(curH - nextH) > 0.005) {
				frm.doc.custom_total_achieved_meter = nextH;
			}
		}
		return;
	}
	if (jobGridDirty) {
		try {
			if (!settings.skipGridRefresh) {
				frm.refresh_field('shaft_jobs');
			}
		} catch (e) {
			/* ignore */
		}
		if (!settings.skipGridRefresh && !spr_should_use_lightweight_grid_pass(frm)) {
			setTimeout(function () {
				spr_apply_shaft_jobs_grid_columns(frm, true);
			}, 60);
		}
	}
	if (hasHdrM) {
		const curH = flt(frm.doc.custom_total_achieved_meter);
		const nextH = flt(meterTotal, 2);
		if (Math.abs(curH - nextH) > 0.005) {
			frm.set_value('custom_total_achieved_meter', nextH);
		}
	}
}

function spr_patch_child_grid_refresh(frm, fieldname) {
	if (!frm || !fieldname) {
		return;
	}
	const patchKey = '_spr_grid_patched_' + fieldname;
	if (frm[patchKey]) {
		return;
	}
	const fd = frm.fields_dict && frm.fields_dict[fieldname];
	if (!fd || !fd.grid) {
		return;
	}
	const grid = fd.grid;
	let hooked = false;
	let _afterGridPaintRunning = false;
	function afterGridPaint() {
		if (_afterGridPaintRunning) {
			return;
		}
		const g = fd && fd.grid;
		if (!g || g._spr_skip_paint_hook) {
			return;
		}
		// Submitted SPR is read-only — any per-paint column tweak causes header zig-zag.
		if (spr_is_submitted_spr(frm)) {
			return;
		}
		_afterGridPaintRunning = true;
		try {
			if (!g) {
				return;
			}
			if (fieldname === 'items' && spr_should_block_grid_realign(frm)) {
				return;
			}
			if (fieldname === 'items' || fieldname === 'shaft_jobs' || fieldname === 'bundle_calculation') {
				var order = g._spr_desired_column_order;
				var needsRepair =
					fieldname === 'items' || fieldname === 'shaft_jobs'
						? spr_child_grid_needs_repair(frm, fieldname)
						: false;
				if (needsRepair) {
					spr_debounced_repair_child_grid_alignment(frm, fieldname);
				} else if (order && order.length && !g._spr_columns_user_locked) {
					var showSet = {};
					order.forEach(function (fn) { showSet[fn] = 1; });
					spr_light_sync_grid_columns(g, order, showSet, { refreshRows: false });
				} else {
					spr_mirror_grid_docfields_to_rows(g);
				}
				if (fieldname === 'items') {
					spr_bind_items_grid_edit_guard(frm);
				}
				if (fieldname === 'shaft_jobs') {
					spr_install_shaft_jobs_grid_column_guard(frm);
				}
			}
			if (fieldname === 'items' && !spr_should_block_grid_realign(frm)) {
				spr_schedule_grid_ui_debounced(frm, { delay: 220, columns: false });
				setTimeout(function () {
					if (!frm || !frm.fields_dict || !frm.fields_dict.items || spr_items_grid_is_editing(frm)) {
						return;
					}
					apply_spr_item_row_styles(frm);
				}, 80);
			}
		} finally {
			_afterGridPaintRunning = false;
		}
	}
	function wrap(method) {
		const orig = grid[method];
		if (typeof orig !== 'function') {
			return;
		}
		const bound = orig.bind(grid);
		grid[method] = function () {
			const ret = bound.apply(grid, arguments);
			afterGridPaint();
			return ret;
		};
		hooked = true;
	}
	wrap('refresh');
	wrap('render');
	// Do not wrap refresh_header — it re-enters afterGridPaint and causes header zig-zag.
	if (fieldname === 'items' && grid.wrapper && grid.wrapper.length && !frm._spr_items_grid_click_patched) {
		frm._spr_items_grid_click_patched = true;
		spr_install_items_grid_row_add_block(frm);
		spr_install_items_row_action_cdn_capture(frm);
		grid.wrapper.on('change input blur', 'input, textarea, select', function () {
			if (frm._spr_grid_input_debounce) {
				clearTimeout(frm._spr_grid_input_debounce);
			}
			frm._spr_grid_input_debounce = setTimeout(function () {
				frm._spr_grid_input_debounce = null;
				spr_schedule_grid_ui_debounced(frm, { delay: 280, columns: false });
			}, 200);
		});
	}
	if (fieldname === 'items') {
		spr_install_items_grid_row_add_block(frm);
		spr_install_items_row_action_cdn_capture(frm);
	}
	if (hooked) {
		frm[patchKey] = true;
	}
}

function spr_patch_items_grid_refresh(frm) {
	SPR_SPR_CHILD_TABLE_FIELDS.forEach(function (fn) {
		spr_patch_child_grid_refresh(frm, fn);
	});
	spr_install_items_grid_row_add_block(frm);
}

/** Legend for |Sticker GSM vs Produced GSM| bands (above Roll Production Results grid). */
function spr_inject_gsm_legend(frm) {
	const fd = frm.fields_dict && frm.fields_dict.items;
	if (!fd || !fd.$wrapper || !fd.$wrapper.length) {
		return;
	}
	if (fd.$wrapper.prev('.spr-gsm-legend').length) {
		return;
	}
	const html =
		'<div class="spr-gsm-legend alert alert-secondary" style="margin-bottom:8px;font-size:12px;line-height:1.5;">' +
		'<strong>' +
		__('Roll lines — GSM difference (Sticker GSM vs Produced GSM)') +
		'</strong><br>' +
		'<span style="display:inline-block;background:#bbf7d0;padding:2px 8px;margin:2px 4px 2px 0;border-radius:2px;">|diff| &lt; 1</span> ' +
		'<span style="display:inline-block;background:#eab308;padding:2px 8px;margin:2px 4px 2px 0;border-radius:2px;">1 - 2</span> ' +
		'<span style="display:inline-block;background:#fb923c;padding:2px 8px;margin:2px 4px 2px 0;border-radius:2px;">2 - 3</span> ' +
		'<span style="display:inline-block;background:#fecaca;padding:2px 8px;margin:2px 4px 2px 0;border-radius:2px;">&ge; 3</span> ' +
		'<span style="display:inline-block;background:#f3f4f6;padding:2px 8px;margin:2px 4px 2px 0;border-radius:2px;">' +
		__('Awaiting produced GSM / incomplete') +
		'</span>' +
		'</div>';
	fd.$wrapper.before(html);
	sprToggleLaminationRollUi(frm);
}

/** Sticker / planned GSM: field or parsed from item_code (same rule as server parse_item_code). */
function sprStickerGsmFromDoc(doc) {
	const fromCode = sprStickerGsmFromItemCode(doc && doc.item_code);
	if (fromCode > 0) {
		return fromCode;
	}
	const g = flt(doc && doc.gsm);
	return g > 0 ? g : 0;
}

/** True when produced length is missing or zero — do not infer GSM from ordered meter_roll (legend: incomplete / grey). */
function sprRollProducedLengthIncomplete(doc) {
	if (!frappe.meta.get_docfield('Shaft Production Run Item', 'produced_length_mtrs')) {
		return false;
	}
	const pl = doc.produced_length_mtrs;
	if (pl === undefined) {
		return false;
	}
	if (pl === null || pl === '') {
		return sprResolveLengthMeters(doc) <= 0;
	}
	// If produced_length_mtrs is set but <= 0, still allow fallback
	if (flt(pl) <= 0) {
		return sprResolveLengthMeters(doc) <= 0;
	}
	return false;
}

/** Same formula as spr_update_produced_gsm — use when produced_gsm not yet written (avoids all-white rows). */
function sprEffectiveProducedGsm(doc, frm) {
	let p = flt(doc.produced_gsm);
	if (p > 0) {
		return p;
	}

	// Get weight: prefer net_weight, fallback to gross_weight
	let nw = flt(doc.net_weight);
	if (nw <= 0) {
		nw = flt(doc.gross_weight);
	}

	const wi = flt(doc.width_inch);
	const wiOk = wi > 0 && wi < 500 ? wi : 0;

	const mr = sprResolveLengthMetersForProducedGsm(frm, doc);
	if (frm && frm.doc && cint(frm.doc.is_mix_roll) && mr <= 0) {
		return 0;
	}

	if (nw > 0 && wiOk > 0 && mr > 0) {
		return Math.round((nw * 1000) / (wiOk * mr * 0.0254) * 100) / 100;
	}
	return 0;
}

function ensure_spr_item_stylesheet() {
	const sprLockCssVer = '3';
	if (window.__sprspr_lock_style_ver !== sprLockCssVer) {
		window.__sprspr_lock_style_ver = sprLockCssVer;
		$('head style[data-spr-row-lock]').remove();
		const lockCss = `
		.form-group[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]),
		.frappe-control[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]),
		.fieldname-items .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) {
			pointer-events: none;
			opacity: 0.94;
		}
		.form-group[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) input,
		.form-group[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) textarea,
		.form-group[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) select,
		.frappe-control[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) input,
		.frappe-control[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) textarea,
		.frappe-control[data-fieldname="items"] .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) select,
		.fieldname-items .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) input,
		.fieldname-items .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) textarea,
		.fieldname-items .grid-row.spr-spr-row-locked .col:not([data-fieldname="print_sticker"]):not([data-fieldname="custom_production_label"]):not([data-fieldname="custom_approval_label"]):not([data-fieldname="custom_qc_approval_label"]):not([data-fieldname="edit_row"]):not([data-fieldname="save_row"]) select {
			pointer-events: none !important;
			background-color: transparent !important;
		}
		.form-group[data-fieldname="items"] .grid-row.spr-spr-row-locked .col[data-fieldname="save_row"] button,
		.frappe-control[data-fieldname="items"] .grid-row.spr-spr-row-locked .col[data-fieldname="save_row"] button,
		.fieldname-items .grid-row.spr-spr-row-locked .col[data-fieldname="save_row"] button {
			display: none !important;
		}
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-locked) .col[data-fieldname="edit_row"] button,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-locked) .col[data-fieldname="edit_row"] button,
		.fieldname-items .grid-row:not(.spr-spr-row-locked) .col[data-fieldname="edit_row"] button {
			display: none !important;
		}
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] button,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] .btn,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] a,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] button,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] .btn,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] a,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] button,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] .btn,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] a,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] button,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] .btn,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] a,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] button,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] .btn,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="print_sticker"] a,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] button,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] .btn,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_production_label"] a,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] button,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] .btn,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] a,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] button,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] .btn,
		.form-group[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] a,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] button,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] .btn,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] a,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] button,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] .btn,
		.frappe-control[data-fieldname="items"] .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] a,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] button,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] .btn,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_approval_label"] a,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] button,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] .btn,
		.fieldname-items .grid-row:not(.spr-spr-row-label-ready) .col[data-fieldname="custom_qc_approval_label"] a {
			opacity: 0 !important;
			visibility: hidden !important;
			pointer-events: none !important;
		}
	`;
		$('head').append(`<style data-spr-row-lock="${sprLockCssVer}">${lockCss}</style>`);
	}
	const sprItemsCssVer = '61';
	if (window.__sprspr_items_css_ver === sprItemsCssVer) {
		return;
	}
	window.__sprspr_items_css_ver = sprItemsCssVer;
	window.__sprspr_style = true;
	$('head style[data-spr-items]').remove();
	/* |Sticker GSM (gsm) vs Produced GSM (produced_gsm)|: <1 green, 1-2 yellow, 2-3 orange, 3+ red */
	const css = `
		.spr-items-wrap .spr-gsm-band-0 { background-color: #bbf7d0 !important; }
		.spr-items-wrap .spr-gsm-band-1 { background-color: #eab308 !important; }
		.spr-items-wrap .spr-gsm-band-2 { background-color: #fb923c !important; }
		.spr-items-wrap .spr-gsm-band-3 { background-color: #fecaca !important; }
		.spr-items-wrap .spr-gsm-pending { background-color: #f3f4f6 !important; }
		.spr-items-wrap .spr-gsm-band-0 .grid-form-row, .spr-items-wrap .spr-gsm-band-0 .form-in-grid,
		.spr-items-wrap .spr-gsm-band-0 .form-section, .spr-items-wrap .spr-gsm-band-0 .frappe-control { background-color: #bbf7d0 !important; }
		.spr-items-wrap .spr-gsm-band-1 .grid-form-row, .spr-items-wrap .spr-gsm-band-1 .form-in-grid,
		.spr-items-wrap .spr-gsm-band-1 .form-section, .spr-items-wrap .spr-gsm-band-1 .frappe-control { background-color: #eab308 !important; }
		.spr-items-wrap .spr-gsm-band-2 .grid-form-row, .spr-items-wrap .spr-gsm-band-2 .form-in-grid,
		.spr-items-wrap .spr-gsm-band-2 .form-section, .spr-items-wrap .spr-gsm-band-2 .frappe-control { background-color: #fb923c !important; }
		.spr-items-wrap .spr-gsm-band-3 .grid-form-row, .spr-items-wrap .spr-gsm-band-3 .form-in-grid,
		.spr-items-wrap .spr-gsm-band-3 .form-section, .spr-items-wrap .spr-gsm-band-3 .frappe-control { background-color: #fecaca !important; }
		.spr-items-wrap .spr-gsm-pending .grid-form-row, .spr-items-wrap .spr-gsm-pending .form-in-grid,
		.spr-items-wrap .spr-gsm-pending .form-section, .spr-items-wrap .spr-gsm-pending .frappe-control { background-color: #f3f4f6 !important; }
		.spr-items-wrap .spr-gsm-band-0 + .grid-form-row { background-color: #bbf7d0 !important; }
		.spr-items-wrap .spr-gsm-band-1 + .grid-form-row { background-color: #eab308 !important; }
		.spr-items-wrap .spr-gsm-band-2 + .grid-form-row { background-color: #fb923c !important; }
		.spr-items-wrap .spr-gsm-band-3 + .grid-form-row { background-color: #fecaca !important; }
		.spr-items-wrap .spr-gsm-pending + .grid-form-row { background-color: #f3f4f6 !important; }
		.spr-items-wrap .grid-row.spr-gsm-band-0, .spr-items-wrap .dt-row.spr-gsm-band-0,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-band-0, .form-group[data-fieldname="items"] .dt-row.spr-gsm-band-0,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-band-0, .frappe-control[data-fieldname="items"] .dt-row.spr-gsm-band-0,
		.fieldname-items .grid-row.spr-gsm-band-0, .fieldname-items .dt-row.spr-gsm-band-0 { background-color: #bbf7d0 !important; }
		.spr-items-wrap .grid-row.spr-gsm-band-1, .spr-items-wrap .dt-row.spr-gsm-band-1,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-band-1, .form-group[data-fieldname="items"] .dt-row.spr-gsm-band-1,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-band-1, .frappe-control[data-fieldname="items"] .dt-row.spr-gsm-band-1,
		.fieldname-items .grid-row.spr-gsm-band-1, .fieldname-items .dt-row.spr-gsm-band-1 { background-color: #eab308 !important; }
		.spr-items-wrap .grid-row.spr-gsm-band-2, .spr-items-wrap .dt-row.spr-gsm-band-2,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-band-2, .form-group[data-fieldname="items"] .dt-row.spr-gsm-band-2,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-band-2, .frappe-control[data-fieldname="items"] .dt-row.spr-gsm-band-2,
		.fieldname-items .grid-row.spr-gsm-band-2, .fieldname-items .dt-row.spr-gsm-band-2 { background-color: #fb923c !important; }
		.spr-items-wrap .grid-row.spr-gsm-band-3, .spr-items-wrap .dt-row.spr-gsm-band-3,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-band-3, .form-group[data-fieldname="items"] .dt-row.spr-gsm-band-3,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-band-3, .frappe-control[data-fieldname="items"] .dt-row.spr-gsm-band-3,
		.fieldname-items .grid-row.spr-gsm-band-3, .fieldname-items .dt-row.spr-gsm-band-3 { background-color: #fecaca !important; }
		.spr-items-wrap .grid-row.spr-gsm-pending, .spr-items-wrap .dt-row.spr-gsm-pending,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-pending, .form-group[data-fieldname="items"] .dt-row.spr-gsm-pending,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-pending, .frappe-control[data-fieldname="items"] .dt-row.spr-gsm-pending,
		.fieldname-items .grid-row.spr-gsm-pending, .fieldname-items .dt-row.spr-gsm-pending { background-color: #f3f4f6 !important; }
		.spr-items-wrap .grid-row[class*="spr-gsm-band"] .static-value,
		.spr-items-wrap .dt-row[class*="spr-gsm-band"] .static-value,
		.spr-items-wrap .grid-row[class*="spr-gsm-band"] .row-index,
		.spr-items-wrap .dt-row[class*="spr-gsm-band"] .row-index,
		.spr-items-wrap .grid-row[class*="spr-gsm-band"] .col,
		.spr-items-wrap .dt-row[class*="spr-gsm-band"] .col,
		.spr-items-wrap .grid-row[class*="spr-gsm-band"] input,
		.spr-items-wrap .dt-row[class*="spr-gsm-band"] input,
		.spr-items-wrap .grid-row[class*="spr-gsm-band"] select,
		.spr-items-wrap .dt-row[class*="spr-gsm-band"] select,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .static-value,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .row-index,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .col,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] input,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] select,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] textarea,
		.form-group[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] a,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .static-value,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .row-index,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] .col,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] input,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] select,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] textarea,
		.frappe-control[data-fieldname="items"] .grid-row[class*="spr-gsm-band"] a,
		.fieldname-items .grid-row[class*="spr-gsm-band"] .static-value,
		.fieldname-items .grid-row[class*="spr-gsm-band"] .row-index,
		.fieldname-items .grid-row[class*="spr-gsm-band"] input,
		.fieldname-items .grid-row[class*="spr-gsm-band"] select {
			color: #111827 !important;
		}
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-pending .static-value,
		.form-group[data-fieldname="items"] .grid-row.spr-gsm-pending .row-index,
		.frappe-control[data-fieldname="items"] .grid-row.spr-gsm-pending .static-value,
		.fieldname-items .grid-row.spr-gsm-pending .static-value {
			color: #4b5563 !important;
		}
		.spr-items-wrap .dt-row.spr-gsm-band-0 .dt-cell, .spr-items-wrap .dt-row.spr-gsm-band-0 td { background-color: #bbf7d0 !important; }
		.spr-items-wrap .dt-row.spr-gsm-band-1 .dt-cell, .spr-items-wrap .dt-row.spr-gsm-band-1 td { background-color: #eab308 !important; }
		.spr-items-wrap .dt-row.spr-gsm-band-2 .dt-cell, .spr-items-wrap .dt-row.spr-gsm-band-2 td { background-color: #fb923c !important; }
		.spr-items-wrap .dt-row.spr-gsm-band-3 .dt-cell, .spr-items-wrap .dt-row.spr-gsm-band-3 td { background-color: #fecaca !important; }
		.spr-items-wrap .dt-row.spr-gsm-pending .dt-cell, .spr-items-wrap .dt-row.spr-gsm-pending td { background-color: #f3f4f6 !important; }
		/* Selected / active row (editing) — Frappe defaults to white; keep GSM band visible */
		.spr-items-wrap .dt-row.selected.spr-gsm-band-0, .spr-items-wrap .dt-row.active.spr-gsm-band-0,
		.form-group[data-fieldname="items"] .dt-row.selected.spr-gsm-band-0,
		.frappe-control[data-fieldname="items"] .dt-row.selected.spr-gsm-band-0,
		.fieldname-items .dt-row.selected.spr-gsm-band-0,
		.spr-items-wrap .grid-row.selected.spr-gsm-band-0, .spr-items-wrap .grid-row.grid-row-open.spr-gsm-band-0 { background-color: #bbf7d0 !important; }
		.spr-items-wrap .dt-row.selected.spr-gsm-band-1, .spr-items-wrap .dt-row.active.spr-gsm-band-1,
		.form-group[data-fieldname="items"] .dt-row.selected.spr-gsm-band-1,
		.frappe-control[data-fieldname="items"] .dt-row.selected.spr-gsm-band-1,
		.fieldname-items .dt-row.selected.spr-gsm-band-1,
		.spr-items-wrap .grid-row.selected.spr-gsm-band-1, .spr-items-wrap .grid-row.grid-row-open.spr-gsm-band-1 { background-color: #eab308 !important; }
		.spr-items-wrap .dt-row.selected.spr-gsm-band-2, .spr-items-wrap .dt-row.active.spr-gsm-band-2,
		.form-group[data-fieldname="items"] .dt-row.selected.spr-gsm-band-2,
		.frappe-control[data-fieldname="items"] .dt-row.selected.spr-gsm-band-2,
		.fieldname-items .dt-row.selected.spr-gsm-band-2,
		.spr-items-wrap .grid-row.selected.spr-gsm-band-2, .spr-items-wrap .grid-row.grid-row-open.spr-gsm-band-2 { background-color: #fb923c !important; }
		.spr-items-wrap .dt-row.selected.spr-gsm-band-3, .spr-items-wrap .dt-row.active.spr-gsm-band-3,
		.form-group[data-fieldname="items"] .dt-row.selected.spr-gsm-band-3,
		.frappe-control[data-fieldname="items"] .dt-row.selected.spr-gsm-band-3,
		.fieldname-items .dt-row.selected.spr-gsm-band-3,
		.spr-items-wrap .grid-row.selected.spr-gsm-band-3, .spr-items-wrap .grid-row.grid-row-open.spr-gsm-band-3 { background-color: #fecaca !important; }
		.spr-items-wrap .dt-row.selected.spr-gsm-pending, .spr-items-wrap .grid-row.selected.spr-gsm-pending { background-color: #f3f4f6 !important; }
		/* Submitted / read-only child table: rows are often plain tbody tr */
		.spr-items-wrap tbody tr.spr-gsm-band-0 td, .spr-items-wrap tbody tr.spr-gsm-band-0 th { background-color: #bbf7d0 !important; }
		.spr-items-wrap tbody tr.spr-gsm-band-1 td, .spr-items-wrap tbody tr.spr-gsm-band-1 th { background-color: #eab308 !important; }
		.spr-items-wrap tbody tr.spr-gsm-band-2 td, .spr-items-wrap tbody tr.spr-gsm-band-2 th { background-color: #fb923c !important; }
		.spr-items-wrap tbody tr.spr-gsm-band-3 td, .spr-items-wrap tbody tr.spr-gsm-band-3 th { background-color: #fecaca !important; }
		.spr-items-wrap tbody tr.spr-gsm-pending td, .spr-items-wrap tbody tr.spr-gsm-pending th { background-color: #f3f4f6 !important; }
		.form-readonly .spr-items-wrap .dt-row.spr-gsm-band-0, .form-readonly .spr-items-wrap .grid-row.spr-gsm-band-0,
		.form-readonly .spr-items-wrap tbody tr.spr-gsm-band-0 td { background-color: #bbf7d0 !important; }
		.form-readonly .spr-items-wrap .dt-row.spr-gsm-band-1, .form-readonly .spr-items-wrap .grid-row.spr-gsm-band-1,
		.form-readonly .spr-items-wrap tbody tr.spr-gsm-band-1 td { background-color: #eab308 !important; }
		.form-readonly .spr-items-wrap .dt-row.spr-gsm-band-2, .form-readonly .spr-items-wrap .grid-row.spr-gsm-band-2,
		.form-readonly .spr-items-wrap tbody tr.spr-gsm-band-2 td { background-color: #fb923c !important; }
		.form-readonly .spr-items-wrap .dt-row.spr-gsm-band-3, .form-readonly .spr-items-wrap .grid-row.spr-gsm-band-3,
		.form-readonly .spr-items-wrap tbody tr.spr-gsm-band-3 td { background-color: #fecaca !important; }
		.form-readonly .spr-items-wrap .dt-row.spr-gsm-pending, .form-readonly .spr-items-wrap .grid-row.spr-gsm-pending,
		.form-readonly .spr-items-wrap tbody tr.spr-gsm-pending td { background-color: #f3f4f6 !important; }
		.spr-items-wrap.spr-doc-submitted .dt-row.spr-gsm-band-0, .spr-items-wrap.spr-doc-submitted .grid-row.spr-gsm-band-0,
		.spr-items-wrap.spr-doc-submitted tbody tr.spr-gsm-band-0 td { background-color: #bbf7d0 !important; }
		.spr-items-wrap.spr-doc-submitted .dt-row.spr-gsm-band-1, .spr-items-wrap.spr-doc-submitted .grid-row.spr-gsm-band-1,
		.spr-items-wrap.spr-doc-submitted tbody tr.spr-gsm-band-1 td { background-color: #eab308 !important; }
		.spr-items-wrap.spr-doc-submitted .dt-row.spr-gsm-band-2, .spr-items-wrap.spr-doc-submitted .grid-row.spr-gsm-band-2,
		.spr-items-wrap.spr-doc-submitted tbody tr.spr-gsm-band-2 td { background-color: #fb923c !important; }
		.spr-items-wrap.spr-doc-submitted .dt-row.spr-gsm-band-3, .spr-items-wrap.spr-doc-submitted .grid-row.spr-gsm-band-3,
		.spr-items-wrap.spr-doc-submitted tbody tr.spr-gsm-band-3 td { background-color: #fecaca !important; }
		.spr-items-wrap.spr-doc-submitted .dt-row.spr-gsm-pending, .spr-items-wrap.spr-doc-submitted .grid-row.spr-gsm-pending,
		.spr-items-wrap.spr-doc-submitted tbody tr.spr-gsm-pending td { background-color: #f3f4f6 !important; }
		/* Wide min-width forces horizontal scroll instead of squeezing columns after Save. */
		.spr-items-wrap .form-grid {
			min-width: 2600px;
		}
		.spr-shaft-jobs-wrap .form-grid {
			min-width: 1200px;
		}
		.spr-items-wrap.spr-doc-submitted .form-grid,
		.spr-shaft-jobs-wrap.spr-doc-submitted .form-grid {
			min-width: 2600px;
		}
		.spr-items-wrap .grid-row .col[data-fieldname="color"],
		.spr-items-wrap .grid-row [data-fieldname="color"],
		.spr-items-wrap .grid-heading-row [data-fieldname="color"],
		.spr-items-wrap .grid-heading-row .grid-static-col[data-fieldname="color"],
		.spr-shaft-jobs-wrap .grid-row .col[data-fieldname="color"],
		.spr-shaft-jobs-wrap .grid-row [data-fieldname="color"],
		.spr-shaft-jobs-wrap .grid-heading-row [data-fieldname="color"],
		.spr-shaft-jobs-wrap .grid-heading-row .grid-static-col[data-fieldname="color"] {
			min-width: 112px !important;
		}
		.spr-items-wrap .grid-row .col[data-fieldname="party_code"],
		.spr-items-wrap .grid-row [data-fieldname="party_code"],
		.spr-items-wrap .grid-heading-row [data-fieldname="party_code"],
		.spr-items-wrap .grid-heading-row .grid-static-col[data-fieldname="party_code"],
		.spr-shaft-jobs-wrap .grid-row .col[data-fieldname="party_code"],
		.spr-shaft-jobs-wrap .grid-row [data-fieldname="party_code"],
		.spr-shaft-jobs-wrap .grid-heading-row [data-fieldname="party_code"],
		.spr-shaft-jobs-wrap .grid-heading-row .grid-static-col[data-fieldname="party_code"] {
			min-width: 92px !important;
		}
		.spr-items-wrap .grid-row .col[data-fieldname="save_row"],
		.spr-items-wrap .grid-row [data-fieldname="save_row"],
		.spr-items-wrap .grid-heading-row [data-fieldname="save_row"],
		.spr-items-wrap .grid-heading-row .grid-static-col[data-fieldname="save_row"] {
			min-width: 88px !important;
		}
		.spr-items-wrap .grid-heading-row [data-fieldname="edit_row"],
		.spr-items-wrap .grid-heading-row .grid-static-col[data-fieldname="edit_row"],
		.spr-items-wrap .grid-row .col[data-fieldname="edit_row"],
		.spr-items-wrap .grid-row [data-fieldname="edit_row"] {
			min-width: 72px !important;
		}
		.spr-items-wrap .grid-row .col[data-fieldname="item_code"],
		.spr-items-wrap .grid-row .col[data-fieldname="item_name"],
		.spr-items-wrap .grid-row .col[data-fieldname="work_order"],
		.spr-items-wrap .grid-row .col[data-fieldname="batch_no"],
		.spr-items-wrap .grid-heading-row [data-fieldname="item_code"],
		.spr-items-wrap .grid-heading-row [data-fieldname="item_name"],
		.spr-items-wrap .grid-heading-row [data-fieldname="work_order"],
		.spr-items-wrap .grid-heading-row [data-fieldname="batch_no"] {
			min-width: 96px !important;
		}
		.spr-grid-wrap .form-grid-container,
		.spr-grid-wrap.spr-doc-submitted .form-grid-container,
		.spr-grid-wrap.spr-doc-submitted .form-grid,
		.spr-items-wrap.spr-doc-submitted .form-grid-container,
		.spr-shaft-jobs-wrap.spr-doc-submitted .form-grid-container,
		.spr-bundle-calc-wrap.spr-doc-submitted .form-grid-container {
			min-height: 200px !important;
		}
		.spr-grid-wrap.spr-doc-submitted .grid-body .rows,
		.spr-items-wrap.spr-doc-submitted .grid-body .rows,
		.spr-shaft-jobs-wrap.spr-doc-submitted .grid-body .rows {
			min-height: 88px !important;
			display: block !important;
		}
		.spr-items-wrap .form-grid-container,
		.spr-shaft-jobs-wrap .form-grid-container,
		.spr-bundle-calc-wrap .form-grid-container {
			overflow-x: auto;
			max-width: 100%;
			min-height: 120px;
		}
		.spr-shaft-jobs-wrap.spr-doc-submitted .form-grid-container,
		.spr-shaft-jobs-wrap.spr-doc-submitted .grid-body .rows {
			min-height: 88px !important;
		}
		.spr-shaft-jobs-wrap.spr-doc-submitted .grid-row {
			min-height: 42px !important;
		}
		.spr-grid-wrap .grid-body .rows,
		.spr-items-wrap .grid-body .rows,
		.spr-shaft-jobs-wrap .grid-body .rows,
		.spr-bundle-calc-wrap .grid-body .rows {
			min-height: 48px;
		}
		.spr-shaft-jobs-wrap .grid-heading-row .grid-settings,
		.spr-shaft-jobs-wrap .grid-heading-row .configure-columns,
		.spr-bundle-calc-wrap .grid-heading-row .grid-settings,
		.spr-bundle-calc-wrap .grid-heading-row .configure-columns,
		.spr-items-wrap .grid-heading-row .grid-settings,
		.spr-items-wrap .grid-heading-row .configure-columns {
			pointer-events: auto !important;
			position: relative;
			z-index: 6;
			min-width: 28px;
		}
		.spr-items-wrap .grid-heading-row .grid-static-col:last-child {
			min-width: 32px;
		}
		/* Header/body structural cols — match nested .data-row cells, not only direct children */
		.spr-items-wrap .grid-heading-row .row-check,
		.spr-shaft-jobs-wrap .grid-heading-row .row-check,
		.spr-items-wrap .grid-body .grid-row:not(.grid-form-row) .row-check,
		.spr-shaft-jobs-wrap .grid-body .grid-row:not(.grid-form-row) .row-check {
			flex: 0 0 36px !important;
			min-width: 36px !important;
			max-width: 36px !important;
			width: 36px !important;
		}
		.spr-items-wrap .grid-heading-row .row-index,
		.spr-shaft-jobs-wrap .grid-heading-row .row-index,
		.spr-items-wrap .grid-body .grid-row:not(.grid-form-row) .row-index,
		.spr-shaft-jobs-wrap .grid-body .grid-row:not(.grid-form-row) .row-index {
			flex: 0 0 60px !important;
			min-width: 60px !important;
			max-width: 60px !important;
			width: 60px !important;
		}
		.spr-items-wrap .grid-heading-row,
		.spr-shaft-jobs-wrap .grid-heading-row,
		.spr-items-wrap .grid-body .rows > .grid-row:not(.grid-form-row),
		.spr-shaft-jobs-wrap .grid-body .rows > .grid-row:not(.grid-form-row) {
			flex-wrap: nowrap;
		}
	`;
	$('head').append(`<style data-spr-items="${sprItemsCssVer}">${css}</style>`);
}

/** Apply row_locked / row_ready_for_print to grid DOM (Print Label only after Save Row). */
function spr_apply_items_row_lock_ui(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) {
		return;
	}
	const items = frm.doc.items || [];
	const $domRows = sprGetItemsDatatableBodyRows(frm);
	items.forEach(function (doc, idx) {
		let $wrap = sprFindItemsRowDomByDocname(frm, doc, idx);
		if (!$wrap || !$wrap.length) {
			if ($domRows && $domRows.length > idx && frm.page_length && idx < frm.page_length) {
				$wrap = $($domRows.get(idx));
			}
		}
		if (!$wrap || !$wrap.length) {
			$wrap = sprResolveItemsRowWrapper(frm, doc, grid, idx);
		}
		if (!$wrap || !$wrap.length) {
			return;
		}
		if (doc.name) {
			$wrap.attr('data-spr-row-name', doc.name);
			if (!$wrap.attr('data-docname')) {
				$wrap.attr('data-docname', doc.name);
			}
		}
		const locked = cint(doc.row_locked);
		const labelReady = cint(doc.row_ready_for_print) && locked;
		const $inner = $wrap.find('.grid-row').first();
		$wrap.addClass('spr-spr-row');
		$wrap.toggleClass('spr-spr-row-locked', !!locked);
		$wrap.toggleClass('spr-spr-row-label-ready', !!labelReady);
		if ($inner.length) {
			$inner.addClass('spr-spr-row');
			$inner.toggleClass('spr-spr-row-locked', !!locked);
			$inner.toggleClass('spr-spr-row-label-ready', !!labelReady);
		}
	});
}

const SPR_GSM_BG = ['#bbf7d0', '#eab308', '#fb923c', '#fecaca'];
/** Neutral row when GSM compare not yet possible (no blue). */
const SPR_GSM_PENDING_BG = '#f3f4f6';

function sprSetRowBgImportant($el, color) {
	if (!$el || !$el.length) {
		return;
	}
	const set = function (node) {
		if (node && node.style) {
			node.style.setProperty('background-color', color, 'important');
		}
	};
	$el.each(function () {
		set(this);
	});
	$el.find(
		'td, .col, .static-value, .editable-row, .row-index, .dt-cell, .form-in-grid, .form-section, .frappe-control, .control-input, .control-value'
	).each(function () {
		set(this);
	});
}

function sprApplyGsmRowVisual($row, bandOrPending) {
	if (!$row || !$row.length) {
		return;
	}
	if (bandOrPending === 'pending') {
		sprSetRowBgImportant($row, SPR_GSM_PENDING_BG);
		return;
	}
	const n = Number(bandOrPending);
	if (n >= 0 && n < SPR_GSM_BG.length) {
		sprSetRowBgImportant($row, SPR_GSM_BG[n]);
	}
}

function sprClearRowBg($row) {
	if (!$row || !$row.length) {
		return;
	}
	const clear = function (node) {
		if (node && node.style) {
			node.style.removeProperty('background-color');
		}
	};
	$row.each(function () {
		clear(this);
	});
	$row.find(
		'td, .col, .static-value, .editable-row, .row-index, .dt-cell, .form-in-grid, .form-section, .frappe-control, .control-input, .control-value'
	).each(function () {
		clear(this);
	});
}

function sprEnsureItemsGridObserver(frm) {
	if (!frm || !frm.fields_dict || !frm.fields_dict.items) return;
	const $wrap = frm.fields_dict.items.$wrapper;
	if (!$wrap || !$wrap.length) return;
	
	if (frm.__spr_items_grid_observer) return;
	const MutationObserver = window.MutationObserver || window.WebKitMutationObserver;
	if (!MutationObserver) return;
	
	const observer = new MutationObserver(function (mutations) {
		let shouldUpdate = false;
		for (let i = 0; i < mutations.length; i++) {
			if (mutations[i].type === 'childList' && mutations[i].addedNodes.length > 0) {
				shouldUpdate = true;
				break;
			}
		}
		if (shouldUpdate && !spr_items_grid_is_editing(frm)) {
			spr_schedule_grid_ui_debounced(frm, { delay: 150, columns: false });
		}
	});
	
	observer.observe($wrap.get(0), { childList: true, subtree: true, attributes: false });
	frm.__spr_items_grid_observer = observer;
}

function schedule_spr_item_row_styles(frm) {
	if (!frm.fields_dict.items || !frm.fields_dict.items.grid) {
		return;
	}
	if (spr_items_grid_is_editing(frm)) {
		frm._spr_row_styles_pending = true;
		return;
	}
	spr_apply_grid_wrap_classes(frm);
	ensure_spr_item_stylesheet();
	spr_schedule_grid_ui_debounced(frm, { delay: 120, columns: false });
}

/** Rebuild child grid columns so headers match row data (draft + submitted). */
function spr_force_child_grids_realign(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	if (spr_is_submitted_spr(frm) && frm._spr_submitted_grids_stable) {
		return;
	}
	if (spr_should_block_grid_realign(frm)) {
		return;
	}
	const cg = spr_get_grid_columns_module();
	if (!cg || typeof cg.apply !== 'function') {
		return;
	}
	const itemCols = (spr_get_items_list_view_config(frm) && spr_get_items_list_view_config(frm).show) || [];
	const jobCols = spr_build_shaft_jobs_show_list(frm) || [];
	if (!itemCols.length && !jobCols.length) {
		return;
	}
	spr_reset_items_grid_field_visibility(frm);
	spr_reset_shaft_jobs_grid_field_visibility(frm);
	if (itemCols.length && frm.fields_dict.items) {
		spr_install_items_grid_column_guard(frm);
		cg.apply(frm, 'items', SPR_SPI_DOCTYPE, itemCols, { fullRefresh: true });
	}
	if (jobCols.length && frm.fields_dict.shaft_jobs) {
		spr_install_shaft_jobs_grid_column_guard(frm);
		cg.apply(frm, 'shaft_jobs', 'Shaft Production Run Job', jobCols, { fullRefresh: true });
	}
	spr_apply_grid_wrap_classes(frm);
	spr_ensure_child_grid_heights(frm);
	spr_apply_spr_child_grid_min_widths(frm);
	['shaft_jobs', 'items'].forEach(function (fn) {
		spr_force_grid_realign(frm, fn);
		if (cg.sync_header_scroll) {
			cg.sync_header_scroll(frm, fn);
		} else {
			spr_light_grid_scroll_sync(frm, fn);
		}
	});
	spr_apply_spr_child_grid_min_widths(frm);
	['items', 'shaft_jobs'].forEach(function (fn) {
		spr_repair_child_grid_alignment(frm, fn);
	});
	spr_reapply_item_row_styles_with_retries(frm, [80, 280]);
}

/** Staggered realign after save / row save — fixes header collapse on draft SPR. */
function spr_schedule_child_grids_realign(frm, delays) {
	if (!frm) {
		return;
	}
	const times = delays || [0, 250, 600, 1200];
	const key = '_spr_child_realign_gen';
	const gen = (frm[key] || 0) + 1;
	frm[key] = gen;
	times.forEach(function (ms) {
		setTimeout(function () {
			if (!frm || frm[key] !== gen) {
				return;
			}
			spr_force_child_grids_realign(frm);
		}, ms);
	});
}

/** Submitted SPR: extra staggered passes after docstatus change. */
function spr_force_submitted_child_grids_realign(frm) {
	spr_schedule_child_grids_realign(frm, [0, 300, 700, 1400]);
}

/** @deprecated use spr_force_submitted_child_grids_realign */
function spr_force_submitted_items_grid_realign(frm) {
	spr_force_submitted_child_grids_realign(frm);
}

/** Show manufacture summary after submit — retries until HTML is ready (reload timing). */
function spr_show_submit_summary_with_retries(frm, delays) {
	if (!frm || !frm.doc || !frm.doc.name || cint(frm.doc.docstatus) !== 1) {
		return;
	}
	const times = delays || [600, 1500, 3000, 6000, 12000];
	times.forEach(function (ms) {
		setTimeout(function () {
			if (!frm || !frm.doc || !frm.doc.name || frm._spr_summary_shown) {
				return;
			}
			frappe.call({
				method:
					'production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_submit_summary',
				args: { shaft_production_run: frm.doc.name },
				freeze: false,
				callback: function (r) {
					const d = (r && r.message) || {};
					if (!d.html || frm._spr_summary_shown) {
						return;
					}
					frm._spr_summary_shown = true;
					frm._spr_pending_summary = false;
					spr_show_manufacture_summary_dialog(frm, d.html, d.title);
				},
			});
		}, ms);
	});
}

/** Re-apply GSM row colours after grid DOM rebuild (save / reopen / column sync). */
function spr_reapply_item_row_styles_with_retries(frm, delays) {
	if (!frm || !frm.fields_dict || !frm.fields_dict.items) {
		return;
	}
	if (spr_should_use_lightweight_grid_pass(frm)) {
		delays = [320];
	}
	ensure_spr_item_stylesheet();
	spr_apply_grid_wrap_classes(frm);
	const times = delays || [0, 500];
	times.forEach(function (ms) {
		setTimeout(function () {
			if (!frm || !frm.fields_dict || !frm.fields_dict.items) {
				return;
			}
			if (spr_items_grid_is_editing(frm)) {
				return;
			}
			apply_spr_item_row_styles(frm);
		}, ms);
	});
}

/** After save — GSM row colours + min-widths only (no full grid rebuild / blink). */
function spr_schedule_item_row_styles_after_doc_write(frm) {
	if (!frm || !frm.fields_dict) {
		return;
	}
	ensure_spr_item_stylesheet();
	const gc = spr_get_grid_columns_module();
	const run = function () {
		if (!frm || !frm.fields_dict) {
			return;
		}
		spr_apply_spr_child_grid_min_widths(frm);
		if (!spr_items_grid_is_editing(frm)) {
			apply_spr_item_row_styles(frm);
		}
	};
	if (gc && typeof gc.debounce === 'function') {
		gc.debounce(frm, 'spr_doc_write_realign', run, 180);
	} else {
		setTimeout(run, 180);
	}
}

function sprResolveItemsRowElement(frm, doc, grid, idx) {
	let $row = null;
	const byName = doc && doc.name && grid.grid_rows_by_docname && grid.grid_rows_by_docname[doc.name];
	if (byName) {
		if (byName.row && byName.row.length) {
			$row = byName.row;
		} else if (byName.wrapper && byName.wrapper.length) {
			$row = byName.wrapper;
		}
	}
	const $wrap = frm.fields_dict.items.$wrapper;
	if ((!$row || !$row.length) && $wrap && $wrap.length && doc && doc.name) {
		$row = $wrap.find('.grid-row[data-docname="' + doc.name + '"]');
		if (!$row.length) {
			$row = $wrap.find('.grid-row[data-name="' + doc.name + '"]');
		}
		if (!$row.length) {
			$row = $wrap.find('[data-name="' + doc.name + '"]').closest('.grid-row, .grid-form-row, tr');
		}
		if (!$row.length) {
			$row = $wrap
				.find('.form-in-grid [data-name="' + doc.name + '"]')
				.closest('.grid-row, tr, .dt-row');
		}
	}
	const $fb =
		$wrap && $wrap.length
			? $wrap.find(
					'.grid-body .grid-row, .form-grid .grid-row, .form-grid .rows .grid-row, .datatable .dt-row, tbody tr[data-idx]'
				)
			: $();
	if ((!$row || !$row.length) && $fb.length > idx) {
		$row = $($fb.get(idx));
	}
	// Frappe DataTable child grid: rows are often .dt-row only (no grid-row / docname on row)
	if ((!$row || !$row.length) && $wrap && $wrap.length) {
		const $dtOnly = $wrap.find('.datatable .dt-row:not(.dt-row-filter)');
		if ($dtOnly.length > idx) {
			$row = $($dtOnly.get(idx));
		}
	}
	return $row;
}

/**
 * All visible DataTable body rows in order. Prefer this over grid.grid_rows[idx] — Frappe often only
 * wires row 0 there while rows 1+ still exist in the DOM (causes only-first-row coloring).
 */
function sprGetItemsDatatableBodyRows(frm) {
	const $wrap = frm.fields_dict.items && frm.fields_dict.items.$wrapper;
	if (!$wrap || !$wrap.length) {
		return null;
	}
	const selectors = [
		'.datatable-body .dt-row',
		'.dt-scrollable .datatable-body .dt-row',
		'.dt-scrollable .dt-row',
		'.datatable .dt-row:not(.dt-row-filter)',
		'.dt-row:not(.dt-row-filter)',
	];
	let $rows = $();
	for (let i = 0; i < selectors.length; i++) {
		const $f = $wrap.find(selectors[i]);
		if ($f.length) {
			$rows = $f;
			break;
		}
	}
	if (!$rows.length) {
		$rows = $wrap.find('tbody tr[data-idx], .grid-body .grid-row').not('.grid-form-row');
	}
	if (!$rows.length) {
		return null;
	}
	$rows = $rows.filter(function () {
		const $t = $(this);
		if ($t.hasClass('dt-row-header') || $t.closest('.dt-row-header').length) {
			return false;
		}
		if ($t.closest('thead').length) {
			return false;
		}
		return true;
	});
	return $rows.length ? $rows : null;
}

/** DataTable / grid: row index matches `items` child order on cloud (grid_rows_by_docname often incomplete). */
function sprGetPrimaryItemsRowTarget(frm, idx) {
	const $wrap = frm.fields_dict.items && frm.fields_dict.items.$wrapper;
	if (!$wrap || !$wrap.length) {
		return null;
	}
	let $dt = $wrap.find('.datatable .dt-row:not(.dt-row-filter)');
	if (!$dt.length) {
		$dt = $wrap.find('.dt-scrollable .dt-row:not(.dt-row-filter)');
	}
	if (!$dt.length) {
		$dt = $wrap.find('.dt-row:not(.dt-row-filter)');
	}
	if ($dt.length > idx) {
		return $($dt.get(idx));
	}
	const $gr = $wrap.find('.grid-body .grid-row').not('.grid-form-row');
	if ($gr.length > idx) {
		return $($gr.get(idx));
	}
	return null;
}

/**
 * Prefer Frappe GridRow at index (matches child table order; works when DataTable only mounts row 0 in DOM).
 * Then docname lookup, then DOM fallbacks.
 */
function sprResolveItemsRowWrapper(frm, doc, grid, idx) {
	let gr = null;
	if (grid && grid.grid_rows && grid.grid_rows[idx] !== undefined) {
		gr = grid.grid_rows[idx];
	}
	if (gr) {
		if (gr.wrapper && gr.wrapper.length) {
			return gr.wrapper;
		}
		if (gr.row && gr.row.length) {
			return gr.row;
		}
	}
	gr = doc && doc.name && grid.grid_rows_by_docname && grid.grid_rows_by_docname[doc.name];
	if (gr) {
		if (gr.wrapper && gr.wrapper.length) {
			return gr.wrapper;
		}
		if (gr.row && gr.row.length) {
			return gr.row;
		}
	}
	const $byIdx = sprGetPrimaryItemsRowTarget(frm, idx);
	if ($byIdx && $byIdx.length) {
		return $byIdx;
	}
	return sprResolveItemsRowElement(frm, doc, grid, idx);
}

/** Resolve the visible row node for a child row (Frappe 16 DataTable uses .dt-row; index order can diverge). */
function sprFindItemsRowDomByDocname(frm, doc, idx) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	const $wrap = frm.fields_dict.items && frm.fields_dict.items.$wrapper;
	
	if ($wrap && $wrap.length && idx !== undefined && idx !== null) {
		const $byRowIdx = $wrap.find('.dt-row[data-row-index="' + idx + '"]');
		if ($byRowIdx.length) return $byRowIdx.first();
	}
	
	if (grid && grid.grid_rows && grid.grid_rows.length) {
		for (let i = 0; i < grid.grid_rows.length; i++) {
			const gr = grid.grid_rows[i];
			if (!gr || !gr.doc || gr.doc.name !== doc.name) {
				continue;
			}
			if (gr.wrapper && gr.wrapper.length) {
				return gr.wrapper;
			}
			if (gr.row && gr.row.length) {
				return gr.row;
			}
		}
	}
	const grByName = doc && doc.name && grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[doc.name];
	if (grByName) {
		if (grByName.wrapper && grByName.wrapper.length) {
			return grByName.wrapper;
		}
		if (grByName.row && grByName.row.length) {
			return grByName.row;
		}
	}
	if (!$wrap || !$wrap.length || !doc || !doc.name) {
		return null;
	}
	const name = String(doc.name);
	const $byDom = $wrap.find('.datatable .dt-row, .dt-row, .grid-row, tbody tr[data-idx]').filter(function () {
		const $t = $(this);
		return $t.attr('data-docname') === name || $t.attr('data-name') === name;
	});
	return $byDom.length ? $byDom.first() : null;
}

function sprCollectItemRowTargets(frm, doc, idx, $primaryRow, $wrap) {
	if (!$wrap || !$wrap.length) {
		$wrap = frm.fields_dict.items && frm.fields_dict.items.$wrapper;
	}
	let $targets = $();
	if ($primaryRow && $primaryRow.length) {
		$targets = $targets.add($primaryRow);
		const $ed = $primaryRow.closest('.editable-row');
		if ($ed && $ed.length) {
			$targets = $targets.add($ed);
		}
	}
	if (!doc || !doc.name || !$wrap || !$wrap.length) {
		return $targets;
	}
	const $byDoc = $wrap.find('.grid-row, .dt-row').filter(function () {
		const $t = $(this);
		return $t.attr('data-docname') === doc.name || $t.attr('data-name') === doc.name;
	});
	$targets = $targets.add($byDoc);
	const $formRows = $wrap.find('.grid-form-row').filter(function () {
		return $(this).find('[data-name="' + doc.name + '"]').length > 0;
	});
	$targets = $targets.add($formRows);
	const $fig = $wrap.find('.form-in-grid').filter(function () {
		return $(this).find('[data-name="' + doc.name + '"]').length > 0;
	});
	$fig.each(function () {
		const $p = $(this).closest('.grid-form-row, .grid-row, tr, .dt-row');
		if ($p.length) {
			$targets = $targets.add($p);
		}
	});
	return $targets;
}

function apply_spr_item_row_styles(frm) {
	if (!frm || !frm.doc) {
		return;
	}
	if (spr_items_grid_is_editing(frm)) {
		frm._spr_row_styles_pending = true;
		return;
	}
	if (frm._spr_applying_row_styles) {
		return;
	}
	const itemsFd = spr_get_field_dict(frm, 'items');
	const grid = itemsFd && itemsFd.grid;
	if (!grid) {
		return;
	}
	frm._spr_applying_row_styles = true;
	const bandClasses = ['spr-gsm-band-0', 'spr-gsm-band-1', 'spr-gsm-band-2', 'spr-gsm-band-3'];
	const baseClasses =
		'spr-gsm-band-0 spr-gsm-band-1 spr-gsm-band-2 spr-gsm-band-3 spr-gsm-pending';
	const items = frm.doc.items || [];
	const $domRows = sprGetItemsDatatableBodyRows(frm);
	const $wrap = itemsFd.$wrapper;
	const styleCap = spr_is_submitted_spr(frm) && items.length > 25 ? 25 : items.length;

	items.forEach(function (doc, idx) {
		if (idx >= styleCap) {
			return;
		}
		// Try multiple resolution methods to find row element for DataTable / Frappe grids
		let $row = null;
		
		// Method 1: Try by index or docname first
		if (!$row || !$row.length) {
			$row = sprFindItemsRowDomByDocname(frm, doc, idx);
		}
		
		
		// Method 2: Use DOM rows array by index (DataTable body rows in order)
		// WARNING: This is unsafe for paginated grids if idx > visible rows, so only use if idx matches visible length
		if ((!$row || !$row.length) && $domRows && $domRows.length > idx && frm.page_length && idx < frm.page_length) {
			$row = $($domRows.get(idx));
		}
		
		// Method 3: Try wrapper resolution by index
		if (!$row || !$row.length) {
			$row = sprResolveItemsRowWrapper(frm, doc, grid, idx);
		}
		
		// Method 4: Direct selector search if other methods fail
		// Skip for large grids (>20 rows) — this DOM scan is O(N²) and causes major lag
		if ((!$row || !$row.length) && $wrap && $wrap.length && doc && doc.name && items.length <= 20) {
			$row = $wrap
				.find('.dt-row, .grid-row, tbody tr')
				.filter(function (i) {
					return i === idx || $(this).attr('data-docname') === doc.name || $(this).attr('data-name') === doc.name;
				})
				.first();
		}
		
		if (!$row || !$row.length) {
			sprLog('Could not resolve row for item at index', idx, doc);
			return;
		}
		
		const $targets = sprCollectItemRowTargets(frm, doc, idx, $row, $wrap);
		// Roll Production Results: Sticker GSM vs produced (field or computed from net/gross × width × length)
		const sticker = sprStickerGsmFromDoc(doc);
		const produced = sprEffectiveProducedGsm(doc, frm);
		$targets.removeClass(baseClasses);
		$targets.each(function () {
			sprClearRowBg($(this));
		});
		if (sticker > 0 && produced > 0) {
			const diff = Math.abs(produced - sticker);
			let band = 3;
			if (diff < 1) {
				band = 0;
			} else if (diff < 2) {
				band = 1;
			} else if (diff < 3) {
				band = 2;
			}
			$targets.addClass(bandClasses[band]);
			$targets.each(function () {
				sprApplyGsmRowVisual($(this), band);
			});
		} else {
			$targets.addClass('spr-gsm-pending');
			$targets.each(function () {
				sprApplyGsmRowVisual($(this), 'pending');
			});
		}
	});
	spr_apply_items_row_lock_ui(frm);
	frm._spr_applying_row_styles = false;
}


// ===== TOTAL PRODUCED WEIGHT CALCULATION =====

function spr_compute_total_produced_weight(frm) {
	if (!frm || !frm.doc) {
		return 0;
	}
	if (sprIsBag(frm)) {
		const bundles = frm.doc.bundle_calculation || [];
		let t = 0;
		for (let i = 0; i < bundles.length; i++) {
			t += flt(bundles[i].total_produced_bag_pcs);
		}
		return flt(t);
	}
	if (sprIsSheetCutting(frm)) {
		const bundles = frm.doc.bundle_calculation || [];
		let t = 0;
		for (let i = 0; i < bundles.length; i++) {
			t += flt(bundles[i].total_achieved_weight);
		}
		return spr_round_net_weight_kg(t);
	}
	const items = frm.doc.items || [];
	let total = 0;
	for (let i = 0; i < items.length; i++) {
		total += spr_round_net_weight_kg(items[i].net_weight);
	}
	return spr_round_net_weight_kg(total);
}

function spr_sync_bag_pcs_headers(frm, opts) {
	if (!frm || !frm.doc || !sprIsBag(frm)) {
		return;
	}
	const settings = opts || {};
	const bundles = frm.doc.bundle_calculation || [];
	let achieved = 0;
	bundles.forEach(function (br) {
		achieved += flt(br.total_produced_bag_pcs);
	});
	achieved = flt(achieved);
	if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_achieved_pcs')) {
		const curAch = flt(frm.doc.custom_total_achieved_pcs);
		if (Math.abs(curAch - achieved) > 0.5) {
			if (settings.silent) {
				frm.doc.custom_total_achieved_pcs = achieved;
				frm.refresh_field('custom_total_achieved_pcs');
			} else {
				frm.set_value('custom_total_achieved_pcs', achieved);
			}
		}
	}
}

function spr_sync_total_produced_weight(frm, opts) {
	if (!frm || !frm.doc) {
		return;
	}
	if (cint(frm.doc.docstatus) !== 0) {
		return;
	}
	const settings = opts || {};
	const calculated = spr_compute_total_produced_weight(frm);
	const current = sprIsBag(frm) ? flt(frm.doc.total_produced_weight) : spr_round_net_weight_kg(frm.doc.total_produced_weight);
	const tolerance = sprIsBag(frm) ? 0.5 : 0.001;

	if (Math.abs(current - calculated) > tolerance) {
		if (settings.silent) {
			frm.doc.total_produced_weight = calculated;
			if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_produced_weight')) {
				frm.doc.custom_total_produced_weight = calculated;
			}
			frm.refresh_field('total_produced_weight');
			if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_produced_weight')) {
				frm.refresh_field('custom_total_produced_weight');
			}
		} else {
			frm.set_value('total_produced_weight', calculated);
			if (frappe.meta.get_docfield('Shaft Production Run', 'custom_total_produced_weight')) {
				frm.set_value('custom_total_produced_weight', calculated);
			}
		}
	}
	if (sprIsBag(frm)) {
		spr_sync_bag_pcs_headers(frm, settings);
	}
}

