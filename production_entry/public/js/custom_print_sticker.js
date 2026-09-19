// ===== CUSTOM PRINT STICKER FLOW =====

// Overwrite the print button behavior
frappe.generate_sticker_flow = function (row_name, frm) {
    var f = frm || cur_frm;
    var row = (locals['Shaft Production Run Item'] || {})[row_name] || (f.doc.items || []).find(function (r) { return r.name === row_name; }) || (f.doc.roll_wise_entry || []).find(function (r) { return r.name === row_name; });
    if (!row) return;
    // Use the SPR child row — do not call frappe.client.get_value (operators lack Item / child DocPerm).
    trigger_print_with_details(row_name, String(row.item_name || "").trim(), f);
};

/** Scandinavian 6x4: skip "Select Fields to Print" and open label directly. */
function is_scandinavian_skip_custom_dialog(row, frm) {
    var f = frm || cur_frm;
    var lt = String(((f.doc || {}).custom_label || "Default")).toLowerCase();
    var customer_id = String(
        row.custom_customer ||
        row.custom_custom_customer ||
        row.customer ||
        ((f.doc || {}).custom_customer) ||
        ((f.doc || {}).customer) ||
        ""
    ).trim();
    return (
        lt.includes("customer 4x6") ||
        lt.includes("scandinavian") ||
        customer_id === "EXP-0071"
    );
}

var _label_template_spec_cache = {};

function resolve_label_template_spec(raw_label, doc, callback) {
    if (doc && doc._label_template && doc._label_template.from_template) {
        callback(doc._label_template);
        return;
    }
    var key = String(raw_label || "").trim();
    if (!key) {
        callback(null);
        return;
    }
    if (_label_template_spec_cache[key]) {
        callback(_label_template_spec_cache[key]);
        return;
    }
    frappe.call({
        method: "production_entry.production_planning.doctype.shaft_production_run.shaft_production_run.get_label_template_print_spec",
        args: { name: key },
        callback: function (r) {
            var spec = r && r.message && r.message.from_template ? r.message : null;
            if (spec) {
                _label_template_spec_cache[key] = spec;
                if (doc) {
                    doc._label_template = spec;
                }
            }
            callback(spec);
        },
        error: function () {
            callback(null);
        }
    });
}

function trigger_print_with_details(row_name, item_name, frm) {
    var doc = frm.doc;
    var raw_label = (doc && doc.custom_label) || "Default";
    resolve_label_template_spec(raw_label, doc, function (spec) {
        continue_print_with_details(row_name, item_name, frm, spec);
    });
}

function continue_print_with_details(row_name, item_name, frm, spec) {
    var doc = frm.doc;
    var raw_label = ((spec && spec.name) || (doc && doc.custom_label) || "Default");
    var label_type = String(raw_label).trim().toLowerCase();
    var row = (locals['Shaft Production Run Item'] || {})[row_name] || (doc.items || []).find(function (r) { return r.name === row_name; }) || (doc.roll_wise_entry || []).find(function (r) { return r.name === row_name; });
    if (!row) return;

    var details = extract_details_enhanced(item_name, row.item_code);
    var final_gsm = row.gsm || details.gsm || "";
    var final_color = row.color || details.color || "";
    var final_quality = row.quality || details.quality || "";
    var template_fields = spec && spec.fields ? spec.fields : null;
    var is_template = !!(spec && spec.from_template);

    if (label_type.includes("reliance") || label_type.includes("relience")) {
        flow_reliance_cm(row_name, final_gsm, final_color, final_quality, frm);
    } else if (!is_template && label_type.includes("custom")) {
        var w_custom = row.width_inch || details.width_inch || "0";
        if (is_scandinavian_skip_custom_dialog(row, frm)) {
            frappe.run_print_logic(row_name, w_custom + " Inches", final_gsm, final_color, final_quality, frm);
        } else {
            flow_customized_label(row_name, final_gsm, final_color, final_quality, frm, w_custom);
        }
    } else {
        var w = row.width_inch || details.width_inch || "0";
        frappe.run_print_logic(row_name, w + " Inches", final_gsm, final_color, final_quality, frm, template_fields);
    }
}

var QUALITY_MASTER = {
    "100": "PREMIUM", "101": "PLATINUM", "102": "SUPER PLATINUM",
    "103": "GOLD", "104": "SILVER", "105": "BRONZE",
    "106": "CLASSIC", "107": "SUPER CLASSIC", "108": "LIFE STYLE",
    "109": "ECO SPECIAL", "110": "ECO GREEN", "111": "SUPER ECO",
    "112": "ULTRA", "113": "DELUXE", "114": "UV"
};

