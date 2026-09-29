# SCRIPT: SYNC PRODUCTION PLAN - ITEM LEVEL MAPPING
# TYPE: API (Server Script)
# METHOD NAME: create_production_plan_from_planning_sheet
#
# One Production Plan per (unit + quality + color) group. Multiple Planning sheet Item rows in the
# same group share one PP. Within each PP: one po_items line per planning row — same item_code is
# NOT merged; each row keeps its own planned_qty.
#
# safe_exec: no frappe.db.has_column; no for-loop tuple unpacking; no names starting with _.
# get_all returns dicts — use row.get("field") not row.field.

planning_sheet = frappe.form_dict.get("planning_sheet")
if not planning_sheet:
    frappe.throw("Planning Sheet not provided")

ps = frappe.get_doc("Planning sheet", planning_sheet)

party_code = str(ps.party_code).strip() if ps.party_code else ""
sales_order = str(ps.sales_order).strip() if ps.sales_order else ""
company = frappe.db.get_default("company") or frappe.db.get_value("Global Defaults", None, "default_company")
today = frappe.utils.nowdate()

# ========== GROUP FROM OLD TABLE ONLY (ps.items) — unit || quality || color ==========
group_map = {}
row_to_pp_map = {}

for r in ps.items:
    if not r.unit:
        continue
    units_list = [u.strip() for u in str(r.unit).split(",") if u.strip()]
    quality = (r.custom_quality or "").strip().upper() or "NO_QUALITY"
    color = (r.color or "").strip().upper() or "NO_COLOR"
    for unit in units_list:
        key = f"{unit}||{quality}||{color}"
        group_map.setdefault(key, []).append(r)

# ========== FETCH EXISTING PLANS ==========
existing_pp = frappe.get_all(
    "Production Plan",
    filters={"custom_planning_sheet": ps.name, "docstatus": 0},
    fields=["name", "custom_unit", "custom_quality", "custom_color"],
)

existing_map = {}
for row in existing_pp:
    ex_unit = (row.get("custom_unit") or "").strip()
    ex_quality = (row.get("custom_quality") or "").strip().upper() or "NO_QUALITY"
    ex_color = (row.get("custom_color") or "").strip().upper() or "NO_COLOR"
    existing_map[f"{ex_unit}||{ex_quality}||{ex_color}"] = row.get("name")

created = []
updated = []
all_pp_list = []

# ========== CREATE / UPDATE PPs ==========
for key in group_map:
    rows = group_map[key]
    parts = key.split("||")
    unit_val = parts[0]
    quality_val = parts[1] if len(parts) > 1 and parts[1] != "NO_QUALITY" else ""
    color_val = parts[2] if len(parts) > 2 and parts[2] != "NO_COLOR" else ""

    plan_codes = []
    for r in rows:
        if r.custom_plan_code:
            code = str(r.custom_plan_code).strip()
            if code and code not in plan_codes:
                plan_codes.append(code)

    if key in existing_map:
        pp = frappe.get_doc("Production Plan", existing_map[key])
        pp.flags.ignore_mandatory = True
        pp.set("po_items", [])
        pp.custom_plan_code = ", ".join(plan_codes)
        updated.append(pp.name)
        all_pp_list.append(pp.name)
    else:
        pp = frappe.new_doc("Production Plan")
        pp.flags.ignore_mandatory = True
        pp.company = company
        pp.posting_date = today
        pp.sales_order = sales_order
        pp.custom_planning_sheet = ps.name
        pp.custom_party_code = party_code
        pp.custom_unit = unit_val
        pp.custom_quality = quality_val
        pp.custom_color = color_val
        pp.custom_plan_code = ", ".join(plan_codes)
        pp.insert(ignore_permissions=True)
        created.append(pp.name)
        all_pp_list.append(pp.name)

    # One po_items line per Planning sheet Item row (no aggregation by item_code)
    for r in rows:
        planned_qty = float(r.qty or 0)
        if planned_qty <= 0:
            continue

        row_to_pp_map[r.name] = pp.name
        r.order_sheet = pp.name

        item_code = r.item_code
        bom = frappe.db.get_value("BOM", {"item": item_code, "is_active": 1, "is_default": 1}, "name")
        if not bom:
            bom = frappe.db.get_value("BOM", {"item": item_code, "is_active": 1}, "name")
        item_uom = frappe.db.get_value("Item", item_code, "stock_uom") or "Kg"

        pp.append(
            "po_items",
            {
                "item_code": item_code,
                "bom_no": bom,
                "planned_qty": planned_qty,
                "uom": item_uom,
                "stock_uom": item_uom,
                "sales_order": sales_order,
                "sales_order_item": str(r.sales_order_item or "").strip(),
                "description": r.description or r.item_name,
                "custom_party_code": party_code,
                "custom_unit": unit_val,
                "custom_quality": quality_val,
                "custom_color": r.color or "",
                "custom_planning_sheet": ps.name,
                "custom_gsm": float(r.gsm or 0),
                "custom_width_": float(r.width_inch or 0),
                "custom_meterperroll": float(r.meter_per_roll or 0),
                "custom_weight_per_roll": float(r.weight_per_roll or 0),
                "custom_no_of_rolls": int(r.no_of_rolls or 0),
            },
        )

    pp.save(ignore_permissions=True)

# ========== SET order_sheet ON PLANNED ITEMS (board) ==========
pt_items = ps.get("planned_items") or []
for pt in pt_items:
    src = str(pt.source_item or "").strip()
    if src and src in row_to_pp_map:
        pt.order_sheet = row_to_pp_map[src]

# ========== SAVE EVERYTHING ==========
ps.planning_status = "Finalized"
# order_sheet is Link → Production Plan (single). A CSV of colour plans breaks save and planned_date.
ps.order_sheet = None
ps.save(ignore_permissions=True)

frappe.db.commit()

frappe.response["message"] = {"success": True, "created": created, "updated": updated}
