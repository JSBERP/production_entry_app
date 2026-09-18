/* jsb-club-bootstrap — wires legacy frm.events.process_selections for old cached pickers */
(function () {
	if (window.__JSB_CLUB_BOOTSTRAP__) return;
	window.__JSB_CLUB_BOOTSTRAP__ = true;

	function wireFrm(frm) {
		if (!frm) return;
		const handler = function (f2, selections) {
			const orders = window._jsb_club_picker_orders || null;
			if (typeof window.jsb_club_add_selected_items === 'function') {
				window.jsb_club_add_selected_items(f2 || frm, selections, orders);
				return;
			}
			frappe.msgprint(__('Clubbing JS not loaded — hard refresh (Ctrl+Shift+R) and retry.'));
		};
		if (!frm.events) frm.events = {};
		frm.events.process_selections = handler;
		if (frm.cscript) frm.cscript.process_selections = handler;
	}

	frappe.ui.form.on('Clubbing Sheet', {
		refresh(frm) { wireFrm(frm); }
	});
})();

function show_rolls_dialog_JSB(frm, args) {
    let rolls = args.rolls;
    let so_name = args.sales_order;

    let html = '<style>' +
        '.rolls-view { font-family: Arial, sans-serif !important; color: #000 !important; background: #fff !important; padding: 0; }' +
        '.printable-area { width: 100%; max-width: 800px; margin: 0 auto; }' +
        '.company-header-table { width: 100%; border-collapse: collapse; border: 2px solid #2e7d32; margin-bottom: 10px; table-layout: fixed; }' +
        '.company-header-table td { padding: 10px; text-align: center; vertical-align: middle; }' +
        '.company-header-table img { height: 60px; width: auto; margin-bottom: 5px; }' +
        '.company-header-table h1 { font-size: 22px; font-weight: 900; margin: 0; text-transform: uppercase; color: #000; }' +
        '.company-header-table .doc-title { font-size: 11px; font-weight: bold; text-transform: uppercase; border-top: 1px solid #ccc; margin-top: 5px; padding-top: 5px; }' +
        '.info-row-table { width: 100%; border-collapse: collapse; margin-bottom: 10px; table-layout: fixed; }' +
        '.info-row-table td { border: 1px solid #555 !important; padding: 0; text-align: center; }' +
        '.info-box { height: 100%; display: block; }' +
        '.info-label { background: #f57f17 !important; color: #fff !important; font-size: 8px; font-weight: 700; text-transform: uppercase; padding: 2px 5px; -webkit-print-color-adjust: exact; print-color-adjust: exact; }' +
        '.info-value { font-size: 11px; font-weight: 700; padding: 4px 5px; }' +
        '.dt-table { width: 100%; border-collapse: collapse; border: 1px solid #000 !important; font-size: 10px; }' +
        '.dt-table th { background: #ffb74d !important; border: 1px solid #000 !important; padding: 6px; font-weight: 700; text-transform: uppercase; -webkit-print-color-adjust: exact; print-color-adjust: exact; }' +
        '.dt-table td { border: 1px solid #000 !important; padding: 5px 6px; text-align: center; vertical-align: middle; }' +
        '.dt-table tfoot td { background: #c8e6c9 !important; border: 1px solid #000 !important; font-weight: bold; color: #1b5e20 !important; -webkit-print-color-adjust: exact; print-color-adjust: exact; }' +
        '.tr { text-align: right !important; }' +
        '.fb { font-weight: 700 !important; }' +
        '@media print { .modal-header, .modal-footer { display: none !important; } .printable-area { width: 100% !important; margin: 0 !important; padding: 0 !important; } body { background: #fff !important; } }' +
        '</style>';

    html += '<div class="rolls-view printable-area" id="printable-rolls-area">';
    html += '<table class="company-header-table"><tr><td>' +
        '<img src="/private/files/JSB LOGO63b225.png" alt="JSB Logo"><br>' +
        '<h1>Jayashree Spun Bond</h1>' +
        '<div class="doc-title">Despatch Roll List | SO: ' + so_name + '</div>' +
        '</td></tr></table>';

    html += '<table class="info-row-table"><tr>' +
        '<td><div class="info-box"><div class="info-label">Date</div><div class="info-value">' + frappe.datetime.nowdate() + '</div></div></td>' +
        '<td><div class="info-box"><div class="info-label">Order Code</div><div class="info-value">' + so_name + '</div></div></td>' +
        '<td><div class="info-box"><div class="info-label">No. of Rolls</div><div class="info-value">' + rolls.length + '</div></div></td>' +
        '<td><div class="info-box"><div class="info-label">Report Type</div><div class="info-value">Order-Wise</div></div></td>' +
        '</tr></table>';

    html += '<table class="dt-table"><thead><tr>' +
        '<th>#</th><th>Batch No</th><th>Quality</th><th>Color</th><th>GSM</th><th>Size (")</th>' +
        '<th>Mtrs</th><th>Net Wt</th><th>Gross Wt</th>' +
        '</tr></thead><tbody>';

    let total_mtr = 0, total_net = 0, total_gross = 0;
    for (let i = 0; i < rolls.length; i++) {
        let r = rolls[i];
        let mtr = flt(r.meter_roll || r.meter_per_roll);
        let net = flt(r.net_weight);
        let gross = flt(r.gross_weight || (r.net_weight + 2));
        total_mtr += mtr;
        total_net += net;
        total_gross += gross;
        html += '<tr>' +
            '<td>' + (i + 1) + '</td>' +
            '<td class="fb">' + (r.batch_no || '') + '</td>' +
            '<td>' + (r.quality || '') + '</td>' +
            '<td>' + (r.color || '') + '</td>' +
            '<td>' + (r.gsm || '') + '</td>' +
            '<td>' + (r.width_inch || (r.width_mm ? (flt(r.width_mm) / 25.4).toFixed(1) : '-')) + '</td>' +
            '<td class="tr">' + mtr.toFixed(1) + '</td>' +
            '<td class="tr">' + net.toFixed(2) + '</td>' +
            '<td class="tr">' + gross.toFixed(2) + '</td>' +
            '</tr>';
    }

    html += '</tbody><tfoot><tr>' +
        '<td colspan="6" class="tr fb">TOTAL CONSOLIDATED DESPATCH</td>' +
        '<td class="tr">' + total_mtr.toFixed(1) + '</td>' +
        '<td class="tr">' + total_net.toFixed(2) + '</td>' +
        '<td class="tr">' + total_gross.toFixed(2) + '</td>' +
        '</tr></tfoot></table></div>';

    let d = new frappe.ui.Dialog({
        title: __('Rolls for Sales Order: {0}', [so_name]),
        fields: [{ fieldtype: 'HTML', fieldname: 'rolls_html', options: html }],
        size: 'extra-large',
        primary_action_label: __('Print for Despatch'),
        primary_action: function () {
            let print_window = window.open('', '_blank');
            print_window.document.write('<html><head><title>Roll List</title>');
            print_window.document.write('<style>@page { size: A4 portrait; margin: 10mm; }</style>');
            print_window.document.write(html);
            print_window.document.write('</body></html>');
            print_window.document.close();
            setTimeout(() => {
                print_window.print();
                print_window.close();
            }, 500);
        }
    });
    d.show();
}

