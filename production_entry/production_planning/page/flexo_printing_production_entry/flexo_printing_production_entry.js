frappe.pages['flexo-printing-production-entry'].on_page_load = function (wrapper) {
	frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Flexo Printing Production Entry',
		single_column: true,
	});

	wrapper.controller = new production_scheduler.FlexoPrintingProductionEntryController(wrapper);
};
