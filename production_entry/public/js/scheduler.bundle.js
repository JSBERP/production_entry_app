import { createApp } from "vue";
import "./custom_print_sticker.js";
import "./spr_roll_label_print.js";
import "./spr_label.js";
import "./spr_patty_stock.js";
import "./spr_mixing_sheet.js";
import "./gsm_breakdown_dialog.js";
import "./spr_quality_check.js";
import ProductionScheduler from "./ProductionScheduler.vue";
import ColorChart from "./ColorChart.vue";

frappe.provide("production_scheduler");

function safeMount(component, wrapper, label, props) {
    // If Vue render throws, we still want the page to show something
    // (instead of leaving the mount div empty).
    try {
        if (!wrapper) return;
        const app = props ? createApp(component, props) : createApp(component);
        app.mount(wrapper);
    } catch (e) {
        console.error(`${label} mount failed`, e);
        if (wrapper) {
            wrapper.innerHTML = `<div style="padding:16px;color:#b91c1c;font-weight:600;">${label} failed to load. Check browser console for details.</div>`;
        }
    }
}

production_scheduler.Controller = class {
    constructor(wrapper) {
        safeMount(ProductionScheduler, wrapper, "Production Scheduler");
    }
};

production_scheduler.ColorChartController = class {
    constructor(wrapper) {
        safeMount(ColorChart, wrapper, "Color Chart");
    }
};

import ConfirmedOrder from "./ConfirmedOrder.vue";
import ProductionTable from "./ProductionTable.vue";
import LaminationOrderTable from "./LaminationOrderTable.vue";
import PrintingOrderTable from "./PrintingOrderTable.vue";
import PrintingOrderBoard from "./PrintingOrderBoard.vue";
import SlittingOrderTable from "./SlittingOrderTable.vue";
import RewindingOrderTable from "./RewindingOrderTable.vue";
import SheetCuttingOrderTable from "./SheetCuttingOrderTable.vue";
import BoxBagOrderTable from "./BoxBagOrderTable.vue";
import SequenceApproval from "./SequenceApproval.vue";
import TransferApproval from "./TransferApproval.vue";
import LogisticsKanban from "./LogisticsKanban.vue";
import ProductionLearning from "./ProductionLearning.vue";
import DespatchApproval from "./DespatchApproval.vue";
import "./spr_trial_order_dialog.js";
import GsmProductionEntry from "./GsmProductionEntry.vue";
import SprTransferDialog from "./SprTransferDialog.vue";

production_scheduler.ConfirmedOrderController = class {
    constructor(wrapper) {
        safeMount(ConfirmedOrder, wrapper, "Confirmed Order");
    }
};

production_scheduler.ProductionTableController = class {
    constructor(wrapper) {
        safeMount(ProductionTable, wrapper, "Production Table");
    }
};

production_scheduler.LaminationOrderTableController = class {
    constructor(wrapper) {
        safeMount(LaminationOrderTable, wrapper, "Lamination Order Table");
    }
};

production_scheduler.PrintedBoppFilmTableController = class {
    constructor(wrapper) {
        safeMount(LaminationOrderTable, wrapper, "Printed BOPP Film Table", {
            tableBoardKind: "printed_bopp_film",
        });
    }
};

production_scheduler.PrintingOrderTableController = class {
    constructor(wrapper) {
        safeMount(PrintingOrderTable, wrapper, "Printing Order Table");
    }
};

production_scheduler.PrintingOrderBoardController = class {
    constructor(wrapper) {
        safeMount(PrintingOrderBoard, wrapper, "Printing Order Board");
    }
};

production_scheduler.SlittingOrderTableController = class {
    constructor(wrapper) {
        safeMount(SlittingOrderTable, wrapper, "Slitting Order Table");
    }
};

production_scheduler.RewindingOrderTableController = class {
    constructor(wrapper) {
        safeMount(RewindingOrderTable, wrapper, "Rewinding Order Table");
    }
};

production_scheduler.SheetCuttingOrderTableController = class {
    constructor(wrapper) {
        safeMount(SheetCuttingOrderTable, wrapper, "Sheet Cutting Order Table");
    }
};

