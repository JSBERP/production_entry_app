frappe.pages['bopp-printing-production-entry'].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'BOPP Printing Production Entry',
		single_column: true,
	});

	wrapper.controller = new production_scheduler.BoppPrintingProductionEntryController(wrapper);
};
