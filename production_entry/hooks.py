from . import __version__ as app_version

app_name = "production_entry"
app_title = "Production Planning"
app_publisher = "Your Company"
app_description = "Production Planning and Queuing System for Manufacturing Units"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "info@yourcompany.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# Scheduler board / Color Chart Vue (production_scheduler POC merged here)
app_include_css = [
	"/assets/production_entry/css/scheduler.css",
	"/assets/production_entry/css/planning_order_tables.css",
	"/assets/production_entry/css/planning_sheet_grid.css",
	"/assets/production_entry/css/production_learning.css",
	"/assets/production_entry/css/logistics_transfer_dialog.css",
]
app_include_js = [
	"scheduler.bundle.js",
	# Clubbing Sheet is a site Custom DocType — doctype_js alone is unreliable on Frappe Cloud.
	# Load once via desk include; JS guards against double-register.
	"/assets/production_entry/js/clubbing_sheet_form.js",
	# Shared roll print dialog — needed on Logistics Kanban + Stock Entry (not only Delivery Note).
	"/assets/production_entry/js/despatch_rolls_dialog.js",
]

# include js, css files in header of web template
# web_include_css = "/assets/production_entry/css/production_entry.css"
# web_include_js = "/assets/production_entry/js/production_entry.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "production_entry/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
page_js = {"logistics-kanban": "public/js/despatch_rolls_dialog.js"}