production_scheduler.BoxBagOrderTableController = class {
    constructor(wrapper) {
        safeMount(BoxBagOrderTable, wrapper, "Box Bag Order Table");
    }
};

production_scheduler.SequenceApprovalController = class {
    constructor(wrapper) {
        safeMount(SequenceApproval, wrapper, "Sequence Approval");
    }
};

production_scheduler.TransferApprovalController = class {
    constructor(wrapper) {
        safeMount(TransferApproval, wrapper, "Transfer Approval");
    }
};

production_scheduler.LogisticsKanbanController = class {
    constructor(wrapper) {
        safeMount(LogisticsKanban, wrapper, "Logistics Kanban");
    }
};

production_scheduler.ProductionLearningController = class {
    constructor(wrapper) {
        safeMount(ProductionLearning, wrapper, "Production Learning");
    }
};

production_scheduler.DespatchApprovalController = class {
    constructor(wrapper) {
        safeMount(DespatchApproval, wrapper, "Despatch Approval");
    }
};

production_scheduler.GsmProductionEntryController = class {
    constructor(wrapper) {
        safeMount(GsmProductionEntry, wrapper, "GSM Production Entry");
    }
};

function mountProcessProductionEntry(wrapper, label, opts) {
    safeMount(GsmProductionEntry, wrapper, label, {
        boardSlug: opts.boardSlug,
        processScope: opts.processScope,
        pageTitle: opts.pageTitle || label,
        showRollInputs: true,
    });
}

production_scheduler.LaminationProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Lamination Production Entry", {
            boardSlug: "lamination-production-entry",
            processScope: "lamination_only",
        });
    }
};

production_scheduler.SlittingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Slitting Production Entry", {
            boardSlug: "slitting-production-entry",
            processScope: "slitting_only",
        });
    }
};

production_scheduler.RewindingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Rewinding Production Entry", {
            boardSlug: "rewinding-production-entry",
            processScope: "rewinding_only",
        });
    }
};

production_scheduler.SheetCuttingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Sheet Cutting Production Entry", {
            boardSlug: "sheet-cutting-production-entry",
            processScope: "sheet_cutting_only",
        });
    }
};

production_scheduler.BoppPrintingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "BOPP Printing Production Entry", {
            boardSlug: "bopp-printing-production-entry",
            processScope: "printed_bopp_pb_only",
        });
    }
};

production_scheduler.FlexoPrintingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Flexo Printing Production Entry", {
            boardSlug: "flexo-printing-production-entry",
            processScope: "printing_only",
        });
    }
};

production_scheduler.BagMakingProductionEntryController = class {
    constructor(wrapper) {
        mountProcessProductionEntry(wrapper, "Bag Making Production Entry", {
            boardSlug: "bag-making-production-entry",
            processScope: "box_bag_only",
        });
    }
};

let _sprTransferDialogMount = null;

production_scheduler.openSprTransferDialog = function (sprName) {
    if (!sprName) {
        frappe.msgprint(__("Save and submit the SPR first."));
        return;
    }
    if (_sprTransferDialogMount) {
        try {
            _sprTransferDialogMount.app.unmount();
        } catch (e) {
            /* ignore */
        }
        if (_sprTransferDialogMount.el && _sprTransferDialogMount.el.parentNode) {
            _sprTransferDialogMount.el.parentNode.removeChild(_sprTransferDialogMount.el);
        }
        _sprTransferDialogMount = null;
    }
    const el = document.createElement("div");
    document.body.appendChild(el);
    const cleanup = () => {
        try {
            if (_sprTransferDialogMount && _sprTransferDialogMount.app) {
                _sprTransferDialogMount.app.unmount();
            }
        } catch (e) {
            /* ignore */
        }
        if (el.parentNode) {
            el.parentNode.removeChild(el);
        }
        _sprTransferDialogMount = null;
    };
    try {
        const app = createApp(SprTransferDialog, {
            sprName,
            onClose: cleanup,
            onSubmitted: cleanup,
        });
        app.mount(el);
        _sprTransferDialogMount = { app, el };
    } catch (e) {
        console.error("openSprTransferDialog failed", e);
        cleanup();
        frappe.msgprint(
            __("Transfer dialog failed to open. Run bench build --app production_entry, hard-refresh, and try again.")
        );
    }
};
