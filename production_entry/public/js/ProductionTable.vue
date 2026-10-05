<template>
  <div class="cc-container">
    <!-- Filter Bar -->
    <div class="cc-filters">
      <div v-if="isLaminationBoard || isSlittingBoard || isRewindingBoard" class="cc-filter-item" style="align-self:center;padding:8px 12px;background:#ecfdf5;border:1px solid #6ee7b7;border-radius:8px;font-weight:600;color:#047857;">
        {{ isRewindingBoard ? "Rewinding Board" : isSlittingBoard ? "Slitting Board" : "Lamination Board" }} — {{ isRewindingBoard ? REWINDING_BOARD_SUBTITLE : isSlittingBoard ? SLITTING_UNIT : LAMINATION_UNIT }}
      </div>
      <div class="cc-filter-item">
        <label>View Scope</label>
        <select v-model="viewScope" @change="toggleViewScope" :disabled="isManufactureUser || accessViewScopeLocked" style="font-weight: bold; color: #4f46e5;" :style="(isManufactureUser || accessViewScopeLocked) ? { opacity: '0.3', cursor: 'not-allowed', pointerEvents: 'none' } : {}">
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
          <option value="monthly">Monthly</option>
        </select>
      </div>
      
      <div class="cc-filter-item" v-if="viewScope === 'daily'">
        <label>Planned Date</label>
        <select
          v-if="accessDateUseSelect"
          v-model="filterOrderDate"
          @change="fetchData"
          :disabled="accessDatePickerDisabled"
          :style="accessDatePickerDisabled ? { opacity: '0.5', cursor: 'not-allowed' } : {}"
        >
          <option v-for="d in accessAllowedDates" :key="d" :value="d">{{ formatAccessDateLabel(d) }}</option>
        </select>
        <input
          v-else
          type="date"
          v-model="filterOrderDate"
          @change="fetchData"
          :disabled="isManufactureUser || accessDatePickerDisabled"
          :style="(isManufactureUser || accessDatePickerDisabled) ? { opacity: '0.5', cursor: 'not-allowed' } : {}"
        />
      </div>
      <div class="cc-filter-item" v-else-if="viewScope === 'weekly'">
        <label>Select Week</label>
        <input type="week" v-model="filterWeek" @change="fetchData" />
      </div>
      <div class="cc-filter-item" v-else-if="viewScope === 'monthly'">
        <label>Select Month</label>
        <input type="month" v-model="filterMonth" @change="fetchData" />
      </div>
      <div class="cc-filter-item">
        <label>Order Code</label>
        <input type="text" v-model="filterPartyCode" placeholder="Search order..." @input="fetchData" />
      </div>
      <div v-if="!hideCustomerColumns" class="cc-filter-item">
        <label>Customer</label>
        <input type="text" v-model="filterCustomer" placeholder="Search customer..." @input="fetchData" />
      </div>
      <div v-if="showUnitFilter" class="cc-filter-item">
        <label>Unit</label>
        <select v-model="filterUnit" @change="fetchData">
          <option value="">All Units</option>
          <option v-for="u in boardUnits" :key="u" :value="u">{{ u }}</option>
        </select>
      </div>
      <button
        class="cc-clear-btn"
        @click="toggleWidthUnit"
        :title="widthDimUnit === 'mm' ? 'Switch width display to inches' : 'Switch width display to mm (rounded to nearest 5mm) with item-code fallback'"
      >
        Width: {{ widthDimUnit === 'mm' ? 'MM' : 'Inches' }}
      </button>
      <button class="cc-clear-btn" @click="fetchData">🔄 Refresh</button>
      <button
        class="cc-lock-btn"
        @click="toggleTableReorder"
        :disabled="freezeReorder"
        :style="boardActionFrozenStyle(boardAccessContext, 'reorder')"
        :title="freezeReorder ? 'Reorder is disabled for your access' : (tableReorderLocked ? 'Unlock to enable drag and drop reordering' : 'Lock to disable drag and drop reordering')"
      >
        {{ tableReorderLocked ? '🔒 Reorder Locked' : '🔓 Reorder Enabled' }}
      </button>
      <button
        class="cc-save-arrange-btn"
        @click="saveArrangement"
        :disabled="freezeArrangement || !arrangementDirty || arrangementSaving"
        :style="boardActionFrozenStyle(boardAccessContext, 'arrangement')"
        :title="freezeArrangement ? 'Arrangement is disabled for your access' : (arrangementDirty ? 'Save current row arrangement permanently' : 'No pending arrangement changes')"
      >
        {{ arrangementSaving ? 'Saving Arrangement...' : '💾 Save Arrangement' }}
      </button>
      <button
        class="cc-clear-btn"
        @click="restoreLastArrangement"
        :disabled="freezeArrangement || arrangementSaving || arrangementRestoring"
        :style="boardActionFrozenStyle(boardAccessContext, 'arrangement')"
        title="Restore last saved arrangement snapshot for current table view"
      >
        {{ arrangementRestoring ? 'Restoring...' : '↩ Restore Last' }}
      </button>
      <span v-if="arrangementSaving" class="cc-arrange-indicator saving">Saving...</span>
      <span v-else-if="arrangementDirty" class="cc-arrange-indicator dirty">Unsaved arrangement changes</span>
      <span v-else class="cc-arrange-indicator clean">Arrangement saved</span>
      <button
        class="cc-lock-btn"
        @click="toggleMergeMode"
        :disabled="freezeMerge"
        :style="[mergeMode ? { background: '#fee2e2', color: '#991b1b', borderColor: '#fca5a5' } : {}, boardActionFrozenStyle(boardAccessContext, 'merge')]"
        :title="freezeMerge ? 'Merge is disabled for your access' : (mergeMode ? 'Disable merge mode' : 'Enable merge mode')"
      >
        {{ mergeMode ? '🔗 Merge Mode ON' : '🔗 Merge Mode OFF' }}
      </button>
      <button
        class="cc-maint-btn"
        @click="openMaintenanceDialog"
        :disabled="freezeMaintenance"
        :style="boardActionFrozenStyle(boardAccessContext, 'maintenance')"
        title="Manage equipment maintenance schedules"
      >⚙️ Maintenance</button>
      <TransferToolbarBlock :board-kind="'production'" :filter-context="transferFilterContext" :disabled="freezeTransfer" @submitted="fetchData" />
      <DespatchToolbarBlock board-kind="production" :filter-context="transferFilterContext" :disabled="freezeDespatch" @submitted="fetchData" />
      
      <div class="cc-filter-item" style="margin-left: auto;">
          <button class="cc-view-btn" @click="goToBoard">📊 Back to Board</button>
      </div>
    </div>

    <div v-if="showMergeDialog" class="pt-merge-overlay" @click.self="closeMergeDialog">
      <div class="pt-merge-dialog">
        <div class="pt-merge-header">
          <h3>Merge Items</h3>
          <button class="pt-merge-close" @click="closeMergeDialog">✕</button>
        </div>
        <div class="pt-merge-filters">
          <input v-model="mergeFilterOrderCode" type="text" placeholder="Filter Order Code" />
          <input v-model="mergeFilterCustomer" type="text" placeholder="Filter Customer" />
          <input v-model="mergeFilterQuality" type="text" placeholder="Filter Quality" />
          <input v-model="mergeFilterColor" type="text" placeholder="Filter Colour" />
          <button class="cc-save-arrange-btn" @click="applyAutoMergeSuggestion">✨ Auto Suggest</button>
        </div>
        <div class="pt-merge-suggest" v-if="autoMergeSuggestions.length">
          <strong>Suggested Groups:</strong>
          <div class="pt-merge-suggest-list">
            <button
              v-for="s in autoMergeSuggestions"
              :key="s.key"
              class="pt-merge-suggest-pill"
              @click="selectSuggestion(s)"
            >
              {{ s.partyCode }} · {{ s.quality }} · {{ s.color }} · GSM: {{ s.gsmSummary }} ({{ s.items.length }})
            </button>
          </div>
        </div>
        <div class="pt-merge-summary">
          <span><b>Selected:</b> {{ selectedMergeSummary.count }} items</span>
          <span><b>Total Target:</b> {{ formatKg(selectedMergeSummary.targetWeight) }} Kg</span>
          <span><b>Total Actual:</b> {{ formatKg(selectedMergeSummary.actualWeight) }} Kg</span>
        </div>
        <div class="pt-merge-list">
          <label v-for="item in mergeDialogItems" :key="item.itemName" class="pt-merge-item">
            <input type="checkbox" :checked="selectedMergeItems.has(item.itemName)" @change="toggleMergeSelection(item.itemName)" />
            <span>{{ item.partyCode }} | {{ item.customer_name || item.customer || '-' }} | {{ item.quality }} | {{ item.color }} | {{ item.gsm }} GSM | {{ item.qty }} Kg</span>
          </label>
          <div v-if="!mergeDialogItems.length" class="pt-merge-empty">No items found for merge filters.</div>
        </div>
        <div class="pt-merge-actions">
          <button class="cc-clear-btn" @click="closeMergeDialog">Cancel</button>
          <button class="cc-clear-btn" :disabled="!autoMergeSuggestions.length" @click="createAllSuggestedMerges">Add All Suggested</button>
          <button class="cc-save-arrange-btn" :disabled="selectedMergeItems.size < 2" @click="createMergeFromDialog">Add Merge</button>
        </div>
      </div>
    </div>

    <!-- Table View (Always Visible in this page) -->
    <div class="cc-table-container">
        <div v-for="unitGroup in tableData" :key="unitGroup.unit" class="cc-table-unit-block">
            <!-- Unit Header -->
            <div class="cc-table-unit-header" :style="{ backgroundColor: getUnitHeaderColor(unitGroup.unit) }">
                {{ unitGroup.unit.toUpperCase() }} (06:00 am to 06:00 am) - Total: {{ unitGroup.totalWeight.toFixed(2) }} T
                <span v-if="unitMtdLabel(unitGroup.unit)" class="cc-unit-mtd-label">{{ unitMtdLabel(unitGroup.unit) }}</span>
            </div>

            <div class="cc-table-scroll cc-order-table-scroll">
            <table class="cc-prod-table">
                <thead>
                    <tr>
                    <th style="width: 36px;">DRAG</th>
                        <th style="width: 80px;">DATE</th>
                        <th style="width: 80px;">DAY</th>
                        <th style="width: 100px;">ORDER CODE</th>
                        <th v-if="!hideCustomerColumns" style="width: 150px;">PARTY NAME</th>
                        <th style="width: 120px;">PLAN CODE</th>
                        <th style="width: 80px;">QUALITY</th>
                        <th style="width: 100px;">COLOUR</th>
                        <th style="width: 80px;">GSM</th>
                        <th style="width: 90px;">WIDTH ({{ widthDimUnit === 'mm' ? 'MM' : 'Inches' }})</th>
                        <th style="width: 120px;">TARGET WEIGHT (Kgs)</th>
                        <th style="width: 100px;">TOTAL TARGET (Kgs)</th>
                        <th v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)" style="width: 110px; line-height: 1.2;">
                          DAY SHIFT
                          <div style="font-size: 9px; font-weight: 500; color: #64748b;">Run date · Kg</div>
                        </th>
                        <th v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)" style="width: 110px; line-height: 1.2;">
                          NIGHT SHIFT
                          <div style="font-size: 9px; font-weight: 500; color: #64748b;">Run date · Kg</div>
                        </th>
                        <th style="width: 150px;">ACTUAL PRODUCTION WEIGHT (Kgs)</th>
                        <th style="width: 100px;">TOTAL ACTUAL (Kgs)</th>
                        <th style="width: 110px;">MERGE ACTION</th>
                        <th style="width: 100px;">DESPATCH STATUS</th>
                        <th style="width: 110px;">MOVEMENT</th>
                        <th class="pt-pp-sticky-cell" style="width: 90px; min-width: 90px; max-width: 90px; position: sticky; right: 200px; background: #fafafa; z-index: 31;">PRODUCTION PLAN</th>
                        <th class="pt-spr-sticky-cell" style="width: 200px; min-width: 200px; max-width: 200px; position: sticky; right: 0; background: #fafafa; z-index: 31; line-height: 1.2;">
                          SPR / WO
                          <div style="font-size: 9px; font-weight: 500; color: #64748b;">Status &amp; entry</div>
                        </th>
                    </tr>
                </thead>
                <tbody
                  v-for="dateGroup in unitGroup.dates"
                  :key="dateGroup.key || dateGroup.date"
                  class="pt-sortable-body"
                  :data-unit="unitGroup.unit"
                  :data-date="dateGroup.date"
                >
                      <template v-if="dateGroup.rows.length">
                        <template v-for="(row, idx) in dateGroup.rows" :key="row.rowKey">
                          <tr
                            v-if="row.type === 'maintenance'"
                            class="pt-non-draggable"
                            style="background-color: #fee2e2; border: 2px solid #dc2626;"
                          >
                            <td class="cell-center" style="color:#991b1b;">🔧</td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center font-bold" style="vertical-align: middle;">
                              <div
                                v-for="(d, dateIdx) in maintenanceCardDates(dateGroup)"
                                :key="d"
                                :style="dateIdx < maintenanceCardDates(dateGroup).length - 1 ? 'padding: 6px 4px; border-bottom: 1px solid #fecaca;' : 'padding: 6px 4px;'"
                              >{{ formatDate(d) }}</div>
                            </td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center" style="vertical-align: middle;">
                              <div
                                v-for="(d, dateIdx) in maintenanceCardDates(dateGroup)"
                                :key="d"
                                :style="dateIdx < maintenanceCardDates(dateGroup).length - 1 ? 'padding: 6px 4px; border-bottom: 1px solid #fecaca;' : 'padding: 6px 4px;'"
                              >{{ getDayName(d) }}</div>
                            </td>
                            <td :colspan="tableColCount(unitGroup.unit, unitGroup.dates) - 3" style="padding: 8px 12px; font-weight: 700; color: #991b1b; text-align: center;">
                              <div style="display: inline-flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap;">
                                <span>MAINTENANCE: {{ row.maint.type }} ({{ formatMaintenanceWindow(row.maint) }})</span>
                                <span v-if="row.maint.afterOrderCode" style="font-weight:600;color:#7f1d1d;">
                                  after {{ row.maint.afterOrderCode }} · {{ row.maint.afterQuality }} · {{ row.maint.afterColor }}
                                </span>
                                <button @click="openEditMaintenanceDialog(row.maint)" style="background: #fff; color: #991b1b; border: 1px solid #dc2626; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 11px;">Edit</button>
                                <button @click="deleteMaintenanceRecord(row.maint.name)" style="background: #dc2626; color: white; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-weight: 600; font-size: 11px;">Remove</button>
                              </div>
                            </td>
                          </tr>
                          <tr v-else-if="row.type === 'item'" class="pt-draggable-row" :data-item-name="row.item.itemName">
                            <td class="cell-center" style="cursor: grab; color: #94a3b8; font-size: 15px;" :title="tableReorderLocked ? 'Unlock reorder to drag' : 'Drag to reorder'">
                              <span class="pt-drag-handle">⠿</span>
                            </td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center font-bold">
                              {{ formatDate(dateGroup.date) }}
                            </td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center">
                              {{ getDayName(dateGroup.date) }}
                            </td>
                                    
                            <td class="cell-center">{{ row.item.partyCode }}</td>
                            <td v-if="!hideCustomerColumns">{{ row.item.customer_name || row.item.party_name || row.item.customer || row.item.partyCode }}</td>
                            <td class="cell-center font-mono font-bold" style="font-size:11px; color:#4f46e5;">{{ row.item.planCode }}</td>
                            <td class="cell-center">{{ row.item.quality }}</td>
                            <td class="cell-center font-bold">{{ row.item.color }}</td>
                            <td class="cell-center">{{ row.item.gsm }}</td>
                            <td class="cell-center font-bold">
                              {{ formatWidthValue(widthInValue(row.item), row.item.itemCode || row.item.item_code || "") }}
                            </td>
                            <td class="cell-right font-bold">{{ formatKg(row.item.qty) }}</td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-right font-bold bg-blue-50">
                              {{ formatKg(dateGroup.dailyTotal) }}
                            </td>
                            <td
                              v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)"
                              class="cell-right pt-shift-cell"
                            >
                              <template v-if="showShiftRunColumns(unitGroup.unit, dateGroup.date)">
                                <div
                                  v-for="run in (row.item.day_shift_runs || [])"
                                  :key="run.spr_name"
                                  class="pt-shift-run"
                                >
                                  <div class="pt-shift-run-date">{{ formatDate(run.run_date) }}</div>
                                  <div class="pt-shift-run-kg">{{ formatKg2(run.kg) }} kg</div>
                                </div>
                                <span v-if="!(row.item.day_shift_runs || []).length">—</span>
                              </template>
                              <span v-else>—</span>
                            </td>
                            <td
                              v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)"
                              class="cell-right pt-shift-cell"
                            >
                              <template v-if="showShiftRunColumns(unitGroup.unit, dateGroup.date)">
                                <div
                                  v-for="run in (row.item.night_shift_runs || [])"
                                  :key="run.spr_name"
                                  class="pt-shift-run"
                                >
                                  <div class="pt-shift-run-date">{{ formatDate(run.run_date) }}</div>
                                  <div class="pt-shift-run-kg">{{ formatKg2(run.kg) }} kg</div>
                                </div>
                                <span v-if="!(row.item.night_shift_runs || []).length">—</span>
                              </template>
                              <span v-else>—</span>
                            </td>
                            <td class="cell-right font-bold">{{ formatKg2(row.item.actual_production_weight_kgs) }}</td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-right font-bold bg-yellow-50">
                              {{ formatKg2(dateGroup.dailyActualTotal) }}
                            </td>
                            <td class="cell-center">-</td>
                                    
                            <td class="cell-center">
                              <span class="status-badge" :class="getDispatchStatusClass(row.item.delivery_status)">
                                {{ formatDispatchStatus(row.item.delivery_status) }}
                              </span>
                            </td>
                            <td class="cell-center" style="font-size:11px;">{{ formatMovementCell(row.item) }}</td>
                            <td class="cell-center pt-pp-sticky-cell" style="position: sticky; right: 200px; background: white; z-index: 9; width: 90px; min-width: 90px;" :style="boardActionFrozenStyle(boardAccessContext, 'production_plan')">
                              <button v-if="row.item.pp_id" @click="guardedOpenProductionPlan(row.item)" class="cc-pp-btn" :title="`View PP: ${row.item.pp_id || 'resolve from sheet'}`">
                                📋 View
                              </button>
                              <span v-else class="pt-no-pp-hint">No PP created</span>
                            </td>
                            <td class="cell-center pt-spr-sticky-cell" style="position: sticky; right: 0; background: white; z-index: 9; width: 200px; min-width: 200px;" :style="boardActionFrozenStyle(boardAccessContext, 'spr_wo')">
                              <div class="pt-stock-cell">
                                <div v-if="row.item.pp_id" class="pt-pill-row">
                                  <span
                                    v-if="row.item.spr_name"
                                    class="pt-pill"
                                    :class="sprPillClass(row.item)"
                                    :title="sprPillTitle(row.item)"
                                  >{{ sprPillLabel(row.item) }}</span>
                                  <span v-else class="pt-pill pt-pill-muted" title="No Shaft Production Run linked yet">SPR: —</span>
                                  <span
                                    class="pt-pill pt-pill-wo"
                                    :class="woPillClassItem(row.item)"
                                    :title="woPillTitleItem(row.item)"
                                  >{{ woPillLabelItem(row.item) }}</span>
                                </div>
                                <div
                                  v-if="itemProductionStatusLine(row.item)"
                                  class="pt-prod-status-line"
                                  :title="itemProductionStatusTitle(row.item)"
                                >
                                  {{ itemProductionStatusLine(row.item) }}
                                </div>
                              <div class="pt-spr-btn-row" v-if="canShowStockEntry(row.item) || shouldShowItemViewSpr(row.item)">
                              <button 
                                v-if="canShowStockEntry(row.item)" 
                                @click="guardedStockEntryAction(row.item)" 
                                class="cc-pp-btn pt-btn-entry" 
                                :title="getStockEntryTitle(row.item)">
                                {{ getStockEntryLabel(row.item) }}
                              </button>
                              <button 
                                v-if="shouldShowItemViewSpr(row.item)" 
                                @click="guardedOpenItemSPR(row.item)" 
                                class="cc-pp-btn pt-btn-entry" 
                                :class="Number(row.item.spr_docstatus) === 1 && row.item.wo_terminal ? 'pt-spr-btn-done' : Number(row.item.spr_docstatus) === 1 ? 'pt-spr-btn-submitted' : 'pt-spr-btn-draft'"
                                :title="itemSprPrimaryButtonTitle(row.item)">
                                {{ itemSprPrimaryButtonLabel(row.item) }}
                              </button>
                              </div>
                              <span
                                v-else-if="row.item.pp_id && Number(row.item.pp_docstatus) !== 1"
                                class="pt-wo-closed-hint"
                                title="Production Plan exists but is still Draft. Submit the Production Plan to enable SPR creation from this table."
                              >PP Draft</span>
                              <span
                                v-else-if="row.item.pp_id && row.item.wo_terminal"
                                class="pt-wo-closed-hint"
                                title="All Work Orders linked to this Production Plan are closed (Completed / Stopped / Cancelled). No further stock entry from this button until a new WO exists."
                              >✅ WO closed</span>
                              <span v-else style="color:#999; font-size:10px; white-space: nowrap;">No PP</span>
                              </div>
                            </td>
                          </tr>

                          <tr v-else class="pt-merge-row pt-draggable-row" :data-merge-id="row.mergeId">
                            <td class="cell-center" style="cursor: grab; color: #7c3aed; font-size: 15px;" :title="tableReorderLocked ? 'Unlock reorder to drag' : 'Drag merged row'">
                              <span class="pt-drag-handle">🔗</span>
                            </td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center font-bold">
                              {{ formatDate(dateGroup.date) }}
                            </td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-center">
                              {{ getDayName(dateGroup.date) }}
                            </td>
                            <td class="cell-center font-bold">{{ row.partyCode }}</td>
                            <td v-if="!hideCustomerColumns">
                              <button v-if="canExpandMergedRows" class="pt-merge-expand-btn" @click="toggleMergeExpanded(row.mergeId)">
                                {{ isMergeExpanded(row.mergeId) ? '▼' : '▶' }} {{ row.displayLabel }}
                              </button>
                              <span v-else>{{ row.displayLabel }}</span>
                              <div v-if="canExpandMergedRows && isMergeExpanded(row.mergeId)" class="pt-merge-inline-details">
                                <div v-for="mItem in row.items" :key="mItem.itemName" class="pt-merge-inline-item">
                                  <span><b>{{ mItem.partyCode }}</b></span>
                                  <span>{{ mItem.customer_name || mItem.customer || '-' }}</span>
                                  <span>{{ mItem.quality }}</span>
                                  <span>{{ mItem.color }}</span>
                                  <span>{{ mItem.gsm }} GSM</span>
                                  <span>Width: {{ formatWidthValue(widthInValue(mItem), mItem.itemCode || mItem.item_code || "") }}</span>
                                  <span>Target: {{ formatKg(mItem.qty) }} Kg</span>
                                  <span>Actual: {{ formatKg2(mItem.actual_production_weight_kgs) }} Kg</span>
                                </div>
                              </div>
                            </td>
                            <td class="cell-center font-mono font-bold" style="font-size:11px; color:#4f46e5;">MERGED</td>
                            <td class="cell-center">{{ row.quality }}</td>
                            <td class="cell-center font-bold">{{ row.color }}</td>
                            <td class="cell-center">{{ row.gsm }}</td>
                            <td class="cell-center font-bold">{{ mergedWidthCsv(row.items || []) }}</td>
                            <td class="cell-right font-bold">{{ formatKg(row.totalTargetWeight) }}</td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-right font-bold bg-blue-50">
                              {{ formatKg(dateGroup.dailyTotal) }}
                            </td>
                            <td
                              v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)"
                              class="cell-right pt-shift-cell"
                            >
                              <template v-if="showShiftRunColumns(unitGroup.unit, dateGroup.date)">
                                <div v-for="run in (row.dayShiftRuns || [])" :key="run.spr_name" class="pt-shift-run">
                                  <div class="pt-shift-run-date">{{ formatDate(run.run_date) }}</div>
                                  <div class="pt-shift-run-kg">{{ formatKg2(run.kg) }} kg</div>
                                </div>
                                <span v-if="!(row.dayShiftRuns || []).length">—</span>
                              </template>
                              <span v-else>—</span>
                            </td>
                            <td
                              v-if="unitHasShiftView(unitGroup.unit, unitGroup.dates)"
                              class="cell-right pt-shift-cell"
                            >
                              <template v-if="showShiftRunColumns(unitGroup.unit, dateGroup.date)">
                                <div v-for="run in (row.nightShiftRuns || [])" :key="run.spr_name" class="pt-shift-run">
                                  <div class="pt-shift-run-date">{{ formatDate(run.run_date) }}</div>
                                  <div class="pt-shift-run-kg">{{ formatKg2(run.kg) }} kg</div>
                                </div>
                                <span v-if="!(row.nightShiftRuns || []).length">—</span>
                              </template>
                              <span v-else>—</span>
                            </td>
                            <td class="cell-right font-bold">{{ formatKg2(row.totalActualWeight) }}</td>
                            <td v-if="idx === 0" :rowspan="dateGroup.rows.length" class="cell-right font-bold bg-yellow-50">
                              {{ formatKg2(dateGroup.dailyActualTotal) }}
                            </td>
                            <td class="cell-center">
                              <button
                                class="cc-clear-btn"
                                style="padding: 4px 8px; font-size: 11px;"
                                :disabled="row.hasDispatchLock"
                                :title="row.hasDispatchLock ? 'Cannot unmerge dispatched rows' : 'Unmerge'"
                                @click="deleteMerge(row.mergeId)"
                              >
                                Unmerge
                              </button>
                            </td>
                            <td class="cell-center">
                              <span class="status-badge" :class="getDispatchStatusClass(row.mergeDispatchStatus)">
                                {{ formatDispatchStatus(row.mergeDispatchStatus) }}
                              </span>
                            </td>
                            <td class="cell-center" style="font-size:11px;">{{ formatMovementCell(row) }}</td>
                            <td class="cell-center pt-pp-sticky-cell" style="position: sticky; right: 200px; background: white; z-index: 9; width: 90px; min-width: 90px;" :style="boardActionFrozenStyle(boardAccessContext, 'production_plan')">
                              <button v-if="row.pp_id" @click="guardedOpenMergedProductionPlan(row)" class="cc-pp-btn" :title="`View PP for merged row`">
                                📋 View
                              </button>
                              <span v-else class="pt-no-pp-hint">No PP created</span>
                            </td>
                            <td class="cell-center pt-spr-sticky-cell" style="position: sticky; right: 0; background: white; z-index: 9; width: 200px; min-width: 200px;" :style="boardActionFrozenStyle(boardAccessContext, 'spr_wo')">
                              <div class="pt-stock-cell">
                                <div v-if="row.pp_id" class="pt-pill-row">
                                  <span
                                    v-if="row.spr_name"
                                    class="pt-pill"
                                    :class="sprPillClassMerge(row)"
                                    :title="sprPillTitleMerge(row)"
                                  >{{ sprPillLabelMerge(row) }}</span>
                                  <span v-else class="pt-pill pt-pill-muted" title="No SPR linked for this merge">SPR: —</span>
                                  <span
                                    class="pt-pill pt-pill-wo"
                                    :class="woPillClassMerge(row)"
                                    :title="woPillTitleMerge(row)"
                                  >{{ woPillLabelMerge(row) }}</span>
                                </div>
                                <div
                                  v-if="mergeProductionStatusLine(row)"
                                  class="pt-prod-status-line pt-prod-status-merge"
                                  :title="mergeProductionStatusTitle(row)"
                                >
                                  {{ mergeProductionStatusLine(row) }}
                                </div>
                              <div class="pt-spr-btn-row" v-if="canShowMergedStockEntry(row) || shouldShowMergedViewSpr(row)">
                              <button 
                                v-if="canShowMergedStockEntry(row)" 
                                @click="guardedMergedStockEntryAction(row)" 
                                class="cc-pp-btn pt-btn-entry" 
                                :title="mergedStockPrimaryTitle(row)">
                                {{ mergedStockPrimaryLabel(row) }}
                              </button>
                              <button 
                                v-if="shouldShowMergedViewSpr(row)" 
                                @click="guardedOpenMergedSPR(row)" 
                                class="cc-pp-btn pt-btn-entry"
                                :class="Number(row.spr_docstatus) === 1 && row.mergeAllWoTerminal ? 'pt-spr-btn-done' : Number(row.spr_docstatus) === 1 ? 'pt-spr-btn-submitted' : 'pt-spr-btn-draft'"
                                :title="mergedSprPrimaryButtonTitle(row)">
                                {{ mergedSprPrimaryButtonLabel(row) }}
                              </button>
                              </div>
                              <span
                                v-else-if="row.pp_id && Number(row.pp_docstatus) !== 1"
                                class="pt-wo-closed-hint"
                                title="Production Plan is Draft. Submit PP to enable SPR for this merged row."
                              >PP Draft</span>
                              <span v-else class="pt-no-pp-hint">No PP created</span>
                              </div>
                            </td>
                          </tr>
                        </template>
                      </template>

                      <tr v-else>
                        <td class="cell-center">-</td>
                        <td class="cell-center font-bold">{{ formatDate(dateGroup.date) }}</td>
                        <td class="cell-center">{{ getDayName(dateGroup.date) }}</td>
                        <td :colspan="emptyDayColspanFor(unitGroup.unit, unitGroup.dates)" style="text-align:center; color:#94a3b8; font-style:italic;">No orders (maintenance day)</td>
                      </tr>
                </tbody>
                <tbody>
                    <tr v-if="unitGroup.dates.length === 0">
                        <td :colspan="emptyUnitColspanFor(unitGroup.unit, unitGroup.dates)" style="text-align:center; padding: 20px; color:#999;">No production planned for this unit</td>
                    </tr>
                </tbody>
            </table>
            </div>
        </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch, reactive } from "vue";
