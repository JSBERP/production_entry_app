/* global frappe, __ */

const QC_DOCTYPE = "Quality Checking";
const TESTING_TYPES = {
	// Legacy callers map to Round (same flow as before GSM testing)
	gsm: "Round Cutting GSM Test",
	round_gsm: "Round Cutting GSM Test",
	patty_gsm: "Patty Cutting GSM Test",
	tensile: "Tensile Testing",
	colour_spectrum: "Colour Spectrum",
	color_spectrum: "Colour Spectrum",
};

const TEMPLATE_WIDTH_FIELDS = [
	"cutting_template_width",
	"custom_cutting_template_width",
];
const TEMPLATE_HEIGHT_FIELDS = [
	"cutting_template_height",
	"custom_cutting_template_height",
];

function cint(v) {
	const n = parseInt(v, 10);
	return Number.isFinite(n) ? n : 0;
}

function sprJobIds(doc) {
	const jobs = doc?.shaft_jobs || [];
	return (jobs || [])
		.map((j) => String(j.job_id || j.job || "").trim())
		.filter(Boolean);
}

async function promptSprJobId(ids, title) {
	if (!ids.length) {
		return "";
	}
	if (ids.length === 1) {
		return ids[0];
	}
	return new Promise((resolve) => {
		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "job_id",
					label: __("Job"),
					options: ids.map((id) => ({ value: id, label: id })),
					reqd: 1,
				},
			],
			(values) => resolve(values.job_id || ids[0]),
			title || __("Quality Check — choose job"),
			__("Continue")
		);
	});
}

async function resolveJobIdForSpr(sprName, jobId) {
	const doc = await loadSprDoc(sprName);
	const preferred = String(jobId || "").trim();
	if (preferred && rollsForSprJob(doc, preferred).length) {
		return preferred;
	}
	const rollJobIds = sprJobIdsWithRolls(doc);
	const shaftJobIds = sprJobIds(doc);
	const ids = rollJobIds.length ? rollJobIds : shaftJobIds;
	if (!ids.length) {
		return "";
	}
	if (preferred && ids.includes(preferred)) {
		return preferred;
	}
	return promptSprJobId(ids);
}

async function loadSprDoc(sprName) {
	const res = await frappe.call({
		method: "production_entry.production_planning.unified_production_entry_api.get_gsm_spr_doc",
		args: { spr_name: sprName },
	});
	return res.message || {};
}

function sprItemJobId(row) {
	return String(row?.job || row?.job_id || "").trim();
}

function sprJobIdsMatch(a, b) {
	const left = String(a || "").trim();
	const right = String(b || "").trim();
	if (!left || !right) {
		return false;
	}
	return left === right;
}

function pickSprJobRow(spr, jobId) {
	const jid = String(jobId || "").trim();
	return (
		(spr.shaft_jobs || []).find((j) => sprJobIdsMatch(j.job_id || j.job, jid)) || null
	);
}

function rollSuffix(row) {
	const batch = String(row?.batch_no || "");
	if (batch.includes("/")) {
		return cint(batch.split("/").pop());
	}
	return cint(row?.roll_no);
}

function rollsForSprJob(spr, jobId) {
	const jid = String(jobId || "").trim();
	return (spr.items || []).filter((row) => {
		if (!sprJobIdsMatch(sprItemJobId(row), jid)) {
			return false;
		}
		if (cint(row.is_wasted) || cint(row.is_bundle_row)) {
			return false;
		}
		return Boolean(String(row.batch_no || "").trim() || cint(row.roll_no));
	});
}

function sprJobIdsWithRolls(spr) {
	const ids = new Set();
	for (const row of spr.items || []) {
		if (cint(row.is_wasted) || cint(row.is_bundle_row)) {
			continue;
		}
		if (!String(row.batch_no || "").trim() && !cint(row.roll_no)) {
			continue;
		}
		const jid = sprItemJobId(row);
		if (jid) {
			ids.add(jid);
		}
	}
	return [...ids];
}