const ROUTE_BELTS = [
    ["madurai", "virudhunagar", "sivakasi", "tuticorin", "thoothukudi"],
    ["madurai", "karur", "coimbatore"],
    ["madurai", "karur", "erode", "salem"],
    ["madurai", "dindigul", "karur", "salem"],
    ["madurai", "pondicherry", "puducherry", "vellore", "kanchipuram", "chennai"],
    ["madurai", "trivandrum", "thiruvananthapuram", "changanacherry"],
    ["madurai", "kollam", "kayankulam", "pathanamthitta", "kottayam"],
    ["madurai", "coimbatore", "palakkad", "trissur", "thrissur", "ernakulam"],
    ["madurai", "coimbatore", "pallakad", "trissur", "thrissur", "malappuram", "kozhikode", "calicut", "mahe", "kannur", "kasargod", "mangaluru", "mangalore", "uduppi", "udupi"],
    ["madurai", "mysore", "mysuru", "hassan", "shimoga", "dawangeree", "davangere", "davanagere"],
    ["madurai", "salem", "hosur", "bangalore", "bengaluru", "dawangeree", "davangere", "davanagere"],
    ["madurai", "mysore", "mysuru", "bangalore", "bengaluru"],
    ["madurai", "bangalore", "bengaluru", "tumkur", "hospet", "hospete", "koppal"],
    ["madurai", "ananthapur", "kurnool", "hyderabad", "karimnagar"],
    ["madurai", "ananthapur", "kurnool", "hyderabad", "nizambad"],
    ["madurai", "kurnool", "hyderabad", "warangal"],
    ["madurai", "vizag", "bhuvaneswar", "bhubaneswar", "cuttack"],
    ["madurai", "brahmbur", "berhampur", "bhubaneswar", "cuttack"],
    ["madurai", "guntur", "vijayawada", "kakinada"],
    ["madurai", "kakinada", "vizag"],
    ["madurai", "kuppam", "palamaner", "bangalore", "bengaluru"],
    ["madurai", "bangalore", "bengaluru", "hospete", "hospet", "vijayapura"],
    ["madurai", "bangalore", "bengaluru", "belgaum", "goa"],
    ["madurai", "bangalore", "bengaluru", "hospete", "hospet", "vijayapura", "satara", "pune", "mumbai"]
];

/** Fuzzy city↔belt match (exact / contains) — same idea as server clubbing_sheet_hooks. */
function jsb_club_city_in_belt(city, belt) {
    const c = String(city || "").trim().toLowerCase();
    if (!c) return false;
    return (belt || []).some(bc => c === bc || c.includes(bc) || bc.includes(c));
}

function jsb_club_cities_on_one_belt(cities) {
    const list = [...cities].filter(Boolean);
    if (list.length <= 1) return true; // same city / single destination = never a conflict
    return ROUTE_BELTS.some(belt => list.every(city => jsb_club_city_in_belt(city, belt)));
}

const MADURAI_DISTANCES = {
    "Madurai": 0, "Melur": 30, "Usilampatti": 45, "Manamadurai": 60, "Sivaganga": 55,
    "Dindigul": 65, "Virudhunagar": 65, "Theni": 70, "Srivilliputhur": 75, "Aruppukottai": 80,
    "Sivakasi": 80, "Periyakulam": 85, "Tiruppathur": 90, "Oddanchatram": 95, "Sattur": 95,
    "Cumbum": 90, "Pudukkottai": 100, "Rajapalayam": 100, "Paramakudi": 130, "Kovilpatti": 130,
    "Palani": 120, "Ramanathapuram": 115, "Kodaikanal": 115, "Thenkasi": 115, "Tenkasi": 115,
    "Courtallam": 120, "Sankarankoil": 125, "Kumily": 130, "Gudalur": 130, "Munnar": 140,
    "Idukki": 145, "Karur": 140, "Tiruchirappalli": 135, "Trichy": 135, "Tirunelveli": 155,
    "Thoothukudi": 160, "Tuticorin": 160, "Perambalur": 160, "Ariyalur": 175, "Thanjavur": 175,
    "Pollachi": 180, "Kottayam": 185, "Namakkal": 200, "Alappuzha": 200, "Alleppey": 200,
    "Thodupuzha": 205, "Padmanabhapuram": 205, "Coimbatore": 210, "Tirupur": 215, "Kumbakonam": 215,
    "Ernakulam": 220, "Kochi": 220, "Cochin": 220, "Salem": 220, "Nagercoil": 230, "Erode": 240,
    "Pala": 195, "Changanacherry": 195, "Pathanamthitta": 215, "Kollam": 250, "Quilon": 250,
    "Palakkad": 250, "Mayiladuthurai": 250, "Kanyakumari": 250, "Nilgiris": 265, "Ooty": 265,
    "Nagapattinam": 280, "Thrissur": 280, "Thiruvananthapuram": 290, "Trivandrum": 290,
    "Malappuram": 320, "Dharmapuri": 320, "Wayanad": 345, "Cuddalore": 365, "Kozhikode": 360,
    "Calicut": 360, "Villupuram": 370, "Mysuru": 370, "Mysore": 370, "Hosur": 385,
    "Pondicherry": 395, "Puducherry": 395, "Hassan": 395, "Vellore": 410, "Kanchipuram": 440,
    "Chengalpattu": 420, "Kannur": 430, "Bengaluru": 445, "Bangalore": 445, "Chennai": 455,
    "Tumkur": 475, "Mangaluru": 490, "Mangalore": 490, "Shimoga": 485, "Davangere": 510, "Davanagere": 510,
    "Udupi": 530, "Guntur": 650, "Vijayawada": 680, "Hyderabad": 770, "Warangal": 850,
    "Vizag": 970, "Bhubaneswar": 1650, "Cuttack": 1680, "Krishnagiri": 355, "Pune": 1250, "Mumbai": 1450,
    "Karaikudi": 95
};

