frappe.provide("production_entry.board_access_form");

const BOARD_SELECT_FALLBACK =
	"production-board|Production Board (Kanban)\n" +
	"printing-order-board|Printing Order Board\n" +
	"lamination-board|Lamination Board\n" +
	"slitting-board|Slitting Board\n" +
	"slitting-order-table|Slitting Order Table\n" +
	"rewinding-board|Rewinding Board\n" +
	"sheet-cutting-board|Sheet Cutting Board\n" +
	"printed-bopp-film-board|Printed BOPP Film Board\n" +
	"box-bag-board|Box Bag Board\n" +
	"w-cut-d-cut-board|W CUT / D CUT Board\n" +
	"color-chart|Color Chart\n" +
	"confirm-orders|Confirm Orders\n" +
	"planning|Planning\n" +
	"gsm-production-entry|GSM Production Entry\n" +
	"lamination-production-entry|Lamination Production Entry\n" +
	"slitting-production-entry|Slitting Production Entry\n" +
	"rewinding-production-entry|Rewinding Production Entry\n" +
	"sheet-cutting-production-entry|Sheet Cutting Production Entry\n" +
	"bopp-printing-production-entry|BOPP Printing Production Entry\n" +
	"flexo-printing-production-entry|Flexo Printing Production Entry\n" +
	"bag-making-production-entry|Bag Making Production Entry\n" +
	"logistics-kanban|Logistics Kanban\n" +
	"transfer-approval-dashboard|Transfer Approval\n" +
	"despatch-approval-dashboard|Despatch Approval\n" +
	"production-learning|Production Learning";

function normalizeBoardSlug(raw) {
	const s = String(raw || "").trim().toLowerCase();
	if (!s) return "";
	return (s.includes("|") ? s.split("|")[0] : s).trim();
}

function isWCutDCutBoardSlug(raw) {
	const slug = normalizeBoardSlug(raw);
	return slug === "w-cut-d-cut-board";
}

function wCutCompaniesFromBoardRows(rows) {
	const out = new Set();
	let hasWCut = false;
	let hasOtherBoards = false;
	(rows || []).forEach((row) => {
		if (!row.board) return;
		if (isWCutDCutBoardSlug(row.board)) {
			hasWCut = true;
			const c = String(row.w_cut_d_cut_company || "Both").trim().toUpperCase();
			out.add(c === "JVE" || c === "VTP" ? c : "BOTH");
			return;
		}
		hasOtherBoards = true;
	});
	return { hasWCut, hasOtherBoards, companies: [...out] };
}

function applyBoardSelectOptions(frm, options) {
	const boards = frm.fields_dict.allowed_boards;
	if (boards && boards.grid) {
		boards.grid.update_docfield_property(
			"board",
			"options",
			options || BOARD_SELECT_FALLBACK
		);
	}
}

function toggleWCutCompanyFieldVisibility(frm, row) {
	const boards = frm.fields_dict.allowed_boards;
	if (!boards || !boards.grid || !row) return;
	const show = isWCutDCutBoardSlug(row.board);
	boards.grid.toggle_display("w_cut_d_cut_company", show, row.name);
	if (show && !row.w_cut_d_cut_company) {
		frappe.model.set_value(row.doctype, row.name, "w_cut_d_cut_company", "Both");
	} else if (!show && row.w_cut_d_cut_company) {
		frappe.model.set_value(row.doctype, row.name, "w_cut_d_cut_company", "");
	}
}

function setupAllowedUnitsQuery(frm) {
	frm.set_query("unit", "allowed_units", () => {
		const meta = wCutCompaniesFromBoardRows(frm.doc.allowed_boards || []);
		return {
			query:
				"production_entry.production_planning.board_access.production_board_access_workstation_query",
			filters: {
				has_w_cut: meta.hasWCut ? 1 : 0,
				has_other_boards: meta.hasOtherBoards ? 1 : 0,
				w_cut_companies: meta.companies,
			},
		};
	});
}

function decorateBoardAccessForm(frm) {
	if (!frm.$wrapper || frm.$wrapper.data("pp-board-access-styled")) return;
	frm.$wrapper.data("pp-board-access-styled", 1);

	const $main = frm.$wrapper.find(".form-layout").first();
	if ($main.length) {
		$main.prepend(
			`<div class="pp-board-access-banner" style="margin:0 0 14px;padding:14px 18px;border-radius:10px;background:linear-gradient(135deg,#eff6ff,#f0fdf4);border:1px solid #bfdbfe;">
				<div style="font-weight:700;font-size:15px;color:#1e3a8a;margin-bottom:6px;">Production Board Access</div>
				<div style="font-size:12px;color:#334155;line-height:1.5;">
					Assign by <strong>user name</strong> (search shows name + email). <strong>Many users can share the same unit</strong> (shift-wise).
					Add <strong>one row per board</strong> (Color Chart, GSM, Logistics, Transfer/Despatch Approval, etc.).
					Tick <strong>Freeze</strong> checkboxes — labels show which screen they belong to:
					<strong>- PB</strong> Production Board/Table, <strong>- CC</strong> Color Chart,
					<strong>- GSM</strong> GSM Entry, <strong>- LK</strong> Logistics, <strong>- TA</strong> Transfer Approval, <strong>- DA</strong> Despatch Approval.
				</div>
			</div>`
		);
	}

	if (frm.doc.user_full_name) {
		frm.dashboard.set_headline(
			`<span style="font-size:14px;color:#0f172a;">${frappe.utils.escape_html(frm.doc.user_full_name)}</span>
			 <span style="color:#64748b;font-size:12px;">(${frappe.utils.escape_html(frm.doc.user || "")})</span>`
		);
	}
}

frappe.ui.form.on("Production Board Access", {
	onload(frm) {
		frm.set_query("user", () => ({
			query:
				"production_entry.production_planning.board_access.production_board_access_user_query",
		}));
		setupAllowedUnitsQuery(frm);
	},

	refresh(frm) {
		applyBoardSelectOptions(frm, BOARD_SELECT_FALLBACK);
		frappe.call({
			method:
				"production_entry.production_planning.board_access.get_production_board_page_options",
			callback: (r) => {
				if (r && r.message) {
					applyBoardSelectOptions(frm, r.message);
				}
			},
		});
		decorateBoardAccessForm(frm);
		setupAllowedUnitsQuery(frm);

		const boards = frm.fields_dict.allowed_boards;
		if (boards && boards.grid) {
			boards.grid.wrapper.find(".grid-heading-row").css({
				background: "#f8fafc",
				"font-weight": "600",
			});
			(frm.doc.allowed_boards || []).forEach((row) => toggleWCutCompanyFieldVisibility(frm, row));
		}
	},

	user(frm) {
		if (frm.doc.user) {
			frappe.db.get_value("User", frm.doc.user, "full_name").then((r) => {
				if (r && r.message) {
					frm.set_value("user_full_name", r.message.full_name || "");
				}
			});
		}
	},

	allowed_boards_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		toggleWCutCompanyFieldVisibility(frm, row);
	},
});

frappe.ui.form.on("Production Board Access Board", {
	board(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		toggleWCutCompanyFieldVisibility(frm, row);
		setupAllowedUnitsQuery(frm);
	},
	w_cut_d_cut_company(frm) {
		setupAllowedUnitsQuery(frm);
	},
});