async function promptRollForSprJob(spr, jobId) {
	const rolls = rollsForSprJob(spr, jobId);
	if (!rolls.length) {
		return null;
	}
	const options = rolls.map((row) => {
		const batch = String(row.batch_no || "").trim();
		const suffix = rollSuffix(row);
		const gsm = cint(row.gsm);
		const label = [batch || `Roll ${suffix || row.idx || ""}`, gsm ? `${gsm} GSM` : ""]
			.filter(Boolean)
			.join(" · ");
		return { value: batch || String(row.name || row.idx || suffix), label };
	});
	return new Promise((resolve) => {
		frappe.prompt(
			[
				{
					fieldtype: "Select",
					fieldname: "batch_no",
					label: __("Batch No"),
					options,
					reqd: 1,
					default: options.length === 1 ? options[0].value : "",
				},
			],
			(values) => {
				const picked =
					rolls.find((r) => String(r.batch_no || "").trim() === values.batch_no) ||
					rolls.find((r) => String(r.name || "") === values.batch_no);
				resolve(picked || rolls[0]);
			},
			__("Quality Check — select batch"),
			__("Continue")
		);
	});
}

async function findExistingQcDraft(sprName, testingType, selectedRoll) {
	const batchNo = String(selectedRoll?.batch_no || "").trim();
	const rollNo = cint(selectedRoll?.roll_no) || rollSuffix(selectedRoll);
	const baseFilters = {
		shaft_production_run: sprName,
		testing_type: testingType,
		docstatus: 0,
	};
	try {
		if (batchNo) {
			const byBatch = await frappe.db.get_list(QC_DOCTYPE, {
				filters: { ...baseFilters, batch_no: batchNo },
				fields: ["name"],
				limit: 1,
				order_by: "modified desc",
			});
			if (byBatch?.[0]?.name) {
				return byBatch[0].name;
			}
		}
		if (rollNo) {
			const byRoll = await frappe.db.get_list(QC_DOCTYPE, {
				filters: { ...baseFilters, roll_no: rollNo },
				fields: ["name"],
				limit: 1,
				order_by: "modified desc",
			});
			if (byRoll?.[0]?.name) {
				return byRoll[0].name;
			}
		}
	} catch (e) {
		console.warn("findExistingQcDraft", e);
	}
	return "";
}

function qcMetaHasField(fieldname) {
	try {
		const meta = frappe.get_meta(QC_DOCTYPE);
		const fields = (meta && meta.fields) || [];
		return !!fields.find((f) => f.fieldname === fieldname);
	} catch {
		return false;
	}
}

function firstSprField(spr, candidates) {
	for (const fn of candidates) {
		const v = spr?.[fn];
		if (v !== undefined && v !== null && String(v).trim() !== "") {
			return { fieldname: fn, value: v };
		}
	}
	return null;
}

function applyPattyTemplateDefaults(defaults, spr) {
	const isPatty =
		String(defaults.testing_type || "").toLowerCase().includes("patty");
	if (!isPatty) {
		return;
	}
	const w = firstSprField(spr, TEMPLATE_WIDTH_FIELDS);
	if (w) {
		for (const fn of TEMPLATE_WIDTH_FIELDS) {
			if (qcMetaHasField(fn)) {
				defaults[fn] = w.value;
				break;
			}
		}
	}
	const h = firstSprField(spr, TEMPLATE_HEIGHT_FIELDS);
	if (h) {
		for (const fn of TEMPLATE_HEIGHT_FIELDS) {
			if (qcMetaHasField(fn)) {
				defaults[fn] = h.value;
				break;
			}
		}
	}
}