function get_distance_from_madurai(city) {
    if (!city) return 0;
    if (MADURAI_DISTANCES[city] !== undefined) return MADURAI_DISTANCES[city];
    let lower = city.toLowerCase();
    for (let key in MADURAI_DISTANCES) {
        if (key.toLowerCase() === lower) return MADURAI_DISTANCES[key];
    }
    for (let key in MADURAI_DISTANCES) {
        if (lower.includes(key.toLowerCase()) || key.toLowerCase().includes(lower)) {
            return MADURAI_DISTANCES[key];
        }
    }
    return 0;
}

const PLANNING_ORDERS_API = 'production_entry.production_planning.clubbing_api.get_planning_orders_for_clubbing';
const DISTANCES_API = 'production_entry.production_planning.clubbing_api.get_distances_from_madurai';
window.JSB_CLUB_PICKER_VER = 'v20260918d';
// Always refresh helpers even if form.on already registered (old Client Script may open picker)
window.__JSB_CLUB_SHEET_JS__ = window.JSB_CLUB_PICKER_VER;

window.jsb_club_install_sequence_override = function (frm) {
	const apply = function (f) {
		window.jsb_club_apply_loading_sequence(f || frm);
	};
	try {
		const h = frappe.ui.form.handlers && frappe.ui.form.handlers['Clubbing Sheet'];
		if (h) {
			h.calculate_loading_sequence = [apply];
		}
	} catch (e) { /* ignore */ }
	if (frm && frm.script_manager && frm.script_manager.events) {
		frm.script_manager.events.calculate_loading_sequence = [apply];
	}
	if (frm && frm.cscript) {
		frm.cscript.calculate_loading_sequence = apply;
	}
	if (frm && frm.events) {
		frm.events.calculate_loading_sequence = apply;
	}
};

window.jsb_club_apply_loading_sequence = function (frm) {
	if (!frm || !frm.doc) return;
	if (typeof jsb_club_loading_sequence_locked === 'function' && jsb_club_loading_sequence_locked(frm)) {
		frm.refresh_field('items');
		return;
	}
	let items = frm.doc.items || [];
	if (!items.length) {
		frm.refresh_field('items');
		return;
	}

	let all_belt_cities = new Set();
	ROUTE_BELTS.forEach(belt => belt.forEach(c => all_belt_cities.add(c)));

	function get_city(item) {
		return (item.party_location || '').trim().toLowerCase();
	}

	let known_cities = new Set();
	items.forEach(item => {
		let city = get_city(item);
		if (!city) return;
		for (let bc of all_belt_cities) {
			if (city === bc || city.includes(bc) || bc.includes(city)) {
				known_cities.add(city);
				break;
			}
		}
	});

	let active_belt = null;
	for (let belt of ROUTE_BELTS) {
		let all_match = true;
		for (let city of known_cities) {
			let found = false;
			for (let bc of belt) {
				if (city === bc || city.includes(bc) || bc.includes(city)) { found = true; break; }
			}
			if (!found) { all_match = false; break; }
		}
		if (all_match && known_cities.size > 0) {
			active_belt = belt;
			break;
		}
	}
	if (!active_belt) {
		let max_matches = 0;
		for (let belt of ROUTE_BELTS) {
			let count = 0;
			for (let city of known_cities) {
				for (let bc of belt) {
					if (city === bc || city.includes(bc) || bc.includes(city)) { count++; break; }
				}
			}
			if (count > max_matches) { max_matches = count; active_belt = belt; }
		}
	}

	function get_sort_key(item) {
		let city = get_city(item);
		let dist = flt(item.distance_from_madurai) || get_distance_from_madurai(item.party_location);
		let beltIdx = 0;
		if (active_belt) {
			for (let idx = 0; idx < active_belt.length; idx++) {
				let bc = active_belt[idx];
				if (city === bc || city.includes(bc) || bc.includes(city)) {
					beltIdx = idx;
					break;
				}
			}
		}
		return [dist, beltIdx];
	}

	// Full Load truck type → every line is Full Load. Part Load never uses that label.
	if (frm.doc.load_type === 'Full Load') {
		items.forEach(item => { item.loading_sequence = 'Full Load'; });
		items.forEach((item, idx) => { item.idx = idx + 1; });
		frm.refresh_field('items');
		return;
	}

	function orderKey(item) {
		return String(
			item.party_code ||
			item.order_code ||
			item.custom_party_code ||
			item.custom_order_code ||
			item.sales_order ||
			''
		).trim().toUpperCase();
	}

	const groups = new Map();
	items.forEach((item, rowIdx) => {
		// Prefer order code; never collapse different blank rows into one "Full Load" group.
		const key = orderKey(item) || ('ROW:' + String(item.name || item.idx || rowIdx));
		const sk = get_sort_key(item);
		if (!groups.has(key)) {
			groups.set(key, { dist: sk[0], beltIdx: sk[1], firstIdx: rowIdx, items: [] });
		} else {
			const g = groups.get(key);
			if (sk[0] > g.dist || (sk[0] === g.dist && sk[1] > g.beltIdx)) {
				g.dist = sk[0];
				g.beltIdx = sk[1];
			}
		}
		groups.get(key).items.push(item);
	});

	const orderedKeys = Array.from(groups.keys()).sort((a, b) => {
		const ga = groups.get(a), gb = groups.get(b);
		if (ga.dist !== gb.dist) return gb.dist - ga.dist;
		if (ga.beltIdx !== gb.beltIdx) return gb.beltIdx - ga.beltIdx;
		return ga.firstIdx - gb.firstIdx;
	});

	const nOrd = orderedKeys.length;
	let labels = [];
	// Part Load: one slot per order code. Never label Part Load rows as "Full Load".
	if (nOrd <= 1) {
		labels = ['Inside'];
	} else if (nOrd === 2) {
		labels = ['Inside', 'Outside'];
	} else {
		labels = ['Inside'];
		for (let i = 0; i < nOrd - 2; i++) {
			labels.push('Center ' + Math.min(i + 1, 10));
		}
		labels.push('Outside');
	}

	const flat = [];
	orderedKeys.forEach((key, i) => {
		const seq = labels[i] || ('Center ' + Math.min(i, 10));
		groups.get(key).items.forEach(item => {
			item.loading_sequence = seq;
			flat.push(item);
		});
	});

	flat.forEach((item, idx) => { item.idx = idx + 1; });
	frm.doc.items = flat;
	frm.refresh_field('items');
};