function extract_details_enhanced(name, code) {
    var res = { gsm: null, color: null, width_inch: null, quality: null };
    var name_upper = (name || "").toUpperCase();

    if (code && code.length === 16 && /^\d+$/.test(code)) {
        var qual_code = code.substring(3, 6);
        if (QUALITY_MASTER[qual_code]) res.quality = QUALITY_MASTER[qual_code];
        var code_gsm = parseInt(code.substring(9, 12));
        if (code_gsm > 0) res.gsm = String(code_gsm);
        var code_width_mm = parseFloat(code.substring(12, 16));
        if (code_width_mm > 0) res.width_inch = Math.round(code_width_mm / 25.4);
        if (res.quality && name) {
            var qual_pos = name_upper.indexOf(res.quality.toUpperCase());
            if (qual_pos !== -1) {
                var after_qual = name.substring(qual_pos + res.quality.length).trim();
                after_qual = after_qual.replace(/\s*\d+\s*GSM.*/i, "").trim();
                if (after_qual) res.color = after_qual;
            }
        }
    } else if (name) {
        var known_qualities = ["SUPER PLATINUM", "SUPER CLASSIC", "LIFE STYLE", "ECO SPECIAL", "ECO GREEN", "SUPER ECO", "DELUXE", "PREMIUM", "PLATINUM", "GOLD", "SILVER", "BRONZE", "CLASSIC", "ULTRA", "UV"];
        known_qualities.sort(function (a, b) { return b.length - a.length; });
        for (var i = 0; i < known_qualities.length; i++) {
            var q = known_qualities[i];
            if (new RegExp('\\b' + q + '\\b', 'i').test(name_upper)) { res.quality = q; break; }
        }
        if (res.quality) {
            var qp = name_upper.indexOf(res.quality.toUpperCase());
            if (qp !== -1) {
                var aq = name.substring(qp + res.quality.length).trim();
                aq = aq.split(/\s*\d+\s*GSM/i)[0].trim();
                aq = aq.replace(/^[\s,:-]+|[\s,:-]+$/g, "");
                if (aq) res.color = aq;
            }
        }
        var mg = name.match(/(\d+)\s*GSM/i);
        if (mg) res.gsm = mg[1];
        var mw = name.match(/(\d+(\.\d+)?)\s*("|inch|in|'')/i);
        if (mw) res.width_inch = mw[1];
    }
    return res;
}

function flow_reliance_cm(row_name, gsm, color, quality, frm) {
    var f = frm || cur_frm;
    var row = (locals['Shaft Production Run Item'] || {})[row_name] || (f.doc.items || []).find(function(r) { return r.name === row_name; }) || (f.doc.roll_wise_entry || []).find(function(r) { return r.name === row_name; });
    var item_code = row ? (row.item_code || "") : "";
    var width_mm = (item_code.length >= 4) ? parseFloat(item_code.slice(-4)) : 0;
    var width_cm = (width_mm > 0) ? (width_mm / 10) : 0;

    frappe.prompt([{
        label: 'Verify Width (CM) for ' + (item_code || 'this row'),
        fieldname: 'width_cm',
        fieldtype: 'Float',
        default: width_cm,
        reqd: 1
    }], function (values) {
        frappe.run_print_logic(row_name, values.width_cm + " CM", gsm, color, quality, frm);
    }, 'Confirm Reliance Size (' + row.roll_no + ')', 'Preview Label');
}

function flow_customized_label(row_name, gsm, color, quality, frm, width_inch) {
    var dialog = new frappe.ui.Dialog({
        title: 'Select Fields to Print',
        fields: [
            { fieldtype: 'Section Break', label: 'Header Fields' },
            { fieldtype: 'Check', fieldname: 'show_company', label: 'Company Name', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_email', label: 'Company Email', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_customer', label: 'Customer Name', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_quality', label: 'Quality', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_order_code', label: 'Order Code', default: 1 },
            { fieldtype: 'Section Break', label: 'Body Fields' },
            { fieldtype: 'Check', fieldname: 'show_gsm', label: 'GSM', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_width', label: 'Width', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_length', label: 'Length', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_gw', label: 'Gross Weight', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_nw', label: 'Net Weight', default: 1 },
            { fieldtype: 'Section Break', label: 'Bottom Fields' },
            { fieldtype: 'Check', fieldname: 'show_batch', label: 'Batch No (Bottom)', default: 1 },
            { fieldtype: 'Check', fieldname: 'show_barcode', label: 'Barcode', default: 1 }
        ],
        primary_action_label: 'Print Label',
        primary_action: function(values) {
            dialog.hide();
            frappe.run_print_logic(row_name, width_inch + " Inches", gsm, color, quality, frm, values);
        }
    });
    dialog.show();
}

frappe.run_print_logic = function (row_name, final_width_display, final_gsm, final_color, final_quality, frm, custom_fields) {
    var f = frm || cur_frm;
    var row = (locals['Shaft Production Run Item'] || {})[row_name] || (f.doc.items || []).find(function(r) { return r.name === row_name; }) || (f.doc.roll_wise_entry || []).find(function(r) { return r.name === row_name; });
    if (!row) return;
    var normalized_custom_fields = normalize_custom_fields(custom_fields);
    var label_type = String(((f.doc || {}).custom_label || "Default")).toLowerCase();
    var customer_id_for_4x6 = String(
        row.custom_customer ||
        row.custom_custom_customer ||
        row.customer ||
        ((f.doc || {}).custom_customer) ||
        ((f.doc || {}).customer) ||
        ""
    ).trim();
    var is_customer_4x6 = (
        label_type.includes("customer 4x6") ||
        label_type.includes("scandinavian") ||
        customer_id_for_4x6 === "EXP-0071"
    );

    var proceed_run = function(customer_name) {
        var d = {
            company: "JAYASHREE SPUN BOND",
            quality: final_quality || "NON WOVEN FABRIC",
            gsm: final_gsm,
            color: final_color,
            width_val: final_width_display,
            item_code: row.item_code || "",
            barcode_data: row.batch_no || "",
            length: row.custom_produced_length_mtrs || row.produced_length_mtrs || "0",
            gw: (flt(row.gross_weight) || flt(row.net_weight)).toFixed(2),
            nw: flt(row.net_weight).toFixed(2),
            batch_no: row.batch_no || "",
            roll_no: row.roll_no || "",
            party_code: row.party_code || "",
            customer_name: customer_name || ""
        };

        if (is_customer_4x6) {
            build_customer_4x6_data(row, d, function (label_data) {
                var html4x6 = get_customer_4x6_format(label_data);
                var pw = window.open('', '_blank', 'width=920,height=520');
                if (pw) {
                    pw.document.write(html4x6);
                    pw.document.close();
                }
            });
            return;
        }

        var htmlContent = get_grid_format(d, label_type, normalized_custom_fields);
        var printWindow = window.open('', '_blank', 'height=650,width=500');
        if (printWindow) {
            printWindow.document.write(htmlContent);
            printWindow.document.close();
        }
    };

    var custom_customer_id = String(
        row.custom_customer ||
        row.custom_custom_customer ||
        row.customer ||
        ((f.doc || {}).custom_customer) ||
        ((f.doc || {}).customer) ||
        ""
    ).trim();
    if (custom_customer_id) {
        fetch_customer_display_name(custom_customer_id, function (name) {
            proceed_run(name);
        });
    } else if (row.party_code) {
        frappe.call({
            method: 'frappe.client.get_value',
            args: {
                doctype: 'Customer',
                filters: { name: String(row.party_code).trim() },
                fieldname: 'customer_name'
            },
            callback: function(r) {
                if (r && r.message && r.message.customer_name) {
                    proceed_run(r.message.customer_name);
                } else {
                    frappe.call({
                        method: 'frappe.client.get_value',
                        args: {
                            doctype: 'Sales Order',
                            filters: { name: row.party_code },
                            fieldname: ['customer_name', 'customer']
                        },
                        callback: function(r2) {
                            if (r2 && r2.message && r2.message.customer_name) {
                                proceed_run(r2.message.customer_name);
                            } else if (r2 && r2.message && r2.message.customer) {
                                fetch_customer_display_name(r2.message.customer, function (name) {
                                    proceed_run(name);
                                });
                            } else {
                                proceed_run(String(row.party_code || "").trim());
                            }
                        }
                    });
                }
            }
        });
    } else {
        proceed_run("");
    }
};

function fetch_customer_display_name(customer_id, callback) {
    customer_id = String(customer_id || "").trim();
    if (!customer_id) {
        callback("");
        return;
    }
    function finish(display) {
        callback(String(display || "").trim() || customer_id);
    }
    frappe.call({
        method: 'frappe.client.get_value',
        args: {
            doctype: 'Customer',
            filters: { name: customer_id },
            fieldname: ['customer_name']
        },
        callback: function (r) {
            var msg = (r && r.message) || {};
            var nm = String(msg.customer_name || "").trim();
            if (nm) {
                finish(nm);
                return;
            }
            frappe.call({
                method: 'frappe.client.get',
                args: { doctype: 'Customer', name: customer_id },
                callback: function (r2) {
                    var doc = (r2 && r2.message) || {};
                    var from_doc = String(doc.customer_name || "").trim();
                    finish(from_doc || customer_id);
                },
                error: function () {
                    finish(customer_id);
                }
            });
        },
        error: function () {
            finish(customer_id);
        }
    });
}

function round_width_mm_to_5(wmm) {
    var n = flt(wmm);
    if (n <= 0) return 0;
    return Math.round(n / 5) * 5;
}

function scandinavian_raw_width_mm(row) {
    var wmm = flt(row.width);
    if (wmm > 0) return wmm;
    var win = flt(row.width_inch);
    if (win > 0) return win * 25.4;
    return 0;
}

function compute_scandinavian_m2(row, length_m_str) {
    var L = flt(length_m_str);
    if (L <= 0) return "";
    var width_mm_r = round_width_mm_to_5(scandinavian_raw_width_mm(row));
    if (width_mm_r <= 0) return "";
    var width_m = width_mm_r / 1000;
    var m2 = width_m * L;
    return String(Math.round(m2));
}

function scandinavian_width_mm_display(row) {
    var w = round_width_mm_to_5(scandinavian_raw_width_mm(row));
    return w > 0 ? String(w) : "";
}

function scandinavian_order_code(row) {
    return String(
        row.party_code ||
        row.custom_party_code ||
        row.sales_order ||
        row.order_code ||
        ""
    ).trim();
}

function resolve_sales_order_docname(order_code, callback) {
    var code = String(order_code || "").trim();
    if (!code) {
        callback(null);
        return;
    }
    frappe.call({
        method: 'frappe.client.get_list',
        args: {
            doctype: 'Sales Order',
            filters: { custom_party_code: code },
            fields: ['name'],
            limit_page_length: 1
        },
        callback: function (r) {
            var msg = (r && r.message) || [];
            if (msg.length > 0 && msg[0].name) {
                callback(msg[0].name);
                return;
            }
            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'Sales Order',
                    filters: { name: code },
                    fields: ['name'],
                    limit_page_length: 1
                },
                callback: function (r2) {
                    var msg2 = (r2 && r2.message) || [];
                    if (msg2.length > 0 && msg2[0].name) {
                        callback(msg2[0].name);
                    } else {
                        callback(null);
                    }
                },
                error: function () {
                    callback(null);
                }
            });
        },
        error: function () {
            callback(null);
        }
    });
}

function strip_html_simple(s) {
    return String(s || "")
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}

function scandinavian_treatment_display(raw) {
    var s = String(raw || "").toLowerCase().replace(/\s+/g, " ").trim();
    if (!s) return "";
    if (s.indexOf("hydrophilic") !== -1) return "HI";
    if (s.indexOf("hydrophobic") !== -1) return "HO";
    if (s.indexOf("fire retardant") !== -1 || s.indexOf("fire-retardant") !== -1) return "FR";
    if (/\buv\b/.test(s)) return "UV";
    return String(raw || "").trim();
}

