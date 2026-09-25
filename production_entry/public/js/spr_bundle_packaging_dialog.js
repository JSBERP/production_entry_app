/**
 * Bundle packaging dialog — same as SPR Actions → Bundle packaging.
 * Used by GSM Production Entry; SPR keeps its own copy in shaft_production_run.js.
 */

function _bpFlt(v) {
	return typeof flt === "function" ? flt(v) : parseFloat(v) || 0;
}

function _bpCint(v) {
	return typeof cint === "function" ? cint(v) : (() => {
		const n = parseInt(v, 10);
		return Number.isFinite(n) ? n : 0;
	})();
}

function _bpParseCombination(comb) {
	if (!comb) {
		return [];
	}
	const text = String(comb)
		.replace(/\u201c/g, '"')
		.replace(/\u201d/g, '"')
		.replace(/\u2033/g, '"')
		.replace(/\u2032/g, "'");
	return text
		.split("+")
		.map((part) => {
			const m = part.replace(/,/g, "").match(/(\d+(?:\.\d+)?)/);
			return m ? _bpFlt(m[1]) : 0;
		})
		.filter((w) => w > 0);
}

function _bpJobKeysMatch(a, b) {
	const na = String(a || "").trim();
	const nb = String(b || "").trim();
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

function _bpWidthFromItemCode(itemCode) {
	const s = String(itemCode || "");
	const m = s.match(/(\d+(?:\.\d+)?)\s*(?:IN|INCH|"|''|INCHES)\b/i) || s.match(/-(\d+(?:\.\d+)?)-(?:MM|IN)/i);
	return m ? _bpFlt(m[1]) : 0;
}

function _bpFormatWidthLabel(w) {
	const fw = _bpFlt(w);
	if (fw <= 0) {
		return "";
	}
	return Math.abs(fw - Math.round(fw)) < 0.001 ? String(Math.round(fw)) : fw.toFixed(1);
}

function _bpJobLabel(j) {
	return __("Job") + " " + String(j.job_id || "");
}

function _bpWidthSelectEl(dialog) {
	return dialog.$wrapper.find("select.spr-bundle-width-select");
}

function _bpSetWidthOptions(dialog, widthValues) {
	const $sel = _bpWidthSelectEl(dialog);
	if (!$sel.length) {
		return [];
	}
	const seen = new Set();
	const labels = [];
	(widthValues || []).forEach((v) => {
		const fw = _bpFlt(v);
		if (fw <= 0) {
			return;
		}
		const key = String(Math.round(fw * 1000) / 1000);
		if (seen.has(key)) {
			return;
		}
		seen.add(key);
		labels.push(_bpFormatWidthLabel(fw));
	});
	labels.sort((a, b) => _bpFlt(a) - _bpFlt(b));
	$sel.empty();
	if (!labels.length) {
		$sel.append($("<option>").val("").text(__("Select width")));
	} else {
		labels.forEach((lbl) => {
			$sel.append($("<option>").val(lbl).text(lbl + '"'));
		});
		$sel.val(labels[0]);
	}
	return labels;
}

function _bpGetWidth(dialog) {
	return _bpFlt(_bpWidthSelectEl(dialog).val());
}

function _bpResolveJob(jobPickVal, jobById, jobByLabel) {
	const raw = String(jobPickVal || "").trim();
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

/** Segment + roll line widths only — never total combination width. */
function _bpCollectWidthOptions(jp, widthsByJob, rollItems, segs) {
	const out = [];
	const seen = new Set();
	function add(w) {
		const fw = _bpFlt(w);
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
	_bpParseCombination(jp.combination_text).forEach(add);
	(segs || []).forEach((s) => {
		add(s.width_inch);
	});
	(rollItems || []).forEach((it) => {
		const jMatch = String(it.job || it.job_no || it.job_id || "").trim();
		if (_bpJobKeysMatch(jMatch, jp.job_id)) {
			add(it.width_inch);
			if (it.item_code) {
				const parsed = _bpWidthFromItemCode(it.item_code);
				if (parsed > 0) {
					add(parsed);
				}
			}
		}
	});
	return out.sort((a, b) => a - b);
}

/**
 * @param {{ sprName: string, gsmMode?: boolean, ppId?: string, onSuccess?: (result?: object) => void }} opts
 */
export function openSprBundlePackagingDialog(opts) {
	opts = opts || {};
	const sprName = opts.sprName;
	const gsmMode = !!opts.gsmMode;
	const ppId = opts.ppId || "";
	if (!sprName) {
		frappe.msgprint(__("Save or select a Shaft Production Run first."));
		return;
	}

	function openBundleDialog(jobs, widthsByJob, rollItems) {
		if (!jobs.length) {
			frappe.msgprint(__("Add Available Jobs (shaft jobs) first."));
			return;
		}
		const jobById = {};
			const jobByLabel = {};
			jobs.forEach((j) => {
				const jid = String(j.job_id);
				j.widths = j.widths || widthsByJob[j.job_id] || [];
				jobById[jid] = j;
				const shortLbl = _bpJobLabel(j);
				jobByLabel[shortLbl] = j;
				jobByLabel[jid] = j;
				if (j.label) {
					jobByLabel[String(j.label)] = j;
				}
			});
			const jobOpts = jobs.map((j) => _bpJobLabel(j)).join("\n");

			const d = new frappe.ui.Dialog({
				title: __("Bundle packaging"),
				fields: [
					{
						fieldname: "spr_bundle_hint",
						fieldtype: "HTML",
						options:
							"<style>" +
							".spr-bundle-seg-table{font-size:13px;margin:8px 0;width:100%;}" +
							".spr-bundle-seg-table th{background:#f1f5f9;font-weight:700;padding:6px 8px;}" +
							".spr-bundle-seg-table td{padding:6px 8px;}" +
							".spr-bundle-width-wrap{margin:8px 0 12px;}" +
							".spr-bundle-width-wrap label{font-weight:600;font-size:12px;color:#334155;display:block;margin-bottom:4px;}" +
							".spr-bundle-width-select{font-size:15px;font-weight:600;min-height:38px;width:100%;}" +
							".spr-bundle-qty-inp{width:72px;min-height:34px;font-size:14px;font-weight:600;}" +
							"</style>" +
							'<p class="text-muted small" style="margin-bottom:10px;">' +
							__(
								"Choose Single width (one size) or Multi width (combo e.g. 30+33 on one sticker). Enter whole gross and length, then Apply."
							) +
							"</p>",
					},
					{
						fieldname: "job_pick",
						fieldtype: "Select",
						label: __("Job ID (Available Jobs)"),
						options: jobOpts,
						reqd: 1,
					},
					{
						fieldname: "job_detail_html",
						fieldtype: "HTML",
						options: '<div class="spr-bundle-job-detail text-muted small"></div>',
					},
					{
						fieldname: "pack_mode",
						fieldtype: "Select",
						label: __("Packaging mode"),
						options: ["Single width", "Multi width"].join("\n"),
						default: "Single width",
						reqd: 1,
					},
					{
						fieldname: "bundle_width_wrap",
						fieldtype: "HTML",
						options:
							'<div class="spr-bundle-width-wrap">' +
							'<div class="spr-bundle-single-only">' +
							"<label>" +
							__("Width") +
							"</label>" +
							'<select class="form-control spr-bundle-width-select">' +
							'<option value="">' +
							__("Select width") +
							"</option>" +
							"</select></div>" +
							'<div class="spr-bundle-multi-only" style="display:none;margin-top:8px;">' +
							"<label>" +
							__("Rolls per width (same count = one combo packaging set)") +
							"</label>" +
							'<div class="spr-bundle-width-qty-table"></div>' +
							'<p class="text-muted small" style="margin-top:6px;">' +
							__(
								"Example: 30\"=3 and 33\"=3 → sticker 3 * 30 + 3 * 33 Inches (3 packaging sets)."
							) +
							"</p></div></div>",
					},
					{
						fieldname: "calc_html",
						fieldtype: "HTML",
						options: '<div class="spr-bundle-calc text-muted small"></div>',
					},
					{
						fieldname: "no_of_packaging",
						fieldtype: "Int",
						label: __("Number of packaging"),
						reqd: 0,
						default: 1,
					},
					{
						fieldname: "whole_gross_kg",
						fieldtype: "Float",
						label: __("Whole gross (Kg)"),
						reqd: 1,
					},
					{
						fieldname: "produced_length_mtrs",
						fieldtype: "Float",
						label: __("Produced length (Mtrs)"),
						reqd: 1,
					},
				],
				primary_action_label: __("Apply"),
				primary_action(values) {
					const jp = _bpResolveJob(values.job_pick, jobById, jobByLabel);
					const mode = String(values.pack_mode || "Single width");
					const isMulti = mode.indexOf("Multi") === 0;
					const mix = _bpCollectWidthMix(d);
					const w = _bpGetWidth(d);
					const n = _bpCint(values.no_of_packaging);
					const whole = _bpFlt(values.whole_gross_kg);
					const producedLength = _bpFlt(values.produced_length_mtrs);
					if (!jp || !jp.job_id) {
						frappe.msgprint(__("Select a job."));
						return;
					}
					let widthMix = [];
					let widthInch = 0;
					let packCount = 0;
					if (isMulti) {
						if (mix.length < 2) {
							frappe.msgprint(
								__("Multi width needs rolls on at least 2 widths (e.g. 30 and 33).")
							);
							return;
						}
						widthMix = mix;
						packCount = widthMix.reduce((s, m) => s + m.rolls, 0);
						widthInch = 0;
					} else {
						if (w <= 0) {
							frappe.msgprint(__("Select a width."));
							return;
						}
						if (n < 1) {
							frappe.msgprint(__("Enter a valid packaging count."));
							return;
						}
						widthMix = [{ width_inch: w, rolls: n }];
						packCount = n;
						widthInch = w;
					}
					if (packCount < 1 || whole <= 0) {
						frappe.msgprint(__("Enter a valid roll count and whole gross weight."));
						return;
					}
					if (producedLength <= 0) {
						frappe.msgprint(__("Enter valid produced length."));
						return;
					}
					d.hide();
					const applyMethod = gsmMode
						? "production_entry.production_planning.unified_production_entry_api.gsm_apply_bundle_packaging"
						: "production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_apply_bundle_packaging_for_job_width";
					const applyArgs = gsmMode
						? {
								shaft_production_run: sprName,
								job_id: jp.job_id,
								width_inch: widthInch || undefined,
								no_of_packaging: packCount,
								whole_gross_kg: whole,
								produced_length_mtrs: producedLength,
								pp_id: ppId || undefined,
								width_mix: JSON.stringify(widthMix),
						  }
						: {
								shaft_production_run: sprName,
								job_id: jp.job_id,
								width_inch: widthInch || widthMix[0].width_inch,
								no_of_packaging: packCount,
								whole_gross_kg: whole,
								produced_length_mtrs: producedLength,
						  };
					if (!gsmMode && widthMix.length > 1) {
						frappe.msgprint(
							__("Desk SPR pack currently applies one width at a time. Use GSM Bundle packaging for multi-width, or apply each width separately.")
						);
						return;
					}
					frappe.call({
						method: applyMethod,
						args: applyArgs,
						freeze: true,
						freeze_message: __("Applying bundle packaging..."),
						callback(r3) {
							const m = r3.message || {};
							if (gsmMode) {
								frappe.show_alert({
									message: __(
										"Bundle created: {0} roll(s), sticker {1}",
										[String(m.updated_rolls || ""), String(m.bundle_batch_no || "")]
									),
									indicator: "green",
								});
								if (typeof opts.onSuccess === "function") {
									opts.onSuccess(m);
								}
								return;
							}
							frappe.show_alert({
								message: __(
									"Updated {0} roll(s). Remaining unpacked: {4}. Single gross {1} Kg, sticker width {2} Inches, bundle net {3} Kg.",
									[
										String(m.updated_rolls != null ? m.updated_rolls : ""),
										String(m.single_roll_gross_kg != null ? m.single_roll_gross_kg : ""),
										String(m.total_width_inch != null ? m.total_width_inch : ""),
										String(m.sticker_bundle_weight_kg != null ? m.sticker_bundle_weight_kg : ""),
										String(m.remaining_unpacked_rolls != null ? m.remaining_unpacked_rolls : ""),
									]
								),
								indicator: "green",
							});
							if (typeof opts.onSuccess === "function") {
								opts.onSuccess(m);
							}
						},
					});
				},
			});

			function _bpIsMultiMode() {
				const mode = String(d.get_value("pack_mode") || "Single width");
				return mode.indexOf("Multi") === 0;
			}

			function _bpApplyPackModeVisibility() {
				const multi = _bpIsMultiMode();
				d.$wrapper.find(".spr-bundle-single-only").toggle(!multi);
				d.$wrapper.find(".spr-bundle-multi-only").toggle(multi);
				d.set_df_property("no_of_packaging", "hidden", multi ? 1 : 0);
				recalc();
			}

			function _bpCollectWidthMix(dialog) {
				const mix = [];
				dialog.$wrapper.find("input.spr-bundle-qty-inp").each(function () {
					const w = _bpFlt($(this).attr("data-width"));
					const n = _bpCint($(this).val());
					if (w > 0 && n > 0) {
						mix.push({ width_inch: w, rolls: n });
					}
				});
				return mix;
			}

			function _bpRenderWidthQtyTable(dialog, widthValues) {
				const $wrap = dialog.$wrapper.find(".spr-bundle-width-qty-table");
				if (!$wrap.length) {
					return;
				}
				const widths = [];
				const seen = new Set();
				(widthValues || []).forEach((v) => {
					const fw = _bpFlt(v);
					if (fw <= 0) {
						return;
					}
					const key = String(Math.round(fw * 1000) / 1000);
					if (seen.has(key)) {
						return;
					}
					seen.add(key);
					widths.push(fw);
				});
				widths.sort((a, b) => a - b);
				if (!widths.length) {
					$wrap.html('<p class="text-muted small">' + __("No widths for this job.") + "</p>");
					return;
				}
				let html =
					'<table class="table table-bordered table-condensed spr-bundle-seg-table"><thead><tr><th>' +
					__("Width") +
					"</th><th>" +
					__("Rolls") +
					"</th></tr></thead><tbody>";
				widths.forEach((w) => {
					const lbl = _bpFormatWidthLabel(w);
					html +=
						"<tr><td><strong>" +
						lbl +
						'"</strong></td><td><input type="number" min="0" step="1" class="form-control spr-bundle-qty-inp" data-width="' +
						lbl +
						'" value="0" /></td></tr>';
				});
				html += "</tbody></table>";
				$wrap.html(html);
				$wrap.find("input.spr-bundle-qty-inp").on("change input", recalc);
			}

			function applySegsToDialog(jp, segs) {
				const det = d.$wrapper.find(".spr-bundle-job-detail");
				if (!jp) {
					return;
				}
				let widthOpts = _bpCollectWidthOptions(jp, widthsByJob, rollItems, segs);
				if (segs && segs.length) {
					const uniqueSegs = [];
					const seenWidths = new Set();
					segs.forEach((s) => {
						const w = _bpFlt(s.width_inch);
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
						__("Width") +
						"</th><th>" +
						__("Net/shaft (Kg)") +
						"</th><th>" +
						__("WO item") +
						"</th></tr></thead><tbody>";
					uniqueSegs.forEach((s) => {
						const net = s.net_kg_per_shaft != null ? _bpFlt(s.net_kg_per_shaft).toFixed(2) : "—";
						const ic = [s.item_code || "", (s.item_name || "").substring(0, 28)].join(" ").trim();
						html +=
							"<tr><td><strong>" +
							_bpFlt(s.width_inch).toFixed(1) +
							'"</strong></td><td>' +
							net +
							"</td><td>" +
							frappe.utils.escape_html(ic) +
							"</td></tr>";
					});
					html += "</tbody></table>";
					if (det.length) {
						det.html(html);
					}
				} else {
					const comb = jp.combination_text || "";
					if (det.length) {
						let head =
							'<p class="small"><strong>' +
							frappe.utils.escape_html(_bpJobLabel(jp)) +
							"</strong></p>";
						if (comb) {
							head +=
								'<p class="small text-muted" style="margin:4px 0 8px;">' +
								frappe.utils.escape_html(comb) +
								"</p>";
						}
						det.html(head);
					}
				}
				function finishWidthSelect() {
					const labels = _bpSetWidthOptions(d, widthOpts);
					_bpRenderWidthQtyTable(d, widthOpts);
					if (!labels.length) {
						frappe.msgprint(__("No width options for this job. Check combination / roll lines."));
					}
					recalc();
				}
				if (widthOpts.length) {
					finishWidthSelect();
					return;
				}
				frappe.call({
					method:
						"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_bundle_width_options",
					args: { shaft_production_run: sprName, job_id: jp.job_id },
					callback(r3) {
						const apiWidths = (r3.message || {}).widths || [];
						if (apiWidths.length) {
							jp.widths = apiWidths;
							widthOpts = _bpCollectWidthOptions(jp, widthsByJob, rollItems, segs);
						}
						finishWidthSelect();
					},
					error() {
						finishWidthSelect();
					},
				});
			}

			function refreshWidthOptions() {
				const jp = _bpResolveJob(d.get_value("job_pick"), jobById, jobByLabel);
				const det = d.$wrapper.find(".spr-bundle-job-detail");
				if (!jp) {
					_bpSetWidthOptions(d, []);
					return;
				}
				applySegsToDialog(jp, jp.segments || []);
				if (jp.segments && jp.segments.length) {
					return;
				}
				if (det.length && !jp.combination_text) {
					det.html('<span class="text-muted small">' + __("Loading segment detail...") + "</span>");
				}
				frappe.call({
					method:
						"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_job_segments",
					args: { shaft_production_run: sprName, job_id: jp.job_id },
					callback(r3) {
						jp.segments = r3.message || [];
						applySegsToDialog(jp, jp.segments);
					},
					error() {
						applySegsToDialog(jp, []);
					},
				});
			}

			function recalc() {
				const jp = _bpResolveJob(d.get_value("job_pick"), jobById, jobByLabel);
				const multi = _bpIsMultiMode();
				const mix = _bpCollectWidthMix(d);
				const wsel = _bpGetWidth(d);
				const n = _bpCint(d.get_value("no_of_packaging"));
				const whole = _bpFlt(d.get_value("whole_gross_kg"));
				const el = d.$wrapper.find(".spr-bundle-calc");
				if (!jp || !el.length) {
					return;
				}
				let planMix = [];
				if (multi) {
					planMix = mix.slice();
				} else if (wsel > 0 && n > 0) {
					planMix = [{ width_inch: wsel, rolls: n }];
				}
				const totalRolls = planMix.reduce((s, m) => s + m.rolls, 0);
				if (!totalRolls || whole <= 0) {
					el.html(
						multi
							? __("Enter rolls for each width and whole gross to preview.")
							: __("Select width, packaging count, and whole gross to preview.")
					);
					return;
				}
				const isMultiPlan = planMix.length > 1;
				const totalW = planMix.reduce((s, m) => s + m.width_inch * m.rolls, 0);
				const parts = planMix.map((m) => {
					const share = isMultiPlan
						? (whole * (m.width_inch * m.rolls)) / totalW
						: whole;
					const each = share / m.rolls;
					return m.rolls + "×" + _bpFormatWidthLabel(m.width_inch) + '" ≈ ' + each.toFixed(2) + " Kg/roll";
				});
				const comb = planMix.map((m) => m.rolls + " * " + _bpFormatWidthLabel(m.width_inch)).join(" + ") + " Inches";
				el.html(
					__("Preview: {0} roll(s). {1}. Sticker: {2}. Net uses core per width after Apply.", [
						String(totalRolls),
						parts.join(" · "),
						comb,
					])
				);
			}

			d.show();
			if (jobs.length > 0) {
				const firstLbl = _bpJobLabel(jobs[0]);
				d.set_df_property("job_pick", "options", jobOpts);
				if (d.fields_dict.job_pick) {
					d.fields_dict.job_pick.refresh();
				}
				d.set_value("job_pick", firstLbl);
			}
			setTimeout(() => {
				_bpApplyPackModeVisibility();
				refreshWidthOptions();
				recalc();
			}, 50);
			if (d.fields_dict.job_pick && d.fields_dict.job_pick.$input) {
				d.fields_dict.job_pick.$input.on("change", () => {
					const sel = _bpResolveJob(d.get_value("job_pick"), jobById, jobByLabel);
					if (sel) {
						sel.segments = [];
					}
					refreshWidthOptions();
					recalc();
				});
			}
			if (d.fields_dict.pack_mode && d.fields_dict.pack_mode.$input) {
				d.fields_dict.pack_mode.$input.on("change", () => {
					_bpApplyPackModeVisibility();
				});
			}
			_bpWidthSelectEl(d).on("change input", recalc);
			["no_of_packaging", "whole_gross_kg"].forEach((fn) => {
				const f = d.fields_dict[fn];
				if (f && f.$input) {
					f.$input.on("change input", recalc);
				}
			});
	}

	frappe.call({
		method:
			"production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.spr_get_bundle_packaging_catalog",
		args: { shaft_production_run: sprName },
		freeze: true,
		freeze_message: __("Loading jobs..."),
		callback(r) {
			const cat = r.message || {};
			let jobs = cat.jobs || [];
			const widthsByJob = cat.widths_by_job || {};
			if (!jobs.length && opts.fallbackJob && opts.fallbackJob.job_id) {
				const fb = opts.fallbackJob;
				jobs = [fb];
				const jid = String(fb.job_id);
				if (fb.widths && fb.widths.length && !widthsByJob[jid]) {
					widthsByJob[jid] = fb.widths;
				}
			}
			openBundleDialog(jobs, widthsByJob, []);
		},
	});
}