/** Add selected Planning rows — direct call; never depends on frm.events.process_selections. */
window.jsb_club_add_selected_items = function (frm, selections, orders_cache) {
	const apply_picked = function (all) {
		let picked = (all || []).filter(o =>
			selections.includes(o.name) ||
			selections.includes(o.planning_table_row)
		);
		if (!picked.length) {
			frappe.msgprint(__('No matching Planning Table rows for selection.'));
			return;
		}

		let item_meta = frappe.get_meta('Clubbing Sheet Item') || {};
		let has_field = (fn) => !!(item_meta.fields || []).find(f => f.fieldname === fn);

		let rows = [];
		picked.forEach(so => {
			let row = frm.add_child('items');
			let rd = locals[row.doctype][row.name];
			rd.customer = so.customer;
			rd.customer_name = so.customer_name || so.customer;
			rd.sales_order = so.sales_order || '';
			rd.party_code = so.party_code || so.custom_party_code || so.order_code || '';
			rd.order_code = rd.party_code;
			rd.weight_kgs = flt(so.weight_kgs || so.total_qty);
			rd.no_of_rolls = flt(so.no_of_rolls);
			rd.party_location = so.city || '';
			rd.custom_planning_table_row = so.planning_table_row || so.name || '';
			rd.custom_planning_sheet = so.planning_sheet || '';
			// Despatch Customer defaults from Planning/SO customer — editable for emergency reallocation
			if (has_field('custom_despatch_customer')) {
				rd.custom_despatch_customer = so.customer || '';
			}
			if (has_field('despatch_customer')) {
				rd.despatch_customer = so.customer || '';
			}
			if (has_field('custom_despatch_sales_order')) {
				rd.custom_despatch_sales_order = '';
			}
			if (has_field('despatch_sales_order')) {
				rd.despatch_sales_order = '';
			}
			if (has_field('planning_table_row')) {
				rd.planning_table_row = so.planning_table_row || so.name || '';
			}
			if (has_field('planning_sheet')) {
				rd.planning_sheet = so.planning_sheet || '';
			}
			if (has_field('quality')) rd.quality = so.quality || '';
			if (has_field('color')) rd.color = so.color || '';
			if (has_field('gsm')) rd.gsm = so.gsm || 0;
			if (has_field('custom_quality')) rd.custom_quality = so.quality || '';
			if (has_field('custom_color')) rd.custom_color = so.color || '';
			if (has_field('custom_gsm')) rd.custom_gsm = so.gsm || 0;
			if (has_field('planned_date')) rd.planned_date = so.planned_date || '';
			if (has_field('custom_planned_date')) rd.custom_planned_date = so.planned_date || '';
			if (has_field('width_inch')) rd.width_inch = so.width_inch || so.inch || 0;
			if (has_field('custom_width_inch')) rd.custom_width_inch = so.width_inch || so.inch || 0;
			rows.push(rd);
		});
		frm.refresh_field('items');
		frm.doc.total_weight = rows.reduce((s, r) => s + flt(r.weight_kgs), 0);
		frm.refresh_field('total_weight');

		let cities = [...new Set(rows.map(r => r.party_location).filter(Boolean))];

		function applyAndSave(distanceMap) {
			rows.forEach(row => {
				let dist = distanceMap[row.party_location];
				row.distance_from_madurai = dist !== undefined ? dist : get_distance_from_madurai(row.party_location);
			});
			jsb_club_clear_loading_sequence_lock(frm);
			frm.trigger('recalculate_load_type');
			frm.refresh_field('items');
			setTimeout(function () { window.jsb_club_apply_loading_sequence(frm); }, 0);
			setTimeout(function () { window.jsb_club_apply_loading_sequence(frm); }, 150);
		}

		if (!cities.length) {
			jsb_club_clear_loading_sequence_lock(frm);
			frm.trigger('recalculate_load_type');
			return;
		}

		frappe.call({
			method: DISTANCES_API,
			args: { cities: cities },
			callback: (res) => applyAndSave(res.message || {}),
			error: () => {
				let fallback = {};
				cities.forEach(c => fallback[c] = get_distance_from_madurai(c));
				applyAndSave(fallback);
			}
		});
	};

	if (orders_cache && orders_cache.length) {
		apply_picked(orders_cache);
		return;
	}

	frappe.call({
		method: PLANNING_ORDERS_API,
		freeze: true,
		callback: function (r) {
			apply_picked(r.message || []);
		}
	});
};

window._jsb_club_process_selections = window.jsb_club_add_selected_items;

function jsb_club_wire_process_selections(frm) {
	const handler = function (frm2, selections) {
		const orders = window._jsb_club_picker_orders || null;
		window.jsb_club_add_selected_items(frm2 || frm, selections, orders);
	};
	if (!frm) return;
	if (frm.script_manager && frm.script_manager.events) {
		frm.script_manager.events.process_selections = [handler];
	}
	if (!frm.events) frm.events = {};
	frm.events.process_selections = handler;
	if (frm.cscript) {
		frm.cscript.process_selections = handler;
	}
	// Also keep a global pointer old Client Scripts can call
	window._jsb_club_frm = frm;
}

function jsb_club_has_loading_lock_field(frm) {
	return !!(frm && frm.meta && (frm.meta.fields || []).some(f => f.fieldname === 'custom_lock_loading_sequence'));
}

