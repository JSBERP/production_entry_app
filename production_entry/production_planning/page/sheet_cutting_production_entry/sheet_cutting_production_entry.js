frappe.pages['sheet-cutting-production-entry'].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Sheet Cutting Production Entry',
		single_column: true,
	});

	wrapper.controller = new production_scheduler.SheetCuttingProductionEntryController(wrapper);
};