async function openQualityCheckingDoc(sprName, testType, jobId, rollRow) {
	try {
		const exists = await frappe.db.exists("DocType", QC_DOCTYPE);
		if (exists === false || exists === 0) {
			frappe.msgprint(
				__(
					"Quality Checking app is not installed on this site. Install the quality-check custom app."
				)
			);
			return;
		}
	} catch (e) {
		console.warn("Quality Checking DocType check failed", e);
	}

	const testingType = TESTING_TYPES[testType] || testType;
	const spr = await loadSprDoc(sprName);
	const jobRow = pickSprJobRow(spr, jobId);
	const selectedRoll = rollRow || (await promptRollForSprJob(spr, jobId));
	if (!selectedRoll) {
		frappe.msgprint(__("No rolls found for this job."));
		return;
	}

	const existingName = await findExistingQcDraft(sprName, testingType, selectedRoll);
	if (existingName) {
		frappe.set_route("Form", QC_DOCTYPE, existingName);
		return;
	}

	const rollGsm = cint(selectedRoll?.gsm) || cint(jobRow?.gsm) || 0;
	const batchNo = String(selectedRoll?.batch_no || "").trim();
	const rollNo = cint(selectedRoll?.roll_no) || rollSuffix(selectedRoll) || 0;

	try {
		const created = await frappe.call({
			method: "quality_gsm_app.api.quality.create_quality_checking_from_shaft",
			args: {
				shaft_production_run: sprName,
				batch_no: batchNo,
				testing_type: testingType,
				roll_no: rollNo,
				gsm: rollGsm || "",
			},
			freeze: true,
		});
		if (created?.message) {
			frappe.set_route("Form", QC_DOCTYPE, created.message);
			return;
		}
	} catch (e) {
		console.warn("create_quality_checking_from_shaft failed, opening a blank form", e);
	}

	const defaults = {
		shaft_production_run: sprName,
		testing_type: testingType,
		unit: spr.custom_unit || spr.unit || "",
		shift: spr.shift || "",
		order_code: spr.custom_order_code || spr.order_code || "",
		quality: selectedRoll?.quality || jobRow?.quality || "",
		color: selectedRoll?.color || jobRow?.color || "",
		batch_no: batchNo,
		roll_no: rollNo,
		gsm: rollGsm || "",
	};
	if (rollGsm > 0) {
		if (qcMetaHasField("set_gsm")) {
			defaults.set_gsm = rollGsm;
		}
		if (qcMetaHasField("target_gsm")) {
			defaults.target_gsm = rollGsm;
		}
	}
	if (qcMetaHasField("job_id") && jobId) {
		defaults.job_id = jobId;
	}
	applyPattyTemplateDefaults(defaults, spr);

	frappe.route_options = { ...defaults };
	if (typeof frappe.new_doc === "function") {
		frappe.new_doc(QC_DOCTYPE, defaults);
		return;
	}

	await new Promise((resolve, reject) => {
		frappe.model.with_doctype(QC_DOCTYPE, () => {
			try {
				const doc = frappe.model.get_new_doc(QC_DOCTYPE);
				Object.assign(doc, defaults);
				frappe.set_route("Form", QC_DOCTYPE, doc.name);
				resolve();
			} catch (e) {
				reject(e);
			}
		});
	});
}

async function openSprQualityCheck(sprName, testType, jobId) {
	const resolvedJob = await resolveJobIdForSpr(sprName, jobId);
	const hook =
		window.__spr_quality_check &&
		typeof window.__spr_quality_check[testType] === "function"
			? window.__spr_quality_check[testType]
			: null;
	if (hook) {
		return hook(sprName, resolvedJob);
	}
	if (!resolvedJob) {
		frappe.msgprint(__("No jobs on this SPR — add a job first."));
		return;
	}
	const spr = await loadSprDoc(sprName);
	const selectedRoll = await promptRollForSprJob(spr, resolvedJob);
	if (!selectedRoll) {
		frappe.msgprint(__("No rolls found for this job."));
		return;
	}
	await openQualityCheckingDoc(sprName, testType, resolvedJob, selectedRoll);
}

frappe.provide("production_entry.spr_quality_check");

production_entry.spr_quality_check.openSprGsmTesting = (sprName, jobId) =>
	openSprQualityCheck(sprName, "round_gsm", jobId);
production_entry.spr_quality_check.openSprRoundCuttingGsmTesting = (sprName, jobId) =>
	openSprQualityCheck(sprName, "round_gsm", jobId);
production_entry.spr_quality_check.openSprPattyCuttingGsmTesting = (sprName, jobId) =>
	openSprQualityCheck(sprName, "patty_gsm", jobId);
production_entry.spr_quality_check.openSprTensileTesting = (sprName, jobId) =>
	openSprQualityCheck(sprName, "tensile", jobId);
production_entry.spr_quality_check.openSprColourSpectrum = (sprName, jobId) =>
	openSprQualityCheck(sprName, "colour_spectrum", jobId);