function jsb_club_set_loading_sequence_lock(frm) {
	if (!frm) return;
	if (jsb_club_has_loading_lock_field(frm)) {
		frm.set_value('custom_lock_loading_sequence', 1);
	} else {
		frm.doc.custom_lock_loading_sequence = 1;
	}
}

function jsb_club_clear_loading_sequence_lock(frm) {
	if (!frm) return;
	if (jsb_club_has_loading_lock_field(frm)) {
		frm.set_value('custom_lock_loading_sequence', 0);
	} else {
		frm.doc.custom_lock_loading_sequence = 0;
	}
}

function jsb_club_loading_sequence_locked(frm) {
	return cint(frm.doc.custom_lock_loading_sequence) === 1;
}

function jsb_club_bind_loading_sequence_lock_ui(frm) {
	try {
		frm.remove_custom_button(__('Recalculate Loading Sequence'));
	} catch (e) { /* ignore */ }
	if (!jsb_club_loading_sequence_locked(frm)) return;
	frm.add_custom_button(__('Recalculate Loading Sequence'), function () {
		jsb_club_clear_loading_sequence_lock(frm);
		frm.trigger('calculate_loading_sequence');
	});
}

function jsb_club_bind_picker_button(frm) {
	// Clear stuck lock from a previous failed open
	window._jsb_club_picker_lock = false;

	const openOnce = function (e) {
		// DOM click passes Event; frm.trigger passes frm — only stop DOM bubbling
		if (e && typeof e.preventDefault === 'function') {
			e.preventDefault();
			e.stopImmediatePropagation();
		}
		try {
			jsb_club_open_despatch_picker(frm);
		} catch (err) {
			console.error('[Clubbing] open picker failed', err);
			frappe.msgprint(__('Could not open order picker: {0}', [err.message || String(err)]));
		}
		return false;
	};
	jsb_club_wire_process_selections(frm);

	['get_planning_items', 'get_sales_orders', 'get_sales_orders_dialog'].forEach(function (fn) {
		if (frm.fields_dict && frm.fields_dict[fn]) {
			frm.set_df_property(fn, 'hidden', 0);
		}
	});
	try {
		frm.remove_custom_button(__('Get Planning Items'));
	} catch (e) { /* ignore */ }
	try {
		frm.remove_custom_button(__('Get Sales Orders'));
	} catch (e3) { /* ignore */ }
	frm.add_custom_button(__('Get Planning Items'), openOnce);
	frm.add_custom_button(__('Get Sales Orders'), openOnce);

	if (frm.script_manager && frm.script_manager.events) {
		frm.script_manager.events.get_planning_items = [openOnce];
		frm.script_manager.events.get_sales_orders = [openOnce];
		frm.script_manager.events.get_sales_orders_dialog = [openOnce];
		frm.script_manager.events.process_selections = [function (f2, sels) {
			window.jsb_club_add_selected_items(f2 || frm, sels, window._jsb_club_picker_orders || null);
		}];
	}
	if (!frm.events) frm.events = {};
	frm.events.get_planning_items = openOnce;
	frm.events.get_sales_orders = openOnce;
	frm.events.get_sales_orders_dialog = openOnce;
	if (frm.cscript) {
		frm.cscript.get_planning_items = openOnce;
		frm.cscript.get_sales_orders = openOnce;
		frm.cscript.get_sales_orders_dialog = openOnce;
	}

	['get_planning_items', 'get_sales_orders', 'get_sales_orders_dialog'].forEach(function (fname) {
		try {
			const fld = frm.get_field(fname);
			if (!fld || !fld.$wrapper) return;
			const $btn = fld.$wrapper.find('button, .btn').first();
			if (!$btn.length) return;
			$btn.off('click.jsbclub').on('click.jsbclub', openOnce);
		} catch (e2) { /* ignore */ }
	});
}

function jsb_club_open_despatch_picker(frm) {
	if (!frm) return;

	// Hard single-open: if dialog already up, focus it — never open a second
	if (window._jsb_club_dialog && window._jsb_club_dialog.$wrapper && window._jsb_club_dialog.$wrapper.is(':visible')) {
		return;
	}
	if (window._jsb_club_picker_lock) return;
	const now = Date.now();
	if (window._jsb_club_picker_last && (now - window._jsb_club_picker_last) < 1200) {
		return;
	}
	window._jsb_club_picker_last = now;
	window._jsb_club_picker_lock = true;
	// Safety unlock if modal close handler doesn't fire (network lag, etc.)
	setTimeout(function () {
		window._jsb_club_picker_lock = false;
	}, 15000);

	if (typeof window._jsb_club_picker_impl !== 'function') {
		frappe.msgprint(__(
			'Clubbing picker missing. Paste latest PASTE_clubbing_client_script.js into Client Script and Save.'
		));
		window._jsb_club_picker_lock = false;
		return;
	}
	window._jsb_club_picker_impl(frm);
}

function jsb_club_hide_view_rolls(frm) {
	const grid = frm.get_field('items') && frm.get_field('items').grid;
	if (!grid) return;
	if (typeof grid.toggle_display === 'function') {
		try {
			grid.toggle_display('view_rolls', false);
		} catch (e) { /* ignore */ }
	}
	if (grid.set_column_disp) {
		try {
			grid.set_column_disp('view_rolls', false);
		} catch (e2) { /* ignore */ }
	}
}