function scandinavian_from_so_line(line, so_doc, fallbacks) {
    line = line || {};
    var article_no = String(
        line.custom_purchase_no ||
        line.item_code ||
        ""
    ).trim();
    var article_name = String(
        line.custom_purchase_quality_name ||
        strip_html_simple(line.description) ||
        line.item_name ||
        ""
    ).trim();
    var tracking = "";
    if (so_doc) {
        tracking = String(so_doc.po_no || "").trim();
    }
    if (!tracking) {
        tracking = fallbacks.tracking_no;
    }
    return {
        article_no: article_no,
        article_name: article_name,
        tracking_no: tracking,
        basis_weight: fallbacks.basis_weight,
        rolls_in_package: fallbacks.rolls_in_package,
        length_per_roll: fallbacks.length_per_roll,
        width_mm: fallbacks.width_mm,
        m2_in_package: fallbacks.m2_in_package,
        kg_per_package: fallbacks.kg_per_package,
        treatment: fallbacks.treatment,
        customer_company: fallbacks.customer_company,
        customer_address: fallbacks.customer_address,
        customer_contact: fallbacks.customer_contact
    };
}

function build_customer_4x6_data(row, base_data, callback) {
    var order_code = scandinavian_order_code(row);
    var length_per_roll = String(row.custom_produced_length_mtrs || row.produced_length_mtrs || "").trim();
    var width_mm_disp = scandinavian_width_mm_display(row);
    var m2_calc = compute_scandinavian_m2(row, length_per_roll);
    var fallbacks = {
        article_no: "",
        article_name: "",
        tracking_no: String(row.po_no || "").trim(),
        basis_weight: String(base_data.gsm || "").trim(),
        rolls_in_package: "1",
        length_per_roll: length_per_roll,
        width_mm: width_mm_disp,
        m2_in_package: m2_calc,
        kg_per_package: String(base_data.nw || "").trim(),
        treatment: String(base_data.quality || "").trim(),
        customer_company: "Scandinavian Nonwoven AB",
        customer_address: "Alevagen 1 - S-291 62 Kristianstad - Sweden",
        customer_contact: "Tel: +46 44 203960 - info@nonwoven.se - www.nonwoven.se"
    };

    if (!order_code) {
        callback(fallbacks);
        return;
    }

    resolve_sales_order_docname(order_code, function (so_name) {
        if (!so_name) {
            callback(fallbacks);
            return;
        }

        function finish_from_list(so_items) {
            var match = null;
            var list = so_items || [];
            for (var i = 0; i < list.length; i++) {
                if (String(list[i].item_code || "") === String(row.item_code || "")) {
                    match = list[i];
                    break;
                }
            }
            if (!match && list.length > 0) {
                match = list[0];
            }
            frappe.call({
                method: 'frappe.client.get_value',
                args: { doctype: 'Sales Order', filters: { name: so_name }, fieldname: 'po_no' },
                callback: function (gv) {
                    var pseudoSo = { po_no: (gv && gv.message && gv.message.po_no) || '' };
                    callback(scandinavian_from_so_line(match, pseudoSo, fallbacks));
                },
                error: function () {
                    callback(scandinavian_from_so_line(match, null, fallbacks));
                }
            });
        }

        frappe.call({
            method: 'frappe.client.get',
            args: { doctype: 'Sales Order', name: so_name },
            callback: function (r) {
                var so = r && r.message;
                if (!so) {
                    frappe.call({
                        method: 'frappe.client.get_list',
                        args: {
                            doctype: 'Sales Order Item',
                            filters: { parent: so_name },
                            fields: [
                                'item_code', 'item_name', 'description',
                                'custom_purchase_no', 'custom_purchase_quality_name'
                            ],
                            limit_page_length: 200
                        },
                        callback: function (r2) {
                            finish_from_list((r2 && r2.message) || []);
                        },
                        error: function () {
                            callback(fallbacks);
                        }
                    });
                    return;
                }
                var items = so.items || [];
                var match = null;
                for (var j = 0; j < items.length; j++) {
                    if (String(items[j].item_code || "") === String(row.item_code || "")) {
                        match = items[j];
                        break;
                    }
                }
                if (!match && items.length > 0) {
                    match = items[0];
                }
                callback(scandinavian_from_so_line(match, so, fallbacks));
            },
            error: function () {
                frappe.call({
                    method: 'frappe.client.get_list',
                    args: {
                        doctype: 'Sales Order Item',
                        filters: { parent: so_name },
                        fields: [
                            'item_code', 'item_name', 'description',
                            'custom_purchase_no', 'custom_purchase_quality_name'
                        ],
                        limit_page_length: 200
                    },
                    callback: function (r2) {
                        finish_from_list((r2 && r2.message) || []);
                    },
                    error: function () {
                        callback(fallbacks);
                    }
                });
            }
        });
    });
}