import Sortable from "sortablejs";
import { mergeSprCsv, resolveSprNavigationTarget, parseSprIds } from "./spr_csv_utils.js";
import { openProductionPlanPrintPreview, resolveAndOpenProductionPlanPrintPreview } from "./pp_print_utils.js";
import { formatKgPlanning, mmDisplayFromInchesWithCodeFallback } from "./planning_table_size_units.js";
import TransferToolbarBlock from "./TransferToolbarBlock.vue";
import DespatchToolbarBlock from "./DespatchToolbarBlock.vue";
import { formatMovementCell } from "./movementDisplay.js";
import {
  buildMaintenanceData,
  getMaintenanceRecordsForDate,
  getPrimaryMaintenanceRecord,
  normalizeMaintenanceUnit,
  maintenanceUnitsEqual,
  unitAllowedByBoardAccess,
} from "./maintenance_utils.js";
import {
  buildMtdHeaderLabel,
  buildReminderDialogBody,
  buildMaintenanceReminders,
  computeMtdTargetFromProductionTable,
  computeMtdTargetKgByUnit,
  filterProductionTableReminderRows,
  UNIT_MAINTENANCE_THRESHOLDS,
} from "./maintenance_reminder_utils.js";
import {
  applyBoardAccessDateScope,
  applyBoardAccessUnitScope,
  boardAccessDatePickerDisabled,
  boardAccessDateUseSelect,
  boardAccessViewScopeLocked,
  boardActionFrozenStyle,
  formatAccessDateLabel,
  isBoardActionFrozen,
  shouldHideCustomerColumns,
} from "./board_access_ui.js";

// ===== MAINTENANCE DATA =====
const maintenanceRecords = ref([]);
const maintenanceData = ref({});
const unitMtdStats = ref({});
const maintenanceReminderQueue = ref([]);
const maintenanceReminderShowing = ref(false);
const pendingMaintenancePrefill = ref(null);
const mtdMonthRows = ref([]);

function productionTableReminderFilterOpts() {
  const ctx = boardAccessContext.value || {};
  const allowed = ctx.loaded && !ctx.unlimited ? ctx.allowed_units || [] : null;
  return {
    partyCode: filterPartyCode.value,
    customer: filterCustomer.value,
    allowedUnits: allowed,
    unitAllowedFn: unitAllowedByBoardAccess,
  };
}

function normalizeRowsForMtd(messageRows) {
  return (messageRows || []).map((d) => ({
    ...d,
    plannedDate: d.plannedDate || d.planned_date || "",
    partyCode: d.partyCode || d.party_code || "",
    itemCode: d.itemCode || d.item_code || "",
    qty: parseFloat(d.qty) || 0,
  }));
}

function rowsForMtdTargetCalculation() {
  if (viewScope.value === "monthly") {
    return filteredData.value || [];
  }
  if (mtdMonthRows.value && mtdMonthRows.value.length) {
    return filterProductionTableReminderRows(mtdMonthRows.value, productionTableReminderFilterOpts());
  }
  return filteredData.value || [];
}

async function ensureMtdMonthRowsLoaded() {
  if (viewScope.value === "monthly") {
    mtdMonthRows.value = [];
    return;
  }
  const month = getReminderMonth();
  if (!month) return;
  const [year, mon] = month.split("-");
  const lastDay = new Date(parseInt(year, 10), parseInt(mon, 10), 0).getDate();
  const startDate = `${month}-01`;
  const endDate = `${month}-${String(lastDay).padStart(2, "0")}`;
  try {
    let args = tableBoardArgs({
      party_code: filterPartyCode.value,
      start_date: startDate,
      end_date: endDate,
      plan_name: "__all__",
      planned_only: 1,
    });
    if (isRewindingBoard.value) {
      args.board_process_scope = "rewinding_only";
    } else if (isSlittingBoard.value) {
      args.board_process_scope = "slitting_only";
    } else if (isLaminationBoard.value) {
      args.board_process_scope = "lamination_only";
    } else {
      try {
        const sp = new URLSearchParams(window.location.search || "");
        const b = (sp.get("board") || "").toLowerCase();
        if (b === "lamination") args.board_process_scope = "lamination_only";
        else if (b === "slitting") args.board_process_scope = "slitting_only";
        else if (b === "rewinding") args.board_process_scope = "rewinding_only";
        else args.board_process_scope = "exclude_special";
      } catch (e) {
        args.board_process_scope = "exclude_special";
      }
    }
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_color_chart_data",
      args,
    });
    mtdMonthRows.value = normalizeRowsForMtd(res.message);
  } catch (e) {
    console.warn("Failed to load full-month rows for MTD target", e);
    mtdMonthRows.value = [];
  }
}

function recomputeMtdTargetStats() {
  if (!isMainFabricProductionTable()) {
    unitMtdStats.value = {};
    return {};
  }
  const month = getReminderMonth();

  // Monthly view: sum exactly what Production Table renders (not Color Chart matrix).
  if (viewScope.value === "monthly" && tableData.value && tableData.value.length) {
    const stats = computeMtdTargetFromProductionTable(tableData.value, month, normalizeUnit);
    unitMtdStats.value = stats;
    return stats;
  }

  const rows = rowsForMtdTargetCalculation();
  const stats = computeMtdTargetKgByUnit(rows, month, normalizeUnit);
  unitMtdStats.value = stats;
  return stats;
}

function isMainFabricProductionTable() {
  try {
    const b = (new URLSearchParams(window.location.search || "").get("board") || "").toLowerCase();
    return b !== "lamination" && b !== "slitting" && b !== "rewinding";
  } catch (e) {
    return true;
  }
}

function getReminderMonth() {
  if (viewScope.value === "monthly" && filterMonth.value) {
    return String(filterMonth.value).slice(0, 7);
  }
  if (viewScope.value === "daily" && filterOrderDate.value) {
    return String(filterOrderDate.value).slice(0, 7);
  }
  if (viewScope.value === "weekly" && filterWeek.value) {
    const [yearStr, weekStr] = String(filterWeek.value).split("-W");
    const y = parseInt(yearStr, 10);
    const w = parseInt(weekStr, 10);
    if (Number.isFinite(y) && Number.isFinite(w)) {
      const simple = new Date(y, 0, 1 + (w - 1) * 7);
      return `${simple.getFullYear()}-${String(simple.getMonth() + 1).padStart(2, "0")}`;
    }
  }
  return frappe.datetime.get_today().slice(0, 7);
}

function unitMtdLabel(unit) {
  const norm = normalizeUnit(unit);
  const stats = unitMtdStats.value[norm];
  if (!stats) return "";
  return buildMtdHeaderLabel({ unit: norm, tons: stats.tons }, UNIT_MAINTENANCE_THRESHOLDS);
}

async function loadUnitMtdStats() {
  if (!isMainFabricProductionTable()) {
    unitMtdStats.value = {};
    return null;
  }
  await ensureMtdMonthRowsLoaded();
  const stats = recomputeMtdTargetStats();
  return { month: getReminderMonth(), units: stats };
}

function showNextMaintenanceReminder() {
  if (maintenanceReminderShowing.value) return;
  const next = maintenanceReminderQueue.value.shift();
  if (!next) return;
  maintenanceReminderShowing.value = true;
  const d = new frappe.ui.Dialog({
    title: next.overdue ? `Maintenance Overdue — ${next.unit}` : `Maintenance Required — ${next.unit}`,
    fields: [{ fieldtype: "HTML", fieldname: "body", options: buildReminderDialogBody(next, !!next.overdue) }],
    primary_action_label: "OK",
    primary_action: async () => {
      try {
        await frappe.call({
          method: "production_entry.production_planning.scheduler_api.ack_maintenance_reminder",
          args: {
            month: getReminderMonth(),
            unit: next.unit,
            reminder_type: next.reminder_type,
            level: next.level,
            tonnage_at_ack: next.current_tons,
          },
        });
      } catch (e) {
        console.warn("Failed to ack maintenance reminder", e);
      }
      d.hide();
      maintenanceReminderShowing.value = false;
      showNextMaintenanceReminder();
    },
    secondary_action_label: "Add Maintenance",
    secondary_action: () => {
      pendingMaintenancePrefill.value = {
        unit: next.unit,
        maint_type: next.reminder_type,
      };
      d.hide();
      maintenanceReminderShowing.value = false;
      openMaintenanceDialog();
    },
  });
  d.show();
}

async function checkMaintenanceReminders() {
  if (!isMainFabricProductionTable()) {
    maintenanceReminderQueue.value = [];
    return;
  }
  const month = getReminderMonth();
  await ensureMtdMonthRowsLoaded();
  const unitStats = recomputeMtdTargetStats();
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_maintenance_reminder_status",
      args: {
        month,
        unit_targets_json: JSON.stringify(unitStats),
      },
    });
    const msg = res.message || {};
    maintenanceReminderQueue.value = (msg.reminders || []).slice();
    if (!maintenanceReminderQueue.value.length) {
      maintenanceReminderQueue.value = buildMaintenanceReminders(
        unitStats,
        month,
        maintenanceRecords.value,
        UNIT_MAINTENANCE_THRESHOLDS,
        normalizeUnit
      );
    }
    showNextMaintenanceReminder();
  } catch (e) {
    console.warn("Failed to check maintenance reminders", e);
    maintenanceReminderQueue.value = buildMaintenanceReminders(
      unitStats,
      month,
      maintenanceRecords.value,
      UNIT_MAINTENANCE_THRESHOLDS,
      normalizeUnit
    );
    showNextMaintenanceReminder();
  }
}

const widthDimUnit = ref("inches"); // 'inches' | 'mm'
const WIDTH_UNIT_LS_KEY = "pp_production_table_width_unit";

function toggleWidthUnit() {
  widthDimUnit.value = widthDimUnit.value === "mm" ? "inches" : "mm";
  try {
    localStorage.setItem(WIDTH_UNIT_LS_KEY, widthDimUnit.value);
  } catch (e) {}
}