frappe.ui.form.on('Clubbing Sheet', {
    refresh: function (frm) {
        jsb_club_bind_picker_button(frm);
        jsb_club_install_sequence_override(frm);
        // Rebind several times so we win over any old Enabled Client Script on the site
        [200, 500, 1000, 2000].forEach(function (ms) {
            setTimeout(function () {
                if (cur_frm && cur_frm === frm) {
                    jsb_club_bind_picker_button(frm);
                    jsb_club_install_sequence_override(frm);
                }
            }, ms);
        });

        if (!frm.doc.__islocal && frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Submit'), function () {
                frm.trigger('submit_with_trip_id');
            }, __('Actions'));
        }

        frm.trigger('show_load_type_indicator');
        frm.trigger('toggle_loading_sequence_visibility');
        frm.trigger('set_vehicle_no_options');
        jsb_club_bind_loading_sequence_lock_ui(frm);
        jsb_club_hide_view_rolls(frm);
    },

    vehicle_feet: function (frm) {
        frm.trigger('set_vehicle_no_options');
        frm.set_value('vehicle_no', '');
    },

    set_vehicle_no_options: function (frm) {
        if (!frm.doc.vehicle_feet) {
            frm.set_df_property('vehicle_no', 'options', []);
            return;
        }
        const VEHICLE_MAP = {
            "20": ["TN 64 T 2207", "TN 64 U 0093", "TN 64 V 2054", "TN 64 AE 1904", "TN 64 AE 1976"],
            "21": ["TN 64 V 3522", "TN 64 V 4066", "TN 64 V 4258"],
            "22": ["TN 64 V 3016", "TN 64 V 3619", "TN 64 V 3083", "TN 64 V 8409", "TN 64 V 8437"],
            "24": ["TN 64 W 9289", "TN 64 W 9767", "TN 64 X 4926", "TN 64 X 4944", "TN 64 X 6939", "TN 64 Y 1944", "TN 64 Y 1982", "TN 64 Y 1993", "TN 64 Y 8719", "TN 64 Y 8731", "TN 64 Y 8782", "TN 64 Z 1720", "TN 64 Z 1748"],
            "32": ["TN 64 V 8852", "TN 64 W 8825"],
            "Bolero": ["TN 64 W 6736"]
        };
        let feet_str = frm.doc.vehicle_feet.toString();
        if (VEHICLE_MAP[feet_str]) {
            frm.set_df_property('vehicle_no', 'options', [""].concat(VEHICLE_MAP[feet_str]));
            return;
        }
        let match = feet_str.match(/\d+/);
        if (match && VEHICLE_MAP[match[0]]) {
            frm.set_df_property('vehicle_no', 'options', [""].concat(VEHICLE_MAP[match[0]]));
        } else {
            frm.set_df_property('vehicle_no', 'options', []);
        }
    },

    submit_with_trip_id: function (frm) {
        if (!frm.doc.trip_id) {
            frappe.prompt(
                [{ fieldname: 'trip_id', fieldtype: 'Data', label: 'Trip ID', reqd: 1 }],
                (values) => {
                    frappe.model.set_value(frm.doctype, frm.docname, 'trip_id', values.trip_id);
                    frm.save().then(() => {
                        frappe.ui.form.save(frm, 'Submit');
                    });
                },
                __('Enter Trip ID to Submit'),
                __('Submit')
            );
        } else {
            frappe.confirm(
                __('Submit this Clubbing Sheet with Trip ID: <b>') + frm.doc.trip_id + '</b>?',
                () => frappe.ui.form.save(frm, 'Submit')
            );
        }
    },

    get_sales_orders: function (frm) {
        jsb_club_open_despatch_picker(frm);
    },

    get_planning_items: function (frm) {
        jsb_club_open_despatch_picker(frm);
    },

    get_sales_orders_dialog: function (frm) {
        jsb_club_open_despatch_picker(frm);
    },

    show_load_type_indicator: function (frm) {
        frm.page.wrapper.find('.form-message.blue, .form-message.orange, .form-message.red').remove();

        let customer_weights = {};
        let selected_cities = new Set();
        (frm.doc.items || []).forEach(i => {
            if (i.customer) {
                customer_weights[i.customer] = (customer_weights[i.customer] || 0) + flt(i.weight_kgs);
            }
            if (i.party_location) {
                selected_cities.add(i.party_location.trim().toLowerCase());
            }
        });

        let customers = Object.keys(customer_weights);
        let full_load_customers = customers.filter(c => customer_weights[c] >= 5000);

        let is_valid = jsb_club_cities_on_one_belt(selected_cities);

        if (!is_valid) {
            frm.set_intro(__("ROUTE CONFLICT — cities do not fall on one forward route/belt (save/submit still allowed)."), "orange");
        } else if (full_load_customers.length > 0 && customers.length > 1) {
            frm.set_intro(__("FULL LOAD WARNING — Customer {0} has {1} kgs (>= 5000). Normally a dedicated Full Load — save/submit still allowed.", [full_load_customers[0], customer_weights[full_load_customers[0]]]), "orange");
        } else if (frm.doc.load_type === "Full Load") {
            frm.set_intro(__("Full Load — dedicated vehicle."), "blue");
        } else if (frm.doc.load_type === "Part Load") {
            frm.set_intro(customers.length > 1 ? __("Part Load — multiple orders clubbed.") : __("Part Load — needs clubbing."), "orange");
        }
    },

    load_type: function (frm) {
        jsb_club_clear_loading_sequence_lock(frm);
        frm.trigger('show_load_type_indicator');
        frm.trigger('toggle_loading_sequence_visibility');
        frm.trigger('calculate_loading_sequence');
    },

    toggle_loading_sequence_visibility: function (frm) {
        // Always show when there are items (was hidden when load_type cleared on false route conflict)
        let show = (frm.doc.items || []).length > 0;
        let grid = frm.get_field('items') && frm.get_field('items').grid;
        if (grid && grid.set_column_disp) {
            grid.set_column_disp('loading_sequence', show);
        }
    },

    recalculate_load_type: function (frm) {
        let customer_weights = {};
        (frm.doc.items || []).forEach(i => {
            if (i.customer) {
                customer_weights[i.customer] = (customer_weights[i.customer] || 0) + flt(i.weight_kgs);
            }
        });

        let customers = Object.keys(customer_weights);
        let full_load_customers = customers.filter(c => customer_weights[c] >= 5000);

        // Soft warn when ≥5000 kg customer is mixed with others — still Part Load so save works.
        let nextType = '';
        if (full_load_customers.length && customers.length > 1) {
            nextType = 'Part Load';
        } else if (full_load_customers.length) {
            nextType = 'Full Load';
        } else if (customers.length >= 1) {
            nextType = 'Part Load';
        }

        const applySeq = function () {
            frm.trigger('show_load_type_indicator');
            frm.trigger('toggle_loading_sequence_visibility');
            window.jsb_club_apply_loading_sequence(frm);
        };

        // set_value is async — apply sequence only after load_type is committed
        // so Part Load never gets stuck with Full Load sequence labels.
        if ((frm.doc.load_type || '') === nextType) {
            applySeq();
        } else {
            frm.set_value('load_type', nextType).then(applySeq);
        }
    },

    jsb_pick_despatch_planning_rows: function (frm) {
        window._jsb_club_picker_impl(frm);
    },
});