# include js in doctype views
doctype_js = {
    "Planning sheet": [
        "public/js/child_grid_columns.js",
        "public/js/planning_sheet_process_grid.js",
        "public/js/production_entry.js",
        "public/js/planning_sheet_custom.js",
        "public/js/planning_sheet_stock_check.js",
    ],
    "Work Order": [
        "public/js/work_order_rm_uom.js",
        "public/js/work_order_start_production.js",
    ],
    "Production Plan": "public/js/production_plan_rm_uom.js",
    "Shaft Production Run": [
        "public/js/child_grid_columns.js",
        "public/js/spr_trial_smart_bom.js",
        "public/js/spr_transfer_dialog.js",
        "public/js/custom_print_sticker.js",
        "public/js/spr_roll_label_print.js",
        "public/js/spr_label.js",
        "public/js/spr_patty_stock.js",
        "public/js/spr_quality_check.js",
        "public/js/spr_mixing_sheet.js",
        "public/js/shaft_production_run.js",
    ],
    "Roll Production Entry": "public/js/roll_production_entry.js",
    "Transfer Approval": "public/js/transfer_approval_form.js",
    "Despatch Approval": "public/js/despatch_approval_form.js",
    "Stock Entry": [
        "public/js/despatch_rolls_dialog.js",
        "public/js/stock_entry_transfer.js",
    ],
    "Delivery Note": [
        "public/js/despatch_rolls_dialog.js",
        "public/js/delivery_note_despatch.js",
    ],
    "Production Board Access": "public/js/production_board_access.js",
    "Clubbing Sheet": "public/js/clubbing_sheet_form.js",
    "Sales Order": "public/js/sales_order_planning.js",
}
doctype_list_js = {
	"Transfer Approval": "public/js/transfer_approval_list.js",
	"Shift Breakdown": "public/js/shift_breakdown_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
#	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------

# before_install = "production_entry.install.before_install"
# Install/migrate hooks MUST live in install.py — not setup.py (setuptools build file at app root).
after_install = "production_entry.install.after_install"
after_migrate = ["production_entry.install.after_migrate"]

# Desk Notifications
# -------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "production_entry.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Gate Production Planning Pages by Production Board Access (sidebar + route).
has_permission = {
	"Page": "production_entry.production_planning.board_access.page_has_permission",
}

# Strip ungranted boards from desk boot (search / Pages sidebar).
# Do not also hook boot_session — Frappe runs both, and the same function twice
# doubles Production Board Access work on every Operator login.
extend_bootinfo = "production_entry.production_planning.board_access.extend_bootinfo"

# DocType Class
# ---------------
# Override standard doctype classes

override_doctype_class = {
	"Stock Entry": "production_entry.stock_entry_override.SPRStockEntryOverride",
	"Work Order": "production_entry.work_order_override.SPRWorkOrderOverride",
}

# Document Events
# ---------------
# Hook on document methods and events

_DESIGN_MASTER_HOOKS = {
    "before_save": "production_entry.production_planning.design_verification.design_master_hooks.run_design_verification",
    "on_update": "production_entry.production_planning.design_verification.design_master_hooks.run_design_verification",
}

doc_events = {
    "Sales Order": {
        "on_submit": "production_entry.production_planning.scheduler_api.auto_create_planning_sheet",
    },
    "Planning sheet": {
        "before_validate": "production_entry.production_planning.scheduler_hooks.planning_sheet_before_validate",
        "validate": "production_entry.production_planning.scheduler_hooks.planning_sheet_validate_combined",
        "before_save": "production_entry.production_planning.scheduler_hooks.planning_sheet_allocate_unit",
        "on_update": "production_entry.production_planning.scheduler_hooks.planning_sheet_on_update",
        "on_submit": "production_entry.production_planning.scheduler_hooks.planning_sheet_update_queue",
        "before_cancel": "production_entry.production_planning.scheduler_hooks.planning_sheet_before_cancel",
    },
    "Production Plan": {
        "validate": "production_entry.production_planning.scheduler_api.normalize_production_plan_multi_uom_rm_requirements",
        "before_save": "production_entry.production_planning.scheduler_api.normalize_production_plan_multi_uom_rm_requirements",
        "on_submit": "production_entry.production_planning.scheduler_api.on_production_plan_submitted",
    },
    "Work Order": {
        "before_validate": [
            "production_entry.production_planning.scheduler_api.sync_work_order_custom_production_plan",
            "production_entry.production_planning.scheduler_api.normalize_work_order_pending_status",
            "production_entry.production_planning.scheduler_api.normalize_work_order_multi_uom_rm_requirements",
        ],
        "validate": "production_entry.production_planning.scheduler_api.normalize_work_order_multi_uom_rm_requirements",
        "before_save": "production_entry.production_planning.scheduler_api.normalize_work_order_multi_uom_rm_requirements",
        "after_insert": "production_entry.production_planning.scheduler_api.persist_work_order_rm_stock_qty_after_save",
        "on_update": "production_entry.production_planning.scheduler_api.persist_work_order_rm_stock_qty_after_save",
    },
    "Shaft Production Run": {
        "before_validate": "production_entry.production_planning.scheduler_api.normalize_linked_work_orders_for_spr",
        "before_submit": "production_entry.production_planning.scheduler_api.normalize_linked_work_orders_for_spr",
    },
    "Stock Entry": {
        "before_submit": "production_entry.production_planning.transfer_logistics.stock_entry_validate_logistics_scan",
        "on_submit": "production_entry.production_planning.transfer_logistics.stock_entry_on_submit",
        "on_cancel": "production_entry.production_planning.transfer_logistics.stock_entry_on_cancel",
        "on_trash": "production_entry.production_planning.transfer_logistics.stock_entry_on_trash",
    },
    "Transfer Approval": {
        "on_trash": "production_entry.production_planning.transfer_logistics.transfer_approval_on_trash",
    },
    "Design Master": _DESIGN_MASTER_HOOKS,
    "DESIGN MASTER": _DESIGN_MASTER_HOOKS,
    "Clubbing Sheet": {
        "before_save": "production_entry.production_planning.clubbing_sheet_hooks.clubbing_sheet_before_save",
        "before_submit": "production_entry.production_planning.clubbing_sheet_hooks.clubbing_sheet_before_submit",
        "on_cancel": "production_entry.production_planning.clubbing_sheet_hooks.clubbing_sheet_on_cancel",
    },
}

# Scheduled Tasks
# ---------------

scheduler_events = {
    "daily": [
        "production_entry.production_planning.doctype.planning_sheet.planning_sheet.daily_capacity_reset"
    ],
    "hourly": [
        "production_entry.production_planning.doctype.planning_sheet.planning_sheet.update_production_queue"
    ]
}

# Testing
# -------

# before_tests = "production_entry.install.before_tests"

# Overriding Methods
# ------------------------------
# Route legacy production_scheduler API names to the canonical implementation (255/108 BOM sync).
override_whitelisted_methods = {
	"production_scheduler.api.regenerate_planning_sheet": "production_entry.production_planning.scheduler_api.regenerate_planning_sheet",
	"production_scheduler.api.create_planning_sheet_from_so": "production_entry.production_planning.scheduler_api.create_planning_sheet_from_so",
	"production_scheduler.api.sync_bom_children_for_planning_sheet": "production_entry.production_planning.scheduler_api.sync_bom_children_for_planning_sheet",
	"production_scheduler.api.make_planning_sheet_from_sales_order": "production_entry.production_planning.scheduler_api.make_planning_sheet_from_sales_order",
	"production_scheduler.api.get_printing_order_table_data": "production_entry.production_planning.scheduler_api.get_printing_order_table_data",
	"production_scheduler.api.get_lamination_order_table_data": "production_entry.production_planning.scheduler_api.get_lamination_order_table_data",
	"production_scheduler.api.get_printed_bopp_film_table_data": "production_entry.production_planning.scheduler_api.get_printed_bopp_film_table_data",
}
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "production_entry.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]


# User Data Protection
# --------------------

user_data_fields = [
    {
        "doctype": "{doctype_1}",
        "filter_by": "{filter_by}",
        "redact_fields": ["{field_1}", "{field_2}"],
        "partial": 1,
    },
    {
        "doctype": "{doctype_2}",
        "filter_by": "{filter_by}",
        "partial": 1,
    },
    {
        "doctype": "{doctype_3}",
        "strict": False,
    },
    {
        "doctype": "{doctype_4}"
    }
]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"production_entry.auth.validate"
# ]