function widthInValue(item) {
  const raw =
    item?.width_inch ??
    item?.width ??
    item?.custom_width ??
    item?.widthInch ??
    item?.custom_width_inch ??
    0;
  const n = Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function formatWidthValue(inches, fallbackCode) {
  if (widthDimUnit.value === "mm") {
    const mm = mmDisplayFromInchesWithCodeFallback(inches, fallbackCode);
    return mm != null && Number(mm) > 0 ? `${mm} mm` : "-";
  }
  return formatWidth(inches);
}

function mergedWidthCsv(items) {
  const arr = Array.isArray(items) ? items : [];
  const vals = [];
  const seen = new Set();
  for (const it of arr) {
    const w = widthInValue(it);
    if (!(w > 0)) continue;
    const k = (Math.round(w * 100) / 100).toFixed(2);
    if (seen.has(k)) continue;
    seen.add(k);
    vals.push(w);
  }
  vals.sort((a, b) => a - b);
  const out = vals
    .map((w) => formatWidthValue(w, ""))
    .filter((s) => s && s !== "-")
    .join(", ");
  return out || "-";
}

async function fetchMaintenanceRecords() {
	try {
		const res = await frappe.call({
			method: "production_entry.production_planning.scheduler_api.get_all_equipment_maintenance"
		});
		if (res.message) {
			maintenanceRecords.value = res.message;
			maintenanceData.value = buildMaintenanceData(res.message);
		}
	} catch (e) {
		console.error("Failed to fetch maintenance records", e);
	}
}

function openEditMaintenanceDialog(maint) {
  if (freezeMaintenance.value || !maint?.name) return;
  const unit = maint.unit || "Unit 4";
  const startDate = normalizeDateString(maint.startDate);
  let afterOrderOptions = buildAfterOrderOptionsForDialog(unit, startDate);
  const currentKey = encodeAfterOrderKey(maint.afterOrderCode, maint.afterQuality, maint.afterColor);
  const currentLabel = maint.afterOrderCode
    ? `${maint.afterOrderCode} · ${maint.afterQuality || ""} · ${maint.afterColor || ""}`
    : afterOrderOptions[0]?.label || "";
  if (maint.afterOrderCode && !afterOrderOptions.some((o) => o.value === currentKey)) {
    afterOrderOptions.push({ value: currentKey, label: currentLabel });
  }
  const saved = (maintenanceRecords.value || []).find((r) => r.name === maint.name) || {};
  const notes = String(saved.notes || "").split("MAINTENANCE_CASCADE_LOG::")[0].trim();
  const d = new frappe.ui.Dialog({
    title: "Edit Maintenance",
    fields: [
      { fieldtype: "Data", fieldname: "new_unit", label: "Unit", reqd: 1, read_only: 1, default: unit },
      {
        fieldtype: "Select",
        fieldname: "maint_type",
        label: "Maintenance Type",
        options: "Mesh Change\nDie Change\nBreakdown - Partial\nBreakdown - Full\nEB Shutdown\nMachine Off",
        reqd: 1,
        default: maint.type,
      },
      { fieldtype: "Date", fieldname: "start_date", label: "Start Date", reqd: 1, default: startDate },
      {
        fieldtype: "Time",
        fieldname: "start_time",
        label: "Start Time",
        description: "Optional. Blank = start of day (00:00)",
        default: maint.startTime || "",
      },
      { fieldtype: "Date", fieldname: "end_date", label: "End Date", reqd: 1, default: normalizeDateString(maint.endDate) },
      {
        fieldtype: "Time",
        fieldname: "end_time",
        label: "End Time",
        description: "Optional. Blank = end of day (23:59). Set 12:00 to open the afternoon and pull orders back.",
        default: maint.endTime || "",
      },
      {
        fieldtype: "Select",
        fieldname: "after_order",
        label: "After Order (Order Code · Quality · Colour)",
        options: afterOrderOptions.map((o) => o.label).join("\n"),
        default: currentLabel,
      },
      { fieldtype: "Small Text", fieldname: "notes", label: "Notes", default: notes },
    ],
    primary_action_label: "Save",
    primary_action: async (vals) => {
      const df = d.get_field("after_order");
      const label = vals.after_order || "";
      const key =
        (df && df._after_order_map && df._after_order_map[label]) ||
        (afterOrderOptions.find((o) => o.label === label)?.value ?? "");
      const decoded = decodeAfterOrderKey(key);
      try {
        const res = await frappe.call({
          method: "production_entry.production_planning.scheduler_api.update_equipment_maintenance",
          args: {
            name: maint.name,
            unit: vals.new_unit,
            maintenance_type: vals.maint_type,
            start_date: vals.start_date,
            end_date: vals.end_date,
            start_time: vals.start_time || "",
            end_time: vals.end_time || "",
            after_order_code: decoded.code || "",
            after_quality: decoded.quality || "",
            after_color: decoded.color || "",
            notes: vals.notes || "",
          },
        });
        if (res.message && res.message.status === "success") {
          frappe.show_alert({ message: res.message.message, indicator: "green" });
          await fetchMaintenanceRecords();
          await fetchData();
          d.hide();
        } else if (res.message && res.message.status === "error") {
          frappe.msgprint(res.message.message || "Could not update maintenance");
        }
      } catch (e) {
        frappe.msgprint("Error updating maintenance");
        console.error(e);
      }
    },
  });
  const dfInit = d.get_field("after_order");
  if (dfInit) {
    dfInit._after_order_map = {};
    afterOrderOptions.forEach((o) => {
      dfInit._after_order_map[o.label] = o.value;
    });
  }
  d.show();
}

async function deleteMaintenanceRecord(recordName) {
  if (!confirm('Remove this maintenance record?')) return;
	try {
		const res = await frappe.call({
			method: "production_entry.production_planning.scheduler_api.delete_maintenance_and_cascade",
			args: { maintenance_record_name: recordName }
		});
		if (res.message && res.message.status === 'success') {
			frappe.show_alert({ message: `${res.message.message}`, indicator: 'green' });
			await fetchMaintenanceRecords();
			await fetchData();
		} else if (res.message && res.message.status === 'error') {
			frappe.msgprint(res.message.message || "Error deleting maintenance record");
		}
	} catch (e) {
		frappe.msgprint("Error deleting maintenance record");
		console.error(e);
	}
}

function getMaintenanceForDate(date, unit) {
  return getMaintenanceRecordsForDate(maintenanceData.value, date, unit);
}

function normalizeDateString(dateValue) {
  if (!dateValue) return "";
  return String(dateValue).split(' ')[0].split('T')[0];
}

function getMaintenanceBannerForDate(date, unit) {
  return getPrimaryMaintenanceRecord(maintenanceData.value, date, unit);
}

function formatMaintenanceWindow(rec) {
  if (!rec) return "";
  const st = rec.startTime ? String(rec.startTime).slice(0, 5) : "";
  const et = rec.endTime ? String(rec.endTime).slice(0, 5) : "";
  const start = st ? `${rec.startDate} ${st}` : rec.startDate;
  const end = et ? `${rec.endDate} ${et}` : rec.endDate;
  return `${start} - ${end}`;
}

function _normMaintKey(v) {
  return String(v || "").trim().toUpperCase().replace(/\s+/g, " ");
}

function encodeAfterOrderKey(code, quality, color) {
  return [code || "", quality || "", color || ""].map((x) => String(x).trim()).join("|||");
}

function decodeAfterOrderKey(key) {
  const parts = String(key || "").split("|||");
  return {
    code: (parts[0] || "").trim(),
    quality: (parts[1] || "").trim(),
    color: (parts[2] || "").trim(),
  };
}

function rowMatchesAfterOrder(row, maint) {
  if (!maint?.afterOrderCode) return false;
  const code = row.type === "merge" ? row.partyCode : row.item?.partyCode;
  const quality = row.type === "merge" ? row.quality : row.item?.quality;
  const color = row.type === "merge" ? row.color : row.item?.color;
  return (
    _normMaintKey(code) === _normMaintKey(maint.afterOrderCode) &&
    _normMaintKey(quality) === _normMaintKey(maint.afterQuality) &&
    _normMaintKey(color) === _normMaintKey(maint.afterColor)
  );
}

function eachMaintenanceDateKey(startKey, endKey) {
  const keys = [];
  if (!startKey || !endKey) return keys;
  const cur = new Date(`${startKey}T00:00:00`);
  const end = new Date(`${endKey}T00:00:00`);
  if (Number.isNaN(cur.getTime()) || Number.isNaN(end.getTime()) || cur > end) return keys;
  while (cur <= end) {
    keys.push(toLocalDateKeyFromDate(cur));
    cur.setDate(cur.getDate() + 1);
  }
  return keys;
}

function maintenanceCardDates(group) {
  const dates = group?.spanDates || [];
  if (dates.length) return dates;
  return group?.date ? [group.date] : [];
}

function maintenanceRecordCard(rec) {
  return {
    name: rec.name,
    type: rec.maintenance_type,
    startDate: rec.start_date,
    endDate: rec.end_date,
    startTime: rec.start_time || "",
    endTime: rec.end_time || "",
    afterOrderCode: rec.after_order_code || "",
    afterQuality: rec.after_quality || "",
    afterColor: rec.after_color || "",
    status: rec.status,
    unit: rec.unit,
  };
}

function placeMaintenanceCards(unit, dates) {
  const range = getCurrentScopeDateRange();
  const seen = new Set();
  (maintenanceRecords.value || []).forEach((rec) => {
    if (!maintenanceUnitsEqual(rec.unit, unit) || !rec?.name || seen.has(rec.name)) return;
    seen.add(rec.name);
    const start = normalizeDateString(rec.start_date);
    const end = normalizeDateString(rec.end_date || rec.start_date);
    const span = eachMaintenanceDateKey(start, end).filter((key) => {
      if (!range) return true;
      const dt = new Date(`${key}T00:00:00`);
      return dt >= range.start && dt <= range.end;
    });
    if (!span.length) return;
    const startGroupAt = dates.findIndex((g) => !g.maintenanceCard && normalizeDateString(g.date) === start);
    const cardDates = startGroupAt >= 0 ? span.filter((key) => key !== start) : span.slice();
    const shown = cardDates.length ? cardDates : span.slice();
    const card = {
      date: shown[0],
      key: `maint-card-${rec.name}`,
      maintenanceCard: true,
      spanDates: shown,
      items: [],
      dailyTotal: 0,
      dailyActualTotal: 0,
      rows: [{
        type: "maintenance",
        rowKey: `maint-${rec.name}`,
        maint: maintenanceRecordCard(rec),
      }],
    };
    if (startGroupAt >= 0) {
      const group = dates[startGroupAt];
      const maint = card.rows[0].maint;
      let anchorAt = -1;
      if (maint.afterOrderCode) {
        (group.rows || []).forEach((row, i) => {
          if (row.type !== "maintenance" && rowMatchesAfterOrder(row, maint)) anchorAt = i;
        });
      }
      if (anchorAt >= 0 && anchorAt < (group.rows || []).length - 1) {
        const rest = group.rows.splice(anchorAt + 1);
        const restQty = rest.reduce((sum, row) => {
          if (row.type === "item") return sum + (parseFloat(row.item?.qty) || 0);
          if (row.type === "merge") return sum + (parseFloat(row.totalTargetWeight) || 0);
          return sum;
        }, 0);
        group.dailyTotal = Math.max((parseFloat(group.dailyTotal) || 0) - restQty, 0);
        const restGroup = {
          date: group.date,
          key: `${normalizeDateString(group.date)}-after-${rec.name}`,
          items: [],
          dailyTotal: restQty,
          dailyActualTotal: 0,
          rows: rest,
        };
        dates.splice(startGroupAt + 1, 0, card, restGroup);
      } else {
        dates.splice(startGroupAt + 1, 0, card);
      }
    } else {
      const at = dates.findIndex((g) => new Date(g.date).getTime() > new Date(`${shown[0]}T00:00:00`).getTime());
      if (at < 0) dates.push(card);
      else dates.splice(at, 0, card);
    }
  });
}

function buildAfterOrderOptionsForDialog(unit, dateStr) {
  const dateKey = normalizeDateString(dateStr);
  const options = [{ value: "", label: "(Top of day — before all orders)" }];
  const seen = new Set();
  (filteredData.value || []).forEach((d) => {
    if (!maintenanceUnitsEqual(d.unit, unit)) return;
    if (normalizeDateString(d.plannedDate) !== dateKey) return;
    const code = d.partyCode || "";
    const quality = d.quality || "";
    const color = d.color || "";
    if (!code) return;
    const key = encodeAfterOrderKey(code, quality, color);
    if (seen.has(_normMaintKey(key))) return;
    seen.add(_normMaintKey(key));
    options.push({
      value: key,
      label: `${code} · ${quality} · ${color}`,
    });
  });
  return options;
}

function getCurrentScopeDateRange() {
  if (viewScope.value === 'monthly') {
    if (!filterMonth.value) return null;
    const [year, month] = filterMonth.value.split('-').map(v => parseInt(v, 10));
    const start = new Date(year, month - 1, 1);
    const end = new Date(year, month, 0);
    return { start, end };
  }

  if (viewScope.value === 'weekly') {
    if (!filterWeek.value) return null;
    const [yearStr, weekStr] = filterWeek.value.split('-W');
    const year = parseInt(yearStr, 10);
    const week = parseInt(weekStr, 10);
    const simple = new Date(year, 0, 1 + (week - 1) * 7);
    const dow = simple.getDay();
    const start = new Date(simple);
    if (dow <= 4) start.setDate(simple.getDate() - dow + 1);
    else start.setDate(simple.getDate() + 8 - dow);
    const end = new Date(start);
    end.setDate(start.getDate() + 6);
    return { start, end };
  }

  if (!filterOrderDate.value) return null;
  const d = new Date(filterOrderDate.value);
  return { start: d, end: d };
}

function getScopeMaintenanceDates(unit) {
  const range = getCurrentScopeDateRange();
  if (!range) return [];

  const out = [];
  const seen = new Set();
  (maintenanceRecords.value || []).forEach((rec) => {
    if (!maintenanceUnitsEqual(rec.unit, unit)) return;
    const start = normalizeDateString(rec.start_date);
    const end = normalizeDateString(rec.end_date || rec.start_date);
    if (!start || !end) return;
    for (const key of eachMaintenanceDateKey(start, end)) {
      const dt = new Date(`${key}T00:00:00`);
      if (dt < range.start || dt > range.end) continue;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(key);
    }
  });
  return out;
}

function toLocalDateKeyFromDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function openMaintenanceDialog() {
  if (freezeMaintenance.value) return;
  const prefill = pendingMaintenancePrefill.value;
  const defaultUnit = prefill && prefill.unit ? prefill.unit : "Unit 4";
  const defaultDate = filterOrderDate.value || frappe.datetime.get_today();
  let afterOrderOptions = buildAfterOrderOptionsForDialog(defaultUnit, defaultDate);

  const refreshAfterOrderField = () => {
    const unit = d.get_value("new_unit") || defaultUnit;
    const dateStr = d.get_value("start_date") || defaultDate;
    afterOrderOptions = buildAfterOrderOptionsForDialog(unit, dateStr);
    const opts = afterOrderOptions.map((o) => o.value).join("\n");
    const labels = {};
    afterOrderOptions.forEach((o) => {
      labels[o.value] = o.label;
    });
    const df = d.get_field("after_order");
    if (df) {
      df.df.options = afterOrderOptions.map((o) => o.label).join("\n");
      // Frappe Select uses options as values; keep parallel map via df._after_order_map
      df._after_order_map = {};
      afterOrderOptions.forEach((o) => {
        df._after_order_map[o.label] = o.value;
      });
      df.refresh();
      d.set_value("after_order", afterOrderOptions[0]?.label || "");
    }
  };

	const d = new frappe.ui.Dialog({
		title: "⚙️ Equipment Maintenance Management",
		fields: [
			{
				fieldtype: "Section Break",
				label: "Add New Maintenance"
			},
			{
				fieldtype: "Select",
				fieldname: "new_unit",
				label: "Unit",
				options: "Unit 1\nUnit 2\nUnit 3\nUnit 4",
				reqd: 1,
        default: defaultUnit,
        onchange: () => refreshAfterOrderField(),
			},
			{
				fieldtype: "Select",
				fieldname: "maint_type",
				label: "Maintenance Type",
        options: "Mesh Change\nDie Change\nBreakdown - Partial\nBreakdown - Full\nEB Shutdown\nMachine Off",
				reqd: 1,
        default: prefill && prefill.maint_type ? prefill.maint_type : undefined,
			},
			{
				fieldtype: "Date",
				fieldname: "start_date",
				label: "Start Date",
				reqd: 1,
        default: defaultDate,
        onchange: () => refreshAfterOrderField(),
			},
			{
				fieldtype: "Time",
				fieldname: "start_time",
				label: "Start Time",
				description: "Optional. Blank = start of day (00:00)",
			},
			{
				fieldtype: "Date",
				fieldname: "end_date",
				label: "End Date",
				reqd: 1,
        default: defaultDate,
			},
			{
				fieldtype: "Time",
				fieldname: "end_time",
				label: "End Time",
				description: "Optional. Blank = end of day (23:59)",
			},
			{
				fieldtype: "Select",
				fieldname: "after_order",
				label: "After Order (Order Code · Quality · Colour)",
				options: afterOrderOptions.map((o) => o.label).join("\n"),
				description: "Maintenance banner will show after this order on the production table",
				default: afterOrderOptions[0]?.label || "",
			},
			{
				fieldtype: "Small Text",
				fieldname: "notes",
				label: "Notes"
			},
			{
				fieldtype: "Section Break",
				label: "Existing Maintenance Records"
			},
			{
				fieldtype: "HTML",
				fieldname: "records_display",
				options: getMaintenanceRecordsHTML()
			}
		],
		primary_action_label: "Add Maintenance",
		primary_action: async (vals) => {
			if (!vals.new_unit || !vals.maint_type || !vals.start_date || !vals.end_date) {
				frappe.msgprint("Please fill all required fields");
				return;
			}
      const df = d.get_field("after_order");
      const label = vals.after_order || "";
      const key =
        (df && df._after_order_map && df._after_order_map[label]) ||
        (afterOrderOptions.find((o) => o.label === label)?.value ?? "");
      const decoded = decodeAfterOrderKey(key);
			try {
				const res = await frappe.call({
					method: "production_entry.production_planning.scheduler_api.add_equipment_maintenance",
					args: {
						unit: vals.new_unit,
						maintenance_type: vals.maint_type,
						start_date: vals.start_date,
						end_date: vals.end_date,
						start_time: vals.start_time || "",
						end_time: vals.end_time || "",
						after_order_code: decoded.code || "",
						after_quality: decoded.quality || "",
						after_color: decoded.color || "",
						notes: vals.notes || ""
					}
				});
				if (res.message && res.message.status === 'success') {
					frappe.show_alert({ message: res.message.message, indicator: 'green' });
					await fetchMaintenanceRecords();
          await fetchData();
          pendingMaintenancePrefill.value = null;
          d.hide();
				}
			} catch (e) {
				frappe.msgprint("Error adding maintenance record");
				console.error(e);
			}
		}
	});
  const dfInit = d.get_field("after_order");
  if (dfInit) {
    dfInit._after_order_map = {};
    afterOrderOptions.forEach((o) => {
      dfInit._after_order_map[o.label] = o.value;
    });
  }
	d.show();
  pendingMaintenancePrefill.value = null;
	await fetchMaintenanceRecords();
  refreshAfterOrderField();
}

function getMaintenanceRecordsHTML() {
	if (!maintenanceRecords.value || maintenanceRecords.value.length === 0) {
		return '<p style="color: #999; text-align: center;">No maintenance records scheduled</p>';
	}
	
	let html = '<table style="width:100%; border-collapse: collapse; font-size: 12px;"><tr style="background: #f1f5f9; font-weight: 600;">';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">Unit</th>';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">Type</th>';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">Start</th>';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">End</th>';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">After Order</th>';
	html += '<th style="border: 1px solid #ddd; padding: 6px;">Status</th>';
	html += '</tr>';
	
	maintenanceRecords.value.forEach(rec => {
		const startLabel = rec.start_time ? `${rec.start_date} ${String(rec.start_time).slice(0, 5)}` : rec.start_date;
		const endLabel = rec.end_time ? `${rec.end_date} ${String(rec.end_time).slice(0, 5)}` : rec.end_date;
		const afterLabel = rec.after_order_code
			? `${rec.after_order_code} · ${rec.after_quality || ""} · ${rec.after_color || ""}`
			: "—";
		html += `<tr style="border: 1px solid #ddd;">`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center; font-weight: 600;">${rec.unit}</td>`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center;">${rec.maintenance_type}</td>`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center;">${startLabel}</td>`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center;">${endLabel}</td>`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center;">${afterLabel}</td>`;
		html += `<td style="border: 1px solid #ddd; padding: 6px; text-align: center;">`;
		const statusColor = rec.status === 'Completed' ? '#10b981' : rec.status === 'In Progress' ? '#f59e0b' : '#999';
		html += `<span style="background: ${statusColor}20; color: ${statusColor}; padding: 2px 6px; border-radius: 4px; font-weight: 600;">${rec.status || ""}</span>`;
		html += `</td>`;
		html += `</tr>`;
	});
	
	html += '</table>';
	return html;
}

// Color groups for keyword-based matching
// Check MOST SPECIFIC (multi-word) first, then SINGLE-WORD catch-all groups
const COLOR_GROUPS = [
  // ── 1. WHITES (Priority 0) ───────────────────────────────────
  { keywords: ["BRIGHT WHITE", "SUNSHINE WHITE", "MILKY WHITE", "SUPER WHITE",
               "BLEACH WHITE", "OPTICAL WHITE"], priority: 0, hex: "#FFFFFF" },
  { keywords: ["WHITE"], priority: 0, hex: "#FFFFFF" },

  // ── 2. BABY PINK (Priority 1) ───────────────────────────────────
  { keywords: ["BABY PINK"], priority: 1, hex: "#FFB6C1" },

  // ── 3. MEDICAL BLUE (Priority 2) ───────────────────────────────────
  { keywords: ["MEDICAL BLUE"],          priority: 2, hex: "#0096FF" },

  // ── 4. MEDICAL GREEN (Priority 3) ───────────────────────────────────
  { keywords: ["MEDICAL GREEN"],         priority: 3, hex: "#00A36C" },

  // ── 5. IVORY / CREAM / OFF WHITE (Priority 4) ──────────────────
  { keywords: ["BRIGHT IVORY", "IVORY", "OFF WHITE", "CREAM"], priority: 4, hex: "#FFFFF0" },

  // ── 6. YELLOWS (Priority 5-6): Lemon → Yellow → Golden
  { keywords: ["LEMON YELLOW"],          priority: 5, hex: "#FFF44F" },
  { keywords: ["GOLDEN YELLOW", "GOLD"], priority: 6, hex: "#FFD700" },
  { keywords: ["YELLOW"],                priority: 5, hex: "#FFEA00" },

  // ── 7. ORANGES (Priority 7)
  { keywords: ["LIGHT ORANGE", "PEACH", "BRIGHT ORANGE", "ORANGE"], priority: 7, hex: "#FF8C00" },

  // ── 8. PINKS (Priority 8)
  { keywords: ["DARK PINK"],             priority: 8, hex: "#C71585" },
  { keywords: ["PINK", "PINK 1.0", "PINK 2.0", "PINK 3.0", "PINK 5.0", "HOT PINK"], priority: 8, hex: "#FFC0CB" },

  // ── 9. REDS / MAROONS (Priority 9)
  { keywords: ["BRIGHT RED", "SCARLET", "CRIMSON", "RED"],  priority: 9, hex: "#D32F2F" },
  { keywords: ["MAROON", "BURGUNDY", "DARK RED"],  priority: 9, hex: "#800000" },

  // ── 10. BLUES (Priority 10-12): Peacock → Royal → Navy
  { keywords: ["LIGHT PEACOCK BLUE", "PEACOCK BLUE"], priority: 10, hex: "#008B8B" },
  { keywords: ["SKY BLUE", "LIGHT BLUE"], priority: 11, hex: "#87CEEB" },
  { keywords: ["ROYAL BLUE", "BLUE"], priority: 11, hex: "#2962FF" },
  { keywords: ["NAVY BLUE", "DARK BLUE"], priority: 12, hex: "#1A237E" },

  // ── 11. VIOLET / PURPLE (Priority 13)
  { keywords: ["VIOLET", "VOILET", "PURPLE"], priority: 13, hex: "#8B00FF" },

  // ── 12. GREENS (Priority 14-17): Reliance / Parrot / Sea / Army
  { keywords: ["GREEN 1.0 MINT", "MEDICAL GREEN"], priority: 14, hex: "#00897B" },
  { keywords: ["PARROT GREEN", "RELIANCE GREEN", "GREEN"], priority: 15, hex: "#228B22" },
  { keywords: ["SEA GREEN"],             priority: 16, hex: "#2E8B57" },
  { keywords: ["ARMY GREEN", "ARMY"],    priority: 17, hex: "#4B5320" },

  // ── 13. GREYS / SILVERS (Priority 18)
  { keywords: ["SILVER", "LIGHT GREY", "GREY", "GRAY", "DARK GREY"], priority: 18, hex: "#808080" },

  // ── 14. BROWNS (Priority 19)
  { keywords: ["BROWN", "CHOCOLATE"], priority: 19, hex: "#8B4513" },

  // ── 15. BLACK (Priority 20)
  { keywords: ["BLACK"],                 priority: 20, hex: "#000000" },

  // ── 16. BEIGES (Priority 21-22) ── Transition Rule: Run last to recover machine
  { keywords: ["DARK BEIGE"], priority: 21, hex: "#C2B280" },
  { keywords: ["LIGHT BEIGE", "BEIGE"], priority: 22, hex: "#F5F5DC" },

  // ── MIX MARKERS (priority 199) ──
  { keywords: ["WHITE MIX", "BLACK MIX", "COLOR MIX", "BEIGE MIX"], priority: 199, hex: "#c0c0c0" },
  { keywords: ["NO COLOR"], priority: 999, hex: "#e5e7eb" },
];

function findColorGroup(color) {
  const upper = (color || "").toUpperCase().trim();
  for (const group of COLOR_GROUPS) {
    for (const keyword of group.keywords) {
      if (upper.includes(keyword)) return group;
    }
  }
  return null;
}

function getColorPriority(color) {
  const group = findColorGroup(color);
  return group ? group.priority : 50;
}

function compareColor(a, b, direction) {
  const pA = getColorPriority(a.color);
  const pB = getColorPriority(b.color);
  return direction === 'asc' ? pA - pB : pB - pA;
}

function compareGsm(a, b, direction) {
  const gsmA = parseFloat(a.gsm) || 0;
  const gsmB = parseFloat(b.gsm) || 0;
  return direction === 'asc' ? gsmA - gsmB : gsmB - gsmA;
}

// Track saved sequences from 'Color Sequence Approval' to match Board order exactly
const unitSequenceStore = reactive({});

function sortItems(unit, items, date) {
  // 1. If we have a saved sequence (Manual Sort / Approved Sequence) for this unit/date, use it primarily
  const normalizedUnit = normalizeUnit(unit);
  const key = `${normalizedUnit}||${date}`;
  const savedSeq = unitSequenceStore[key]?.sequence;

  if (savedSeq && savedSeq.length) {
    const rowNames = items.map(a => a.itemName || a.name);
    console.log(`Applying saved sequence for ${key}:`, savedSeq.length, "items");
    console.log('Saved sequence:', savedSeq);
    console.log('Table row names:', rowNames);
    const seqMap = {};
    savedSeq.forEach((name, i) => seqMap[name] = i);

    return [...items].sort((a, b) => {
      const nameA = a.itemName || a.name;
      const nameB = b.itemName || b.name;

      const idxA = seqMap[nameA] !== undefined ? seqMap[nameA] : 9999 + parseInt(a.idx || 0);
      const idxB = seqMap[nameB] !== undefined ? seqMap[nameB] : 9999 + parseInt(b.idx || 0);

      if (idxA !== idxB) return idxA - idxB;

      // Fallback if not in sequence map
      const pA = getColorPriority(a.color);
      const pB = getColorPriority(b.color);
      if (pA !== pB) return pA - pB;
      return (parseFloat(b.gsm) || 0) - (parseFloat(a.gsm) || 0);
    });
  }

  // 2. Default Auto Sort (Matches Board's default when no manual sequence): 
  // Color Priority (Asc) -> GSM (Desc) -> DB Index
  return [...items].sort((a, b) => {
    // A. Color Priority
    const pA = getColorPriority(a.color);
    const pB = getColorPriority(b.color);
    if (pA !== pB) return pA - pB;

    // B. GSM Descending (Heuristic: heavier first to minimize gaps)
    const gsmA = parseFloat(a.gsm) || 0;
    const gsmB = parseFloat(b.gsm) || 0;
    if (gsmB !== gsmA) return gsmB - gsmA;

    // C. Database Index (Initial Sequence)
    const idxA = parseInt(a.idx || 0);
    const idxB = parseInt(b.idx || 0);
    return idxA - idxB;
  });
}
// ─────────────────────────────────────────────────────────────────────────────

const units = ["Unit 1", "Unit 2", "Unit 3", "Unit 4", "Mixed"];
const LAMINATION_UNIT = "TNSPL - LAMINATION UNIT";
const SLITTING_UNIT = "JVE - SLITTING MACHINE";
const REWINDING_UNIT_L3 = "TSNPL - L3 REWINDING MACHINE";
const REWINDING_UNIT_L4 = "JSB - L4 REWINDING MACHINE";
const REWINDING_UNIT_L5 = "JSB - L5 REWINDING MACHINE";
const REWINDING_UNASSIGNED_UNIT = "UNASSIGNED REWINDING UNIT";
const REWINDING_BOARD_UNITS = [REWINDING_UNIT_L3, REWINDING_UNIT_L4, REWINDING_UNIT_L5, REWINDING_UNASSIGNED_UNIT];
const REWINDING_BOARD_SUBTITLE = "L3 / L4 / L5 + Unassigned (102)";
/** True when opened from Lamination Board (production-table?board=lamination). */
const isLaminationBoard = ref(false);
const isSlittingBoard = ref(false);
const isRewindingBoard = ref(false);
const filterOrderDate = ref(frappe.datetime.get_today());
const filterWeek = ref("");
const filterMonth = ref("");
const viewScope = ref("daily");
const isManufactureUser = ref(false);

const filterPartyCode = ref("");
const filterCustomer = ref("");
const filterUnit = ref("");
/** Board slug for Production Table — separate permission from production-board (Kanban). */
const PRODUCTION_TABLE_BOARD_SLUG = "production-table";
function tableBoardArgs(extra = {}) {
  return { board_slug: PRODUCTION_TABLE_BOARD_SLUG, ...extra };
}
const boardAccessContext = ref({ unlimited: false, allowed_units: [], loaded: false });
const unitFilterState = ref({ pool: null, showUnitFilter: true, unitLocked: false });
const showUnitFilter = computed(() => unitFilterState.value.showUnitFilter !== false);

const accessAllowedDates = computed(() => boardAccessContext.value.allowed_dates || []);
const accessDateUseSelect = computed(() => boardAccessDateUseSelect(boardAccessContext.value));
const accessDatePickerDisabled = computed(() =>
  boardAccessDatePickerDisabled(boardAccessContext.value, isManufactureUser.value)
);
const accessViewScopeLocked = computed(() =>
  boardAccessViewScopeLocked(boardAccessContext.value, isManufactureUser.value)
);
const freezeMaintenance = computed(() => isBoardActionFrozen(boardAccessContext.value, "maintenance"));
const freezeTransfer = computed(() => isBoardActionFrozen(boardAccessContext.value, "transfer"));
const freezeDespatch = computed(() => isBoardActionFrozen(boardAccessContext.value, "despatch"));
const freezeArrangement = computed(() => isBoardActionFrozen(boardAccessContext.value, "arrangement"));
const freezeMerge = computed(() => isBoardActionFrozen(boardAccessContext.value, "merge"));
const freezeReorder = computed(() => isBoardActionFrozen(boardAccessContext.value, "reorder"));
const freezeProductionPlan = computed(() => isBoardActionFrozen(boardAccessContext.value, "production_plan"));
const freezeSprWo = computed(() => isBoardActionFrozen(boardAccessContext.value, "spr_wo"));

function guardedOpenProductionPlan(item) {
  if (freezeProductionPlan.value) {
    frappe.msgprint(__("Production Plan column is frozen for your access."));
    return;
  }
  openProductionPlanView(item.planningSheet, item.salesOrderItem, item.itemName, item.pp_id || "");
}

function guardedOpenMergedProductionPlan(row) {
  if (freezeProductionPlan.value) {
    frappe.msgprint(__("Production Plan column is frozen for your access."));
    return;
  }
  openMergedProductionPlan(row);
}

function guardedStockEntryAction(item) {
  if (freezeSprWo.value) {
    frappe.msgprint(__("SPR / WO column is frozen for your access."));
    return;
  }
  handleStockEntryAction(item);
}

function guardedOpenItemSPR(item) {
  if (freezeSprWo.value) {
    frappe.msgprint(__("SPR / WO column is frozen for your access."));
    return;
  }
  openItemSPR(item.spr_name, item);
}

function guardedMergedStockEntryAction(row) {
  if (freezeSprWo.value) {
    frappe.msgprint(__("SPR / WO column is frozen for your access."));
    return;
  }
  handleMergedStockEntryAction(row);
}

function guardedOpenMergedSPR(row) {
  if (freezeSprWo.value) {
    frappe.msgprint(__("SPR / WO column is frozen for your access."));
    return;
  }
  openMergedSPR(row.spr_name, row);
}
const hideCustomerColumns = computed(() => shouldHideCustomerColumns(boardAccessContext.value));
const customerColHidden = computed(() => (hideCustomerColumns.value ? 1 : 0));
const SHIFT_VIEW_PILOT_DATES = new Set(["2026-06-23", "2026-06-24"]);

function showShiftRunColumns(unit, date) {
  const d = String(date || "").trim().slice(0, 10);
  return normalizeUnit(unit) === "Unit 4" && SHIFT_VIEW_PILOT_DATES.has(d);
}

function unitHasShiftView(unit, dates) {
  if (normalizeUnit(unit) !== "Unit 4") return false;
  return (dates || []).some((dg) => showShiftRunColumns(unit, dg.date));
}

function shiftColExtra(unit, dates) {
  return unitHasShiftView(unit, dates) ? 2 : 0;
}

function tableColCount(unit, dates) {
  return 19 - customerColHidden.value + shiftColExtra(unit, dates);
}

function emptyDayColspanFor(unit, dates) {
  return 15 - customerColHidden.value + shiftColExtra(unit, dates);
}

function emptyUnitColspanFor(unit, dates) {
  return 18 - customerColHidden.value + shiftColExtra(unit, dates);
}

function mergeShiftRuns(items) {
  const dayMap = new Map();
  const nightMap = new Map();
  for (const it of items || []) {
    for (const run of it.day_shift_runs || []) {
      if (run?.spr_name && !dayMap.has(run.spr_name)) dayMap.set(run.spr_name, run);
    }
    for (const run of it.night_shift_runs || []) {
      if (run?.spr_name && !nightMap.has(run.spr_name)) nightMap.set(run.spr_name, run);
    }
  }
  const sortRuns = (arr) =>
    [...arr].sort((a, b) => String(a.run_date || "").localeCompare(String(b.run_date || "")));
  return {
    dayShiftRuns: sortRuns([...dayMap.values()]),
    nightShiftRuns: sortRuns([...nightMap.values()]),
  };
}

const emptyMaintenanceColspan = computed(() => 19 - customerColHidden.value);
const emptyDayColspan = computed(() => 15 - customerColHidden.value);
const emptyUnitColspan = computed(() => 18 - customerColHidden.value);

function applyBoardAccessContext(ctx) {
  const scope = ctx || { unlimited: false, allowed_units: [], allowed_boards: [], frozen_actions: {} };
  boardAccessContext.value = { ...scope, loaded: true };
  if (!scope || scope.unlimited) {
    unitFilterState.value = { pool: null, showUnitFilter: true, unitLocked: false };
    return;
  }
  applyBoardAccessDateScope(scope, { filterOrderDate, viewScope });
  unitFilterState.value = applyBoardAccessUnitScope(scope, filterUnit, units);
}

async function loadBoardAccessContext() {
  await new Promise((resolve) => {
    frappe.call({
      method: "production_entry.production_planning.board_access.get_production_board_user_context",
      args: { board_slug: PRODUCTION_TABLE_BOARD_SLUG },
      callback: (r) => {
        const scope = (r && r.message) || { unlimited: false, allowed_units: [] };
        applyBoardAccessContext(scope);
        try {
          window.__production_board_user_context = scope;
        } catch (e) {
          /* ignore */
        }
        resolve();
      },
      error: () => {
        applyBoardAccessContext({ unlimited: false, allowed_units: [], permitted: false });
        resolve();
      },
    });
  });
}
const transferFilterContext = computed(() => ({
  board_slug: PRODUCTION_TABLE_BOARD_SLUG,
  view_scope: viewScope.value,
  date: filterOrderDate.value,
  week: filterWeek.value,
  month: filterMonth.value,
  unit: filterUnit.value || "",
  party_code: filterPartyCode.value,
  customer: filterCustomer.value,
}));
const rawData = ref([]);
const tableReorderLocked = ref(true);
const sortableInstances = ref([]);
const pendingArrangementUpdates = ref({});
const arrangementDirty = ref(false);
const arrangementSaving = ref(false);
const arrangementRestoring = ref(false);
const mergeMode = ref(false);
const showMergeDialog = ref(false);
const merges = ref([]);
const mergedItemsMap = ref({});
const expandedMerges = ref(new Set());
const selectedMergeItems = ref(new Set());
const mergeFilterOrderCode = ref("");
const mergeFilterCustomer = ref("");
const mergeFilterQuality = ref("");
const mergeFilterColor = ref("");
const UNIT_TONNAGE_LIMITS = { "Unit 1": 4.4, "Unit 2": 12, "Unit 3": 9, "Unit 4": 5.5, "Mixed": 999, [LAMINATION_UNIT]: 999 };

function getArrangementKey(unit, date) {
  return `${unit}||${date}`;
}

// Removed duplicate normalizeUnit definition
function normalizeUnit(raw) {
  const norm = normalizeMaintenanceUnit(raw);
  if (["Unit 1", "Unit 2", "Unit 3", "Unit 4"].includes(norm)) return norm;
  if (norm === LAMINATION_UNIT || norm === SLITTING_UNIT) return norm;
  return "Mixed";
}

function destroyTableSortables() {
  sortableInstances.value.forEach((instance) => {
    try {
      instance.destroy();
    } catch (e) {}
  });
  sortableInstances.value = [];
}

async function persistDateGroupOrder(tbodyEl) {
  const unit = tbodyEl?.dataset?.unit;
  const date = tbodyEl?.dataset?.date;
  if (!unit || !date) return;

  const rows = Array.from(tbodyEl.querySelectorAll('.pt-draggable-row'));
  const expandedNames = [];

  // Persist merge rows as a contiguous block by expanding to all child item names.
  rows.forEach((row) => {
    const mergeId = row.dataset.mergeId;
    if (mergeId) {
      const unitGroup = tableData.value.find((g) => normalizeUnit(g.unit) === normalizeUnit(unit));
      const dateGroup = unitGroup?.dates?.find((d) => String(d.date) === String(date));
      const mergeRow = dateGroup?.rows?.find((r) => r.type === 'merge' && r.mergeId === mergeId);
      const mergeItemNames = (mergeRow?.items || []).map((it) => it.itemName).filter(Boolean);
      expandedNames.push(...mergeItemNames);
      return;
    }

    if (row.dataset.itemName) {
      expandedNames.push(row.dataset.itemName);
    }
  });

  const items = expandedNames
    .map((name, idx) => ({
      name,
      unit,
      date,
      index: idx + 1,
    }))
    .filter((row) => row.name);

  if (!items.length) return;

  pendingArrangementUpdates.value[getArrangementKey(unit, date)] = items;
  arrangementDirty.value = true;
}

async function saveArrangement() {
  if (freezeArrangement.value) return;
  if (!arrangementDirty.value || arrangementSaving.value) return;

  const groupedUpdates = Object.entries(pendingArrangementUpdates.value);
  const hasRows = groupedUpdates.some(([, items]) => (items || []).length > 0);
  if (!hasRows) {
    arrangementDirty.value = false;
    return;
  }

  arrangementSaving.value = true;
  try {
    // Persist explicit sequence per unit/date only.
    for (const [, items] of groupedUpdates) {
      if (!items.length) continue;
      const unit = normalizeUnit(items[0].unit);
      const date = items[0].date;
      const sequence = items.map((row) => row.name).filter(Boolean);
      if (!unit || !date || !sequence.length) continue;

      // Save to backend
      await frappe.call({
        method: "production_entry.production_planning.scheduler_api.save_color_sequence",
        args: tableBoardArgs({
          date,
          unit,
          sequence_data: JSON.stringify(sequence),
          plan_name: "Default",
        }),
      });

      // Update local store with the saved sequence
      const storeKey = `${unit}||${date}`;
      unitSequenceStore[storeKey] = {
        sequence,
        status: "Draft",
      };
      console.log(`Saved and cached sequence for ${storeKey}:`, sequence.length, "items");
    }

    pendingArrangementUpdates.value = {};
    arrangementDirty.value = false;
    frappe.show_alert({ message: 'Arrangement saved permanently', indicator: 'green' });
    
    // DO NOT call fetchData() here - it would reset the sequences we just saved
    // Arrangement stays visible until user manually refreshes if they want to verify
  } catch (e) {
    console.error('Failed to save arrangement:', e);
    frappe.msgprint('Failed to save arrangement');
  } finally {
    arrangementSaving.value = false;
  }
}

async function restoreLastArrangement() {
  if (freezeArrangement.value) return;
  if (arrangementSaving.value || arrangementRestoring.value) return;
  if (!window.confirm("Restore previous saved row arrangement for current view?")) return;

  const pairs = [];
  (tableData.value || []).forEach((unitGroup) => {
    const unit = normalizeUnit(unitGroup.unit);
    (unitGroup.dateGroups || []).forEach((dg) => {
      const date = dg.date;
      if (unit && date && date !== "No Date") {
        pairs.push({ unit, date });
      }
    });
  });

  if (!pairs.length) {
    frappe.msgprint("No date/unit groups available to restore.");
    return;
  }

  arrangementRestoring.value = true;
  try {
    let restored = 0;
    for (const p of pairs) {
      const res = await frappe.call({
        method: "production_entry.production_planning.scheduler_api.restore_last_color_sequence",
        args: tableBoardArgs({ date: p.date, unit: p.unit, plan_name: "Default" }),
      });
      if (res?.message?.status === "success") restored += 1;
    }
    arrangementDirty.value = false;
    pendingArrangementUpdates.value = {};
    await fetchData();
    frappe.show_alert({ message: `Restored ${restored} arrangement snapshot(s).`, indicator: "green" });
  } catch (e) {
    console.error("Failed to restore arrangement:", e);
    frappe.msgprint("Failed to restore last arrangement.");
  } finally {
    arrangementRestoring.value = false;
  }
}

async function initTableSortables() {
  destroyTableSortables();
  if (tableReorderLocked.value) return;

  await nextTick();
  const bodies = document.querySelectorAll('.pt-sortable-body');

  bodies.forEach((tbody) => {
    const sortable = new Sortable(tbody, {
      animation: 200,
      easing: "cubic-bezier(1, 0, 0, 1)",
      handle: '.pt-drag-handle',
      draggable: '.pt-draggable-row',
      filter: '.pt-non-draggable',
      ghostClass: 'pt-drag-ghost',
      chosenClass: 'pt-drag-chosen',
      dragClass: 'pt-drag-dragging',
      scrollSensitivity: 30,
      scrollSpeed: 10,
      onStart: () => {
        document.body.style.cursor = 'grabbing';
      },
      onEnd: async (evt) => {
        document.body.style.cursor = 'default';
        try {
          await persistDateGroupOrder(evt.to);
          frappe.show_alert({ message: 'Arrangement changed. Click Save Arrangement.', indicator: 'orange' });
        } catch (e) {
          console.error('Failed to queue row order:', e);
          frappe.msgprint('Failed to stage new row order');
          await fetchData();
        }
      },
    });

    sortableInstances.value.push(sortable);
  });
}

async function toggleTableReorder() {
  if (freezeReorder.value) return;
  tableReorderLocked.value = !tableReorderLocked.value;

  if (tableReorderLocked.value) {
    destroyTableSortables();
    frappe.show_alert({ message: 'Row reorder locked', indicator: 'blue' });
    return;
  }

  await initTableSortables();
  frappe.show_alert({ message: 'Row reorder enabled', indicator: 'orange' });
}

// ===== ROLE-BASED VISIBILITY CONTROL =====

const RESTRICTED_ROLE_NAMES = [
  "Manufacturing User",
  "Manufacture User"
];
const PRIVILEGED_ROLE_NAMES = ["System Manager"];
const MERGE_EXPAND_ALLOWED_ROLES = ["System Manager", "Manufacturing Manager"];

function getCurrentUserRoles() {
  const roleSet = new Set();

  if (Array.isArray(frappe?.user_roles)) {
    frappe.user_roles.forEach((r) => roleSet.add(String(r || "").trim()));
  }

  const bootRoles = frappe?.boot?.user?.roles;
  if (Array.isArray(bootRoles)) {
    bootRoles.forEach((r) => {
      if (typeof r === "string") roleSet.add(r.trim());
      else if (r && typeof r === "object" && r.role) roleSet.add(String(r.role).trim());
    });
  }

  return Array.from(roleSet);
}

function detectRestrictedUser() {
  const currentUser = String(frappe?.session?.user || "").toLowerCase();
  if (currentUser === "administrator") return false;

  const roles = getCurrentUserRoles();
  if (roles.length) {
    const lower = roles.map((r) => r.toLowerCase());
    const isPrivileged = PRIVILEGED_ROLE_NAMES.some((r) => lower.includes(r.toLowerCase()));
    if (isPrivileged) return false;
    return RESTRICTED_ROLE_NAMES.some((r) => lower.includes(r.toLowerCase()));
  }

  try {
    if (frappe?.user?.has_role) {
      if (PRIVILEGED_ROLE_NAMES.some((r) => frappe.user.has_role(r))) return false;
      return RESTRICTED_ROLE_NAMES.some((r) => frappe.user.has_role(r));
    }
  } catch (e) {}

  try {
    if (frappe?.has_role) {
      if (PRIVILEGED_ROLE_NAMES.some((r) => frappe.has_role(r))) return false;
      return RESTRICTED_ROLE_NAMES.some((r) => frappe.has_role(r));
    }
  } catch (e) {}

  return false;
}

const canExpandMergedRows = computed(() => {
  const currentUser = String(frappe?.session?.user || "").toLowerCase();
  if (currentUser === "administrator") return true;

  const roles = getCurrentUserRoles().map((r) => r.toLowerCase());
  return MERGE_EXPAND_ALLOWED_ROLES.some((role) => roles.includes(role.toLowerCase()));
});

const boardUnits = computed(() => {
  let list = isRewindingBoard.value
    ? [...REWINDING_BOARD_UNITS]
    : isLaminationBoard.value
      ? [LAMINATION_UNIT]
      : isSlittingBoard.value
        ? [SLITTING_UNIT]
        : units;
  const ctx = boardAccessContext.value;
  if (ctx && ctx.loaded && !ctx.unlimited) {
    const allowed = ctx.allowed_units || [];
    if (!allowed.length) list = [];
    else list = list.filter((u) => unitAllowedByBoardAccess(u, allowed));
  }
  return list;
});

const visibleUnits = computed(() => {
  if (!filterUnit.value) return boardUnits.value;
  return boardUnits.value.filter((u) => u === filterUnit.value);
});

const filteredData = computed(() => {
  let data = rawData.value || [];
  
  // Only show items that have been pushed to Production Board
  data = data.filter(d => !!d.plannedDate);

  // Exclude missing parameters and NO COLOR
  data = data.filter(d => {

      const ic = String(d.itemCode || d.item_code || "").trim();
      const isFabricChild = ic.startsWith("100");
      if ((!d.unit || d.unit === "Mixed" || d.unit === "Unassigned")) return false;
      if (!isFabricChild && (!d.quality || !d.color)) return false;
      const colorUpper = d.color.toUpperCase().trim();
      if (colorUpper === "NO COLOR") return false;
      return true;
  });
  
  data = data.map(d => ({ ...d, unit: d.unit || "Mixed" }));

  if (filterPartyCode.value) {
    const search = filterPartyCode.value.toLowerCase();
    data = data.filter((d) =>
      (d.partyCode || "").toLowerCase().includes(search)
    );
  }
  if (filterCustomer.value) {
    const search = filterCustomer.value.toLowerCase();
    data = data.filter((d) =>
      (
        d.customer_name ||
        d.party_name ||
        d.customer ||
        d.partyCode ||
        d.party_code ||
        ""
      ).toLowerCase().includes(search)
    );
  }
  const ctx = boardAccessContext.value;
  if (ctx && ctx.loaded && !ctx.unlimited) {
    const allowed = ctx.allowed_units || [];
    if (!allowed.length) data = [];
    else data = data.filter((d) => unitAllowedByBoardAccess(d.unit, allowed));
  }
  return data;
});

const mergeDialogItems = computed(() => {
  let items = filteredData.value || [];

  // Merge dialog must follow active table scope, not full dataset.
  if (filterUnit.value) {
    items = items.filter((d) => (d.unit || "") === filterUnit.value);
  }

  if (viewScope.value === "daily" && filterOrderDate.value) {
    items = items.filter((d) => String(d.plannedDate || "") === String(filterOrderDate.value));
  }

  const orderSearch = mergeFilterOrderCode.value.trim().toLowerCase();
  const customerSearch = mergeFilterCustomer.value.trim().toLowerCase();
  const qualitySearch = mergeFilterQuality.value.trim().toLowerCase();
  const colorSearch = mergeFilterColor.value.trim().toLowerCase();

  if (orderSearch) {
    items = items.filter((d) => (d.partyCode || "").toLowerCase().includes(orderSearch));
  }
  if (customerSearch) {
    items = items.filter((d) => ((d.customer_name || d.customer || "").toLowerCase().includes(customerSearch)));
  }
  if (qualitySearch) {
    items = items.filter((d) => (d.quality || "").toLowerCase().includes(qualitySearch));
  }
  if (colorSearch) {
    items = items.filter((d) => (d.color || "").toLowerCase().includes(colorSearch));
  }

  return items.filter((d) => !mergedItemsMap.value[d.itemName]);
});

const autoMergeSuggestions = computed(() => {
  const grouped = {};
  (mergeDialogItems.value || []).forEach((item) => {
    const key = [item.partyCode || "", item.quality || "", item.color || "", item.unit || "", item.plannedDate || ""].join("||");
    if (!grouped[key]) {
      grouped[key] = {
        key,
        partyCode: item.partyCode || "",
        quality: item.quality || "",
        color: item.color || "",
        gsmSet: new Set(),
        unit: item.unit || "",
        plannedDate: item.plannedDate || "",
        items: [],
      };
    }
    grouped[key].items.push(item);
    grouped[key].gsmSet.add(String(item.gsm || "-"));
  });
  return Object.values(grouped)
    .map((g) => ({ ...g, gsmSummary: Array.from(g.gsmSet).sort((a, b) => Number(a) - Number(b)).join(",") }))
    .filter((g) => g.items.length >= 2)
    .sort((a, b) => b.items.length - a.items.length);
});

/**
 * Merged board rows often point at the same Shaft Production Run; each row may carry the
 * full SPR produced kg OR per-item-code WO/SPR weights (e.g. 29" + 2" on one SPR).
 * When weights differ under the same SPR, sum them; when identical duplicates, count once.
 */
function dedupeMergedActualProductionKg(items) {
  if (!items || !items.length) return 0;

  const allSprIds = new Set();
  items.forEach((it) => parseSprIds(it.spr_name).forEach((id) => allSprIds.add(id)));
  const sprIds = [...allSprIds];

  // Multiple distinct SPRs on merged group (e.g. SPR-A + SPR-B) — sum each SPR once.
  if (sprIds.length > 1) {
    const headerMax = Math.max(
      ...items.map((it) => parseFloat(it.spr_produced_total_kg ?? it.spr_total_produced_kg ?? 0) || 0),
      0
    );
    if (headerMax > 0) {
      return headerMax;
    }
    let total = 0;
    for (const sid of sprIds) {
      let best = 0;
      for (const it of items) {
        if (!parseSprIds(it.spr_name).includes(sid)) continue;
        best = Math.max(best, parseFloat(it.actual_production_weight_kgs) || 0);
      }
      total += best;
    }
    return total;
  }

  if (sprIds.length === 1) {
    const headerTotal = items.reduce(
      (m, it) =>
        Math.max(
          m,
          parseFloat(it.spr_produced_total_kg ?? it.spr_total_produced_kg ?? 0) || 0
        ),
      0
    );
    const partialWeights = items.map((it) => parseFloat(it.actual_production_weight_kgs) || 0);
    const partialSum = partialWeights.reduce((s, w) => s + (w > 0 ? w : 0), 0);
    const rounded = partialWeights.filter((w) => w > 0).map((w) => Math.round(w * 100) / 100);
    const uniqPartial = [...new Set(rounded)];

    if (headerTotal > 0) {
      // Split rows sharing one SPR may each carry the full produced kg — count SPR once.
      if (partialSum > headerTotal * 1.01) {
        return headerTotal;
      }
      if (uniqPartial.length <= 1 && uniqPartial[0] > 0) {
        return Math.max(uniqPartial[0], headerTotal);
      }
      if (partialSum + 0.05 < headerTotal) {
        return headerTotal;
      }
      return partialSum;
    }
    if (uniqPartial.length <= 1) {
      return uniqPartial[0] || 0;
    }
    return partialSum;
  }
  const bySpr = new Map();
  let noSprSum = 0;
  for (const it of items) {
    const w = parseFloat(it.actual_production_weight_kgs) || 0;
    const ids = parseSprIds(it.spr_name);
    if (ids.length) {
      for (const spr of ids) {
        if (!bySpr.has(spr)) {
          bySpr.set(spr, []);
        }
        bySpr.get(spr).push(w);
      }
    } else {
      noSprSum += w;
    }
  }
  let total = noSprSum;
  for (const weights of bySpr.values()) {
    const rounded = weights.map((w) => Math.round(w * 100) / 100);
    const uniq = [...new Set(rounded.filter((w) => w > 0))];
    if (uniq.length <= 1) {
      total += uniq[0] || 0;
    } else {
      total += rounded.reduce((s, w) => s + w, 0);
    }
  }
  return total;
}

const selectedMergeSummary = computed(() => {
  const selectedItems = (mergeDialogItems.value || []).filter((it) => selectedMergeItems.value.has(it.itemName));
  const targetWeight = selectedItems.reduce((sum, it) => sum + (parseFloat(it.qty) || 0), 0);
  const actualWeight = dedupeMergedActualProductionKg(selectedItems);
  return {
    count: selectedItems.length,
    targetWeight,
    actualWeight,
  };
});

function getMergeById(mergeId) {
  return (merges.value || []).find((m) => m.name === mergeId);
}

function isMergeExpanded(mergeId) {
  return expandedMerges.value.has(mergeId);
}

function toggleMergeExpanded(mergeId) {
  if (!canExpandMergedRows.value) return;
  if (expandedMerges.value.has(mergeId)) expandedMerges.value.delete(mergeId);
  else expandedMerges.value.add(mergeId);
}

const tableData = computed(() => {
    return visibleUnits.value.map(unit => {
        let items = filteredData.value.filter(d => normalizeUnit(d.unit || "Mixed") === unit);
        
        const dateGroupsObj = {};
        items.forEach(item => {
            const d = item.plannedDate || "No Date";
            if (!dateGroupsObj[d]) dateGroupsObj[d] = { date: d, items: [], dailyTotal: 0 };
            dateGroupsObj[d].items.push(item);
            dateGroupsObj[d].dailyTotal += (item.qty || 0);
        });

        const dates = Object.values(dateGroupsObj).sort((a, b) => new Date(a.date) - new Date(b.date));
        
        // Sort each date group individually using Board's exact queuing for that day
        dates.forEach(group => {
            group.items = sortItems(unit, group.items, group.date);
            const mergeSeenForDaily = new Set();
            group.dailyActualTotal = group.items.reduce((sum, item) => {
              const mergeId = mergedItemsMap.value[item.itemName];
              if (!mergeId) {
                return sum + (parseFloat(item.actual_production_weight_kgs) || 0);
              }
              if (mergeSeenForDaily.has(mergeId)) return sum;
              mergeSeenForDaily.add(mergeId);
              const mergeItems = (group.items || []).filter((it) => mergedItemsMap.value[it.itemName] === mergeId);
              return sum + dedupeMergedActualProductionKg(mergeItems);
            }, 0);

            const seenMerges = new Set();
            const rows = [];
            group.items.forEach((item) => {
              const mergeId = mergedItemsMap.value[item.itemName];
              if (!mergeId) {
                rows.push({ type: "item", rowKey: `item-${item.itemName}`, item });
                return;
              }
              if (seenMerges.has(mergeId)) return;
              seenMerges.add(mergeId);

              const merge = getMergeById(mergeId);
              const mergeItems = (group.items || []).filter((it) => mergedItemsMap.value[it.itemName] === mergeId);
              const totalTargetWeight = mergeItems.reduce((s, it) => s + (parseFloat(it.qty) || 0), 0);
              const totalActualWeight = dedupeMergedActualProductionKg(mergeItems);
              const hasDispatchLock = mergeItems.some((it) => ["Partly Delivered", "Fully Delivered"].includes(String(it.delivery_status || "")));
              const statuses = mergeItems.map((it) => String(it.delivery_status || "Not Delivered"));
              const mergeDispatchStatus = statuses.every((s) => s === "Fully Delivered")
                ? "Fully Delivered"
                : statuses.some((s) => s === "Partly Delivered" || s === "Fully Delivered")
                  ? "Partly Delivered"
                  : "Not Delivered";
              const first = mergeItems[0] || item;
              const customer = first.customer_name || first.customer || "-";
              const gsmSummary = Array.from(new Set(mergeItems.map((it) => String(it.gsm || "-")).filter(Boolean)))
                .sort((a, b) => Number(a) - Number(b))
                .join(",");
              const displayLabel = `${customer}(${mergeItems.length}items)`;
              const sprIdSet = new Set();
              mergeItems.forEach((it) => parseSprIds(it.spr_name).forEach((id) => sprIdSet.add(id)));
              const spr_name = [...sprIdSet].join(", ");
              const sprItem = spr_name
                ? mergeItems.find((it) => parseSprIds(it.spr_name).length > 0) || mergeItems[0]
                : null;
              const spr_docstatus = sprItem != null ? sprItem.spr_docstatus : null;
              const mergeAnyWoOpen = mergeItems.some((it) => it.wo_open);
              const mergeAllWoTerminal = mergeItems.length > 0 && mergeItems.every((it) => it.wo_terminal);
              const gapKg = Math.max(totalTargetWeight - totalActualWeight, 0);
              const mergeMaxPendingKg = Math.max(
                mergeItems.reduce(
                  (m, it) =>
                    Math.max(
                      m,
                      parseFloat(it.pp_pending_qty ?? it.pending_qty ?? it.item_pending_qty ?? 0) || 0
                    ),
                  0
                ),
                gapKg
              );
              const { dayShiftRuns, nightShiftRuns } = mergeShiftRuns(mergeItems);
              rows.push({
                type: "merge",
                rowKey: `merge-${mergeId}`,
                mergeId,
                mergeLabel: (merge && merge.merge_label) || `Merge ${mergeItems.length}`,
                displayLabel,
                items: mergeItems,
                partyCode: first.partyCode,
                customer,
                quality: first.quality,
                color: first.color,
                gsm: gsmSummary,
                totalTargetWeight,
                totalActualWeight,
                hasDispatchLock,
                mergeDispatchStatus,
                pp_id: first.pp_id || "",
                pp_docstatus: first.pp_docstatus,
                spr_name,
                spr_docstatus,
                mergeAnyWoOpen,
                mergeAllWoTerminal,
                mergeMaxPendingKg,
                dayShiftRuns,
                nightShiftRuns,
              });
            });

            group.rows = rows;
        });

        placeMaintenanceCards(unit, dates);

        const totalWeight = items.reduce((s, i) => s + (i.qty || 0), 0) / 1000;

        return { unit, dates, totalWeight };
    });
});

function formatDate(dateStr) {
    if (!dateStr || dateStr === 'No Date') return '-';
    const d = new Date(dateStr);
    return `${d.getDate()}/${d.getMonth()+1}/${d.getFullYear()}`;
}

function getDayName(dateStr) {
    if (!dateStr || dateStr === 'No Date') return '-';
    const d = new Date(dateStr);
    return d.toLocaleDateString('en-US', { weekday: 'long' }).toUpperCase();
}

function getUnitHeaderColor(unit) {
    return "#fcd34d"; 
}

function formatKg(value) {
  return formatKgPlanning(value);
}

function formatKg2(value) {
  const num = parseFloat(value || 0);
  if (!Number.isFinite(num)) return "0.00";
  return num.toFixed(2);
}

function formatWidth(value) {
  const num = parseFloat(value);
  if (!Number.isFinite(num) || num <= 0) return "-";
  return `${num} Inches`;
}

/** Status pills: SPR docstate (draft vs submitted). */
function sprPillLabel(item) {
  if (!item?.spr_name) return "";
  if (item.spr_docstatus === 0 || item.spr_docstatus === "0") return "Draft";
  if (Number(item.spr_docstatus) === 1) return "Submitted";
  return "SPR";
}
function sprPillClass(item) {
  if (!item?.spr_name) return "pt-pill-muted";
  if (item.spr_docstatus === 0 || item.spr_docstatus === "0") return "pt-pill-draft";
  if (Number(item.spr_docstatus) === 1) return "pt-pill-submitted";
  return "pt-pill-muted";
}
function sprPillTitle(item) {
  if (!item?.spr_name) return "";
  const id = item.spr_name || "";
  if (item.spr_docstatus === 0 || item.spr_docstatus === "0") {
    return `Draft Shaft Production Run ${id}. Submit in the SPR form when you finish recording rolls for this run.`;
  }
  if (Number(item.spr_docstatus) === 1) {
    return `Submitted SPR ${id}. Production is posted; open to review.`;
  }
  return id;
}

function woPillLabelItem(item) {
  if (!item) return "";
  if (item.wo_terminal) return "WO done";
  if (item.wo_open) return "WO open";
  return "WO";
}
function woPillClassItem(item) {
  if (item.wo_terminal) return "pt-pill-wo-done";
  if (item.wo_open) return "pt-pill-wo-open";
  return "pt-pill-wo-unknown";
}
function woPillTitleItem(item) {
  if (!item) return "";
  if (item.wo_terminal) return "All work orders for this Production Plan are closed or terminal.";
  if (item.wo_open) return "At least one work order is still open — more production can be recorded if allowed.";
  return "Work order status from the linked Production Plan.";
}

function sprPillLabelMerge(row) {
  if (!row?.spr_name) return "";
  if (row.spr_docstatus === 0 || row.spr_docstatus === "0") return "Draft";
  if (Number(row.spr_docstatus) === 1) return "Submitted";
  return "SPR";
}
function sprPillClassMerge(row) {
  if (!row?.spr_name) return "pt-pill-muted";
  if (row.spr_docstatus === 0 || row.spr_docstatus === "0") return "pt-pill-draft";
  if (Number(row.spr_docstatus) === 1) return "pt-pill-submitted";
  return "pt-pill-muted";
}
function sprPillTitleMerge(row) {
  if (!row?.spr_name) return "";
  const id = row.spr_name || "";
  if (row.spr_docstatus === 0 || row.spr_docstatus === "0") return `Draft SPR ${id} for merged group.`;
  if (Number(row.spr_docstatus) === 1) return `Submitted SPR ${id}.`;
  return id;
}
function woPillLabelMerge(row) {
  if (!row) return "";
  if (row.mergeAllWoTerminal) return "WO done";
  if (row.mergeAnyWoOpen) return "WO open";
  return "WO";
}
function woPillClassMerge(row) {
  if (row.mergeAllWoTerminal) return "pt-pill-wo-done";
  if (row.mergeAnyWoOpen) return "pt-pill-wo-open";
  return "pt-pill-wo-unknown";
}
function woPillTitleMerge(row) {
  if (!row) return "";
  if (row.mergeAllWoTerminal) return "All merged lines: work orders are closed or terminal.";
  if (row.mergeAnyWoOpen) return "At least one merged line still has an open work order.";
  return "Aggregated WO state for this merge.";
}

/** One-line kg hint under pills (target vs actual on this row). */
function itemProductionStatusLine(item) {
  if (!item) return "";
  const t = parseFloat(item.qty) || 0;
  const a = parseFloat(item.actual_production_weight_kgs) || 0;
  const gap = t - a;
  if (Math.abs(gap) <= 0.5) return "";
  return gap > 0 ? `${formatKg2(gap)} kg below target` : `${formatKg2(-gap)} kg over target`;
}

function itemProductionStatusTitle(item) {
  const line = itemProductionStatusLine(item);
  const p = Number(item.pp_pending_qty ?? item.pending_qty ?? item.item_pending_qty ?? 0);
  const extra =
    p > 0
      ? ` System pending (PP): ${formatKg2(p)} kg. Buttons open or create a Shaft Production Run — not a generic Stock Entry list.`
      : " No pending qty on this line; check PP/SPR if you need more production.";
  return (line || "Production vs target") + extra;
}

function itemTargetGapKg(item) {
  const t = parseFloat(item?.qty) || 0;
  const a = parseFloat(item?.actual_production_weight_kgs ?? item?.total_achieved_weight_kgs) || 0;
  return t - a;
}

function itemRemainingKg(item) {
  if (!item) return 0;
  const pendingKg = Number(item.pp_pending_qty ?? item.pending_qty ?? item.item_pending_qty ?? 0);
  const gap = itemTargetGapKg(item);
  if (gap > 0.5) return Math.max(pendingKg, gap);
  return pendingKg;
}

function mergeTargetGapKg(row) {
  const t = parseFloat(row.totalTargetWeight) || 0;
  const a = parseFloat(row.totalActualWeight) || 0;
  return t - a;
}

function mergeProductionStatusLine(row) {
  if (!row || row.type !== "merge") return "";
  const g = mergeTargetGapKg(row);
  if (Math.abs(g) <= 0.5) return "";
  return g > 0 ? `${formatKg2(g)} kg below target` : `${formatKg2(-g)} kg over target`;
}

function mergeProductionStatusTitle(row) {
  const line = mergeProductionStatusLine(row);
  const p = Number(row.mergeMaxPendingKg || 0);
  const base =
    line ||
    "Merged row: WO/PP status is aggregated from the lines in this merge. Same SPR counts once toward Actual.";
  const pend =
    p > 0
      ? ` System pending total (max across lines): ${formatKg2(p)} kg. Partial entry today + more production tomorrow is normal — use Continue SPR while draft exists.`
      : "";
  return base + pend;
}

function mergedStockPrimaryLabel(row) {
  if (!row || row.type !== "merge") return "New SPR";
  const isDraftSpr = !!row.spr_name && (row.spr_docstatus === 0 || row.spr_docstatus === "0");
  if (isDraftSpr) return "Continue SPR";
  return "New SPR";
}

function mergedStockPrimaryTitle(row) {
  if (!row || row.type !== "merge") return "";
  const pending = mergedRemainingKg(row);
  const isDraftSpr = !!row.spr_name && (row.spr_docstatus === 0 || row.spr_docstatus === "0");
  if (!row.spr_name) {
    return "Create the first Shaft Production Run for this merged group (one SPR can cover all merged lines).";
  }
  if (isDraftSpr) {
    return `Open draft SPR — add rolls, Submit when finished. Pending: ${formatKg2(pending)} kg.`;
  }
  return `Start another Shaft Production Run for remaining production (e.g. next shift). Pending: ${formatKg2(pending)} kg. Night shift SPR stays submitted — this creates a new run for morning balance.`;
}

/** Primary action when pending Stock Entry column is hidden (no pending PP qty). */
function itemSprPrimaryButtonLabel(item) {
  if (!item?.spr_name) return "";
  if (item.spr_docstatus === 0 || item.spr_docstatus === "0") return "Open draft SPR";
  if (item.wo_terminal) return "View SPR (done)";
  return "View SPR";
}

function itemSprPrimaryButtonTitle(item) {
  if (!item?.spr_name) return "";
  if (item.spr_docstatus === 0 || item.spr_docstatus === "0") return "Draft SPR — continue recording rolls, then Submit.";
  if (item.wo_terminal) {
    return "All work orders on this Production Plan are in a closed/terminal status and this SPR is submitted — shop-floor entry is complete. Open to review the document only.";
  }
  return "Submitted SPR — open to review. WO may still be open if more production is planned.";
}

function mergedSprPrimaryButtonLabel(row) {
  if (!row?.spr_name) return "";
  if (row.spr_docstatus === 0 || row.spr_docstatus === "0") return "Open draft SPR";
  if (row.mergeAllWoTerminal) return "View SPR (done)";
  return "View SPR";
}

function mergedSprPrimaryButtonTitle(row) {
  if (!row?.spr_name) return mergedStockPrimaryTitle(row);
  if (row.spr_docstatus === 0 || row.spr_docstatus === "0") {
    return "Draft SPR for merged group — same SPR can cover all merged lines when linked.";
  }
  if (row.mergeAllWoTerminal) {
    return "All linked WOs are terminal and this SPR is submitted — merged entry is complete. Open to review only.";
  }
  return mergedStockPrimaryTitle(row);
}

function getMergeRuleKey(item) {
  return [
    String(item?.partyCode || "").trim().toUpperCase(),
    String(item?.quality || "").trim().toUpperCase(),
    String(item?.color || "").trim().toUpperCase(),
    String(item?.unit || "").trim().toUpperCase(),
    String(item?.plannedDate || "").trim(),
  ].join("||");
}

function formatDispatchStatus(status) {
    if (!status || status === 'Not Delivered') return 'NOT DESPATCHED';
    if (status === 'Fully Delivered') return 'DESPATCHED';
    if (status === 'Partly Delivered') return 'PARTLY DESPATCHED';
    return status.toUpperCase();
}

function getDispatchStatusClass(status) {
    if (!status || status === 'Not Delivered') return 'bg-red-100 text-red-800';
    if (status === 'Fully Delivered') return 'bg-green-100 text-green-800';
    if (status === 'Partly Delivered') return 'bg-orange-100 text-orange-800';
    return 'bg-gray-100 text-gray-800';
}

function toggleMergeMode() {
  if (freezeMerge.value) return;
  mergeMode.value = !mergeMode.value;
  if (mergeMode.value) {
    showMergeDialog.value = true;
    tableReorderLocked.value = true;
    destroyTableSortables();
  } else {
    closeMergeDialog();
  }
}

function closeMergeDialog() {
  showMergeDialog.value = false;
  mergeMode.value = false;
  selectedMergeItems.value = new Set();
}

function openMergedProductionPlan(row) {
  const planningSheets = Array.from(new Set((row.items || []).map((it) => it.planningSheet).filter(Boolean)));
  if (!planningSheets.length) {
    frappe.msgprint("No Planning Sheet found for this merged row");
    return;
  }
  if (planningSheets.length > 1) {
    frappe.show_alert({ message: `Multiple planning sheets in merge. Opening first: ${planningSheets[0]}`, indicator: 'orange' });
  }
  const firstItem = (row.items || [])[0] || {};
  openProductionPlanView(planningSheets[0], firstItem.salesOrderItem, firstItem.itemName, firstItem.pp_id);
}

function toggleMergeSelection(itemName) {
  const next = new Set(selectedMergeItems.value);
  if (next.has(itemName)) next.delete(itemName);
  else next.add(itemName);
  selectedMergeItems.value = next;
}

function applyAutoMergeSuggestion() {
  const top = autoMergeSuggestions.value[0];
  if (!top) {
    frappe.msgprint("No auto merge suggestions available.");
    return;
  }
  const next = new Set(selectedMergeItems.value);
  top.items.forEach((item) => next.add(item.itemName));
  selectedMergeItems.value = next;
}

function selectSuggestion(suggestion) {
  const next = new Set(selectedMergeItems.value);
  suggestion.items.forEach((item) => next.add(item.itemName));
  selectedMergeItems.value = next;
}

async function loadMergesForCurrentData() {
  const dates = Array.from(new Set((filteredData.value || []).map((d) => d.plannedDate).filter(Boolean)));
  const all = [];
  for (const date of dates) {
    try {
      const res = await frappe.call({
        method: "production_entry.production_planning.scheduler_api.get_merges_for_date",
        args: tableBoardArgs({
          date,
          unit: filterUnit.value || null,
          plan_name: "Default",
        }),
      });
      if (Array.isArray(res.message)) {
        res.message.forEach((m) => all.push(m));
      }
    } catch (e) {
      console.warn("Failed to load merge records", e);
    }
  }
  merges.value = all;
  const map = {};
  all.forEach((m) => {
    const mergedItems = Array.isArray(m.merged_items) ? m.merged_items : [];
    mergedItems.forEach((itemName) => {
      map[itemName] = m.name;
    });
  });
  mergedItemsMap.value = map;
}

async function createMergeFromDialog() {
  const selectedItems = (mergeDialogItems.value || []).filter((it) => selectedMergeItems.value.has(it.itemName));
  if (selectedItems.length < 2) {
    frappe.msgprint("Select at least 2 items to merge.");
    return;
  }

  const groupedByKey = {};
  selectedItems.forEach((it) => {
    const key = getMergeRuleKey(it);
    if (!groupedByKey[key]) groupedByKey[key] = [];
    groupedByKey[key].push(it);
  });
  const groupedSelections = Object.values(groupedByKey);

  if (groupedSelections.length > 1) {
    let success = 0;
    let failed = 0;

    for (const groupItems of groupedSelections) {
      if ((groupItems || []).length < 2) {
        failed += 1;
        continue;
      }

      const firstGroupItem = groupItems[0];
      const groupGsmSummary = Array.from(new Set(groupItems.map((it) => String(it.gsm || "-")).filter(Boolean)))
        .sort((a, b) => Number(a) - Number(b))
        .join(",");
      const groupLabel = `${firstGroupItem.partyCode || ''}, ${firstGroupItem.customer_name || firstGroupItem.customer || '-'}, ${firstGroupItem.quality || ''}, ${firstGroupItem.color || ''}, GSM: ${groupGsmSummary}`;

      const ok = await createMergeForItems(groupItems, groupLabel);
      if (ok) success += 1;
      else failed += 1;
    }

    await loadMergesForCurrentData();
    frappe.show_alert({ message: `Merge created: ${success}, failed: ${failed}`, indicator: failed ? "orange" : "green" });
    if (success > 0) closeMergeDialog();
    return;
  }

  const first = selectedItems[0];
  const sameSlot = selectedItems.every((it) => it.unit === first.unit && it.plannedDate === first.plannedDate);
  if (!sameSlot) {
    frappe.msgprint("Please select items from same Unit and Planned Date.");
    return;
  }

  const totalSelectedKg = selectedItems.reduce((s, it) => s + (parseFloat(it.qty) || 0), 0);
  const unitLimitKg = (UNIT_TONNAGE_LIMITS[first.unit] || 999) * 1000;
  if (totalSelectedKg > unitLimitKg) {
    frappe.msgprint(`Selected merge weight ${formatKg(totalSelectedKg)} Kg exceeds ${first.unit} capacity ${formatKg(unitLimitKg)} Kg`);
    return;
  }

  const selectedGsmSummary = Array.from(new Set(selectedItems.map((it) => String(it.gsm || "-")).filter(Boolean)))
    .sort((a, b) => Number(a) - Number(b))
    .join(",");

  const label = window.prompt(
    "Merge label",
    `${first.partyCode || ''}, ${first.customer_name || first.customer || '-'}, ${first.quality || ''}, ${first.color || ''}, GSM: ${selectedGsmSummary}`
  ) || "";
  if (!label.trim()) return;

  const ok = await createMergeForItems(selectedItems, label.trim());
  if (ok) {
    await loadMergesForCurrentData();
    frappe.show_alert({ message: "Merge created", indicator: "green" });
    closeMergeDialog();
  }
}

async function createMergeForItems(selectedItems, label) {
  if (!selectedItems || selectedItems.length < 2) {
    frappe.msgprint("Select at least 2 items to merge.");
    return false;
  }

  const first = selectedItems[0];
  const sameSlot = selectedItems.every((it) => it.unit === first.unit && it.plannedDate === first.plannedDate);
  if (!sameSlot) {
    frappe.msgprint("Please select items from same Unit and Planned Date.");
    return false;
  }

  const mergeKeys = new Set(selectedItems.map(getMergeRuleKey));
  if (mergeKeys.size > 1) {
    frappe.msgprint("Cannot merge different Order Code / Quality / Color groups together.");
    return false;
  }

  const totalSelectedKg = selectedItems.reduce((s, it) => s + (parseFloat(it.qty) || 0), 0);
  const unitLimitKg = (UNIT_TONNAGE_LIMITS[first.unit] || 999) * 1000;
  if (totalSelectedKg > unitLimitKg) {
    frappe.msgprint(`Selected merge weight ${formatKg(totalSelectedKg)} Kg exceeds ${first.unit} capacity ${formatKg(unitLimitKg)} Kg`);
    return false;
  }

  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.create_merge",
      args: tableBoardArgs({
        date: first.plannedDate,
        unit: first.unit,
        plan_name: "Default",
        item_names: JSON.stringify(selectedItems.map((it) => it.itemName)),
        merge_label: label,
      }),
    });
    if (res.message && res.message.status === "success") {
      return true;
    }
  } catch (e) {
    frappe.msgprint(e?.message || "Unable to create merge");
  }
  return false;
}

async function createAllSuggestedMerges() {
  const suggestions = autoMergeSuggestions.value || [];
  if (!suggestions.length) {
    frappe.msgprint("No suggested groups found.");
    return;
  }

  let success = 0;
  let failed = 0;

  for (const s of suggestions) {
    const items = (s.items || []).filter(Boolean);
    if (items.length < 2) {
      failed += 1;
      continue;
    }

    const label = `${s.partyCode || ''}, ${items[0]?.customer_name || items[0]?.customer || '-'}, ${s.quality || ''}, ${s.color || ''}, GSM: ${s.gsmSummary || '-'}`;
    const ok = await createMergeForItems(items, label);
    if (ok) success += 1;
    else failed += 1;
  }

  await loadMergesForCurrentData();
  frappe.show_alert({ message: `Merge created: ${success}, failed: ${failed}`, indicator: failed ? "orange" : "green" });
  if (success > 0) closeMergeDialog();
}

async function deleteMerge(mergeId) {
  if (!mergeId) return;
  if (!window.confirm("Remove this merge and restore individual rows?")) return;
  try {
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.delete_merge",
      args: tableBoardArgs({ merge_id: mergeId }),
    });
    if (res.message && res.message.status === "success") {
      frappe.show_alert({ message: "Merge removed", indicator: "orange" });
      await loadMergesForCurrentData();
    }
  } catch (e) {
    frappe.msgprint("Unable to remove merge");
  }
}

async function openProductionPlanView(planningSheetName, salesOrderItem = null, planningSheetItem = null, directPpId = null) {
  if (!planningSheetName) {
    frappe.msgprint("Planning Sheet not found for this order");
    return;
  }
  
  try {
    // STRICT PRIORITY: Item-level PP ID overrides everything. NO fallback allowed.
    let ppId = String(directPpId || "").trim();
    
    console.log("openProductionPlanView - STRICT mode:", {
      planningSheetName,
      planningSheetItem,
      directPpId,
      ppIdTrimmed: ppId,
      usingItemLevelPP: !!ppId
    });
    
    // If item-level PP is provided, use it directly and open immediately
    // WITHOUT calling API fallback, which might return different PP from Planning Sheet level
    if (ppId) {
      console.log("✅ Using ITEM-LEVEL PP ID directly (NO API fallback):", ppId);
      openProductionPlanPrintPreview(ppId);
      return;
    }
    
    // Only if NO item-level PP provided, fallback to API resolution (sheet-level or SO-level)
    console.warn("No item-level PP provided. Using API fallback for sheet:", planningSheetName);
    const res = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_planning_sheet_pp_id",
      args: {
        planning_sheet_name: planningSheetName,
        sales_order_item: salesOrderItem,
        planning_sheet_item: planningSheetItem,
      }
    });
    
    if (res.message && res.message.status === "ok") {
      ppId = String(res.message.pp_id || "").trim();
      console.log("📌 API resolved PP (fallback):", ppId);
      
      if (ppId) {
        openProductionPlanPrintPreview(ppId);
      } else {
        frappe.msgprint("No Production Plan found for this item");
      }
    } else {
      const errorMsg = res.message?.message || "Error fetching Production Plan";
      frappe.msgprint(errorMsg);
    }
  } catch (e) {
    frappe.msgprint("Error opening Production Plan");
    console.error(e);
  }
}

function canShowStockEntry(item) {
  if (!item || !item.pp_id) return false;
  if (Number(item.pp_docstatus) !== 1) return false;

  const isDraftSpr = !!item.spr_name && (item.spr_docstatus === 0 || item.spr_docstatus === "0");
  if (isDraftSpr) return true;

  if (item.wo_terminal) return false;

  const remainingKg = itemRemainingKg(item);
  return remainingKg > 0.5;
}

function shouldShowItemViewSpr(item) {
  if (!item?.spr_name) return false;
  const isDraft = item.spr_docstatus === 0 || item.spr_docstatus === "0";
  return !isDraft;
}

function canShowMergedStockEntry(row) {
  if (!row || row.type !== "merge") return false;
  if (Number(row.pp_docstatus) !== 1) return false;
  if (row.mergeAllWoTerminal) return false;

  const isDraftSpr = !!row.spr_name && (row.spr_docstatus === 0 || row.spr_docstatus === "0");
  if (isDraftSpr) return true;

  const remainingKg = mergedRemainingKg(row);
  return remainingKg > 0.5;
}

function mergedRemainingKg(row) {
  if (!row || row.type !== "merge") return 0;
  const pendingKg = Number(row.mergeMaxPendingKg ?? 0);
  const gap = mergeTargetGapKg(row);
  if (gap > 0.5) return Math.max(pendingKg, gap);
  return pendingKg;
}

function shouldShowMergedViewSpr(row) {
  if (!row?.spr_name) return false;
  const isDraft = row.spr_docstatus === 0 || row.spr_docstatus === "0";
  return !isDraft;
}

async function handleMergedStockEntryAction(row) {
  if (!row) return;
  const isDraftSpr = !!row.spr_name && (row.spr_docstatus === 0 || row.spr_docstatus === "0");
  if (isDraftSpr) {
    await openMergedSPR(row.spr_name, row);
    return;
  }
  createMergedStockEntry(row);
}

function getStockEntryLabel(item) {
  if (!item) return "New SPR";
  const isDraftSpr = !!item.spr_name && (item.spr_docstatus === 0 || item.spr_docstatus === "0");
  if (isDraftSpr) {
    return `Continue SPR${item.spr_unit ? " · " + item.spr_unit : ""}`;
  }
  return "New SPR";
}

function getStockEntryTitle(item) {
  if (!item) return "Create a Shaft Production Run for this line";
  const isDraftSpr = !!item.spr_name && (item.spr_docstatus === 0 || item.spr_docstatus === "0");
  const pending = itemRemainingKg(item);
  if (isDraftSpr) {
    return `Open draft SPR — add rolls, Submit when finished. Pending: ${formatKg2(pending)} kg.`;
  }
  return `Start another Shaft Production Run for remaining production (e.g. next shift). Pending: ${formatKg2(pending)} kg.`;
}

async function handleStockEntryAction(item) {
  if (!item) return;
  const isDraftSpr = !!item.spr_name && (item.spr_docstatus === 0 || item.spr_docstatus === "0");
  if (isDraftSpr) {
    await openItemSPR(item.spr_name, item);
    return;
  }
  createItemStockEntry(item);
}

function syncSprNameForSamePP(ppId, sprId, sourceItemName = "") {
  const pid = String(ppId || "").trim();
  const sid = String(sprId || "").trim();
  if (!pid || !sid) return;

  (rawData.value || []).forEach((row) => {
    if (
      String(row.pp_id || "").trim() === pid &&
      (!sourceItemName || String(row.itemName || "") === String(sourceItemName || ""))
    ) {
      row.spr_name = mergeSprCsv(row.spr_name, sid);
    }
  });
}

async function createItemStockEntry(item) {
  if (item.__creating_spr) {
    return;
  }

  // Detailed debug logging
  console.log("createItemStockEntry called with item:", {
    itemName: item.itemName,
    pp_id: item.pp_id,
    partyCode: item.partyCode,
    color: item.color
  });
  // Resolve pp_id if missing but planningSheet exists
  if (!item.pp_id && item.planningSheet) {
    try {
      const ppRes = await frappe.call({
        method: "production_entry.production_planning.scheduler_api.get_planning_sheet_pp_id",
        args: {
          planning_sheet_name: item.planningSheet,
          sales_order_item: item.salesOrderItem || null,
          planning_sheet_item: item.itemName || null,
        }
      });
      if (ppRes.message && ppRes.message.status === "ok" && ppRes.message.pp_id) {
        item.pp_id = ppRes.message.pp_id;
        console.log(`Resolved PP for ${item.itemName}: ${item.pp_id}`);
      }
    } catch (e) {
      console.warn("Could not resolve PP for item", item.itemName, e);
    }
  }

  if (!item.pp_id) {
    frappe.msgprint("❌ No Production Plan linked to this item.<br/>Item Details:<br/>Code: " + item.partyCode + "<br/>Color: " + item.color);
    return;
  }
  
  if (!item.itemName) {
    frappe.msgprint("❌ Item Name missing - cannot create Stock Entry");
    return;
  }
  
  // Check if PP exists and has WOs started
  try {
    const ppCheckRes = await frappe.call({
      method: "frappe.client.get",
      args: {
        doctype: "Production Plan",
        name: item.pp_id
      }
    });
    
    if (!ppCheckRes.message) {
      frappe.msgprint(`❌ Production Plan '${item.pp_id}' not found in system.<br/>Please verify PP exists.`);
      console.error("PP not found:", item.pp_id);
      return;
    }
    
    const pp = ppCheckRes.message;
    console.log("Found PP:", pp.name);
    
    // Check if PP has Work Orders
    const woCheckRes = await frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: "Work Order",
        filters: { "production_plan": item.pp_id, "docstatus": ["<", 2] },
        limit_page_length: 1
      }
    });
    
    if (!woCheckRes.message || woCheckRes.message.length === 0) {
      frappe.msgprint(`⚠️ Production Plan '${item.pp_id}' has no started Work Orders.<br/>Please start production (create Work Orders) first.`);
      console.warn("No WO for PP:", item.pp_id);
      return;
    }
    
    console.log("Found WO for PP:", item.pp_id);
  } catch (e) {
    console.warn("Could not validate PP, proceeding anyway", e);
  }
  
  const itemDisplay = getItemDisplayName(item);

  frappe.confirm(
    `Create Stock Entry for <b>${item.partyCode}</b> (${item.color})?<br/>PP: ${item.pp_id}<br/>Item: ${itemDisplay}`,
    async () => {
      item.__creating_spr = true;
      try {
        console.log("Calling create_item_spr with:", {
          pp_id: item.pp_id,
          planning_sheet_item_names: [item.itemName]
        });
        
        const res = await frappe.call({
          method: "production_entry.production_planning.scheduler_api.create_item_spr",
          args: {
            pp_id: item.pp_id,
            planning_sheet_item_names: JSON.stringify([item.itemName])
          }
        });
        
        console.log("create_item_spr response:", res);
        console.log("Response status:", res.message?.status);
        console.log("Response message:", res.message?.message);
        console.log("Response full:", JSON.stringify(res.message));
        
        if (res.message && res.message.status === "ok") {
          const sprId = res.message.spr_id;
          item.spr_name = mergeSprCsv(item.spr_name, sprId);
          syncSprNameForSamePP(item.pp_id, sprId, item.itemName);
          const reused = !!res.message.reused;
          
          frappe.show_alert({
            message: reused ? `✅ Using existing SPR: ${sprId}. Opening form...` : `✅ SPR Created: ${sprId}. Opening form...`,
            indicator: 'green'
          }, 3);
          
          // Set flag for WO popup on SPR form
          frappe.flags.spr_show_wo_popup = item.pp_id;

          await new Promise(resolve => {
            setTimeout(() => {
              frappe.set_route('Form', 'Shaft Production Run', sprId);
              resolve();
            }, 800);
          });
        } else {
          const msg = res.message?.message || JSON.stringify(res.message) || "Failed to create SPR";
          console.error("SPR creation failed - Full response:", res.message);
          frappe.msgprint(`❌ Error: ${msg}`);
        }
      } catch (e) {
        console.error("Exception in createItemStockEntry:", e);
        frappe.msgprint(`❌ Error creating Stock Entry: ${e.message || e}`);
      } finally {
        item.__creating_spr = false;
      }
    }
  );
}

function getItemDisplayName(item) {
  if (!item) return "-";
  const semanticName = [item.quality, item.color, item.gsm].filter(Boolean).join(" ").trim();
  return (
    item.description ||
    item.item_name ||
    item.itemCode ||
    item.item_code ||
    semanticName ||
    item.itemName ||
    "-"
  );
}

async function createMergedStockEntry(mergedRow) {
  if (!mergedRow || !mergedRow.items || mergedRow.items.length === 0) {
    frappe.msgprint("No items in merged row");
    return;
  }
  
  // Resolve missing pp_id for items that have a planningSheet but no pp_id
  for (const item of mergedRow.items) {
    if (!item.pp_id && item.planningSheet) {
      try {
        const res = await frappe.call({
          method: "production_entry.production_planning.scheduler_api.get_planning_sheet_pp_id",
          args: {
            planning_sheet_name: item.planningSheet,
            sales_order_item: item.salesOrderItem || null,
            planning_sheet_item: item.itemName || null,
          }
        });
        if (res.message && res.message.status === "ok" && res.message.pp_id) {
          item.pp_id = res.message.pp_id;
          console.log(`Resolved PP for ${item.itemName}: ${item.pp_id}`);
        }
      } catch (e) {
        console.warn("Could not resolve PP for item", item.itemName, e);
      }
    }
  }
  
  // Group by PP ID
  const groupedByPP = {};
  let noPPCount = 0;
  
  for (const item of mergedRow.items) {
    if (!item.pp_id) {
      noPPCount++;
      continue;
    }
    const pp = item.pp_id;
    if (!groupedByPP[pp]) groupedByPP[pp] = [];
    groupedByPP[pp].push(item);
  }
  
  const ppGroups = Object.entries(groupedByPP);
  
  if (ppGroups.length === 0) {
    if (noPPCount > 0) {
      frappe.msgprint(`⚠️ All ${noPPCount} items in merged row have no Production Plan linked.<br/>Please create Production Plans first.`);
    } else {
      frappe.msgprint("No items found in merged row");
    }
    return;
  }
  
  if (noPPCount > 0) {
    frappe.show_alert({
      message: `⚠️ ${noPPCount} item(s) have no PP. Creating SPRs for ${ppGroups.reduce((sum, [, items]) => sum + items.length, 0)} items with PPs.`,
      indicator: 'orange'
    }, 5);
  }
  
  if (ppGroups.length === 1) {
    // Same PP - create 1 SPR
    await createSingleMergedSPR(ppGroups[0][0], ppGroups[0][1], mergedRow);
  } else {
    // Multiple PPs - create separate SPRs
    frappe.msgprint(`Merged items have ${ppGroups.length} different Production Plans.<br/>Creating separate SPRs for each...`);
    for (const [pp, items] of ppGroups) {
      await createSingleMergedSPR(pp, items, mergedRow);
    }
  }
}

async function createSingleMergedSPR(ppId, mergedItems, mergedRow) {
  return new Promise((resolve) => {
    // First validate WO exists
    frappe.call({
      method: "frappe.client.get_list",
      args: {
        doctype: "Work Order",
        filters: { "production_plan": ppId, "docstatus": ["<", 2] },
        limit_page_length: 1
      },
      async callback(r) {
        if (!r.message || r.message.length === 0) {
          frappe.msgprint(`⚠️ No Work Orders found for PP: ${ppId}<br/>Please start production first.`);
          resolve();
          return;
        }
        
        // WO exists, proceed with confirmation
        frappe.confirm(
          `Create SPR for ${mergedItems.length} merged items?<br/>PP: ${ppId}`,
          async () => {
            try {
              const itemNames = mergedItems.map(it => it.itemName);
              const res = await frappe.call({
                method: "production_entry.production_planning.scheduler_api.create_item_spr",
                args: {
                  pp_id: ppId,
                  planning_sheet_item_names: JSON.stringify(itemNames)
                }
              });
              
              if (res.message && res.message.status === "ok") {
                const sprId = res.message.spr_id;
                mergedRow.spr_name = mergeSprCsv(mergedRow.spr_name, sprId);
                const reused = !!res.message.reused;

                showLinkedWorkOrdersPopup(ppId);
                
                frappe.show_alert({
                  message: reused ? `✅ Using existing merged SPR: ${sprId}. Opening form...` : `✅ Merged SPR Created: ${sprId}. Opening form...`,
                  indicator: 'green'
                }, 3);
                
                await new Promise(rr => setTimeout(() => {
                  frappe.set_route('Form', 'Shaft Production Run', sprId);
                  rr();
                }, 1000));
              } else {
                frappe.msgprint(res.message?.message || "Failed to create SPR");
              }
              resolve();
            } catch (e) {
              frappe.msgprint("Error creating SPR");
              console.error(e);
              resolve();
            }
          },
          () => resolve()
        );
      }
    });
  });
}

async function openItemSPR(sprName, item = null) {
  if (!sprName) {
    frappe.msgprint("No SPR linked");
    return;
  }
  const target = await resolveSprNavigationTarget(sprName, item?.spr_docstatus);
  if (!target) {
    frappe.msgprint("No SPR linked");
    return;
  }
  try {
    const r = await frappe.call({
      method: "frappe.client.get",
      args: { doctype: "Shaft Production Run", name: target },
    });
    if (r.message) {
      frappe.set_route("Form", "Shaft Production Run", target);
      return;
    }
    if (item) {
      try {
        await frappe.call({
          method: "production_entry.production_planning.scheduler_api.prune_planning_table_spr_links",
          args: { planning_table_names: JSON.stringify([item.itemName || ""]) },
        });
      } catch (e2) {}
      item.spr_name = "";
      frappe.show_alert({
        message: "SPR was deleted. You can create a new one.",
        indicator: "orange",
      }, 3);
      createItemStockEntry(item);
    } else {
      frappe.msgprint("SPR not found. It may have been deleted.");
    }
  } catch (e) {
    if (item) {
      try {
        await frappe.call({
          method: "production_entry.production_planning.scheduler_api.prune_planning_table_spr_links",
          args: { planning_table_names: JSON.stringify([item.itemName || ""]) },
        });
      } catch (e2) {}
      item.spr_name = "";
      frappe.show_alert({
        message: "SPR was deleted. You can create a new one.",
        indicator: "orange",
      }, 3);
      createItemStockEntry(item);
    } else {
      frappe.msgprint("SPR not found.");
    }
  }
}

async function openMergedSPR(sprName, mergedRow) {
  if (!sprName) {
    frappe.msgprint("No SPR linked");
    return;
  }
  const ids = parseSprIds(sprName);
  if (!ids.length) {
    frappe.msgprint("No SPR linked");
    return;
  }
  const target = await resolveSprNavigationTarget(sprName, mergedRow?.spr_docstatus);
  if (!target) {
    frappe.msgprint("No SPR linked");
    return;
  }
  try {
    const r = await frappe.call({
      method: "frappe.client.get",
      args: { doctype: "Shaft Production Run", name: target },
    });
    if (r.message) {
      if (ids.length > 1) {
        frappe.show_alert(
          {
            message: `Opening ${target} (${ids.length} SPR(s) linked: ${ids.join(", ")})`,
            indicator: "blue",
          },
          5
        );
      }
      frappe.set_route("Form", "Shaft Production Run", target);
      return;
    }
    if (mergedRow) {
      mergedRow.spr_name = "";
      frappe.show_alert(
        {
          message: "SPR was deleted. You can create a new one.",
          indicator: "orange",
        },
        3
      );
      createMergedStockEntry(mergedRow);
    } else {
      frappe.msgprint("SPR not found. It may have been deleted.");
    }
  } catch (e) {
    if (mergedRow) {
      mergedRow.spr_name = "";
      frappe.show_alert(
        {
          message: "SPR was deleted. You can create a new one.",
          indicator: "orange",
        },
        3
      );
      createMergedStockEntry(mergedRow);
    } else {
      frappe.msgprint("SPR not found.");
    }
  }
}

function showLinkedWorkOrdersPopup(ppId) {
  if (!ppId) return;

  frappe.call({
    method: "frappe.client.get_list",
    args: {
      doctype: "Work Order",
      filters: {
        production_plan: ppId,
        docstatus: ["<", 2]
      },
      fields: ["name", "production_item", "status", "qty", "produced_qty"],
      order_by: "creation asc",
      limit_page_length: 20
    },
    callback: (r) => {
      const rows = Array.isArray(r.message) ? r.message : [];
      if (!rows.length) return;

      const body = rows.map((wo) => {
        const target = Number(wo.qty || 0);
        const produced = Number(wo.produced_qty || 0);
        const pending = target - produced;
        return `
          <tr>
            <td><b>${wo.name}</b></td>
            <td>${wo.production_item || "-"}</td>
            <td>${wo.status || "-"}</td>
            <td style="text-align:right;">${target.toFixed(2)}</td>
            <td style="text-align:right;">${produced.toFixed(2)}</td>
            <td style="text-align:right;">${pending.toFixed(2)}</td>
          </tr>
        `;
      }).join("");

      const html = `
        <div style="max-height:420px; overflow:auto;">
          <table class="table table-sm table-bordered">
            <thead>
              <tr>
                <th>WO</th>
                <th>Item</th>
                <th>Status</th>
                <th style="text-align:right;">Target</th>
                <th style="text-align:right;">Produced</th>
                <th style="text-align:right;">Pending</th>
              </tr>
            </thead>
            <tbody>${body}</tbody>
          </table>
        </div>
      `;

      const d = new frappe.ui.Dialog({
        title: `Work Orders for ${ppId}`,
        fields: [{ fieldtype: "HTML", fieldname: "wo_html", options: html }],
        primary_action_label: "Close",
        primary_action() {
          d.hide();
        }
      });
      d.show();
    }
  });
}

function goToBoard() {
    let query = {};
    if (viewScope.value === "daily") query.date = filterOrderDate.value;
    if (viewScope.value === "weekly") query.week = filterWeek.value;
    if (viewScope.value === "monthly") query.month = filterMonth.value;
    query.scope = viewScope.value;
    if (isLaminationBoard.value) query.board = "lamination";
    if (isSlittingBoard.value) query.board = "slitting";
    if (isRewindingBoard.value) query.board = "rewinding";
    frappe.set_route(
        isLaminationBoard.value
          ? "lamination-board"
          : isSlittingBoard.value
            ? "slitting-board"
            : isRewindingBoard.value
              ? "rewinding-board"
              : "production-board",
        query
    );
}

function toggleViewScope() {
    // Prevent manufacture users from changing view scope - force back to daily
    if (isManufactureUser.value) {
        viewScope.value = "daily";
        filterOrderDate.value = frappe.datetime.get_today();
        console.warn("Manufacture users cannot change view scope");
        return;
    }
    
    // Normal users can change scope
    if (viewScope.value === 'monthly' && !filterMonth.value) {
        filterMonth.value = frappe.datetime.get_today().substring(0, 7);
    } else if (viewScope.value === 'weekly' && !filterWeek.value) {
        const d = new Date();
        const dStart = new Date(d.getFullYear(), 0, 1);
        const days = Math.floor((d - dStart) / (24 * 60 * 60 * 1000));
        const weekNum = Math.ceil(days / 7);
        filterWeek.value = `${d.getFullYear()}-W${String(weekNum).padStart(2,'0')}`;
    }
    fetchData();
}

let fetchTimeout = null;

async function fetchData() {
  return new Promise((resolve) => {
    if (fetchTimeout) clearTimeout(fetchTimeout);
    fetchTimeout = setTimeout(async () => {
      try {
        if (boardAccessContext.value.loaded && boardAccessContext.value.permitted === false) {
          rawData.value = [];
          return resolve();
        }
        let args = tableBoardArgs({ party_code: filterPartyCode.value });
        
        if (viewScope.value === 'monthly') {
            if (!filterMonth.value) return resolve();
            const startDate = `${filterMonth.value}-01`;
            const [year, month] = filterMonth.value.split("-");
            const lastDay = new Date(year, month, 0).getDate();
            const endDate = `${filterMonth.value}-${lastDay}`;
            args.start_date = startDate;
            args.end_date = endDate;
        } else if (viewScope.value === 'weekly') {
            if (!filterWeek.value) return resolve();
            const [yearStr, weekStr] = filterWeek.value.split('-W');
            const y = parseInt(yearStr);
            const w = parseInt(weekStr);
            const simple = new Date(y, 0, 1 + (w - 1) * 7);
            const dow = simple.getDay();
            const ISOweekStart = new Date(simple);
            if (dow <= 4) ISOweekStart.setDate(simple.getDate() - simple.getDay() + 1);
            else ISOweekStart.setDate(simple.getDate() + 8 - simple.getDay());
            
            const ISOweekEnd = new Date(ISOweekStart);
            ISOweekEnd.setDate(ISOweekEnd.getDate() + 6);
            
            const format = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            args.start_date = format(ISOweekStart);
            args.end_date = format(ISOweekEnd);
        } else {
            args.date = filterOrderDate.value;
        }

        args.plan_name = "__all__";
        args.planned_only = 1;
        if (isRewindingBoard.value) {
          args.board_process_scope = "rewinding_only";
        } else if (isSlittingBoard.value) {
          args.board_process_scope = "slitting_only";
        } else if (isLaminationBoard.value) {
          args.board_process_scope = "lamination_only";
        } else {
          try {
            const sp = new URLSearchParams(window.location.search || "");
            const b = (sp.get("board") || "").toLowerCase();
            if (b === "lamination") {
              args.board_process_scope = "lamination_only";
            } else if (b === "slitting") {
              args.board_process_scope = "slitting_only";
            } else if (b === "rewinding") {
              args.board_process_scope = "rewinding_only";
            } else {
              args.board_process_scope = "exclude_special";
            }
          } catch (e) {
            args.board_process_scope = "exclude_special";
          }
        }

    const r = await frappe.call({
      method: "production_entry.production_planning.scheduler_api.get_color_chart_data",
      args: args,
    });
    rawData.value = (r.message || []).map(d => ({
      ...d,
      plannedDate: d.plannedDate || d.planned_date || "",
      partyCode: d.partyCode || d.party_code || "",
      customer_name: d.customer_name || d.party_name || d.customer || d.party_code || "",
      itemName: d.itemName || d.item_name || "",
      orderDate: d.orderDate || d.ordered_date || "",
      planCode: d.planCode || d.custom_plan_code || "",
      // Actual production weight must come only from SPR achieved fields.
      actual_production_weight_kgs: parseFloat(
        d.actual_production_weight_kgs ?? d.total_achieved_weight_kgs ?? 0
      ) || 0,
    }));

    // After loading raw data, fetch the exact sequence for the range 
    // to match the Production Board's exact queuing flow for each day.
    let seqStart = args.start_date;
    let seqEnd = args.end_date;
    
    // For single day, use the same date for start/end
    if (args.date && !seqStart) {
        seqStart = args.date;
        seqEnd = args.date;
    }
    
    if (seqStart && seqEnd) {
        try {
            const seqRes = await frappe.call({
                method: "production_entry.production_planning.scheduler_api.get_color_sequences_range",
                args: tableBoardArgs({
                    start_date: seqStart, 
                    end_date: seqEnd, 
                    unit: filterUnit.value || "All Units",
                // Must match save_color_sequence plan_name to avoid loading stale rows
                plan_name: "Default" 
                }),
            });
            if (seqRes.message) {
                // Backend returns keys as "unit-date", but unit might have dashes.
                // Reconstruct using the unit and date from object properties instead.
                const normalized = {};
                for (const [origKey, val] of Object.entries(seqRes.message)) {
                    // Parse the key carefully: "Unit 1-2026-01-18" -> unit="Unit 1", date="2026-01-18"
                    // Assume date is always YYYY-MM-DD at the end
                    const dateMatch = origKey.match(/(\d{4}-\d{2}-\d{2})$/);
                    if (dateMatch) {
                        const date = dateMatch[1];
                        const unit = origKey.substring(0, origKey.length - date.length - 1).trim();
                        const normalizedUnit = normalizeUnit(unit);
                        const newKey = `${normalizedUnit}||${date}`;
                        normalized[newKey] = val;
                        console.log(`Mapped key: ${origKey} -> ${newKey}`);
                    }
                }
                Object.assign(unitSequenceStore, normalized);
                console.log("Sequences loaded:", Object.keys(normalized).length, "keys");
            }
        } catch (e) {
            console.warn(`Failed to fetch sequence range`, e);
        }
    }

    await loadMergesForCurrentData();
    await loadUnitMtdStats();
    await checkMaintenanceReminders();
      } catch (e) {
        const msg = e?.message || String(e);
        if (e?.exc_type !== "PermissionError" && !/not permitted/i.test(msg)) {
          frappe.msgprint("Error loading plan data");
        }
        console.error(e);
      }
      await initTableSortables();
      resolve();
    }, 150);
  });
}

function updateUrlParams() {
    let query = {};
    if (viewScope.value === 'daily') query.date = filterOrderDate.value;
    if (viewScope.value === 'weekly') query.week = filterWeek.value;
    if (viewScope.value === 'monthly') query.month = filterMonth.value;
    query.scope = viewScope.value;
    
    const newUrl = window.location.protocol + "//" + window.location.host + window.location.pathname + '?' + new URLSearchParams(query).toString();
    window.history.replaceState({path: newUrl}, '', newUrl);
}

watch(viewScope, (newVal) => {
  // Manufacture users are locked to "daily" view
  if (isManufactureUser.value && newVal !== "daily") {
    console.warn("Manufacture users cannot change view scope - resetting to daily");
    viewScope.value = "daily";
    fetchData();
    return;
  }
  updateUrlParams();
});
watch(filterOrderDate, (newVal) => {
  // Prevent manufacture users from changing the date - force today
  if (isManufactureUser.value && newVal !== frappe.datetime.get_today()) {
    filterOrderDate.value = frappe.datetime.get_today();
    return;
  }
  updateUrlParams();
});
watch(filterWeek, updateUrlParams);
watch(filterMonth, updateUrlParams);

onMounted(async () => {
  // Check user role for visibility control
  try {
    isManufactureUser.value = detectRestrictedUser();
  } catch (e) {
    console.log("Could not detect user role", e);
    isManufactureUser.value = false;
  }
  
  try {
    const saved = localStorage.getItem(WIDTH_UNIT_LS_KEY);
    if (saved === "mm" || saved === "inches") {
      widthDimUnit.value = saved;
    }
  } catch (e) {}

  const params = new URLSearchParams(window.location.search);
  isLaminationBoard.value = (params.get("board") || "").toLowerCase() === "lamination";
  isSlittingBoard.value = (params.get("board") || "").toLowerCase() === "slitting";
  isRewindingBoard.value = (params.get("board") || "").toLowerCase() === "rewinding";
  const scopeParam = params.get('scope');
  const dateParam = params.get('date');
  const weekParam = params.get('week');
  const monthParam = params.get('month');
  
  // For manufacture users: FORCE daily view with today's date only
  if (isManufactureUser.value) {
    viewScope.value = "daily";
    filterOrderDate.value = frappe.datetime.get_today();
  } else {
    // For other users: respect URL parameters
    if (scopeParam) viewScope.value = scopeParam;
    if (dateParam) filterOrderDate.value = dateParam;
    if (weekParam) filterWeek.value = weekParam;
    if (monthParam) filterMonth.value = monthParam;
  }
  
  await loadBoardAccessContext();
  await fetchMaintenanceRecords();
  await fetchData();
});

onBeforeUnmount(() => {
  destroyTableSortables();
});
</script>

<style scoped>
.cc-container {
  display: flex;
  flex-direction: column;
  padding: 16px;
  background-color: #f3f4f6;
  min-height: 100vh;
  max-width: 100%;
  min-width: 0;
}
.cc-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap; /* prevents toolbar from widening the page and pushing sticky columns off-screen */
  padding: 12px 16px;
  background-color: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  margin-bottom: 20px;
  gap: 12px;
}
.cc-filter-item {
  display: flex;
  flex-direction: column;
}
.cc-filter-item label {
  font-size: 11px;
  font-weight: 600;
  color: #6b7280;
  margin-bottom: 2px;
}
.cc-filter-item input,
.cc-filter-item select {
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
}
.cc-clear-btn, .cc-view-btn {
  padding: 8px 16px;
  background-color: white;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  font-weight: 500;
}
.cc-lock-btn {
  padding: 8px 14px;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  font-weight: 600;
  background: #fff7ed;
  color: #9a3412;
}
.cc-save-arrange-btn {
  padding: 8px 14px;
  border: none;
  border-radius: 4px;
  font-size: 13px;
  cursor: pointer;
  font-weight: 600;
  background: #16a34a;
  color: white;
}
.cc-save-arrange-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.cc-arrange-indicator {
  font-size: 12px;
  font-weight: 600;
}
.cc-arrange-indicator.saving {
  color: #2563eb;
}
.cc-arrange-indicator.dirty {
  color: #c2410c;
}
.cc-arrange-indicator.clean {
  color: #15803d;
}
.cc-view-btn {
    background-color: #3b82f6;
    color: white;
    border: none;
}
.cc-maint-btn {
    padding: 8px 16px;
    background-color: #f97316;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 13px;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.2s;
}
.cc-maint-btn:hover {
    background-color: #ea580c;
    box-shadow: 0 2px 4px rgba(249, 115, 22, 0.3);
}

.cc-pp-btn {
    padding: 6px 12px;
    background-color: #8b5cf6;
    color: white;
    border: none;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
    font-weight: 600;
    transition: all 0.2s;
    white-space: nowrap;
}
.cc-pp-btn:hover {
    background-color: #7c3aed;
    box-shadow: 0 2px 4px rgba(139, 92, 246, 0.3);
}

.cc-table-container {
    display: flex;
    flex-direction: column;
    gap: 30px;
    width: 100%;
    max-width: 100%;
    min-width: 0;
}
.cc-table-scroll {
    width: 100%;
    max-height: calc(100vh - 240px);
    overflow: auto;
    -webkit-overflow-scrolling: touch;
}
.cc-table-unit-header {
  padding: 10px 15px;
  font-weight: 800;
  font-size: 14px;
  border-radius: 8px 8px 0 0;
  border: 1px solid #e5e7eb;
  border-bottom: none;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.cc-unit-mtd-label {
  font-size: 12px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.55);
  padding: 2px 8px;
  border-radius: 4px;
  margin-left: auto;
}
.cc-prod-table {
    width: 100%;
    min-width: 1280px;
    /* border-collapse: separate is required — `collapse` breaks position:sticky on table cells */
    border-collapse: separate;
    border-spacing: 0;
    background: white;
    font-size: 12px;
    border-left: 1px solid #e5e7eb;
    border-top: 1px solid #e5e7eb;
    box-shadow: 0 1px 2px rgba(0,0,0,0.05);
}
.cc-prod-table th {
    background: #f8fafc;
    padding: 10px;
    border-right: 1px solid #e5e7eb;
    border-bottom: 1px solid #e5e7eb;
    text-align: center;
    font-weight: 700;
}
.cc-prod-table thead th {
    position: sticky;
    top: 0;
    z-index: 30;
    background: #f8fafc;
    box-shadow: 0 1px 0 rgba(0, 0, 0, 0.08);
}
.pt-sortable-body .pt-draggable-row {
  transition: background-color 0.2s;
}
.pt-sortable-body .pt-draggable-row:hover {
  background-color: #f8fafc;
}
.pt-drag-handle {
  display: inline-block;
  user-select: none;
}
.cc-prod-table td {
    padding: 8px;
    border-right: 1px solid #e5e7eb;
    border-bottom: 1px solid #e5e7eb;
}
.cell-center { text-align: center; }
.cell-right { text-align: right; }
.font-bold { font-weight: 700; }
.bg-yellow-50 { background-color: #fefce8; }
td.pt-pp-sticky-cell,
td.pt-spr-sticky-cell {
  vertical-align: top;
  padding-top: 6px !important;
  padding-bottom: 6px !important;
}
.pt-pp-sticky-cell,
.pt-spr-sticky-cell {
  box-sizing: border-box;
  overflow: hidden;
}
/* Fixed widths so the right-pinned offsets (right: 0 / right: 200px) always line up */
.pt-spr-sticky-cell {
  width: 200px !important;
  min-width: 200px !important;
  max-width: 200px !important;
}
td.pt-pp-sticky-cell {
  width: 90px !important;
  min-width: 90px !important;
  max-width: 90px !important;
}

.status-badge {
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 10px;
    font-weight: 600;
}
.bg-red-100 { background: #fee2e2; color: #991b1b; }
.bg-green-100 { background: #dcfce7; color: #166534; }
.bg-orange-100 { background: #ffedd5; color: #9a3412; }
.bg-gray-100 { background: #f3f4f6; color: #374151; }

.pt-draggable-row.pt-drag-ghost {
  opacity: 0.4 !important;
  background-color: #f0f9ff !important;
}

.pt-draggable-row.pt-drag-chosen {
  background-color: #dbeafe !important;
  box-shadow: inset 0 0 8px rgba(59, 130, 246, 0.3) !important;
}

.pt-draggable-row.pt-drag-dragging {
  opacity: 1 !important;
  background-color: #e0f2fe !important;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
  z-index: 1000 !important;
  transform: scale(1.01) !important;
}

.pt-sortable-body.sortable-ghost {
  background-color: #f5f5f5 !important;
}

.pt-spr-btn-row {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  justify-content: center;
  width: 100%;
}
.pt-spr-btn-row .pt-btn-entry {
  max-width: 96px;
  flex: 1 1 88px;
}
.pt-stock-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-start;
  gap: 3px;
  min-width: 0;
  width: 100%;
  max-width: none;
  margin: 0;
  overflow: visible;
}
.pt-stock-cell .pt-spr-btn,
.pt-stock-cell .btn {
  white-space: normal;
  word-break: break-word;
  max-width: 100%;
}
.pt-pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  justify-content: center;
  margin-bottom: 0;
}
.pt-pill {
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  line-height: 1.2;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}
.pt-pill-draft {
  background: #fef3c7;
  color: #92400e;
  border: 1px solid #fcd34d;
}
.pt-pill-submitted {
  background: #d1fae5;
  color: #065f46;
  border: 1px solid #6ee7b7;
}
.pt-pill-muted {
  background: #f1f5f9;
  color: #64748b;
  border: 1px solid #e2e8f0;
}
.pt-pill-wo {
  font-size: 9px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
}
.pt-pill-wo-open {
  background: #dbeafe;
  color: #1e40af;
  border: 1px solid #93c5fd;
}
.pt-pill-wo-done {
  background: #ecfdf5;
  color: #047857;
  border: 1px solid #6ee7b7;
}
.pt-pill-wo-unknown {
  background: #f8fafc;
  color: #475569;
  border: 1px solid #cbd5e1;
}
.pt-btn-entry {
  font-size: 11px !important;
  font-weight: 600 !important;
  padding: 5px 8px !important;
  line-height: 1.2 !important;
  white-space: normal;
  max-width: 122px;
}
.pt-spr-btn-draft {
  background: #d97706 !important;
  color: #fff !important;
}
.pt-spr-btn-submitted {
  background: #0d9488 !important;
  color: #fff !important;
}
.pt-spr-btn-done {
  background: #059669 !important;
  color: #fff !important;
}
.pt-prod-status-line {
  font-size: 10px;
  line-height: 1.25;
  color: #475569;
  text-align: center;
  max-width: 174px;
}
.pt-prod-status-merge {
  color: #334155;
  font-weight: 600;
}

.pt-wo-closed-hint {
  font-size: 10px;
  font-weight: 700;
  color: #047857;
  white-space: nowrap;
  text-align: center;
  max-width: 108px;
  line-height: 1.2;
}
.pt-no-pp-hint {
  font-size: 10px;
  font-weight: 700;
  color: #991b1b;
  white-space: nowrap;
  text-align: center;
}

.pt-merge-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pt-merge-dialog {
  width: min(980px, 92vw);
  max-height: 88vh;
  overflow: hidden;
  background: white;
  border-radius: 10px;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
}

.pt-merge-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.pt-merge-close {
  border: none;
  background: transparent;
  font-size: 18px;
  cursor: pointer;
}

.pt-merge-filters {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.pt-merge-filters input {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 8px;
  font-size: 13px;
}

.pt-merge-suggest {
  padding: 8px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.pt-merge-summary {
  display: flex;
  gap: 20px;
  padding: 8px 16px;
  border-bottom: 1px solid #e5e7eb;
  background: #f8fafc;
  font-size: 12px;
  color: #334155;
}

.pt-merge-suggest-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 8px;
}

.pt-merge-suggest-pill {
  border: 1px solid #c4b5fd;
  background: #f5f3ff;
  color: #5b21b6;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}

.pt-merge-list {
  padding: 10px 16px;
  overflow: auto;
  max-height: 45vh;
}

.pt-merge-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #e5e7eb;
  font-size: 12px;
}

.pt-merge-empty {
  color: #6b7280;
  font-style: italic;
}

.pt-merge-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #e5e7eb;
}

.pt-merge-row {
  background: #faf5ff;
}

.pt-merge-expand-btn {
  border: none;
  background: transparent;
  color: #6d28d9;
  font-weight: 700;
  cursor: pointer;
  padding: 0;
}

.pt-merge-expanded-row {
  background: #fdf4ff;
}

.pt-merge-inline-details {
  margin-top: 6px;
  padding: 6px 8px;
  background: #faf5ff;
  border: 1px solid #e9d5ff;
  border-radius: 6px;
}

.pt-merge-inline-item {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  padding: 2px 0;
  color: #334155;
}

.pt-shift-cell {
  font-size: 11px;
  line-height: 1.35;
  vertical-align: middle;
}

.pt-shift-run + .pt-shift-run {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed #e2e8f0;
}

.pt-shift-run-date {
  font-weight: 600;
  color: #475569;
}

.pt-shift-run-kg {
  font-weight: 700;
  color: #0f172a;
}
</style>