// Actual dialog — also exposed so Get Sales Orders works even if trigger chain is empty
window._jsb_club_picker_impl = function (frm) {
        // Never stack two dialogs (app JS + Client Script both calling open)
        if (window._jsb_club_dialog && window._jsb_club_dialog.$wrapper && window._jsb_club_dialog.$wrapper.is(':visible')) {
            return;
        }
        try {
            $('.modal.jsb-club-picker, .jsb-club-picker').closest('.modal').modal('hide');
        } catch (e0) { /* ignore */ }

        let d = new frappe.ui.Dialog({
            title: __('Select Planning Despatch Rows') + ' · ' + (window.JSB_CLUB_PICKER_VER || ''),
            size: 'extra-large',
            fields: [
                { fieldtype: 'Section Break', label: __('Filters') },
                { fieldtype: 'Date', fieldname: 'planned_date', label: __('Planned Date'), reqd: 0 },
                { fieldtype: 'Column Break' },
                { fieldtype: 'Data', fieldname: 'search_order', label: __('Order ID'), onchange: () => refresh_list() },
                { fieldtype: 'Section Break' },
                { fieldtype: 'Data', fieldname: 'search_customer', label: __('Customer'), onchange: () => refresh_list() },
                { fieldtype: 'Column Break' },
                { fieldtype: 'Data', fieldname: 'search_city', label: __('City / Route'), onchange: () => refresh_list() },
                { fieldtype: 'Section Break' },
                { fieldtype: 'Data', fieldname: 'search_party', label: __('Party Code'), onchange: () => refresh_list() },
                { fieldtype: 'Column Break' },
                { fieldtype: 'Button', fieldname: 'btn_reload', label: __('Reload') },
                { fieldtype: 'Section Break' },
                { fieldtype: 'HTML', fieldname: 'list_area' }
            ],
            primary_action_label: __('Get Items'),
            primary_action(values) {
                let selected = Array.from(selected_orders);
                if (selected.length === 0) {
                    frappe.msgprint(__('Please select at least one planning row'));
                    return;
                }
                d.hide();
                window._jsb_club_dialog = null;
                window._jsb_club_picker_orders = orders;
                try {
                    window.jsb_club_add_selected_items(frm, selected, orders);
                } catch (err) {
                    console.error('[Clubbing] Get Items failed', err);
                    frappe.msgprint(__('Could not add items: {0}', [err.message || String(err)]));
                }
            }
        });

        window._jsb_club_dialog = d;
        d.$wrapper.addClass('jsb-club-picker');
        if (!document.getElementById('jsb-club-picker-css')) {
            let style = document.createElement('style');
            style.id = 'jsb-club-picker-css';
            style.textContent = `
                .jsb-club-picker .modal-content { border-radius: 14px; border: 1px solid #cbd5e1; box-shadow: 0 18px 50px rgba(15,23,42,.18); overflow: hidden; }
                .jsb-club-picker .modal-header { background: linear-gradient(180deg,#0f172a,#1e293b); color: #fff; border-bottom: none; padding: 14px 18px; }
                .jsb-club-picker .modal-title { color: #fff !important; font-weight: 700; letter-spacing: .02em; }
                .jsb-club-picker .modal-header .btn-modal-close { color: #fff; opacity: .85; }
                .jsb-club-picker .modal-body { background: #f8fafc; padding: 14px 16px 8px; }
                .jsb-club-picker .form-control { border: 1.5px solid #94a3b8; border-radius: 10px; background: #fff; min-height: 34px; }
                .jsb-club-picker .form-control:focus { border-color: #0284c7; box-shadow: 0 0 0 3px rgba(2,132,199,.18); }
                .jsb-club-picker .btn[data-fieldname="btn_reload"] { border-radius: 10px !important; border: 1.5px solid #0ea5e9 !important; background: #e0f2fe !important; color: #0369a1 !important; font-weight: 700 !important; padding: 6px 14px !important; }
                .jsb-club-picker .modal-footer .btn-primary { border-radius: 10px !important; border: none !important; background: #0f172a !important; font-weight: 700 !important; padding: 8px 18px !important; }
                .jsb-club-picker-wrap { max-height: 440px; overflow: auto; border: 1.5px solid #94a3b8; border-radius: 12px; background: #fff; }
                .jsb-club-picker-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 12px; }
                .jsb-club-picker-table thead th { position: sticky; top: 0; z-index: 1; background: #0f172a; color: #e2e8f0; font-size: 10px; letter-spacing: .04em; text-transform: uppercase; padding: 10px 8px; border-bottom: 1px solid #334155; white-space: nowrap; }
                .jsb-club-picker-table tbody td { padding: 9px 8px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }
                .jsb-club-picker-table tbody tr:hover { background: #f0f9ff; }
                .jsb-club-picker-table .jsb-pill { display: inline-block; padding: 2px 8px; border-radius: 999px; background: #e0f2fe; color: #0369a1; font-weight: 700; font-size: 11px; }
                .jsb-club-picker-table .jsb-attr { font-weight: 700; color: #0f172a; }
                .jsb-club-picker-hint { margin: 0 0 10px; color: #475569; font-size: 12px; }
            `;
            document.head.appendChild(style);
        }

        let orders = [];
        let selected_orders = new Set();

        d.$wrapper.on('change', '.so-checkbox', function () {
            let name = $(this).data('name');
            if (!name) return;
            if ($(this).prop('checked')) selected_orders.add(name);
            else selected_orders.delete(name);
        });

        d.$wrapper.on('change', '.so-select-all', function () {
            let is_checked = $(this).prop('checked');
            d.$wrapper.find('.so-checkbox').each(function () {
                $(this).prop('checked', is_checked);
                let name = $(this).data('name');
                if (!name) return;
                if (is_checked) selected_orders.add(name);
                else selected_orders.delete(name);
            });
        });

        let refresh_list = () => {
            let s_order = (d.get_value('search_order') || '').toLowerCase();
            let s_cust = (d.get_value('search_customer') || '').toLowerCase();
            let s_city = (d.get_value('search_city') || '').toLowerCase();
            let s_party = (d.get_value('search_party') || '').toLowerCase();

            let filtered = orders.filter(o =>
                (!s_order || (o.name || '').toLowerCase().includes(s_order) || (o.party_code || '').toLowerCase().includes(s_order) || (o.sales_order || '').toLowerCase().includes(s_order) || (o.planning_sheet || '').toLowerCase().includes(s_order) || (o.item_code || '').toLowerCase().includes(s_order) || (o.quality || '').toLowerCase().includes(s_order) || (o.color || '').toLowerCase().includes(s_order)) &&
                (!s_cust || (o.customer || '').toLowerCase().includes(s_cust) || (o.customer_name || '').toLowerCase().includes(s_cust)) &&
                (!s_city || (o.city || '').toLowerCase().includes(s_city)) &&
                (!s_party || (o.custom_party_code || o.party_code || '').toLowerCase().includes(s_party))
            );

            let inchVal = (o) => {
                let v = flt(o.width_inch || o.inch || 0);
                return v > 0 ? v : '—';
            };

            let html = `
                <p class="jsb-club-picker-hint">${__('Quality · Color · GSM · Width(Inch) — tick the exact Planning lines.')} <b>${window.JSB_CLUB_PICKER_VER || ''}</b></p>
                <div class="jsb-club-picker-wrap">
                <table class="jsb-club-picker-table">
                    <thead>
                        <tr>
                            <th style="width: 36px; text-align: center;"><input type="checkbox" class="so-select-all"></th>
                            <th>${__('Order')}</th>
                            <th>${__('Item')}</th>
                            <th>${__('Quality')}</th>
                            <th>${__('Color')}</th>
                            <th>${__('GSM')}</th>
                            <th>${__('Width (Inch)')}</th>
                            <th>${__('Planned')}</th>
                            <th>${__('City')}</th>
                            <th class="text-right">${__('Wt / Rolls')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${filtered.length === 0 ? `<tr><td colspan="10" class="text-center text-muted" style="padding:24px;">${__('No Despatch planning rows')}</td></tr>` :
                filtered.map(o => `
                            <tr>
                                <td style="text-align: center;"><input type="checkbox" class="so-checkbox" data-name="${frappe.utils.escape_html(o.name)}" ${selected_orders.has(o.name) ? 'checked' : ''}></td>
                                <td><b>${frappe.utils.escape_html(o.party_code || o.sales_order || o.name)}</b><br><span class="text-muted small">${frappe.utils.escape_html(o.sales_order || '')}</span><br><span class="text-muted small">${frappe.utils.escape_html(o.planning_sheet || '')}</span></td>
                                <td><span class="text-muted small">${frappe.utils.escape_html(o.item_code || '')}</span></td>
                                <td class="jsb-attr">${frappe.utils.escape_html(o.quality || '—')}</td>
                                <td class="jsb-attr">${frappe.utils.escape_html(o.color || '—')}</td>
                                <td class="jsb-attr">${o.gsm || '—'}</td>
                                <td class="jsb-attr">${inchVal(o)}</td>
                                <td>${frappe.utils.escape_html(o.planned_date || '')}</td>
                                <td><span class="jsb-pill">${frappe.utils.escape_html(o.city || '—')}</span></td>
                                <td class="text-right">${o.total_qty || 0}<br><span class="text-muted small">${o.no_of_rolls || 0} rolls</span></td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
                </div>`;
            d.get_field('list_area').$wrapper.html(html);
        };

        let load_orders = () => {
            selected_orders.clear();
            frappe.call({
                method: PLANNING_ORDERS_API,
                args: {
                    planned_date: d.get_value('planned_date') || '',
                    party_code: d.get_value('search_party') || d.get_value('search_order') || '',
                    customer: d.get_value('search_customer') || '',
                    city: d.get_value('search_city') || ''
                },
                freeze: true,
                freeze_message: __('Loading Despatch planning rows...'),
                callback: (r) => {
                    orders = r.message || [];
                    window._jsb_club_picker_orders = orders;
                    refresh_list();
                }
            });
        };

        d.fields_dict.planned_date.$input.on('change', () => load_orders());
        if (d.fields_dict.btn_reload && d.fields_dict.btn_reload.$input) {
            d.fields_dict.btn_reload.$input.on('click', () => load_orders());
        }
        load_orders();
        d.show();
        d.$wrapper.on('hidden.bs.modal', function () {
            window._jsb_club_dialog = null;
            window._jsb_club_picker_lock = false;
            frm._jsb_club_picker_open = false;
        });
};

frappe.ui.form.on('Clubbing Sheet', {
    process_selections: function (frm, selections, planned_date) {
        window.jsb_club_add_selected_items(frm, selections, window._jsb_club_picker_orders || null);
    },

    total_weight: function (frm) {
        frm.trigger('show_load_type_indicator');
    },

    calculate_loading_sequence: function (frm) {
        window.jsb_club_apply_loading_sequence(frm);
    }
});

frappe.ui.form.on('Clubbing Sheet Item', {
    form_render(frm, cdt, cdn) {
        let grid = frm.get_field('items') && frm.get_field('items').grid;
        if (!grid) return;
        grid.get_field('custom_despatch_sales_order') &&
            frm.set_query('custom_despatch_sales_order', 'items', function (doc, cdt2, cdn2) {
                let row = locals[cdt2][cdn2];
                let cust = row.custom_despatch_customer || row.despatch_customer;
                if (!cust) return {};
                return { filters: { customer: cust, docstatus: 1 } };
            });
    },

    custom_despatch_customer(frm, cdt, cdn) {
        // Clear override SO when despatch customer changes
        frappe.model.set_value(cdt, cdn, 'custom_despatch_sales_order', '');
    },

    loading_sequence(frm, cdt, cdn) {
        jsb_club_set_loading_sequence_lock(frm);
        jsb_club_bind_loading_sequence_lock_ui(frm);
    },

    items_remove: function (frm) {
        frm.trigger('recalculate_load_type');
    }
});