function get_grid_format(d, type, custom_fields) {
    type = (type || "default").trim().toLowerCase();
    var isReliance = type.includes("reliance") || type.includes("relience");
    var isPerfect = type.includes("perfect");
    var isPlainCC = type.includes("plain cc");
    var isPlain = type.includes("plain") && !isPlainCC;
    var isCustom = type.includes("custom");
    var isDefault = !isReliance && !isPerfect && !isPlainCC && !isPlain && !isCustom;

    var header = "";
    var sub1 = "";
    var sub2 = "";

    var fields = custom_fields || {
        show_company: 1, show_email: 1, show_customer: 1, show_quality: 1, show_order_code: 1,
        show_gsm: 1, show_color: 1, show_width: 1, show_length: 1, show_gw: 1, show_nw: 1,
        show_batch: 1, show_barcode: 1, width_in: 4, height_in: 4
    };
    var labelW = flt(fields.width_in) || 4;
    var labelH = flt(fields.height_in) || 4;
    var is4x2 = labelH <= 2.5;

    var qualityText = fields.show_quality ? String(d.quality || "").trim() : "";
    var orderCodeText = fields.show_order_code ? String(d.party_code || "").trim() : "";
    var joinSep = (isPlain || isPlainCC || isDefault) ? " | " : " / ";
    var qualityAndOrder = [qualityText, orderCodeText].filter(function (s) { return !!s; }).join(joinSep);

    if (isDefault || isCustom) {
        header = fields.show_company ? "JayaShree Spun Bond" : "";
        sub1 = fields.show_email ? "enquiry@jayashreespunbond.com" : "";
        sub2 = qualityAndOrder;
    } else if (isPlainCC) {
        header = fields.show_company ? "NON WOVEN FABRICS" : "";
        sub1 = qualityAndOrder;
        sub2 = "";
    } else {
        header = fields.show_company ? "NON WOVEN FABRICS" : "";
        sub1 = qualityAndOrder;
        sub2 = "";
    }

    var rows = [];
    var customer_header_row = "";
    if (isCustom && fields.show_customer && d.customer_name) {
        customer_header_row = '<div class="customer-sub">' + escape_html(d.customer_name) + '</div>';
    }
    if (fields.show_gsm) {
        rows.push('<tr><td><span class="lbl">GSM</span></td><td class="colon">:</td><td><span class="val">' + d.gsm + '</span></td></tr>');
    }
    if (d.color && fields.show_color) {
        rows.push('<tr><td><span class="lbl">COLOR</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.color) + '</span></td></tr>');
    }

    var widthUnit = " Inches";
    var wValLower = String(d.width_val || "").toLowerCase();
    if (wValLower.includes("inches") || wValLower.includes("inch") || wValLower.includes("cm") || wValLower.includes('"')) {
        widthUnit = ""; 
    }

    var lengthUnit = " Mtrs";
    var lValStr = String(d.length || "");
    if (lValStr.toLowerCase().includes("mtr")) lengthUnit = "";
    if (fields.show_length) {
        rows.push('<tr><td><span class="lbl">LENGTH</span></td><td class="colon">:</td><td><span class="val">' + d.length + lengthUnit + '</span></td></tr>');
    }
    if (fields.show_width) {
        rows.push('<tr><td><span class="lbl">WIDTH</span></td><td class="colon">:</td><td><span class="val">' + d.width_val + widthUnit + '</span></td></tr>');
    }

    if (fields.show_gw) {
        rows.push('<tr><td><span class="lbl">Gross Weight</span></td><td class="colon">:</td><td><span class="val">' + d.gw + ' Kgs</span></td></tr>');
    }
    if (fields.show_nw) {
        rows.push('<tr><td><span class="lbl">Net Weight</span></td><td class="colon">:</td><td><span class="val">' + d.nw + ' Kgs</span></td></tr>');
    }

    var btmRow = "";
    if (fields.show_batch) {
        btmRow = '<div class="btm-row"><span class="lbl">BATCH No : <span class="batch-val">' + d.batch_no + '</span></span></div>';
    }

    var rowCount = rows.length;
    var isCompact = is4x2 || (!isCustom && rowCount > 5);
    var hasCustomerHeader = !!customer_header_row;
    var hasHeaderContent = !!(header || sub1 || sub2 || customer_header_row);
    var hasBatch = !!(fields.show_batch && d.batch_no);
    var hasBarcode = !!fields.show_barcode;

    var labelStyle, headerSize, tdPad, lblSize, valSize, batchLblSize, batchValSize, barcodeH, barcodeFontSize, barcodeWidth, headerPadBot, subheaderSize, emailSize, innerMargin, innerPad, headerMarginBot, barcodeContPad, btmPadTop, btmMargin, colonSize, customerSubSize, customerSubMarginTop, customerSubMarginBottom, tableMarginY, tableJustify, headerBorderStyle, headerDisplay, btmDisplay, barcodeDisplay, tableHeight;

    if (isCompact) {
        labelStyle = 'font-size: 0.95em;';
        headerSize = 'font-size: 22px;';
        emailSize = '11px';
        subheaderSize = '15px';
        headerPadBot = '3px';
        headerMarginBot = '3px';
        innerMargin = '5px';
        innerPad = '5px 8px';
        tdPad = '3px 0';
        colonSize = '14px';
        lblSize = '15px';
        valSize = '15px';
        btmPadTop = '4px';
        btmMargin = '1px 0';
        batchLblSize = '14px';
        batchValSize = '16px';
        barcodeContPad = '2px 0 1px 0';
        barcodeH = '50px';
        barcodeFontSize = 11;
        barcodeWidth = 1.9;
        customerSubSize = '13px';
        customerSubMarginTop = '1px';
        customerSubMarginBottom = '0px';
        tableMarginY = '1px';
        tableJustify = rowCount <= 2 ? 'center' : 'flex-start';
        tableHeight = '100%';
    } else {
        labelStyle = 'font-size: 1.05em;';
        headerSize = 'font-size: 24px;';
        emailSize = '12px';
        subheaderSize = (isCustom && hasCustomerHeader) ? '15px' : '17px';
        headerPadBot = (isCustom && hasCustomerHeader) ? '3px' : '4px';
        headerMarginBot = (isCustom && hasCustomerHeader) ? '3px' : '4px';
        innerMargin = '6px';
        innerPad = (isCustom && hasCustomerHeader) ? '5px 9px' : '6px 10px';
        tdPad = (isCustom && hasCustomerHeader) ? '4px 0' : '5px 0';
        colonSize = '15px';
        lblSize = (isCustom && hasCustomerHeader) ? '15px' : '16px';
        valSize = (isCustom && hasCustomerHeader) ? '15px' : '16px';
        btmPadTop = (isCustom && hasCustomerHeader) ? '5px' : '6px';
        btmMargin = '2px 0';
        batchLblSize = (isCustom && hasCustomerHeader) ? '15px' : '16px';
        batchValSize = (isCustom && hasCustomerHeader) ? '17px' : '18px';
        barcodeContPad = (isCustom && hasCustomerHeader) ? '2px 0 1px 0' : '3px 0 2px 0';
        barcodeH = (isCustom && hasCustomerHeader) ? '52px' : '55px';
        barcodeFontSize = 12;
        barcodeWidth = 2.0;
        customerSubSize = (isCustom && hasCustomerHeader) ? '14px' : '15px';
        customerSubMarginTop = '1px';
        customerSubMarginBottom = '1px';
        tableMarginY = (isCustom && hasCustomerHeader) ? '1px' : '2px';
        tableJustify = rowCount <= 2 ? 'center' : 'flex-start';
        tableHeight = 'auto';
    }
    headerBorderStyle = hasHeaderContent ? '2px solid #333' : 'none';
    headerDisplay = hasHeaderContent ? 'block' : 'none';
    btmDisplay = hasBatch ? 'flex' : 'none';
    barcodeDisplay = hasBarcode ? 'flex' : 'none';

    var missingSections = (hasHeaderContent ? 0 : 1) + (hasBatch ? 0 : 1) + (hasBarcode ? 0 : 1);
    if (missingSections > 0) {
        tdPad = (parseInt(tdPad, 10) + missingSections) + 'px 0';
        lblSize = (parseInt(lblSize, 10) + (missingSections > 1 ? 1 : 0)) + 'px';
        valSize = (parseInt(valSize, 10) + (missingSections > 1 ? 1 : 0)) + 'px';
        tableJustify = 'center';
        tableHeight = '100%';
    }

    var pageSize = labelW + 'in ' + labelH + 'in';
    var stickerBox = 'width: ' + labelW + 'in; height: ' + labelH + 'in;';
    if (is4x2) {
        labelStyle = 'font-size: 0.82em;';
        headerSize = 'font-size: 16px;';
        emailSize = '9px';
        subheaderSize = '12px';
        headerPadBot = '2px';
        headerMarginBot = '2px';
        innerMargin = '4px';
        innerPad = '3px 6px';
        tdPad = '1px 0';
        colonSize = '12px';
        lblSize = '12px';
        valSize = '12px';
        btmPadTop = '2px';
        btmMargin = '1px 0';
        batchLblSize = '11px';
        batchValSize = '12px';
        barcodeContPad = '1px 0 0 0';
        barcodeH = '28px';
        barcodeFontSize = 9;
        barcodeWidth = 1.4;
        tableMarginY = '0px';
        tableJustify = 'center';
        tableHeight = '100%';
    }
    return '<html><head><title>Label Preview</title><style>' +
        '@media print { .btn-panel { display: none !important; } @page { size: ' + pageSize + '; margin: 0; } body { margin: 0; } }' +
        'body { font-family: "Arial", sans-serif; margin: 0; padding: 0; text-align: center; background: #eee; ' + labelStyle + ' }' +
        '.btn-panel { padding: 10px; background: #eee; }' +
        '.sticker { ' + stickerBox + ' margin: 20px auto; border: 2px solid black; background: white; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; }' +
        '.inner-border { border: 2px solid black; margin: ' + innerMargin + '; padding: ' + innerPad + '; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }' +
        '.header { text-align: center; display: ' + headerDisplay + '; border-bottom: ' + headerBorderStyle + '; padding-bottom: ' + headerPadBot + '; margin-bottom: ' + headerMarginBot + '; }' +
        '.company { ' + headerSize + ' font-weight: 900; letter-spacing: 0.5px; margin-bottom: 1px; }' +
        '.email { font-size: ' + emailSize + '; font-weight: bold; color: #444; margin-bottom: 1px; }' +
        '.customer-sub { font-size: ' + customerSubSize + '; font-weight: 900; color: #111; letter-spacing: 0.3px; margin-top: ' + customerSubMarginTop + '; margin-bottom: ' + customerSubMarginBottom + '; }' +
        '.subheader { font-size: ' + subheaderSize + '; font-weight: 900; color: black; letter-spacing: 0.5px; margin-top: 1px; }' +
        '.table-container { flex-grow: 1; display: flex; flex-direction: column; justify-content: ' + tableJustify + '; margin: ' + tableMarginY + ' 0; }' +
        'table { width: 100%; height: ' + tableHeight + '; border-collapse: collapse; margin: 0 auto; }' +
        'td { padding: ' + tdPad + '; vertical-align: middle; border: none; text-align: left; }' +
        'td:nth-child(1) { width: 44%; padding-left: 8px; }' +
        'td.colon { width: 5%; text-align: center; font-weight: bold; font-size: ' + colonSize + '; }' +
        'td:nth-child(3) { width: 51%; padding-left: 4px; }' +
        '.lbl { font-size: ' + lblSize + '; font-weight: 900; color: #333; }' +
        '.val { font-size: ' + valSize + '; font-weight: 900; color: #000; }' +
        '.btm-row { display: ' + btmDisplay + '; justify-content: center; align-items: center; border-top: 2px dashed #666; padding-top: ' + btmPadTop + '; margin: ' + btmMargin + '; }' +
        '.btm-row .lbl { font-size: ' + batchLblSize + '; }' +
        '.btm-row .batch-val { font-weight: 900; color: #000; font-size: ' + batchValSize + '; }' +
        '.barcode-container { display: ' + barcodeDisplay + '; justify-content: center; align-items: center; padding: ' + barcodeContPad + '; }' +
        '#barcode { max-width: 100%; height: ' + barcodeH + '; }' +
        '</style></head><body>' +
        '<div class="btn-panel"><button onclick="window.print()" style="padding:10px 20px; font-weight:bold; cursor:pointer;">PRINT</button><button onclick="window.close()" style="padding:10px 20px; margin-left:10px;">CLOSE</button></div>' +
        '<div class="sticker"><div class="inner-border">' +
        '<div class="header">' +
            (header ? '<div class="company">' + header + '</div>' : '') +
            (sub1 ? '<div class="email">' + sub1 + '</div>' : '') +
            customer_header_row +
            (sub2 ? '<div class="subheader">' + sub2 + '</div>' : '') +
        '</div>' +
        '<div class="table-container"><table>' + rows.join('') + '</table></div>' +
        btmRow +
        '<div class="barcode-container"><svg id="barcode"></svg></div>' +
        '</div></div>' +
        '<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.0/dist/JsBarcode.all.min.js"><\/script>' +
        (hasBarcode ? ('<script>JsBarcode("#barcode", "' + d.barcode_data + '", { format: "CODE128", displayValue: true, fontSize: ' + barcodeFontSize + ', textMargin: 1, height: ' + parseInt(barcodeH) + ', width: ' + barcodeWidth + ', margin: 0 });<\/script>') : '') +
        '</body></html>';
}

function get_customer_4x6_format(d) {
    return '<html><head><title>Customer Label 6x4</title><style>' +
        '@media print { .btn-panel { display:none !important; } @page { size: 6in 4in; margin: 0; } body { margin: 0; } }' +
        'html, body { font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 0; background: #eee; }' +
        'body { min-height: 100vh; box-sizing: border-box; }' +
        '.btn-panel { padding: 10px; background: #eee; text-align: center; }' +
        '.label { width: 6in; height: 4in; min-width: 6in; min-height: 4in; max-width: 6in; max-height: 4in; margin: 14px auto; background: #fff; box-sizing: border-box; border: 2px solid #000; padding: 8px 12px 6px; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; flex-shrink: 0; }' +
        '.top-block { flex: 0 0 auto; }' +
        '.top-block > p:nth-child(1) { margin: 0 0 2px; }' +
        '.top-block > p:nth-child(2) { margin: 0 0 6px; }' +
        '.top-block > p:nth-child(3) { margin: 5px 0 3px; }' +
        '.top-block > p:nth-child(4) { margin: 0 0 8px; }' +
        '.line-label { font-size: 14px; font-weight: 400; margin: 0; }' +
        '.line-value { font-size: 40px; font-weight: 700; margin: 0; line-height: 0.98; letter-spacing: -0.5px; }' +
        '.line-caption { font-size: 14px; margin: 0; }' +
        '.line-text { font-size: 18px; font-weight: 700; margin: 0; line-height: 1.1; }' +
        '.mid-wrap { flex: 1 1 0; min-height: 0; width: 100%; display: flex; flex-direction: column; justify-content: stretch; align-items: stretch; }' +
        '.row-dual { display: grid; grid-template-columns: 1fr 1fr; column-gap: 16px; width: 100%; margin-top: 2px; margin-bottom: 4px; align-items: start; }' +
        '.row-dual .cell-right .k, .row-dual .cell-right .v { text-align: right; }' +
        '.grid-spec { flex: 1 1 auto; width: 100%; min-height: 0; display: grid; grid-template-columns: 1fr 1fr 1fr; grid-template-rows: 1fr 1fr; column-gap: 16px; row-gap: 2px; align-content: stretch; justify-items: stretch; }' +
        '.cell { display: flex; flex-direction: column; justify-content: center; padding: 3px 0; min-height: 0; }' +
        '.cell .k { font-size: 13px; font-weight: 400; margin: 0; line-height: 1.1; }' +
        '.cell .v { font-size: 23px; font-weight: 700; line-height: 1.05; margin-top: 2px; min-height: 1.05em; }' +
        '.cell-mid { text-align: center; align-items: center; }' +
        '.cell-mid .k, .cell-mid .v { text-align: center; }' +
        '.cell-right { text-align: right; align-items: flex-end; }' +
        '.cell-right .k, .cell-right .v { text-align: right; }' +
        '.footer { flex: 0 0 auto; padding-top: 2px; }' +
        '.cust-name { text-align: center; font-size: 17px; font-weight: 700; margin: 0; }' +
        '.cust-addr { text-align: center; font-size: 11px; margin-top: 2px; text-decoration: underline; }' +
        '.cust-contact { text-align: center; font-size: 10px; margin-top: 2px; line-height: 1.2; }' +
        '</style></head><body>' +
        '<div class="btn-panel"><button onclick="window.print()" style="padding:10px 20px; font-weight:bold; cursor:pointer;">PRINT</button><button onclick="window.close()" style="padding:10px 20px; margin-left:10px;">CLOSE</button></div>' +
        '<div class="label">' +
        '<div class="top-block">' +
        '<p class="line-label">Article No</p>' +
        '<p class="line-value">' + escape_html(d.article_no) + '</p>' +
        '<p class="line-caption">Article</p>' +
        '<p class="line-text">' + escape_html(d.article_name) + '</p>' +
        '</div>' +
        '<div class="mid-wrap">' +
        '<div class="row-dual">' +
        '<div class="cell"><div class="k">Tracking No</div><div class="v">' + escape_html(d.tracking_no) + '</div></div>' +
        '<div class="cell cell-right"><div class="k">Length per roll (m)</div><div class="v">' + escape_html(d.length_per_roll) + '</div></div>' +
        '</div>' +
        '<div class="grid-spec">' +
        '<div class="cell"><div class="k">Basis Weight (g/m²)</div><div class="v">' + escape_html(d.basis_weight) + '</div></div>' +
        '<div class="cell cell-mid"><div class="k">Rolls in package</div><div class="v">' + escape_html(d.rolls_in_package) + '</div></div>' +
        '<div class="cell cell-right"><div class="k">Width (mm)</div><div class="v">' + escape_html(d.width_mm) + '</div></div>' +
        '<div class="cell"><div class="k">m² in package</div><div class="v">' + escape_html(d.m2_in_package) + '</div></div>' +
        '<div class="cell cell-mid"><div class="k">Kg per package</div><div class="v">' + escape_html(d.kg_per_package) + '</div></div>' +
        '<div class="cell cell-right"><div class="k">Treatment</div><div class="v">' + escape_html(scandinavian_treatment_display(d.treatment)) + '</div></div>' +
        '</div>' +
        '</div>' +
        '<div class="footer">' +
        '<div class="cust-name">' + escape_html(d.customer_company) + '</div>' +
        '<div class="cust-addr">' + escape_html(d.customer_address) + '</div>' +
        '<div class="cust-contact">' + escape_html(d.customer_contact) + '</div>' +
        '</div>' +
        '</div>' +
        '</body></html>';
}

function escape_html(s) {
    if (s === null || s === undefined) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function normalize_custom_fields(custom_fields) {
    var defaults = {
        show_company: 1, show_email: 1, show_customer: 1, show_quality: 1, show_order_code: 1,
        show_gsm: 1, show_color: 1, show_width: 1, show_length: 1, show_gw: 1, show_nw: 1,
        show_batch: 1, show_barcode: 1, width_in: 4, height_in: 4
    };
    if (!custom_fields) return defaults;

    var as_bool = function(v, default_value) {
        if (v === undefined || v === null || v === "") return !!default_value;
        if (typeof v === "boolean") return v;
        if (typeof v === "number") return v === 1;
        var s = String(v).trim().toLowerCase();
        return s === "1" || s === "true" || s === "yes" || s === "on";
    };

    return {
        show_company: as_bool(custom_fields.show_company, 1),
        show_email: as_bool(custom_fields.show_email, 1),
        show_customer: as_bool(custom_fields.show_customer, 1),
        show_quality: as_bool(custom_fields.show_quality, 1),
        show_order_code: as_bool(custom_fields.show_order_code, 1),
        show_gsm: as_bool(custom_fields.show_gsm, 1),
        show_color: as_bool(custom_fields.show_color, 1),
        show_width: as_bool(custom_fields.show_width, 1),
        show_length: as_bool(custom_fields.show_length, 1),
        show_gw: as_bool(custom_fields.show_gw, 1),
        show_nw: as_bool(custom_fields.show_nw, 1),
        show_batch: as_bool(custom_fields.show_batch, 1),
        show_barcode: as_bool(custom_fields.show_barcode, 1),
        width_in: flt(custom_fields.width_in) || 4,
        height_in: flt(custom_fields.height_in) || 4
    };
}

/** Bundle sticker label (desk Bundle Stickers → Print Label). */
frappe.generate_bundle_sticker_flow = function (sticker_row, frm, gridRow) {
    var f = frm || cur_frm;
    var doc = (f && f.doc) || {};
    var sticker = sticker_row || {};
    var row = gridRow || {};
    var combination = String(sticker.combination || row.combination || "").trim();
    var packCount = parseInt(sticker.rolls_per_bundle || row.pack_count || 0, 10) || 0;
    var segW = flt(row.segment_width || 0);
    if (!segW && combination) {
        var cm = combination.match(/(\d+(?:\.\d+)?)\s*\*/);
        if (cm) {
            segW = flt(cm[1]);
        }
    }
    var widthDisplay = combination;
    if (!widthDisplay && packCount > 0 && segW > 0) {
        widthDisplay = packCount + " * " + segW + " Inches";
    }
    var rollNumbers = String(sticker.roll_numbers || row.roll_numbers || "").trim();
    var lengthVal = sticker.produced_length_mtrs || sticker.custom_produced_length_mtrs
        || row.produced_length_mtrs || row.meter_roll || "0";
    var gw = flt(sticker.sticker_bundle_gross_weight_kg || row.gross_weight || 0);
    var nw = flt(sticker.sticker_bundle_weight || row.net_weight || 0);
    var batchNo = String(sticker.batch_no || row.batch_no || "").trim();
    var quality = String(row.quality || "").trim();
    var color = String(row.color || "").trim();
    var gsm = String(row.gsm || sticker.gsm || "").trim();
    var partyCode = String(row.party_code || doc.custom_order_code || "").trim();
    var qualityAndOrder = [quality, partyCode].filter(function (s) { return !!s; }).join(" | ");

    var d = {
        company: "JAYASHREE SPUN BOND",
        email: "enquiry@jayashreespunbond.com",
        quality_order: qualityAndOrder,
        gsm: gsm,
        color: color,
        width_val: widthDisplay,
        length: lengthVal,
        gw: gw.toFixed(2),
        nw: nw.toFixed(2),
        batch_no: batchNo,
        roll_numbers: rollNumbers,
        barcode_data: batchNo
    };

    var htmlContent = get_bundle_label_format(d);
    var printWindow = window.open("", "_blank", "height=650,width=500");
    if (printWindow) {
        printWindow.document.write(htmlContent);
        printWindow.document.close();
    }
};

function get_bundle_label_format(d) {
    var rows = [];
    if (d.gsm) {
        rows.push('<tr><td><span class="lbl">GSM</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.gsm) + '</span></td></tr>');
    }
    if (d.color) {
        rows.push('<tr><td><span class="lbl">COLOR</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.color) + '</span></td></tr>');
    }
    if (d.length) {
        rows.push('<tr><td><span class="lbl">LENGTH</span></td><td class="colon">:</td><td><span class="val">' + escape_html(String(d.length)) + ' MTRS</span></td></tr>');
    }
    if (d.width_val) {
        rows.push('<tr><td><span class="lbl">WIDTH</span></td><td class="colon">:</td><td><span class="val">' + escape_html(String(d.width_val).toUpperCase()) + '</span></td></tr>');
    }
    rows.push('<tr><td><span class="lbl">GROSS WEIGHT</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.gw) + ' KGS</span></td></tr>');
    rows.push('<tr><td><span class="lbl">NET WEIGHT</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.nw) + ' KGS</span></td></tr>');
    if (d.batch_no) {
        rows.push('<tr><td><span class="lbl">BATCH NO</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.batch_no) + '</span></td></tr>');
    }
    if (d.roll_numbers) {
        rows.push('<tr><td><span class="lbl">ROLL NO</span></td><td class="colon">:</td><td><span class="val">' + escape_html(d.roll_numbers) + '</span></td></tr>');
    }

    return '<html><head><title>Bundle Label Preview</title><style>' +
        '@media print { .btn-panel { display: none !important; } @page { size: 4in 4in; margin: 0; } body { margin: 0; } }' +
        'body { font-family: "Arial", sans-serif; margin: 0; padding: 0; text-align: center; background: #eee; }' +
        '.btn-panel { padding: 10px; background: #eee; }' +
        '.sticker { width: 4in; height: 4in; margin: 20px auto; border: 2px solid black; background: white; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; }' +
        '.inner-border { border: 2px solid black; margin: 6px; padding: 6px 10px; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }' +
        '.header { text-align: center; border-bottom: 2px solid #333; padding-bottom: 4px; margin-bottom: 4px; }' +
        '.company { font-size: 24px; font-weight: 900; }' +
        '.email { font-size: 12px; font-weight: bold; color: #444; }' +
        '.subheader { font-size: 17px; font-weight: 900; margin-top: 2px; }' +
        'table { width: 100%; border-collapse: collapse; margin: 2px auto; }' +
        'td { padding: 4px 0; text-align: left; vertical-align: middle; }' +
        'td:nth-child(1) { width: 44%; padding-left: 8px; }' +
        'td.colon { width: 5%; text-align: center; font-weight: bold; }' +
        '.lbl { font-size: 15px; font-weight: 900; color: #333; }' +
        '.val { font-size: 15px; font-weight: 900; color: #000; }' +
        '.barcode-container { display: flex; justify-content: center; align-items: center; padding: 3px 0 2px 0; }' +
        '#barcode { max-width: 100%; height: 55px; }' +
        '</style></head><body>' +
        '<div class="btn-panel"><button onclick="window.print()" style="padding:10px 20px; font-weight:bold; cursor:pointer;">PRINT</button><button onclick="window.close()" style="padding:10px 20px; margin-left:10px;">CLOSE</button></div>' +
        '<div class="sticker"><div class="inner-border">' +
        '<div class="header">' +
            '<div class="company">' + escape_html(d.company) + '</div>' +
            '<div class="email">' + escape_html(d.email) + '</div>' +
            (d.quality_order ? '<div class="subheader">' + escape_html(d.quality_order) + '</div>' : '') +
        '</div>' +
        '<div class="table-container"><table>' + rows.join('') + '</table></div>' +
        '<div class="barcode-container"><svg id="barcode"></svg></div>' +
        '</div></div>' +
        '<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.0/dist/JsBarcode.all.min.js"><\/script>' +
        (d.barcode_data ? ('<script>JsBarcode("#barcode", "' + d.barcode_data + '", { format: "CODE128", displayValue: true, fontSize: 12, textMargin: 1, height: 55, width: 2.0, margin: 0 });<\/script>') : '') +
        '</body></html>';
}

// ===== WASTAGE / PATTY LABEL (desk SPR + GSM Production Entry) =====

function _wastage_row_from_frm(row_name, frm, table_field) {
    var f = frm || (typeof cur_frm !== "undefined" ? cur_frm : null);
    if (!f || !f.doc) return null;
    table_field = table_field || "custom_running_patty_wastage";
    var rows = f.doc[table_field] || [];
    var row = (rows || []).find(function (r) { return r.name === row_name; });
    if (!row && typeof locals !== "undefined" && locals) {
        var cdt = table_field === "custom_roll_waste" ? "Roll Waste Row" : "Running Patty Wastage Row";
        row = (locals[cdt] || {})[row_name];
    }
    if (!row && f.doc) {
        var tableFields = [table_field, "custom_running_patty_wastage", "custom_roll_waste"];
        for (var i = 0; i < tableFields.length; i++) {
            var tf = tableFields[i];
            var altRows = f.doc[tf] || [];
            row = (altRows || []).find(function (r) { return r && r.name === row_name; });
            if (row) break;
        }
    }
    return row || null;
}

frappe.print_wastage_row_direct = function (row, frm, table_field) {
    if (!row) {
        frappe.msgprint(__("Wastage row not found."));
        return;
    }
    var html = _wastage_label_html(row, frm, table_field);
    var pw = window.open("", "_blank", "height=650,width=500");
    if (pw) {
        pw.document.write(html);
        pw.document.close();
    }
};

frappe.print_wastage_label_flow = function (row_name, frm, table_field) {
    var row = _wastage_row_from_frm(row_name, frm, table_field);
    if (!row) {
        frappe.msgprint(__("Wastage row not found."));
        return;
    }
    frappe.print_wastage_row_direct(row, frm, table_field);
};

function _patty_batch_from_roll_batch(rollBatch) {
    var s = String(rollBatch || "").trim();
    var idx = s.lastIndexOf("/");
    if (idx <= 0) {
        return "";
    }
    return s.slice(0, idx) + "W" + s.slice(idx);
}

function _wastage_label_date(doc) {
    var raw = (doc && (doc.run_date || doc.posting_date)) || "";
    if (!raw && typeof frappe !== "undefined" && frappe.datetime && frappe.datetime.get_today) {
        raw = frappe.datetime.get_today();
    }
    raw = String(raw || "").trim();
    if (!raw) {
        return "";
    }
    var parts = raw.split(/[-/]/);
    if (parts.length === 3 && parts[0].length === 4) {
        return String(parts[2]).padStart(2, "0") + "-" + String(parts[1]).padStart(2, "0") + "-" + parts[0];
    }
    return raw;
}

function _wastage_width_display(row) {
    var w = row.width_inch != null && row.width_inch !== ""
        ? row.width_inch
        : (row.width != null && row.width !== "" ? row.width : row.width_inches);
    var ws = String(w == null ? "" : w).trim();
    if (!ws) {
        return "";
    }
    var lower = ws.toLowerCase();
    if (lower.indexOf("inch") !== -1) {
        return ws;
    }
    var n = parseFloat(ws);
    if (Number.isFinite(n) && n === Math.round(n)) {
        ws = String(Math.round(n));
    }
    return ws + " Inches";
}

function _wastage_resolve_order(row, doc) {
    doc = doc || {};
    var order = String(
        doc.custom_order_code || doc.order_code || row.order_code || row.party_code || ""
    ).trim();
    if (order) {
        return order;
    }
    var job = String(row.job_id || row.job || "").trim();
    var items = doc.items || [];
    for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (job && String(it.job || it.job_id || "") !== job) {
            continue;
        }
        var pc = String(it.party_code || it.custom_party_code || "").trim();
        if (pc) {
            return pc;
        }
    }
    return "";
}

function _wastage_resolve_batch(row, doc, table_field) {
    var batch = String(row.batch_no || row.patty_batch || row.source_roll || "").trim();
    if (batch) {
        return batch;
    }
    doc = doc || {};
    var job = String(row.job_id || row.job || "").trim();
    var items = doc.items || [];
    if (table_field === "custom_roll_waste") {
        for (var i = 0; i < items.length; i++) {
            var rollIt = items[i];
            if (job && String(rollIt.job || "") !== job) {
                continue;
            }
            var rollBn = String(rollIt.batch_no || "").trim();
            if (rollBn) {
                return rollBn;
            }
        }
        return "";
    }
    for (var j = 0; j < items.length; j++) {
        var pattyIt = items[j];
        if (job && String(pattyIt.job || "") !== job) {
            continue;
        }
        var pattyBn = String(pattyIt.batch_no || "").trim();
        if (pattyBn.indexOf("W/") !== -1) {
            return pattyBn;
        }
    }
    for (var k = 0; k < items.length; k++) {
        var srcIt = items[k];
        if (job && String(srcIt.job || "") !== job) {
            continue;
        }
        var rb = String(srcIt.batch_no || "").trim();
        if (rb) {
            return _patty_batch_from_roll_batch(rb);
        }
    }
    return "";
}

function _wastage_label_html(row, frm, table_field) {
    var f = frm || {};
    var doc = f.doc || {};
    table_field = table_field || row.parentfield || "custom_running_patty_wastage";
    var title =
        table_field === "custom_roll_waste"
            ? "ROLL WASTE"
            : table_field === "custom_running_patty_wastage"
              ? "PATTY WASTE"
              : "WASTE";

    var item_code = String(row.item_code || "").trim();
    var item_name = String(row.item_name || "").trim();
    var quality = String(row.quality || "").trim();
    var color = String(row.color || "").trim();
    var gsm = row.gsm != null && row.gsm !== "" ? String(row.gsm) : "";
    if ((!quality || !color || !gsm) && (item_code || item_name)) {
        var specs = extract_details_enhanced(item_name, item_code);
        if (!quality && specs.quality) quality = specs.quality;
        if (!color && specs.color) color = specs.color;
        if (!gsm && specs.gsm) gsm = String(specs.gsm);
    }
    if (!quality || !color || !gsm) {
        var job = String(row.job_id || row.job || "").trim();
        var items = doc.items || [];
        for (var si = 0; si < items.length; si++) {
            var it = items[si];
            if (job && String(it.job || it.job_id || "") !== job) {
                continue;
            }
            if (!quality) quality = String(it.quality || "").trim();
            if (!color) color = String(it.color || "").trim();
            if (!gsm && it.gsm != null && it.gsm !== "") gsm = String(it.gsm);
            if (quality && color && gsm) break;
        }
    }
    if (!quality) quality = "NON WOVEN FABRIC";

    var runDate = _wastage_label_date(doc);
    var widthDisplay = _wastage_width_display(row);
    if (!widthDisplay) {
        var jobW = String(row.job_id || row.job || "").trim();
        var itemsW = doc.items || [];
        for (var wi = 0; wi < itemsW.length; wi++) {
            var itW = itemsW[wi];
            if (jobW && String(itW.job || itW.job_id || "") !== jobW) {
                continue;
            }
            widthDisplay = _wastage_width_display(itW);
            if (widthDisplay) break;
        }
    }
    var netRaw =
        row.net_wastage != null && row.net_wastage !== ""
            ? row.net_wastage
            : row.wastage != null && row.wastage !== ""
              ? row.wastage
              : row.wastage_qty;
    var netKg = parseFloat(netRaw);
    if (!Number.isFinite(netKg)) {
        netKg = 0;
    }
    var batch = _wastage_resolve_batch(row, doc, table_field);
    var orderCode = _wastage_resolve_order(row, doc);
    var barcode = String(batch || "").trim();

    function esc(s) { return frappe.utils.escape_html(String(s == null ? "" : s)); }
    function rowHtml(lbl, val) {
        if (val === "" || val === null || val === undefined) return "";
        return '<tr><td><span class="lbl">' + esc(lbl) + '</span></td><td class="colon">:</td><td><span class="val">' + esc(val) + "</span></td></tr>";
    }

    var body = [
        rowHtml("Date", runDate),
        rowHtml("Quality", quality),
        rowHtml("Color", color),
        rowHtml("GSM", gsm),
        rowHtml("Width", widthDisplay),
        rowHtml("Net Weight", netKg.toFixed(2) + " Kgs"),
    ].join("");

    var btmHtml = '<div class="btm-info-row">' +
        '<span class="lbl">BATCH No: </span><span class="val-large">' + esc(batch) + '</span>';
    if (orderCode) {
        btmHtml += '<span class="lbl" style="margin-left:24px;">Order: </span><span class="val-large">' + esc(orderCode) + '</span>';
    }
    btmHtml += '</div>';

    return '<html><head><title>Wastage Label Preview</title><style>' +
        '@media print { .btn-panel { display: none !important; } @page { size: 4in 4in; margin: 0; } body { margin: 0; } }' +
        'body { font-family: "Arial", sans-serif; margin: 0; padding: 0; text-align: center; background: #eee; font-size: 1.05em; }' +
        '.btn-panel { padding: 10px; background: #eee; }' +
        '.sticker { width: 4in; height: 4in; margin: 20px auto; border: 1px solid black; background: white; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; }' +
        '.inner-border { border: 2px solid black; margin: 6px; padding: 6px; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }' +
        '.header { text-align: center; border-bottom: 2px solid #333; padding-bottom: 4px; margin-bottom: 4px; margin-top: 10px; }' +
        '.wastage-header { font-size: 24px; font-weight: 900; color: #d32f2f; letter-spacing: 1px; margin-top: 2px; }' +
        '.table-container { flex-grow: 1; display: flex; flex-direction: column; justify-content: center; margin: 4px 0; }' +
        'table { width: 95%; border-collapse: collapse; margin: 0 auto; }' +
        'td { padding: 6px 0; vertical-align: middle; border: none; text-align: left; }' +
        'td:nth-child(1) { width: 42%; padding-left: 15px; }' +
        'td.colon { width: 6%; text-align: center; font-weight: bold; font-size: 15px; }' +
        'td:nth-child(3) { width: 52%; }' +
        '.lbl { font-size: 13px; font-weight: 900; color: #333; }' +
        '.val { font-size: 14px; font-weight: 900; color: #000; margin-left: 2px; }' +
        '.val-large { font-size: 14px; font-weight: 900; color: #000; }' +
        '.btm-info-row { border-top: 2px dashed #666; padding-top: 8px; padding-bottom: 6px; margin: 0 10px; text-align: center; white-space: nowrap; overflow: hidden; }' +
        '.barcode-container { display: flex; justify-content: center; align-items: center; padding: 6px 0 4px 0; }' +
        '#barcode { max-width: 95%; height: auto; }' +
        '</style></head><body>' +
        '<div class="btn-panel"><button onclick="window.print()" style="padding:10px 20px; font-weight:bold; cursor:pointer;">PRINT</button>' +
        '<button onclick="window.close()" style="padding:10px 20px; margin-left:10px;">CLOSE</button></div>' +
        '<div class="sticker"><div class="inner-border">' +
        '<div class="header"><div class="wastage-header">' + esc(title) + '</div></div>' +
        '<div class="table-container"><table>' + body + '</table></div>' +
        btmHtml +
        '<div class="barcode-container">' + (barcode ? '<svg id="barcode"></svg>' : '') + '</div>' +
        '</div></div>' +
        '<script src="https://cdn.jsdelivr.net/npm/jsbarcode@3.11.0/dist/JsBarcode.all.min.js"><\/script>' +
        (barcode
            ? ('<script>try{JsBarcode("#barcode",' + JSON.stringify(barcode) + ',{format:"CODE128",displayValue:true,fontSize:12,textMargin:1,height:28,width:1.6,margin:0});}catch(e){}<\/script>')
            : '') +
        '</body></html>';
}

frappe.generate_wastage_sticker_flow = frappe.print_wastage_label_flow;
frappe.print_patty_wastage_label = frappe.print_wastage_label_flow;

// ===== QC / APPROVAL LABEL (desk SPR + GSM Production Entry) =====

function _approval_operator_ids(frm, options) {
    var f = frm || {};
    var doc = f.doc || {};
    options = options || {};
    var operatorId = String(
        options.operator || doc.custom_operator || doc.operator || doc.custom_shift_operator || ""
    ).trim();
    var supervisorId = String(
        options.supervisor || doc.custom_supervisor || doc.supervisor || doc.custom_shift_supervisor || ""
    ).trim();
    return { operatorId: operatorId, supervisorId: supervisorId };
}

function _approval_label_html(d) {
    function esc(s) { return frappe.utils.escape_html(String(s == null ? "" : s)); }
    return '<html><head><title>Approval Label</title><style>' +
        '@media print { .btn-panel { display: none !important; } @page { size: 4in 4in; margin: 0; } body { margin: 0; } }' +
        'body { font-family: "Arial", sans-serif; margin: 0; padding: 0; text-align: center; background: #eee; }' +
        '.btn-panel { padding: 10px; background: #eee; }' +
        '.sticker { width: 4in; height: 4in; margin: 20px auto; border: 1px solid black; background: white; box-sizing: border-box; display: flex; flex-direction: column; overflow: hidden; }' +
        '.inner-border { border: 2px solid black; margin: 6px; padding: 6px; flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; overflow: hidden; }' +
        '.batch-row { display: flex; justify-content: center; align-items: center; border-bottom: 2px dashed #666; padding-bottom: 6px; margin: 15px 10px 4px 10px; }' +
        '.batch-no { font-size: 18px; font-weight: 900; color: #000; }' +
        '.table-container { flex-grow: 1; display: flex; flex-direction: column; justify-content: center; margin: 4px 0; }' +
        'table { width: 95%; border-collapse: collapse; margin: 0 auto; }' +
        'td { padding: 18px 0; vertical-align: middle; border: 1px solid #ddd; text-align: left; }' +
        'td:nth-child(1) { width: 42%; padding-left: 10px; font-weight: 900; color: #333; font-size: 14px; background: #f9f9f9; }' +
        'td:nth-child(2) { width: 58%; padding-left: 10px; font-weight: 900; color: #000; font-size: 14px; }' +
        '.footer { font-size: 10px; color: #999; margin-top: auto; padding-bottom: 2px; }' +
        '</style></head><body>' +
        '<div class="btn-panel"><button onclick="window.print()" style="padding:10px 20px; font-weight:bold; cursor:pointer;">PRINT</button>' +
        '<button onclick="window.close()" style="padding:10px 20px; margin-left:10px;">CLOSE</button></div>' +
        '<div class="sticker"><div class="inner-border">' +
        '<div class="batch-row"><span class="batch-no">BATCH No : ' + esc(d.batch_no) + '</span></div>' +
        '<div class="table-container"><table>' +
        '<tr><td>Entered By</td><td>' + esc(d.entered_by) + '</td></tr>' +
        '<tr><td>Checked By</td><td>' + esc(d.checked_by) + '</td></tr>' +
        '<tr><td>Verified By</td><td>' + esc(d.verified_by) + '</td></tr>' +
        '<tr><td>Despatched By</td><td>' + esc(d.despatched_by) + '</td></tr>' +
        '</table></div>' +
        '<div class="footer">Approval Label - System Generated</div>' +
        '</div></div></body></html>';
}

function _open_approval_label_window(html) {
    var printWindow = window.open("", "_blank", "height=500,width=500");
    if (printWindow) {
        printWindow.document.write(html);
        printWindow.document.close();
    }
}

frappe.generate_approval_label = function (row_name, frm, options) {
    var f = frm || (typeof cur_frm !== "undefined" ? cur_frm : null);
    options = options || {};
    if (!f || !f.doc) {
        return;
    }

    var row = null;
    if (row_name) {
        row = (typeof locals !== "undefined" && locals && (locals["Shaft Production Run Item"] || {})[row_name])
            || (f.doc.items || []).find(function (r) { return r && r.name === row_name; });
    }
    var raw_batch = String(options.batch_no || (row && row.batch_no) || "").trim();
    if (!raw_batch) {
        frappe.msgprint(__("Batch No is required for the QC label."));
        return;
    }

    var ids = _approval_operator_ids(f, options);
    var emp_ids = [];
    if (ids.operatorId) emp_ids.push(ids.operatorId);
    if (ids.supervisorId && ids.supervisorId !== ids.operatorId) emp_ids.push(ids.supervisorId);

    function finish_generation(names_map) {
        var entered = names_map[ids.operatorId] || ids.operatorId || "";
        var htmlContent = _approval_label_html({
            company: "JAYASHREE SPUN BOND",
            batch_no: raw_batch,
            entered_by: String(entered || "").toUpperCase(),
            checked_by: "",
            verified_by: "",
            despatched_by: ""
        });
        _open_approval_label_window(htmlContent);
    }

    if (emp_ids.length > 0) {
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Employee",
                filters: { name: ["in", emp_ids] },
                fields: ["name", "employee_name"]
            },
            callback: function (r) {
                var names_map = {};
                (r.message || []).forEach(function (emp) {
                    names_map[emp.name] = emp.employee_name;
                });
                finish_generation(names_map);
            }
        });
    } else {
        finish_generation({});
    }
};
